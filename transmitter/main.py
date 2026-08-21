"""Transmitter application for capturing audio and streaming to the server."""
from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from pathlib import Path
from typing import Optional

import numpy as np
import websockets
from websockets import WebSocketClientProtocol

from common.audio import AudioCapture, AudioFrame
from common.config import TransmitterConfig, load_config
from common.logging_utils import configure_logging
from common.platform_utils import install_signal_handlers
from common.protocol import AudioFrameMessage, HeartbeatMessage, make_handshake, serialize_message

LOGGER = configure_logging(name="transmitter")

FRAME_QUEUE_MAX = 200
HEARTBEAT_INTERVAL = 5.0
LEVEL_UPDATE_INTERVAL = 0.1
RECONNECT_DELAY = 3.0


class TransmitterClient:
    """Handles audio capture and streaming to the central server."""

    def __init__(self, config: TransmitterConfig):
        self.config = config
        self._status_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._frame_queue: asyncio.Queue[Optional[AudioFrame]] = asyncio.Queue(maxsize=FRAME_QUEUE_MAX)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[WebSocketClientProtocol] = None
        self._capture: Optional[AudioCapture] = None
        self._stop_event = asyncio.Event()
        self._connected = False
        self._running = False
        self._dropped_frames = 0
        self._last_level_emit = 0.0

    @property
    def status_queue(self) -> "asyncio.Queue[dict]":
        return self._status_queue

    def request_stop(self) -> None:
        self._stop_event.set()
        if self._loop:
            def _schedule_shutdown() -> None:
                if self._ws and not self._ws.closed:
                    asyncio.create_task(self._ws.close(code=1001, reason="Transmitter stop requested"))
                self._inject_stop_sentinel()

            self._loop.call_soon_threadsafe(_schedule_shutdown)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._status_queue.put({"event": "transmitter_started"})
        while not self._stop_event.is_set():
            try:
                await self._status_queue.put({
                    "event": "connecting",
                    "url": self.config.server_url,
                })
                await self._connect_and_stream()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging
                LOGGER.exception("Transmitter encountered an error: %s", exc)
                await self._status_queue.put({
                    "event": "error",
                    "message": str(exc),
                })
            finally:
                await self._cleanup_connection()

            if not self._stop_event.is_set():
                await self._status_queue.put({
                    "event": "reconnecting",
                    "delay": RECONNECT_DELAY,
                })
                await asyncio.sleep(RECONNECT_DELAY)

        await self._status_queue.put({"event": "transmitter_stopped"})

    async def _connect_and_stream(self) -> None:
        async with websockets.connect(
            self.config.server_url,
            max_size=2_000_000,
            ping_interval=None,
        ) as websocket:
            self._ws = websocket
            handshake = serialize_message(make_handshake("transmitter"))
            await websocket.send(handshake)
            self._connected = True
            await self._status_queue.put({"event": "connected"})

            # Reset state for new session
            self._frame_queue = asyncio.Queue(maxsize=FRAME_QUEUE_MAX)
            self._dropped_frames = 0
            self._last_level_emit = 0.0
            self._running = True

            self._capture = AudioCapture(
                device=self.config.input_device,
                callback=self._on_captured_frame,
            )
            try:
                self._capture.start()
            except Exception as exc:
                await self._status_queue.put({
                    "event": "capture_error",
                    "message": str(exc),
                })
                raise
            await self._status_queue.put({"event": "capture_started"})

            sender_task = asyncio.create_task(self._sender_loop())
            receiver_task = asyncio.create_task(self._receiver_loop())
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                done, pending = await asyncio.wait(
                    {sender_task, receiver_task, heartbeat_task},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
            finally:
                for task in (sender_task, receiver_task, heartbeat_task):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def _sender_loop(self) -> None:
        assert self._ws is not None
        while self._running and not self._stop_event.is_set():
            frame = await self._frame_queue.get()
            if frame is None:
                break
            message = AudioFrameMessage.from_frame(frame)
            await self._ws.send(serialize_message(message))
            now = time.time()
            if now - self._last_level_emit >= LEVEL_UPDATE_INTERVAL:
                samples = frame.to_numpy().astype(np.float32)
                if samples.size:
                    rms = float(np.sqrt(np.mean(np.square(samples))) / 32768.0)
                    await self._status_queue.put({
                        "event": "level",
                        "value": max(0.0, min(1.0, rms)),
                    })
                    self._last_level_emit = now

    async def _receiver_loop(self) -> None:
        assert self._ws is not None
        async for _ in self._ws:
            # Server may send status/control messages; for now we ignore but keep connection alive
            await asyncio.sleep(0)  # yield control

    async def _heartbeat_loop(self) -> None:
        assert self._ws is not None
        while self._running and not self._stop_event.is_set():
            heartbeat = HeartbeatMessage(timestamp=time.time())
            await self._ws.send(serialize_message(heartbeat))
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def _on_captured_frame(self, frame: AudioFrame) -> None:
        if not self._loop or not self._running:
            return

        def _enqueue() -> None:
            if not self._running:
                return
            try:
                self._frame_queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._dropped_frames += 1
                if self._dropped_frames == 1 or self._dropped_frames % 50 == 0:
                    asyncio.create_task(self._status_queue.put({
                        "event": "dropped_frames",
                        "count": self._dropped_frames,
                    }))

        self._loop.call_soon_threadsafe(_enqueue)

    async def _cleanup_connection(self) -> None:
        self._running = False
        if self._capture:
            self._capture.stop()
            self._capture = None
        if self._ws:
            with contextlib.suppress(Exception):
                if not self._ws.closed:
                    await self._ws.close()
        self._ws = None
        if self._connected:
            await self._status_queue.put({"event": "disconnected"})
            self._connected = False

    async def stop(self) -> None:
        self.request_stop()

    def _inject_stop_sentinel(self) -> None:
        if self._frame_queue:
            try:
                self._frame_queue.put_nowait(None)
            except asyncio.QueueFull:
                # best effort, sender loop will drain existing frames then exit
                pass


async def _main(config_path: Optional[Path] = None) -> None:
    raw_config = load_config(config_path)
    config = TransmitterConfig(**raw_config.get("transmitter", {}))
    client = TransmitterClient(config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        LOGGER.info("Signal received, stopping transmitter")
        client.request_stop()
        stop_event.set()

    install_signal_handlers(loop, _handle_signal)

    client_task = asyncio.create_task(client.run())
    await stop_event.wait()
    client_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await client_task


def run_transmitter() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run_transmitter()
