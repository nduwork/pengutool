"""Regression coverage for shared-server session identity and view ownership."""
import json

import pytest

from pengupool import ctl, model, tmux


@pytest.fixture
def external_session(monkeypatch):
    monkeypatch.setattr(ctl, "_index", lambda: ({"sid": "%0"}, {
        "sid": {"sessionId": "sid", "name": "worker", "cwd": "/repo", "pid": 4242},
    }))
    monkeypatch.setattr(tmux, "pane_exists", lambda pane, h="cc": pane == "%0")
    monkeypatch.setattr(tmux, "pane_owns", lambda pane, pid, h="cc": False)
    monkeypatch.setattr(tmux, "pane_for_pid", lambda pid, h="cc": "")
    monkeypatch.setattr(tmux, "pane_resuming", lambda sid, h="cc": "")


def test_adopt_keeps_new_session_running_until_it_has_a_transcript(external_session, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tmux, "reserve_window", lambda *args, **k: "%9")
    monkeypatch.setattr(tmux, "stop", lambda *args, **k: pytest.fail("stopped a session with no transcript"))
    monkeypatch.setattr(model, "CLAUDE", tmp_path)
    assert ctl.main(["adopt", "sid"]) == 1
    assert "Send a prompt" in capsys.readouterr().err


def test_attach_rejects_other_servers_pane(external_session, monkeypatch):
    monkeypatch.setattr(tmux, "view_command", lambda *args, **k: pytest.fail("viewed unrelated pane"))
    assert ctl.main(["attach", "sid", "worker"]) == 2


def test_close_does_not_kill_other_servers_pane(external_session, monkeypatch):
    calls = []
    monkeypatch.setattr(tmux, "kill", lambda pane, pid, h="cc": calls.append((pane, pid)) or True)
    assert ctl.main(["close", "sid"]) == 0
    assert calls == [("", 4242)]


def test_attach_discovers_owned_pane(external_session, monkeypatch, capsys):
    monkeypatch.setattr(tmux, "pane_for_pid", lambda pid, h="cc": "%9")
    monkeypatch.setattr(tmux, "view_command", lambda pane, name, *a: f"VIEW {pane}")
    assert ctl.main(["attach", "sid", "worker"]) == 0
    assert capsys.readouterr().out.strip() == "VIEW %9"


def test_resume_rejects_live_external_session(external_session, monkeypatch):
    monkeypatch.setattr(tmux, "new_window", lambda *args, **k: pytest.fail("duplicated live session"))
    assert ctl.main(["resume", "/repo", "worker", "sid"]) == 2


def test_resume_attaches_live_hosted_session(external_session, monkeypatch, capsys):
    monkeypatch.setattr(tmux, "pane_owns", lambda pane, pid, h="cc": True)
    monkeypatch.setattr(tmux, "new_window", lambda *args, **k: pytest.fail("restarted live session"))
    monkeypatch.setattr(tmux, "view_command", lambda pane, name, *a: f"VIEW {pane}")
    assert ctl.main(["resume", "/repo", "worker", "sid"]) == 0
    assert capsys.readouterr().out.strip() == "VIEW %0"


