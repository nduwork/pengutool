# PenguPool

**See your agent sessions in one place.** PenguPool is a local terminal UI and VS Code/Cursor extension for managing Claude Code and pi sessions. It shows the session tree, live work panes, a map of who is talking to whom, and recent messages. Sessions run in tmux and remain available when you leave the UI.

[Landing page](docs/index.html) · [VS Code extension](extension/README.md) · [Contributing](.github/CONTRIBUTING.md) · [MIT license](LICENSE)

## Install

Requires macOS or Linux, Python 3.11+, [uv](https://docs.astral.sh/uv/), Make, tmux 3.2+, and Claude Code and/or [pi](https://pi.dev). The installer checks for missing agent tools. Building the editor extension also requires Node.js/npm and VS Code or Cursor.

```sh
git clone https://github.com/nduwork/pengutool.git
cd pengutool
make install
pengupool
```

For the editor extension, run `make ext-deps` once, then `make install-all`. Use `EDITOR_CLI=code` or `EDITOR_CLI=cursor` to select an editor. The published extension still needs the local `pengupool` backend. To remove everything, run `make uninstall-all`. Session transcripts, worktrees, and saved grouping data are retained.

`make install HARNESS=cc`, `HARNESS=pi`, or `HARNESS=both` selects the agent integration. With no selection, installed harnesses are detected automatically. Run `make check-install` to inspect prerequisites and wiring.

## Use

The left pane lists sessions. The right side shows the session map and message log above a live tmux work pane. The editor extension reads the same local model and can show the same sessions.

| Key | Action |
| --- | --- |
| `↑` / `↓`, `Enter` | Select and open a session |
| `n`, `a` | Start a session or add a previous one |
| `g`, `d`, `r` | Group, describe, or rename a session |
| `c`, `x` | Compact or close a session |
| `m`, `l`, `h` | Toggle map/log or switch harness view |
| `Ctrl+T`, `q` | Switch between list and work pane, or quit |

New sessions can use their own git worktree. Grouped sessions receive brief context about their position and role. For managed sessions, messages to another PenguPool session are limited to the sender's parent or direct children. Claude Code uses a `PreToolUse` guard; pi uses the bundled extension and pi-intercom.

PenguPool reads local Claude Code and pi session files and keeps its own state under `~/.pengupool/`. It does not need a cloud account or hosted service. The optional [workflow tracker](workflow-tracker/SKILL.md) is bundled and installed by `make install`; it shows each session's current work phase.

## Develop

```sh
uv sync --group dev
uv run pytest -q
cd extension && npm ci && npm test && npm run compile
```

`pengupool serve --once` emits one JSON snapshot for integrations. `pengupool ctl --help` lists control commands. To propose a change, use a fork and pull request; see [CONTRIBUTING.md](.github/CONTRIBUTING.md).

Version tags trigger the GitHub Actions release workflow. It builds and attaches the Python wheel, source archive, and editor VSIX to the GitHub Release. Build outputs are kept in the CI runner's temporary directory, not in the repository.

## Limits

PenguPool currently supports macOS/Linux and requires tmux. Adopting a running session from outside PenguPool stops that process and resumes it from its transcript, so wait for the current turn to finish. Context percentages appear only when the agent reports them. The message guard applies to supported agent messaging tools, not every possible external communication channel.
