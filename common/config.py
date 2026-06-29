"""Configuration management utilities."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

CONFIG_FILE_NAME = "audio_suite.yaml"


@dataclass(slots=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    max_clients: int = 40
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = None


@dataclass(slots=True)
class TransmitterConfig:
    server_url: str = "ws://localhost:8765"
    input_device: Optional[int] = None


@dataclass(slots=True)
class ClientConfig:
    server_url: str = "ws://localhost:8765"
    output_device: Optional[int] = None


def load_config(path: Optional[Path] = None) -> dict:
    path = path or Path(CONFIG_FILE_NAME)
    if not path.exists():
        return {
            "server": ServerConfig().__dict__,
            "transmitter": TransmitterConfig().__dict__,
            "client": ClientConfig().__dict__,
        }
    with path.open("r", encoding="utf8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def save_config(config: dict, path: Optional[Path] = None) -> None:
    path = path or Path(CONFIG_FILE_NAME)
    with path.open("w", encoding="utf8") as fh:
        yaml.safe_dump(config, fh, default_flow_style=False)
