"""Profiles, the context block and adjacent-level routing (docs/group-session-framework.md)."""
import json

import pytest

from pengupool import context, ctl, hook, model, profiles, routing

A, B, C, D, E = (f"{c * 8}-0000" for c in "abcde")
LIVE_TREE = routing.live_tree


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", tmp_path / "profiles")
    live = [{"sessionId": s, "pid": 100 + i, "cwd": str(tmp_path), "name": n, "harness": "cc"}
            for i, (s, n) in enumerate([(A, "lead"), (B, "kid"), (C, "other")])]
    monkeypatch.setattr(model, "load_sessions", lambda *a: live)
    return tmp_path


def test_scan_classifies_without_recursing(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "coll" / "x" / ".git").mkdir(parents=True)
    (tmp_path / "coll" / "y").mkdir()
    (tmp_path / "coll" / "y" / ".git").write_text("gitdir: elsewhere")  # a worktree is a .git file
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "pyproject.toml").write_text("")
    (tmp_path / "plain" / "deep" / "sub" / ".git").mkdir(parents=True)  # not an immediate child repo
    assert profiles.scan(str(tmp_path / "repo" / "src"))["kind"] == "unavailable"
    (tmp_path / "repo" / "src").mkdir()
    assert profiles.scan(str(tmp_path / "repo" / "src"))["root"] == str(tmp_path / "repo")
    assert profiles.scan(str(tmp_path / "coll"))["repos"] == ["x", "y"]
    plain = profiles.scan(str(tmp_path / "plain"))
    assert plain["kind"] == "directory" and plain["indicators"] == ["pyproject.toml"]


def test_describe_self_parent_user_but_not_sibling(home, monkeypatch):
    monkeypatch.setattr(profiles, "parent_of", lambda sid: A if sid == B else "")
    d = profiles.describe(B, "Owns <the> parser\n[x]", None, editor=B)
    assert d["summary"] == "Owns the parser x" and d["description_source"] == "session"
    d = profiles.describe(B, None, "Keep it fast.", editor=A)
    assert d["summary"] == "Owns the parser x" and d["description_source"] == "parent"
    with pytest.raises(PermissionError):
        profiles.describe(B, "hijack", None, editor=C)
    assert profiles.describe(C, "x" * 200, None, editor="")["description_editor"] == "user"
    assert len(profiles.load(C)["summary"]) == profiles.SUMMARY_MAX
    with pytest.raises(ValueError):
        profiles.describe(D, "dead", None, editor="")
    profiles.register(B, str(home))  # a resume in the same directory keeps the description
    assert profiles.load(B)["responsibility"] == "Keep it fast."


