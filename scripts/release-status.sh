#!/usr/bin/env bash
# What has landed since the last release, and whether a release is warranted.
# Read-only: no commits, no tags, no file writes. Single source of truth for the next version
# and for the "docs/chore-only changes don't need a release" rule.
#
# Usage: scripts/release-status.sh [patch|minor|major|X.Y.Z] [--porcelain]
#
# One tag versions the pair: vX.Y.Z (= pyproject) moves on ANY release-worthy change, core or
# extension, so an install from one tag always gets a matching backend + extension. The extension's
# own version (extension/package.json) moves only when a feat/fix commit touched extension/ — unless
# it was already bumped by hand since the last tag.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
PORCELAIN=0; WANT=""
for a in "$@"; do case "$a" in --porcelain) PORCELAIN=1;; *) WANT="$a";; esac; done

cur=$(grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')
last=$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' | sort -t. -k1,1V -k2,2n -k3,3n | tail -1 || true)
range=${last:+$last..HEAD}
subjects=$(git log --no-merges --pretty=%s ${range:-HEAD} 2>/dev/null || true)

# Only feat/fix/perf and breaking changes warrant a release. docs/chore/ci/build/test/style/refactor
# are not release-worthy on their own — a docs update does not need a new release.
feats=$(grep -cE '^feat(\(.*\))?!?:' <<<"$subjects" || true)
fixes=$(grep -cE '^(fix|perf)(\(.*\))?!?:' <<<"$subjects" || true)
breaking=$(grep -cE '^[a-z]+(\(.*\))?!:' <<<"$subjects" || true)
# Real content already curated under ## [Unreleased] also warrants a release.
unrel=$(awk '/^## \[Unreleased\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md 2>/dev/null | grep -cE '^- ' || true)

# Triage by what changed: release-worthy commits that touched extension/ bump the extension too.
ext_feats=0; ext_fixes=0
while read -r h subj; do
  [ -n "$h" ] || continue
  git diff-tree --no-commit-id --name-only -r "$h" | grep -q '^extension/' || continue
  if grep -qE '^feat(\(.*\))?!?:|^[a-z]+(\(.*\))?!:' <<<"$subj"; then ext_feats=$((ext_feats+1))
  elif grep -qE '^(fix|perf)(\(.*\))?!?:' <<<"$subj"; then ext_fixes=$((ext_fixes+1)); fi
done < <(git log --no-merges --pretty='%H %s' ${range:-HEAD} 2>/dev/null || true)
ext_ver() { python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'; }
ext_cur=$(ext_ver < extension/package.json)
ext_last=$( { [ -n "$last" ] && git show "$last:extension/package.json" 2>/dev/null | ext_ver; } || echo "")
ext_next=""
if [ "$ext_cur" != "$ext_last" ] && [ -n "$last" ]; then ext_next=$ext_cur   # already bumped by hand
elif [ -n "$last" ] && { [ "$ext_feats" -gt 0 ] || [ "$ext_fixes" -gt 0 ]; }; then  # first release ships it as-is
  IFS=. read -r emaj emin epat <<<"$ext_cur"
  if [ "$ext_feats" -gt 0 ]; then ext_next="$emaj.$((emin+1)).0"; else ext_next="$emaj.$emin.$((epat+1))"; fi
fi

worthy=false
if [ "$feats" -gt 0 ] || [ "$fixes" -gt 0 ] || [ "$breaking" -gt 0 ] || [ "$unrel" -gt 0 ] || [ -n "$ext_next" ]; then worthy=true; fi

if [[ "$WANT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then next="$WANT"
elif [ -z "$last" ]; then next="$cur"                       # first release: ship pyproject's version
else
  IFS=. read -r maj min pat <<<"$cur"
  case "${WANT:-auto}" in
    major) next="$((maj+1)).0.0";;
    minor) next="$maj.$((min+1)).0";;
    patch) next="$maj.$min.$((pat+1))";;
    auto)  if [ "$feats" -gt 0 ] || [ "$breaking" -gt 0 ]; then next="$maj.$((min+1)).0"; else next="$maj.$min.$((pat+1))"; fi;;
    *) echo "unknown bump '$WANT' (use patch|minor|major|X.Y.Z)" >&2; exit 1;;
  esac
fi

if [ "$PORCELAIN" = 1 ]; then
  printf 'current=%s\nlast=%s\nworthy=%s\nnext=%s\nfeats=%s\nfixes=%s\nbreaking=%s\next_current=%s\next_next=%s\n' \
    "$cur" "${last:-}" "$worthy" "$next" "$feats" "$fixes" "$breaking" "$ext_cur" "$ext_next"
  exit 0
fi

n=0; [ -n "$subjects" ] && n=$(grep -c '' <<<"$subjects")
echo "current version : $cur"
echo "last release    : ${last:-none}"
echo "commits since   : $n  (feat:$feats  fix/perf:$fixes  breaking:$breaking)"
echo "extension       : $ext_cur → ${ext_next:-unchanged}  (feat:$ext_feats  fix/perf:$ext_fixes touching extension/)"
echo
[ -n "$subjects" ] && git log --no-merges --pretty='  %h %s' ${range:-HEAD}
echo
if [ "$worthy" = true ]; then
  echo "→ release-worthy. proposed next: v$next (backend $next + extension ${ext_next:-$ext_cur}, one tag)"
  echo "  cut it with:  scripts/release.sh        (or /release)"
else
  echo "→ only docs/chore changes since ${last:-start}. No release needed."
  echo "  force anyway: scripts/release.sh --allow-empty"
fi
