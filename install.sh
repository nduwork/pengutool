#!/usr/bin/env bash
# One-command PenguPool install: the CLI, wiring for every installed harness (Claude Code, pi), the
# workflow tracker, and the editor extension (with its logo) in every VS Code / Cursor found.
#
#   curl -fsSL https://pengupool.nduwork.com/install.sh | bash
#   bash install.sh                      # from a checkout: installs that tree
#
# PENGUPOOL_REF=vX.Y.Z pins a release (default: the latest). HARNESS=cc|pi|both overrides detection.
# Needs uv, make and python3; tmux and the agent CLIs are offered by `pengupool setup` (y/N each).
set -euo pipefail
REPO=nduwork/pengutool
if [ ! -t 0 ] && (exec </dev/tty) 2>/dev/null; then exec </dev/tty; fi  # piped: keep y/N prompts on the terminal

need() { command -v "$1" >/dev/null || { echo "PenguPool needs $1: $2" >&2; exit 1; }; }
need python3 "install Python 3.11+"
need make "install Xcode Command Line Tools (macOS) or build-essential (Linux)"
if ! command -v uv >/dev/null; then  # user-space installer, no sudo
  echo "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
export PATH="$(uv tool dir --bin):$PATH"  # make install runs the freshly installed `pengupool` from here

# fetch <release asset|source> <tag> <dest>: gh's REST API when present (authenticated, so no anonymous
# rate limit; REST, because `gh release download` goes through GraphQL), else anonymous curl
fetch() {
  if command -v gh >/dev/null; then
    if [ "$1" = source ]; then gh api "repos/$REPO/tarball/$2" > "$3"
    else
      local url
      url=$(gh api "repos/$REPO/releases/tags/$2" --jq ".assets[] | select(.name == \"$1\") | .url")
      [ -n "$url" ] && gh api -H "Accept: application/octet-stream" "$url" > "$3"
    fi
  elif [ "$1" = source ]; then curl -fsSL "https://github.com/$REPO/archive/refs/tags/$2.tar.gz" -o "$3"
  else curl -fsSL "https://github.com/$REPO/releases/download/$2/$1" -o "$3"; fi
}

latest() {
  { command -v gh >/dev/null && gh api "repos/$REPO/releases/latest" --jq .tag_name 2>/dev/null; } ||
    curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' || true
}

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
here=""  # piped into bash there is no script file, so never a checkout (whatever the cwd)
[ -f "${BASH_SOURCE[0]:-}" ] && here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -z "${PENGUPOOL_REF:-}" ] && [ -n "$here" ] && [ -f "$here/Makefile" ] && [ -d "$here/pengupool" ]; then
  src=$here; ref=""
else
  ref=${PENGUPOOL_REF:-$(latest)}
  [ -n "$ref" ] || { echo "Could not find the latest PenguPool release (rate-limited? run 'gh auth login' or set PENGUPOOL_REF=vX.Y.Z)." >&2; exit 1; }
  echo "Installing PenguPool $ref"
  fetch source "$ref" "$tmp/src.tar.gz"
  mkdir "$tmp/src" && tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components 1
  src=$tmp/src
fi

make -C "$src" install HARNESS="${HARNESS:-auto}"

editors=${EDITOR_CLI:-}  # EDITOR_CLI picks one; unset = every VS Code / Cursor found (PATH or macOS app)
if [ -z "$editors" ]; then
  for pair in "code:Visual Studio Code" "cursor:Cursor"; do
    c=${pair%%:*}; app=${pair#*:}.app/Contents/Resources/app/bin/$c
    for e in "$(command -v "$c" || true)" "/Applications/$app" "$HOME/Applications/$app"; do
      if [ -n "$e" ] && [ -x "$e" ]; then editors+=$e$'\n'; break; fi
    done
  done
fi
if [ -z "$editors" ]; then
  echo "No VS Code or Cursor found: skipped the editor extension (install VS Code or Cursor, then re-run)."
  exit 0
fi
vsix=$tmp/pengupool.vsix
if [ -n "$ref" ] && fetch pengupool.vsix "$ref" "$vsix" 2>/dev/null; then :
elif command -v npm >/dev/null; then  # a checkout, or a release without the asset: build it
  (cd "$src/extension" && npm ci --silent && npm run --silent compile && npm run --silent package -- --out "$vsix" >/dev/null)
else
  echo "No prebuilt extension for ${ref:-this checkout} and no npm to build one: skipped the editor extension." >&2
  exit 0
fi
while IFS= read -r ed; do
  "$ed" --install-extension "$vsix" --force && echo "Extension installed in $ed"
done <<< "${editors%$'\n'}"
echo "Done. Reload your editor window, then open the PenguPool view (penguin icon in the Activity Bar)."
