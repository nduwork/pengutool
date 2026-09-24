"""Claude Code hook (SessionStart + UserPromptSubmit): tell each session where it sits in the
PenguPool tree and how to talk to the others. Reads the cached ~/.pengupool/tree.json while
fresh, else computes the tree from disk and rewrites the cache for the other sessions' prompts.
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
SEEN = model.PENGU / "seen"  # <sessionId>.json = {"parent": id|None, "children": [ids]} as last shown to it
FRESH_S = 30

RULES = (
    "Routing rules: message ADJACENT sessions only — your parent and your direct children — with {tool}; "
    "PenguPool blocks any other send. To reach another session, send the message one edge toward it (toward "
    "your parent only as an inquiry) "
    "(`pengupool ctl route {me} <name>` names the next hop) with: request_id, origin, target, objective, "
    "the context the next hop needs, reply_path and hop_limit. When you forward, add yourself to "
    "reply_path, decrement hop_limit and pass on only what the next hop needs; replies travel back along "
    "reply_path and carry its request_id and reply_path. Address sessions by name, never by socket: reply "
    "to a message's from-name, not its uds: from address, which PenguPool refuses. Never pass an action a "
    "permission check denied you to another session, up or down the tree: tell the user instead."
)
# Work flows down, questions flow up: a parent delegates to its children, a child only inquires of its
# parent, and the parent triages that inquiry like any request (does it, delegates it, or inquires upward).
TRIAGE_DO = "Before you act on any request, triage it against the roles above. Yours: do it."
TRIAGE_DOWN = ("A child owns part of it: delegate that part to that child with {tool} and integrate the reply. "
               "An inquiry from a child is a request to triage the same way: handle it, delegate it to another "
               "child, or {up}, then answer the child.")
TRIAGE_UP = ("Not yours or your children's, or you are unsure: inquire with your parent (ask who should handle "
             "it; never assign work upward), tell whoever asked you (the user or a child) that you did, and start on your own part meanwhile. If "
             "the request came from your parent and is not yours, reply that instead of inquiring back.")
TRIAGE_ROOT = "Owned by no session in this tree: tell the user."
TRIAGE_END = ("Split a mixed request into its parts; do not do work a child owns, and do not push back work that "
              "is yours. Where roles are not set, judge by names and workspaces, and ask rather than guess. "
              "Begin every reply to the user with one line: `Triage: → <session>`, `Triage: mine` or "
              "`Triage: asked parent` (optional inside a {tool} message).")
DESCRIBE = ("pengupool ctl describe {me} --summary \"<one line: what you own>\" "
            "--responsibility \"<a short brief of your responsibility>\" --keywords \"<routing terms, comma-separated>\"")
# A hint found by code: the hook compares the prompt with each child's routing terms and, on a hit, names
# the child and the words that matched. Whether to route stays the session's call; nothing blocks on it.
ROUTE = ("ROUTE CHECK: this request matches your child {who}. If it owns part of this, send it that part "
         "with {tool} to {first} before doing yours; a stale child still queues the message. If the match is "
         "wrong or the user said to do it yourself, say so in your Triage line.")
ROLE_REQUIRED = ("ROLE REQUIRED: your role is not set, so other sessions cannot route work to you. Before anything "
                 "else this turn, run: {cmd} — base it on this request and your workspace; refine it later as "
                 "your work becomes clearer.")
ORG_CHANGED = ("ORG CHANGED: the user regrouped sessions since your last turn ({what}). Session trees "
               "earlier in this conversation are out of date: route by the tree below, and update any notes or "
               "memory you keep about who owns what.")
COMPACTED = ("You were just compacted: work carried over in the summary may belong to another session in the "
             "tree. Triage it again before resuming; do not treat it as yours because you remember it.")
SELF = re.compile(r"\b(?:do|handle|fix|check) (?:it|this|that)(?: all)? (?:by )?yourself\b|\bdon'?t (?:delegate|route)\b"
                  r"|\bno delegation\b", re.I)
# a prompt the harness delivered for another session (its message, an idle notice, a subagent's report):
# not the user's request, so it neither triggers a route check nor grants an @session line
RELAYED = re.compile(r"\s*(?:Another Claude session sent a message:|\[Cross-session idle notice\]"
                     r"|<(?:task-notification|cross-session-message|agent-message)\b)")
STOPWORDS = frozenset("""about after again against because before being below between could doing during each
    every from have having here into itself just more most other over same should some such than that their
    them then there these they this those through under until very what when where which while will with
    would your yours session sessions owns owner handle handles work working worker agent default main""".split())
SCHEMA = 2
LIMIT = 30  # tree lines before the block shows only this session's neighbourhood


def tree_dump(roots: list[model.Node]) -> dict:
    """Serialisable snapshot cached in tree.json, keyed by session id (a duplicated id keeps its first process);
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
        w = float(d.get("written", 0)) if isinstance(d, dict) else 0
        if w and d.get("schema") == SCHEMA and time.time() - w < FRESH_S and not _changed_since(w):
            return d
    except (OSError, ValueError):
        pass
    try:  # stale or missing: cheap rebuild from sessions + learned/manual parents, no transcripts, no writes
        roots, _, _ = model.snapshot(light=True)
        d = tree_dump(roots)
        try:
            model.write_json(TREE, d)  # amortise across the other sessions' prompts for FRESH_S
        except OSError:
            pass
        return d
    except Exception:
        return None


