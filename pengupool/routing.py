"""Adjacent-level routing (docs/group-session-framework.md): a grouped session messages only its
parent or a direct child; anything further is routed one edge at a time.

`authorize_send` is the one pure rule both harnesses enforce before delivery — Claude through the
`PreToolUse` hook on `SendMessage` (`python -m pengupool.routing`), pi through the PenguPool pi
extension's `tool_call` handler on pi-intercom sends (`pengupool ctl authorize`)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import time
from dataclasses import dataclass, field

from . import model


@dataclass
class Tree:
    """Live topology by session id: parent (None = top level), children, names, harness."""
    parent: dict[str, str | None]
    children: dict[str, list[str]]
    name: dict[str, str]
    harness: dict[str, str]
    sock: dict[str, str] = field(default_factory=dict)  # "uds:<messagingSocketPath>" -> session id


def live_tree() -> Tree:
    groups = model.GROUPS
    if groups.exists():  # raise, not "no groups": the guard retries a torn groups.json
        json.loads(groups.read_text())
    roots, _, _ = model.snapshot(light=True)
    if model.TORN:  # raise, not a vanished session: the guard retries a file caught mid-write
        raise RuntimeError(f"session file mid-write: {model.TORN[0]}")
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
    for s in model.load_sessions():  # Claude also addresses peers by socket: `uds:<messagingSocketPath>`
        if isinstance(s.get("messagingSocketPath"), str) and s["sessionId"] in t.parent:
            t.sock[f"uds:{s['messagingSocketPath']}"] = s["sessionId"]
    return t


def resolve(t: Tree, sender: str, recipient: str) -> set[str]:
    """Live sessions of the sender's harness that `recipient` (a name, "name [ref]", or id) names."""
    r = re.sub(r"\s*\[.*\]$", "", str(recipient)).strip()
    h = t.harness.get(sender)
    same = [s for s in t.name if t.harness[s] == h]
    if r.startswith("uds:"):
        return {t.sock[r]} if t.sock.get(r) in same else set()
    by_id = {s for s in same if s == r or (len(r) >= 8 and s.startswith(r))}
    return by_id or {s for s in same if t.name[s].split("~")[0].casefold() == r.casefold()}


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
KEYS = model.PENGU / "grant-keys"  # <sid>.json = {"sha256": hex, "holder": pid}: who may record @-grants


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_key(sid: str, token: str, holder: int) -> bool:
    """The pi extension keeps a random secret in memory and registers its hash at session start. A key is
    replaced only once its holder process is gone (a restart), so an agent's own shell cannot swap in
    its own. Claude sessions need none: their grants come only from Claude's prompt hook."""
    if not re.fullmatch(r"[\w.-]{1,128}", sid) or len(token) < 32:
        return False
    p = KEYS / f"{sid}.json"
    cur = model._json(p) or {}
    held = cur.get("holder")
    if isinstance(held, int) and model.pid_alive(held):  # even the same pid: `bash -c` can exec as it
        return hmac.compare_digest(str(cur.get("sha256", "")), _digest(token))
    model.write_json(p, {"sha256": _digest(token), "holder": holder})
    return True


def key_ok(sid: str, token: str) -> bool:
    """True when `token` is the registered secret of sid's pi extension (so this prompt is the user's)."""
    # ponytail: same-uid isolation only; an agent that reads process memory or rewrites the key file
    # after killing the holder can still forge. Upgrade path: an OS keychain or a per-user socket.
    if not token or not re.fullmatch(r"[\w.-]{1,128}", sid):
        return False
    d = model._json(KEYS / f"{sid}.json") or {}
    return hmac.compare_digest(str(d.get("sha256", "")), _digest(token))
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
    ok = adjacent(t, sender)
    if not recipient.strip():  # e.g. pi-intercom's send by cwd: no name to check, so no free pass
        who = ", ".join(t.name[s] for s in ok)
        return False, f"PenguPool: name the session you are messaging (to=…); you may message: {who}."
    hits = resolve(t, sender, recipient)
    if not hits and str(recipient).strip().startswith("uds:"):  # a session address we cannot map: refuse
        who = ", ".join(t.name[s] for s in ok)
        return False, f"PenguPool: {recipient} is not a session you may message; message by name: {who}."
    if not hits:
        return True, ""
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
    """Claude Code `PreToolUse` hook for `SendMessage`. It denies only a send it has checked and found
    non-adjacent: when PenguPool can't read the tree (a file mid-write, a bug), it retries briefly and then
    lets the message through, so a PenguPool fault never cuts sessions off from each other."""
    inp = json.loads(sys.stdin.read() or "{}")
    if inp.get("tool_name") != "SendMessage":
        return
    tool = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    sender, to = str(inp.get("session_id", "")), str(tool.get("to") or tool.get("recipient") or "")
    for attempt in range(3):
        try:
            t = live_tree()
            break
        except Exception as e:
            if attempt == 2:
                print(f"PenguPool routing guard skipped ({e}); the message was sent unchecked", file=sys.stderr)
                return
            time.sleep(0.1)
    ok, reason = authorize_send(sender, to, t)
    if not ok:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never block a message because the guard itself broke
        print(f"PenguPool routing guard skipped ({e}); the message was sent unchecked", file=sys.stderr)
