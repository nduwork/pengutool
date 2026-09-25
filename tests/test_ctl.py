"""`pengupool ctl` verbs: group round-trips through groups.json (never across harnesses),
worktree-add prints the path (or falls back to the dir on a non-repo), past emits JSON, and every
launch/resume/adopt runs its harness's CLI on that harness's tmux server."""
import json

import pytest

from pengupool import ctl, harness, model

PI_SID = "0192f7a1-1111-7000-8000-000000000001"


def test_group_set_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "GROUPS", tmp_path / "groups.json")
    monkeypatch.setattr(ctl.profiles, "caller", lambda: "")  # the user, from the editor
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "c1"}, {"sessionId": "p1"}])
    assert ctl.main(["group", "c1", "p1"]) == 0
    assert model.load_groups() == {"c1": "p1"}
    assert ctl.main(["group", "c1", ""]) == 0        # "" => top level, forget the parent
    assert model.load_groups() == {}


def test_group_refuses_to_mix_harnesses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(model, "GROUPS", tmp_path / "groups.json")
    monkeypatch.setattr(ctl.profiles, "caller", lambda: "")  # the user, from the editor
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "cc1", "harness": "cc"},
                                                         {"sessionId": "pi1", "harness": "pi"},
                                                         {"sessionId": "pi2", "harness": "pi"}])
    assert ctl.main(["group", "pi1", "cc1"]) == 2
    assert "harnesses never share a tree" in capsys.readouterr().err
    assert model.load_groups() == {}
    assert ctl.main(["group", "pi1", "pi2"]) == 0    # same harness is fine
    assert ctl.main(["group", "pi1", "pi1"]) == 2    # and nothing parents itself


def test_worktree_add_prints_path_and_falls_back(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pengupool.tmux.worktree_add", lambda d, n: "/wt/repo-wt-x")
    assert ctl.main(["worktree-add", str(tmp_path), "x"]) == 0
    assert capsys.readouterr().out.strip() == "/wt/repo-wt-x"

    monkeypatch.setattr("pengupool.tmux.worktree_add", lambda d, n: "")   # non-repo
    assert ctl.main(["worktree-add", str(tmp_path), "x"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path)               # falls back to the dir


def test_past_emits_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(model, "past_sessions", lambda d: [["id1", "a title", "cc"], ["id2", "b", "pi"]])
    assert ctl.main(["past", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == [["id1", "a title", "cc"], ["id2", "b", "pi"]]


def test_attach_missing_returns_2(monkeypatch):
    monkeypatch.setattr(model, "load_registry", lambda: {})
    monkeypatch.setattr(model, "load_sessions", lambda: [])
    assert ctl.main(["attach", "sid", "name"]) == 2   # 2 => extension may offer adopt instead


def test_attach_prints_view_command(monkeypatch, capsys):
    monkeypatch.setattr(model, "load_registry", lambda: {"sid": "%1"})
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "sid", "pid": 4242}])
    monkeypatch.setattr("pengupool.tmux.pane_owns", lambda pane, pid, h: pane == "%1" and pid == 4242)
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"ATTACH {pane} {name} {h}")
    assert ctl.main(["attach", "sid", "worker"]) == 0
    assert capsys.readouterr().out.strip() == "ATTACH %1 worker cc"


def test_attach_uses_the_sessions_own_harness_server(monkeypatch, capsys):
    monkeypatch.setattr(model, "load_registry", lambda: {"sid": "%1"})
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "sid", "pid": 4242, "harness": "pi"}])
    seen = []
    monkeypatch.setattr("pengupool.tmux.pane_owns", lambda pane, pid, h: seen.append(h) or True)
    monkeypatch.setattr("pengupool.tmux.view_command",
                        lambda pane, name, view, h: f"ATTACH {pane} {name} {view} {h}")
    assert ctl.main(["--json", "attach", "sid", "worker", "editor-1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "ATTACH %1 worker editor-1 pi" and out["harness"] == "pi"
    assert seen == ["pi"]


def test_select_switches_the_existing_extension_view(monkeypatch):
    monkeypatch.setattr(model, "load_registry", lambda: {"sid": "%1"})
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "sid", "pid": 4242}])
    monkeypatch.setattr("pengupool.tmux.pane_owns", lambda pane, pid, h: True)
    calls = []
    monkeypatch.setattr("pengupool.tmux.select_view",
                        lambda pane, view, h: calls.append((pane, view, h)) or True)
    assert ctl.main(["select", "sid", "editor-1"]) == 0
    assert calls == [("%1", "editor-1", "cc")]


