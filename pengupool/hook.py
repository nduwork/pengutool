"""`pengupool install-hook`: register pengupool.context on Claude lifecycle events, and the
pengupool.routing guard on `SendMessage`."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from .model import CLAUDE, write_json

# never let a broken/uninstalled PenguPool surface as a hook error in every Claude session, or block a
# message: the SendMessage guard denies only a send it checked (docs/group-session-framework.md)
CMD = f"{shlex.quote(sys.executable)} -m pengupool.context 2>/dev/null || true"
GUARD = f"{shlex.quote(sys.executable)} -m pengupool.routing || true"
LEGACY = "pengupool/session_start.sh"


def _without_ours(entries: list) -> list:
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            kept.append(entry)
            continue
        remaining = [h for h in entry["hooks"] if not (
            isinstance(h, dict) and (LEGACY in str(h.get("command", ""))
                                    or "pengupool.context" in str(h.get("command", ""))
                                    or "pengupool.routing" in str(h.get("command", ""))))]
        if remaining or not entry["hooks"]:
            kept.append({**entry, "hooks": remaining})
    return kept


def uninstall(settings: Path = CLAUDE / "settings.json") -> None:
    if not settings.exists():
        return
    try:
        cfg = json.loads(settings.read_text())
    except ValueError as e:
        raise SystemExit(f"{settings} is not valid JSON ({e})")
    if not isinstance(cfg, dict):
        raise SystemExit(f"{settings} is not a JSON object")
    hooks = cfg.get("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"{settings}: 'hooks' is not an object")
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = _without_ours(entries)
        if kept != entries:
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not hooks:
        cfg.pop("hooks", None)
    write_json(settings, cfg)
    print(f"removed PenguPool lifecycle hooks → {settings}")


def install(settings: Path = CLAUDE / "settings.json") -> None:
    try:
        cfg = json.loads(settings.read_text()) if settings.is_file() else {}
    except ValueError as e:
        raise SystemExit(f"{settings} is not valid JSON ({e}); fix it before installing the hook")
    if not isinstance(cfg, dict):
        raise SystemExit(f"{settings} is not a JSON object")
    hooks = cfg.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"{settings}: 'hooks' is not an object")
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop"):
        entries = hooks.get(event) if isinstance(hooks.get(event), list) else []
        # drop the old shell hook and any stale pengupool command (e.g. from another venv path)
        kept = _without_ours(entries)
        kept.append({"hooks": [{"type": "command", "command": CMD}]})
        if event == "PreToolUse":
            kept.append({"matcher": "SendMessage", "hooks": [{"type": "command", "command": GUARD}]})
        hooks[event] = kept
    write_json(settings, cfg)  # atomic: a torn settings.json would break every Claude session
    print(f"installed session lifecycle hooks → {settings}\n  {CMD}")
    if "/.venv/" in sys.executable or "/venv/" in sys.executable:
        print("warning: the hook points at a project virtualenv; install with `make install` so it survives")
