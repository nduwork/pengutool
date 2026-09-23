#!/usr/bin/env bash
# What has landed since the last release, and whether a release is warranted.
# Read-only: no commits, no tags, no file writes. Single source of truth for the next version
# and for the "docs/chore-only changes don't need a release" rule.
#
# Usage: scripts/release-status.sh [patch|minor|major|X.Y.Z] [--porcelain]
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

worthy=false
if [ "$feats" -gt 0 ] || [ "$fixes" -gt 0 ] || [ "$breaking" -gt 0 ] || [ "$unrel" -gt 0 ]; then worthy=true; fi

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
  printf 'current=%s\nlast=%s\nworthy=%s\nnext=%s\nfeats=%s\nfixes=%s\nbreaking=%s\n' \
    "$cur" "${last:-}" "$worthy" "$next" "$feats" "$fixes" "$breaking"
  exit 0
fi

n=0; [ -n "$subjects" ] && n=$(grep -c '' <<<"$subjects")
echo "current version : $cur"
echo "last release    : ${last:-none}"
echo "commits since   : $n  (feat:$feats  fix/perf:$fixes  breaking:$breaking)"
echo
[ -n "$subjects" ] && git log --no-merges --pretty='  %h %s' ${range:-HEAD}
echo
if [ "$worthy" = true ]; then
  echo "→ release-worthy. proposed next: v$next"
  echo "  cut it with:  scripts/release.sh        (or /release)"
else
  echo "→ only docs/chore changes since ${last:-start}. No release needed."
  echo "  force anyway: scripts/release.sh --allow-empty"
fi
