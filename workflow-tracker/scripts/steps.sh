#!/usr/bin/env bash
# workflow-tracker CLI — record where a multi-step workflow is, so the status line can
# render a chain like:  init ✓ → loop|check agent status ● → summary ○
#
#   steps.sh set [--name CHAIN] <step>...  define the chain; first step active. --name/-n
#                                     names the ticker (the [bracket] label); default: "default"
#   steps.sh start <name> [detail]    mark a step in progress (detail shows as name|detail)
#   steps.sh done <name>              mark done; activates the next planned step if none is active
#   steps.sh fail <name>              mark failed
#   steps.sh assert <name>            ordering gate: exit 2 unless every earlier step is done
#   steps.sh cycle <step>...          loops: re-arm a segment (the named steps) for its next
#                                     pass; brackets them as [ … ↻N]. No args reuses the last body
#   steps.sh msg sent|recv <session> [text]   note a cross-session message on the active step
#                                     (detail becomes "⇢ session: text" / "⇠ session: text")
#   steps.sh render                   print the current chain (nothing if no chain)
#   steps.sh clear                    remove the current chain (and its note)
#   steps.sh use <chain>              switch to (or create) a named chain; the status line follows
#   steps.sh list                     all chains in this dir: * marks current, with notes
#   steps.sh note [text]              set (or print) a one-line context note for the current chain
#   steps.sh --selfcheck              run the built-in check
#
# A finished chain — no step active (all ✓, or stopped at a ✗, or the last step done past a
# skipped ○) and not a loop — expires DONE_TTL seconds after its last update ($STEP_STATUS_DONE_TTL,
# default 60). render prints nothing, start/done/fail/msg refuse it, and a bare `set` starts a new
# `default` chain rather than overwriting it, so the next workflow always gets its own chain. Its
# files stay; `list` still shows it. A looping chain (one that has been `cycle`d) never expires:
# it ends only with `set` or `clear`.
#
# State: $STEP_STATUS_DIR (default ./.step-status). `current` names the active chain (default:
# "default"); <chain>.state holds one step per line, <chain>.note an optional context line:
#   <status>\t<name>\t<detail>     status ∈ planned|active|done|failed
set -uo pipefail
# ponytail: cwd-keyed — two sessions in one dir clobber each other; key by session if that bites.

# STEP_STATUS_DIR overrides everything (selfcheck, callers). Otherwise, inside a git repo, use the
# MAIN worktree root's .step-status so every linked worktree shares one tracker (matches PenguPool's
# status_dir reader). --git-common-dir returns the shared .git; its dirname is the main root. A git
# spawn here is fine — steps.sh runs on demand, not per poll. Falls back to $PWD outside a repo.
if [[ -n "${STEP_STATUS_DIR:-}" ]]; then
  DIR="$STEP_STATUS_DIR"