def test_new_metadata_uses_actual_worktree(monkeypatch, capsys):
    monkeypatch.setattr(tmux, "worktree_add", lambda *args, **k: "/repo-wt-worker")
    monkeypatch.setattr(tmux, "new_window", lambda *args, **k: "%9")
    monkeypatch.setattr(tmux, "view_command", lambda *args, **k: "VIEW %9")
    assert ctl.main(["--json", "new", "/repo", "worker"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"command": "VIEW %9", "pane": "%9", "cwd": "/repo-wt-worker", "harness": "cc"}


def test_adopt_reuses_hosted_session(external_session, monkeypatch, capsys):
    monkeypatch.setattr(tmux, "pane_owns", lambda pane, pid, h="cc": True)
    monkeypatch.setattr(tmux, "stop", lambda *args, **k: pytest.fail("stopped hosted session"))
    monkeypatch.setattr(tmux, "view_command", lambda pane, name, *a: f"VIEW {pane}")
    assert ctl.main(["adopt", "sid"]) == 0
    assert capsys.readouterr().out.strip() == "VIEW %0"


def test_adopt_does_not_resume_after_stop_failure(external_session, monkeypatch):
    monkeypatch.setattr(model, "resumable_transcript", lambda sid, cwd, h="cc": True)
    monkeypatch.setattr(tmux, "reserve_window", lambda *args, **k: "%9")
    monkeypatch.setattr(tmux, "stop", lambda *args, **k: False)
    monkeypatch.setattr(tmux, "start_reserved", lambda *args, **k: pytest.fail("duplicated live session"))
    closed = []
    monkeypatch.setattr(tmux, "kill_reserved", lambda pane, h="cc": closed.append(pane))
    assert ctl.main(["adopt", "sid"]) == 1
    assert closed == ["%9"]


def test_adopt_does_not_stop_if_window_cannot_be_reserved(external_session, monkeypatch, capsys):
    monkeypatch.setattr(model, "resumable_transcript", lambda sid, cwd, h="cc": True)
    monkeypatch.setattr(tmux, "reserve_window", lambda *args, **k: "")
    monkeypatch.setattr(tmux, "stop", lambda *args, **k: pytest.fail("stopped session without a tmux window"))
    assert ctl.main(["adopt", "sid"]) == 1
    assert "could not prepare a tmux window" in capsys.readouterr().err


def test_resume_dead_session_and_reuse_starting_pane(monkeypatch, capsys):
    monkeypatch.setattr(ctl, "_index", lambda: ({}, {}))
    monkeypatch.setattr(tmux, "pane_resuming", lambda sid, h="cc": "")
    started = []
    monkeypatch.setattr(tmux, "resume_window", lambda *args, **k: started.append(args) or "%9")
    monkeypatch.setattr(tmux, "view_command", lambda pane, name, *a: f"VIEW {pane}")
    assert ctl.main(["resume", "/repo", "worker", "sid"]) == 0
    assert started == [("/repo", "worker", "sid", "cc")]
    monkeypatch.setattr(tmux, "pane_resuming", lambda sid, h="cc": "%9")
    assert ctl.main(["resume", "/repo", "worker", "sid"]) == 0
    assert len(started) == 1
    assert capsys.readouterr().out.splitlines() == ["VIEW %9", "VIEW %9"]


def test_extension_views_use_window_identity_not_display_name(monkeypatch):
    monkeypatch.setattr(tmux, "_run", lambda *args, **k: "@9\n" if "%9" in args else "@10\n")
    first = tmux.view_command("%9", "duplicate")
    renamed = tmux.view_command("%9", "renamed")
    second = tmux.view_command("%10", "duplicate")
    assert first == renamed
    assert "pv-ext-9" in first and "pv-ext-10" in second
    assert "set-option -u -t pv-ext-9 mouse" in first  # drop an older build's pin so the toggle governs
    assert "set-option -w -t pv-ext-9:@9 window-size latest" in first


def test_select_view_restores_automatic_window_sizing(monkeypatch):
    monkeypatch.setattr(tmux, "_run", lambda *args, **k:
                        "/dev/ttys052\tpv-ext-editor-1\n" if args[0] == "list-clients" else "@9\n")
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)

    assert tmux.select_view("%9", "editor-1")
    assert calls == [
        ("has-session", "-t", "=pv-ext-editor-1"),
        ("set-option", "-w", "-t", "pv-ext-editor-1:@9", "window-size", "latest"),
        ("select-window", "-t", "pv-ext-editor-1:@9"),
        ("switch-client", "-c", "/dev/ttys052", "-t", "=pv-ext-editor-1"),
    ]


def test_mouse_copy_flashes_the_hint_top_right(monkeypatch):
    monkeypatch.setattr(tmux, "_copy_ready", set())
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: True)
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)

    tmux.enable_mouse_copy()

    assert calls[0] == ("set-option", "-g", "set-clipboard", "on")
    assert ("set-option", "-g", "status-position", "top") in calls
    assert ("set-option", "-g", "status-left", "") in calls
    binds = [c for c in calls if c[0] == "bind-key"]
    for call, table in zip(binds, ("copy-mode", "copy-mode-vi"), strict=True):
        assert call == (
            "bind-key", "-T", table, "MouseDragEnd1Pane", "if-shell", "-F", tmux.MIN_SELECTION,
            "send-keys -X copy-pipe-and-cancel pbcopy ; set-option status on ; run-shell -b "
            "\"sleep 2; tmux -L pengupool set-option -t '#{session_name}' status off\"",
            "send-keys -X cancel",  # a tiny drag (a wobbly click) leaves the clipboard alone
        )


def test_copy_bindings_install_even_when_mouse_starts_off(monkeypatch):
    """`copy_on_drag: false` only changes the server's initial mouse option; the drag-copy binding is
    still installed so prefix+m can restore it without a restart."""
    monkeypatch.setattr(tmux, "_copy_ready", set())
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: False)
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)

    tmux.enable_mouse_copy()

    assert calls[0] == ("set-option", "-g", "set-clipboard", "on")  # OSC 52 still advertised
    assert any(c[0] == "bind-key" and "MouseDragEnd1Pane" in c for c in calls), "drag-copy stays installed"


def test_copy_on_drag_reads_the_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tmux.model, "PENGU", tmp_path)
    assert tmux.copy_on_drag() is True  # absent config keeps the tmux drag-copy default
    (tmp_path / "config.json").write_text('{"copy_on_drag": false}')
    assert tmux.copy_on_drag() is False
    (tmp_path / "config.json").write_text('{"copy_on_drag": "no"}')
    assert tmux.copy_on_drag() is True  # a non-bool falls back to the default


