#!/usr/bin/env bash
#
# update.sh — Update Local Audio Transcriber to the latest published release.
#
# Ivan runs this (one command) whenever a fix is available. It:
#   1. asks GitHub for the latest release
#   2. if newer than what's installed, downloads it
#   3. backs up the current app, swaps in the new files
#   4. re-runs the (idempotent) installer
# His virtualenv, settings, and downloaded speech models are left in place.
#
# Pass --force to reinstall even if already up to date.
#
set -euo pipefail

REPO="onthegosnow/local-audio-transcriber"
API="https://api.github.com/repos/${REPO}/releases/latest"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

FORCE=0
for a in "$@"; do [[ "$a" == "--force" ]] && FORCE=1; done

command -v curl >/dev/null 2>&1 || { echo "ERROR: 'curl' is required." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: 'python3' is required." >&2; exit 1; }

current="$(python3 - <<'PY'
import re
try:
    s = open("transcriber/__init__.py").read()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', s)
    print(m.group(1) if m else "0.0.0")
except Exception:
    print("0.0.0")
PY
)"
echo "==> Installed version: v${current}"

echo "==> Checking GitHub for the latest release…"
json="$(curl -fsSL -H 'Accept: application/vnd.github+json' "$API" 2>/dev/null || true)"
if [[ -z "$json" ]]; then
    echo "ERROR: could not reach GitHub. Check your internet connection." >&2
    exit 1
fi

# Parse tag + the .tar.gz asset URL with python3 (robust; no jq needed).
read -r latest asset_url < <(python3 - "$json" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    print("_ERR_ _ERR_"); raise SystemExit
tag = (d.get("tag_name") or "").lstrip("v")
url = ""
for a in d.get("assets", []):
    if a.get("name", "").endswith(".tar.gz"):
        url = a.get("browser_download_url", "")
        break
print(tag or "_ERR_", url or "_ERR_")
PY
)

if [[ "$latest" == "_ERR_" || -z "$latest" ]]; then
    echo "ERROR: no published release found for ${REPO} yet." >&2
    exit 1
fi
echo "==> Latest release:  v${latest}"

ver_ge() {  # returns 0 if $1 >= $2  (dotted numeric compare)
    python3 - "$1" "$2" <<'PY'
import sys
def t(v): return tuple(int(''.join(c for c in p if c.isdigit()) or 0) for p in v.split('.'))
sys.exit(0 if t(sys.argv[1]) >= t(sys.argv[2]) else 1)
PY
}

if [[ $FORCE -eq 0 ]] && ver_ge "$current" "$latest"; then
    echo "==> You're already up to date (v${current}). Nothing to do."
    exit 0
fi

if [[ "$asset_url" == "_ERR_" || -z "$asset_url" ]]; then
    echo "ERROR: release v${latest} has no .tar.gz asset attached." >&2
    exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> Downloading v${latest}…"
curl -fSL --retry 3 -o "$tmp/update.tar.gz" "$asset_url"

echo "==> Extracting…"
tar -xzf "$tmp/update.tar.gz" -C "$tmp"
newroot="$(find "$tmp" -maxdepth 1 -mindepth 1 -type d | head -1)"
if [[ -z "$newroot" || ! -f "$newroot/install.sh" ]]; then
    echo "ERROR: the downloaded archive looks wrong (no install.sh inside)." >&2
    exit 1
fi

# Back up current app code (NOT the venv or downloaded models).
backup="$DIR/.backup-v${current}"
rm -rf "$backup"; mkdir -p "$backup"
for item in transcriber install.sh run.sh update.sh requirements.txt \
            local-transcriber.desktop.in icon.svg README.md QUICKSTART.txt; do
    [[ -e "$item" ]] && cp -R "$item" "$backup/" 2>/dev/null || true
done
echo "==> Backed up current version to: $backup"

echo "==> Installing v${latest}…"
cp -R "$newroot"/. "$DIR"/

echo "==> Running the installer to pick up any new dependencies…"
# exec so we stop reading THIS (now-overwritten) script and hand off cleanly.
exec ./install.sh
