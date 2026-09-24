---
name: workflow-tracker
description: |
  Report progress through a multi-step workflow (a skill or task with two or more named
  phases such as init → loop → summary) as a one-line step chain posted in the conversation
  at every phase transition: `init ✓ → loop|check agent status ● → summary ○`. Use whenever
  you start such a workflow, when the user asks for a status/progress indicator, or types
  "/workflow-tracker". Claude records transitions with scripts/steps.sh and quotes the echoed
  chain in its reply; showing it in the status line as well is optional.
triggers:
  - workflow-tracker
  - step-status
  - status line progress
  - show pipeline status
  - step chain
  - workflow progress indicator
---

# workflow-tracker

A step chain reported in the conversation, driven by explicit CLI calls. Nothing is
inferred: you tell it the phases, mark them as you go, and post the chain after each mark.

Symbols: `✓` done · `●` in progress · `○` planned · `✗` failed. An active step may carry
a detail after `|` (e.g. `loop|check agent status`).

## How to use (Claude Code and pi)

`STEPS` below means `bash <this skill's dir>/scripts/steps.sh`. The Skill tool prints the
base directory of this skill when loaded; installed as a plugin it is
`${CLAUDE_PLUGIN_ROOT}` (Codex: `${PLUGIN_ROOT}`), as a symlinked skill it is
`~/.claude/skills/workflow-tracker`, under pi it is `~/.pi/agent/skills/workflow-tracker`. Run it via the Bash tool from the project root — state
lives in `./.step-status/` (self-ignoring, keyed by cwd).

1. **At the start** of a multi-step workflow, define the chain once, naming it for the task
   with `--name`. That name is the `[bracket]` label on every ticker line, so pick something
   descriptive (`add-plugin`, `fix-auth`) — never leave it as `default`. The first step
   becomes active:
   ```bash
   STEPS set --name add-plugin init loop summary   # → [add-plugin] init ● → loop ○ → summary ○
   ```
2. **On each transition**, run the command and **post the line it echoes in your reply**,
   on its own line, before continuing — that message *is* the progress indicator. The
   user may not see tool output, so a transition you do not quote is invisible.
   ```bash
   STEPS done init                          # ✓ init, auto-activates loop
   STEPS start loop "check agent status"    # optional detail → loop|check agent status ●
   STEPS done loop                          # ✓ loop, auto-activates summary
   STEPS fail summary                       # ✗ if a step blows up
   ```
3. **At the end**: post the finished chain (all `✓`) as the last progress line. There's no need to clear
   it: a finished chain (every step `✓` or `✗`) stops rendering a minute after its last update
   (`STEP_STATUS_DONE_TTL`, in seconds), so the status line, the map and the next prompt no longer
   carry it and the next workflow starts with `set`. Its files stay, and `list` still shows it. An
   unfinished chain never expires. Session starts and compaction preserve the shared chain and
   reinject tracking instructions.

### Messages between sessions

When a workflow talks to another session (SendMessage or a pi-intercom send/ask out, a
`<cross-session-message>` or an intercom message in), record it on the active step and post the echoed line, so
the ticker shows the hand-off and the reply, not just a step that seems stalled:

```bash
STEPS msg sent RCM-info-2099 "asked for step-status diagnostics"   # → ask|⇢ RCM-info-2099: asked for … ●
STEPS msg recv RCM-info-2099 "cwd + state reported"                # → ask|⇠ RCM-info-2099: cwd + state reported ●
```

`sent` renders as `⇢ session`, `recv` as `⇠ session`; the detail is replaced by the latest
message, so a step waiting on another session always shows what it is waiting for. Once the
reply is handled, `done` the step as usual (or `start <step> "<detail>"` to overwrite).

### Named chains (several workflows in one session)

Every chain has a name and every rendered line starts with it in brackets, so the user can
always tell which workflow a ticker line belongs to. `set --name <name>` names the current
chain as you define it (the one-command form used above); `use <name>` switches to (or
creates) another. Use a distinct `use` when the user is likely to come back to a thread
after doing something else (a PR review, a migration, an investigation):

```bash
STEPS use pr-42                          # switch to (or create) chain "pr-42"; renders as [pr-42] …
STEPS set triage fix verify              # chains are independent — set/done/clear act on the current one
STEPS note "PR 42: flaky test in auth"   # one-line context so you can pick the thread up later
STEPS list                               # * marks current:  * [pr-42] triage ✓ → fix ● → verify ○  # PR 42: …
STEPS use default                        # switch back; pr-42 keeps its state
```

