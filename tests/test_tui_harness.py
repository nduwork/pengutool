"""The TUI keeps one work pane per harness: pane ids are per tmux server, so a Claude %3 and a pi
%3 are different panes, and showing one must never replace the other's work pane."""
import json

from pengupool import app as tui, model, tmux
from pengupool.app import ListApp, _arrow_style, _ancestors, ARROW_DOWN, ARROW_UP
from pengupool.model import Node

TERMS = {"cc": {"pane": "%10", "tty": "/dev/ttys1"}, "pi": {"pane": "%11", "tty": "/dev/ttys2"}}


def test_each_harness_shows_in_its_own_work_pane(monkeypatch):
    a = ListApp()
    a.ui = {"terms": TERMS}
    cc = Node("c", "cc-s", "/r", 1, "active", "", tmux_pane="%3")
    pi = Node("p", "pi-s", "/r", 2, "active", "", tmux_pane="%3", harness="pi")  # same id, other server
    a.roots = [cc, pi]
    monkeypatch.setattr(tmux, "pane_owns", lambda pane, pid, h: True)
    shown = []
    monkeypatch.setattr(tmux, "show_in_client", lambda tty, pane, h: shown.append((tty, pane, h)) or True)
    monkeypatch.setattr(a, "_write_ui", lambda: None)
    where, swaps = {"%10": "0", "%11": "1"}, []                 # the pi work pane starts hidden in ui:1

    def swap(*args):
        swaps.append(args)
        where["%10"], where["%11"] = where["%11"], where["%10"]
        return True
    monkeypatch.setattr(tmux, "outer", lambda *args: where[args[3]])
    monkeypatch.setattr(tmux, "outer_ok", swap)
    a._goto("%3", "pi", focus=False)
    a._goto("%3", "pi", focus=False)                            # already on screen: no swap
    a._goto("%3", "cc", focus=False)
    assert swaps == [("swap-pane", "-d", "-s", "%11", "-t", "%10"), ("swap-pane", "-d", "-s", "%10", "-t", "%11")] and where == {"%10": "0", "%11": "1"}
    assert shown == [("/dev/ttys2", "%3", "pi"), ("/dev/ttys2", "%3", "pi"), ("/dev/ttys1", "%3", "cc")]
    assert a._shown == {"pi": "%3", "cc": "%3"}          # both stay shown
    assert a._node_at("pi", "%3") is pi and a._node_at("cc", "%3") is cc
    assert a._committed() is cc                           # map/log follow the last one entered


def test_missing_work_pane_is_reported_not_crashed(monkeypatch):
    a = ListApp()
    a.ui = {"terms": {"cc": TERMS["cc"]}}                 # pi not installed at launch
    monkeypatch.setattr(tmux, "show_in_client", lambda *a: (_ for _ in ()).throw(AssertionError("no pane")))
    notes = []
    monkeypatch.setattr(a, "notify", lambda msg, **k: notes.append(msg))
    a._goto("%3", "pi", focus=False)
    assert "no pi work pane" in notes[0] and a._shown == {}


def test_launcher_adds_a_pi_work_pane_only_when_pi_is_installed(monkeypatch, tmp_path):
    for installed in (False, True):
        cmds, out = [], []
        monkeypatch.setattr(tui, "UI", tmp_path / "ui.json")
        monkeypatch.setattr(tui.shutil, "which", lambda cli: "/bin/pi" if installed and cli == "pi" else None)
        monkeypatch.setattr(tmux, "kill_views", lambda: None)
        monkeypatch.setattr(tmux, "ensure_server", lambda h: out.append(h) or True)
        monkeypatch.setattr(tmux, "outer_ok", lambda *a: cmds.append(a) or True)
        rows = ["0.0", "0.1", "0.2"] + ["1.0"] * installed
        monkeypatch.setattr(tmux, "outer", lambda *a: "".join(f"{r}\t%{i}\t/dev/t{i}\n" for i, r in enumerate(rows)))
        monkeypatch.setattr(tui.os, "execvpe", lambda *a: None)
        tui.launch()
        splits = [c[-1] for c in cmds if c[0] == "split-window"]
        hidden = [c[-1] for c in cmds if c[0] == "new-window"]  # one work pane on screen, pi's waits off it
        assert out == (["cc", "pi"] if installed else ["cc"])
        assert any("-L pengupool -u attach-session" in c for c in splits)
        assert not any("pengupool-pi" in c for c in splits)
        assert any("-L pengupool-pi -u attach-session" in c for c in hidden) == installed
        terms = json.loads((tmp_path / "ui.json").read_text())["terms"]
        assert terms == ({"cc": {"pane": "%2", "tty": "/dev/t2"}, "pi": {"pane": "%3", "tty": "/dev/t3"}}
                         if installed else {"cc": {"pane": "%2", "tty": "/dev/t2"}})


