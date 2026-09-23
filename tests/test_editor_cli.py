"""Editor selection for Make installs without a globally installed editor CLI."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('editor_cli', Path(__file__).resolve().parents[1] / 'scripts/editor_cli.py')
editor_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor_cli)


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return str(path)


@pytest.mark.parametrize('name', ['code', 'cursor'])
def test_either_editor_on_path(tmp_path, name):
    expected = executable(tmp_path / name)
    assert editor_cli.resolve_editor(path=str(tmp_path), app_roots=[]) == expected


def test_vscode_app_without_path_and_explicit_cursor(tmp_path):
    code = executable(tmp_path / 'Visual Studio Code.app/Contents/Resources/app/bin/code')
    cursor = executable(tmp_path / 'cursor')
    assert editor_cli.resolve_editor(path=str(tmp_path), app_roots=[tmp_path]) == code
    assert editor_cli.resolve_editor('cursor', path=str(tmp_path), app_roots=[tmp_path]) == cursor


@pytest.mark.parametrize(('name', 'app'), [('code', 'Visual Studio Code.app'), ('cursor', 'Cursor.app')])
def test_named_editor_override_finds_macos_app_cli(tmp_path, name, app):
    expected = executable(tmp_path / app / 'Contents/Resources/app/bin' / name)
    assert editor_cli.resolve_editor(name, path=str(tmp_path / 'empty'), app_roots=[tmp_path]) == expected


def test_missing_editor_explains_requirement(tmp_path):
    with pytest.raises(ValueError, match='at least one editor: VS Code or Cursor'):
        editor_cli.resolve_editor(path=str(tmp_path), app_roots=[])


def test_invalid_override_does_not_install_into_another_editor(tmp_path):
    executable(tmp_path / 'code')
    with pytest.raises(ValueError, match='not executable'):
        editor_cli.resolve_editor('missing', path=str(tmp_path), app_roots=[])


def test_make_forwards_install_and_uninstall_to_selected_editor(tmp_path):
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    log = tmp_path / 'arguments'
    cli = tmp_path / 'VS Code' / 'code'
    executable(cli)
    cli.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$EDITOR_TEST_LOG"\n')
    env = {**os.environ, 'EDITOR_TEST_LOG': str(log)}
    # Stub only the recursive packaging command; exercise the real editor recipes.
    for target in ('ext-install', 'ext-uninstall'):
        result = subprocess.run(['make', target, 'MAKE=true', f'EDITOR_CLI={cli}'],
                                cwd=root, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == [
        '--install-extension', str(root / 'extension/pengupool-local.vsix'), '--force',
        '--uninstall-extension', 'nduwork.pengupool']
