"""Claude subscription usage before a video is made (user rules: 2026-11-06 start only at 70% or less;
2026-11-08 the 5-hour window at 70% or less and the weekly window at 90% or less).

The percentages shown by `/usage` reach scripts only through the status line JSON (`rate_limits`:
`five_hour` and `seven_day`, each with `used_percentage` and `resets_at`), and the status line runs only
in an interactive session. `probe` therefore opens a short interactive Claude session (smallest model,
one-word prompt) inside a pseudo-terminal, with a status line command that writes its input to a file,
and stops the session as soon as `rate_limits` arrives. Model API credentials are removed from its
environment; it uses the subscription login.

A window missing from `rate_limits` has reset, so it counts as 0%. No `rate_limits` at all, or no answer
in time, is an error: callers then do not start (fail closed).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from .media import ROOT

WINDOWS = {"five_hour": "5時間枠", "seven_day": "週間枠"}
DEFAULT_LIMIT = 70
# `vg usage --check` exit codes: 0 = may start, HIGH = over the limit, UNKNOWN = could not be read.
HIGH, UNKNOWN = 13, 14


def limit(config_path: Path | None = None) -> dict[str, float]:
    """{window: max percent} from `max_usage_percent` in config/headless.yaml (default 70 for each window).

    The setting is one number for both windows, or a mapping with `five_hour` and/or `seven_day`
    (a window left out keeps the default)."""
    import yaml

    path = config_path or ROOT / "config/headless.yaml"
    value = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("max_usage_percent", DEFAULT_LIMIT) \
        if path.exists() else DEFAULT_LIMIT
    if isinstance(value, dict):
        unknown = set(value) - set(WINDOWS)
        if unknown:
            raise ValueError(f"headless.max_usage_percent: unknown window(s) {sorted(unknown)}")
        values = {name: value.get(name, DEFAULT_LIMIT) for name in WINDOWS}
    else:
        values = {name: value for name in WINDOWS}
    for number in values.values():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 < number <= 100:
            raise ValueError("headless.max_usage_percent must be a number in (0, 100] (or one per window)")
    return {name: float(number) for name, number in values.items()}


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ("API_KEY", "API_TOKEN"))
           and key.upper() not in ("ANTHROPIC_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK")}
    env.setdefault("TERM", "xterm-256color")
    node = Path.home() / ".local/opt/node/bin"
    env["PATH"] = f"{env.get('PATH', '')}:{node}:{Path.home() / '.local/bin'}"
    return env


def probe(timeout: float = 120, cwd: Path = ROOT) -> dict:
    """Current `rate_limits` from a short interactive Claude session (see the module docstring)."""
    env = _clean_env()
    claude = shutil.which("claude", path=env["PATH"])
    if not claude or not shutil.which("script"):
        raise RuntimeError("claude or script (util-linux) is not on PATH")
    with tempfile.TemporaryDirectory(prefix="vg-usage-") as folder:
        out = Path(folder) / "statusline.json"
        settings = {"statusLine": {"type": "command", "command": f"cat > {shlex.quote(str(out))}"}}
        command = shlex.join([claude, "--model", "haiku", "--strict-mcp-config",
                              "--settings", json.dumps(settings), "Reply with OK only."])
        process = subprocess.Popen(["script", "-qfec", command, "/dev/null"], cwd=cwd, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data = json.loads(out.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                if data.get("rate_limits"):
                    return data["rate_limits"]
                if process.poll() is not None:
                    break
                time.sleep(1)
            raise RuntimeError("Claude did not report rate_limits (not a subscription login, or no answer in time)")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def evaluate(rate_limits: dict, max_percent: float | dict[str, float], now: float | None = None) -> dict:
    """{"ok": bool, "windows": {name: {"used_percentage", "resets_at", "max_percent"}}, "max_percent"}.

    `max_percent` is one limit for both windows or {window: limit}."""
    now = time.time() if now is None else now
    if not isinstance(max_percent, dict):
        max_percent = {name: float(max_percent) for name in WINDOWS}
    windows = {}
    for name in WINDOWS:
        window = rate_limits.get(name) or {}
        resets = window.get("resets_at")
        used = 0.0 if resets is not None and resets <= now else float(window.get("used_percentage") or 0)
        windows[name] = {"used_percentage": used, "resets_at": resets, "max_percent": max_percent[name]}
    ok = all(window["used_percentage"] <= window["max_percent"] for window in windows.values())
    return {"ok": ok, "max_percent": max_percent, "windows": windows}


def summary(result: dict) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    parts = []
    for name, window in result["windows"].items():
        reset = window["resets_at"]
        when = f"（{datetime.fromtimestamp(reset, ZoneInfo('Asia/Tokyo')):%m/%d %H:%M}リセット）" if reset else ""
        parts.append(f"{WINDOWS[name]} {window['used_percentage']:g}%{when}")
    over = [f"{WINDOWS[name]}上限{window['max_percent']:g}%" for name, window in result["windows"].items()
            if window["used_percentage"] > window["max_percent"]]
    verdict = "開始可" if result["ok"] else f"{'・'.join(over)}超のため開始しない"
    return f"Claude使用量: {'、'.join(parts)} → {verdict}"


def check() -> tuple[int, str]:
    """(exit code, one-line Japanese summary) for `vg usage --check`."""
    try:
        result = evaluate(probe(), limit())
    except Exception as exc:  # fail closed: an unknown usage never starts a video
        return UNKNOWN, f"Claude使用量を取得できないため開始しない: {type(exc).__name__}: {exc}"
    return (0 if result["ok"] else HIGH), summary(result)
