import subprocess
import sys


def run(*args):
    return subprocess.run([sys.executable, "-m", "pengupool.cli", *args], capture_output=True, text=True)


def test_bare_command_prints_help_instead_of_a_ui():
    r = run()
    assert r.returncode == 0 and "usage: pengupool" in r.stdout and "serve" in r.stdout


def test_unknown_command_prints_help_and_fails():
    r = run("bogus")
    assert r.returncode == 2 and "usage: pengupool" in r.stdout
