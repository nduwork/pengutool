"""`pengupool setup auto|cc|pi|both` / `pengupool teardown …` / `pengupool setup --check …`.

`auto` (the default) means every harness whose CLI is installed, or Claude Code when neither is.

Setup makes sure everything the chosen harnesses need is present and wired: tmux, the agent CLI
(`claude`, `pi`), and PenguPool's own plugins — the Claude Code lifecycle hooks, or for pi the
pi-intercom package plus the PenguPool pi extension. A missing tmux or CLI is installed only after
a y/N prompt (declining prints the manual command and fails); PenguPool's own wiring needs no prompt.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import harness, hook, model

CLI_INSTALL = {  # official installers
    "cc": "curl -fsSL https://claude.ai/install.sh | bash",
    "pi": "npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
}
INTERCOM = "npm:pi-intercom"
EXTENSION = Path(__file__).with_name("pi_extension.ts")


def _settings() -> Path:
    return Path(os.environ.get("CLAUDE_SETTINGS", str(model.CLAUDE / "settings.json")))


def _pi_extension() -> Path:
    return harness.PI / "extensions" / "pengupool.ts"


def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:  # non-interactive (CI, piped): never install system software unasked
        return False


def _pkg_install(pkg: str) -> str:
    """Install command for a system package via the first package manager found ('' = none)."""
    for pm, cmd in (("brew", f"brew install {pkg}"), ("apt-get", f"sudo apt-get install -y {pkg}"),
                    ("dnf", f"sudo dnf install -y {pkg}"), ("pacman", f"sudo pacman -S --noconfirm {pkg}")):
        if shutil.which(pm):
            return cmd
    return ""


def ensure(name: str, binary: str, command: str) -> bool:
    """True when `binary` is on PATH, installing it with `command` after a y/N prompt if missing."""
    if shutil.which(binary):
        print(f"✓ {name}")
        return True
    print(f"checking {name}… missing")
    if not command:
        print(f"  install {name} with your package manager, then re-run")
        return False
    if _confirm(f"  install {name} with `{command}`?") and subprocess.run(["bash", "-c", command]).returncode == 0:
        if shutil.which(binary):
            print(f"✓ {name} installed")
        else:  # e.g. Claude's installer puts it in ~/.local/bin, which this shell may not have yet
            print(f"✓ {name} installed; open a new shell if `{binary}` is not on your PATH yet")
        return True
    print(f"  install it manually: {command}")
    return False


def _has_intercom() -> bool:
    try:
        out = subprocess.run([harness.CLI["pi"], "list"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "pi-intercom" in out


def _extension_source() -> str:
    """The pi extension with the absolute `pengupool` path baked in: pi may run without uv's tool bin
    on PATH (the Claude hook pins its interpreter the same way)."""
    cli = shutil.which("pengupool") or str(Path(sys.executable).with_name("pengupool"))
    return EXTENSION.read_text().replace('|| "pengupool"', "|| " + json.dumps(cli), 1)


def _unbaked(source: str) -> str:
    """The extension as shipped: whichever `pengupool` path setup baked in is not a difference."""
    return re.sub(r'(PENGUPOOL_CLI \|\| )"[^"]*"', r'\1"pengupool"', source, count=1)


def wire(h: str) -> bool:
    if h == "cc":
        hook.install(_settings())
        return True
    if not _has_intercom():
        print(f"installing pi-intercom ({INTERCOM}) — PenguPool sessions message each other through it")
        if subprocess.run([harness.CLI["pi"], "install", INTERCOM]).returncode != 0:
            print(f"  could not install pi-intercom: run `pi install {INTERCOM}` and re-run")
            return False
    target = _pi_extension()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_extension_source())
    print(f"installed PenguPool pi extension → {target}")
    return True


def unwire(h: str) -> None:
    if h == "cc":
        hook.uninstall(_settings())
        return
    target = _pi_extension()
    if target.exists():
        target.unlink()
        print(f"removed PenguPool pi extension → {target}")
    print(f"left pi-intercom installed (other tools may use it); remove it with `pi remove {INTERCOM}`")


def check(h: str) -> bool:
    ok = bool(shutil.which(harness.CLI[h]))
    print(f"{'✓' if ok else '✗'} {harness.CLI[h]} on PATH")
    if h == "cc":
        try:
            text = _settings().read_text()
            wired = "pengupool.context" in text and "pengupool.routing" in text
        except OSError:
            wired = False
        print(f"{'✓' if wired else '✗'} Claude Code lifecycle hooks and SendMessage guard in {_settings()}")
        return ok and wired
    wired = _pi_extension().is_file() and _unbaked(_pi_extension().read_text()) == EXTENSION.read_text()
    print(f"{'✓' if wired else '✗'} PenguPool pi extension at {_pi_extension()}"
          + ("" if wired or not _pi_extension().is_file() else " (outdated: re-run setup)"))
    intercom = ok and _has_intercom()
    print(f"{'✓' if intercom else '✗'} pi-intercom installed")
    return ok and wired and intercom


def _harnesses(arg: str) -> list[str]:
    if arg in ("both", "all"):
        return list(harness.HARNESSES)
    if arg == "auto":  # a pi user who just runs `make install` must not end up with pi unwired
        return [h for h in harness.HARNESSES if shutil.which(harness.CLI[h])] or ["cc"]
    return [harness.check(arg)]


def main(args: list[str]) -> int:
    verb, rest = args[0], args[1:]
    checking = "--check" in rest
    rest = [a for a in rest if a != "--check"]
    try:
        hs = _harnesses(rest[0] if rest else "auto")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if verb == "teardown":
        for h in hs:
            unwire(h)
        return 0
    if checking:
        ok = bool(shutil.which("tmux"))
        print(f"{'✓' if ok else '✗'} tmux on PATH")
        return 0 if all([check(h) for h in hs]) and ok else 1
    if not ensure("tmux", "tmux", _pkg_install("tmux")):
        return 1
    ok = True
    for h in hs:
        if h == "pi" and not shutil.which(harness.CLI["pi"]) \
                and not ensure("npm (needed to install pi)", "npm", _pkg_install("node")):
            ok = False
            continue
        if ensure(f"{harness.LABEL[h]} ({harness.CLI[h]})", harness.CLI[h], CLI_INSTALL[h]):
            ok = wire(h) and ok
        else:
            ok = False
    return 0 if ok else 1
