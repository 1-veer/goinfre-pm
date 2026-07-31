"""Single source of project identity values.

The adjacent ``project.conf`` is deliberately valid both as simple POSIX shell
assignments and as input to this strict parser.  The installer and Python code
therefore consume the same branding source.
"""

from __future__ import annotations

from pathlib import Path
import re

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CONFIG = Path(__file__).with_name("project.conf")


def _load() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in _CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not _KEY.fullmatch(key):
            raise RuntimeError(f"Invalid branding line: {raw!r}")
        value = value.strip()
        if len(value) < 2 or value[0] != "'" or value[-1] != "'":
            raise RuntimeError(f"Branding values must be single quoted: {key}")
        values[key] = value[1:-1]
    return values


_VALUES = _load()
DISPLAY_NAME = _VALUES["PROJECT_DISPLAY_NAME"]
COMMAND = _VALUES["PROJECT_COMMAND"]
MODULE = _VALUES["PROJECT_MODULE"]
SLUG = _VALUES["PROJECT_SLUG"]
AUTHOR = _VALUES["PROJECT_AUTHOR"]
REPOSITORY = _VALUES["PROJECT_REPOSITORY"]
VERSION = _VALUES["PROJECT_VERSION"]
USER_AGENT = f"{DISPLAY_NAME}/{VERSION} (+{REPOSITORY})"
