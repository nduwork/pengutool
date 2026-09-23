"""Render robustness: A1 a sole session follows the directory's current chain (no stale pin);
A2 a linked git worktree reads the MAIN repo root's .step-status so it shows the repo's chain."""
from pengupool import model


def test_status_dir_resolves_worktree_to_main_root(tmp_path):
    model._STATUS_DIR_CACHE.clear()
    main = tmp_path / "repo"
    (main / ".git").mkdir(parents=True)                 # main worktree: .git is a directory
    ss = main / ".step-status"
    ss.mkdir()
    (ss / "current").write_text("build")
    (ss / "build.state").write_text("done\tcompile\t\nactive\ttest\t\n")

    wt = tmp_path / "repo-wt-x"                          # linked worktree: .git is a FILE
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/x\n")

    assert model.status_dir(str(main)) == ss                              # main -> its own
    assert model.status_dir(str(wt)) == ss                               # worktree -> main root's
    assert model.read_status(str(wt)) == model.read_status(str(main))    # same rendered chain
    assert "compile" in model.read_status(str(wt)) and "[build]" in model.read_status(str(wt))

    plain = tmp_path / "not-a-repo"                      # no .git -> its own .step-status
    plain.mkdir()
    assert model.status_dir(str(plain)) == plain / ".step-status"


def test_snapshot_sole_session_follows_current(tmp_path, monkeypatch):
    model._STATUS_DIR_CACHE.clear()
    monkeypatch.setattr(model, "CLAUDE", tmp_path)       # no teams
    monkeypatch.setattr(model, "PENGU", tmp_path)        # no registry/groups
    monkeypatch.setattr(model, "GROUPS", tmp_path / "groups.json")
    cwd = tmp_path / "repo"
    (cwd / ".git").mkdir(parents=True)
    ss = cwd / ".step-status"
    ss.mkdir()
    (ss / "current").write_text("live")
    (ss / "live.state").write_text("active\tplanning\t\n")
    (ss / "pinned.state").write_text("done\tstale\t\n")

    sid = "abcdef12-0000"
    sess = {"sessionId": sid, "name": "solo", "cwd": str(cwd), "pid": 1, "state": "active"}
    monkeypatch.setattr(model, "load_sessions", lambda *a, **k: [sess])

    class FakeT:  # the session is pinned to a DIFFERENT chain than `current`
        chains = {sid: "pinned"}
        ctx: dict = {}

        def scan(self, sessions):
            return []
    monkeypatch.setattr(model, "TRANSCRIPTS", FakeT())

    roots, _cross, _msgs = model.snapshot()
    # sole session in the cwd must follow `current` (live), not its stale pinned chain
    assert roots[0].status_line == "[live] planning ●"


def test_map_snapshots_refresh_chain_and_statusline_context(tmp_path, monkeypatch):
    import json
    import time
    from pengupool import serve
    model._STATUS_DIR_CACHE.clear()
    monkeypatch.setattr(model, 'CLAUDE', tmp_path)
    monkeypatch.setattr(model, 'PENGU', tmp_path)
    monkeypatch.setattr(model, 'GROUPS', tmp_path / 'groups.json')
    cwd = tmp_path / 'repo'
    (cwd / '.git').mkdir(parents=True)
    ss = cwd / '.step-status'
    ss.mkdir()
    (ss / 'current').write_text('fix')
    (ss / 'fix.state').write_text('active\tdiagnose\t\nplanned\tverify\t\n')
    sid = 'abcdef12-0000'
    session = {'sessionId': sid, 'name': 'one', 'cwd': str(cwd), 'pid': 1, 'state': 'active'}
    monkeypatch.setattr(model, 'load_sessions', lambda: [session.copy()])
    t = model.Transcripts()
    monkeypatch.setattr(model, 'TRANSCRIPTS', t)
    p = t.path(session)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'message': {'usage': {'input_tokens': 685261}}}) + '\n')
    before = serve.build()
    assert before['roots'][0]['ctx_pct'] is None  # transcript tokens cannot establish the window size
    (ss / 'fix.state').write_text('done\tdiagnose\t\nactive\tverify\t\n')
    model.write_json(tmp_path / 'context' / f'{sid}.json', {'pct': 2.1, 'ts': time.time()})
    after = serve.build()
    assert after['topo_hash'] == before['topo_hash']
    assert after['roots'][0]['ctx_pct'] == 2.1
    assert after['roots'][0]['status'] == '[fix] diagnose ✓ → verify ●'
