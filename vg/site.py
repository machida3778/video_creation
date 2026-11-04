"""Site configuration from private local/site.env, overridden by environment variables.

The file accepts simple KEY=VALUE assignments with shell quoting and comments.
Values are literal: shell expansion and commands are never evaluated by this loader.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex

SITE_FILE = Path(__file__).resolve().parents[1] / "local" / "site.env"
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load(path: Path | None = None) -> dict[str, str]:
    """Read assignments and overlay the process environment; a missing file is fine."""
    path = Path(path) if path is not None else SITE_FILE
    values = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, raw = line.partition("=")
            if not separator or not KEY.fullmatch(key):
                raise ValueError(f"{path}:{number}: expected KEY=VALUE")
            try:
                tokens = shlex.split(raw, comments=True)
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
            if len(tokens) > 1:
                raise ValueError(f"{path}:{number}: quote values containing spaces")
            values[key] = tokens[0] if tokens else ""
    values.update(os.environ)
    return values


def require(key: str, values: dict[str, str] | None = None) -> str:
    """Require a configured, nonempty setting before any site-dependent operation."""
    values = load() if values is None else values
    value = values.get(key, "").strip()
    if not value:
        raise RuntimeError(f"Missing site setting {key}; set it in local/site.env or the environment "
                           "(see config/site.example.env)")
    return value
