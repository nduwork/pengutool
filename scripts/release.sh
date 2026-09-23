#!/usr/bin/env bash
# Cut a release from main. Usage: scripts/release.sh [patch|minor|major|X.Y.Z] [--dry-run] [--allow-empty]
#
#   1. main, clean, in sync with origin; tests pass
#   2. next version: explicit arg, or from conventional commits since the last tag
#      (feat: → minor, anything else → patch; first release uses pyproject's version as-is)
#   3. pyproject.toml version, uv.lock, CHANGELOG.md ("Unreleased" → "[X.Y.Z] - date"; if the
#      Unreleased section is empty, bullets are generated from commit subjects)
#   4. extension triage (release-status.sh): when a feat/fix touched extension/, bump
#      extension/package.json (+ lock) and add its CHANGELOG section; the project CHANGELOG names
#      the extension version the tag ships, so one tag = one matching backend + extension pair
#   5. commit "chore: release X.Y.Z", tag vX.Y.Z, push both → .github/workflows/release.yml publishes
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
DRY=0; WANT=""; ALLOW_EMPTY=0
for a in "$@"; do case "$a" in --dry-run) DRY=1;; --allow-empty) ALLOW_EMPTY=1;; *) WANT="$a";; esac; done

[ "$(git branch --show-current)" = main ] || { echo "release from main (on $(git branch --show-current))" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 1; }
git fetch origin main --quiet
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "main is not in sync with origin/main" >&2; exit 1; }

uv run pytest -q

# Version + worthiness come from one place (release-status.sh), so the docs-only rule cannot drift.
eval "$(scripts/release-status.sh ${WANT:+"$WANT"} --porcelain)"
if [ "$worthy" != true ] && [ -z "$WANT" ] && [ "$ALLOW_EMPTY" != 1 ]; then
  echo "Only docs/chore changes since ${last:-start} — nothing to release." >&2
  echo "Pass a version (e.g. patch) or --allow-empty to force." >&2
  exit 1
fi
git rev-parse -q --verify "refs/tags/v$next" >/dev/null && { echo "v$next already exists" >&2; exit 1; }
echo "current $current → next $next  (last tag: ${last:-none}); extension $ext_current → ${ext_next:-unchanged}"

# --- CHANGELOG: promote Unreleased, or synthesize from commits -----------------------------------
today=$(date +%F)
python3 - "$next" "$today" "$ext_next" "${last:-}" <<'PY'
import json, re, subprocess, sys
next_v, today, ext_next, last_tag = sys.argv[1:5]
p = "CHANGELOG.md"
try: text = open(p).read()
except FileNotFoundError:
    text = "# Changelog\n\nAll notable changes to this project are documented here. The format follows\n[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org).\n\n## [Unreleased]\n"
m = re.search(r"## \[Unreleased\]\n(.*?)(?=\n## \[|\Z)", text, re.S)
body = m.group(1).strip() if m else ""
if not body:
    last = subprocess.run(["bash", "-c", "git tag -l 'v[0-9]*' | sort -t. -k1,1V -k2,2n -k3,3n | tail -1"], capture_output=True, text=True).stdout.strip()
    rng = f"{last}..HEAD" if last else "HEAD"
    subs = subprocess.run(["git", "log", "--no-merges", "--pretty=%s", rng], capture_output=True, text=True).stdout.splitlines()
    groups = {"Added": [], "Fixed": [], "Changed": []}
    skip = ("chore", "docs", "ci", "build", "test", "style")
    for s in subs:
        if s.startswith("chore: release") or re.match(r"^(" + "|".join(skip) + r")(\([^)]*\))?!?:", s):
            continue
        k = "Added" if s.startswith("feat") else "Fixed" if s.startswith(("fix", "perf")) else "Changed"
        groups[k].append("- " + re.sub(r"^[a-z]+(\([^)]*\))?!?:\s*", "", s))
    body = "\n\n".join(f"### {k}\n" + "\n".join(v) for k, v in groups.items() if v) or "- Maintenance release."
if ext_next:
    body += f"\n\n- Editor extension: {ext_next} (install both with `install.sh`; they ship together in this tag)."
    # the extension's own version + changelog, only when it changed (a hand bump keeps its version)
    for f in ("extension/package.json", "extension/package-lock.json"):
        d = json.load(open(f))
        d["version"] = ext_next
        if "packages" in d:
            d["packages"][""]["version"] = ext_next
        open(f, "w").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    ec = "extension/CHANGELOG.md"
    etext = open(ec).read()
    if f"## {ext_next}\n" not in etext:
        rng = f"{last_tag}..HEAD" if last_tag else "HEAD"
        subs = subprocess.run(["git", "log", "--no-merges", "--pretty=%s", rng, "--", "extension/"],
                              capture_output=True, text=True).stdout.splitlines()
        lines = [re.sub(r"^[a-z]+(\([^)]*\))?!?:\s*", "- ", s) for s in subs if re.match(r"^(feat|fix|perf)", s)]
        entry = f"## {ext_next}\n\n" + ("\n".join(lines) or "- Maintenance release.") + f"\n\nShips with PenguPool v{next_v}.\n\n"
        head, sep, rest = etext.partition("\n## ")
        etext = head.rstrip("\n") + "\n\n" + entry + (("## " + rest) if sep else "")
        open(ec, "w").write(etext)
section = f"## [{next_v}] - {today}\n\n{body}\n"
if m:
    text = text[:m.start()] + "## [Unreleased]\n\n" + section + text[m.end():].lstrip("\n")
else:
    text = text.rstrip("\n") + "\n\n## [Unreleased]\n\n" + section
open(p, "w").write(text)
PY
# portable version bump (BSD sed lacks the GNU 0,/re/ address, which silently no-ops on macOS)
python3 -c 'import re,sys,pathlib; p=pathlib.Path("pyproject.toml"); t=p.read_text(); p.write_text(re.sub(r"(?m)^version = \"[^\"]+\"", "version = \"%s\"" % sys.argv[1], t, count=1))' "$next"
got=$(grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')
[ "$got" = "$next" ] || { echo "version bump failed: pyproject is $got, expected $next" >&2; exit 1; }
uv lock --quiet

if [ "$DRY" = 1 ]; then echo "--- dry run: would commit, tag v$next and push ---"; git --no-pager diff --stat; git checkout -q -- pyproject.toml uv.lock CHANGELOG.md extension; exit 0; fi
git add pyproject.toml uv.lock CHANGELOG.md extension/package.json extension/package-lock.json extension/CHANGELOG.md
git commit -q -m "chore: release $next"
git tag -a "v$next" -m "v$next"
git push --atomic -q origin main "v$next"  # both refs or neither: never a tag that main lacks
echo "released v$next → https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v$next (workflow publishes the wheel, source archive, VSIX, and notes)"
