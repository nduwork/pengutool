"""`pengupool ctl <verb>` — one-shot backend ops for the VS Code extension (and the pi extension).

The extension renders; the backend owns the tmux sessions servers (`-L pengupool` for Claude Code,
`-L pengupool-pi` for pi) and the `~/.pengupool` state, so the same session is usable from the TUI and
the extension (no restart to switch — tmux lets a live session be re-viewed). Commands that produce
output print it on stdout; errors print to stderr with a non-zero exit.
Pass `--json` before the verb for launch metadata (command, pane, actual cwd, harness).

Verbs:
    new <dir> <name> [view] [cc|pi] [worktree|folder]
                                  start a Claude Code (default) or pi session (git worktree when
                                  possible); print the shell command a terminal runs to VIEW it
    resume <dir> <name> <sid>     resume a PAST (dead) session of either harness; print its view command
    adopt <sid>                   take over a LIVE session not on the server: stop it, then resume
    attach <sid> <name> [view]   print the view command for an already-running session (no restart)
    select <sid> <view>          switch one existing extension terminal to a live session
    close <sid>                   kill a running session's tmux window
    group <childSid> <parentSid|"">   move a session under a parent of the same harness, or "" for top level
    worktree-add <dir> <name>     git worktree for a session; print the path (or <dir>)
    past <dir>                    JSON [[sessionId, title, harness], …] of resumable past sessions
    context <sid>                 print the session-tree context block for a session (used by the pi extension)
    register <sid> <cwd>          create/refresh a session's profile (workspace scan; used by the pi extension)
    describe <sid> [--summary S] [--responsibility R]
                                  set what a session owns; allowed from the session itself, its parent, or the user
    profile <sid>                 JSON profile of a session (summary, responsibility, workspace, who edited it)
    tree <sid>                    the full session tree around a session, with summaries
    route <sid> <target>          the next adjacent hop from a session toward a target (name or id)
    authorize <sid> <recipient>   exit 0 when the session may message the recipient; else the reason, exit 3
"""
from __future__ import annotations

import json
import sys

from . import harness, model, profiles, routing


def _index() -> tuple[dict[str, str], dict[str, dict]]:
    return model.load_registry(), {s["sessionId"]: s for s in model.load_sessions()}


def _session_pane(sid: str, reg: dict[str, str], sessions: dict[str, dict]) -> str:
    """Pane ids are socket-local and recyclable; resolve using the live process owner."""
    from . import tmux
    s = sessions.get(sid, {})
    pid, h = int(s.get("pid", 0) or 0), harness.of(s)
    if pid <= 1:
        return ""
    pane = reg.get(sid, "")
    if pane and tmux.pane_owns(pane, pid, h):
        return pane
    return tmux.pane_for_pid(pid, h)


def _emit_view(pane: str, name: str, cwd: str = "", json_output: bool = False,
               view_id: str = "", h: str = "cc") -> int:
    from . import tmux
    cmd = tmux.view_command(pane, name, view_id, h)
    if not cmd:
        print("session window is gone", file=sys.stderr)
        return 1
    print(json.dumps({"command": cmd, "pane": pane, "cwd": cwd, "harness": h}) if json_output else cmd)
    return 0


def _new(directory: str, name: str, json_output: bool = False, view_id: str = "",
         h: str = "cc", location: str = "worktree") -> int:
    from . import tmux
    h = harness.check(h)
    if location not in ("worktree", "folder"):
        print(f"invalid session location: {location}", file=sys.stderr)
        return 2
    if location == "worktree":
        directory = tmux.worktree_add(directory, name) or directory
    pane = tmux.new_window(directory, name, h)
    if not pane:
        print("failed to start session", file=sys.stderr)
        return 1
    return _emit_view(pane, name, directory, json_output, view_id, h)