else
  _gcd="$(git -C "$PWD" rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$_gcd" ]]; then
    case "$_gcd" in /*) ;; *) _gcd="$PWD/$_gcd" ;; esac   # relative ".git" in the main worktree
    DIR="$(cd "$(dirname "$_gcd")" 2>/dev/null && pwd)/.step-status"
  fi
  DIR="${DIR:-$PWD/.step-status}"
fi
CHAIN="$( [[ -f "$DIR/current" && ! -L "$DIR/current" ]] && head -c 200 "$DIR/current" | tr -d '\n' )"
# `current` is repo-controlled: enforce the same charset as valid_chain() so a hostile
# current file can't traverse out of $DIR (e.g. ../../foo) via $DIR/$CHAIN.state.
[[ "$CHAIN" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || CHAIN=default
CHAIN="${CHAIN:-default}"
STATE="$DIR/$CHAIN.state"
NOTE="$DIR/$CHAIN.note"

DONE_TTL="${STEP_STATUS_DONE_TTL:-60}"; DONE_TTL="${DONE_TTL//[^0-9]/}"; DONE_TTL="${DONE_TTL:0:9}"
DONE_TTL=$((10#${DONE_TTL:-60}))   # 10#: "08" is eight seconds, not an octal error

# expired <state-file> — true when no step is active, the chain isn't a loop, and the file is older
# than DONE_TTL. mtime is the last update, i.e. when the chain finished (no-op updates don't write).
# GNU stat first; BSD/macOS stat second.
expired() {
  local f="$1" st name detail
  [[ -f "$f" ]] || return 1
  [[ -e "${f%.state}.cycle" ]] && return 1   # a loop waits between passes; it ends with set or clear
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do
    [[ "$st" == active ]] && return 1
  done < "$f"
  local m; m="$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)" || return 1
  [[ "$m" =~ ^[0-9]+$ ]] || return 1
  (( $(date +%s) - m > DONE_TTL ))
}

sym() { case "$1" in done) printf '✓';; active) printf '●';; failed) printf '✗';; *) printf '○';; esac; }

# State lives inside the project cwd, which a cloned repo controls: never follow symlinks
# and never echo control characters into the terminal.
safe_state() {
  if [[ -L "$DIR" || -L "$STATE" || -L "$NOTE" ]]; then echo "steps.sh: refusing symlinked $DIR" >&2; return 1; fi
}
valid_name() {
  [[ -n "$1" && "$1" != *$'\t'* && "$1" != *$'\n'* ]] || { echo "steps.sh: invalid step name/detail (empty, tab or newline)" >&2; return 2; }
}
# Chain names become file names: keep them to a safe charset.
valid_chain() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "steps.sh: invalid chain name '$1' (letters, digits, . _ -)" >&2; return 2; }
}

# bash 3.2-safe membership test (macOS ships bash 3.2 — no namerefs/assoc arrays).
in_list() { local n="$1"; shift; local x; for x in "$@"; do [[ "$x" == "$n" ]] && return 0; done; return 1; }

# Opt-in ordering enforcement: STEP_STATUS_STRICT_ORDER=1|true|yes|on makes `done`/`fail` refuse to
# finish a step out of order (an earlier step still not done). Off by default (backward compatible).
strict_on() {
  case "${STEP_STATUS_STRICT_ORDER:-}" in 1|true|yes|on) return 0;; *) return 1;; esac
}

# Every row loop reads `… || [[ -n "${name-}" ]]` so a last row without a trailing newline (a
# hand-edited file) is still read: dropping it could hide an active step, or make a running chain
# look finished and expire it.
#
# render_file <state-file> [body-first] [body-last] [count] — one line, or nothing if empty.
# When a loop body is given, the contiguous run from body-first to body-last is wrapped
# `[ … ↻count]` so a loop segment reads apart from one-shot steps around it.
render_file() {
  local file="$1" bfirst="${2-}" blast="${3-}" cnt="${4-}"
  [[ -f "$file" && ! -L "$file" ]] || return 0
  local out="" st name detail seg
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do
    [[ -z "$name" ]] && continue
    [[ -n "$out" ]] && out+=" → "
    [[ -n "$bfirst" && "$name" == "$bfirst" ]] && out+="["
    seg="$name"; [[ -n "$detail" ]] && seg+="|$detail"; seg+=" $(sym "$st")"
    out+="$seg"
    [[ -n "$blast" && "$name" == "$blast" ]] && out+=" ↻$cnt]"
  done < "$file"
  [[ -n "$out" ]] && printf '%s\n' "$out" | tr -d '\000-\010\013-\037\177'
}
# Current chain, always prefixed with its [name] so every ticker line says which chain it is.
render() {
  safe_state || return 0
  local bf="" bl="" cnt="" info; info="$(read_cycle)"
  IFS=$'\t' read -r bf bl cnt <<<"$info"
  expired "$STATE" && return 0   # finished a while ago: no longer the repo's current workflow
  local line; line="$(render_file "$STATE" "$bf" "$bl" "$cnt")"
  [[ -n "$line" ]] && printf '[%s] %s\n' "$CHAIN" "$line"
}

# read_cycle — for a looping chain, echo "<body-first>\t<body-last>\t<count>" (first/last body
# step in chain order); nothing if the chain isn't looping. The ↻ segment lives inside the chain.
read_cycle() {                       # read_cycle [chain] — defaults to the current chain
  local cf="$DIR/${1:-$CHAIN}.cycle" sf="$DIR/${1:-$CHAIN}.state" cnt body
  [[ -f "$sf" && -f "$cf" && ! -L "$cf" ]] || return 0
  { IFS= read -r cnt; IFS= read -r body; } < "$cf"
  cnt="${cnt//[^0-9]/}"; cnt="${cnt:0:6}"; [[ -n "$cnt" ]] || return 0   # repo-controlled: strip + cap so junk can't overflow
  local -a bodyarr=(); IFS=$'\t' read -r -a bodyarr <<<"$body"
  [[ ${#bodyarr[@]} -ge 1 ]] || return 0
  local st name detail bf="" bl=""
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do
    [[ -z "$name" ]] && continue
    in_list "$name" "${bodyarr[@]}" && { [[ -z "$bf" ]] && bf="$name"; bl="$name"; }
  done < "$sf"
  [[ -n "$bf" ]] && printf '%s\t%s\t%s\n' "$bf" "$bl" "$cnt"
}

# Point `current` at a chain and repoint the STATE/NOTE globals at it.
switch_chain() {
  valid_chain "$1" || return 2
  ensure_dir || return 1
  printf '%s\n' "$1" | atomic_write "$DIR/current"
  CHAIN="$1"; STATE="$DIR/$CHAIN.state"; NOTE="$DIR/$CHAIN.note"
}

use_chain() {
  switch_chain "$1" || return $?
  if [[ -f "$STATE" ]]; then
    expired "$STATE" && { printf '[%s] %s (finished)\n' "$CHAIN" "$(render_file "$STATE")"; return; }
    render; return
  fi
  printf '[%s] (empty — run 'set')\n' "$CHAIN"
}

list_chains() {
  [[ -d "$DIR" ]] || return 0
  safe_state || return 1
  local f n mark note
  for f in "$DIR"/*.state; do
    [[ -f "$f" ]] || continue
    n="$(basename "$f" .state)"
    valid_chain "$n" 2>/dev/null || continue   # skip foreign *.state names (never ours; may carry control bytes)
    mark=" "; [[ "$n" == "$CHAIN" ]] && mark="*"
    note=""; [[ -f "$DIR/$n.note" && ! -L "$DIR/$n.note" ]] && note="$(head -n1 "$DIR/$n.note" | tr -d '\000-\037\177')"
    local bf="" bl="" cnt=""; IFS=$'\t' read -r bf bl cnt <<<"$(read_cycle "$n")"
    printf '%s [%s] %s%s\n' "$mark" "$n" "$(render_file "$f" "$bf" "$bl" "$cnt")" "${note:+  # $note}"
  done
}

note_chain() {
  ensure_dir || return 1
  if [[ $# -eq 0 ]]; then [[ -f "$NOTE" ]] && head -n1 "$NOTE"; return 0; fi
  valid_name "$*" || return 2
  printf '%s\n' "$*" | atomic_write "$NOTE"
}

ensure_dir() {
  mkdir -p "$DIR" || return 1
  safe_state || return 1
  [[ -f "$DIR/.gitignore" && ! -L "$DIR/.gitignore" ]] || printf '*\n' | atomic_write "$DIR/.gitignore"
}

# Atomic write: stdin → <path> via a temp file in the same dir. Refuses a symlinked <path>
# outright — `mv` onto a symlink that resolves to a directory would drop the temp file inside
# it (writing outside $DIR). Every state artifact (current, .gitignore, <chain>.cycle, note,
# state) writes here, so a repo-planted symlink can never redirect a write.
atomic_write() {
  [[ -L "$1" ]] && { echo "steps.sh: refusing symlinked $1" >&2; return 1; }
  [[ -e "$1" && ! -f "$1" ]] && { echo "steps.sh: refusing non-file $1" >&2; return 1; }   # planted dir: mv would drop the temp inside
  local tmp; tmp="$(mktemp "$DIR/.w.XXXXXX")" || return 1
  { cat > "$tmp" && mv -f "$tmp" "$1"; } || { rm -f "$tmp"; return 1; }
}
write_state() { atomic_write "$STATE"; }

# A finished, expired chain is history: changing it would hand the old chain (name, ✓s) to the next
# workflow. Say how to start the next one instead.
finished_hint() {
  expired "$STATE" || return 0
  echo "steps.sh: chain '$CHAIN' finished — start the next workflow with: steps.sh set --name <name> <step>..." >&2
  return 1
}

# update <name> <status> [detail] — rewrite matching row. After `done`, activate the first
# planned row only if no row is active (out-of-order use never yields two ● at once).
update() {
  local target="$1" newst="$2" newdetail="${3-}" hit=0 any_active=0 i
  local -a sts=() names=() details=()
  [[ -f "$STATE" ]] || { echo "steps.sh: no chain — run 'set' first" >&2; return 1; }
  safe_state || return 1
  finished_hint || return 1
  local at=-1
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do
    [[ -z "$name" ]] && continue
    if [[ "$name" == "$target" ]]; then st="$newst"; detail="$newdetail"; hit=1; at=${#sts[@]}; fi
    sts+=("$st"); names+=("$name"); details+=("$detail")
  done < "$STATE"
  [[ $hit == 1 ]] || { echo "steps.sh: unknown step '$target'" >&2; return 1; }
  # strict ordering (opt-in): can't finish a step out of order while an earlier step isn't done
  if [[ "$newst" == done || "$newst" == failed ]] && strict_on; then
    for (( i=0; i<at; i++ )); do
      [[ "${sts[$i]}" != done ]] && { echo "steps.sh: strict order: can't $newst '$target' until an earlier step is done" >&2; return 3; }
    done
  fi
  # single-current-phase invariant: activating a step demotes any other active step to planned.
  if [[ "$newst" == active ]]; then
    for i in "${!sts[@]}"; do [[ "${names[$i]}" != "$target" && "${sts[$i]}" == active ]] && sts[$i]=planned; done
  fi
  for st in "${sts[@]}"; do [[ "$st" == active ]] && any_active=1; done
  # advance forward only: finishing a step never re-opens a planned step before it (a skipped one)
  if [[ "$newst" == done && $any_active == 0 ]]; then
    for i in "${!sts[@]}"; do (( i > at )) && [[ "${sts[$i]}" == planned ]] && { sts[$i]=active; break; }; done
  fi
  local new; new="$(for i in "${!sts[@]}"; do printf '%s\t%s\t%s\n' "${sts[$i]}" "${names[$i]}" "${details[$i]}"; done)"
  # a no-op (e.g. `done` on a step that is already done) leaves the file alone: rewriting it would
  # bump the mtime and revive a finished chain that has already expired
  [[ "$new" == "$(cat "$STATE")" ]] && return 0
  printf '%s\n' "$new" | write_state
}

check_steps() {
  [[ $# -ge 1 ]] || { echo "usage: steps.sh set <name>..." >&2; return 2; }
  local n seen=$'\n'
  for n in "$@"; do
    valid_name "$n" || return 2
    [[ "$seen" == *$'\n'"$n"$'\n'* ]] && { echo "steps.sh: duplicate step name '$n'" >&2; return 2; }
    seen+="$n"$'\n'
  done
}
set_chain() {
  check_steps "$@" || return $?
  ensure_dir || return 1
  rm -f "$DIR/$CHAIN.cycle"   # a fresh set is pass 1 — drop any stale ↻ counter
  { printf 'active\t%s\t\n' "$1"; shift; for n in "$@"; do printf 'planned\t%s\t\n' "$n"; done; } | write_state
}

# cycle <step>... — re-arm a loop *segment* for its next pass. The named steps are the loop
# body: they reset to planned (first one active), steps outside the body are left untouched,
# and the ↻ counter bumps. No args reuses the body from the last cycle. Absent counter means
# we're leaving pass 1, so the next pass is ↻2.
cycle_chain() {
  [[ -f "$STATE" ]] || { echo "steps.sh: no chain — run 'set' first" >&2; return 1; }
  safe_state || return 1
  local cf="$DIR/$CHAIN.cycle" st name detail x
  local -a body=("$@") names=()
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do [[ -n "$name" ]] && names+=("$name"); done < "$STATE"
  [[ ${#names[@]} -ge 1 ]] || { echo "steps.sh: empty chain — run 'set' first" >&2; return 1; }
  if [[ ${#body[@]} -eq 0 && -f "$cf" && ! -L "$cf" ]]; then          # reuse the stored body
    { IFS= read -r x; IFS= read -r x; } < "$cf"; IFS=$'\t' read -r -a body <<<"$x"
  fi
  [[ ${#body[@]} -ge 1 ]] || { echo "steps.sh: cycle needs the looping step(s): steps.sh cycle <step>..." >&2; return 2; }
  for name in "${body[@]}"; do in_list "$name" "${names[@]}" || { echo "steps.sh: unknown step '$name'" >&2; return 1; }; done
  local inb=0 left=0                                                   # body must be one contiguous run in chain order
  for name in "${names[@]}"; do
    if in_list "$name" "${body[@]}"; then [[ $left == 1 ]] && { echo "steps.sh: loop body must be contiguous steps" >&2; return 2; }; inb=1
    else [[ $inb == 1 ]] && left=1; fi
  done
  local cnt=1
  [[ -f "$cf" && ! -L "$cf" ]] && { IFS= read -r x < "$cf"; x="${x//[^0-9]/}"; x="${x:0:6}"; cnt="${x:-1}"; }
  cnt=$((10#$cnt+1)); cnt="${cnt:0:6}"   # 10#: a hand-edited "08" is not octal; cap matches read_cycle
  local first=""                                                       # first body step in chain order → active
  for name in "${names[@]}"; do in_list "$name" "${body[@]}" && { first="$name"; break; }; done
  { while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do
      [[ -z "$name" ]] && continue
      if in_list "$name" "${body[@]}"; then detail=""; [[ "$name" == "$first" ]] && st=active || st=planned
      elif [[ "$st" == active ]]; then st=planned; fi   # re-arming the loop clears a stray active outside the body
      printf '%s\t%s\t%s\n' "$st" "$name" "$detail"
    done < "$STATE"; } | write_state
  { printf '%s\n' "$cnt"; local IFS=$'\t'; printf '%s\n' "${body[*]}"; } | atomic_write "$cf"
}

# msg sent|recv <session> [text] — record inter-session comms as the active step's detail.
msg_step() {
  local dir="${1-}" who="${2-}" text="${3-}" arrow st name detail active=""
  case "$dir" in sent) arrow='⇢';; recv) arrow='⇠';; *) echo "usage: steps.sh msg sent|recv <session> [text]" >&2; return 2;; esac
  [[ -n "$who" ]] || { echo "usage: steps.sh msg sent|recv <session> [text]" >&2; return 2; }
  valid_name "$who$text" || return 2
  [[ -f "$STATE" ]] || { echo "steps.sh: no chain — run 'set' first" >&2; return 1; }
  safe_state || return 1
  finished_hint || return 1
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do [[ "$st" == active ]] && { active="$name"; break; }; done < "$STATE"
  [[ -n "$active" ]] || { echo "steps.sh: no active step to attach the message to" >&2; return 1; }
  update "$active" active "$arrow $who${text:+: $text}"
}

need_name() { [[ -n "${1-}" ]] || { echo "usage: steps.sh $2 <name>" >&2; return 2; }; }

# assert <name> — ordering gate for callers (a skill, or a phase script). Succeeds only if every
# step before <name> is done, i.e. the workflow has genuinely reached that phase. Jumping ahead is
# then caught at the call site instead of drifting silently. Exits 3 on unknown step, 2 on blocked.
assert_step() {
  local target="$1" st name detail i at=-1
  local -a sts=() names=()
  [[ -f "$STATE" ]] || { echo "steps.sh: no chain — run 'set' first" >&2; return 1; }
  safe_state || return 1
  while IFS=$'\t' read -r st name detail || [[ -n "${name-}" ]]; do
    [[ -z "$name" ]] && continue
    sts+=("$st"); names+=("$name")
  done < "$STATE"
  for i in "${!names[@]}"; do [[ "${names[$i]}" == "$target" ]] && { at=$i; break; }; done
  [[ $at -ge 0 ]] || { echo "steps.sh: assert: unknown step '$target'" >&2; return 3; }
  for (( i=0; i<at; i++ )); do
    [[ "${sts[$i]}" != done ]] && { echo "steps.sh: assert: blocked — step '$target' has an earlier step not done" >&2; return 2; }
  done
  return 0
}

selfcheck() {
  local root d s; root="$(mktemp -d)"; d="$root/state"; mkdir "$d"; s="${BASH_SOURCE[0]}"; export STEP_STATUS_DIR="$d"   # two levels so $d/.. fixtures stay private
  r() { bash "$s" render; }
  fail() { echo "FAIL $1: $(r)"; rm -rf "$root"; exit 1; }
  bash "$s" set init loop summary
  [[ "$(r)" == "[default] init ● → loop ○ → summary ○" ]] || fail set
  bash "$s" done init; bash "$s" start loop "check agent status"
  [[ "$(r)" == "[default] init ✓ → loop|check agent status ● → summary ○" ]] || fail start
  bash "$s" done loop
  [[ "$(r)" == "[default] init ✓ → loop ✓ → summary ●" ]] || fail auto-next
  bash "$s" fail summary
  [[ "$(r)" == "[default] init ✓ → loop ✓ → summary ✗" ]] || fail fail
  bash "$s" set a b c; bash "$s" start b; bash "$s" done a
  [[ "$(r)" == "[default] a ✓ → b ● → c ○" ]] || fail out-of-order
  # --assert gate: a step with an earlier step not done is blocked; reachable once priors are done
  bash "$s" use default >/dev/null; bash "$s" set a b c >/dev/null
  bash "$s" assert b 2>/dev/null && fail assert-before-prior
  bash "$s" done a >/dev/null
  bash "$s" assert b || fail assert-after-prior
  [[ -z "$(bash "$s" assert c 2>/dev/null && echo ok)" ]] || fail assert-c-still-blocked
  bash "$s" done b >/dev/null
  bash "$s" assert c || fail assert-c-reachable
  bash "$s" assert zzz 2>/dev/null && fail assert-unknown-step
  [[ "$(r)" == "[default] a ✓ → b ✓ → c ●" ]] || fail "assert-unchanged-state: $(r)"
  bash "$s" clear; bash "$s" use default >/dev/null
  # strict ordering is OPT-IN: default keeps out-of-order `done` allowed (backward compatible) ...
  bash "$s" set a b >/dev/null; bash "$s" done b
  [[ "$(r)" == "[default] a ● → b ✓" ]] || fail "default-allows-out-of-order: $(r)"
  # ... and STEP_STATUS_STRICT_ORDER=1 refuses it, then admits the ordered path
  STEP_STATUS_STRICT_ORDER=1 bash "$s" set a b >/dev/null
  STEP_STATUS_STRICT_ORDER=1 bash "$s" done b 2>/dev/null && fail strict-refuses-out-of-order
  STEP_STATUS_STRICT_ORDER=1 bash "$s" done a || fail strict-ordered-a
  STEP_STATUS_STRICT_ORDER=1 bash "$s" done b || fail strict-ordered-b
  bash "$s" clear
  # single-active: `start` transfers active, never leaves two ●
  bash "$s" set a b; bash "$s" start b
  [[ "$(r)" == "[default] a ○ → b ●" ]] || fail "start-single-active: $(r)"
  bash "$s" set a a b 2>/dev/null && fail duplicate-accepted
  bash "$s" set $'a\tb' 2>/dev/null && fail tab-name-accepted
  bash "$s" set "" b 2>/dev/null && fail empty-name-accepted
  out="$(bash "$s" start 2>&1)"; [[ $? -ne 0 && "$out" != *unbound* ]] || fail start-no-arg
  bash "$s" done zzz 2>/dev/null && fail unknown-step-accepted
  bash "$s" --bogus 2>/dev/null && fail unknown-cmd-accepted
  bash "$s" clear; [[ -z "$(r)" ]] || fail clear
  printf 'active\tx\e[31mred\t\n' > "$d/default.state"; [[ "$(r)" == "[default] x[31mred ●" ]] || fail control-chars
  ln -sfn /dev/null "$d/default.state"; bash "$s" set p 2>/dev/null && fail symlink-followed
  rm -f "$d/default.state"
  # named chains: switch, keep both, notes, list, clear only the current one
  bash "$s" set a b; bash "$s" use pr >/dev/null; bash "$s" set x y z; bash "$s" done x
  bash "$s" note "reviewing PR 42"
  [[ "$(r)" == "[pr] x ✓ → y ● → z ○" ]] || fail named-render
  [[ "$(bash "$s" note)" == "reviewing PR 42" ]] || fail note-read
  [[ "$(bash "$s" use default)" == "[default] a ● → b ○" ]] || fail switch-back
  [[ "$(bash "$s" list)" == $'* [default] a ● → b ○\n  [pr] x ✓ → y ● → z ○  # reviewing PR 42' ]] || fail "list: $(bash "$s" list)"
  bash "$s" use pr >/dev/null; bash "$s" clear; [[ -z "$(r)" && ! -e "$d/pr.note" ]] || fail named-clear
  [[ -f "$d/default.state" ]] || fail clear-scoped
  bash "$s" use ../evil 2>/dev/null && fail bad-chain-name
  # inter-session comms land on the active step
  bash "$s" use default >/dev/null; bash "$s" set ask wait >/dev/null; bash "$s" msg sent RCM-info "need diagnostics" >/dev/null
  [[ "$(r)" == "[default] ask|⇢ RCM-info: need diagnostics ● → wait ○" ]] || fail "msg-sent: $(r)"
  bash "$s" done ask >/dev/null; bash "$s" msg recv RCM-info >/dev/null
  [[ "$(r)" == "[default] ask ✓ → wait|⇠ RCM-info ●" ]] || fail "msg-recv: $(r)"
  bash "$s" msg bogus x 2>/dev/null && fail msg-bad-direction
  # `set --name` names the chain in one command
  bash "$s" set --name build compile test >/dev/null
  [[ "$(r)" == "[build] compile ● → test ○" ]] || fail "named-set: $(r)"
  bash "$s" set --name '../evil' x 2>/dev/null && fail named-set-bad-name
  # a hostile `current` file must fall back to `default`, never traverse out of $DIR
  bash "$s" use default >/dev/null; bash "$s" set fallback >/dev/null
  printf 'active\tSECRET\t\n' > "$d/../evil.state"; printf '../evil' > "$d/current"
  [[ "$(r)" == "[default] fallback ●" ]] || fail current-traversal-read
  bash "$s" set p >/dev/null; [[ "$(cat "$d/../evil.state")" == $'active\tSECRET\t' ]] || fail current-traversal-write
  rm -f "$d/../evil.state" "$d/current"
  bash "$s" clear
  # loops: cycle brackets a segment (the named steps) with ↻N; steps outside stay untouched;
  # fresh set resets to pass 1; clear drops the counter
  bash "$s" use loop >/dev/null; bash "$s" set fetch check report >/dev/null
  bash "$s" done fetch >/dev/null
  [[ "$(r)" == "[loop] fetch ✓ → check ● → report ○" ]] || fail "cycle-pre: $(r)"
  [[ "$(bash "$s" cycle fetch check)" == "[loop] [fetch ● → check ○ ↻2] → report ○" ]] || fail "cycle-2: $(r)"
  bash "$s" done fetch >/dev/null; bash "$s" done check >/dev/null   # loop body done → report auto-active
  [[ "$(r)" == "[loop] [fetch ✓ → check ✓ ↻2] → report ●" ]] || fail "cycle-report-active: $(r)"
  # cycle with report ● must clear that stray active (single-active invariant)
  [[ "$(bash "$s" cycle)" == "[loop] [fetch ● → check ○ ↻3] → report ○" ]] || fail "cycle-clears-stray-active: $(r)"
  bash "$s" cycle zzz 2>/dev/null && fail cycle-unknown-step
  [[ "$(bash "$s" set fetch check report)" == "[loop] fetch ● → check ○ → report ○" ]] || fail "cycle-reset: $(r)"
  bash "$s" cycle 2>/dev/null && fail cycle-noargs-nobody                # fresh set: no stored body to reuse
  # non-contiguous body brackets the whole span (documented ceiling); garbage/huge counters are neutralised
  bash "$s" cycle fetch report 2>/dev/null && fail cycle-noncontiguous-accepted
  printf 'abc\nzzz\n' > "$d/loop.cycle"; [[ "$(r)" == "[loop] fetch ● → check ○ → report ○" ]] || fail "cycle-garbage-ignored: $(r)"
  printf '99999999999999999999\nfetch\n' > "$d/loop.cycle"; bash "$s" cycle >/dev/null
  [[ "$(r)" == "[loop] [fetch ● ↻100000] → check ○ → report ○" ]] || fail "cycle-overflow: $(r)"
  printf '08\nfetch\n' > "$d/loop.cycle"; [[ "$(bash "$s" cycle)" == "[loop] [fetch ● ↻9] → check ○ → report ○" ]] || fail "cycle-octal: $(r)"
  rm -f "$d/loop.state"; bash "$s" render 2>&1 | grep -q . && fail cycle-without-state-noise   # stale .cycle alone renders nothing, quietly
  : > "$d/loop.state"; bash "$s" cycle fetch 2>/dev/null && fail cycle-empty-state
  bash "$s" set fetch check report >/dev/null
  # set --name with bad steps must not switch `current`; planted dirs are refused, not written into
  bash "$s" use default >/dev/null; bash "$s" set --name other x x 2>/dev/null && fail dup-accepted
  [[ "$(cat "$d/current")" == default ]] || fail "set-name-partial-switch: $(cat "$d/current")"
  mkdir "$d/dirchain.state"; bash "$s" use dirchain >/dev/null; bash "$s" set a 2>/dev/null && fail dir-state-accepted
  [[ -z "$(ls -A "$d/dirchain.state")" ]] || fail dir-state-written-into
  rmdir "$d/dirchain.state"; bash "$s" use loop >/dev/null
  bash "$s" done fetch >/dev/null                                    # single-step loop body
  bash "$s" cycle check >/dev/null; [[ -f "$d/loop.cycle" ]] || fail cycle-file
  [[ "$(r)" == "[loop] fetch ✓ → [check ● ↻2] → report ○" ]] || fail "cycle-single: $(r)"
  [[ "$(bash "$s" list)" == *"[check ● ↻2]"* ]] || fail "list-shows-loop: $(bash "$s" list)"
  bash "$s" clear; [[ ! -e "$d/loop.cycle" ]] || fail cycle-clear
  bash "$s" use default >/dev/null
  # a symlinked state DIR is refused by clear/list too (clear would rm through it)
  local sd="$root/symdir" tgt="$root/tgt"; mkdir -p "$tgt"; : > "$tgt/default.state"; ln -sfn "$tgt" "$sd"
  STEP_STATUS_DIR="$sd" bash "$s" clear 2>/dev/null && fail clear-through-symlink-dir
  [[ -e "$tgt/default.state" ]] || fail clear-deleted-through-symlink
  STEP_STATUS_DIR="$sd" bash "$s" list 2>/dev/null && fail list-through-symlink-dir
  # symlinked state artifacts are refused, never written through
  local out2="$d/../outside2"; : > "$out2"
  ln -sfn "$out2" "$d/current"; bash "$s" use victim >/dev/null 2>&1     # symlink → external file
  [[ -s "$out2" ]] && fail current-symlink-file-write
  local outd="$d/../outside2dir"; mkdir -p "$outd"
  ln -sfn "$outd" "$d/current"; bash "$s" use victim >/dev/null 2>&1     # symlink → external dir
  [[ -n "$(ls -A "$outd")" ]] && fail current-symlink-dir-write
  rm -rf "$out2" "$outd" "$d/current"
  bash "$s" use default >/dev/null
  # foreign *.state filename with control bytes is skipped by list, not printed
  printf 'active\tx\t\n' > "$d/$(printf 'ev\033il').state"
  [[ "$(bash "$s" list)" != *$'\033'* ]] || fail list-control-bytes
  [[ "$(cat "$d/.gitignore")" == "*" ]] || fail gitignore
  # a finished chain (no active step, not a loop) expires DONE_TTL after its last update
  local old; old="$(date -v-2M +%Y%m%d%H%M 2>/dev/null || date -d '-2 min' +%Y%m%d%H%M)"
  age() { touch -t "$old" "$d/$1.state"; }
  bash "$s" set --name ttl a b >/dev/null; bash "$s" done a >/dev/null; bash "$s" done b >/dev/null
  [[ "$(r)" == "[ttl] a ✓ → b ✓" ]] || fail "ttl-fresh-finished-shows: $(r)"
  age ttl; [[ -z "$(r)" ]] || fail "ttl-finished-expires: $(r)"
  [[ "$(bash "$s" list)" == *"[ttl] a ✓ → b ✓"* ]] || fail "ttl-list-keeps-history"
  [[ "$(bash "$s" use ttl)" == "[ttl] a ✓ → b ✓ (finished)" ]] || fail "use-expired-says-finished: $(bash "$s" use ttl)"
  bash "$s" done b 2>/dev/null && fail "edit-of-expired-accepted"                      # refused, and no revive
  [[ -z "$(r)" ]] || fail "expired-revived: $(r)"
  [[ "$(bash "$s" start a 2>&1)" == *"finished — start the next workflow with: steps.sh set --name"* ]] || fail "expired-hint"
  [[ "$(bash "$s" set x y)" == "[default] x ● → y ○" ]] || fail "bare-set-after-finished-starts-default: $(r)"
  [[ "$(bash "$s" list)" == *"[ttl] a ✓ → b ✓"* ]] || fail "bare-set-kept-old-chain"
  # stopping at a ✗ is finished too; so is finishing the last step past a skipped one
  bash "$s" set --name ttl a b c >/dev/null; bash "$s" fail a >/dev/null; age ttl
  [[ -z "$(r)" ]] || fail "ttl-failed-counts-as-finished: $(r)"
  bash "$s" set --name ttl a b c >/dev/null; bash "$s" done a >/dev/null; bash "$s" start c >/dev/null; bash "$s" done c >/dev/null
  [[ "$(r)" == "[ttl] a ✓ → b ○ → c ✓" ]] || fail "forward-only-advance: $(r)"               # b skipped, not re-opened
  age ttl; [[ -z "$(r)" ]] || fail "skipped-step-still-finishes: $(r)"
  # an active step, or a loop between passes, never expires
  bash "$s" set --name ttl a b >/dev/null; bash "$s" done a >/dev/null; age ttl
  [[ "$(r)" == "[ttl] a ✓ → b ●" ]] || fail "ttl-unfinished-never-expires: $(r)"
  bash "$s" set --name mon init poll >/dev/null; bash "$s" done init >/dev/null; bash "$s" cycle poll >/dev/null
  bash "$s" done poll >/dev/null; age mon
  [[ "$(r)" == "[mon] init ✓ → [poll ✓ ↻2]" ]] || fail "loop-never-expires: $(r)"
  bash "$s" clear; bash "$s" use ttl >/dev/null
  # a last row without a newline still counts; the TTL is configurable and base 10
  printf 'done\ta\t\nactive\tb\t' > "$d/ttl.state"; age ttl
  [[ "$(r)" == "[ttl] a ✓ → b ●" ]] || fail "last-row-without-newline: $(r)"
  printf 'done\ta\t\ndone\tb\t\n' > "$d/ttl.state"; age ttl
  [[ "$(STEP_STATUS_DONE_TTL=100000 bash "$s" render)" == "[ttl] a ✓ → b ✓" ]] || fail "ttl-configurable"
  [[ -z "$(STEP_STATUS_DONE_TTL=08 bash "$s" render 2>&1)" ]] || fail "ttl-leading-zero"
  bash "$s" clear; bash "$s" use default >/dev/null
  rm -rf "$root"; echo "selfcheck OK"
}

main() {
  local cmd="${1:-render}"; shift || true
  case "$cmd" in
    set)   if [[ "${1-}" == --name || "${1-}" == -n ]]; then
             need_name "${2-}" "set --name" && check_steps "${@:3}" && switch_chain "$2" || return $?; shift 2   # validate before touching `current'
           elif expired "$STATE" && [[ "$CHAIN" != default ]]; then   # bare set after a finished chain: keep it, start `default`
             check_steps "$@" && switch_chain default || return $?
           fi
           set_chain "$@" && render ;;
    start) need_name "${1-}" start && valid_name "${2-x}" && update "$1" active "${2-}" && render ;;
    done)  need_name "${1-}" done && update "$1" done && render ;;
    fail)  need_name "${1-}" fail && update "$1" failed && render ;;
    assert) need_name "${1-}" assert && assert_step "$1" ;;
    cycle) cycle_chain "$@" && render ;;
    msg)   msg_step "$@" && render ;;
    render) render ;;
    clear) safe_state || return 1; rm -f "$STATE" "$NOTE" "$DIR/$CHAIN.cycle" ;;
    use)   need_name "${1-}" use && use_chain "$1" ;;
    list)  list_chains ;;
    note)  note_chain "$@" ;;
    --selfcheck) selfcheck ;;
    -h|--help) sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *) echo "steps.sh: unknown command '$cmd'" >&2; return 2 ;;
  esac
}
main "$@"
