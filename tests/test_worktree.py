"""worktree_add: isolate a manually-added session in its own git worktree, or fall back cleanly."""
import os
import subprocess

from pengupool import tmux


def _git(repo, *a):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, env=env)


def test_worktree_add_and_fallback(tmp_path):
    # not a git repo -> '' so the caller launches plainly
    assert tmux.worktree_add(str(tmp_path), "feature x") == ""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "commit", "--allow-empty", "-m", "root")

    p1 = tmux.worktree_add(str(repo), "feature x")
    assert p1 and os.path.isdir(p1) and p1 != str(repo)         # real worktree dir, sanitised name
    p2 = tmux.worktree_add(str(repo), "feature x")
    assert p2 and p2 != p1                                       # collision suffix advances
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"],
                              capture_output=True, text=True).stdout.split()
    assert any(b.startswith("pengupool/feature-x") for b in branches)
