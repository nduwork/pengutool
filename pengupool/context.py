"""Claude Code hook (SessionStart + UserPromptSubmit): tell each session where it sits in the
PenguPool tree and how to talk to the others. Reads ~/.pengupool/tree.json written by the running
TUI (fresh = dynamic), falls back to computing the tree from disk when the TUI is not running.
pi sessions get the same block through the PenguPool pi extension (`pengupool ctl context <sid>`)."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata

from . import harness, model, profiles

TREE = model.PENGU / "tree.json"
FRESH_S = 30

RULES = (
    "Routing rules: message ADJACENT sessions only — your parent and your direct children — with {tool}; "
    "PenguPool blocks any other send. To reach another session, send the request one edge toward it "
    "(`pengupool ctl route {me} <name>` names the next hop) with: request_id, origin, target, objective, "
    "the context the next hop needs, reply_path and hop_limit. When you forward, add yourself to "
    "reply_path, decrement hop_limit and pass on only what the next hop needs; replies travel back along "
    "reply_path. If no child owns a request, ask your parent or answer \"owner unknown\" — never guess."
)
DESCRIBE = ("pengupool ctl describe {me} --summary \"<one line: what you own>\" "
            "--responsibility \"<a short brief of your responsibility>\"")
SCHEMA = 2
LIMIT = 30  # tree lines before the block shows only this session's neighbourhood


def tree_dump(roots: list[model.Node]) -> dict:
    """Serialisable snapshot the TUI writes, keyed by session id (a duplicated id keeps its first process);
    parents and children are ids too, so duplicate display names cannot confuse the tree."""
    out = {"schema": SCHEMA, "written": time.time(), "tui_pid": os.getpid(), "sessions": {}}

    def walk(n: model.Node, parent: str | None):
        if n.session_id in out["sessions"]:
            return
        p = profiles.load(n.session_id)
        out["sessions"][n.session_id] = {
            "name": n.name, "cwd": n.cwd, "repo": n.repo, "state": n.state, "harness": n.harness,
            "parent": parent, "children": [c.session_id for c in n.children],
            "workspace": profiles.workspace_label(p) or n.repo, "summary": p.get("summary", "")}
        for c in n.children:
            walk(c, n.session_id)
    for r in roots:
        walk(r, None)
    return out


def _clean(s: str, n: int = 60) -> str:
    """Names and repos come from the sessions themselves (/rename, cwd) and land in other agents'
    prompts: one plain line, no tags/brackets/quotes, no control, format or separator characters
    (C0/C1, bidi overrides, line separators) so they cannot smuggle instructions or spoof the tree."""
    s = "".join(c for c in str(s) if not unicodedata.category(c).startswith(("C", "Z")) or c == " ")
    s = re.sub(r'[<>\[\]"`]', "", s)
    return s[:n]


def load_tree() -> dict | None:
    try:
        d = json.loads(TREE.read_text())
        if isinstance(d, dict) and d.get("schema") == SCHEMA and time.time() - float(d.get("written", 0)) < FRESH_S:
            return d
    except (OSError, ValueError):
        pass
    try:  # TUI not running: cheap rebuild from sessions + learned/manual parents, no transcripts, no writes
        roots, _, _ = model.snapshot(light=True)
        d = tree_dump(roots)
        try:
            model.write_json(TREE, d)  # amortise across the other sessions' prompts for FRESH_S
        except OSError:
            pass
        return d
    except Exception:
        return None


def _line(s: dict, sid: str) -> str:
    """name #id  [workspace, state] — summary"""
    role = _clean(s.get("summary", ""), profiles.SUMMARY_MAX) or "role not set"
    return f"{_clean(s['name'])} #{sid[:6]}  [{_clean(s.get('workspace') or s['repo'])}, {_clean(s['state'], 8)}] — {role}"


def draw_tree(sess: dict, me: str, full: bool = False) -> list[str]:
    """The tree holding `me`. Past LIMIT lines (unless `full`): the path from the root to me and my
    children only, with a count of what was left out under each."""
    root, seen = me, {me}
    while sess[root].get("parent") in sess and sess[root]["parent"] not in seen:
        root = sess[root]["parent"]
        seen.add(root)
    count, stack = 0, [root]
    while stack:
        count += 1
        stack += [c for c in sess[stack.pop()]["children"] if c in sess]
    keep = None
    if not full and count > LIMIT:
        keep = set(seen) | set(sess[me]["children"])
    lines: list[str] = []

    def draw(sid: str, prefix: str, last: bool, top: bool, path: set):
        branch = "" if top else ("└ " if last else "├ ")
        lines.append(f"{prefix}{branch}{_line(sess[sid], sid)}{'  ← you' if sid == me else ''}")
        kids = [c for c in sess[sid]["children"] if c in sess and c not in path]
        shown = [c for c in kids if keep is None or c in keep]
        inner = prefix + ("" if top else ("   " if last else "│  "))
        for i, k in enumerate(shown):
            draw(k, inner, i == len(shown) - 1 and len(shown) == len(kids), False, path | {k})
        if len(shown) < len(kids):
            lines.append(f"{inner}└ … {len(kids) - len(shown)} more")
    draw(root, "", True, True, {root})
    if keep is not None:
        lines.append(f"({count - len(keep & set(sess))} sessions not shown: `pengupool ctl tree {me}` prints the whole tree)")
    return lines