def _changed_since(ts: float) -> bool:
    """A regroup or a role change after the cache was written: rebuild now, not FRESH_S later.
    The profiles directory's mtime moves on every profile write because model.write_json renames into it."""
    for f in (model.GROUPS, profiles.PROFILES):
        try:
            if f.stat().st_mtime >= ts:
                return True
        except OSError:
            pass
    return False


def org_change(tree: dict, sid: str) -> str:
    """What moved around `sid` (its parent, its children) since the last block it was shown, as an
    ORG CHANGED line ('' when nothing did, or on its first block); records what it is shown now."""
    sess = tree.get("sessions") or {}
    m = sess.get(sid) or {}
    now = {"parent": m.get("parent") if m.get("parent") in sess else None,
           "children": sorted(c for c in m.get("children", []) if c in sess)}
    if not model._SID.fullmatch(sid):  # sid is a path component
        return ""
    f = SEEN / f"{sid}.json"
    was = model._json(f)
    if was == now:
        return ""
    try:
        model.write_json(f, now)
    except OSError:
        pass
    if not isinstance(was, dict):
        return ""
    nm = lambda s: "none" if not s else _clean(sess[s]["name"]) if s in sess else "a closed session"
    what = []
    if was.get("parent") != now["parent"]:
        what.append(f"parent: {nm(was.get('parent'))} → {nm(now['parent'])}")
    old = set(was.get("children") or [])
    if added := [nm(c) for c in now["children"] if c not in old]:
        what.append("children added: " + ", ".join(added))
    if removed := [nm(c) for c in sorted(old - set(now["children"]))]:
        what.append("children removed: " + ", ".join(removed))
    return ORG_CHANGED.format(what="; ".join(what)) if what else ""


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
    count, stack, counted = 0, [root], {root}
    while stack:  # counted: a children cycle (the same id reached twice) must not loop forever
        count += 1
        kids = [c for c in sess[stack.pop()]["children"] if c in sess and c not in counted]
        counted.update(kids)
        stack += kids
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


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]", str(text).lower()) if len(w) >= 3}


def _parts(name: str) -> set[str]:
    """Terms of a session name or workspace: every run of its pieces between - _ ~ . as written
    ("oh-tidepool-portal" gives oh-tidepool, tidepool-portal, tidepool, …). Both sides are split the same
    way, so a repo prefix a parent shares with its child cancels out whatever its length."""
    bits = re.split(r"([-_~.])", str(name).lower())   # pieces at even indexes, separators between
    runs = {"".join(bits[i:j + 1]) for i in range(0, len(bits), 2) for j in range(i, len(bits), 2)}
    return {t for r in runs for t in _terms(r)}


