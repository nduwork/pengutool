# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org).

## [Unreleased]

## [0.2.0] - 2026-09-23

### Added
- Website at https://pengupool.nduwork.com: an animated tutorial of a real pool, a demo script for presenting it, and a best-practices guide (`docs/guide.md`). Install with `curl -fsSL https://pengupool.nduwork.com/install.sh | bash`.
- Map and Sessions view: context use is coloured by level: green below 30%, orange below 60%, red from 60%. **⟳ Refresh** now reloads everything from the backend and lays the map out again.
- `pengupool ctl slash <sid> compact | rename <name>`: Rename and Compact type into the session's own pane after clearing its input line, so a half-typed prompt is never submitted with them.
- Releases attach `SHA256SUMS`, and `install.sh` verifies the editor extension against it before installing.

### Fixed
- `curl … | bash` no longer freezes waiting on the terminal. It works when GitHub CLI is installed but not signed in, a download cut off mid-transfer runs nothing, `PENGUPOOL_REF` accepts tags, branches or SHAs, a failed editor install exits non-zero, and older uv versions accept the dependency cooloff.
- Clicking into the PenguPool terminal no longer replaces your clipboard: a drag must select at least 2 characters to copy.
- A session in a long busy turn no longer shows as Stale.
- Routing guard: an agent can no longer grant itself a direct line through `ctl context`, regroup itself with `ctl group`, or message past the rule by sending to a folder (pi), a `uds:` socket, or a differently cased name. A session file caught mid-write now blocks the send instead of allowing it.
- Process safety: the pid-reuse check works in every locale, stop and restart never report success for someone else's process, and resume rejects ids that would read as flags.
- Editor extension: it no longer closes your terminals when a session shares their name. Cmd/Ctrl shortcuts in the sidebar stay with the editor (Cmd+C no longer compacts). The default terminal attaches once instead of retrying every second. Map edges are easier to see.
- Settings writes keep symlinks and file permissions, and concurrent regrouping no longer loses a change.

### Changed
- A new session may share a folder with an existing one; the new-session picker and docs say so.
- CI runs only the suites a change needs; docs-only changes skip both.

- Editor extension: 0.1.1 (install both with `install.sh`; they ship together in this tag).
## [0.1.0] - 2026-09-23

First public release.

### Added
- VS Code/Cursor extension over a local Python backend: a session tree per harness, a live map of who instructs whom, a message log, and one integrated tmux terminal per harness that switches between sessions without restarting them.
- Claude Code and pi sessions, each on its own tmux server and never grouped across harnesses. New sessions can start in their own git worktree; a session running outside PenguPool can be adopted and resumed from its transcript.
- Restart & Resume (`Shift+R` or right-click in the editor, `pengupool ctl restart <sid>`): stop a session and resume it in the same pane with the installed Claude Code or pi, keeping its id, group and view.
- One-command install: `install.sh` sets up the CLI, the wiring for every installed harness, the workflow tracker, and the extension in every VS Code and Cursor it finds, downloading the release through GitHub's REST API. Each release attaches `pengupool.vsix`, so no Node.js is needed.
- Session roles (`pengupool ctl describe <id> --summary … --responsibility … --keywords …`) and a per-prompt `<pengupool>` block that tells each grouped session its tree, parent, children and the routing rule.
- Enforced routing: a grouped session may message only its parent or direct children (Claude's `SendMessage` through a `PreToolUse` guard, pi-intercom through the pi extension). `pengupool ctl route` names the next hop.
- Enforced triage: when a prompt matches a child's keywords, name, workspace or role, the session is told `ROUTE REQUIRED` and a Stop-hook audit sends it back once if it did not message that child. A session without a role is told `ROLE REQUIRED`. Each grouped reply starts with a `Triage:` line; "do it yourself" opts out for one prompt.
- `@session` tags: tagging a session in a prompt lets the session you typed into message it directly (and it may reply) until your next prompt.
- Log colours by edge: green parent → child, milky blue child → parent, orange for an `@session`-granted message.
- Map cards sized from the rendered text, and a **⟳ Refresh** button that redraws the map.
- Bundled workflow tracker for Claude Code and pi, shown under each map card.
- Paired releases: one tag `vX.Y.Z` is the backend version; the extension's version moves only when `extension/` changed (`scripts/release-status.sh`, `scripts/release.sh`, `.github/workflows/release.yml`).
