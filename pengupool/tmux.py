"""Thin tmux wrapper. Every function shells out; failures return False/''."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

from . import harness, model

SESSION = "pengupool"  # tmux session that holds every Claude window
SESS = harness.SOCK["cc"]  # Claude's sessions server (-L): shared by every VS Code/Cursor window,
#                            so a session started in one is attachable in another (no restart).
#                            pi sessions live on their own server, harness.SOCK["pi"]: every helper below
#                            takes `h` ("cc" | "pi") and talks only to that harness's server.
# Legacy: the launcher used to pass the user's ambient socket; sessions now always live on `-L SESS`
# so both front-ends agree. Kept as no-op env reads for backwards compat with any old launcher.
USER_SOCK = os.environ.get("PENGUPOOL_USER_SOCK", "")
USER_SESSION = os.environ.get("PENGUPOOL_USER_SESSION", "")
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "login", ""}
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
    if _ok("has-session", "-t", "=" + SESSION, h=h):
        return True
    return _ok("new-session", "-d", "-s", SESSION, h=h) and _ok("set-option", "-t", SESSION, "status", "off", h=h)


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
            f"\\; set-option -t {shlex.quote(view)} mouse on")


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


_copy_ready: set[str] = set()  # servers whose copy binds are installed


# The copy hint: the status line (off in every view) is drawn at the top with only a right-aligned
# badge, and shown for two seconds after a copy. Global on PenguPool's private servers.
HINT = {"status-position": "top", "status-style": "default", "status-left": "", "window-status-format": "",
        "window-status-current-format": "", "status-right": "#[fg=black,bg=yellow,bold] Text copied "}


def enable_mouse_copy(h: str = "cc") -> None:
    """Drag-select in the work pane auto-copies to the macOS clipboard, mouse staying on for
    scroll/click. tmux enters copy-mode on a left-drag; on release we pipe the selection to pbcopy
    and flash the copy hint in the top-right corner of that view (set-clipboard also lets
    OSC52-capable terminals copy). Global binds, so set once per process."""
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
    for table in ("copy-mode", "copy-mode-vi"):
        _ok("bind-key", "-T", table, "MouseDragEnd1Pane",
            "send-keys", "-X", "copy-pipe-and-cancel", "pbcopy", "\\;",
            "set-option", "status", "on", "\\;",
            "run-shell", "-b", hide_status, h=h)



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
    except OSError:
        return True
    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
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