Context switching: when the user changes topic ("back to the PR", "park this, look at X"),
`STEPS list`, then `STEPS use <name>` for the chain that matches (or create one). Read the
note and the rendered chain aloud in one line so the user knows where that thread stopped,
then continue from the active step. Before leaving a chain, `STEPS note` what the next
action is. `STEPS clear` removes only the current chain; a fresh session (startup/clear)
removes all of them.

### Loops (`/loop`, polling, retry cadences)

A linear chain marks each step done once — wrong shape for a workflow that repeats. For a
loop, name the repeating steps as a *segment* with `cycle`; they bracket off with a `↻N`
pass counter while steps outside the loop (a final report, a teardown) stay put:

```bash
STEPS set --name watch-ci fetch check report   # [watch-ci] fetch ● → check ○ → report ○
STEPS done fetch                                # normal pass 1 — no counter yet
STEPS cycle fetch check                         # → [watch-ci] [fetch ● → check ○ ↻2] → report ○
STEPS done fetch ; STEPS done check
STEPS cycle                                     # no args reuses the body → ↻3, body re-armed
# … loop until the exit condition …
STEPS done fetch ; STEPS done check ; STEPS done report   # body done, terminal step runs
```

- `cycle <step>...` marks those steps the loop body: resets them to planned (first active),
  bumps `↻N`, and leaves every other step alone. `cycle` with no args reuses the last body.
- In a `/loop`, the session persists across ticks, so `cycle` once per iteration (at the top
  of the tick) and quote the echoed line — that line is the per-iteration ticker.
- `↻N` appears only once looping starts; a fresh `set` drops it back to pass 1.
- The body should be a contiguous run (it brackets from the first to the last body step).

Rules:
- One `set` per workflow. Re-running `set` restarts the chain (and clears any `↻` counter).
- `done X` activates the next *planned* step, so you rarely need `start` unless you
  want to attach a detail or skip ahead.
- Names with spaces are fine (quote them). Keep them short — it's a status bar.
- If the user asks "where are we?", run `STEPS render` (one chain) or `STEPS list` (all) and quote it.
- Chain names: letters, digits, `.`, `_`, `-` (they become file names).

## What the user sees

One short line from you at each transition, e.g.

```
[add-plugin] init ✓ → loop|check agent status ● → summary ○
```

Every mutating `steps.sh` command echoes that line; quote it verbatim. Keep it to the chain
alone — no preamble — so the messages read as a ticker. A `SessionStart` hook clears any
chain left in the cwd from a previous session. A `UserPromptSubmit` hook injects the current
chain (or a nudge to `set` one) into every turn, so the ticker does not depend on Claude
remembering this skill exists.

## Optional: mirror the chain in the status line (`/workflow-tracker setup`)

Under pi, `make install-tracker` installs a pi extension that does the hooks' job (the chain or
nudge is appended to every prompt) and shows the chain in pi's footer; the rest of this section is
Claude Code only.

Not needed for the conversation ticker. If the user explicitly wants the chain in the
status bar too, plugins cannot set `statusLine`, so run:

```bash
bash <this skill's dir>/scripts/wire_statusline.sh          # plugin installs skip the hook automatically (already shipped)
bash <this skill's dir>/scripts/wire_statusline.sh --unwire # undo
```

It auto-detects an existing status line command and wraps it, or installs standalone. It
refuses to touch a settings.json it cannot parse, and saves the original `statusLine` so
`--unwire` restores it exactly. Plugin installs live in a versioned cache dir, so the scripts
are copied to `~/.claude/step-status/bin` and settings point there: re-run setup after a
plugin update. Tell the user to restart Claude Code afterwards. `install.sh` (symlink install) runs this
for you. Hook manifest for reference: `hooks/hooks.json`.

## Checks

```bash
bash workflow-tracker/scripts/steps.sh --selfcheck
bash workflow-tracker/scripts/wire_statusline.sh --selfcheck   # settings.json wrap/unwire round trip
for s in workflow-tracker/scripts/*.sh; do bash -n "$s"; done
```

## Gotchas

- State is keyed by cwd, not session. Two Claude sessions in the same directory share
  (and clobber) the same chains and `current` pointer.
- The SessionStart hook clears the chain on `startup|clear` only; `/compact` and resume keep it.
- Step names must not be empty or contain tabs/newlines, and must be unique in a chain.
- Tool output is not reliably shown to the user: the progress line only exists if you
  write it in your reply. Every mutating command echoes the chain so you can quote it.
- If the optional status-line mirror is wired, it repaints on Claude Code's cadence, so a
  chain cleared in the same turn it was set never shows there.
- `statusline.sh` never fails and prints nothing when no chain is set, so wrapping is
  invisible until a workflow calls `set`.