def _isolated(monkeypatch, tmp_path):
    for name in ("UI", "VIEW", "TREE", "CONFIG"):
        monkeypatch.setattr(tui, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(model, "snapshot", lambda light=False: ([], [], []))


def _rows(app):
    from textual.widgets import Tree
    tree = app.query_one("#sessions", Tree)
    out = []

    def walk(n):
        for c in n.children:
            out.append(c)
            walk(c)
    walk(tree.root)
    return tree, out


def test_pi_section_only_while_both_harnesses_run(monkeypatch, tmp_path):
    import asyncio
    _isolated(monkeypatch, tmp_path)
    lead = Node("c", "lead", "/r", 1, "active", "", children=[Node("k", "kid", "/r", 2, "active", "")])
    pi = Node("p", "piw", "/r", 3, "active", "", harness="pi")

    async def scenario():
        a = ListApp()
        async with a.run_test() as pilot:
            a.roots = [lead]
            a._rebuild_tree()
            await pilot.pause()
            assert [r.label.plain for r in _rows(a)[1]] == ["● lead", "● kid"]   # one harness: as before
            a.roots, a._want = [lead, pi], "p"
            a._rebuild_tree()
            await pilot.pause()
            tree, rows = _rows(a)
            assert [r.data.session_id if r.data else "HEADER" for r in rows] == ["c", "k", "HEADER", "p"]
            assert "pi sessions" in rows[2].label.plain
            assert a.selected().session_id == "p"      # cursor lands past the header on the wanted row
            tree.cursor_line = 2
            await pilot.pause()
            assert a.selected() is None                 # the header is not a session: actions no-op
    asyncio.run(scenario())


def test_h_switches_the_map_log_tab_only_while_both_run(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    a = ListApp()
    notes = []
    monkeypatch.setattr(a, "notify", lambda msg, **k: notes.append(msg))
    a.roots = [Node("c", "lead", "/r", 1, "active", "")]
    a.action_tab()
    assert notes == ["only one harness is running"] and a._active_h == "cc"
    a.roots.append(Node("p", "piw", "/r", 3, "active", "", harness="pi"))
    a.action_tab()
    assert a._active_h == "pi" and json.loads((tmp_path / "ui.json").read_text())["tab"] == "pi"


def test_map_shows_only_the_active_tabs_trees():
    from pengupool.app import MapApp, _tabs
    m = MapApp()
    cc, pi = Node("c", "lead", "/r", 1, "active", ""), Node("p", "piw", "/r", 3, "active", "", harness="pi")
    m.roots, m.selected_id = [cc, pi], "c"
    m.tab = "pi"
    assert m._sel() is pi               # a Claude selection never shows on the pi tab
    m.tab = "cc"
    assert m._sel() is cc
    m.roots, m.tab = [pi], "cc"         # only pi running: no tabs, show what there is
    assert m._sel() is pi
    strip = _tabs("pi")
    assert strip.plain == " Claude Code  │  pi " and any("reverse" in str(sp.style) for sp in strip.spans
                                                      if strip.plain[sp.start:sp.end] == " pi ")


def test_arrow_colors_distinguish_parent_child_direction():
    """log-arrow-colors: parent->child edge is green, child->parent (reply) is orange."""
    root = Node("1", "lead", "/r", 1, "active", "")
    a = Node("2", "worker-a", "/a", 2, "waiting", "")
    b = Node("3", "worker-b", "/b", 3, "stale", "")
    c = Node("4", "worker-c", "/c", 4, "active", "")
    root.children = [a, b]
    a.children = [c]
    anc = _ancestors(root)                        # lead → worker-a → worker-c ; lead → worker-b
    assert anc["lead"] == set()
    assert anc["worker-a"] == {"lead"}
    assert anc["worker-c"] == {"lead", "worker-a"}
    assert anc["worker-b"] == {"lead"}
    # parent -> child: green
    assert _arrow_style("lead", "worker-a", anc) == ARROW_DOWN
    assert _arrow_style("lead", "worker-c", anc) == ARROW_DOWN
    assert _arrow_style("worker-a", "worker-c", anc) == ARROW_DOWN
    # child -> parent (reply): orange
    assert _arrow_style("worker-a", "lead", anc) == ARROW_UP
    assert _arrow_style("worker-c", "worker-a", anc) == ARROW_UP
    assert _arrow_style("worker-c", "lead", anc) == ARROW_UP
    # siblings / unrelated: cyan (neither green nor orange)
    assert _arrow_style("worker-a", "worker-b", anc) == "cyan"
    assert _arrow_style("worker-b", "worker-a", anc) == "cyan"
    assert _arrow_style("nobody", "lead", anc) == "cyan"


def test_log_arrow_colors_render_in_textual():
    """log-arrow-colors regression: the arrow colors must be parseable by Textual's renderer.
    ``orange`` is NOT a valid Textual/Rich color and crashed the MapApp log pane (map+log
    disappeared permanently on any child->parent reply, e.g. clicking a pi session)."""
    from rich.text import Text
    from textual.content import Content
    from textual.app import App as _App

    class _A(_App):
        enable_ansi = True

    app = _A()
    for color in (ARROW_DOWN, ARROW_UP, "cyan"):
        t = Text()
        t.append("src ⇢ dst", style=color)          # same path as MapApp._draw log lines
        Content.from_rich_text(t, console=app.console)  # would raise MissingStyle if invalid


def test_arrow_style_returns_rich_parseable_names():
    """The resolver must return the exact render-safe names (never the bare 'orange')."""
    anc = _ancestors(_arrow_tree())
    assert _arrow_style("lead", "worker-a", anc) == "green"     # parent -> child
    assert _arrow_style("worker-a", "lead", anc) == "orange1"   # child -> parent (reply)
    assert _arrow_style("worker-a", "nobody", anc) == "cyan"


def _arrow_tree():
    root = Node("1", "lead", "/r", 1, "active", "")
    root.children = [Node("2", "worker-a", "/a", 2, "waiting", "")]
    return root
