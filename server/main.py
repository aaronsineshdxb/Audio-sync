"""Asyncio WebSocket server for realtime audio distribution."""
from __future__ import annotations

import asyncio
import contextlib
import signal
import ssl
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Set

import websockets
from websockets import WebSocketServerProtocol

from common.config import ServerConfig, load_config
from common.logging_utils import configure_logging
from common.platform_utils import install_signal_handlers
from common.protocol import (
    AudioFrameMessage,
    MessageType,
    deserialize_message,
    serialize_message,
)

LOGGER = configure_logging(name="server")


@dataclass(slots=True)
class Peer:
    websocket: WebSocketServerProtocol
    role: str


class AudioServer:
    def __init__(self, config: ServerConfig):
        self.config = config
        self._clients: Set[WebSocketServerProtocol] = set()
        self._clients_lock = asyncio.Lock()
        self._peers: Dict[WebSocketServerProtocol, Peer] = {}
        self._status_queue: "asyncio.Queue[dict]" = asyncio.Queue()
        self._ws_server: Optional[websockets.server.Serve] = None
        self._stop_event = asyncio.Event()

    @property
    def status_queue(self) -> "asyncio.Queue[dict]":
        return self._status_queue

    async def start(self) -> None:
        LOGGER.info("Starting audio server on %s:%s", self.config.host, self.config.port)
        ssl_context = self._build_ssl_context()
        self._ws_server = await websockets.serve(
            self._handle_connection,
            self.config.host,
            self.config.port,
            ssl=ssl_context,
            max_size=2_000_000,  # allow large audio payloads
        )
        await self._status_queue.put({
            "event": "server_started",
            "host": self.config.host,
            "port": self.config.port,
        })
        await self._stop_event.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        LOGGER.info("Shutdown requested")
        self._stop_event.set()
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        async with self._clients_lock:
            await asyncio.gather(
                *[client.close(code=1001, reason="Server shutdown") for client in list(self._clients)],
                return_exceptions=True,
            )
            self._clients.clear()
        await self._status_queue.put({"event": "server_stopped"})

    def request_stop(self) -> None:
        self._stop_event.set()

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        peer: Optional[Peer] = None
        try:
            async with self._clients_lock:
                if len(self._clients) >= self.config.max_clients:
                    LOGGER.warning("Rejecting connection, server full")
                    await websocket.close(code=4000, reason="Server full")
                    return
                self._clients.add(websocket)
            LOGGER.info("Accepted connection from %s", websocket.remote_address)

            # Expect handshake message identifying role
            handshake = await websocket.recv()
            msg = deserialize_message(handshake)
            role = "client"
            if getattr(msg, "message_type", None) == MessageType.CONTROL and getattr(msg, "command", "") == "handshake":
                role = msg.data.get("role", "client")
            elif isinstance(msg, dict) and msg.get("command") == "handshake":
                role = msg.get("data", {}).get("role", "client")
            else:
                raise ValueError("First message must be CONTROL handshake with role")
            peer = Peer(websocket=websocket, role=role)
            self._peers[websocket] = peer
            await self._status_queue.put({
                "event": "client_joined",
                "address": websocket.remote_address,
                "role": role,
                "connections": len(self._clients),
            })

            async for raw_message in websocket:
                await self._dispatch_message(peer, raw_message)
        except websockets.ConnectionClosedOK:
            LOGGER.info("Connection closed normally from %s", websocket.remote_address)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Error handling connection: %s", exc)
        finally:
            async with self._clients_lock:
                self._clients.discard(websocket)
            if peer:
                self._peers.pop(websocket, None)
                await self._status_queue.put({
                    "event": "client_left",
                    "address": websocket.remote_address,
                    "role": peer.role,
                    "connections": len(self._clients),
                })

    async def _dispatch_message(self, peer: Peer, raw_message: str) -> None:
        message = deserialize_message(raw_message)
        if isinstance(message, AudioFrameMessage):
            await self._broadcast_audio(peer, message)
        elif isinstance(message, dict):
            await self._status_queue.put({
                "event": "status",
                "from": peer.role,
                "payload": message,
            })
        else:
            await self._status_queue.put({
                "event": "control",
                "from": peer.role,
                "payload": asdict(message),
            })

    async def _broadcast_audio(self, peer: Peer, message: AudioFrameMessage) -> None:
        # Only transmitters should broadcast audio
        if peer.role not in {"transmitter", "server"}:
            LOGGER.warning("Non-transmitter attempted to send audio")
            return
        frame_payload = serialize_message(message)
        failures = 0
        async with self._clients_lock:
            receivers = [ws for ws in self._clients if ws != peer.websocket]
            for ws in receivers:
                try:
                    await ws.send(frame_payload)
                except Exception as exc:  # pragma: no cover - network errors
                    LOGGER.error("Failed to send audio to %s: %s", ws.remote_address, exc)
                    failures += 1
        if failures:
            await self._status_queue.put({
                "event": "broadcast_failures",
                "count": failures,
            })

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        if not (self.config.ssl_cert and self.config.ssl_key):
            return None
        cert_path = Path(self.config.ssl_cert).expanduser().resolve()
        key_path = Path(self.config.ssl_key).expanduser().resolve()
        if not cert_path.exists() or not key_path.exists():
            LOGGER.warning("SSL cert/key not found, running without TLS")
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        return ctx


async def _main(config_path: Optional[Path] = None) -> None:
    raw_config = load_config(config_path)
    server_config = ServerConfig(**raw_config.get("server", {}))
    server = AudioServer(server_config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        LOGGER.info("Signal received, shutting down")
        stop_event.set()

    install_signal_handlers(loop, _handle_signal)

    server_task = asyncio.create_task(server.start())

    await stop_event.wait()
    await server.shutdown()
    server_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server_task


def run_server() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run_server()
