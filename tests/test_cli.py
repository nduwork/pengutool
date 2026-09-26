import subprocess
import sys

from pengupool import cli


def run(*args):
    return subprocess.run([sys.executable, "-m", "pengupool.cli", *args], capture_output=True, text=True)


def test_help_prints_usage():
    for flag in ("--help", "-h", "help"):
        r = run(flag)
        assert r.returncode == 0 and "usage: pengupool" in r.stdout and "serve" in r.stdout


def test_bare_command_launches_the_tui(monkeypatch):
    launched = []
    monkeypatch.setattr("pengupool.app.launch", lambda: launched.append(True))
    monkeypatch.setattr(sys, "argv", ["pengupool"])
    cli.main()
    assert launched == [True]


def test_tui_command_launches_the_tui(monkeypatch):
    launched = []
    monkeypatch.setattr("pengupool.app.launch", lambda: launched.append(True))
    monkeypatch.setattr(sys, "argv", ["pengupool", "tui"])
    cli.main()
    assert launched == [True]


def test_unknown_command_prints_help_and_fails():
    r = run("bogus")
    assert r.returncode == 2 and "usage: pengupool" in r.stdout
