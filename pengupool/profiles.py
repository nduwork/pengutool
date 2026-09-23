"""Session profiles: what each session owns, keyed by session id.

~/.pengupool/profiles/<sid>.json = {schema, session_id, workspace, summary, responsibility,
description_source, description_editor, updated_at}. One file per session, so a session can update
its own description without rewriting the shared groups.json. Shared by both harnesses."""
from __future__ import annotations

import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import model

PROFILES = model.PENGU / "profiles"
SUMMARY_MAX, RESPONSIBILITY_MAX = 120, 600
INDICATORS = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
              "Gemfile", "requirements.txt", "Makefile", "Dockerfile", "main.nf", "Snakefile")
MAX_ENTRIES, MAX_REPOS, BUDGET_S = 500, 20, 0.2


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(sid: str) -> Path | None:
    return PROFILES / f"{sid}.json" if model._SID.fullmatch(sid) else None  # sid is a path component


def load(sid: str) -> dict:
    p = _path(sid)
    d = model._json(p) if p else None
    return d if isinstance(d, dict) and d.get("session_id") == sid else {}


def _save(d: dict) -> dict:
    model.write_json(_path(d["session_id"]), d)
    return d


def scan(cwd: str) -> dict:
    """One bounded look at a directory: no recursion, no file contents, no git, no symlinked dirs."""
    out = {"kind": "unavailable", "root": cwd, "scanned_at": _now()}
    p = Path(cwd)
    if not cwd or not p.is_dir():
        return out
    for d in (p, *p.parents):  # a .git dir (repo) or file (worktree) here or above
        if (d / ".git").exists():
            return {**out, "kind": "repo", "root": str(d), "repos": [d.name]}
    t0, repos, found, truncated = time.monotonic(), [], [], False
    try:
        with os.scandir(p) as it:
            for i, e in enumerate(it):
                if i >= MAX_ENTRIES or time.monotonic() - t0 > BUDGET_S:
                    truncated = True
                    break
                if e.name in INDICATORS:
                    found.append(e.name)
                elif e.is_dir(follow_symlinks=False) and os.path.exists(os.path.join(e.path, ".git")):
                    repos.append(e.name)
    except OSError:
        return out
    if repos:
        out.update(kind="collection", repos=sorted(repos)[:MAX_REPOS])
        truncated = truncated or len(repos) > MAX_REPOS
    else:
        out.update(kind="directory", repos=[p.name], indicators=sorted(found))
    if truncated:
        out["truncated"] = True
    return out


def register(sid: str, cwd: str) -> dict:
    """SessionStart: create the profile, rescanning only when the directory changed. A resumed
    session keeps its description."""
    if not _path(sid):
        return {}
    d = load(sid) or {"schema": 1, "session_id": sid, "summary": "", "responsibility": "",
                      "description_source": "", "description_editor": "", "updated_at": ""}
    if d.get("workspace", {}).get("cwd") != cwd:
        d["workspace"] = {**scan(cwd), "cwd": cwd}
        _save(d)
    return d


def workspace_label(p: dict) -> str:
    w = p.get("workspace") or {}
    repos = w.get("repos") or []
    if w.get("kind") == "collection":
        return f"{len(repos)} repos" + ("+" if w.get("truncated") else "")
    return repos[0] if repos else ""


def clean(text: str, n: int) -> str:
    """One plain line: descriptions land in other agents' prompts, like names do (context._clean)."""
    s = "".join(c if not unicodedata.category(c).startswith(("C", "Z")) else " " for c in str(text))
    s = re.sub(r"\s+", " ", re.sub(r'[<>\[\]"`]', "", s)).strip()
    return s[:n]


def caller() -> str:
    """The live session this process runs under ('' = not inside a session, i.e. the user).
    Found by walking the real process tree, never from an argument the caller could forge."""
    by_pid = {s["pid"]: s["sessionId"] for s in model.load_sessions()}
    pid, seen = os.getppid(), set()
    while pid > 1 and pid not in seen:
        if pid in by_pid:
            return by_pid[pid]
        seen.add(pid)
        pid = model.PROCS.ppid(pid)
    # ponytail: an agent that double-forks out of its session's process tree reads as the user; the
    # env marker catches the ordinary case. Real isolation needs a per-session token from the hook.
    if os.environ.get("CLAUDECODE") or os.environ.get("PENGUPOOL_SESSION"):
        raise PermissionError("cannot tell which session is calling; run this from the session itself")
    return ""


def parent_of(sid: str) -> str:
    """Current parent session id in the live tree ('' at the top level)."""
    roots, _, _ = model.snapshot(light=True)

    def walk(n: model.Node, parent: str) -> str | None:
        if n.session_id == sid:
            return parent
        for c in n.children:
            r = walk(c, n.session_id)
            if r is not None:
                return r
        return None
    for r in roots:
        hit = walk(r, "")
        if hit is not None:
            return hit
    return ""


def describe(sid: str, summary: str | None, responsibility: str | None, editor: str | None = None) -> dict:
    """Set a session's summary/responsibility. `editor` is the calling session ('' = the user), resolved
    by the caller with caller(). A session may edit itself or a direct child; the user may edit any."""
    if not _path(sid) or sid not in {s["sessionId"] for s in model.load_sessions()}:
        raise ValueError(f"no live session {sid}")
    editor = caller() if editor is None else editor
    if editor and editor != sid and parent_of(sid) != editor:
        raise PermissionError("a session may only describe itself or one of its direct children")
    d = load(sid) or register(sid, next((s["cwd"] for s in model.load_sessions() if s["sessionId"] == sid), ""))
    if summary is not None:
        d["summary"] = clean(summary, SUMMARY_MAX)
    if responsibility is not None:
        d["responsibility"] = clean(responsibility, RESPONSIBILITY_MAX)
    d.update(description_source="session" if editor == sid else ("parent" if editor else "user"),
             description_editor=editor or "user", updated_at=_now())
    return _save(d)