def test_new_starts_and_prints_view(monkeypatch, capsys):
    monkeypatch.setattr("pengupool.tmux.worktree_add", lambda d, n: "/wt")
    monkeypatch.setattr("pengupool.tmux.new_window", lambda cwd, name, h: "%9")
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"VIEW {pane}")
    assert ctl.main(["new", "/dir", "x"]) == 0
    assert capsys.readouterr().out.strip() == "VIEW %9"


def test_new_starts_the_selected_harness(monkeypatch, capsys):
    monkeypatch.setattr("pengupool.tmux.worktree_add", lambda d, n: "/wt")
    calls = []
    monkeypatch.setattr("pengupool.tmux.new_window", lambda cwd, name, h: calls.append((cwd, name, h)) or "%9")
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"VIEW {pane} {view} {h}")
    assert ctl.main(["--json", "new", "/dir", "x", "editor-1", "pi"]) == 0
    assert calls == [("/wt", "x", "pi")]
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "VIEW %9 editor-1 pi" and out["harness"] == "pi"


def test_new_rejects_an_unknown_harness(monkeypatch, capsys):
    monkeypatch.setattr("pengupool.tmux.new_window", lambda *a: pytest.fail("must not start anything"))
    assert ctl.main(["new", "/dir", "x", "editor-1", "ollama launch claude --model qwen"]) == 1
    assert "unknown harness" in capsys.readouterr().err


def test_new_can_use_the_selected_folder_without_a_worktree(monkeypatch, capsys):
    monkeypatch.setattr("pengupool.tmux.worktree_add", lambda *args: pytest.fail("unexpected worktree"))
    calls = []
    monkeypatch.setattr("pengupool.tmux.new_window", lambda cwd, name, h: calls.append((cwd, name, h)) or "%9")
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"VIEW {pane} {view}")
    assert ctl.main(["--json", "new", "/dir", "x", "editor-1", "cc", "folder"]) == 0
    assert calls == [("/dir", "x", "cc")]
    assert json.loads(capsys.readouterr().out)["cwd"] == "/dir"


def test_new_rejects_an_unknown_session_location(monkeypatch, capsys):
    monkeypatch.setattr("pengupool.tmux.worktree_add", lambda *args: pytest.fail("unexpected worktree"))
    assert ctl.main(["new", "/dir", "x", "view", "cc", "somewhere"]) == 2
    assert "invalid session location" in capsys.readouterr().err


@pytest.mark.parametrize("h, argv, sock", [("cc", "claude --teammate-mode in-process --name worker", "pengupool"),
                                           ("pi", "pi --name worker", "pengupool-pi")])
def test_new_window_runs_the_harness_cli_on_its_own_server(monkeypatch, h, argv, sock):
    from pengupool import tmux
    calls = []
    monkeypatch.setattr(tmux, "ensure_server", lambda h: True)
    monkeypatch.setattr(tmux.subprocess, "run", lambda cmd, **k: calls.append(cmd) or
                        type("R", (), {"stdout": "%9\n", "returncode": 0})())
    assert tmux.new_window("/repo", "worker", h) == "%9"
    assert calls[0][:3] == ["tmux", "-L", sock]
    assert calls[0][-1] == argv


def test_reservation_rejects_a_pane_that_already_exited(monkeypatch):
    from pengupool import tmux
    monkeypatch.setattr(tmux, "_spawn", lambda *args: "%9")
    monkeypatch.setattr(tmux, "_run", lambda *args, **k: "")
    assert tmux.reserve_window("/repo", "worker") == ""


@pytest.mark.parametrize("h, command", [("cc", "claude --teammate-mode in-process --name worker --resume abcdef12-0000"),
                                        ("pi", "pi --session abcdef12-0000")])
def test_reserved_pane_resumes_with_its_harness(monkeypatch, h, command):
    from pengupool import tmux
    calls = []
    monkeypatch.setattr(tmux, "_ok", lambda *args, h: calls.append((args, h)) or True)
    assert tmux.start_reserved("%9", "/repo", "worker", "abcdef12-0000", h)
    assert calls == [(("respawn-pane", "-k", "-t", "%9", "-c", "/repo", command), h)]


