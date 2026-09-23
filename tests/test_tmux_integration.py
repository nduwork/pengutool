"""Disposable real tmux servers: never connect to the user's named sockets."""
import shutil
import subprocess
import tempfile
import time

import pytest

from pengupool import ctl, harness, model, tmux


@pytest.fixture
def servers(monkeypatch):
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    # Keep paths below the Unix socket length limit, including on macOS.
    directory = tempfile.TemporaryDirectory(prefix="pp-review-", dir="/tmp")
    sockets = [directory.name + "/shared", directory.name + "/external", directory.name + "/pi"]

    def run(socket, *args):
        return subprocess.run(["tmux", "-S", socket, *args], capture_output=True, text=True, timeout=5)

    try:
        for socket in sockets:
            result = run(socket, "-f", "/dev/null", "new-session", "-d", "-s", "pengupool", "sleep 120")
            if result.returncode or result.stderr:
                pytest.skip(f"cannot start disposable tmux server: {result.stderr.strip()}")
        by_harness = {"cc": sockets[0], "pi": sockets[2]}  # one disposable server per harness, like -L
        monkeypatch.setattr(tmux, "_user", lambda *args, h="cc": ["tmux", "-S", by_harness[h], *args])
        monkeypatch.setattr(tmux, "_CACHE", {})
        yield sockets, run
    finally:
        for socket in sockets:
            run(socket, "kill-server")
        directory.cleanup()


def test_cross_server_collision_cannot_attach_or_kill_unrelated_session(servers, monkeypatch):
    (shared, external, _), run = servers
    pid = int(run(external, "display-message", "-p", "-t", "%0", "#{pane_pid}").stdout)
    monkeypatch.setattr(ctl, "_index", lambda: ({"external": "%0"}, {"external": {"pid": pid}}))
    assert ctl.main(["attach", "external", "worker"]) == 2
    assert ctl.main(["close", "external"]) == 0
    assert run(shared, "has-session", "-t", "=pengupool").returncode == 0


@pytest.mark.parametrize("h, args", [("cc", ["--teammate-mode", "in-process", "--name", "worker", "--resume", "abcdef12-0000"]),
                                     ("pi", ["--session", "abcdef12-0000"])])
def test_adopt_resumes_with_its_harness_on_its_own_server(servers, monkeypatch, tmp_path, capsys, h, args):
    (shared, _, pi), run = servers
    sid = "abcdef12-0000"
    marker = tmp_path / "launch-args.txt"
    launcher = tmp_path / f"fake-{h}"
    launcher.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{marker}'\nsleep 120\n")
    launcher.chmod(0o755)
    monkeypatch.setitem(harness.CLI, h, str(launcher))
    outside = subprocess.Popen(["sleep", "120"])
    monkeypatch.setattr(ctl, "_index", lambda: ({}, {sid: {
        "sessionId": sid, "name": "worker", "cwd": str(tmp_path), "pid": outside.pid, "harness": h,
    }}))
    monkeypatch.setattr(model, "resumable_transcript", lambda *args, **k: True)
    monkeypatch.setattr(tmux, "view_command", lambda pane, name, *a: f"VIEW {pane}")
    try:
        assert ctl.main(["adopt", sid, ""]) == 0
        assert outside.wait(timeout=3) == -15
        for _ in range(20):
            if marker.exists():
                break
            time.sleep(0.1)
        assert marker.read_text().splitlines() == args
        pane = capsys.readouterr().out.strip().split()[-1]
        own, other = (shared, pi) if h == "cc" else (pi, shared)
        assert str(launcher) in run(own, "display-message", "-p", "-t", pane, "#{pane_start_command}").stdout
        assert str(launcher) not in run(other, "list-panes", "-a", "-F", "#{pane_start_command}").stdout
    finally:
        if outside.poll() is None:
            outside.terminate()
            outside.wait(timeout=3)


def test_tui_cleanup_leaves_extension_and_legacy_views(servers):
    (shared, _, _), run = servers
    for view in ("pv-ext-0", "pv-legacy", "pv-tui-pengupool"):
        assert run(shared, "new-session", "-d", "-s", view, "-t", "pengupool").returncode == 0
    tmux.kill_views()
    assert run(shared, "has-session", "-t", "=pv-ext-0").returncode == 0
    assert run(shared, "has-session", "-t", "=pv-legacy").returncode == 0
    assert run(shared, "has-session", "-t", "=pv-tui-pengupool").returncode != 0


def test_one_extension_view_switches_between_windows(servers):
    (shared, _, _), run = servers
    second = run(shared, "new-window", "-d", "-P", "-F", "#{pane_id}",
                 "-t", "pengupool:", "sleep 120").stdout.strip()
    run(shared, "new-session", "-d", "-s", "pv-ext-editor-1", "-t", "pengupool")
    assert tmux.select_view(second, "editor-1")
    selected = run(shared, "display-message", "-p", "-t", "pv-ext-editor-1", "#{window_id}").stdout.strip()
    target = run(shared, "display-message", "-p", "-t", second, "#{window_id}").stdout.strip()
    assert selected == target
