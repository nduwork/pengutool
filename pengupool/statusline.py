"""Claude Code statusLine wrapper: save the context % for the list and map, then run the user's own
status line unchanged. pi sessions save the same `context/<sid>.json` from the PenguPool pi extension.

    python -m pengupool.statusline                    capture only, print nothing
    python -m pengupool.statusline -- '<command>'     capture, then run <command> on the same stdin

`hook.install` wraps whatever statusLine is configured and `hook.uninstall` restores it exactly."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time

from . import model


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return max(0.0, float(value))
    return None


def context_percentage(data: dict) -> float | None:
    """Claude's own percentage, else used tokens over an explicit window size; never a guessed size."""
    window = data.get("context_window")
    if not isinstance(window, dict):
        return None
    direct = _number(window.get("used_percentage"))
    if direct is not None:
        return round(min(100.0, direct), 1)
    size = _number(window.get("context_window_size"))
    usage = window.get("current_usage")
    if not size or usage is None:
        return None
    used = _number(usage)
    if isinstance(usage, dict):
        fields = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        values = [_number(usage.get(key)) for key in fields if key in usage]
        used = sum(values) if values and all(v is not None for v in values) else None
    return round(min(100.0, used / size * 100), 1) if used is not None else None


def capture(raw: bytes) -> None:
    try:
        data = json.loads(raw)
        sid = data.get("session_id") if isinstance(data, dict) else None
        if not isinstance(sid, str) or not model._SID.fullmatch(sid):
            return
        pct = context_percentage(data)
        if pct is not None:
            model.write_json(model.PENGU / "context" / f"{sid}.json", {"pct": pct, "ts": time.time()})
    except (OSError, ValueError, TypeError):
        pass  # a status line must never disturb Claude


def main(argv: list[str]) -> int:
    raw = sys.stdin.buffer.read()
    capture(raw)
    if argv[:1] == ["--"] and len(argv) > 1:
        return subprocess.run(argv[1], shell=True, input=raw).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