def _resume(directory: str, name: str, sid: str, json_output: bool = False,
            view_id: str = "") -> int:
    from . import tmux
    reg, sessions = _index()
    if sid in sessions:
        h = harness.of(sessions[sid])
        pane = _session_pane(sid, reg, sessions)
        if pane:
            return _emit_view(pane, name, sessions[sid].get("cwd", directory), json_output, view_id, h)
        print(f"session {sid} is already running outside PenguPool; use adopt to stop and re-host it",
              file=sys.stderr)
        return 2
    h = model.harness_of_past(sid, directory)
    # A prior resume may still be starting up, before its lifecycle hook is visible.
    pane = tmux.pane_resuming(sid, h)
    if pane:
        return _emit_view(pane, name, directory, json_output, view_id, h)
    pane = tmux.resume_window(directory, name, sid, h)
    if not pane:
        print("failed to resume session", file=sys.stderr)
        return 1
    return _emit_view(pane, name, directory, json_output, view_id, h)


def _adopt(sid: str, json_output: bool = False, view_id: str = "") -> int:
    """Take over a session that's alive but not on the shared server: STOP the running process first
    (resuming a still-live session would fork a duplicate — the `name~pid` you saw), then resume it
    in a tmux window of its own harness. Mirrors the TUI's Enter-to-adopt."""
    from . import tmux
    reg, sessions = _index()
    s = sessions.get(sid)
    if not s:
        print(f"session {sid} is not alive", file=sys.stderr)
        return 1
    name, cwd, pid, h = s.get("name") or sid[:8], s.get("cwd", ""), int(s.get("pid", 0) or 0), harness.of(s)
    pane = _session_pane(sid, reg, sessions) or tmux.pane_resuming(sid, h)
    if pane:
        return _emit_view(pane, name, cwd, json_output, view_id, h)
    if not model.resumable_transcript(sid, cwd, h):
        print(f"{harness.LABEL[h]} has not written this session's transcript yet. Send a prompt in the outside "
              "session, wait for it to finish, then adopt it; the session is still running.", file=sys.stderr)
        return 1
    if pid <= 1:
        print("invalid live session process", file=sys.stderr)
        return 1
    pane = tmux.reserve_window(cwd, name, h)
    if not pane:
        print("could not prepare a tmux window; the outside session is still running", file=sys.stderr)
        return 1
    if not tmux.stop(pid):
        tmux.kill_reserved(pane, h)
        print("failed to stop the live session; refusing to resume a duplicate", file=sys.stderr)
        return 1
    if not tmux.start_reserved(pane, cwd, name, sid, h):
        print(f"could not start {harness.LABEL[h]} in prepared pane {pane}; resume session {sid} manually",
              file=sys.stderr)
        return 1
    return _emit_view(pane, name, cwd, json_output, view_id, h)


def _attach(sid: str, name: str, json_output: bool = False, view_id: str = "") -> int:
    reg, sessions = _index()
    pane = _session_pane(sid, reg, sessions)
    if not pane:
        print(f"session {sid} is not on a PenguPool tmux server", file=sys.stderr)
        return 2  # 2 => caller may offer to adopt/resume instead
    return _emit_view(pane, name, sessions[sid].get("cwd", ""), json_output, view_id, harness.of(sessions[sid]))


def _select(sid: str, view_id: str) -> int:
    from . import tmux
    reg, sessions = _index()
    pane = _session_pane(sid, reg, sessions)
    if not pane:
        return 2
    return 0 if tmux.select_view(pane, view_id, harness.of(sessions[sid])) else 3


def _close(sid: str) -> int:
    from . import tmux
    reg, sess = _index()
    s = sess.get(sid, {})
    ok = tmux.kill(_session_pane(sid, reg, sess), int(s.get("pid", 0) or 0), harness.of(s))
    return 0 if ok else 1


def _group(child: str, parent: str) -> int:
    err = model.group_error(child, parent, model.load_sessions(), model.load_groups())
    if err:
        print(err, file=sys.stderr)
        return 2
    groups = model.load_groups()
    if parent:
        groups[child] = parent
    else:
        groups.pop(child, None)          # "" = top level: forget any manual parent
    model.save_groups(groups)
    return 0


def _worktree_add(directory: str, name: str) -> int:
    from . import tmux
    print(tmux.worktree_add(directory, name) or directory)
    return 0


def _past(directory: str) -> int:
    print(json.dumps(model.past_sessions(directory)))
    return 0