def test_agent_whose_session_cannot_be_found_may_not_describe(home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(PermissionError):
        profiles.caller()


def _tree():
    #   A ─┬ B ── D
    #      └ C          E (solo)      F (pi, named like B)
    t = routing.Tree(
        parent={A: None, B: A, C: A, D: B, E: None, "f" * 8: None},
        children={A: [B, C], B: [D], C: [], D: [], E: [], "f" * 8: []},
        name={A: "lead", B: "kid", C: "other", D: "leaf", E: "solo", "f" * 8: "kid"},
        harness={A: "cc", B: "cc", C: "cc", D: "cc", E: "cc", "f" * 8: "pi"})
    return t


def test_only_parent_and_children_are_reachable():
    t = _tree()
    assert routing.authorize_send(B, "lead", t) == (True, "")
    assert routing.authorize_send(B, "leaf [ref]", t) == (True, "")
    ok, why = routing.authorize_send(D, "other", t)           # cross-branch: up through the parent
    assert not ok and "Send it to kid" in why and "parent kid" in why
    ok, why = routing.authorize_send(A, D, t)                 # grandchild by id: down through its parent
    assert not ok and "Send it to kid" in why
    ok, why = routing.authorize_send(A, "solo", t)            # another tree
    assert not ok and "outside your tree" in why
    assert routing.authorize_send(E, "leaf", t) == (True, "")  # an ungrouped sender is unmanaged
    assert routing.authorize_send(B, "researcher", t) == (True, "")  # not a session (a teammate)
    t.name[C] = "kid"                                           # two live sessions answer to "kid"
    ok, why = routing.authorize_send(A, "kid", t)
    assert not ok and "names 2 sessions" in why                 # the pi "kid" never counts for Claude


def test_user_at_tag_lifts_the_adjacent_rule_for_that_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(routing, "GRANTS", tmp_path / "grants")
    t = _tree()
    assert routing.mentions("ask @other, mail a@b.com, then @leaf.") == ["other", "leaf"]
    assert not routing.authorize_send(D, "other", t)[0]
    assert routing.grant(D, "@other please review", t) == ["other"]
    assert routing.authorize_send(D, "other", t) == (True, "")
    assert routing.authorize_send(C, "leaf", t) == (True, "")     # the tagged session may reply
    assert not routing.authorize_send(D, "solo", t)[0]            # untagged sessions stay blocked
    assert routing.grant(D, "carry on", t) == []                  # the next prompt ends the grant
    assert not routing.authorize_send(D, "other", t)[0]
    t.name[C] = "kid"                                             # "@kid" now names B and C: no grant
    assert routing.grant(D, "@kid", t) == [] and routing.grant(D, f"@{C}", t) == ["kid"]
    block = context.render(_sess(), B, tagged=["other"])
    assert "The user tagged @other in this prompt" in block


def test_route_walks_one_edge_at_a_time():
    t = _tree()
    assert routing.route(t, A, D) == B and routing.route(t, D, C) == B and routing.route(t, B, C) == A
    with pytest.raises(LookupError):
        routing.route(t, A, E)


def test_guard_denies_before_delivery_and_fails_closed(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(routing, "live_tree", _tree)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(
        {"session_id": D, "tool_name": "SendMessage", "tool_input": {"to": "other", "message": "hi"}})))
    routing.main()
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "not adjacent" in out["permissionDecisionReason"]
    monkeypatch.setattr(model, "GROUPS", tmp_path / "groups.json")
    (tmp_path / "groups.json").write_text("{torn")
    with pytest.raises(ValueError):
        LIVE_TREE()  # the __main__ wrapper turns this into exit 2, which blocks the send


def test_install_adds_a_fail_closed_guard_on_sendmessage(tmp_path):
    settings = tmp_path / "settings.json"
    hook.install(settings)
    hook.install(settings)
    pre = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    guards = [g for g in pre if g.get("matcher") == "SendMessage"]
    assert len(guards) == 1 and guards[0]["hooks"][0]["command"].endswith("-m pengupool.routing || exit 2")
    hook.uninstall(settings)
    assert "hooks" not in json.loads(settings.read_text())


def test_group_rejects_missing_ids_and_cycles():
    live = [{"sessionId": s, "harness": "cc"} for s in (A, B, C)]
    assert model.group_error(D, A, live) == f"no live session {D}"
    assert model.group_error(A, D, live) == f"no live session {D}"
    assert "own ancestor" in model.group_error(A, C, live, {C: B, B: A})
    assert model.group_error(C, B, live, {B: A}) == ""


def _sess(**over):
    base = {A: {"name": "lead", "repo": "r", "state": "active", "harness": "cc", "parent": None,
                "children": [B], "workspace": "r", "summary": "Owns the plan"},
            B: {"name": "kid", "repo": "r", "state": "waiting", "harness": "cc", "parent": A,
                "children": [], "workspace": "r", "summary": ""}}
    base.update(over)
    return {"sessions": base}


