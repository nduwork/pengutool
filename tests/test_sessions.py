"""An active session's file is rewritten non-atomically by Claude Code, so a poll can read it
torn. load_sessions must keep the (alive) session from the last good parse, not drop it."""
import json

from pengupool import model


def test_new_session_needs_a_saved_transcript_before_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "CLAUDE", tmp_path)
    sid = "abcdef12-0000"
    cwd = "/repo"
    transcript = tmp_path / "projects" / model.slug(cwd) / f"{sid}.jsonl"
    assert not model.resumable_transcript(sid, cwd)
    transcript.parent.mkdir(parents=True)
    transcript.touch()
    assert not model.resumable_transcript(sid, cwd)
    transcript.write_text('{"type":"user"}\n')
    assert model.resumable_transcript(sid, cwd)


def test_torn_read_keeps_alive_session(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "CLAUDE", tmp_path)
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(model, "pid_alive", lambda *a, **k: True)
    f = tmp_path / "sessions" / "12345.json"
    f.write_text(json.dumps({"sessionId": "abcdef12-0000", "pid": 12345, "name": "sess-x", "cwd": "/x"}))

    assert [s["sessionId"] for s in model.load_sessions()] == ["abcdef12-0000"]

    f.write_text('{"sessionId": "abcdef12-')  # torn write mid-rewrite
    assert [s["sessionId"] for s in model.load_sessions()] == ["abcdef12-0000"]  # still there

    # once the process is gone, the session drops even from cache
    monkeypatch.setattr(model, "pid_alive", lambda *a, **k: False)
    assert model.load_sessions() == []


def test_permission_request_marks_blocked(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(model, "CLAUDE", tmp_path)
    monkeypatch.setattr(model, "AGENT_STATE", tmp_path / "agent-state")
    monkeypatch.setattr(model, "pid_alive", lambda *a, **k: True)
    (tmp_path / "sessions").mkdir()
    sid = "abcdef12-0000"
    (tmp_path / "sessions" / "12345.json").write_text(json.dumps(
        {"sessionId": sid, "pid": 12345, "name": "x", "cwd": "/x", "status": "busy",
         "updatedAt": int(time.time() * 1000)}))

    assert model.load_sessions()[0]["state"] == "active"          # busy + fresh -> active
    model.write_json(model.AGENT_STATE / f"{sid}.json", {"event": "PermissionRequest"})
    assert model.load_sessions()[0]["state"] == "blocked"          # blocked on a permission prompt
    model.write_json(model.AGENT_STATE / f"{sid}.json", {"event": "PostToolUse"})
    assert model.load_sessions()[0]["state"] == "active"           # a later event clears it
