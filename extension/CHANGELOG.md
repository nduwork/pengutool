# Changelog

## Unreleased

- **Control-mode terminals (default).** Session panes are now rendered by VS Code through tmux's
  control mode instead of a real `tmux attach`, so selection, copy/paste, scrolling, and find behave
  like any other VS Code terminal; tmux still keeps the session alive when you close the editor.
  Mouse-tracking sequences from agents are dropped so the host keeps the mouse. Set
  `pengupool.terminalMode` to `tmux` to restore the old terminal.
- A failed screen capture reports failure and retries on the next selection instead of leaving a
  blank terminal. When the native addon is not available for the platform, control mode falls back to
  the tmux terminal so the extension still activates.

## 0.3.0

- Added a **clear** button to the message log panel that wipes the cross-session message log
  (via `pengupool ctl clear-logs`); the wipe is durable across restarts — only new messages return.

Ships with PenguPool v0.4.0.

## 0.2.1

- context use from 30% to 60% is yellow, between the green and the red (was the theme's orange)

Ships with PenguPool v0.3.2.

## 0.2.0

- light tree lines green for a message, milky blue for the reply
- ungrouped sessions stack in a left-aligned column on the right
- right-angle routing that never crosses a card, plus layout options

Ships with PenguPool v0.3.0.

## 0.1.1

- terminals, shortcuts, rename/compact; clearer map
- a long busy turn no longer shows as stale; sessions may share a folder (#1)

Ships with PenguPool v0.2.0.

## 0.1.0

- First public release: Sessions and pi Sessions trees, live map, message log, and one tmux-backed terminal per harness.
- New session (worktree or current folder, Claude Code or pi), add a previous session, adopt, group by drag and drop, rename, describe role, compact, close.
- Restart & Resume (`Shift+R` or right-click) resumes a session in place after a Claude Code or pi update.
- Log colours by edge: green parent → child, milky blue child → parent, orange for an `@session`-granted message.
- Map cards are sized from the rendered text; **⟳ Refresh** redraws the map.

Ships with PenguPool v0.1.0.
