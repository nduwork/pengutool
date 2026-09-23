"""Read Claude Code's on-disk state and build session trees. Pure functions, no TUI."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import harness

CLAUDE = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
PENGU = Path(os.environ.get("PENGUPOOL_HOME", Path.home() / ".pengupool"))
# live-session files per harness; the pi extension writes Claude-shaped files into PI_LIVE
PI_LIVE = PENGU / "pi-sessions"
STALE_S = 600
MSG_LABEL_MAX = 4000  # retained message text so the log/webview can show full details (never cropped)
SYM = {"done": "✓", "active": "●", "failed": "✗"}


def local_log_time(value: str) -> str:
    """Format an ISO timestamp in the machine's local timezone for compact log rows."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%m/%d/%Y-%H:%M:%S")
    except (TypeError, ValueError):
        return value


@dataclass
class Node:
    session_id: str
    name: str
    cwd: str
    pid: int
    state: str  # active | waiting | stale
    status_line: str = ""
    children: list["Node"] = field(default_factory=list)
    label: str = ""  # instruction on the edge from parent
    tmux_pane: str = ""
    started: float = 0.0  # session startedAt (epoch seconds)
    ctx_pct: float | None = None  # Claude status-line % of context used (None = unavailable)
    harness: str = "cc"  # "cc" | "pi" (see harness.py)
    summary: str = ""  # one-line role from the session's profile ('' = role not set)

    @property
    def repo(self) -> str:
        if not self.cwd or not os.path.isdir(self.cwd):
            return ""
        p = Path(self.cwd)
        for d in (p, *p.parents):
            if (d / ".git").exists():
                return d.name
        return p.name


Edge = tuple[str, str, str]  # (src name, dst name, label)


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def write_json(p: Path, data) -> None:
    """Atomic: several hooks and the TUI may write the same file; a reader must never see a torn file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


def load_context_pct(sid: str, max_age: float = 300) -> float | None:
    """Read a recent percentage captured from Claude's status-line JSON; never infer capacity."""
    if not _SID.fullmatch(sid):
        return None
    data = _json(PENGU / "context" / f"{sid}.json")
    if not isinstance(data, dict):
        return None
    pct, ts = data.get("pct"), data.get("ts")
    if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x)
           for x in (pct, ts)):
        return None
    if not (0 <= pct <= 100 and 0 <= time.time() - ts <= max_age):
        return None
    return round(float(pct), 1)