def render(tree: dict, me: str, solo: bool = False, h: str = "cc") -> str:
    """The block for session `me`; a solo session gets its workspace + role prompt only when `solo`."""
    sess = tree.get("sessions") or {}
    if me not in sess:
        if not solo:
            return ""
        # just started: not in the tree snapshot yet, so it has no pool — describe it from its profile
        sess = {**sess, me: {"name": me[:8], "repo": "", "state": "", "harness": h, "parent": None, "children": []}}
    m = sess[me]
    grouped = bool(m.get("parent") or m.get("children"))
    if not grouped and not solo:
        return ""  # solo session: no pool to describe, keep its context clean
    h = harness.of(m)  # a tree never mixes harnesses (model.apply_groups), so one tool fits all of it
    p = profiles.load(me)
    ws = (p.get("workspace") or {})
    where = f"{_clean(profiles.workspace_label(p) or m.get('workspace') or m['repo'] or '?')} ({ws.get('kind', 'directory')}{', truncated' if ws.get('truncated') else ''})"
    out = [f"You are {harness.LABEL[h]} session \"{_clean(m['name'])}\" (id {me}) in {where}."]
    if p.get("summary") or p.get("responsibility"):
        out.append(f"Your role: {_clean(p.get('summary', ''), profiles.SUMMARY_MAX) or 'no summary'}.")
        if p.get("responsibility"):
            out.append(f"Your responsibility: {_clean(p['responsibility'], profiles.RESPONSIBILITY_MAX)}")
        if p.get("description_source") in ("parent", "user"):
            by = "your parent" if p["description_source"] == "parent" else "the user"
            out.append(f"This description was last set by {by} at {_clean(p.get('updated_at', ''), 20)}: adopt or "
                       "refine it (do not silently overwrite it).")
        out.append("Update it when your enduring responsibility changes: " + DESCRIBE.format(me=me))
    else:
        out.append("Your role is not set. Once your task is clear, record what you own so other sessions can "
                   "route to you: " + DESCRIBE.format(me=me))
    if not grouped:
        out.append("You are not grouped with other sessions.")
        return "<pengupool>\n" + "\n".join(out) + "\n</pengupool>"
    rel = lambda sid: f"{_clean(sess[sid]['name'])} — {_clean(sess[sid].get('summary', ''), profiles.SUMMARY_MAX) or 'role not set'}"
    parent = rel(m["parent"]) if m.get("parent") in sess else "none (you are the root)"
    kids = "; ".join(rel(c) for c in m["children"] if c in sess) or "none"
    return ("<pengupool>\n" + "\n".join(out) + "\nSession tree (managed by PenguPool, updated live when the user "
            "regroups sessions):\n" + "\n".join(draw_tree(sess, me)) + "\n"
            f"Your parent: {parent}.\nYour children: {kids}.\n{RULES.format(tool=harness.TOOL[h], me=me)}\n</pengupool>")


def text_for(sid: str, solo: bool = True, h: str = "cc") -> str:
    """The context block for one session ('' when it has nothing to describe)."""
    return render(load_tree() or {}, sid, solo, h)


def main() -> None:
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        inp = {}
    sid = str(inp.get("session_id", ""))
    event = str(inp.get("hook_event_name", "UserPromptSubmit"))
    if not sid:
        return
    # record the latest lifecycle event so the TUI can show an accurate per-session state
    # (e.g. PermissionRequest -> "blocked"); a later event overwrites it. Never emit anything for
    # tool/permission/stop events — the hook must not influence Claude's approval flow.
    try:
        model.write_json(model.AGENT_STATE / f"{sid}.json", {"event": event, "ts": int(time.time())})
    except OSError:
        pass
    if event == "SessionStart":  # registry line for the TUI's session -> tmux pane mapping
        profiles.register(sid, str(inp.get("cwd") or os.getcwd()))
        model.PENGU.mkdir(parents=True, exist_ok=True)
        with (model.PENGU / "registry.jsonl").open("a") as fh:
            fh.write(json.dumps({"sessionId": sid, "cwd": os.getcwd(), "tmuxPane": os.environ.get("TMUX_PANE", ""),
                                 "ts": int(time.time())}) + "\n")
    if event not in ("SessionStart", "UserPromptSubmit"):
        return  # only these two inject the tree; other events just recorded state above
    # a solo session hears about its workspace and role once, at start, not on every prompt
    text = render(load_tree() or {}, sid, solo=event == "SessionStart")
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


if __name__ == "__main__":
    main()
