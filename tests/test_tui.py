"""The terminal UI is launched by `pengupool` (no command) on a private tmux server. These check the
launcher's wiring without a real tmux: it must build the UI server, remember the pane ids, and attach
the user to it. The pane apps themselves are Textual widgets rendered by tmux, exercised in the editor
by hand."""
import json

from pengupool import app


def test_launch_builds_the_ui_server_and_attaches(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    execs: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(app, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(app, "UI", tmp_path / "ui.json")
    monkeypatch.setattr(app.shutil, "get_terminal_size", lambda *a: (160, 45))
    monkeypatch.setattr(app.tmux, "kill_views", lambda: calls.append(("kill_views",)))
    monkeypatch.setattr(app.tmux, "ensure_server", lambda h: calls.append(("ensure_server", h)) or True)
    monkeypatch.setattr(app.tmux, "outer", lambda *a: calls.append(("outer", *a)) or "")
    monkeypatch.setattr(app.tmux, "outer_ok", lambda *a: calls.append(("outer_ok", *a)) or True)
    monkeypatch.setattr(app.os, "execvpe", lambda prog, argv, env: execs.append((prog, argv)))

    app.launch()

    flat = [c[0] for c in calls]
    assert "kill_views" in flat and "ensure_server" in flat
    assert any(c[:3] == ("outer_ok", "-f", "/dev/null") for c in calls), "creates the isolated UI server"
    assert any(c[:2] == ("outer_ok", "split-window") for c in calls), "splits the map and agent panes"
    # the launcher points the user's terminal at the private UI server
    assert execs and execs[0][1][:5] == ["tmux", "-L", app.tmux.OUTER, "attach-session", "-t"]
    ui = json.loads((tmp_path / "ui.json").read_text())
    assert ui.get("terms"), "records the nested agent-client ttys for the list pane"
