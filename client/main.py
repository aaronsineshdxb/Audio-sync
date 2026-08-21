"""Receive and optionally play audio from an Audio Suite server."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import time
from pathlib import Path
from typing import Optional

import websockets

from common.audio import AudioPlayback
from common.config import ClientConfig, load_config
from common.logging_utils import configure_logging
from common.platform_utils import install_signal_handlers, run_app
from common.protocol import AudioFrameMessage, deserialize_message, make_handshake, serialize_message

LOGGER = configure_logging(name="client")


class AudioClient:
    def __init__(self, config: ClientConfig, *, playback: bool = True):
        self.config = config
        self.playback = AudioPlayback(device=config.output_device) if playback else None
        self._stop = asyncio.Event()
        self.received_frames = 0
        self.invalid_frames = 0
        self.started_at = 0.0

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        async with websockets.connect(self.config.server_url, max_size=2_000_000, ping_interval=20) as ws:
            await ws.send(serialize_message(make_handshake("client")))
            if self.playback:
                self.playback.start()
            self.started_at = time.monotonic()
            LOGGER.info("Connected to %s", self.config.server_url)
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    message = deserialize_message(raw)
                    if not isinstance(message, AudioFrameMessage):
                        continue
                    try:
                        frame = message.to_frame()
                    except (ValueError, TypeError):
                        self.invalid_frames += 1
                        LOGGER.warning("Discarding invalid audio frame")
                        continue
                    self.received_frames += 1
                    if self.playback:
                        self.playback.enqueue(frame)
                    if self.received_frames % 100 == 0:
                        elapsed = max(time.monotonic() - self.started_at, 0.001)
                        LOGGER.info("Received %d frames (%.1f frames/s)", self.received_frames, self.received_frames / elapsed)
            finally:
                if self.playback:
                    self.playback.stop()


async def _main(config_path: Optional[Path], no_playback: bool) -> None:
    raw = load_config(config_path)
    client = AudioClient(ClientConfig(**raw.get("client", {})), playback=not no_playback)
    loop = asyncio.get_running_loop()
    install_signal_handlers(loop, client.request_stop)
    await client.run()


def run_client() -> None:
    parser = argparse.ArgumentParser(description="Receive realtime audio")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-playback", action="store_true", help="Receive and validate frames without opening an output device")
    args = parser.parse_args()
    run_app(_main(args.config, args.no_playback))


if __name__ == "__main__":
    run_client()
