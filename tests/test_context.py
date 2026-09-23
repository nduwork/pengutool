"""Context on the map comes from Claude's status-line payload, not transcript estimates."""
import json
import os
import subprocess
import time
from pathlib import Path

from pengupool import model


SID = "abcdef12-0000"
STATUSLINE = Path(__file__).resolve().parents[1] / "workflow-tracker" / "scripts" / "statusline.sh"


def feed_statusline(tmp_path, payload):
    env = {**os.environ, "PENGUPOOL_HOME": str(tmp_path)}
    return subprocess.run(["bash", str(STATUSLINE), "--", "printf ccstatusline"],
                          input=json.dumps(payload), text=True, capture_output=True, env=env, check=True)


def test_statusline_captures_claudes_used_percentage_and_preserves_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "PENGU", tmp_path)
    result = feed_statusline(tmp_path, {"session_id": SID, "context_window": {
        "used_percentage": 12.3, "context_window_size": 1_000_000,
        "current_usage": {"input_tokens": 100_000},
    }})
    assert result.stdout == "ccstatusline\n"
    assert model.load_context_pct(SID) == 12.3  # Claude's percentage wins over token arithmetic


def test_statusline_uses_explicit_window_size_when_percentage_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "PENGU", tmp_path)
    feed_statusline(tmp_path, {"session_id": SID, "context_window": {
        "context_window_size": 1_000_000,
        "current_usage": {"input_tokens": 2, "output_tokens": 1104,
                          "cache_creation_input_tokens": 1809, "cache_read_input_tokens": 215694},
    }})
    assert model.load_context_pct(SID) == 21.9


def test_unknown_or_stale_context_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "PENGU", tmp_path)
    feed_statusline(tmp_path, {"session_id": SID, "context_window": {
        "context_window_size": 1_000_000, "current_usage": {"input_tokens": "unknown"},
    }})
    assert model.load_context_pct(SID) is None
    feed_statusline(tmp_path, {"session_id": SID, "context_window": {
        "current_usage": {"input_tokens": 100_000},
    }})
    assert model.load_context_pct(SID) is None  # no window size, no guess
    model.write_json(tmp_path / "context" / f"{SID}.json", {"pct": 40.0, "ts": time.time() - 301})
    assert model.load_context_pct(SID) is None
    assert model.load_context_pct("../unsafe") is None