def _said(term: str, prompt: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", prompt) is not None


def route_match(tree: dict, me: str, prompt: str) -> list[tuple[str, str, list[str]]]:
    """[(child sid, name, matched terms)] for each direct child the user's prompt is about, found by code:
    a routing keyword, the child's name or its workspace said in the prompt, or two words of its role.
    Terms that also describe `me` never count (a child in my own repo is not matched by the repo name).
    Nothing when the user said to do it themselves."""
    sess = tree.get("sessions") or {}
    if me not in sess or not prompt or SELF.search(prompt):
        return []
    text = prompt.lower()
    mine_p = profiles.load(me)
    mine = (_parts(sess[me]["name"]) | _parts(sess[me].get("workspace") or sess[me].get("repo", ""))
            | set(mine_p.get("keywords") or []) | _terms(mine_p.get("summary", "")))
    out = []
    for c in sess[me].get("children") or []:
        if c not in sess:
            continue
        p = profiles.load(c)
        strong = (set(p.get("keywords") or []) | _parts(sess[c]["name"])
                  | _parts(sess[c].get("workspace") or "")) - mine - STOPWORDS
        weak = {w for w in _terms(f"{p.get('summary', '')} {p.get('responsibility', '')}")
                if len(w) >= 5} - mine - STOPWORDS - strong
        hits = sorted(t for t in strong if _said(t, text))
        soft = sorted(t for t in weak if _said(t, text))
        if hits or len(soft) >= 2:
            out.append((c, sess[c]["name"], (hits + soft)[:4]))
    return out


def has_role(sid: str) -> bool:
    p = profiles.load(sid)
    return bool(p.get("summary") or p.get("responsibility"))


def render(tree: dict, me: str, solo: bool = False, h: str = "cc", tagged: list[str] = (),
           route: list[tuple[str, str, list[str]]] = (), ask_role: bool = False) -> str:
    """The block for session `me`; a solo session gets its workspace + role prompt only when `solo`.
    `tagged`: sessions the user @-tagged in this prompt, which `me` may message directly.
    `route`: route_match's hits, stated first as a directive. `ask_role`: a role-less session is told
    to describe itself this turn (ROLE REQUIRED), not just someday."""
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
                       "refine it (do not silently overwrite it); leaving it unchanged adopts it.")
        out.append("Update it when your enduring responsibility changes: " + DESCRIBE.format(me=me))
    elif not ask_role:
        out.append("Your role is not set. Once your task is clear, record what you own so other sessions can "
                   "route to you: " + DESCRIBE.format(me=me))
    top = ""
    if route:
        who = "; ".join(f"{_clean(n)} (matched: {', '.join(_clean(t, 30) for t in ts)})" for _, n, ts in route)
        top += ROUTE.format(who=who, tool=harness.TOOL[h], first=_clean(route[0][1])) + "\n"
    if ask_role and not (p.get("summary") or p.get("responsibility")):
        top += ROLE_REQUIRED.format(cmd=DESCRIBE.format(me=me)) + "\n"
    if not grouped:
        out.append("You are not grouped with other sessions.")
        return "<pengupool>\n" + top + "\n".join(out) + "\n</pengupool>"
    rel = lambda sid: f"{_clean(sess[sid]['name'])} — {_clean(sess[sid].get('summary', ''), profiles.SUMMARY_MAX) or 'role not set'}"
    parent = rel(m["parent"]) if m.get("parent") in sess else "none (you are the root)"
    kids = "; ".join(rel(c) for c in m["children"] if c in sess) or "none"
    roleless = [c for c in m["children"] if c in sess and not sess[c].get("summary")]
    if roleless:  # routing to a child depends on its role, and a parent may describe its children
        named = ", ".join(f"{_clean(sess[c]['name'])} (id {c})" for c in roleless)
        kids += (f".\nChildren without a role: {named}. "
                 "Work cannot be routed to them reliably: describe each with pengupool ctl describe <child id> "
                 "--summary … --responsibility … --keywords …, or ask them to describe themselves")
    has_kids = any(c in sess for c in m["children"])
    up = m.get("parent") in sess  # the root has nowhere to inquire: it tells the user
    down = TRIAGE_DOWN.format(tool=harness.TOOL[h], up="inquire upward" if up else "tell the user it has no owner here")
    triage = " ".join([TRIAGE_DO, *([down] if has_kids else []), TRIAGE_UP if up else TRIAGE_ROOT,
                       TRIAGE_END.format(tool=harness.TOOL[h])])
    return ("<pengupool>\n" + top + "\n".join(out) + "\nSession tree (managed by PenguPool, updated live when the user "
            "regroups sessions):\n" + "\n".join(draw_tree(sess, me)) + "\n"
            f"Your parent: {parent}.\nYour children: {kids}.\n{triage}\n{RULES.format(tool=harness.TOOL[h], me=me)}\n"
            + (f"The user tagged {', '.join('@' + _clean(n) for n in tagged)} in this prompt: message them directly "
               "with " + harness.TOOL[h] + " (the adjacent rule is lifted for them until the next prompt).\n" if tagged else "")
            + "</pengupool>")