def test_failed_resume_pane_is_not_mistaken_for_a_running_session(monkeypatch):
    from pengupool import tmux
    monkeypatch.setattr(tmux, "_run", lambda *args, h: "%9\t1\tfalse\tclaude --resume abcdef12-0000\n")
    assert tmux.pane_resuming("abcdef12-0000") == ""


def test_pane_resuming_matches_the_pi_cli(monkeypatch):
    from pengupool import tmux
    monkeypatch.setattr(tmux, "_run", lambda *args, h: "%9\t0\tnode\tpi --session abcdef12-0000\n")
    assert tmux.pane_resuming("abcdef12-0000", "pi") == "%9"


def test_resume_detects_a_past_pi_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(model, "load_registry", lambda: {})
    monkeypatch.setattr(model, "load_sessions", lambda: [])
    d = harness.pi_dir("/repo")
    d.mkdir(parents=True)
    (d / f"2026-09-22T10-00-00-000Z_{PI_SID}.jsonl").write_text('{"type":"session"}\n')
    calls = []
    monkeypatch.setattr("pengupool.tmux.pane_resuming", lambda sid, h: "")
    monkeypatch.setattr("pengupool.tmux.resume_window",
                        lambda cwd, name, sid, h: calls.append((cwd, name, sid, h)) or "%7")
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"VIEW {pane} {h}")
    assert ctl.main(["resume", "/repo", "worker", PI_SID]) == 0
    assert calls == [("/repo", "worker", PI_SID, "pi")]
    assert capsys.readouterr().out.strip() == "VIEW %7 pi"


@pytest.mark.parametrize("h", ["cc", "pi"])
def test_adopt_stops_then_resumes_with_its_harness(monkeypatch, capsys, h):
    monkeypatch.setattr(model, "load_registry", lambda: {})
    monkeypatch.setattr(model, "load_sessions",
                        lambda: [{"sessionId": "sid", "name": "worker", "cwd": "/repo", "pid": 4242, "harness": h}])
    monkeypatch.setattr("pengupool.tmux.pane_for_pid", lambda pid, h: "")
    monkeypatch.setattr("pengupool.tmux.pane_resuming", lambda sid, h: "")
    monkeypatch.setattr(model, "resumable_transcript", lambda sid, cwd, h: True)
    calls = []
    monkeypatch.setattr("pengupool.tmux.stop", lambda pid, **k: calls.append(("stop", pid)) or True)
    monkeypatch.setattr("pengupool.tmux.reserve_window", lambda cwd, name, h:
                        calls.append(("reserve", cwd, name, h)) or "%5")
    monkeypatch.setattr("pengupool.tmux.start_reserved", lambda pane, cwd, name, sid, h:
                        calls.append(("start", pane, cwd, name, sid, h)) or True)
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"VIEW {pane} {view}")
    assert ctl.main(["adopt", "sid", "editor-1"]) == 0
    assert calls == [("reserve", "/repo", "worker", h), ("stop", 4242),
                     ("start", "%5", "/repo", "worker", "sid", h)]
    assert capsys.readouterr().out.strip() == "VIEW %5 editor-1"


@pytest.mark.parametrize("h", ["cc", "pi"])
def test_restart_resumes_in_the_same_pane(monkeypatch, capsys, h):
    monkeypatch.setattr(model, "load_registry", lambda: {"sid": "%7"})
    monkeypatch.setattr(model, "load_sessions",
                        lambda: [{"sessionId": "sid", "name": "worker", "cwd": "/repo", "pid": 4242, "harness": h}])
    monkeypatch.setattr("pengupool.tmux.pane_owns", lambda pane, pid, h: pane == "%7")
    monkeypatch.setattr(model, "resumable_transcript", lambda sid, cwd, h: True)
    calls = []
    monkeypatch.setattr("pengupool.tmux.hold", lambda pane, h: calls.append(("hold", pane)) or True)
    monkeypatch.setattr("pengupool.tmux.stop", lambda pid, **k: calls.append(("stop", pid)) or True)
    monkeypatch.setattr("pengupool.tmux.start_reserved", lambda pane, cwd, name, sid, h:
                        calls.append(("start", pane, cwd, name, sid, h)) or True)
    monkeypatch.setattr("pengupool.tmux.view_command", lambda pane, name, view, h: f"VIEW {pane} {view}")
    assert ctl.main(["restart", "sid", "editor-1"]) == 0
    assert calls == [("hold", "%7"), ("stop", 4242), ("start", "%7", "/repo", "worker", "sid", h)]
    assert capsys.readouterr().out.strip() == "VIEW %7 editor-1"