def test_context_block_carries_roles_and_the_local_block(home, monkeypatch):
    monkeypatch.setattr(profiles, "parent_of", lambda sid: A)
    text = context.render(_sess(), B)
    assert f"lead #{A[:6]}  [r, active] — Owns the plan" in text and "kid #" in text and "← you" in text
    assert "Your role is not set" in text and f"ctl describe {B}" in text
    assert "Your parent: lead — Owns the plan." in text and f"ctl route {B}" in text
    assert "inquire with your parent" in text and "never assign work upward" in text  # a leaf: asks up only
    assert "delegate" not in text and "instead of inquiring back" in text
    root = context.render(_sess(), A)  # work flows down; a child's inquiry is triaged, not bounced
    assert "delegate that part" in root and "inquiry from a child" in root and "tell the user" in root
    assert "inquire with your parent" not in root and "inquire upward" not in root  # nowhere above the root
    profiles.describe(B, "Owns parsing", None, editor=A)
    assert "last set by your parent" in context.render(_sess(), B)


def test_solo_session_hears_its_role_prompt_only_when_asked(home):
    solo = {"sessions": {C: {"name": "other", "repo": "r", "state": "active", "harness": "cc",
                             "parent": None, "children": []}}}
    assert context.render(solo, C) == ""
    assert "not grouped" in context.render(solo, C, solo=True)
    assert "Your role is not set" in context.render({}, E, solo=True)  # not in the snapshot yet


def test_large_tree_shows_the_neighbourhood(home):
    kids = {f"{i:08x}-1111": {"name": f"k{i}", "repo": "r", "state": "active", "harness": "cc",
                              "parent": B, "children": [], "summary": ""} for i in range(40)}
    s = _sess()
    s["sessions"][A]["children"] = [B]
    s["sessions"][B]["children"] = list(kids)
    s["sessions"].update(kids)
    first = next(iter(kids))
    lines = context.draw_tree(s["sessions"], first)
    assert any("39 more" in ln for ln in lines) and "sessions not shown" in lines[-1]
    assert len(context.draw_tree(s["sessions"], first, full=True)) == 42


