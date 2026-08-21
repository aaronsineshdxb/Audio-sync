"""Cross-platform helpers shared by the entry-point applications."""
from __future__ import annotations

import asyncio
import sys
from typing import Iterable, Optional


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    callback,
    signals: Optional[Iterable] = None,
) -> None:
    """Register SIGINT/SIGTERM handlers on every supported platform.

    `loop.add_signal_handler` is not implemented on Windows (ProactorEventLoop),
    so we fall back to `signal.signal` there.
    """
    import signal

    if signals is None:
        signals = (signal.SIGINT, signal.SIGTERM)

    if sys.platform == "win32":
        for sig in signals:
            try:
                signal.signal(sig, lambda _sig, _frame: callback())
            except (ValueError, OSError):  # not main thread / unsupported
                pass
    else:
        for sig in signals:
            try:
                loop.add_signal_handler(sig, callback)
            except NotImplementedError:
                signal.signal(sig, lambda _sig, _frame: callback())


def run_app(main_coro) -> None:
    """Run an async main with KeyboardInterrupt suppressed on all platforms."""
    try:
        asyncio.run(main_coro)
    except KeyboardInterrupt:
        pass
