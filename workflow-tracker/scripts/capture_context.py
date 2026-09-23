#!/usr/bin/env python3
"""Save Claude's status-line context percentage for PenguPool without changing status-line output."""
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path


SID = re.compile(r"[0-9a-fA-F-]{8,64}\Z")


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return max(0.0, float(value))
    return None


def context_percentage(data):
    window = data.get("context_window")
    if not isinstance(window, dict):
        return None
    direct = number(window.get("used_percentage"))
    if direct is not None:
        return round(min(100.0, direct), 1)
    size = number(window.get("context_window_size"))
    usage = window.get("current_usage")
    if not size or usage is None:
        return None
    used = number(usage)
    if isinstance(usage, dict):
        fields = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        values = [number(usage.get(key)) for key in fields if key in usage]
        used = sum(values) if values and all(value is not None for value in values) else None
    return round(min(100.0, used / size * 100), 1) if used is not None else None


def main():
    try:
        data = json.load(sys.stdin)
        sid = data.get("session_id")
        if not isinstance(sid, str) or not SID.fullmatch(sid):
            return
        pct = context_percentage(data)
        if pct is None:
            return
        root = Path(os.environ.get("PENGUPOOL_HOME", Path.home() / ".pengupool")) / "context"
        root.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=f".{sid}.", dir=root)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump({"pct": pct, "ts": time.time()}, out)
            os.replace(temp, root / f"{sid}.json")
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    except (OSError, ValueError, TypeError):
        pass  # a status line must never disturb Claude


if __name__ == "__main__":
    main()
