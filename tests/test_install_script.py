"""`curl … | bash` must keep reading install.sh from the pipe (regression: `exec </dev/tty` froze it)."""
import os
import pty
import select
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_piped_in_a_terminal(script: str, timeout: float = 5.0) -> str:
    pid, fd = pty.fork()
    if pid == 0:  # child: a terminal session whose bash reads the script from a pipe, like curl | bash
        os.execvp("bash", ["bash", "-c", 'printf "%s" "$0" | bash', script])
    out, end = b"", time.time() + timeout
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if b"REACHED" in out:
                break
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    return out.decode(errors="replace")


def test_piped_install_keeps_reading_the_script_and_asks_on_the_terminal():
    text = (ROOT / "install.sh").read_text()
    prelude = text[: text.index("need()")]  # everything before the first side effect
    out = _run_piped_in_a_terminal(prelude + 'echo "REACHED answers=$answers"\n')
    assert "REACHED answers=/dev/tty" in out, out
