"""Adjacent-level routing (docs/group-session-framework.md): a grouped session messages only its
parent or a direct child; anything further is routed one edge at a time.

`authorize_send` is the one pure rule both harnesses enforce before delivery — Claude through the
`PreToolUse` hook on `SendMessage` (`python -m pengupool.routing`), pi through the PenguPool pi
extension's `tool_call` handler on pi-intercom sends (`pengupool ctl authorize`)."""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass

from . import model


@dataclass
class Tree:
    """Live topology by session id: parent (None = top level), children, names, harness."""
    parent: dict[str, str | None]
    children: dict[str, list[str]]
    name: dict[str, str]
    harness: dict[str, str]


def live_tree() -> Tree:
    groups = model.GROUPS
    if groups.exists():  # fail closed: a torn/hand-broken groups.json must not read as "no groups"
        json.loads(groups.read_text())
    roots, _, _ = model.snapshot(light=True)
    t = Tree({}, {}, {}, {})

    def walk(n: model.Node, parent: str | None):
        if n.session_id in t.parent:
            return  # the same session resumed twice: its first process stands for it
        t.parent[n.session_id], t.name[n.session_id], t.harness[n.session_id] = parent, n.name, n.harness
        t.children[n.session_id] = []
        if parent:
            t.children[parent].append(n.session_id)
        for c in n.children:
            walk(c, n.session_id)
    for r in roots:
        walk(r, None)
    return t


def resolve(t: Tree, sender: str, recipient: str) -> set[str]:
    """Live sessions of the sender's harness that `recipient` (a name, "name [ref]", or id) names."""
    r = re.sub(r"\s*\[.*\]$", "", str(recipient)).strip()
    h = t.harness.get(sender)
    same = [s for s in t.name if t.harness[s] == h]
    by_id = {s for s in same if s == r or (len(r) >= 8 and s.startswith(r))}
    return by_id or {s for s in same if t.name[s].split("~")[0] == r}


def adjacent(t: Tree, sid: str) -> list[str]:
    return ([t.parent[sid]] if t.parent.get(sid) else []) + t.children.get(sid, [])


def route(t: Tree, current: str, target: str) -> str:
    """Next adjacent hop from `current` toward `target`: down through the child whose subtree holds
    it, otherwise up to the parent. Raises LookupError when no hop exists."""
    if current not in t.parent or target not in t.parent:
        raise LookupError("unknown session")
    if current == target:
        raise LookupError("that is you")
    hop = target
    while t.parent.get(hop) and t.parent[hop] != current:
        hop = t.parent[hop]
    if t.parent.get(hop) == current:
        return hop
    if t.parent.get(current):
        return t.parent[current]
    raise LookupError("owner unknown: it is not in your tree")


GRANTS = model.PENGU / "grants"  # <sid>.json = {"targets": [sid, ...], "ts": epoch}
GRANT_TTL = 3600
MENTION = re.compile(r"(?<![\w.@])@([\w][\w.~-]*)")  # @name / @id, not an email address


def mentions(prompt: str) -> list[str]:
    return [m.rstrip(".") for m in MENTION.findall(prompt or "")]


def grant(sender: str, prompt: str, t: Tree | None = None) -> list[str]:
    """The user's own prompt tags sessions with @name: lift the adjacent rule for sender <-> each tagged
    live session of its harness until the sender's next prompt (at most GRANT_TTL). Called only from
    the prompt hooks, which see what the user typed. Returns the tagged sessions' names."""
    if not re.fullmatch(r"[\w.-]{1,128}", sender):  # sender is a path component
        return []
    names = mentions(prompt)
    t = (t or live_tree()) if names else None
    targets = []
    for n in names:
        hits = resolve(t, sender, n)
        if len(hits) == 1 and (s := next(iter(hits))) != sender and s not in targets:
            targets.append(s)  # an ambiguous tag grants nothing: the send must name a session id
    p = GRANTS / f"{sender}.json"
    if targets:
        model.write_json(p, {"targets": targets, "ts": time.time()})
    else:
        p.unlink(missing_ok=True)
    return [t.name[s] for s in targets]


def granted(a: str, b: str) -> bool:
    """The user tagged b in a's current prompt, or a in b's (so the tagged session can reply)."""
    # ponytail: a file the agent could write itself, like groups.json; a per-prompt token from the hook
    # would make it unforgeable
    for x, y in ((a, b), (b, a)):
        d = model._json(GRANTS / f"{x}.json") if re.fullmatch(r"[\w.-]{1,128}", x) else None
        if isinstance(d, dict) and y in (d.get("targets") or []) and time.time() - float(d.get("ts", 0)) < GRANT_TTL:
            return True
    return False


def authorize_send(sender: str, recipient: str, t: Tree | None = None) -> tuple[bool, str]:
    """(allowed, reason). Only a grouped sender is managed; a solo session keeps its normal behaviour.
    A name that is no live session of this harness (a teammate, a typo) is left to the harness.
    A session the user tagged with @name in the current prompt is reachable directly (see grant)."""
    t = t or live_tree()
    if sender not in t.parent or not adjacent(t, sender):
        return True, ""
    hits = resolve(t, sender, recipient)
    if not hits:
        return True, ""
    ok = adjacent(t, sender)
    if len(hits) == 1 and (next(iter(hits)) in ok or granted(sender, next(iter(hits)))):
        return True, ""
    who = ", ".join(f"{'parent' if s == t.parent.get(sender) else 'child'} {t.name[s]}" for s in ok)
    if len(hits) > 1:
        return False, (f"PenguPool: \"{recipient}\" names {len(hits)} sessions; message by session id. "
                       f"You may message: {who}.")
    target = next(iter(hits))
    try:
        via = f" Send it to {t.name[route(t, sender, target)]} and ask them to route it on."
    except LookupError:
        via = " It is outside your tree: ask your parent, or the user, how to reach it."
    return False, (f"PenguPool: {t.name[target]} is not adjacent to you (parent or direct child), so the "
                   f"message was not sent. You may message: {who}.{via}")


def main() -> None:
    """Claude Code `PreToolUse` hook for `SendMessage`. Crashes exit 2 (via the installed command), which
    blocks the send: a managed session must never message past the guard because PenguPool broke."""
    inp = json.loads(sys.stdin.read() or "{}")
    if inp.get("tool_name") != "SendMessage":
        return
    tool = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    sender, to = str(inp.get("session_id", "")), str(tool.get("to") or tool.get("recipient") or "")
    t = live_tree()
    ok, reason = authorize_send(sender, to, t)
    if ok:
        try:  # a ROUTE REQUIRED child is now messaged: the Stop audit has nothing to say
            from .context import routed
            for hit in resolve(t, sender, to):
                routed(sender, hit)
        except Exception:
            pass
    if not ok:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"PenguPool routing guard failed ({e}); retry the message after PenguPool recovers", file=sys.stderr)
        raise SystemExit(2)
