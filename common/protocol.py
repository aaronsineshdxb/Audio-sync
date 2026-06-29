"""WebSocket message schema and helpers."""
from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict

from .audio import AudioFrame


class MessageType(str, Enum):
    AUDIO_FRAME = "audio_frame"
    HEARTBEAT = "heartbeat"
    CONTROL = "control"
    STATUS = "status"


@dataclass(slots=True)
class AudioFrameMessage:
    message_type: MessageType
    sequence: int
    timestamp: float
    payload_b64: str

    @classmethod
    def from_frame(cls, frame: AudioFrame) -> "AudioFrameMessage":
        payload_b64 = base64.b64encode(frame.payload).decode("ascii")
        return cls(
            message_type=MessageType.AUDIO_FRAME,
            sequence=frame.sequence,
            timestamp=frame.timestamp,
            payload_b64=payload_b64,
        )

    def to_frame(self) -> AudioFrame:
        payload = base64.b64decode(self.payload_b64.encode("ascii"))
        return AudioFrame(sequence=self.sequence, timestamp=self.timestamp, payload=payload)


@dataclass(slots=True)
class HeartbeatMessage:
    message_type: MessageType = MessageType.HEARTBEAT
    timestamp: float = time.time()


@dataclass(slots=True)
class ControlMessage:
    message_type: MessageType = MessageType.CONTROL
    command: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


def make_handshake(role: str) -> ControlMessage:
    return ControlMessage(command="handshake", data={"role": role})


def serialize_message(message: Any) -> str:
    if hasattr(message, "message_type"):
        data = asdict(message)
        data["message_type"] = message.message_type.value  # ensure enum serialization
    else:
        raise TypeError("Unsupported message type for serialization")
    return json.dumps(data, separators=(",", ":"))


def deserialize_message(payload: str) -> Any:
    data = json.loads(payload)
    message_type = MessageType(data["message_type"])
    if message_type == MessageType.AUDIO_FRAME:
        return AudioFrameMessage(
            message_type=message_type,
            sequence=data["sequence"],
            timestamp=data["timestamp"],
            payload_b64=data["payload_b64"],
        )
    if message_type == MessageType.HEARTBEAT:
        return HeartbeatMessage(
            message_type=message_type,
            timestamp=data.get("timestamp", time.time()),
        )
    if message_type == MessageType.CONTROL:
        return ControlMessage(
            message_type=message_type,
            command=data["command"],
            data=data.get("data", {}),
        )
    if message_type == MessageType.STATUS:
        return data
    raise ValueError(f"Unsupported message type: {message_type}")
