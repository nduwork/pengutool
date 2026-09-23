#!/usr/bin/env bash
# statusLine command. Two modes, chosen by wire_statusline.sh:
#   bash statusline.sh -- '<inner command>'   run the previous status line, then append the step chain
#   bash statusline.sh                        standalone: print only the step chain
# Reads the statusLine JSON from stdin; the chain is looked up under workspace.current_dir.
# Never fails — a broken status line must not break the session.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT="$(cat 2>/dev/null || true)"

# Claude gives the status line an exact context percentage. Save only that number for PenguPool;
# the existing renderer below still receives the original JSON unchanged.
if [[ -n "$INPUT" && -f "$HERE/capture_context.py" ]]; then
  printf '%s' "$INPUT" | python3 "$HERE/capture_context.py" >/dev/null 2>&1 || true
fi

[[ "${1-}" == "--" ]] && shift
if [[ -n "${1-}" ]]; then
  INNER="$(printf '%s' "$INPUT" | bash -c "$1" 2>/dev/null || true)"
  [[ -n "$INNER" ]] && printf '%s\n' "$INNER"
fi

# Fail closed: without a cwd from the JSON, show nothing rather than another directory's chain.
CWD="$(STEP_INPUT="$INPUT" python3 -c 'import json,os
try: h=json.loads(os.environ["STEP_INPUT"] or "{}")
except Exception: h={}
print((h.get("workspace") or {}).get("current_dir") or h.get("cwd") or "")' 2>/dev/null || true)"
# run from the cwd (no STEP_STATUS_DIR override) so steps.sh resolves the shared main-worktree-root
# .step-status — a worktree session shows its repo's chain, not an empty per-worktree one
[[ -n "$CWD" ]] && ( cd "$CWD" 2>/dev/null && bash "$HERE/steps.sh" render 2>/dev/null )
exit 0