class ProcTable:
    """One `ps` per poll: pid -> (ppid, lstart, comm). Used to detect pid reuse (a dead session's file
    lingers; if the OS recycles its pid we must not list, kill or adopt the new owner) and to walk
    parent chains without spawning `ps` per pid."""

    def __init__(self):
        self.rows: dict[int, tuple[int, str, str]] = {}
        self.at = 0.0

    def refresh(self, max_age: float = 0.8) -> "ProcTable":
        if time.time() - self.at < max_age:
            return self
        rows: dict[int, tuple[int, str, str]] = {}
        try:
            out = subprocess.run(["ps", "-axo", "pid=,ppid=,lstart=,comm="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            rest = parts[2].split()
            if len(rest) < 6:
                continue
            rows[int(parts[0])] = (int(parts[1]), " ".join(rest[:5]), " ".join(rest[5:]))
        self.rows, self.at = rows, time.time()
        return self

    def alive(self, pid: int, proc_start: str | None = None) -> bool:
        row = self.rows.get(pid)
        if row is None:
            return False
        if proc_start:
            mine, theirs = _start_epoch(proc_start, utc=True), _start_epoch(row[1], utc=False)
            if mine and theirs and abs(mine - theirs) > 2:
                return False  # pid recycled by another process
        return True

    def ppid(self, pid: int) -> int:
        row = self.rows.get(pid)
        return row[0] if row else 0


def _start_epoch(s: str, utc: bool) -> float:
    """'Fri Sep 18 00:39:01 2026' → epoch. Claude writes procStart in UTC; `ps lstart` is local time."""
    try:
        t = time.strptime(" ".join(str(s).split()), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return 0.0
    import calendar
    return float(calendar.timegm(t)) if utc else time.mktime(t)


PROCS = ProcTable()
_SID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def pid_alive(pid: int, proc_start: str | None = None) -> bool:
    if pid <= 1:
        return False
    if PROCS.rows or PROCS.refresh().rows:
        return PROCS.refresh().alive(pid, proc_start)
    try:  # ps unavailable: fall back to a bare liveness probe
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


_SESSION_CACHE: dict[str, dict] = {}  # last good parse per file: Claude Code rewrites these
# frequently and not atomically, so an active session's file reads torn (→ None) now and then;
# without this it would vanish from the tree/map for that poll and the UI would jump to the first row.


def load_sessions(now: float | None = None) -> list[dict]:
    now = now or time.time()
    out = []
    PROCS.refresh()
    for p, h in [(p, "cc") for p in (CLAUDE / "sessions").glob("*.json")] + \
                [(p, "pi") for p in PI_LIVE.glob("*.json")]:
        d = _json(p)
        if not isinstance(d, dict) or not isinstance(d.get("sessionId"), str) or not _SID.match(d["sessionId"]) \
                or not isinstance(d.get("pid"), int) or isinstance(d.get("pid"), bool) or d["pid"] <= 1:
            d = _SESSION_CACHE.get(str(p))  # torn read: fall back to the last good parse
            if d is None:
                continue  # sessionId is a path component and argv; pid 0/-1 would hit our own group
        else:
            _SESSION_CACHE[str(p)] = d
        d["harness"] = h
        if not isinstance(d.get("name"), str) or not d["name"]:
            d["name"] = d["sessionId"][:8]
        if not isinstance(d.get("cwd"), str):
            d["cwd"] = ""
        for k in ("updatedAt", "startedAt"):
            if not isinstance(d.get(k), (int, float)) or isinstance(d.get(k), bool):
                d[k] = 0
        if not pid_alive(d["pid"], d.get("procStart") if isinstance(d.get("procStart"), str) else None):
            _SESSION_CACHE.pop(str(p), None)
            continue  # dead session files linger (or the pid now belongs to something else)
        age = now - d["updatedAt"] / 1000
        if age > STALE_S:
            d["state"] = "stale"
        elif d.get("status") == "idle" and d.get("kind", "interactive") == "interactive":
            d["state"] = "waiting"
        else:
            d["state"] = "active"
        if load_agent_state(d["sessionId"]).get("event") == "PermissionRequest":
            d["state"] = "blocked"  # blocked on a permission prompt; wins even over stale-by-age
        out.append(d)
    return out


AGENT_STATE = PENGU / "agent-state"  # <sessionId>.json = {"event": <last hook event>, "ts": <epoch>}


def load_agent_state(sid: str) -> dict:
    """Latest Claude lifecycle hook event recorded for a session (written by pengupool.context)."""
    return _json(AGENT_STATE / f"{sid}.json") or {}


_CHAIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# `steps.sh set --name X …` / `steps.sh use X` / bare `steps.sh set …` (chain "default") in a Bash tool call
# A real steps.sh mutation echoes its chain as the tool result: `[name] step ● → next ○`. That echo is
# the only trustworthy attribution signal — command text can contain the words inside heredocs or
# strings, and the tracker's hook injects the *directory's* chain into prompts (exactly the confusion
# we are trying to avoid), so those are ignored.
_ECHO_RE = re.compile(r"^\[([A-Za-z0-9][A-Za-z0-9._-]{0,63})\] \S.*[●○✓✗](?: ↻\d+\])?$")  # ends in a step glyph


def chain_in_line(line: str) -> str | None:
    """Chain name echoed by a steps.sh call in a Bash tool result on this transcript line, else None."""
    if ("tool_result" not in line and '"toolResult"' not in line) or "] " not in line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    if d.get("type") == "user":  # Claude: tool_result blocks inside a user message
        bodies = [c.get("content") for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_result"]
    elif d.get("type") == "message" and msg.get("role") == "toolResult":  # pi: one message per tool result
        bodies = [msg.get("content")]
    else:
        return None
    found = None
    for body in bodies:
        if isinstance(body, list):
            body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
        if not isinstance(body, str):
            continue
        for ln in body.splitlines():
            m = _ECHO_RE.match(ln.strip())
            if m and m.group(1) != "workflow-tracker":
                found = m.group(1)
    return found


_STATUS_DIR_CACHE: dict[str, Path] = {}


def status_dir(cwd: str) -> Path:
    """The `.step-status` dir the tracker writes for `cwd`. A linked git worktree (whose `.git` is a
    FILE `gitdir: <root>/.git/worktrees/<name>`) shares the MAIN repo root's `.step-status`, so a
    session in a worktree shows the same chain as the repo it belongs to. Parsed from the `.git`
    file directly — no `git` subprocess (this runs per node per poll). Memoized per cwd."""
    hit = _STATUS_DIR_CACHE.get(cwd)
    if hit is not None:
        return hit
    root = Path(cwd)
    try:
        git = root / ".git"
        if git.is_file():  # linked worktree: point at the main repo root's .step-status
            for line in git.read_text(errors="replace").splitlines():
                line = line.strip()
                if line.startswith("gitdir:"):
                    gd = Path(line[len("gitdir:"):].strip())
                    if not gd.is_absolute():
                        gd = (root / gd).resolve()
                    s = str(gd)
                    marker = "/.git/worktrees/"
                    if marker in s:
                        root = Path(s.split(marker, 1)[0])  # the path before /.git/worktrees
                    break
    except OSError:
        pass
    result = root / ".step-status"
    _STATUS_DIR_CACHE[cwd] = result
    return result


def read_status(cwd: str, chain: str | None = None) -> str:
    """Render the workflow-tracker chain like steps.sh render (without ↻ loop brackets).
    `chain` selects a specific chain (the one this session uses); None follows the directory's `current`."""
    # ponytail: re-implements render_file's TSV walk to avoid a bash spawn per node per second;
    # loop brackets omitted — shell out to steps.sh if they matter.
    d = status_dir(cwd)
    cur = d / "current"
    try:
        if chain is None:
            chain = cur.read_text(errors="replace").strip() if cur.is_file() and not cur.is_symlink() else "default"
        if not _CHAIN_RE.fullmatch(chain):  # same charset as steps.sh
            chain = "default"
        state = d / f"{chain}.state"
        if d.is_symlink() or state.is_symlink() or not state.is_file():
            return ""
        with state.open(errors="replace") as fh:
            text = fh.read(65536)  # cap: a hostile multi-GB file must not stall the poll
    except OSError:
        return ""
    segs = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[1]:
            continue
        st, name, detail = (parts + [""])[:3]
        detail = re.sub(r"[\x00-\x1f\x7f]", "", detail)[:80]
        segs.append((f"{name}|{detail}" if detail else name) + " " + SYM.get(st, "○"))
    return f"[{chain}] " + " → ".join(segs) if segs else ""


def message_edges(name: str, status_line: str) -> list[Edge]:
    """`ask|⇢ X: text ●` → (name, X, text); `⇠ X: text` → (X, name, text)."""
    edges = []
    for seg in status_line.split(" → "):
        if "|" not in seg:
            continue
        detail = seg.split("|", 1)[1].rsplit(" ", 1)[0]
        if detail[:1] not in "⇢⇠":
            continue
        who, _, text = detail[1:].strip().partition(":")
        who, text = who.strip(), text.strip()
        if not who:
            continue
        edges.append((name, who, text) if detail[0] == "⇢" else (who, name, text))
    return edges


def team_edges(sessions: list[dict]) -> list[Edge]:
    by_id = {s["sessionId"]: s["name"] for s in sessions}
    by_cwd: dict[str, list[str]] = {}
    for s in sessions:
        by_cwd.setdefault(s["cwd"], []).append(s["name"])
    edges = []
    for cfg in (CLAUDE / "teams").glob("*/config.json"):
        t = _json(cfg)
        if not isinstance(t, dict) or not isinstance(t.get("leadSessionId"), str):
            continue
        lead = by_id.get(t["leadSessionId"])
        if not lead:
            continue
        for m in t.get("members") or []:
            if not isinstance(m, dict) or m.get("agentType") == "team-lead":
                continue
            target = by_id.get(m.get("sessionId", "")) or m.get("name")
            if target in by_id.values() or target in {n for ns in by_cwd.values() for n in ns}:
                edges.append((lead, target, ""))
    return edges


def load_registry() -> dict[str, str]:
    """sessionId -> tmux pane, written by session_start.sh (last write wins)."""
    # ponytail: append-only file re-read every tick; prune dead ids if it ever grows past a few MB.
    reg: dict[str, str] = {}
    p = PENGU / "registry.jsonl"
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                d = json.loads(line)
                if isinstance(d, dict) and isinstance(d.get("sessionId"), str) and isinstance(d.get("tmuxPane"), str) and d["tmuxPane"]:
                    reg[d["sessionId"]] = d["tmuxPane"]
            except ValueError:
                pass
    return reg


def build_trees(sessions: list[dict], edges: list[Edge], registry: dict[str, str] | None = None
                ) -> tuple[list[Node], list[Edge]]:
    """Return (roots, cross_edges). Edge labels from later edges overwrite earlier ones."""
    registry = registry or {}
    nodes: dict[str, Node] = {}
    for s in sessions:
        name = s["name"]
        if name in nodes:  # same name twice (often the same session id resumed twice): show every process
            # ponytail: edges still reference the bare name, so they attach to the first one.
            name = f"{name}~{s['pid']}"
        nodes[name] = Node(s["sessionId"], name, s["cwd"], int(s.get("pid", 0)), s["state"],
                           s.get("status_line", ""), tmux_pane=registry.get(s["sessionId"], ""),
                           started=float(s.get("startedAt", 0) or 0) / 1000, ctx_pct=s.get("ctx_pct"),
                           harness=harness.of(s), summary=s.get("summary", ""))
    out: dict[tuple[str, str], str] = {}
    for src, dst, label in edges:
        if src in nodes and dst in nodes and src != dst:
            out[(src, dst)] = label or out.get((src, dst), "")
    # parent = source of the FIRST edge into a node, in priority order (manual > learned > messages > …)
    parent: dict[str, str] = {}
    for src, dst in out:
        parent.setdefault(dst, src)
    for name in list(nodes):  # break cycles: drop the parent link that closes the loop
        seen: list[str] = []
        cur = name
        while cur in parent and cur not in seen:
            seen.append(cur)
            cur = parent[cur]
        if cur in seen:
            del parent[cur]
    for dst, src in parent.items():
        child = nodes[dst]
        child.label = out[(src, dst)]
        nodes[src].children.append(child)
    # one contiguous section per harness (Claude first), so the list, map and extension group them
    roots = sorted((n for name, n in nodes.items() if name not in parent),
                   key=lambda n: (harness.HARNESSES.index(n.harness), n.name))
    cross: list[Edge] = [(s, d, l) for (s, d), l in out.items() if parent.get(d) != s]
    return roots, cross


def snapshot(light: bool = False) -> tuple[list[Node], list[Edge], list["Msg"]]:
    """The tree comes ONLY from manual groups (the `g` action) — it never re-parents itself from
    message traffic, so the hierarchy is stable until the user regroups. Messages still feed the log.
    `light` (used by the per-prompt hook when the TUI is not running): no transcript tailing, no writes."""
    from . import profiles  # profiles builds on this module
    sessions = load_sessions()
    msgs = [] if light else TRANSCRIPTS.scan(sessions)
    groups = load_groups()
    per_cwd: dict[str, int] = {}
    for s in sessions:
        per_cwd[s["cwd"]] = per_cwd.get(s["cwd"], 0) + 1
    for s in sessions:
        # the tracker keys state by directory; attribute chains to the session that switched to them so a
        # parent and a child in the same repo do not show each other's progress
        chain = TRANSCRIPTS.chains.get(s["sessionId"])
        if per_cwd[s["cwd"]] == 1:
            s["status_line"] = read_status(s["cwd"])            # sole session: follow current (no stale pin)
        elif chain:
            s["status_line"] = read_status(s["cwd"], chain)     # shared cwd: this session's own chain
        else:
            s["status_line"] = ""
        s["ctx_pct"] = load_context_pct(s["sessionId"])
        s["summary"] = profiles.load(s["sessionId"]).get("summary", "")
    roots, cross = build_trees(sessions, apply_groups(sessions, team_edges(sessions), groups), load_registry())
    return roots, cross, msgs


# ---- cross-session messages from transcripts -------------------------------------------------

@dataclass
class Msg:
    ts: str
    src: str
    dst: str
    label: str
    incoming: bool = False  # parsed from the receiver's transcript (envelope), not the sender's tool call


def slug(cwd: str) -> str:
    return re.sub(r"[/.]", "-", cwd)


def transcript(sid: str, cwd: str, h: str = "cc") -> Path | None:
    return CLAUDE / "projects" / slug(cwd) / f"{sid}.jsonl" if h == "cc" else harness.pi_transcript(cwd, sid)


def resumable_transcript(sid: str, cwd: str, h: str = "cc") -> bool:
    """A harness cannot resume a new session until it has written a transcript for it."""
    if not _SID.fullmatch(sid):  # the sid becomes a path component and argv
        return False
    p = transcript(sid, cwd, h)
    try:
        return bool(p) and p.stat().st_size > 0
    except OSError:
        return False


def harness_of_past(sid: str, cwd: str) -> str:
    """A past session's transcript lives under exactly one harness, so its sid tells which to resume."""
    return "pi" if resumable_transcript(sid, cwd, "pi") else "cc"


def past_sessions(cwd: str, limit: int = 20) -> list[tuple[str, str, str]]:
    """(session_id, title, harness) for a cwd across harnesses, newest first. Title = the session's
    own name (Claude /rename, pi /alias or --name) or its first user line.
    Pure disk read (no TUI), so the CLI (`pengupool ctl past`) and the extension can reuse it."""
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    files = [(f, "cc") for f in (CLAUDE / "projects" / slug(cwd)).glob("*.jsonl")] + \
            [(f, "pi") for f in harness.pi_dir(cwd).glob("*_*.jsonl")]
    files = sorted(files, key=lambda fh: mtime(fh[0]), reverse=True)[:limit]
    out = []
    for f, h in files:
        sid = f.stem if h == "cc" else f.stem.split("_", 1)[1]
        title, first = "", ""
        try:
            with f.open() as fh:
                for i, line in enumerate(fh):
                    if i > 4000:
                        break
                    if '"custom-title"' in line or '"session_info"' in line:
                        d = json.loads(line)
                        t = d.get("customTitle", d.get("name")) if isinstance(d, dict) else None
                        if isinstance(t, str):
                            title = t
                    elif not first and ('"type":"user"' in line or '"role":"user"' in line):
                        d = json.loads(line)
                        m = d.get("message", {}) if isinstance(d, dict) else {}
                        c = _content_text(m.get("content", "")) if isinstance(m, dict) else ""
                        if c and not c.startswith("<"):
                            first = (c.strip().splitlines() or [""])[0][:60]
        except Exception:  # a hostile/malformed transcript must not crash a caller
            pass
        out.append((sid, title or first or sid[:8], h))
    return out


def _content_text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def parse_transcript_line(line: str, me: str) -> list[Msg]:
    """SendMessage tool_use → (me → to, summary); `<cross-session-message from=X>` → (X → me)."""
    out: list[Msg] = []
    if "SendMessage" not in line and "<cross-session-message" not in line and '"intercom_' not in line:
        return out
    try:
        d = json.loads(line)
    except ValueError:
        return out
    if not isinstance(d, dict):
        return out
    ts = str(d.get("timestamp", ""))
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    content = msg.get("content")
    if d.get("type") == "assistant" and isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "SendMessage":
                inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                to = str(inp.get("to") or inp.get("recipient") or "")
                to = re.sub(r"\s*\[.*\]$", "", to).strip()  # "name [ref]" → name
                # the summary IS the message title Claude Code shows; fall back to the full body text
                label = str(inp.get("summary") or "") or str(inp.get("message") or inp.get("content") or "")
                if to:
                    out.append(Msg(ts, me, to, label[:MSG_LABEL_MAX]))
    elif d.get("type") == "custom" and d.get("customType") in ("intercom_sent", "intercom_received"):
        # pi-intercom records each delivered message in the sender's and receiver's session file
        data = d.get("data") if isinstance(d.get("data"), dict) else {}
        body = data.get("message") if isinstance(data.get("message"), dict) else {}
        label = str(body.get("text") or "")
        if d["customType"] == "intercom_sent" and data.get("to"):
            out.append(Msg(ts, me, str(data["to"]).strip(), label[:MSG_LABEL_MAX]))
        elif d["customType"] == "intercom_received" and data.get("from"):
            out.append(Msg(ts, str(data["from"]).strip(), me, label[:MSG_LABEL_MAX], incoming=True))
    elif d.get("type") == "user":
        text = _content_text(content)
        for m in re.finditer(r'<cross-session-message([^>]*)>(.*?)(?=<cross-session-message|$)', text, re.S):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
            src = attrs.get("from-name") or attrs.get("from", "")  # from-name is the session name; from may be a socket
            if src:
                body = re.sub(r"<[^>]*>", " ", m.group(2))   # drop the closing </cross-session-message> / envelope tags
                out.append(Msg(ts, src.strip(), me, body.strip()[:MSG_LABEL_MAX], incoming=True))
    return out


class Transcripts:
    """Incrementally tail each live session's transcript for cross-session messages."""

    def __init__(self, tail_bytes: int = 1_048_576, keep: int = 300):  # long sessions have huge tool outputs
        self.offsets: dict[Path, int] = {}
        self.msgs: list[Msg] = []
        self.tail_bytes, self.keep = tail_bytes, keep
        self._sock_seen: dict[Path, float] = {}
        self._by_sock: dict[str, str] = {}
        self.chains: dict[str, str] = {}  # sessionId -> tracker chain it last switched to

    def socket_names(self) -> dict[str, str]:
        """'uds:<socket>' -> session name, from every session file (dead ones keep their name)."""
        for p in (CLAUDE / "sessions").glob("*.json"):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if self._sock_seen.get(p) == mtime:
                continue
            self._sock_seen[p] = mtime
            d = _json(p)
            if isinstance(d, dict) and d.get("messagingSocketPath") and d.get("name"):
                self._by_sock[f"uds:{d['messagingSocketPath']}"] = str(d["name"])
        return self._by_sock

    def path(self, s: dict) -> Path | None:
        if harness.of(s) == "pi" and isinstance(s.get("sessionFile"), str):
            return Path(s["sessionFile"])
        return transcript(s["sessionId"], s["cwd"], harness.of(s))

    def scan(self, sessions: list[dict]) -> list[Msg]:
        # incoming envelopes name the sender by socket: `from="uds:/tmp/cc-socks/<pid>.sock"`
        by_sock = self.socket_names()  # includes sessions that have since exited
        live = {s["name"] for s in sessions}
        seen = {(m.ts, m.src, m.dst, m.label) for m in self.msgs}
        for s in sessions:
            p = self.path(s)
            if p is None:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            start = self.offsets.get(p)
            if start is None:  # first sight: only the tail, and skip the partial first line
                start = max(0, size - self.tail_bytes)
            if size < start:  # truncated/rewritten
                start = 0
            if size == start:
                continue
            try:
                with p.open("rb") as fh:
                    fh.seek(start)
                    chunk = fh.read(size - start)
            except OSError:
                continue
            if start and p not in self.offsets:
                chunk = chunk.split(b"\n", 1)[-1]
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                continue  # line still being written
            self.offsets[p] = size - (len(chunk) - last_nl - 1)
            for line in chunk[:last_nl].decode("utf-8", "replace").splitlines():
                ch = chain_in_line(line)
                if ch:
                    self.chains[s["sessionId"]] = ch
                for m in parse_transcript_line(line, s["name"]):
                    m.src = by_sock.get(m.src, m.src)
                    if m.incoming and m.src in live:
                        continue  # the sender's own transcript already records this send, with its summary
                    key = (m.ts, m.src, m.dst, m.label)
                    if key not in seen:  # transcripts can repeat an assistant line
                        seen.add(key)
                        self.msgs.append(m)
        self.msgs.sort(key=lambda m: m.ts)
        self.msgs = self.msgs[-self.keep:]
        return self.msgs


TRANSCRIPTS = Transcripts()


def message_edges_from_msgs(msgs: list[Msg], live: set[str]) -> list[Edge]:
    """One edge per (src, dst) pair between live sessions; label = latest message in either direction.
    Incoming records are only used when the sender's own transcript is not being read (not live)."""
    latest: dict[tuple[str, str], str] = {}
    order: list[tuple[str, str]] = []
    for m in msgs:
        if m.src not in live or m.dst not in live:
            continue
        key = (m.src, m.dst)
        if key not in latest:
            order.append(key)
        latest[key] = m.label
    return [(s, d, latest[(s, d)]) for s, d in order]


# ---- manual grouping ------------------------------------------------------------------------

GROUPS = PENGU / "groups.json"


def load_groups() -> dict[str, str]:
    """child sessionId -> parent sessionId ('' pins the child at top level)."""
    try:
        g = json.loads(GROUPS.read_text())
        return {k: v for k, v in g.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(g, dict) else {}
    except (OSError, ValueError):
        return {}


def save_groups(groups: dict[str, str]) -> None:
    write_json(GROUPS, groups)


def apply_groups(sessions: list[dict], edges: list[Edge], groups: dict[str, str]) -> list[Edge]:
    """Manual parents win: drop every derived edge into a grouped child, then prepend the manual edge."""
    by_id = {s["sessionId"]: s["name"] for s in sessions}
    h_of = {s["sessionId"]: harness.of(s) for s in sessions}
    # a parent of another harness is ignored (the child stays top level): harnesses never share a tree
    pinned = {by_id[c]: (by_id.get(p, "") if h_of.get(p) == h_of[c] else "") for c, p in groups.items() if c in by_id}
    kept = [e for e in edges if e[1] not in pinned]
    manual = [(p, c, "") for c, p in pinned.items() if p]
    h_by_name = {s["name"]: harness.of(s) for s in sessions}
    return [e for e in manual + kept if h_by_name.get(e[0]) == h_by_name.get(e[1])]


def group_error(child: str, parent: str, sessions: list[dict], groups: dict[str, str] | None = None) -> str:
    """Why `child` may not be placed under `parent` ('' = allowed; parent '' means top level)."""
    by_id = {s["sessionId"]: s for s in sessions}
    if child not in by_id:
        return f"no live session {child}"
    if not parent:
        return ""
    if parent not in by_id:
        return f"no live session {parent}"
    if child == parent:
        return "a session cannot be its own parent"
    cur, seen = parent, set()
    while cur and cur not in seen:  # would the new edge close a loop through the saved parents?
        if cur == child:
            return "that would make a session its own ancestor"
        seen.add(cur)
        cur = (groups or {}).get(cur, "")
    if child in by_id and parent in by_id and harness.of(by_id[child]) != harness.of(by_id[parent]):
        return (f"cannot group a {harness.LABEL[harness.of(by_id[child])]} session under a "
                f"{harness.LABEL[harness.of(by_id[parent])]} session: harnesses never share a tree")
    return ""
