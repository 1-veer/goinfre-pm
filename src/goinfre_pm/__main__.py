"""Module entry point with a quiet interactive startup indicator."""

from __future__ import annotations

import os
import sys
import threading


class _StartupIndicator:
    """Show activity while Python and Textual load, without touching logs."""

    _frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self) -> None:
        self._enabled = (
            os.environ.get("GPM_STARTUP_INDICATOR") == "1"
            and sys.stdout.isatty()
            and not os.environ.get("NO_COLOR")
            and all(argument == "--no-restore" for argument in sys.argv[1:])
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled:
            return
        self._thread = threading.Thread(target=self._animate, name="gpm-startup", daemon=True)
        self._thread.start()

    def _animate(self) -> None:
        index = 0
        while not self._stop.wait(0.12):
            frame = self._frames[index % len(self._frames)]
            sys.stdout.write(f"\r\033[38;5;141m[GoinfrePM]\033[0m {frame} Starting the interface")
            sys.stdout.flush()
            index += 1

    def finish(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()
        self._enabled = False


indicator = _StartupIndicator()
indicator.start()

from .cli import main  # noqa: E402 - imports intentionally happen under the indicator

try:
    raise SystemExit(main(_startup_ready=indicator.finish))
finally:
    indicator.finish()
