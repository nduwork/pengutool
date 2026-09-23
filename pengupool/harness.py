"""The few things that differ between agent harnesses: Claude Code ("cc") and pi ("pi").

Everything else is shared: a pi session is made to look like a Claude one. The PenguPool pi
extension (pi-extension/) writes a live-session file in the same shape as Claude Code's
`~/.claude/sessions/*.json`, the same `context/<sid>.json` and the same registry line, so discovery,
liveness, state and ctx % reuse the Claude code paths. Harnesses never mix: each has its own tmux
server and a session may only be grouped with sessions of its own harness.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

HARNESSES = ("cc", "pi")
CLI = {"cc": "claude", "pi": "pi"}
LABEL = {"cc": "Claude Code", "pi": "pi"}
SOCK = {"cc": "pengupool", "pi": "pengupool-pi"}  # one tmux sessions server (-L) per harness
TOOL = {"cc": "SendMessage", "pi": "the intercom tool (action send or ask)"}
# slash command that renames a live session; pi-intercom's /alias sets the name intercom addresses
RENAME = {"cc": "/rename", "pi": "/alias"}

PI = Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent"))


def check(h: str) -> str:
    """Validate a harness name from argv or JSON; raise so ctl reports it instead of guessing."""
    if h not in HARNESSES:
        raise ValueError(f"unknown harness {h!r} (expected one of {', '.join(HARNESSES)})")
    return h


def of(s: dict) -> str:
    """A session's harness; records written before harnesses existed are Claude's."""
    h = s.get("harness")
    return h if h in HARNESSES else "cc"


# PenguPool hosts every session in tmux, where Claude's "auto" teammateMode splits agent-team teammates
# into panes that pop open over the work pane. Keep them in-process, as Claude does outside tmux.
CC_FLAGS = ["--teammate-mode", "in-process"]


def argv_new(h: str, name: str) -> list[str]:
    return [CLI["cc"], *CC_FLAGS, "--name", name] if check(h) == "cc" else [CLI["pi"], "--name", name]


def argv_resume(h: str, name: str, sid: str) -> list[str]:
    # pi keeps the session's name in the session file itself (session_info entry)
    if check(h) == "cc":
        return [CLI["cc"], *CC_FLAGS, "--name", name, "--resume", sid]
    return [CLI["pi"], "--session", sid]


def pi_dir(cwd: str) -> Path:
    """pi's per-cwd session folder: `--<cwd without leading slash, / \\ : → ->--` (session-manager.js)."""
    return PI / "sessions" / ("--" + re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", cwd)) + "--")


def pi_transcript(cwd: str, sid: str) -> Path | None:
    """pi names files `<timestamp>_<uuid>.jsonl`; newest wins if a session was ever re-created."""
    hits = sorted(pi_dir(cwd).glob(f"*_{sid}.jsonl"))
    return hits[-1] if hits else None
