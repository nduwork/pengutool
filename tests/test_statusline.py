"""The Claude statusLine wrapper saves ctx % like the pi extension and keeps the renderer's output."""
import json
import subprocess
import sys

from pengupool import model

SID = "abcdef12-3456-7890-abcd-ef1234567890"


def run(tmp_path, payload, *args):
    return subprocess.run([sys.executable, "-m", "pengupool.statusline", *args], input=json.dumps(payload),
                          capture_output=True, text=True, env={"PENGUPOOL_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})


def test_wrapper_saves_claudes_percentage_and_passes_the_inner_line_through(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "PENGU", tmp_path)
    payload = {"session_id": SID, "context_window": {"used_percentage": 37.46}}
    result = run(tmp_path, payload, "--", "python3 -c 'import sys,json; print(json.load(sys.stdin)[\"session_id\"])'")
    assert result.stdout == SID + "\n"  # the inner command gets the same JSON on stdin
    assert model.load_context_pct(SID) == 37.5


def test_standalone_prints_nothing_and_skips_unusable_input(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "PENGU", tmp_path)
    assert run(tmp_path, {"session_id": SID, "context_window": {
        "context_window_size": 200_000, "current_usage": {"input_tokens": 50_000}}}).stdout == ""
    assert model.load_context_pct(SID) == 25.0
    assert run(tmp_path, {"session_id": "../x", "context_window": {"used_percentage": 5}}).returncode == 0
    assert run(tmp_path, {"session_id": SID}).returncode == 0
    assert model.load_context_pct(SID) == 25.0
