#!/usr/bin/env bash
# Wire (or unwire) the workflow-tracker status line in Claude Code settings.json.
# Plugins cannot set statusLine, so this is the one manual step (opt-in, run by the user).
#
#   wire_statusline.sh            # wrap an existing statusLine command, or install standalone
#   wire_statusline.sh --unwire   # restore the previous statusLine exactly / remove the standalone entry
#   wire_statusline.sh --selfcheck
#
# Auto-detect: an existing command (e.g. `npx -y ccstatusline@latest`) is wrapped so its
# output stays and the step chain is appended; no command → the chain is the status line.
# The original statusLine object is saved to $STEP_STATUS_HOME/prev-statusline.json and
# restored wholesale on --unwire. Also registers the SessionStart and UserPromptSubmit hooks
# unless STEP_STATUS_NO_HOOK=1 (plugin installs already ship them via hooks/hooks.json).
# Plugin installs live in a versioned cache dir that changes on update, so in that case the
# scripts are copied to $STEP_STATUS_HOME/bin and settings point there (re-run after updates).
# Settings path: $CLAUDE_SETTINGS (default ~/.claude/settings.json). Never rewrites a file it
# could not parse.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
HOME_DIR="${STEP_STATUS_HOME:-$HOME/.claude/step-status}"
case "${1-}" in
  "") MODE=wire ;; --unwire) MODE=unwire ;; --selfcheck) MODE=selfcheck ;;
  *) echo "usage: wire_statusline.sh [--unwire|--selfcheck]" >&2; exit 2 ;;
esac

