# Contributing to PenguPool

Thanks for helping improve PenguPool. Contributions go through a fork and pull request: fork this repository, make a focused change on your fork, and open a PR against `main`. The maintainer reviews and merges changes. Direct write access is not offered to contributors.

Before opening a PR, run `uv run pytest -q` and, for extension changes, `cd extension && npm test && npm run compile`. Include a short description of the behavior change and any relevant test results. Use a Conventional Commit style PR title such as `fix: handle stale sessions`.

By submitting a contribution, you agree to license it under this repository's [MIT license](../LICENSE). You must have the right to submit the code and any included assets.
