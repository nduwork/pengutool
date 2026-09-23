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
