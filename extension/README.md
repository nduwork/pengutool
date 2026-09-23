# PenguPool for VS Code

Manage collaborating Claude Code and pi sessions in VS Code. PenguPool shows a session tree, a live
map and message log, and a shared tmux terminal for each harness. The extension uses the separate
PenguPool Python CLI (`pengupool serve`) as its backend.

## Setup

**Supported environments:** desktop VS Code on macOS or Linux, including a remote Linux workspace
with the extension installed in the remote extension host. The backend needs Python 3.11+, `uv`,
`make`, tmux 3.2+, and Claude Code and/or [pi](https://pi.dev). The Marketplace extension does not
include the Python CLI, tmux, or either agent harness. Install those in the environment where the
workspace and extension host run.

**One command** installs the backend and this extension together, in every VS Code and Cursor found:

```sh
curl -fsSL https://raw.githubusercontent.com/nduwork/pengutool/main/install.sh | bash
```

Or step by step:

1. Install this extension from the Marketplace or a VSIX (each GitHub release attaches `pengupool.vsix`).
2. Install and configure the backend from the [PenguPool repository](https://github.com/nduwork/pengutool):

   ```sh
   git clone https://github.com/nduwork/pengutool.git
   cd pengutool
   make install
   make check-install
   ```

3. Reload the VS Code window and open the PenguPool Activity Bar view. If `pengupool` is not on the
   extension host's `PATH`, set `pengupool.command` to the absolute path of the CLI.

To update the backend, pull the repository and rerun `make install`. The extension and backend
share a protocol, so update both when a release calls for it. The backend manages local tmux
sessions and agent hooks; a session running outside PenguPool can be adopted only after you approve
stopping and resuming that process.

## Use

Drag a session row onto another row to group it underneath, or onto empty tree space to move it to
top level. Click a map node to select the matching sidebar row and show that session in the shared
terminal; focused map nodes also respond to Enter and Space.
Right-click a session row for its actions. Right-click empty space in Sessions to create a new
session or add a previous one.
The list and map share state cues: `● Active`, `◷ Waiting`, `○ Stale`, and `? Approval`.
The map marks the selected session with a separate focus outline.

When creating a session, PenguPool asks whether to create an isolated worktree or use the selected
folder, then which harness to run: Claude Code or pi. Each harness has its own tmux server and its
own terminal (`PenguPool` for Claude Code, `PenguPool · pi` for pi), so showing a session of one
never replaces the other. Sessions group only under a session of the same harness; pi rows are
marked `pi` in the Sessions list.
Add Previous reopens its folder picker at the last directory selected, including after an editor
restart, and lists past sessions of both harnesses.
Adopting a live session from outside PenguPool resumes it with the harness it was started with.
A session needs a saved transcript to resume, so send a prompt in a brand-new outside session and
let it finish before adopting it. PenguPool leaves the outside process running if the transcript or
destination tmux pane is not ready.
The sidebar help repeats the Map and Log toolbar icons so the controls are easy to locate. The Log
legend colors each message by its edge: green parent → child, milky blue child → parent, and orange for
a message between sessions that are not parent and child, which is only possible when you tagged the
session with `@session` in your prompt.

The sidebar and editor webviews use VS Code theme colors, and sidebar/panel **resize,
click-to-select, and zoom all work**. They do not go through xterm.js mouse reporting, which breaks border-drag resize
in the terminal version inside VS Code (xterm.js drops button-held motion / mouseup; see the repo
CHANGELOG and [xterm.js #4781](https://github.com/xtermjs/xterm.js/issues/4781) /
[VS Code #336284](https://github.com/microsoft/vscode/issues/336284)).

## Architecture

```
pengupool serve   ──NDJSON──▶  ServeClient  ──▶  SessionsView (webview tree)
(shared Python model)                          └▶  MapPanel (webview, live)
```

- **`pengupool serve`** (in `pengupool/serve.py`) emits one JSON snapshot per poll tick. Idle ticks
  are skipped; each snapshot carries a `topo_hash` covering **structure only**.
- The map webview **relayouts only when `topo_hash` changes** and otherwise restyles nodes in place,
  so the ~1 Hz refresh never makes the graph jump. Cards are sized from the rendered text, and
  **⟳ Refresh** redraws the map on demand. Nodes route through the same session-switch
  command as the sidebar.

Sessions are hosted on the shared `tmux -L pengupool`
server. The extension opens one transient **PenguPool** terminal backed
directly by a grouped tmux client. Selecting a session switches that client to the session's window,
without restarting Claude or opening another terminal. A live session
outside the server is taken over via `ctl adopt` (stop → resume, no fork), and past sessions resume
via "Add previous". Operations, command-palette quick-switch, and the map/log webviews are wired through
`pengupool serve` + `pengupool ctl`.

On the first reveal of the PenguPool Activity Bar view, the extension opens the working layout:
Sessions and Shortcuts stay in the sidebar, Map opens in the first editor group, Log opens beside
it, and the selected session appears in the bottom terminal panel. This runs
once per extension activation, so later manual editor and panel rearrangement is preserved.

The terminal directly owns the tmux client and enables tmux mouse mode. The wheel therefore enters
tmux copy-mode and scrolls the session's history instead of being translated by xterm into Up/Down
key presses. The terminal is transient: VS Code does not restore it as a stale zsh terminal after a
window reload; PenguPool creates a fresh client when its Activity Bar view is revealed again.
Log timestamps use the machine's local timezone and the compact `MM/DD/YYYY-HH:mm:ss` format.
In the PenguPool Claude terminal, Shift+Enter sends Claude's multiline sequence while Enter submits. To
copy terminal text on selection, enable VS Code's `terminal.integrated.copyOnSelection` setting.
`Shift+R` (or right-click → Restart & Resume) stops a session and resumes it in the same terminal, so a
Claude Code or pi update takes effect without losing the session or its group.

Grouping is session-based: use `c` or `/compact` to stay grouped. Do not use `/new` or `/clear`,
because either command starts a new session and removes it from the current group.

## Install, update, and remove

From the repository root:

```sh
make ext-deps                        # first time or after lockfile changes
make install-all                     # CLI, hooks, tracker, and detected editor extension
make install-all EDITOR_CLI=code     # explicitly select VS Code
make uninstall-all                   # remove from detected editor plus CLI/hooks/tracker
# VS Code: make uninstall-all EDITOR_CLI=code
```

After updating, run **Developer: Reload Window**. Use `make ext-install` or
`make ext-uninstall` to manage just the extension. At least one of **VS Code or Cursor** is required. Make detects VS Code first, then Cursor,
including standard macOS app locations when the CLI is absent from PATH. `EDITOR_CLI` is optional;
set it to `code`, `cursor`, or an absolute CLI path to choose explicitly. Use the same editor
choice when uninstalling.
The map shows workflow chains and Claude's reported context percentage, updating them on each
changed snapshot. When Claude provides no recent percentage or explicit context window size,
PenguPool hides `%ctx` instead of estimating it from transcript tokens.
Chains remain visible after completion; session starts and compaction preserve shared tracker state.

## Develop

From the repository root:

```sh
make ext-deps
make ext-compile      # then press F5 in VS Code for an Extension Development Host
make ext-package     # builds a VSIX in the system temporary directory
```

Requires the `pengupool` CLI on PATH (`make install`), or set `pengupool.command` to its path.
The extension uses `pengupool ctl --json` for launch metadata. Update the Python backend and
extension together with `make install-all`. Adding an already-live session switches to it or
offers adoption, without starting a duplicate.
