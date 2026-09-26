"""Thin tmux wrapper. Every function shells out; failures return False/''."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time

from . import harness, model

SESSION = "pengupool"  # tmux session that holds every agent window, on each harness's server
SESS = harness.SOCK["cc"]  # Claude's sessions server (-L): shared by every VS Code/Cursor window,
#                            so a session started in one is attachable in another (no restart).
#                            pi sessions live on their own server, harness.SOCK["pi"]: every helper below
#                            takes `h` ("cc" | "pi") and talks only to that harness's server.
OUTER = "pengupool-ui"  # private tmux server (-L) that hosts the terminal UI's own panes
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "login", ""}
# Agent-pane scrollback for the wheel: tmux's default of 2000 lines runs out fast when an agent
# streams, so the sessions server gets a deeper history (tmux applies it to panes created after).
HISTORY_LIMIT = 50000
# OSC 52 `Ms` for every TERM, not just `xterm*`: lets a tmux copy reach the host clipboard in
# terminals tmux does not already cover. Appended once, because appending repeats it each launch.
CLIPBOARD_FEATURE = ",*:clipboard"
CLIPBOARD_MARK = "*:clipboard"  # tmux stores the entry without the leading comma; match that for idempotency
_CACHE: dict[str, tuple[float, str]] = {}


def _cached(key: str, ttl: float, *args: str, h: str = "cc") -> str:
    """Poll-rate tmux queries (list-panes) are shared across a tick instead of spawned per node."""
    now = time.time()
    key = f"{h}:{key}"
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    out = _run(*args, h=h)
    _CACHE[key] = (now, out)
    return out


def _user(*args: str, h: str = "cc") -> list[str]:
    return ["tmux", "-L", harness.SOCK[h], *args]  # the harness's shared PenguPool sessions server


def outer(*args: str) -> str:
    """Run a command on PenguPool's private UI server (the terminal UI's own panes)."""
    try:
        return subprocess.run(["tmux", "-L", OUTER, *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def outer_ok(*args: str) -> bool:
    try:
        return subprocess.run(["tmux", "-L", OUTER, *args], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def safe_arg(s: str) -> str:
    """tmux splits argv on a trailing ';' and treats leading '-' as an option: neutralise both."""
    return re.sub(r"[;\s]+", "-", s).strip("-") or "session"


def _run(*args: str, h: str = "cc") -> str:
    try:
        return subprocess.run(_user(*args, h=h), capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _ok(*args: str, h: str = "cc") -> bool:
    try:
        return subprocess.run(_user(*args, h=h), capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def panes(h: str = "cc") -> dict[str, tuple[int, str]]:
    """pane id -> (pane pid, current command), refreshed at most every 0.4 s."""
    out = {}
    for line in _cached("panes", 0.4, "list-panes", "-a", "-F", "#{pane_id}\t#{pane_pid}\t#{pane_current_command}",
                        h=h).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return out


def pane_exists(pane: str, h: str = "cc") -> bool:
    """Registry panes can be stale (tmux restarted, ids recycled): only act on a pane tmux still lists."""
    return bool(pane) and pane in panes(h)


def pane_owns(pane: str, pid: int, h: str = "cc") -> bool:
    """True when `pid` runs inside `pane` (the pane's shell is an ancestor): guards recycled pane ids."""
    info = panes(h).get(pane)
    if not info:
        return False
    cur = pid
    procs = model.PROCS.refresh()
    for _ in range(12):
        if cur == info[0]:
            return True
        cur = procs.ppid(cur)
        if cur <= 1:
            break
    return False


def pane_for_pid(pid: int, h: str = "cc") -> str:
    """Walk the pid's parent chain (from the shared ps table, no subprocess) to a pane's shell pid."""
    by_pid = {info[0]: pane for pane, info in panes(h).items()}
    procs = model.PROCS.refresh()
    for _ in range(12):
        if pid in by_pid:
            return by_pid[pid]
        if pid <= 1:
            break
        pid = procs.ppid(pid)
    return ""


def switch(pane: str, h: str = "cc") -> bool:
    if not pane_exists(pane, h):
        return False
    return _ok("switch-client", "-t", pane, h=h) or _ok("select-window", "-t", pane, h=h)


def ensure_server(h: str = "cc") -> bool:
    """Make sure the harness's sessions server has a session to put agent windows in."""
    created = False
    if not _ok("has-session", "-t", "=" + SESSION, h=h):
        if not (_ok("new-session", "-d", "-s", SESSION, h=h)
                and _ok("set-option", "-t", SESSION, "status", "off", h=h)):
            return False
        created = True
    tune(h)
    if created:
        # Only on a fresh server: the default comes from config, but a prefix+m toggle at runtime must
        # survive the next CLI process, which would otherwise re-apply the default here.
        _ok("set-option", "-g", "mouse", mouse_option(), h=h)
    mouse_toggle(h)
    return True


def target_session() -> str:
    return SESSION


def _extension_view(pane: str, view_id: str = "", h: str = "cc") -> tuple[str, str]:
    """Return (view session, window id) for an extension client."""
    win = _run("display-message", "-p", "-t", pane, "#{window_id}", h=h).strip()
    if not win:
        return "", ""
    view = "pv-ext-" + (safe_arg(view_id) if view_id else win.lstrip("@"))
    return view, win


def cleanup_extension_views(keep: str = "", h: str = "cc") -> None:
    """Remove detached extension view sessions left by closed or reloaded editor windows."""
    sessions = _run("list-sessions", "-F", "#{session_name}\t#{session_attached}", h=h)
    for line in sessions.splitlines():
        name, sep, attached = line.partition("\t")
        if sep and name.startswith("pv-ext-") and name != keep and attached == "0":
            _ok("kill-session", "-t", "=" + name, h=h)


def view_command(pane: str, name: str, view_id: str = "", h: str = "cc") -> str:
    """Shell command for a terminal (a VS Code integrated terminal) to VIEW `pane`'s window without
    disturbing other clients: attach to a grouped view session (shares windows, own current-window
    and size) on the shared server, then select the target window. Empty string if the pane is gone.
    Attaching never restarts the session — tmux is what lets a live session be re-viewed."""
    view, win = _extension_view(pane, view_id, h)
    if not view:
        return ""
    if view_id:
        cleanup_extension_views(view, h)
    enable_mouse_copy(h)  # the extension's views auto-copy a drag selection
    # `\;` are passed through the shell to tmux as command separators.
    return (f"tmux -L {harness.SOCK[h]} new-session -A -s {shlex.quote(view)} -t {SESSION} "
            f"\\; select-window -t {shlex.quote(view + ':' + win)} "
            f"\\; set-option -w -t {shlex.quote(view + ':' + win)} window-size latest "
            f"\\; set-option -t {shlex.quote(view)} status off "
            f"\\; set-option -u -t {shlex.quote(view)} mouse")  # drop an older build's pin, follow the global toggle


def select_view(pane: str, view_id: str, h: str = "cc") -> bool:
    """Switch one extension-owned grouped view to `pane` without typing into its tmux client."""
    view, win = _extension_view(pane, view_id, h)
    if not view or not _ok("has-session", "-t", "=" + view, h=h):
        return False
    target = f"{view}:{win}"
    # Older clients could leave shared windows in tmux's manual-size mode. Restore automatic
    # sizing and mark the extension's client as latest so its integrated terminal fills the panel.
    if not (_ok("set-option", "-w", "-t", target, "window-size", "latest", h=h) and
            _ok("select-window", "-t", target, h=h)):
        return False
    clients = _run("list-clients", "-F", "#{client_name}\t#{session_name}", h=h)
    client = next((name for line in clients.splitlines()
                   for name, sep, session in [line.partition("\t")]
                   if sep and session == view), "")
    return not client or _ok("switch-client", "-c", client, "-t", "=" + view, h=h)


def show_in_client(tty: str, pane: str, h: str = "cc") -> bool:
    """Point the terminal UI's nested client (`tty`) at `pane` without touching the user's own clients:
    tmux keeps 'current window' per session, so attach the client to a grouped view (shares the
    windows, has its own current window) and select the window there."""
    info = _run("display-message", "-p", "-t", pane, "#{session_group}\t#{session_name}\t#{window_index}",
                h=h).rstrip("\r\n")
    if info.count("\t") != 2:
        return False
    group, sname, widx = info.split("\t")
    session = (group or sname)
    if session.startswith("pv-"):
        session = session[3:]
    view = "pv-tui-" + safe_arg(session)[:40]
    if not _ok("has-session", "-t", "=" + view, h=h):
        if not _ok("new-session", "-d", "-t", session, "-s", view, h=h):
            return False
        _ok("set-option", "-t", view, "status", "off", h=h)
    # Drop any session-level mouse override (older builds pinned `mouse on` here), so the view follows
    # the server's global setting and the prefix+m toggle reaches it.
    _ok("set-option", "-u", "-t", view, "mouse", h=h)
    enable_mouse_copy(h)
    if not (_ok("switch-client", "-c", tty, "-t", view, h=h) and _ok("select-window", "-t", f"{view}:{widx}", h=h)
            and _ok("select-pane", "-t", pane, h=h)):
        return False
    fit_window(tty, f"{view}:{widx}", h)
    return True


def fit_window(tty: str, window: str, h: str = "cc") -> None:
    """Let a shared window follow the latest active client as either front end is resized. An explicit
    ``resize-window`` permanently changes tmux's per-window policy to ``manual``, leaving dotted unused
    space when an editor terminal later grows, so restore automatic sizing. ``tty`` stays in the
    signature because callers identify the client they switched."""
    del tty
    _ok("set-option", "-w", "-t", window, "window-size", "latest", h=h)


def kill_views() -> None:
    """Remove only terminal-UI views on every harness server; editor clients may still be attached."""
    for h in harness.HARNESSES:
        for line in _run("list-sessions", "-F", "#{session_name}", h=h).splitlines():
            if line.startswith("pv-tui-"):
                _ok("kill-session", "-t", "=" + line, h=h)


_copy_ready: set[str] = set()  # servers whose copy binds are installed
_tuned: set[str] = set()  # servers whose clipboard/scrollback defaults are installed
_mouse_ready: set[str] = set()  # servers whose mouse toggle bind is installed


def _has_clipboard_feature(h: str = "cc") -> bool:
    """True when a `*:clipboard` terminal-features entry is already set. tmux drops the leading comma
    we append, so match the stored entry exactly rather than the append form."""
    for line in _run("show-options", "-g", "terminal-features", h=h).splitlines():
        if line.rpartition(" ")[2].strip() == CLIPBOARD_MARK:
            return True
    return False


def tune(h: str = "cc") -> None:
    """Server-wide clipboard and scrollback defaults for a harness's sessions server (idempotent).

    `terminal-features ,*:clipboard` advertises the OSC 52 `Ms` capability to every TERM, so a tmux
    copy reaches the system clipboard beyond `xterm*`; `history-limit` deepens the wheel scrollback."""
    if h in _tuned:
        return
    _tuned.add(h)
    if not _has_clipboard_feature(h):
        _ok("set-option", "-ga", "terminal-features", CLIPBOARD_FEATURE, h=h)
    _ok("set-option", "-g", "history-limit", str(HISTORY_LIMIT), h=h)
    # NB: the `mouse` default is set only when a server is first created (see ensure_server), not here:
    # `tune` runs on every CLI process, and re-applying it would undo a runtime prefix+m toggle.


def _config() -> dict:
    """Read ~/.pengupool/config.json (the TUI's own config); {} when missing or malformed."""
    try:
        data = json.loads((model.PENGU / "config.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def copy_on_drag() -> bool:
    """Whether a work-pane drag selects in tmux and auto-copies on release (the default).

    With `"copy_on_drag": false` in ~/.pengupool/config.json, selection is left to the host terminal:
    hold Option (macOS) or Shift while dragging to copy with the terminal's own clipboard."""
    value = _config().get("copy_on_drag", True)
    return value if isinstance(value, bool) else True


def mouse_option() -> str:
    """The `mouse` value PenguPool's servers default to: `on` lets tmux scroll history and drag-copy;
    `off` (`copy_on_drag: false`) leaves selection and scrolling to the host terminal. Set globally so
    the prefix+m toggle can still flip it — pinning it per session/view would override the global."""
    return "on" if copy_on_drag() else "off"


def _mouse_flip_command(h: str) -> str:
    """Shell that flips the mouse option on the servers this bind does not run on: the other harness's
    sessions server and the TUI's private server. The bound `set-option` already flipped the current one,
    so both work panes and the outer UI stay in step. Absent servers just fail and are ignored."""
    socks = sorted({harness.SOCK[o] for o in harness.HARNESSES if o != h})
    flips = [f"tmux -L {sock} set-option -g mouse 2>/dev/null" for sock in (*socks, OUTER)]
    return " ; ".join(flips) + " ; true"


def mouse_toggle(h: str = "cc") -> None:
    """Bind prefix + m on the sessions server to flip tmux's mouse handling at runtime (idempotent).

    Mouse off leaves selection to the host terminal (hold Option/Shift to copy natively); mouse on
    restores tmux history scroll and drag-copy. The bind flips the other harness's server and the TUI's
    private server too, so every work pane and the outer panes agree."""
    if h in _mouse_ready:
        return
    _mouse_ready.add(h)
    # `set-option -g mouse` with no value toggles the boolean (tmux's own idiom). The escaped `\;`
    # keeps the commands inside the binding instead of running the tail of it now.
    _ok("bind-key", "m", "set-option", "-g", "mouse", r"\;",
        "run-shell", _mouse_flip_command(h), r"\;",
        "display-message", "mouse #{?mouse,on,off} (off = terminal-native selection)", h=h)


# The copy hint: the status line (off in every view) is drawn at the top with only a right-aligned
# badge, and shown for two seconds after a copy. Global on PenguPool's private servers.
HINT = {"status-position": "top", "status-style": "default", "status-left": "", "window-status-format": "",
        "window-status-current-format": "", "status-right": "#[fg=black,bg=yellow,bold] Text copied "}


# True when the copy-mode selection spans at least 2 characters or more than one line. tmux has no abs(),
# so both drag directions are checked.
MIN_SELECTION = ("#{||:#{!=:#{selection_start_y},#{selection_end_y}},"
                 "#{||:#{e|>=|:#{e|-|:#{selection_end_x},#{selection_start_x}},1},"
                 "#{e|>=|:#{e|-|:#{selection_start_x},#{selection_end_x}},1}}}")


def enable_mouse_copy(h: str = "cc") -> None:
    """Install drag-select copy on a harness's server: tmux enters copy-mode on a left-drag and on
    release we pipe the selection to pbcopy and flash the copy hint in the top-right corner
    (set-clipboard also lets OSC52-capable terminals copy). Global binds, set once per process.
    Installed even when the server starts with mouse off (`copy_on_drag: false`), so the prefix+m
    toggle restores drag-copy intact rather than leaving copy-mode without it."""
    if h in _copy_ready:
        return
    _copy_ready.add(h)
    _ok("set-option", "-g", "set-clipboard", "on", h=h)
    for k, v in HINT.items():
        _ok("set-option", "-g", k, v, h=h)
    # tmux does NOT format-expand a `-t` target inside a key binding ("#{…}" is looked up literally →
    # "no such session"), so the bound set-option has no -t: it acts on the pressing client's own view
    # session. run-shell does expand its command, so the delayed hide can name that session.
    hide_status = f"sleep 2; tmux -L {harness.SOCK[h]} set-option -t '#{{session_name}}' status off"
    copy = f'send-keys -X copy-pipe-and-cancel pbcopy ; set-option status on ; run-shell -b "{hide_status}"'
    for table in ("copy-mode", "copy-mode-vi"):
        # A click that wobbles a pixel is a drag too: copying its 1-character "selection" would replace
        # the user's clipboard (they click into the chat, then Cmd+V pastes nothing useful). Copy only
        # a real selection; otherwise just leave copy-mode with the clipboard untouched.
        _ok("bind-key", "-T", table, "MouseDragEnd1Pane",
            "if-shell", "-F", MIN_SELECTION, copy, "send-keys -X cancel", h=h)



def _spawn(cwd: str, name: str, command: str, h: str) -> str:
    if not ensure_server(h):
        return ""
    return _run("new-window", "-d", "-P", "-F", "#{pane_id}", "-t", target_session() + ":", "-c", cwd,
                "-n", safe_arg(name), command, h=h).strip()


def new_window(cwd: str, name: str, h: str = "cc") -> str:
    """Start a new agent session of harness `h` in a new window on that harness's server."""
    return _spawn(cwd, name, shlex.join(harness.argv_new(h, safe_arg(name))), h)


def resume_window(cwd: str, name: str, session_id: str, h: str = "cc") -> str:
    """Resume a past (dead) session of harness `h` in a new window."""
    return _spawn(cwd, name, shlex.join(harness.argv_resume(h, safe_arg(name), session_id)), h)


def reserve_window(cwd: str, name: str, h: str = "cc") -> str:
    """Create the destination pane before stopping an outside session."""
    pane = _spawn(cwd, name, "sleep 3600", h)
    # tmux can report a pane ID even when its command exits immediately. Do not stop the session
    # unless the reserved pane is still there and can receive the resume command.
    if not pane or _run("display-message", "-p", "-t", pane, "#{pane_id}", h=h).strip() != pane:
        return ""
    if not hold(pane, h):
        kill_reserved(pane, h)
        return ""
    return pane


def hold(pane: str, h: str = "cc") -> bool:
    """Keep the pane after its agent exits (tmux would close the window), so it can be respawned and a
    failed resume's output stays visible."""
    return _ok("set-option", "-w", "-t", pane, "remain-on-exit", "on", h=h)


def start_reserved(pane: str, cwd: str, name: str, session_id: str, h: str = "cc") -> bool:
    """Replace the reservation with the agent after its previous process has exited."""
    command = shlex.join(harness.argv_resume(h, safe_arg(name), session_id))
    return _ok("respawn-pane", "-k", "-t", pane, "-c", cwd, command, h=h)


def kill_reserved(pane: str, h: str = "cc") -> None:
    if pane:
        _ok("kill-pane", "-t", pane, h=h)


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=15)


def worktree_add(base_cwd: str, name: str) -> str:
    """Create a git worktree on a new branch off base_cwd's repo; return its path. Return '' when
    base_cwd is not a git repo or git fails, so the caller falls back to a plain session in base_cwd."""
    try:
        top = _git(base_cwd, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return ""
        top = top.stdout.strip()
        slug = safe_arg(name)[:40]
        repo, parent = os.path.basename(top), os.path.dirname(top)
        for k in range(20):  # bump a suffix on both the path and the branch until one is free
            suf = "" if k == 0 else f"-{k}"
            path = os.path.join(parent, f"{repo}-wt-{slug}{suf}")
            if os.path.exists(path):
                continue
            if _git(top, "worktree", "add", "-b", f"pengupool/{slug}{suf}", path).returncode == 0:
                return path
        return ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def stop(pid: int, grace: float = 8.0) -> bool:
    """SIGTERM a session and wait for it to exit (SIGKILL after `grace`)."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return True
    except OSError:
        return False  # EPERM: the pid now belongs to someone else's process; it is not our session
    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
        time.sleep(0.2)
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    return True


def send(pane: str, text: str, h: str = "cc") -> bool:
    if not pane_exists(pane, h):
        return False
    return _ok("send-keys", "-t", pane, "-l", text, h=h) and _ok("send-keys", "-t", pane, "Enter", h=h)


def slash(pane: str, text: str, h: str = "cc") -> bool:
    """Type a slash command into the agent's own pane: clear the input line first (C-u) so a half-typed
    prompt is not submitted with it. ponytail: C-u clears one line; a multi-line draft keeps its
    earlier lines. Upgrade path: the harness's own "clear input" key if it gains one."""
    return pane_exists(pane, h) and _ok("send-keys", "-t", pane, "C-u", h=h) and send(pane, text, h)


def kill(pane: str, pid: int, h: str = "cc") -> bool:
    if pane_exists(pane, h) and _ok("kill-pane", "-t", pane, h=h):
        return True
    if pid <= 1:
        return False
    try:
        os.kill(pid, 15)
        return True
    except OSError:
        return False


def inside_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def pane_resuming(session_id: str, h: str = "cc") -> str:
    """Pane whose start command resumes `session_id` AND still runs the agent ('' if none). A pane whose
    agent exited back to a shell keeps its start command forever, so the current command is checked too."""
    if not session_id:
        return ""
    for line in _run("list-panes", "-a", "-F", "#{pane_id}\t#{pane_dead}\t#{pane_current_command}\t#{pane_start_command}",
                     h=h).splitlines():
        parts = line.split("\t", 3)
        # tmux reports Claude's current command as its version string (e.g. "2.1.276") and pi's as node,
        # so we only reject panes that have fallen back to a shell. Each server hosts one harness.
        if len(parts) == 4 and parts[1] == "0" and session_id in parts[3] and harness.CLI[h] in parts[3] \
                and parts[2] not in SHELLS:
            return parts[0]
    return ""
