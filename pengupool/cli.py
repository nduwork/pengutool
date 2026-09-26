"""`pengupool` entry point: launch the terminal UI, or dispatch a backend command (serve/ctl/setup)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

HELP = """usage: pengupool [command]

  (no command)              launch the terminal UI (tmux panes: sessions · map/log · agents)
  tui                       same as no command
  serve [--once]            stream NDJSON session snapshots (used by the editor extension)
  ctl <verb> …              session control: new, resume, restart, adopt, attach, select, close,
                            group, past, context, describe, profile, tree, route, authorize, register
  setup [auto|cc|pi|both]   check prerequisites and wire each harness (--check to only report)
  teardown [auto|cc|pi|both]
  install-hook              register the Claude Code lifecycle hooks
  uninstall-hook
  --version"""


def main() -> None:
    args = sys.argv[1:]
    if args[:1] in (["--version"], ["-V"]):
        from importlib.metadata import version
        print(f"pengupool {version('pengupool')}")
        return
    if args in (["install-hook"], ["uninstall-hook"]):
        from . import model
        from .hook import install, uninstall
        action = install if args[0] == "install-hook" else uninstall
        action(Path(os.environ.get("CLAUDE_SETTINGS", str(model.CLAUDE / "settings.json"))))
        return
    if args[:1] in (["setup"], ["teardown"]):
        from .install import main as setup_main  # prerequisites + per-harness wiring (see install.py)
        sys.exit(setup_main(args))
    if args[:1] == ["serve"]:
        from .serve import serve  # shared JSON backend for the VS Code extension
        serve(once="--once" in args)
        return
    if args[:1] == ["ctl"]:
        from .ctl import main as ctl_main  # one-shot mutations for the editor and pi extensions
        sys.exit(ctl_main())
    if args[:1] == ["--pane"]:  # internal: the TUI's own tmux panes run their Textual app here
        from .app import main as tui_main
        tui_main()
        return
    if args[:1] in (["-h"], ["--help"], ["help"]):
        print(HELP)
        return
    if args[:1] in ([], ["tui"]):
        from .app import launch  # Textual UI + a private tmux server for its panes
        launch()
        return
    print(HELP)
    sys.exit(2)


if __name__ == "__main__":
    main()