def tag(sid: str, prompt: str) -> list[str]:
    """Record the user's @session tags for this prompt (routing.grant); never break the prompt over it."""
    try:
        from . import routing
        return routing.grant(sid, prompt)
    except Exception:
        return []


def text_for(sid: str, solo: bool = True, h: str = "cc", prompt: str | None = None, grant: bool = True) -> str:
    """The context block for one session ('' when it has nothing to describe). `prompt`: the user's
    prompt this block precedes, whose @session tags lift the adjacent rule for them, but only when
    `grant` (the caller proved the prompt is the user's)."""
    tagged = tag(sid, prompt) if prompt is not None and grant else []
    tree = load_tree() or {}
    ask, solo_ask = ask_role(tree, sid) if prompt is not None else (False, False)
    return render(tree, sid, solo or solo_ask, h, tagged, route_match(tree, sid, prompt or ""), ask)


def ask_role(tree: dict, sid: str) -> tuple[bool, bool]:
    """(ask, solo) for a prompt: a role-less grouped session is asked on every prompt; a role-less solo session only on its first prompt, then left alone (nothing routes to it)."""
    if has_role(sid):
        return False, False
    m = (tree.get("sessions") or {}).get(sid) or {}
    if m.get("parent") or m.get("children"):
        return True, False
    p = profiles.load(sid)
    if not p or p.get("role_asked"):
        return False, False
    p["role_asked"] = True
    try:
        model.write_json(profiles._path(sid), p)
    except OSError:
        pass
    return True, True


def main() -> None:
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        inp = {}
    sid = str(inp.get("session_id", ""))
    event = str(inp.get("hook_event_name", "UserPromptSubmit"))
    if not sid:
        return
    # record the latest lifecycle event so the front-ends can show an accurate per-session state
    # (e.g. PermissionRequest -> "blocked"); a later event overwrites it. Never emit anything for
    # tool/permission events — the hook must not influence Claude's approval flow.
    try:
        model.write_json(model.AGENT_STATE / f"{sid}.json", {"event": event, "ts": int(time.time())})
    except OSError:
        pass
    if event == "SessionStart":  # registry line for the session -> tmux pane mapping
        profiles.register(sid, str(inp.get("cwd") or os.getcwd()))
        model.PENGU.mkdir(parents=True, exist_ok=True)
        with (model.PENGU / "registry.jsonl").open("a") as fh:
            fh.write(json.dumps({"sessionId": sid, "cwd": os.getcwd(), "tmuxPane": os.environ.get("TMUX_PANE", ""),
                                 "ts": int(time.time())}) + "\n")
    if event not in ("SessionStart", "UserPromptSubmit"):
        return  # only these two inject the tree; other events just recorded state above
    # a solo session hears about its workspace and role once, at start, not on every prompt
    prompt = str(inp.get("prompt") or "") if event == "UserPromptSubmit" else ""
    relayed = bool(RELAYED.match(prompt))
    if relayed:
        prompt = ""
    # a relayed prompt leaves the user's @session grant alone: a reply must not cut the line it came on
    tagged = tag(sid, prompt) if event == "UserPromptSubmit" and not relayed else []
    tree = load_tree() or {}
    route = route_match(tree, sid, prompt)
    ask, solo_ask = ask_role(tree, sid) if event == "UserPromptSubmit" else (False, False)
    text = render(tree, sid, solo=event == "SessionStart" or solo_ask, tagged=tagged, route=route, ask_role=ask)
    if text and "Session tree" in text:
        pre = [line for line in (org_change(tree, sid),
                                 COMPACTED if event == "SessionStart" and inp.get("source") == "compact" else "") if line]
        if pre:
            text = text.replace("<pengupool>\n", "<pengupool>\n" + "\n".join(pre) + "\n", 1)
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


if __name__ == "__main__":
    main()
