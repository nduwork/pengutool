"""PenguPool.

`pengupool` launches a private tmux server with three panes and attaches to it:

    ┌ list (Textual) ┬ map + log (Textual) ┐
    │                ├─────────────────────┤
    │                │ nested tmux client  │   ← the selected session, rendered by tmux itself
    │                ├─────────────────────┤
    │                │ nested pi client    │   ← only when pi is installed: pi sessions live on
    └────────────────┴─────────────────────┘     their own tmux server, so they get their own pane

Nothing emulates a terminal here: each work pane is a real `tmux attach` to a grouped view of
the selected session on its harness's server, switched with `switch-client`. Panes are resized
by dragging borders, focus follows the mouse or Ctrl+T, and the wheel scrolls tmux history.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, OptionList, Static, Tree
from textual_fspicker import SelectDirectory

from . import harness, model, profiles, tmux
from .context import TREE, tree_dump
from .graph import CROSS_LABEL_W, GLYPH, render_tree, wrap_label

# Mouse is ON: click selects panes, the wheel scrolls, and borders drag to resize — the terminal-native
# feel the user asked for. In the work pane a left-drag selects text and auto-copies on release (pbcopy).
_NoMouseDriver = None  # ponytail: mouse on = Textual default; get_driver_class() falls back to super()

COLOR = {"active": "green", "waiting": "yellow", "stale": "grey50", "blocked": "red"}
ARROW_DOWN = "green"     # parent -> child message
ARROW_UP = "orange1"     # child -> parent (reply) message ("orange" isn't a valid Textual/Rich color and crashes the log render)
# Pane chrome — one source of truth for every pane's border, Textual and tmux alike (tmux gets
# truecolor via `terminal-overrides ,*:RGB`, so the same hex drives both). idle = xterm colour240,
# active/focused = xterm colour45.
BORDER_IDLE = "#585858"
BORDER_ACTIVE = "#00d7ff"


class _Chrome:
    """Mixin: expose BORDER_IDLE/ACTIVE to CSS as $border-idle / $border-active."""
    def get_css_variables(self) -> dict:
        v = super().get_css_variables()
        v.update({"border-idle": BORDER_IDLE, "border-active": BORDER_ACTIVE})
        return v
DEFAULT_LIST_W = 34  # matches the config default; `=` optimize resets the list pane to this width
CONFIG = model.PENGU / "config.json"
UI = model.PENGU / "ui.json"      # launcher: pane ids + nested client tty; list: selection
VIEW = model.PENGU / "view.json"  # list → map: serialized trees, cross edges, messages


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["map"] = bool(cfg.get("map", True))
    cfg["log"] = bool(cfg.get("log", True))
    for key, default, lo, hi in (("poll", 1.0, 0.2, 60.0), ("stale_s", 600, 10, 86400),
                                 ("list_w", 34, 20, 120), ("top_h", 16, 5, 200)):
        v = cfg.get(key, default)
        cfg[key] = min(hi, max(lo, v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    return cfg


def save_config(cfg: dict) -> None:
    model.write_json(CONFIG, cfg)


def load_ui() -> dict:
    d = model._json(UI)
    return d if isinstance(d, dict) else {}


past_sessions = model.past_sessions  # moved to model.py (pure); kept here for existing callers


# ---------------------------------------------------------------- dialogs ----

class Ask(ModalScreen[dict | None]):
    """Generic small form: list of (key, label, default) inputs, optional OptionList."""
    DEFAULT_CSS = """
    Ask { align: center middle; }
    Ask > Vertical { width: 70; height: auto; border: round $border-active; padding: 1 2; background: $surface; }
    Ask OptionList { height: 10; }
    Ask Horizontal { height: auto; }
    """

    def __init__(self, title: str, fields: list[tuple[str, str, str]], options: list[tuple[str, str]] | None = None):
        super().__init__()
        self.title_, self.fields, self.options = title, fields, options

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self.title_, style="bold"))
            if self.options:
                yield OptionList(*[Text.assemble(t, ("  " + i[:8], "dim")) for i, t in self.options], id="opts")
            for key, label, default in self.fields:
                yield Label(label)
                yield Input(value=default, id=key)
            with Horizontal():
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#ok")
    @on(Input.Submitted)
    @on(OptionList.OptionSelected)
    def _ok(self, _=None) -> None:
        res = {k: self.query_one(f"#{k}", Input).value.strip() for k, _, _ in self.fields}
        if self.options:
            idx = self.query_one("#opts", OptionList).highlighted
            if idx is None:
                self.dismiss(None)  # nothing chosen: never fall through to a default choice
                return
            res["choice"] = self.options[idx][0]
        self.dismiss(res)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)


class SessionTree(Tree):
    """Single click shows the session in the work pane but keeps focus here; Enter or double-click
    enters it (moves focus to the work pane)."""

    def action_select_cursor(self) -> None:
        self.app.action_enter()  # keyboard Enter: enter + focus the work pane

    async def _on_click(self, event: events.Click) -> None:
        async with self.lock:
            meta = event.style.meta
            if "line" not in meta:
                return
            line = meta["line"]
            if meta.get("toggle", False):
                node = self.get_node_at_line(line)
                if node is not None:
                    self._toggle_node(node)
                return
            self.cursor_line = line
            if event.chain == 2:
                self.app.action_enter()   # double-click: enter + focus the work pane
            else:
                self.app.action_show()    # single click: show it, keep focus on the list


# ------------------------------------------------------------ list pane ----

KEYS = [
    ("⏎", "show session"), ("^T", "focus work pane"),
    ("n", "new"), ("a", "add previous"),
    ("g", "group"), ("r", "rename"),
    ("x", "close"), ("c", "compact"),
    ("m", "map on/off"), ("l", "log on/off"),
    ("h", "Claude Code | pi tab"), ("=", "optimize layout"),
    ("d", "describe role"), ("q", "quit"),
]


def help_text(width: int) -> Text:
    """Key legend, two entries per row (one when narrow), keys highlighted."""
    per_row = 2 if width >= 30 else 1
    col = max(14, (width - 1) // per_row)
    t = Text()
    for i in range(0, len(KEYS), per_row):
        for k, desc in KEYS[i:i + per_row]:
            cell = Text.assemble((f" {k}", "bold cyan"), (f" {desc}", "dim"))
            cell.truncate(col, overflow="ellipsis")
            cell.pad_right(col - cell.cell_len)
            t.append_text(cell)
        t.append("\n")
    t.append(" ↑↓ highlight · click/⏎ show · ^T enter pane", style="dim")
    t.append("\n these keys act only when this panel is focused (^T)", style="cyan")
    t.append("\n" + "─" * max(1, width - 1), style="dim")
    t.append("\n resize: drag borders · scroll: wheel · drag to copy text", style="dim")
    t.append("\n tip: c sends /compact to the selected session — keeps it in its group (/new leaves it)", style="cyan")
    return t


class Help(Static):
    """Key legend that re-flows to its own width."""

    def on_resize(self, event: events.Resize) -> None:
        self.update(help_text(event.size.width))


class ListApp(_Chrome, App):
    """Left pane: the session tree, all actions, the data poll, and the nested client's target."""
    CSS = """
    #sessions { height: 1fr; }
    #help { dock: bottom; height: auto; padding: 0 0 0 0; border-top: solid $border-idle; }
    """

    def get_driver_class(self):
        return _NoMouseDriver or super().get_driver_class()
    BINDINGS = [
        Binding("enter", "enter", "enter", show=False),
        Binding("n", "new", "new", show=False),
        Binding("a", "add", "add", show=False),
        Binding("g", "group", "group", show=False),
        Binding("r", "rename", "rename", show=False),
        Binding("d", "describe", "describe", show=False),
        Binding("x", "close", "close", show=False),
        Binding("c", "compact", "compact", show=False),
        Binding("m", "map", "map", show=False),
        Binding("l", "log", "log", show=False),
        Binding("h", "tab", "tab", show=False),
        Binding("equals_sign,plus", "optimize", "optimize", show=False),
        Binding("q", "quit_all", "quit", show=False),
    ]

    def __init__(self):
        super().__init__(ansi_color=True)  # inherit the terminal's default background, like the work pane
        self.cfg = load_config()
        self.ui = load_ui()
        self.roots: list[model.Node] = []
        self.cross: list[model.Edge] = []
        self.msgs: list[model.Msg] = []
        self._hash = None
        self._tree_written = 0.0
        self._shown: dict[str, str] = {}   # harness -> pane its work pane shows
        self._active_h = "cc"              # harness whose work pane the user last committed to
        self.working_id: str | None = None
        self._adopting: set[str] = set()
        self._want: str | None = None       # session the user is on; survives transient snapshot drops
        self._want_pane: tuple[str, str] = ("", "")  # its (harness, tmux pane): follows an in-place /new
        self._rebuilding = False

    def compose(self) -> ComposeResult:
        yield SessionTree("sessions", id="sessions")
        yield Help(help_text(34), id="help")

    def on_mount(self) -> None:
        self.query_one("#sessions", Tree).show_root = False
        self.refresh_data(force=True)
        # show the first session of each harness at launch (display only) so no work pane is blank;
        # cursor moves won't change them — only Enter will
        for h in harness.HARNESSES:
            first = next((n for n in self.all_nodes() if n.harness == h), None)
            tty = self._term(h)[1]
            pane = self._pane_of(first) if first and tty else ""
            if pane and tmux.show_in_client(tty, pane, h):
                self._shown[h] = pane
        self._write_ui()
        self.set_interval(float(self.cfg["poll"]), self.refresh_data)
        self.set_interval(0.5, self._sync_term)

    # ---- data ----
    def refresh_data(self, force: bool = False) -> None:
        model.STALE_S = int(self.cfg["stale_s"])
        try:
            roots, cross, msgs = model.snapshot()
        except Exception as e:  # a bad file must not kill the poller
            self.notify(f"poll failed: {e}", severity="error", markup=False)
            return
        h = hash(repr([(n.session_id, n.pid, n.tmux_pane, n.name, n.state, n.status_line, n.label, n.summary,
                        [c.name for c in n.children]) for r in roots for n in _walk(r)] + cross
                       + [(m.ts, m.src, m.dst, m.label) for m in msgs[-8:]]))
        if h == self._hash and not force:
            if time.time() - self._tree_written > 10:
                self._write_state(roots)
            return
        self._hash, self.roots, self.cross, self.msgs = h, roots, cross, msgs
        self._write_state(roots)
        self._rebuild_tree()

    def _write_state(self, roots) -> None:
        """tree.json feeds the hooks; view.json feeds the map pane."""
        try:
            model.write_json(TREE, tree_dump(roots))
            model.write_json(VIEW, {"roots": [_ser(r) for r in roots], "cross": self.cross,
                                    "msgs": [[m.ts, m.src, m.dst, m.label] for m in self.msgs[-60:]]})
            self._tree_written = time.time()
        except OSError:
            pass

    def _write_ui(self) -> None:
        c = self._committed()  # what the work pane shows (set by Enter), not the tree cursor
        sid = c.session_id if c else None
        self.working_id = sid
        # the map/log tab is the harness last committed to: they never mix Claude Code and pi
        self.ui = load_ui() | {"selected": sid, "working": sid, "tab": self._active_h,
                               "map": self.cfg["map"], "log": self.cfg["log"]}
        try:
            model.write_json(UI, self.ui)
        except OSError:
            pass

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#sessions", Tree)
        prev_line = tree.cursor_line
        cur = self.selected()
        want = self._want or (cur.session_id if cur else None)
        self._rebuilding = True
        tree.clear()
        target_line = None
        line = 0  # display line of each node (DFS pre-order; the hidden root is line -1)
        by_line: list[model.Node | None] = []

        def add(parent, n: model.Node):
            nonlocal target_line, line
            leaf = parent.add(_label(n), data=n, expand=True)
            by_line.append(n)
            if n.session_id == want:
                target_line = line
            line += 1
            for c in n.children:
                add(leaf, c)

        header = _both(self.roots)  # a pi section only while both harnesses are running
        for r in self.roots:
            if header and r.harness == "pi":
                # roots arrive Claude first: one header row opens the pi section (data=None: not a session)
                tree.root.add_leaf(Text("── pi sessions ──", style="bold magenta"), data=None)
                by_line.append(None)
                line += 1
                header = False
            add(tree.root, r)
        if target_line is None and self._want_pane[1]:
            # `want` is gone but its tmux pane lives on — an agent's `/new` reused the pane under a new
            # sessionId. Follow the selection to whatever session now occupies that pane.
            for i, n in enumerate(by_line):
                if n and (n.harness, self._pane_of(n)) == self._want_pane:
                    target_line, self._want = i, n.session_id
                    break
        # move_cursor(node) is unreliable here: a freshly added node's ._line is -1 until the next
        # layout, so it collapses to line 0 (the first row). Set cursor_line by index instead.
        if target_line is not None:
            tree.cursor_line = target_line
        elif tree.root.children:
            # `want` isn't in this snapshot (a session blips out for a poll or two, or none picked
            # yet). Keep the current row instead of yanking to the top; when `want` reappears the
            # next rebuild snaps back to it. Don't overwrite `_want` from this fallback position.
            tree.cursor_line = prev_line if self._want else 0  # setter clamps to a valid line
        self._rebuilding = False
        self._write_ui()
        self._sync_term()

    def selected(self) -> model.Node | None:
        node = self.query_one("#sessions", Tree).cursor_node
        return node.data if node else None

    def all_nodes(self) -> list[model.Node]:
        return [n for r in self.roots for n in _walk(r)]

    @on(Tree.NodeHighlighted)
    def _moved(self) -> None:
        if self._rebuilding:
            return  # a rebuild is restoring the cursor; not a user navigation
        sel = self.selected()
        if sel:
            self._want = sel.session_id
            self._want_pane = (sel.harness, self._pane_of(sel))
        # cursor movement is pre-selection only: it must NOT switch the work pane / map / log.
        # Enter commits (see action_enter -> _goto); nothing else here.

    # ---- nested client ----
    def _pane_of(self, n: model.Node) -> str:
        """The node's pane on ITS harness's server (pane ids are per server: cc %3 and pi %3 differ)."""
        if n.tmux_pane and tmux.pane_owns(n.tmux_pane, n.pid, n.harness):
            return n.tmux_pane
        return tmux.pane_for_pid(n.pid, n.harness)

    def _term(self, h: str) -> tuple[str, str]:
        """(outer pane id, tty) of harness h's work pane; ('', '') when it has none (pi not installed)."""
        t = (self.ui.get("terms") or {}).get(h) or {}
        return t.get("pane", ""), t.get("tty", "")

    def _reveal(self, h: str) -> None:
        """Show only h's work pane: the other harness's waits in the hidden window ui:1 (see launch), and
        swapping moves the panes with their clients, so pane ids and ttys stay valid."""
        term = self._term(h)[0]
        other = next((t.get("pane") for k, t in (self.ui.get("terms") or {}).items() if k != h and t.get("pane")), "")
        if term and other and tmux.outer("display-message", "-p", "-t", term, "#{window_index}").strip() != "0":
            tmux.outer_ok("swap-pane", "-d", "-s", term, "-t", other)

    def _term_focused(self, h: str) -> bool:
        return tmux.outer("display-message", "-p", "-t", "ui:0", "#{pane_id}").strip() == self._term(h)[0]

    def _node_at(self, h: str, pane: str) -> model.Node | None:
        return next((n for n in self.all_nodes() if pane and n.harness == h and self._pane_of(n) == pane), None)

    def _committed(self) -> model.Node | None:
        """The session shown in the work pane last committed to — identified by that pane, so it follows
        an in-place /new (same pane, new id) and never changes just because the tree cursor moved."""
        return self._node_at(self._active_h, self._shown.get(self._active_h, ""))

    def _sync_term(self) -> None:
        for h, shown in list(self._shown.items()):
            term, tty = self._term(h)
            sel = self._node_at(h, shown)
            if not sel or not tty:
                continue
            pane = self._pane_of(sel)
            if pane and pane != shown:
                if tmux.show_in_client(tty, pane, h):
                    self._shown[h] = pane
            elif pane and self._term_focused(h):
                # keep the shown window sized to our pane while the user works here (another client
                # attached to the same window may have shrunk it in between)
                win = tmux._run("display-message", "-p", "-t", tty, "#{session_name}:#{window_index}", h=h).strip()
                size = tmux._run("display-message", "-p", "-t", tty,
                                 "#{client_width} #{client_height} #{window_width} #{window_height}", h=h).split()
                if win and len(size) == 4 and size[:2] != size[2:]:
                    tmux.fit_window(tty, win, h)
            ctx = f" · {sel.ctx_pct}% ctx" if sel.ctx_pct is not None else ""
            tag = f" · {harness.LABEL[h]}" if h != "cc" else ""
            title = f" {sel.name}{ctx}{tag} " if pane else f" {sel.name} (not in tmux — Enter to adopt)"
            tmux.outer_ok("select-pane", "-t", term, "-T", title)

    def _goto(self, pane: str, h: str, focus: bool = True) -> None:
        term, tty = self._term(h)
        if not tty:
            self.notify(f"no {harness.LABEL[h]} work pane: restart pengupool after installing {harness.CLI[h]}",
                        severity="warning", markup=False)
            return
        self._reveal(h)
        if tmux.show_in_client(tty, pane, h):
            self._shown[h] = pane  # committing to this pane is what "selects" the session
            self._active_h = h
        self._write_ui()
        if focus:  # move keyboard into the work pane (used after new/adopt; ^T from the list)
            # re-assert after the event settles: a mouse click also makes tmux focus the list pane,
            # which can otherwise land after this and snap focus back off the session.
            move = lambda: tmux.outer_ok("select-pane", "-t", term)
            move()
            self.call_after_refresh(move)

    def _select_pane(self, pane: str, h: str) -> None:
        match = self._node_at(h, pane)
        if match:
            self._want = match.session_id  # sticky; _rebuild_tree places the cursor by index
            self._rebuild_tree()
        self._goto(pane, h)

    async def _await_pane(self, pane: str, h: str) -> None:
        """A freshly launched session isn't a tree node until the agent writes its state; poll
        until it appears, then select it — otherwise _rebuild_tree falls back to the first row."""
        for _ in range(20):
            await asyncio.sleep(0.5)
            self.refresh_data(force=True)
            if self._node_at(h, pane):
                break
        self._select_pane(pane, h)

    # ---- actions ----
    async def _adopt(self, n: model.Node) -> None:
        self._adopting.add(n.session_id)
        try:
            h = n.harness
            existing = tmux.pane_resuming(n.session_id, h)
            if existing:
                self.notify(f"{n.name} is already resumed in tmux", markup=False)
                self._select_pane(existing, h)
                return
            if not model.resumable_transcript(n.session_id, n.cwd, h):
                self.notify(f"{harness.LABEL[h]} has not saved this session yet. Send a prompt in the outside "
                            "session and wait for it to finish before adopting it.", severity="warning", markup=False)
                return
            pane = tmux.reserve_window(n.cwd, n.name, h)
            if not pane:
                self.notify("could not prepare a tmux window; the outside session is still running",
                            severity="error", markup=False)
                return
            self.notify(f"stopping {n.name} (pid {n.pid})…", markup=False)
            if not await asyncio.to_thread(tmux.stop, n.pid):
                tmux.kill_reserved(pane, h)
                self.notify("could not stop the outside session", severity="error", markup=False)
                return
            if not tmux.start_reserved(pane, n.cwd, n.name, n.session_id, h):
                self.notify(f"could not start {harness.LABEL[h]} in pane {pane}; resume {n.session_id} manually",
                            severity="error", markup=False)
                return
            for _ in range(20):
                await asyncio.sleep(0.5)
                self.refresh_data(force=True)
                if any(x.session_id == n.session_id and x.pid != n.pid for x in self.all_nodes()):
                    break
            self._select_pane(pane, h)
        finally:
            self._adopting.discard(n.session_id)

    def action_show(self) -> None:
        """Single click: show the selected session in the work pane, keep keyboard on the list."""
        n = self.selected()
        pane = self._pane_of(n) if n else ""
        if pane:
            self._goto(pane, n.harness, focus=False)

    def action_enter(self) -> None:
        n = self.selected()
        if not n:
            return
        pane = self._pane_of(n)
        if pane:
            self._goto(pane, n.harness, focus=False)  # show it; ^T moves focus into the work pane
            return
        if n.session_id in self._adopting:
            self.notify(f"{n.name} is being moved into tmux, hold on…", markup=False)
            return
        existing = tmux.pane_resuming(n.session_id, n.harness)
        if existing:
            self._select_pane(existing, n.harness)
            return
        if not os.path.isdir(n.cwd):
            self.notify(f"cannot adopt {n.name}: its directory {n.cwd} no longer exists", severity="error", markup=False)
            return

        def done(res):
            if res is not None and n.session_id not in self._adopting:
                self.run_worker(self._adopt(n), exclusive=True)
        self.push_screen(Ask(f"{n.name} is not in tmux. Stop it NOW (interrupting its current turn) and resume it "
                             f"in a tmux window from {n.cwd}?", []), done)

    def action_new(self) -> None:
        n = self.selected()
        cwd = n.cwd if n else str(Path.cwd())
        # new sessions start at top level; the user assigns a parent later with `g`

        def named(directory, res):
            if not (res and res.get("name")):
                self.notify("a new session needs a name", severity="warning", markup=False)
                return
            h = res.get("choice") or available[0]
            work_cwd = tmux.worktree_add(directory, res["name"]) or directory  # own worktree, else plain
            pane = tmux.new_window(work_cwd, res["name"], h)
            if pane:
                self.run_worker(self._await_pane(pane, h), exclusive=True)
            else:
                self.notify("tmux new-window failed", severity="error", markup=False)

        # choose the harness only when there is a choice: a Claude-only setup looks exactly as before
        available = [h for h in harness.HARNESSES if shutil.which(harness.CLI[h])] or ["cc"]
        opts = [(h, harness.LABEL[h]) for h in available] if len(available) > 1 else None

        def pick(directory):
            if not directory:
                return
            self.push_screen(Ask(f"New session in {directory}", [("name", "session name", Path(directory).name)], opts),
                             lambda res: named(directory, res))

        # browse from home so sibling repos are all reachable; fall back to the selected cwd
        root = str(Path.home()) if os.path.isdir(Path.home()) else (cwd if os.path.isdir(cwd) else "/")
        self.push_screen(SelectDirectory(root, title="Pick a directory for the new session"),
                         lambda p: pick(str(p)) if p else None)

    def action_add(self) -> None:
        n = self.selected()
        cwd = n.cwd if n else str(Path.cwd())
        harness_of: dict[str, str] = {}

        def picked_session(directory, r2):
            if r2 and r2.get("choice") and r2["name"]:
                h = harness_of[r2["choice"]]
                already = tmux.pane_resuming(r2["choice"], h)
                if already or any(x.session_id == r2["choice"] for x in self.all_nodes()):
                    self.notify("that session is already running", severity="warning", markup=False)
                    if already:
                        self._select_pane(already, h)
                    return
                pane = tmux.resume_window(directory, r2["name"], r2["choice"], h)
                if pane:
                    self.run_worker(self._await_pane(pane, h), exclusive=True)
                else:
                    self.notify("tmux new-window failed", severity="error", markup=False)

        def pick_dir(directory):
            if not directory:
                return
            past = past_sessions(directory)
            harness_of.update({sid: h for sid, _, h in past})
            opts = [(sid, title if h == "cc" else f"{title}  [{harness.LABEL[h]}]") for sid, title, h in past]
            if not opts:
                self.notify("no past sessions for that directory", severity="warning", markup=False)
                return
            self.push_screen(Ask("Resume which session?", [("name", "session name", Path(directory).name)], opts),
                             lambda r2: picked_session(directory, r2))

        root = str(Path.home()) if os.path.isdir(Path.home()) else (cwd if os.path.isdir(cwd) else "/")
        self.push_screen(SelectDirectory(root, title="Pick a directory to add a previous session"),
                         lambda p: pick_dir(str(p)) if p else None)

    def action_group(self) -> None:
        n = self.selected()
        if not n:
            return
        mine = {x.session_id for x in _walk(n)}
        opts = [("", "(top level)")] + \
               [(x.session_id, x.name) for x in self.all_nodes() if x.session_id not in mine and x.harness == n.harness]

        def done(res):
            if res is None or "choice" not in res:
                return
            groups = model.load_groups()
            err = model.group_error(n.session_id, res["choice"], model.load_sessions(), groups)
            if err:
                self.notify(err, severity="error", markup=False)
                return
            groups[n.session_id] = res["choice"]  # "" pins at top level
            model.save_groups(groups)
            self.refresh_data(force=True)
        self.push_screen(Ask(f"Move {n.name} under…", [], opts), done)

    def action_rename(self) -> None:
        n = self.selected()
        if not n:
            return
        pane = self._pane_of(n)
        if not pane:
            self.notify("session is not in tmux yet: press Enter to adopt it first", severity="warning", markup=False)
            return

        def done(res):
            if res and res["name"] and res["name"] != n.name:
                cmd = harness.RENAME[n.harness]
                ok = tmux.send(pane, f"{cmd} {tmux.safe_arg(res['name'])}", n.harness)
                self.notify(f"sent {cmd}" if ok else "no tmux pane for this session", severity="information" if ok else "error", markup=False)
        self.push_screen(Ask(f"Rename {n.name}", [("name", "new name", n.name)]), done)

    def action_describe(self) -> None:
        n = self.selected()
        if not n:
            return
        p = profiles.load(n.session_id)
        by = f"  (last set by {p['description_editor']} {p.get('updated_at', '')})" if p.get("description_editor") else ""

        def done(res):
            if res is None:
                return
            try:
                profiles.describe(n.session_id, res["summary"], res["responsibility"], editor="")
            except (ValueError, PermissionError) as e:
                self.notify(str(e), severity="error", markup=False)
                return
            self.notify("role saved; the session sees it on its next prompt", markup=False)
            self.refresh_data(force=True)
        self.push_screen(Ask(f"Describe {n.name}{by}", [
            ("summary", f"summary: one line, what it owns (≤{profiles.SUMMARY_MAX})", p.get("summary", "")),
            ("responsibility", "responsibility: a short private brief", p.get("responsibility", ""))]), done)

    def action_compact(self) -> None:
        """Send /compact to the selected session (keeps its id, so it stays in its group)."""
        n = self.selected()
        if not n:
            return
        pane = self._pane_of(n)
        if not pane:
            self.notify("session is not in tmux yet: press Enter to adopt it first", severity="warning", markup=False)
            return
        ok = tmux.send(pane, "/compact", n.harness)
        self.notify(f"sent /compact to {n.name}" if ok else "no tmux pane for this session",
                    severity="information" if ok else "error", markup=False)

    def action_close(self) -> None:
        n = self.selected()
        if not n:
            return

        def done(res):
            if res is not None:
                ok = tmux.kill(self._pane_of(n), n.pid, n.harness)
                self.notify(f"closed {n.name}" if ok else "could not close", severity="information" if ok else "error", markup=False)
        self.push_screen(Ask(f"Close {n.name}? (OK to confirm)", []), done)

    def action_tab(self) -> None:
        """Switch the map/log between the Claude Code and pi trees (only while both are running)."""
        if not _both(self.roots):
            self.notify("only one harness is running", markup=False)
            return
        self._active_h = "pi" if self._active_h == "cc" else "cc"
        self._reveal(self._active_h)
        self._write_ui()

    def action_map(self) -> None:
        self.cfg["map"] = not self.cfg["map"]
        save_config(self.cfg)
        self._write_ui()

    def action_log(self) -> None:
        self.cfg["log"] = not self.cfg["log"]
        save_config(self.cfg)
        self._write_ui()

    # ---- layout (keyboard, so it works where Orca eats mouse-border drags) ----
    def _outer_h(self, pane: str) -> int:
        try:
            return int(tmux.outer("display-message", "-p", "-t", pane, "#{window_height}").strip())
        except ValueError:
            return 45

    def action_optimize(self) -> None:
        """Reset the session list to its default width and grow the top region so the selected
        session's map is fully visible; the work pane keeps the rest."""
        ui = self.ui or load_ui()
        lp, mp = ui.get("list_pane", ""), ui.get("map_pane", "")
        tmux.outer_ok("resize-pane", "-t", lp, "-x", str(DEFAULT_LIST_W))
        sel = self.selected()
        root = next((r for r in self.roots if sel and any(x.session_id == sel.session_id for x in _walk(r))),
                    self.roots[0] if self.roots else None)
        if root and mp:
            need = len(render_tree(root, max_w=2000, cross=self.cross)) + 3  # + pane/container chrome
            top = max(5, min(need, self._outer_h(mp) - 10))  # leave the work pane at least ~10 rows
            tmux.outer_ok("resize-pane", "-t", mp, "-y", str(top))
        save_layout(self.cfg)
        self.notify("optimized layout", markup=False)

    def action_quit_all(self) -> None:
        save_layout(self.cfg)
        tmux.kill_views()
        tmux.outer_ok("kill-server")
        self.exit()


