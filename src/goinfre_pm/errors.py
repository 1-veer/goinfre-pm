from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import traceback

from .storage import CONFIG_DIR


def error_text(error: BaseException) -> str:
    """Return a useful single-line message, even for empty exceptions."""
    message = str(error).strip()
    return message or error.__class__.__name__


def write_crash_log(error: BaseException, path: Path | None = None) -> Path | None:
    """Best-effort diagnostic logging that must never hide the original error."""
    target = path or CONFIG_DIR / "crash.log"
    descriptor: int | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            descriptor = None
            handle.write(f"\n[{timestamp}] {details}")
        return target
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return None
