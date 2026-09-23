"""`pengupool` entry point: dispatch to the backend the editor and pi extensions call."""
from __future__ import annotations

import os
import sys
from pathlib import Path

HELP = """usage: pengupool <command>

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
    print(HELP)
    sys.exit(0 if args[:1] in ([], ["-h"], ["--help"], ["help"]) else 2)


if __name__ == "__main__":
    main()
