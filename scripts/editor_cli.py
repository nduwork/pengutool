#!/usr/bin/env python3
"""Find VS Code or Cursor, including macOS installs without a CLI on PATH."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def resolve_editor(editor="", *, path=None, app_roots=None):
    roots = app_roots if app_roots is not None else (Path('/Applications'), Path.home() / 'Applications')
    if editor:
        found = shutil.which(editor, path=path)
        if found:
            return found
        apps = {'code': 'Visual Studio Code.app', 'cursor': 'Cursor.app'}
        if editor in apps:
            for root in roots:
                candidate = Path(root) / apps[editor] / 'Contents/Resources/app/bin' / editor
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
        raise ValueError(f"EDITOR_CLI={editor!r} is not executable. Use code, cursor, or an absolute CLI path.")
    for command, app in (('code', 'Visual Studio Code.app'), ('cursor', 'Cursor.app')):
        found = shutil.which(command, path=path)
        if found:
            return found
        for root in roots:
            candidate = Path(root) / app / 'Contents/Resources/app/bin' / command
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise ValueError('The extension needs at least one editor: VS Code or Cursor. '
                     'Install either, enable its code/cursor command on PATH, or set '
                     'EDITOR_CLI=/absolute/path/to/code-or-cursor. '
                     'For the terminal UI only, use make install.')


def main():
    try:
        editor = resolve_editor(os.environ.get('EDITOR_CLI', ''))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if sys.argv[1:] == ['--check']:
        print(f'Using editor: {editor}')
        return 0
    return subprocess.call([editor, *sys.argv[1:]])


if __name__ == '__main__':
    sys.exit(main())