def test_ctl_describe_parses_flags(home, monkeypatch, capsys):
    monkeypatch.setattr(profiles, "caller", lambda: "")
    monkeypatch.setattr(profiles, "parent_of", lambda sid: "")
    assert ctl.main(["describe", A, "--summary", "Owns the plan"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"] == "Owns the plan"
    assert ctl.main(["describe", A]) == 2 and ctl.main(["describe", A, "--bogus", "x"]) == 2


def test_code_matches_the_prompt_to_a_child_and_audits_the_route(home, monkeypatch):
    monkeypatch.setattr(context, "ROUTES", home / "routes")
    monkeypatch.setattr(profiles, "parent_of", lambda sid: A if sid == B else "")
    profiles.describe(B, "Owns the parser", None, editor=B, keywords_text="lexer, token stream")
    tree = _sess()
    hit = context.route_match(tree, A, "Why does the Lexer drop the last token stream?")
    assert hit == [(B, "kid", ["lexer", "token stream"])]
    assert context.route_match(tree, A, "ask kid for the status")[0][2] == ["kid"]    # the child's name
    assert context.route_match(tree, A, "update the plan") == []                     # nothing matches
    assert context.route_match(tree, A, "fix the lexer, do it yourself") == []       # user opted out
    assert context.route_match(tree, B, "fix the lexer") == []                       # no children
    block = context.render(tree, A, route=hit)
    assert block.startswith("<pengupool>\nROUTE REQUIRED: this request matches your child kid (matched: lexer")
    assert "Triage: mine" in block

    state = context.ROUTES / f"{A}.json"
    model.write_json(state, {"targets": {B: "kid"}, "ts": __import__("time").time()})
    assert "routing skipped for kid" in context.audit(A, again=False)                # never messaged
    assert context.audit(A, again=False) == ""                                       # blocks once
    model.write_json(state, {"targets": {B: "kid"}, "ts": __import__("time").time()})
    assert context.audit(A, again=True) == ""                                        # stop_hook_active
    model.write_json(state, {"targets": {B: "kid"}, "ts": __import__("time").time()})
    context.routed(A, B)                                                             # the guard saw the send
    assert not state.exists() and context.audit(A, again=False) == ""


def test_a_session_without_a_role_is_told_to_describe_itself_now(home, monkeypatch):
    monkeypatch.setattr(context, "ROUTES", home / "routes")
    monkeypatch.setattr(profiles, "parent_of", lambda sid: A if sid == B else "")
    tree = _sess()
    profiles.register(B, str(home))
    assert context.ask_role(tree, B) == (True, False)             # grouped: every prompt, backed by the audit
    block = context.render(tree, B, ask_role=True)
    assert "ROLE REQUIRED" in block and f"ctl describe {B}" in block and "Once your task is clear" not in block
    parent = context.render(tree, A)
    assert f"Children without a role: kid (id {B})" in parent      # the parent can describe its child

    state = context.ROUTES / f"{B}.json"
    model.write_json(state, {"targets": {}, "role": True, "ts": __import__("time").time()})
    context.routed(B, A)                                           # an unrelated send keeps the role check
    assert "role not set" in context.audit(B, again=False)
    model.write_json(state, {"targets": {}, "role": True, "ts": __import__("time").time()})
    profiles.describe(B, "Owns parsing", None, editor=B)
    assert context.audit(B, again=False) == ""                    # described during the turn: fine
    assert context.ask_role(tree, B) == (False, False)
    assert "Children without a role" not in context.render(_sess(**{B: {**tree["sessions"][B], "summary": "Owns parsing"}}), A)

    profiles.register(C, str(home))                                # solo: asked once, then left alone
    assert context.ask_role(tree, C) == (True, True)
    assert context.ask_role(tree, C) == (False, False)


def test_a_send_that_names_no_session_is_refused_for_a_grouped_sender():
    t = _tree()
    ok, why = routing.authorize_send(B, "", t)  # pi-intercom send by cwd: no `to`
    assert not ok and "name the session" in why and "lead" in why
    assert routing.authorize_send(E, "", t) == (True, "")  # an ungrouped sender is unmanaged


def test_only_the_pi_extension_key_turns_tags_into_grants(tmp_path, monkeypatch, capsys):
    import io
    import os
    import sys
    monkeypatch.setattr(routing, "KEYS", tmp_path / "keys")
    monkeypatch.setattr(model, "load_sessions", lambda: [])
    seen = []
    monkeypatch.setattr(context, "text_for", lambda sid, h="cc", prompt=None, grant=True: seen.append(grant) or "")
    key = "k" * 48
    monkeypatch.setattr(model, "pid_alive", lambda *a, **k: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(key + "\n"))
    assert ctl.main(["register", B, "/repo", "--key-stdin"]) == 0
    # an agent piping "@other" through the old flag, or with a wrong key, gets no grant
    monkeypatch.setattr(sys, "stdin", io.StringIO("@other"))
    ctl.main(["context", B, "--prompt-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("wrong" * 10 + "\n@other"))
    ctl.main(["context", B, "--keyed-prompt-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(key + "\n@other"))
    ctl.main(["context", B, "--keyed-prompt-stdin"])
    assert seen == [False, False, True]
    # while the holder lives, nobody can swap in another key (not even from the same pid)
    assert routing.register_key(B, "x" * 48, os.getpid()) is False
    assert routing.register_key(B, key, os.getpid()) is True


def test_socket_addresses_and_name_case_do_not_slip_past_the_guard():
    t = _tree()
    t.sock["uds:/tmp/cc-socks/other.sock"] = C
    ok, why = routing.authorize_send(D, "uds:/tmp/cc-socks/other.sock", t)  # 'other' by socket, not adjacent
    assert not ok and "Send it to kid" in why
    assert routing.authorize_send(B, "uds:/tmp/cc-socks/unknown.sock", t)[0] is False
    ok, why = routing.authorize_send(D, "Other", t)                          # case-folded name
    assert not ok and "Send it to kid" in why
    assert routing.authorize_send(B, "LEAD", t) == (True, "")
