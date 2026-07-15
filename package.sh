#!/usr/bin/env bash
#
# package.sh — Build a sendable release tarball of Local Audio Transcriber.
#
# Run this on the DEVELOPER machine (macOS or Linux). It produces:
#   dist/local-audio-transcriber-<version>.tar.gz
#   dist/local-audio-transcriber-<version>.tar.gz.sha256
#
# The archive unpacks into a single top-level directory so extraction is clean.
#
set -euo pipefail

# --- macOS AppleDouble / xattr suppression (must be set before any tar/cp) ---
export COPYFILE_DISABLE=1

# --- Locate ourselves / the repo root ---------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
cd "$SCRIPT_DIR"

APP_SLUG="local-audio-transcriber"
DEFAULT_VERSION="0.0.0"

# --- Read version from transcriber/__init__.py ------------------------------
VERSION=""
INIT_FILE="transcriber/__init__.py"
if [[ -f "$INIT_FILE" ]]; then
    # Tolerates:  __version__ = "x"   and   __version__: str = "x"
    VERSION="$(
        sed -n -E 's/^[[:space:]]*__version__[[:space:]]*(:[^=]*)?=[[:space:]]*["'"'"']([^"'"'"']+)["'"'"'].*/\2/p' \
            "$INIT_FILE" | head -n1
    )"
fi
if [[ -z "$VERSION" ]]; then
    echo "WARN: could not read __version__ from $INIT_FILE; using $DEFAULT_VERSION" >&2
    VERSION="$DEFAULT_VERSION"
fi

TOPDIR="${APP_SLUG}-${VERSION}"
DIST_DIR="dist"
TARBALL="${DIST_DIR}/${TOPDIR}.tar.gz"
CHECKSUM="${TARBALL}.sha256"

# --- Sanity: required files must exist --------------------------------------
REQUIRED=(
    "transcriber"
    "requirements.txt"
    "install.sh"
    "run.sh"
    "icon.svg"
    "local-transcriber.desktop.in"
    "README.md"
)
MISSING=0
for f in "${REQUIRED[@]}"; do
    if [[ ! -e "$f" ]]; then
        echo "ERROR: required path missing: $f" >&2
        MISSING=1
    fi
done
if [[ "$MISSING" -ne 0 ]]; then
    echo "Aborting: run this script from the repo root." >&2
    exit 1
fi

# --- Assemble a clean staging tree ------------------------------------------
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/${APP_SLUG}.stage.XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

STAGE_TOP="${STAGE}/${TOPDIR}"
mkdir -p "$STAGE_TOP/transcriber"

# Copy the WHOLE python package (excluding only caches/bytecode) so any future
# non-.py asset (templates, .qss, data files) ships too — don't whitelist *.py.
find transcriber -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0 \
    | while IFS= read -r -d '' src; do
        dest="${STAGE_TOP}/${src}"
        mkdir -p "$(dirname "$dest")"
        cp -p "$src" "$dest"
    done

# Copy the individual top-level files (+ optional QUICKSTART.txt).
COPY_FILES=(
    "requirements.txt"
    "install.sh"
    "run.sh"
    "update.sh"
    "icon.svg"
    "local-transcriber.desktop.in"
    "README.md"
)
[[ -f "QUICKSTART.txt" ]] && COPY_FILES+=("QUICKSTART.txt")
for f in "${COPY_FILES[@]}"; do
    cp -p "$f" "$STAGE_TOP/$f"
done

# --- Defensive scrub of anything unwanted that slipped in -------------------
find "$STAGE_TOP" \
    \( -name '__pycache__' -o -name '.git' -o -name '.venv' \
       -o -name 'smoke-venv' -o -name 'dist' \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_TOP" \( -name '*.pyc' -o -name '.DS_Store' -o -name '._*' \) -delete 2>/dev/null || true

# --- Ensure the scripts are executable inside the archive -------------------
chmod 0755 "$STAGE_TOP/install.sh" "$STAGE_TOP/run.sh"
[[ -f "$STAGE_TOP/update.sh" ]] && chmod 0755 "$STAGE_TOP/update.sh"

# Strip macOS extended attributes from the staging tree if xattr is available.
command -v xattr >/dev/null 2>&1 && xattr -rc "$STAGE_TOP" 2>/dev/null || true

# --- Build the tarball (portable across GNU and BSD tar) --------------------
mkdir -p "$DIST_DIR"
rm -f "$TARBALL" "$CHECKSUM"

TAR_FLAGS=()
if tar --version 2>/dev/null | grep -qi 'bsdtar'; then
    TAR_FLAGS+=("--no-mac-metadata" "--no-xattrs")   # BSD/libarchive tar (macOS)
else
    TAR_FLAGS+=("--no-xattrs")                        # GNU tar
fi

# Only fall back to a flagless tar if the metadata flags themselves were rejected
# — never mask a genuine failure (disk full, permissions, interrupted).
if ! err="$(tar "${TAR_FLAGS[@]}" -czf "$TARBALL" -C "$STAGE" "$TOPDIR" 2>&1)"; then
    if printf '%s' "$err" | grep -qiE 'no-mac-metadata|no-xattrs|unknown|invalid option'; then
        echo "WARN: tar rejected metadata flags; retrying without them." >&2
        tar -czf "$TARBALL" -C "$STAGE" "$TOPDIR"
    else
        printf '%s\n' "$err" >&2
        exit 1
    fi
fi

# --- Checksum ----------------------------------------------------------------
TARBALL_BASE="$(basename "$TARBALL")"
if command -v shasum >/dev/null 2>&1; then
    ( cd "$DIST_DIR" && shasum -a 256 "$TARBALL_BASE" > "$(basename "$CHECKSUM")" )
elif command -v sha256sum >/dev/null 2>&1; then
    ( cd "$DIST_DIR" && sha256sum "$TARBALL_BASE" > "$(basename "$CHECKSUM")" )
else
    echo "WARN: no shasum/sha256sum found; skipping checksum file." >&2
    CHECKSUM="(none)"
fi

# --- Human-readable size -----------------------------------------------------
if command -v du >/dev/null 2>&1; then
    SIZE="$(du -h "$TARBALL" | awk '{print $1}')"
else
    SIZE="$(wc -c < "$TARBALL" | awk '{printf "%d bytes", $1}')"
fi

ABS_TARBALL="$(cd "$(dirname "$TARBALL")" && pwd -P)/$(basename "$TARBALL")"

# --- Report ------------------------------------------------------------------
echo
echo "==========================================================="
echo " Built Local Audio Transcriber ${VERSION}"
echo "==========================================================="
echo " Archive : ${ABS_TARBALL}"
echo " Size    : ${SIZE}"
if [[ "$CHECKSUM" != "(none)" ]]; then
    echo " SHA256  : $(cd "$DIST_DIR" && cat "$(basename "$CHECKSUM")")"
fi
echo
echo " HOW TO SEND IT:"
echo "   Attach the .tar.gz to an email, or upload it to a cloud"
echo "   link (Google Drive / Dropbox) and send Ivan the link."
echo
echo " WHAT IVAN DOES (one time, in a terminal):"
echo "   tar -xzf ${TARBALL_BASE}"
echo "   cd ${TOPDIR}"
echo "   ./install.sh"
echo
echo " Then he launches it from the app menu, or with ./run.sh"
echo "==========================================================="