# ------------------------------------------------------------- map pane ----

class MapApp(_Chrome, App):
    """Top-right pane: map + log, fed by view.json / ui.json written by the list pane."""

    def get_driver_class(self):
        return _NoMouseDriver or super().get_driver_class()
    CSS = """
    #top { height: 1fr; }
    #graphwrap { width: 1fr; height: 1fr; border: round $border-idle; }
    #graphwrap:focus-within { border: round $border-active; }
    #graph { width: auto; height: auto; padding: 0 1; }
    #logwrap { width: 1fr; height: 1fr; border: round $border-idle; }
    #logwrap:focus-within { border: round $border-active; }
    #log { width: auto; height: auto; padding: 0 1; }
    .hidden { display: none; }
    """
    BINDINGS = [Binding("m", "map", "map"), Binding("l", "log", "log")]

    def __init__(self):
        super().__init__(ansi_color=True)  # inherit the terminal's default background, like the work pane
        self.cfg = load_config()
        self._stamp = (0.0, 0.0, 0.0)
        self.roots: list[model.Node] = []
        self.cross: list = []
        self.msgs: list = []
        self.selected_id = None
        self.tab = "cc"
        self._shown_id = None  # last session _sel resolved; kept across a transient view.json lag

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            with ScrollableContainer(id="graphwrap"):
                yield Static(id="graph")
            with ScrollableContainer(id="logwrap"):
                yield Static(id="log")

    def on_mount(self) -> None:
        self.query_one("#graphwrap").border_title = "map"
        self.query_one("#logwrap").border_title = "log"
        self._tick()
        self.set_interval(0.2, self._tick)

    def _tick(self) -> None:
        def mt(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        stamp = (mt(VIEW), mt(UI), mt(CONFIG))
        if stamp == self._stamp:
            return
        self._stamp = stamp
        self.cfg = load_config()
        ui = load_ui()
        self.selected_id = ui.get("working") if ui.get("working") else ui.get("selected")
        self.tab = ui.get("tab") if ui.get("tab") in harness.HARNESSES else "cc"
        v = model._json(VIEW) or {}
        self.roots = [_deser(r) for r in v.get("roots", [])]
        self.cross = [tuple(c) for c in v.get("cross", [])]
        self.msgs = v.get("msgs", [])
        for wid, key in (("#graphwrap", "map"), ("#logwrap", "log")):
            self.query_one(wid).set_class(not self.cfg[key], "hidden")
        self._draw()

    def _sel(self) -> model.Node | None:
        # ui.json (selection) updates more often than view.json (this tree), so the selected id can
        # briefly name a session not in `roots` yet — e.g. right after /new. Keep showing the last
        # session we resolved instead of snapping to roots[0], and let it catch up next tick.
        # with both harnesses running, only the active tab's trees are candidates: never mix them
        pool = [r for r in self.roots if r.harness == self.tab] if _both(self.roots) else self.roots
        for want in (self.selected_id, self._shown_id):
            for r in pool:
                for n in _walk(r):
                    if n.session_id == want:
                        self._shown_id = want
                        return n
        return pool[0] if pool else None

    def _root_of(self, n: model.Node) -> model.Node:
        for r in self.roots:
            if any(x is n for x in _walk(r)):
                return r
        return n

    def _draw(self) -> None:
        sel = self._sel()
        graph, log = self.query_one("#graph", Static), self.query_one("#log", Static)
        if not sel:
            graph.update("no live sessions")
            log.update("")
            return
        root = self._root_of(sel)
        names = {n.name for n in _walk(root)}
        tabs = _tabs(root.harness) if _both(self.roots) else Text("")
        self.query_one("#graphwrap").border_title = Text.assemble("map ", tabs)
        self.query_one("#logwrap").border_title = Text.assemble("log ", tabs)
        if self.cfg["map"]:
            rows = render_tree(root, max_w=2000, cross=self.cross)
            text = Text("\n".join(rows), no_wrap=True, overflow="ignore")
            focus_xy = None
            for n in _walk(root):
                for m in re.finditer(re.escape(f"{GLYPH[n.state]} {n.name}"), text.plain):
                    text.stylize(COLOR[n.state] + (" bold reverse" if n is sel else " bold"), m.start(), m.end())
                    if n is sel and focus_xy is None:
                        before = text.plain[: m.start()]
                        focus_xy = (m.start() - before.rfind("\n") - 1, before.count("\n"))
                for ln in wrap_label(n.label):
                    if len(ln) >= 4:
                        for m in re.finditer(re.escape(ln), text.plain):
                            text.stylize("italic cyan", m.start(), m.end())
            for m in re.finditer(r"▼", text.plain):
                text.stylize(ARROW_DOWN, m.start(), m.end())   # tree edges: parent -> child
            for m in re.finditer(r"[┈┊◀╮╯]+", text.plain):
                text.stylize("magenta", m.start(), m.end())
            for src, dst, label in self.cross:
                if label and src in names and dst in names:
                    for m in re.finditer(re.escape(label[:CROSS_LABEL_W - 1]), text.plain):
                        text.stylize("italic magenta", m.start(), m.end())
            graph.update(text)
            if focus_xy:
                wrap = self.query_one("#graphwrap")
                x, y = focus_xy
                self.call_after_refresh(lambda: wrap.scroll_to(x=max(0, x - wrap.size.width // 2 + 6),
                                                               y=max(0, y - wrap.size.height // 2), animate=False))
        if self.cfg["log"]:
            anc = _ancestors(root)
            t = Text(no_wrap=True, overflow="ignore")
            for ts, src, dst, label in reversed([m for m in self.msgs if m[1] in names or m[2] in names][-40:]):
                t.append(model.local_log_time(ts) + " ", style="dim")
                t.append(src, style="bold" if src == sel.name else "")
                t.append(" ⇢ ", style=_arrow_style(src, dst, anc))
                t.append(dst, style="bold" if dst == sel.name else "")
                t.append(": " + label + "\n")
            log.update(t if t.plain else Text("no messages in this tree yet", style="dim"))

    def action_map(self) -> None:
        self.cfg["map"] = not self.cfg["map"]
        save_config(self.cfg)

    def action_log(self) -> None:
        self.cfg["log"] = not self.cfg["log"]
        save_config(self.cfg)


# --------------------------------------------------------------- helpers ----

def _ser(n: model.Node) -> dict:
    return {"id": n.session_id, "name": n.name, "cwd": n.cwd, "pid": n.pid, "state": n.state,
            "status": n.status_line, "label": n.label, "harness": n.harness,
            "children": [_ser(c) for c in n.children]}


def _deser(d: dict) -> model.Node:
    n = model.Node(d["id"], d["name"], d["cwd"], d["pid"], d["state"], d["status"], label=d["label"],
                   harness=d.get("harness", "cc"))
    n.children = [_deser(c) for c in d["children"]]
    return n


def _both(roots: list[model.Node]) -> bool:
    """True while sessions of more than one harness are running (roots never mix harnesses)."""
    return len({r.harness for r in roots}) > 1


def _tabs(active: str) -> Text:
    """`Claude Code | pi` tab strip for the map/log titles, the shown harness highlighted (h switches)."""
    t = Text()
    for i, h in enumerate(harness.HARNESSES):
        t.append(" │ " if i else "")
        t.append(f" {harness.LABEL[h]} ", style="bold reverse" if h == active else "dim")
    return t


def _walk(n: model.Node):
    yield n
    for c in n.children:
        yield from _walk(c)


def _ancestors(root: model.Node) -> dict[str, set[str]]:
    """name -> set of ancestor names within the tree, for arrow-direction coloring."""
    anc: dict[str, set[str]] = {root.name: set()}

    def walk(n: model.Node, acc: set[str]):
        for c in n.children:
            anc[c.name] = acc | {n.name}
            walk(c, anc[c.name])

    walk(root, set())
    return anc


def _arrow_style(src: str, dst: str, anc: dict[str, set[str]]) -> str:
    """Arrow color for a src ⇢ dst message: green for parent->child, orange for
    child->parent (a reply), cyan for everything else (siblings / unrelated)."""
    if src in anc.get(dst, ()):
        return ARROW_DOWN      # src is an ancestor of dst → parent -> child
    if dst in anc.get(src, ()):
        return ARROW_UP        # dst is an ancestor of src → child -> parent
    return "cyan"


def _label(n: model.Node) -> Text:
    t = Text(f"{GLYPH[n.state]} {n.name}", style=COLOR[n.state])
    if n.harness != "cc":
        t.append(f" {n.harness}", style="bold magenta")  # Claude sessions stay unmarked, as before
    if n.label:
        t.append(f"  ({wrap_label(n.label)[0]})", style="italic dim")
    if n.summary:
        t.append(f"  — {n.summary[:48]}", style="dim")
    return t


def save_layout(cfg: dict) -> None:
    """Remember the dragged pane sizes for next launch."""
    ui = load_ui()
    try:
        w = int(tmux.outer("display-message", "-p", "-t", ui.get("list_pane", ""), "#{pane_width}").strip())
        h = int(tmux.outer("display-message", "-p", "-t", ui.get("map_pane", ""), "#{pane_height}").strip())
        cfg["list_w"], cfg["top_h"] = w, h
        save_config(cfg)
    except ValueError:
        pass


# -------------------------------------------------------------- launcher ----

def launch() -> None:
    """Build the private tmux UI server and attach to it (this process becomes the tmux client)."""
    cfg = load_config()
    env = dict(os.environ)
    env.pop("TMUX", None)  # the TUI runs inside the OUTER server; sessions live on the shared -L socket
    tmux.kill_views()     # stale grouped views (and their clients) from an earlier run would fight over window size
    # a work pane per harness: pi sessions live on their own tmux server, which one client can't switch to.
    # Only one is on screen; the other waits in the hidden window ui:1 until _reveal swaps it in.
    hs = ["cc"] + (["pi"] if shutil.which(harness.CLI["pi"]) else [])
    for h in hs:
        tmux.ensure_server(h)  # the session each nested client attaches to first
    cols, rows = shutil.get_terminal_size((160, 45))
    # an exported COLUMNS/LINES would make Rich size the pane apps after the outer terminal
    py = f"env -u COLUMNS -u LINES {shlex.quote(sys.executable)}"
    o = lambda *a: tmux.outer_ok(*a)
    tmux.outer_ok("kill-server")
    o("-f", "/dev/null", "new-session", "-d", "-s", "ui", "-x", str(cols), "-y", str(rows),
      "-e", "TMUX=", f"{py} -m pengupool.app --pane list")
    for opt in (("mouse", "on"), ("prefix", "None"), ("prefix2", "None"), ("status", "off"), ("escape-time", "0"),
                ("pane-border-status", "top"), ("pane-border-format", " #{pane_title} "), ("focus-events", "on"),
                ("pane-border-lines", "heavy"), ("pane-border-style", f"fg={BORDER_IDLE}"),
                ("pane-active-border-style", f"fg={BORDER_ACTIVE}"),
                ("remain-on-exit", "off"), ("history-limit", "2000"), ("default-terminal", "tmux-256color")):
        o("set-option", "-g", *opt)  # mouse ON: click selects panes, wheel scrolls, borders drag to resize
    o("set-option", "-ga", "terminal-overrides", ",*:RGB")
    o("bind-key", "-n", "C-t", "select-pane", "-l")  # keyboard alt to clicking: switch focus list <-> work pane
    # Resize by dragging any border with the mouse; Alt+Shift+arrows kept as a keyboard alternative.
    right_w = max(40, cols - int(cfg["list_w"]) - 1)
    o("split-window", "-h", "-t", "ui:0.0", "-l", str(right_w), f"{py} -m pengupool.app --pane map")
    term_h = max(8, rows - int(cfg["top_h"]) - 2)
    attach = lambda h: shlex.join(["tmux", "-L", harness.SOCK[h], "-u", "attach-session", "-t", tmux.SESSION])
    o("split-window", "-v", "-t", "ui:0.1", "-l", str(term_h), "-e", "TMUX=", attach("cc"))
    if "pi" in hs:
        o("new-window", "-d", "-t", "ui:1", "-e", "TMUX=", attach("pi"))
    panes = {}
    for line in tmux.outer("list-panes", "-s", "-t", "ui", "-F",
                           "#{window_index}.#{pane_index}\t#{pane_id}\t#{pane_tty}").splitlines():
        idx, pid_, tty = line.split("\t")
        panes[idx] = (pid_, tty)
    slot = {"cc": "0.2", "pi": "1.0"}
    terms = {h: dict(zip(("pane", "tty"), panes.get(slot[h], ("", "")))) for h in hs}
    ui = load_ui() | {"list_pane": panes.get("0.0", ("", ""))[0], "map_pane": panes.get("0.1", ("", ""))[0], "terms": terms}
    model.write_json(UI, ui)
    o("select-pane", "-t", ui["list_pane"], "-T", " sessions ")
    o("select-pane", "-t", ui["map_pane"], "-T", " map · log ")
    for h in hs:
        o("select-pane", "-t", terms[h]["pane"], "-T", f" {harness.LABEL[h]} ")
    # Resize the panes by dragging their borders with the mouse. (No keyboard resize: the safe
    # combos are all taken — Ctrl+arrows are macOS Mission Control, Ctrl+[ is Escape, etc.)
    # Text selection in the work pane: left-drag selects and auto-copies to the clipboard on release
    # (tmux copy-mode + pbcopy, wired on the sessions server in tmux.enable_mouse_copy).
    o("select-pane", "-t", ui["list_pane"])
    os.execvpe("tmux", ["tmux", "-L", tmux.OUTER, "attach-session", "-t", "ui"], env)


def main() -> None:
    args = sys.argv[1:]
    if args[:1] in (["--version"], ["-V"]):
        from importlib.metadata import version
        print(f"pengupool {version('pengupool')}")
        return
    if args in (["install-hook"], ["uninstall-hook"]):
        from .hook import install, uninstall
        action = install if args[0] == "install-hook" else uninstall
        action(Path(os.environ.get("CLAUDE_SETTINGS", str(model.CLAUDE / "settings.json"))))
        return
    if args[:1] in (["setup"], ["teardown"]):
        from .install import main as setup_main  # prerequisites + per-harness wiring (see install.py)
        sys.exit(setup_main(args))
    if args[:1] == ["serve"]:
        from .serve import serve  # shared JSON backend for the VS Code extension (no tmux/Textual UI)
        serve(once="--once" in args)
        return
    if args[:1] == ["ctl"]:
        from .ctl import main as ctl_main  # one-shot mutations for the VS Code extension
        sys.exit(ctl_main())
    if args[:1] == ["--pane"]:
        {"list": ListApp, "map": MapApp}[args[1]]().run()
        return
    launch()


if __name__ == "__main__":
    main()
