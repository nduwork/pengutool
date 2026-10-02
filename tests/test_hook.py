"""PenguPool and workflow-tracker hooks coexist on shared Claude events."""
import json

from pengupool import hook


def _tracker(script: str, matcher: str | None = None) -> dict:
    entry = {"hooks": [{"type": "command", "command": f'bash "/stable/{script}"'}]}
    if matcher:
        entry["matcher"] = matcher
    return entry


def test_install_preserves_tracker_hooks_and_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    tracker_start = _tracker("hook_session_start.sh", "startup|clear")
    tracker_prompt = _tracker("hook_prompt.sh")
    settings.write_text(json.dumps({"hooks": {
        "SessionStart": [tracker_start],
        "UserPromptSubmit": [tracker_prompt],
    }}))

    hook.install(settings)
    hook.install(settings)

    installed = json.loads(settings.read_text())["hooks"]
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                  "PermissionRequest", "Stop"):
        commands = [h.get("command", "") for group in installed[event]
                    for h in group.get("hooks", [])]
        assert sum("pengupool.context" in command for command in commands) == 1
    assert tracker_start in installed["SessionStart"]
    assert tracker_prompt in installed["UserPromptSubmit"]


def test_uninstall_preserves_other_hooks_in_shared_group(tmp_path):
    settings = tmp_path / 'settings.json'
    foreign = {'type': 'command', 'command': 'echo keep'}
    settings.write_text(json.dumps({'permissions': {'allow': []}, 'hooks': {
        'SessionStart': [{'matcher': 'startup', 'hooks': [
            {'type': 'command', 'command': hook.CMD}, foreign]}]}}))
    hook.uninstall(settings)
    assert json.loads(settings.read_text()) == {'permissions': {'allow': []}, 'hooks': {
        'SessionStart': [{'matcher': 'startup', 'hooks': [foreign]}]}}
    hook.install(settings)
    hook.uninstall(settings)
    assert json.loads(settings.read_text())['hooks']['SessionStart'][0]['hooks'] == [foreign]


def test_hook_round_trip_preserves_settings(tmp_path):
    settings = tmp_path / 'settings.json'
    original = {'statusLine': {'command': 'echo keep'}}
    settings.write_text(json.dumps(original))
    hook.install(settings)
    hook.uninstall(settings)
    hook.uninstall(settings)
    assert json.loads(settings.read_text()) == original


def test_install_wraps_the_status_line_and_uninstall_restores_it(tmp_path):
    settings = tmp_path / 'settings.json'
    original = {'type': 'command', 'command': "npx -y ccstatusline@latest", 'padding': 0, 'refreshInterval': 10}
    settings.write_text(json.dumps({'statusLine': original}))
    hook.install(settings)
    hook.install(settings)  # idempotent: never wraps its own wrapper
    line = json.loads(settings.read_text())['statusLine']
    assert line == {**original, 'command': f"{hook.STATUS} -- 'npx -y ccstatusline@latest'"}
    hook.uninstall(settings)
    assert json.loads(settings.read_text())['statusLine'] == original


def test_install_adds_a_silent_status_line_when_none_is_set(tmp_path):
    settings = tmp_path / 'settings.json'
    hook.install(settings)
    assert json.loads(settings.read_text())['statusLine'] == {'type': 'command', 'command': hook.STATUS}
    hook.uninstall(settings)
    assert 'statusLine' not in json.loads(settings.read_text())


def test_install_repins_a_stale_wrapper_and_leaves_an_outer_wrapper_alone(tmp_path):
    settings = tmp_path / 'settings.json'
    stale = "/old/venv/bin/python -m pengupool.statusline -- 'echo hi'"
    settings.write_text(json.dumps({'statusLine': {'type': 'command', 'command': stale}}))
    hook.install(settings)
    assert json.loads(settings.read_text())['statusLine']['command'] == f"{hook.STATUS} -- 'echo hi'"
    outer = f'bash "/stable/statusline.sh" -- {hook.shlex.quote(hook.STATUS)}'
    settings.write_text(json.dumps({'statusLine': {'type': 'command', 'command': outer}}))
    hook.uninstall(settings)
    assert json.loads(settings.read_text())['statusLine']['command'] == outer