selfcheck() {
  local d s; d="$(mktemp -d)"; s="${BASH_SOURCE[0]}"
  export STEP_STATUS_HOME="$d/home" STEP_STATUS_NO_HOOK=0
  fail() { echo "FAIL $1"; cat "$d"/*.json 2>/dev/null; exit 1; }
  # 1. malformed settings are never rewritten
  printf '{ "model": "opus", broken' > "$d/bad.json"; cp "$d/bad.json" "$d/bad.before"
  CLAUDE_SETTINGS="$d/bad.json" bash "$s" >/dev/null 2>&1 && fail "malformed accepted"
  cmp -s "$d/bad.json" "$d/bad.before" || fail "malformed rewritten"
  # 2. wrap → idempotent → unwire is an exact round trip (padding preserved)
  printf '{"permissions":{"allow":["Bash"]},"statusLine":{"type":"command","command":"echo X","padding":1}}' > "$d/s.json"; cp "$d/s.json" "$d/s.before"
  export CLAUDE_SETTINGS="$d/s.json"
  bash "$s" | grep -q wrapped || fail wrap
  bash "$s" | grep -q "already wired" || fail idempotent
  python3 -c "import json;d=json.load(open('$d/s.json'));assert d['statusLine']['command'].endswith(\"-- 'echo X'\");assert d['permissions']=={'allow':['Bash']};assert len(d['hooks']['SessionStart'])==1;ups=d['hooks']['UserPromptSubmit'];assert len(ups)==1 and ups[0]['hooks'][0]['command'].endswith('hook_prompt.sh\"') and 'matcher' not in ups[0]" || fail wrapped-shape
  bash "$s" --unwire | grep -q restored || fail unwire
  python3 -c "import json,sys;a=json.load(open('$d/s.json'));b=json.load(open('$d/s.before'));sys.exit(a!=b)" || fail round-trip
  # 2b. a non-object entry in a hook-event list must not crash --unwire (greptile #5)
  HERE="$HERE" python3 -c "import json,os;json.dump({'statusLine':{'type':'command','command':'bash \"'+os.environ['HERE']+'/statusline.sh\"'},'hooks':{'SessionStart':['junk',{'hooks':[]}]}},open('$d/n.json','w'))"
  export CLAUDE_SETTINGS="$d/n.json"
  bash "$s" --unwire >/dev/null 2>&1 || fail unwire-nonobject-crash
  python3 -c "import json;d=json.load(open('$d/n.json'));assert 'junk' in d['hooks']['SessionStart']" || fail unwire-nonobject-preserved
  # 2c. PenguPool owns hooks on the same events; wiring and unwiring must preserve them exactly.
  python3 -c "import json;cmd='python -m pengupool.context';e={'hooks':[{'type':'command','command':cmd}]};json.dump({'hooks':{'SessionStart':[e],'UserPromptSubmit':[e]}},open('$d/p.json','w'))"
  cp "$d/p.json" "$d/p.before"; export CLAUDE_SETTINGS="$d/p.json"
  bash "$s" >/dev/null || fail pengupool-coexist-wire
  python3 -c "import json;d=json.load(open('$d/p.json'));assert len(d['hooks']['SessionStart'])==2 and len(d['hooks']['UserPromptSubmit'])==2;assert sum('pengupool.context' in h.get('command','') for ev in ('SessionStart','UserPromptSubmit') for g in d['hooks'][ev] for h in g.get('hooks',[]))==2" || fail pengupool-coexist-shape
  bash "$s" --unwire >/dev/null || fail pengupool-coexist-unwire
  python3 -c "import json;d=json.load(open('$d/p.json'));b=json.load(open('$d/p.before'));assert d==b" || fail pengupool-coexist-round-trip
  # 2d. a pre-rename (step-status) install elsewhere is "foreign": refuse, never nest two tickers
  printf '{"statusLine":{"type":"command","command":"bash \"/elsewhere/step-status/bin/statusline.sh\""}}' > "$d/f.json"
  CLAUDE_SETTINGS="$d/f.json" bash "$s" >/dev/null 2>&1 && fail foreign-step-status-accepted
  # 2e. a pre-rename symlink install of THIS repo (…/step-status/scripts/) is migrated, not stranded
  LEG="${HERE/\/workflow-tracker\/scripts//step-status/scripts}"
  LEG="$LEG" python3 -c "import json,os;L=os.environ['LEG'];json.dump({'statusLine':{'type':'command','command':'bash \"'+L+'/statusline.sh\" -- \'echo X\'','padding':0},'hooks':{'SessionStart':[{'matcher':'startup|clear','hooks':[{'type':'command','command':'bash \"'+L+'/hook_session_start.sh\"','timeout':10}]}]}},open('$d/l.json','w'))"
  export CLAUDE_SETTINGS="$d/l.json"
  bash "$s" | grep -q migrated || fail legacy-wire-migrate
  grep -q step-status/scripts "$d/l.json" && fail legacy-path-left
  bash "$s" --unwire | grep -q "restored status line: echo X" || fail legacy-unwire
  python3 -c "import json;d=json.load(open('$d/l.json'));assert d['statusLine']['command']=='echo X' and 'hooks' not in d" || fail legacy-unwire-shape
  # 3. standalone → unwire removes the key; hooks:null tolerated
  printf '{"hooks":null}' > "$d/t.json"; export CLAUDE_SETTINGS="$d/t.json"
  bash "$s" | grep -q standalone || fail standalone
  bash "$s" --unwire | grep -q removed || fail standalone-unwire
  [[ "$(python3 -c "import json;print(json.load(open('$d/t.json')))")" == "{}" ]] || fail standalone-clean
  # 4. wrapped mode output: inner output on its own line, chain appended
  export STEP_STATUS_DIR="$d/proj/.step-status"; mkdir -p "$d/proj"
  bash "$HERE/steps.sh" set a b >/dev/null
  out="$(printf '{"workspace":{"current_dir":"%s"}}' "$d/proj" | bash "$HERE/statusline.sh" -- 'printf abc')"
  [[ "$out" == $'abc\n[default] a ● → b ○' ]] || fail "wrapped output: $out"
  [[ -z "$(echo '{}' | bash "$HERE/statusline.sh")" ]] || fail "no-cwd should print nothing"
  rm -rf "$d"; echo "selfcheck OK"
}
[[ "$MODE" == selfcheck ]] && { selfcheck; exit 0; }

# Plugin cache dirs are versioned; copy scripts somewhere stable and wire that.
# A plugin install already ships the SessionStart hook via hooks/hooks.json, so never
# also add it to settings.json here (would duplicate) — force NO_HOOK in this branch.
if [[ "$HERE" == */plugins/cache/* ]]; then
  mkdir -p "$HOME_DIR/bin" && cp "$HERE"/steps.sh "$HERE"/statusline.sh "$HERE"/capture_context.py "$HERE"/hook_session_start.sh "$HERE"/hook_prompt.sh "$HOME_DIR/bin/"
  HERE="$HOME_DIR/bin"
  STEP_STATUS_NO_HOOK=1
fi
mkdir -p "$HOME_DIR"

HERE="$HERE" SETTINGS="$SETTINGS" MODE="$MODE" PREV="$HOME_DIR/prev-statusline.json" NO_HOOK="${STEP_STATUS_NO_HOOK:-0}" python3 - <<'PY'
import json, os, shlex, sys
settings, here, mode, prev = (os.environ[k] for k in ("SETTINGS", "HERE", "MODE", "PREV"))
data = {}
if os.path.exists(settings):
    with open(settings) as f: raw = f.read()
    if raw.strip():
        try: data = json.loads(raw)
        except Exception as e: sys.exit(f"workflow-tracker: refusing to rewrite {settings}: not valid JSON ({e})")
if not isinstance(data, dict): sys.exit(f"workflow-tracker: {settings} is not a JSON object")
hooks = data.get("hooks")
if not isinstance(hooks, dict): hooks = {}
data["hooks"] = hooks
# Pre-rename symlink installs point at <repo>/step-status/scripts/: that's this install too, so
# rewrite those paths to the new dir first — wire re-points them, unwire can then remove them.
legacy = here.replace("/workflow-tracker/scripts", "/step-status/scripts")
migrated = 0
if legacy != here:
    def mig(cmd):
        global migrated
        new = cmd.replace(f'"{legacy}/', f'"{here}/')
        migrated += new != cmd
        return new
    if isinstance(data.get("statusLine"), dict) and isinstance(data["statusLine"].get("command"), str):
        data["statusLine"]["command"] = mig(data["statusLine"]["command"])
    for g in (hooks.get("SessionStart") or []) if isinstance(hooks.get("SessionStart"), list) else []:
        for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
            if isinstance(h, dict) and isinstance(h.get("command"), str): h["command"] = mig(h["command"])
def ours(cmd, script): return f'"{here}/{script}"' in cmd          # exactly this install (AGENTS.md: only touch our own entries)
def foreign(cmd): return ("workflow-tracker" in cmd or "step-status" in cmd) and "statusline.sh" in cmd and not ours(cmd, "statusline.sh")  # pre-rename installs still say step-status
sl = data.get("statusLine") if isinstance(data.get("statusLine"), dict) else None
old = (sl or {}).get("command") or ""
sl_script = f'bash "{here}/statusline.sh"'
notes = [f"migrated {migrated} step-status path(s)"] if migrated else []
# the two hooks a settings.json install must register (a plugin ships these via hooks.json instead)
HOOK_SCRIPTS = ("hook_session_start.sh", "hook_prompt.sh")
def ours_hook(cmd): return any(ours(cmd, s) for s in HOOK_SCRIPTS)
if mode == "wire":
    if ours(old, "statusline.sh"):
        notes.append("already wired")
    elif foreign(old):
        sys.exit(f"workflow-tracker: statusLine is already wired by another workflow-tracker install ({old}); run --unwire there first")
    else:
        with open(prev, "w") as f: json.dump(sl, f)      # None when there was no statusLine
        if old:
            data["statusLine"] = {"type": "command", "command": f"{sl_script} -- {shlex.quote(old)}", "padding": 0}
            notes.append(f"wrapped existing status line ({old})")
        else:
            data["statusLine"] = {"type": "command", "command": sl_script, "padding": 0}
            notes.append("standalone (no existing status line found)")
    if os.environ["NO_HOOK"] != "1":
        def wire_hook(event, script, matcher):
            groups = hooks.get(event) if isinstance(hooks.get(event), list) else []
            hooks[event] = groups
            if any(ours(h.get("command") or "", script) for g in groups if isinstance(g, dict) for h in g.get("hooks", [])):
                # Upgrade the matcher of our existing hook without duplicating it.
                for group in groups:
                    if isinstance(group, dict) and any(ours(h.get("command") or "", script) for h in group.get("hooks", [])):
                        if matcher:
                            group["matcher"] = matcher
                return
            entry = {"hooks": [{"type": "command", "command": f'bash "{here}/{script}"', "timeout": 10}]}
            if matcher: entry = {"matcher": matcher, **entry}
            groups.append(entry)
            notes.append(f"{event} hook added")
        wire_hook("SessionStart", "hook_session_start.sh", "startup|resume|clear|compact")
        wire_hook("UserPromptSubmit", "hook_prompt.sh", None)
else:
    removed = 0
    for ev in list(hooks):
        kept_groups = []
        for g in hooks[ev] if isinstance(hooks[ev], list) else []:
            if not isinstance(g, dict): kept_groups.append(g); continue   # leave foreign entries untouched
            kept = [h for h in g.get("hooks", []) if not (isinstance(h, dict) and ours_hook(h.get("command") or ""))]
            removed += len(g.get("hooks", [])) - len(kept)
            if kept: g["hooks"] = kept; kept_groups.append(g)
        if kept_groups: hooks[ev] = kept_groups
        else: del hooks[ev]
    if removed: notes.append(f"removed {removed} hook(s)")
    if ours(old, "statusline.sh"):
        saved = None
        if os.path.exists(prev):
            with open(prev) as f: saved = json.load(f)
            os.remove(prev)
        if saved:
            data["statusLine"] = saved; notes.append("restored status line: " + str(saved.get("command")))
        else:
            parts = shlex.split(old)
            if "--" in parts and parts.index("--") + 1 < len(parts):
                data["statusLine"]["command"] = parts[parts.index("--") + 1]; notes.append("restored status line: " + data["statusLine"]["command"])
            else:
                data.pop("statusLine", None); notes.append("removed standalone status line")
    else:
        notes.append("no workflow-tracker status line found")
if not hooks: data.pop("hooks", None)
os.makedirs(os.path.dirname(settings) or ".", exist_ok=True)
tmp = settings + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2); f.write("\n")
os.replace(tmp, settings)
print("workflow-tracker: " + "; ".join(notes))
PY
