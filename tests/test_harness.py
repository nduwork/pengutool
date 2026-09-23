"""pi sessions look like Claude ones to the model: discovery, transcripts, messages, chains and
past sessions. Harnesses never share a tree."""
import json
import os
import time

from pengupool import harness, model

SID = "0192f7a1-1111-7000-8000-000000000001"


def j(d) -> str:
    return json.dumps(d, separators=(",", ":"))  # both harnesses write compact JSONL


def test_pi_dir_matches_pi_session_manager():
    assert harness.pi_dir("/Users/a/my.repo").name == "--Users-a-my.repo--"   # "." kept, unlike Claude's slug
    assert harness.pi_dir("C:\\w\\x").name == "--C--w-x--"


def test_pi_live_sessions_are_discovered_and_tagged(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "CLAUDE", tmp_path / "claude")
    now = int(time.time() * 1000)
    model.write_json(model.PI_LIVE / f"{os.getpid()}.json",  # Claude-shaped, written by the pi extension
                     {"sessionId": SID, "pid": os.getpid(), "cwd": "/r", "name": "piw", "status": "idle",
                      "kind": "interactive", "updatedAt": now, "startedAt": now})
    [s] = model.load_sessions()
    assert (s["name"], s["harness"], s["state"]) == ("piw", "pi", "waiting")
    roots, _ = model.build_trees([s], [])
    assert roots[0].harness == "pi"


def test_intercom_entries_become_messages():
    sent = {"type": "custom", "customType": "intercom_sent", "timestamp": "2026-01-01T00:00:01Z",
            "data": {"to": "kid", "message": {"text": "please run the migration now thanks"}}}
    got = {"type": "custom", "customType": "intercom_received", "timestamp": "2026-01-01T00:00:02Z",
           "data": {"from": "lead", "message": {"text": "done"}}}
    [m] = model.parse_transcript_line(json.dumps(sent), "me")
    assert (m.src, m.dst, m.label, m.incoming) == ("me", "kid", "please run the migration now thanks", False)
    [m] = model.parse_transcript_line(json.dumps(got), "me")
    assert (m.src, m.dst, m.incoming) == ("lead", "me", True)


def test_pi_tool_result_attributes_its_chain():
    line = {"type": "message", "message": {"role": "toolResult", "toolName": "bash",
            "content": [{"type": "text", "text": "[feat] build ● → ship ○"}]}}
    assert model.chain_in_line(json.dumps(line)) == "feat"
    assert model.chain_in_line("[1, 2]") is None  # non-object JSON must not crash the tailer


def test_past_sessions_cover_both_harnesses(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "CLAUDE", tmp_path)
    cc = tmp_path / "projects" / model.slug("/r")
    cc.mkdir(parents=True)
    (cc / "cc-id.jsonl").write_text(j({"type": "user", "message": {"content": "fix the bug"}}) + "\n")
    pi = harness.pi_dir("/r")
    pi.mkdir(parents=True)
    (pi / f"2026-09-22T10-00-00-000Z_{SID}.jsonl").write_text(
        j({"type": "session", "id": SID, "cwd": "/r"}) + "\n" +
        j({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "hi pi"}]}}) + "\n" +
        j({"type": "session_info", "name": "pi worker"}) + "\n")
    got = sorted(model.past_sessions("/r"))
    assert got == [(SID, "pi worker", "pi"), ("cc-id", "fix the bug", "cc")]
    assert model.harness_of_past(SID, "/r") == "pi"
    assert model.harness_of_past("abcdef12-0000", "/r") == "cc"


def test_trees_never_mix_harnesses():
    sessions = [{"sessionId": "c", "name": "lead", "harness": "cc"},
                {"sessionId": "p", "name": "piw", "harness": "pi"},
                {"sessionId": "q", "name": "pik", "harness": "pi"}]
    groups = {"p": "c", "q": "p"}  # a hand-edited groups.json putting pi under Claude
    assert model.apply_groups(sessions, [("lead", "pik", "msg")], groups) == [("piw", "pik", "")]
    assert model.group_error("p", "c", sessions).endswith("harnesses never share a tree")
    assert model.group_error("q", "p", sessions) == ""
