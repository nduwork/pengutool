# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org).

## [Unreleased]

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
