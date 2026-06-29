"""Logging configuration shared across suite components."""
from __future__ import annotations

import logging
from typing import Optional

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO, *, name: Optional[str] = None) -> logging.Logger:
    """Configure root logging once and return a module-specific logger."""
    logging.basicConfig(level=level, format=LOG_FORMAT)
    return logging.getLogger(name)
