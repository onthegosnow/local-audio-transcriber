#!/usr/bin/env bash
#
# release.sh — Publish a new version of Local Audio Transcriber (developer tool).
#
#   ./release.sh 1.0.1 "Fixed the thing Ivan reported"
#
# It bumps the version, builds the tarball, commits + tags, and creates a
# GitHub Release with the .tar.gz attached. Ivan's ./update.sh (and the in-app
# "Check for updates") then see it automatically.
#
# Requires: gh (GitHub CLI, logged in) and git. Run from the repo root.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VERSION="${1:-}"
NOTES="${2:-Release v${VERSION}}"
if [[ -z "$VERSION" ]]; then
    echo "Usage: ./release.sh <version> [\"release notes\"]" >&2
    echo "   e.g. ./release.sh 1.0.1 \"Fix Apple Pay transcription bug\"" >&2
    exit 1
fi

command -v gh  >/dev/null 2>&1 || { echo "ERROR: GitHub CLI 'gh' not found." >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "ERROR: 'git' not found." >&2; exit 1; }

echo "==> Bumping version to ${VERSION}…"
python3 - "$VERSION" <<'PY'
import re, sys
v = sys.argv[1]
p = "transcriber/__init__.py"
s = open(p).read()
s2 = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{v}"', s, count=1)
open(p, "w").write(s2)
print(f"    __version__ = \"{v}\"")
PY

echo "==> Building the release tarball…"
./package.sh >/dev/null
TARBALL="dist/local-audio-transcriber-${VERSION}.tar.gz"
[[ -f "$TARBALL" ]] || { echo "ERROR: expected $TARBALL was not built." >&2; exit 1; }

echo "==> Committing + tagging…"
git add -A
git commit -m "Release v${VERSION}" >/dev/null 2>&1 || echo "    (nothing new to commit)"
git tag -f "v${VERSION}" >/dev/null
git push origin HEAD >/dev/null 2>&1 || echo "    (push HEAD skipped)"
git push -f origin "v${VERSION}" >/dev/null 2>&1 || echo "    (push tag skipped)"

echo "==> Creating GitHub release v${VERSION}…"
if gh release view "v${VERSION}" >/dev/null 2>&1; then
    gh release upload "v${VERSION}" "$TARBALL" --clobber
else
    gh release create "v${VERSION}" "$TARBALL" --title "v${VERSION}" --notes "$NOTES"
fi

echo
echo "==> Published v${VERSION}."
echo "    Ivan updates by running:   ./update.sh"
echo "    (or via the app: Help → Check for updates…)"