def test_restart_refuses_a_session_without_a_transcript(monkeypatch):
    monkeypatch.setattr(model, "load_registry", lambda: {"sid": "%7"})
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "sid", "cwd": "/repo", "pid": 4242}])
    monkeypatch.setattr("pengupool.tmux.pane_owns", lambda pane, pid, h: True)
    monkeypatch.setattr(model, "resumable_transcript", lambda sid, cwd, h: False)
    monkeypatch.setattr("pengupool.tmux.stop", lambda pid, **k: pytest.fail("stopped a session it cannot resume"))
    assert ctl.main(["restart", "sid"]) == 1
    assert ctl.main(["restart", "gone"]) == 1


def test_adopt_missing_session(monkeypatch):
    monkeypatch.setattr(model, "load_registry", lambda: {})
    monkeypatch.setattr(model, "load_sessions", lambda: [])
    assert ctl.main(["adopt", "gone"]) == 1


def test_context_prints_the_sessions_block(monkeypatch, capsys):
    monkeypatch.setattr("pengupool.context.load_tree", lambda: {"sessions": {
        "p": {"name": "lead", "repo": "r", "state": "active", "harness": "pi", "parent": None, "children": ["kid"]},
        "k": {"name": "kid", "repo": "r", "state": "active", "harness": "pi", "parent": "lead", "children": []}}})
    assert ctl.main(["context", "k"]) == 0
    out = capsys.readouterr().out
    assert 'You are pi session "kid"' in out and "the intercom tool" in out and "SendMessage" not in out


def test_bad_verb_returns_nonzero(capsys):
    assert ctl.main(["nope"]) == 2
    assert ctl.main([]) == 2


def test_a_session_cannot_regroup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(model, "GROUPS", tmp_path / "groups.json")
    monkeypatch.setattr(model, "load_sessions", lambda: [{"sessionId": "c1"}, {"sessionId": "p1"}])
    monkeypatch.setattr(ctl.profiles, "caller", lambda: "c1")  # run from inside a session
    assert ctl.main(["group", "c1", "p1"]) == 2
    assert "sessions cannot regroup" in capsys.readouterr().err
    assert not (tmp_path / "groups.json").exists()


def test_resume_rejects_an_id_that_would_read_as_a_flag(capsys):
    assert ctl.main(["resume", "/repo", "w", "--dangerously-skip-permissions"]) == 2
    assert "invalid session id" in capsys.readouterr().err


def test_clear_logs_verb(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "CLEARED", tmp_path / "cleared.json")
    monkeypatch.setattr(model, "load_sessions", lambda *a, **k: [])
    assert ctl.main(["clear-logs"]) == 0
    assert (tmp_path / "cleared.json").exists()   # durable watermark written even with no live sessions


def test_slash_types_into_the_sessions_own_pane_and_only_for_the_user(monkeypatch, capsys):
    from pengupool import tmux
    typed = []
    monkeypatch.setattr(tmux, "slash", lambda pane, text, h="cc": typed.append((pane, text, h)) or True)
    sessions = {"c1": {"sessionId": "c1", "harness": "cc"}, "p1": {"sessionId": "p1", "harness": "pi"}}
    monkeypatch.setattr(ctl, "_index", lambda: ({}, sessions))
    monkeypatch.setattr(ctl, "_session_pane", lambda sid, reg, s: {"c1": "%1", "p1": "%2"}[sid])
    monkeypatch.setattr(ctl.profiles, "caller", lambda: "")
    assert ctl.main(["slash", "c1", "compact"]) == 0
    assert ctl.main(["slash", "c1", "rename", "api"]) == 0
    assert ctl.main(["slash", "p1", "rename", "docs"]) == 0
    assert typed == [("%1", "/compact", "cc"), ("%1", "/rename api", "cc"), ("%2", "/alias docs", "pi")]
    assert ctl.main(["slash", "c1", "rename", "a\nsecond prompt"]) == 2  # never a newline into the agent
    monkeypatch.setattr(ctl.profiles, "caller", lambda: "c1")          # from inside a session
    assert ctl.main(["slash", "p1", "compact"]) == 2
    assert len(typed) == 3