def test_tune_appends_the_clipboard_feature_once(monkeypatch):
    monkeypatch.setattr(tmux, "_tuned", set())
    monkeypatch.setattr(tmux, "_run", lambda *args, **k: "")
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: True)
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)

    tmux.tune("cc")
    tmux.tune("cc")  # the server keeps the appended feature between calls

    assert calls == [
        ("set-option", "-ga", "terminal-features", tmux.CLIPBOARD_FEATURE),
        ("set-option", "-g", "history-limit", str(tmux.HISTORY_LIMIT)),
    ]


def test_ensure_server_sets_mouse_from_config_only_when_creating(monkeypatch):
    """`copy_on_drag: false` starts a fresh server with mouse off (the only way the terminal can select)."""
    monkeypatch.setattr(tmux, "_tuned", set())
    monkeypatch.setattr(tmux, "_mouse_ready", set())
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: False)
    monkeypatch.setattr(tmux, "_run", lambda *args, **k: "*:clipboard")
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or args[0] != "has-session")

    assert tmux.ensure_server("cc") is True

    assert ("set-option", "-g", "mouse", "off") in calls


def test_ensure_server_does_not_reset_an_existing_mouse_toggle(monkeypatch):
    """A prefix+m toggle must survive the next CLI process: an existing server keeps its mouse value."""
    monkeypatch.setattr(tmux, "_tuned", set())
    monkeypatch.setattr(tmux, "_mouse_ready", set())
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: True)  # the config default is on...
    monkeypatch.setattr(tmux, "_run", lambda *args, **k: "*:clipboard")
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)  # has-session -> True

    assert tmux.ensure_server("cc") is True

    assert not any(call[:3] == ("set-option", "-g", "mouse") for call in calls), "...but it is not re-applied"


def test_mouse_option_follows_copy_on_drag(monkeypatch):
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: True)
    assert tmux.mouse_option() == "on"
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: False)
    assert tmux.mouse_option() == "off"


def test_tune_does_not_reappend_a_feature_already_present(monkeypatch):
    monkeypatch.setattr(tmux, "_tuned", set())
    monkeypatch.setattr(tmux, "copy_on_drag", lambda: True)
    # tmux's live format: the appended entry is stored as `*:clipboard`, without our leading comma.
    monkeypatch.setattr(tmux, "_run", lambda *args, **k:
                        "terminal-features[0] xterm*:clipboard:ccolour\n"
                        f"terminal-features[3] {tmux.CLIPBOARD_MARK}\n")
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)

    tmux.tune("cc")

    assert ("set-option", "-ga", "terminal-features", tmux.CLIPBOARD_FEATURE) not in calls
    assert ("set-option", "-g", "history-limit", str(tmux.HISTORY_LIMIT)) in calls


def test_mouse_toggle_binds_both_servers_once(monkeypatch):
    monkeypatch.setattr(tmux, "_mouse_ready", set())
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: calls.append(args) or True)

    tmux.mouse_toggle("cc")
    tmux.mouse_toggle("cc")

    assert len(calls) == 1
    assert calls[0][:5] == ("bind-key", "m", "set-option", "-g", "mouse")
    cmd = " ".join(calls[0])
    assert f"tmux -L {tmux.OUTER} set-option -g mouse" in cmd
    assert f"tmux -L {tmux.harness.SOCK['pi']} set-option -g mouse" in cmd  # other harness stays in step
    assert "display-message" in calls[0]


def test_one_extension_view_can_target_different_windows(monkeypatch):
    monkeypatch.setattr(tmux, "_run", lambda *args, **k: "" if args[0] == "list-sessions" else
                        ("@9\n" if "%9" in args else "@10\n"))
    first = tmux.view_command("%9", "first", "editor-1")
    second = tmux.view_command("%10", "second", "editor-1")
    assert "pv-ext-editor-1" in first
    assert "pv-ext-editor-1" in second
    assert "pv-ext-9" not in first and "pv-ext-10" not in second


def test_extension_view_cleanup_removes_only_detached_stale_views(monkeypatch):
    monkeypatch.setattr(tmux, "_run", lambda *args, **k:
                        "pengupool\t0\npv-ext-old\t0\npv-ext-current\t0\npv-ext-live\t1\npv-tui-x\t0\n")
    killed = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, **k: killed.append(args) or True)
    tmux.cleanup_extension_views("pv-ext-current")
    assert killed == [("kill-session", "-t", "=pv-ext-old")]


def test_stop_does_not_claim_success_on_someone_elses_pid(monkeypatch):
    def kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(tmux.os, "kill", kill)
    assert tmux.stop(4242) is False


def test_ps_runs_in_the_c_locale(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen.update(kw.get("env") or {})
        return type("R", (), {"stdout": ""})()
    monkeypatch.setattr(model.subprocess, "run", run)
    model.ProcTable().refresh()
    assert seen.get("LC_ALL") == "C"


def test_write_json_keeps_a_symlink_and_its_mode(tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}")
    real.chmod(0o600)
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    model.write_json(link, {"a": 1})
    assert link.is_symlink() and json.loads(real.read_text()) == {"a": 1}
    assert real.stat().st_mode & 0o777 == 0o600