def _context(sid: str) -> int:
    from .context import text_for
    s = next((s for s in model.load_sessions() if s["sessionId"] == sid), {})
    print(text_for(sid, h=harness.of(s)))
    return 0


def _describe(sid: str, opts: list[str]) -> int:
    fields = {"--summary": None, "--responsibility": None}
    while opts:
        if len(opts) < 2 or opts[0] not in fields:
            print("usage: pengupool ctl describe <sid> [--summary S] [--responsibility R]", file=sys.stderr)
            return 2
        key, value, *opts = opts
        fields[key] = value
    if fields == {"--summary": None, "--responsibility": None}:
        print("nothing to set: pass --summary and/or --responsibility", file=sys.stderr)
        return 2
    try:
        d = profiles.describe(sid, fields["--summary"], fields["--responsibility"])
    except (ValueError, PermissionError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(d))
    return 0


def _profile(sid: str) -> int:
    print(json.dumps(profiles.load(sid)))
    return 0


def _tree(sid: str) -> int:
    from .context import draw_tree, load_tree
    sess = (load_tree() or {}).get("sessions") or {}
    if sid not in sess:
        print(f"session {sid} is not in the live tree", file=sys.stderr)
        return 1
    print("\n".join(draw_tree(sess, sid, full=True)))
    return 0


def _route(sid: str, target: str) -> int:
    t = routing.live_tree()
    hits = routing.resolve(t, sid, target)
    if len(hits) != 1:
        print(f"{target!r} names {len(hits)} live sessions", file=sys.stderr)
        return 2
    try:
        hop = routing.route(t, sid, next(iter(hits)))
    except LookupError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"{t.name[hop]} {hop}")
    return 0


def _authorize(sid: str, recipient: str) -> int:
    ok, reason = routing.authorize_send(sid, recipient)
    if not ok:
        print(reason, file=sys.stderr)
    return 0 if ok else 3


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[2:] if argv is None else argv)  # drop "pengupool ctl"
    json_output = argv[:1] == ["--json"]
    if json_output:
        argv.pop(0)
    if not argv:
        print("usage: pengupool ctl new|resume|adopt|attach|close|group|worktree-add|past|context|describe|profile|tree|route|authorize …", file=sys.stderr)
        return 2
    verb, a = argv[0], argv[1:]
    if verb == "describe" and a:
        return _describe(a[0], a[1:])
    table = {
        ("new", 2): lambda: _new(a[0], a[1], json_output),
        ("new", 3): lambda: _new(a[0], a[1], json_output, a[2]),
        ("new", 4): lambda: _new(a[0], a[1], json_output, a[2], a[3]),
        ("new", 5): lambda: _new(a[0], a[1], json_output, a[2], a[3], a[4]),
        ("resume", 3): lambda: _resume(a[0], a[1], a[2], json_output),
        ("resume", 4): lambda: _resume(a[0], a[1], a[2], json_output, a[3]),
        ("adopt", 1): lambda: _adopt(a[0], json_output),
        ("adopt", 2): lambda: _adopt(a[0], json_output, a[1]),
        ("attach", 2): lambda: _attach(a[0], a[1], json_output),
        ("attach", 3): lambda: _attach(a[0], a[1], json_output, a[2]),
        ("select", 2): lambda: _select(a[0], a[1]),
        ("close", 1): lambda: _close(a[0]),
        ("group", 2): lambda: _group(a[0], a[1]),
        ("worktree-add", 2): lambda: _worktree_add(a[0], a[1]),
        ("past", 1): lambda: _past(a[0]),
        ("context", 1): lambda: _context(a[0]),
        ("register", 2): lambda: (profiles.register(a[0], a[1]), 0)[1],
        ("profile", 1): lambda: _profile(a[0]),
        ("tree", 1): lambda: _tree(a[0]),
        ("route", 2): lambda: _route(a[0], a[1]),
        ("authorize", 2): lambda: _authorize(a[0], a[1]),
    }
    fn = table.get((verb, len(a)))
    if not fn:
        print(f"pengupool ctl: bad verb/args: {argv}", file=sys.stderr)
        return 2
    try:
        return fn()
    except Exception as e:  # a bad request must not dump a traceback at the extension
        print(f"pengupool ctl {verb}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
