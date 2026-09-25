"""`clear logs` (model.clear_logs / Transcripts.clear): wiping the cross-session message log must be
durable — a fresh scanner (another `serve`/`ctl` process, or a restart) must not re-emit the wiped
history, while genuinely new messages still get logged."""
import json

from pengupool import model


def _send(path, ts="2000-01-01T00:00:00Z", src="root", dst="child", label="hello"):
    line = json.dumps({"timestamp": ts, "type": "assistant",
                       "message": {"content": [{"type": "tool_use", "name": "SendMessage",
                                                 "input": {"to": dst, "summary": label}}]}})
    with open(path, "a") as fh:
        fh.write(line + "\n")


def _setup(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    pengu = tmp_path / "pengu"
    monkeypatch.setattr(model, "CLAUDE", claude)
    monkeypatch.setattr(model, "PENGU", pengu)
    monkeypatch.setattr(model, "CLEARED", pengu / "cleared.json")
    path = claude / "projects" / model.slug("/repo") / "root-id.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_clear_logs_durable_across_processes(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    sessions = [{"sessionId": "root-id", "cwd": "/repo", "name": "root", "harness": "cc"}]

    _send(path)                                   # one message ("hello")
    t = model.Transcripts(tail_bytes=1_048_576)
    assert [m.label for m in t.scan(sessions)] == ["hello"]

    t.clear()                                     # wipe it
    assert t.msgs == []
    watermark = model._json(model.CLEARED)
    assert watermark and watermark[str(path)] > 0

    t2 = model.Transcripts(tail_bytes=1_048_576)  # a brand-new scanner honours the durable watermark
    assert t2.scan(sessions) == []                # old history stays gone

    _send(path, ts="2000-01-01T00:01:00Z", label="new")   # ... but a message sent after the clear is shown
    assert [m.label for m in t2.scan(sessions)] == ["new"]


def test_clear_logs_same_process_does_not_resurface(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    sessions = [{"sessionId": "root-id", "cwd": "/repo", "name": "root", "harness": "cc"}]

    _send(path)
    t = model.Transcripts(tail_bytes=1_048_576)
    t.scan(sessions)
    t.clear()

    assert t.scan(sessions) == []                 # the same instance keeps it empty until a new message
    _send(path, ts="2000-01-01T00:00:05Z", label="after")
    assert [m.label for m in t.scan(sessions)] == ["after"]


def test_clear_from_fresh_process_drops_collected_messages(tmp_path, monkeypatch):
    """The extension 'clear' path runs `pengupool ctl clear-logs` in a brand-new process. It has no
    in-memory offsets, so it must watermark live transcripts straight from disk — and a concurrent
    serving process must drop messages it already collected when it next scans."""
    path = _setup(tmp_path, monkeypatch)
    sessions = [{"sessionId": "root-id", "cwd": "/repo", "name": "root", "harness": "cc"}]
    _send(path)

    server = model.Transcripts(tail_bytes=1_048_576)          # e.g. `serve`, already collected it
    assert [m.label for m in server.scan(sessions)] == ["hello"]

    fresh = model.Transcripts(tail_bytes=1_048_576)           # e.g. `ctl clear-logs`, no offsets
    monkeypatch.setattr(model, "load_sessions", lambda *a, **k: [sessions[0]])
    fresh.clear()
    assert model._json(model.CLEARED)[str(path)] > 0            # watermark written to disk

    # a serving process drops its already-collected history on the next scan from the cross-process clear
    assert server.scan(sessions) == []
    _send(path, ts="2000-01-01T00:00:05Z", label="new")
    assert [m.label for m in server.scan(sessions)] == ["new"]
