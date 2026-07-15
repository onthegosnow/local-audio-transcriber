#!/usr/bin/env bash
# One-command installer for Local Audio Transcriber (Ubuntu / Debian).
#
#   1. installs the system prerequisites a PySide6 GUI needs (python3-venv +
#      the Qt 'xcb' runtime libraries) via apt — only the ones actually missing
#   2. creates a Python virtualenv in ./.venv   (idempotent; self-healing)
#   3. installs the Python dependencies (faster-whisper, PySide6, requests)
#   4. runs a smoke test so problems surface NOW, not at first click
#   5. installs a per-user desktop launcher into the applications menu
#
# Nothing system-wide is touched except the apt prerequisites and the per-user
# .desktop entry. Safe to re-run. Pass --force to rebuild the virtualenv.
#
# If anything fails, reply with the file "install.log" written next to this
# script.
set -euo pipefail

# --------------------------------------------------------------------------- #
# 0. Locate ourselves (space-safe) and start logging                          #
# --------------------------------------------------------------------------- #
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

LOG="$DIR/install.log"
exec > >(tee "$LOG") 2>&1
TEE_PID=$!
# Flush the log fully before exiting (avoids losing the last lines on abrupt end).
trap 'ec=$?; exec 1>&- 2>&- || true; [ -n "${TEE_PID:-}" ] && wait "$TEE_PID" 2>/dev/null || true; exit $ec' EXIT

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force|--reinstall) FORCE=1 ;;
    esac
done

echo "==> Local Audio Transcriber installer"
echo "    (a full log is being written to: $LOG)"
echo

# --------------------------------------------------------------------------- #
# 1. Python preflight                                                         #
# --------------------------------------------------------------------------- #
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 was not found." >&2
    echo "       On Ubuntu/Debian:  sudo apt-get install -y python3" >&2
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
    echo "ERROR: Python 3.9 or newer is required (found $PYVER)." >&2
    echo "       Update Python, then re-run:  bash install.sh" >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# 2. System prerequisites (apt — only the ones actually missing)              #
# --------------------------------------------------------------------------- #
# Two of these truly break a clean install and MUST be present:
#   python3-venv   -> else `python3 -m venv` dies: 'ensurepip is not available'
#   libxcb-cursor0 -> else the Qt6.5+/PySide6 GUI dies: 'could not load the Qt
#                     platform plugin xcb' (absent by default on Ubuntu 24.04)
# The rest are xcb helper libs — normally present on a full desktop, listed for
# robustness on minimal installs. apt treats already-installed ones as no-ops.
APT_PKGS=(
    python3-venv          # virtualenv / ensurepip     (REQUIRED)
    python3-pip           # pip bootstrap fallback
    libxcb-cursor0        # Qt6 xcb platform plugin     (REQUIRED on 24.04)
    libxkbcommon-x11-0
    libxcb-icccm4
    libxcb-image0
    libxcb-keysyms1
    libxcb-render-util0
    libxcb-randr0
    libxcb-shape0
    libxcb-xfixes0
    libxcb-xkb1
    libxcb-xinerama0
    libgl1
    libgomp1
)

# Work out which packages are not installed. dpkg-query "misses" must not abort
# us under `set -e`, hence the explicit guards.
missing=()
if command -v dpkg-query >/dev/null 2>&1; then
    for p in "${APT_PKGS[@]}"; do
        status="$(dpkg-query -W -f='${Status}' "$p" 2>/dev/null || true)"
        if ! printf '%s' "$status" | grep -q 'install ok installed'; then
            missing+=("$p")
        fi
    done
else
    missing=("${APT_PKGS[@]}")   # no dpkg — try/advise everything
fi

if [[ ${#missing[@]} -eq 0 ]]; then
    echo "==> All system prerequisites already installed."
    echo
elif command -v apt-get >/dev/null 2>&1; then
    echo "==> Missing system packages: ${missing[*]}"
    if command -v sudo >/dev/null 2>&1; then
        echo "==> Installing them now (you may be prompted for your password)."
        echo "    sudo is needed because Qt's system libraries live outside the venv."
        export DEBIAN_FRONTEND=noninteractive
        sudo apt-get update -qq || echo "    (apt-get update failed; trying install anyway)"
        if sudo apt-get install -y "${missing[@]}"; then
            echo "==> System prerequisites installed."
        else
            echo "    WARNING: some packages could not be installed automatically."
            echo "    If the app fails to open, run this manually:"
            echo "      sudo apt-get install -y ${missing[*]}"
        fi
        echo
    else
        echo "==> 'sudo' is not available. Install these manually, then re-run:"
        echo "      apt-get install -y ${missing[*]}"
        echo "    Continuing anyway (the GUI may not launch until they are present)."
        echo
    fi
else
    echo "==> This is not an apt-based system. Ensure the equivalents are installed:"
    echo "      ${missing[*]}"
    echo "    Continuing anyway."
    echo
fi

# Hard re-check: test the thing that actually goes missing (ensurepip), NOT the
# always-importable stdlib 'venv' module.
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
    echo "ERROR: python3 venv support (ensurepip) is still unavailable." >&2
    echo "       Install it and re-run:  sudo apt-get install -y python3-venv" >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# 3. Virtualenv (idempotent + self-healing)                                   #
# --------------------------------------------------------------------------- #
VENV="$DIR/.venv"
FRESH_VENV=0   # set when we create the venv (=> first install, not an update)

venv_ok=0
if [[ -d "$VENV" ]] && "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
    venv_ok=1
fi

if [[ $FORCE -eq 1 && -d "$VENV" ]]; then
    echo "==> --force given: removing existing virtualenv…"
    rm -rf "$VENV"; venv_ok=0
elif [[ -d "$VENV" && $venv_ok -eq 0 ]]; then
    echo "==> Existing virtualenv looks broken; recreating it…"
    rm -rf "$VENV"
fi

if [[ ! -d "$VENV" ]]; then
    echo "==> Creating virtualenv (.venv)…"
    python3 -m venv "$VENV" || {
        echo "ERROR: could not create the virtualenv." >&2
        echo "       Run:  sudo apt-get install -y python3-venv   and re-run." >&2
        exit 1
    }
    FRESH_VENV=1
else
    echo "==> Reusing existing virtualenv (.venv). Pass --force to rebuild."
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# --------------------------------------------------------------------------- #
# 4. Python dependencies (network-hardened)                                   #
# --------------------------------------------------------------------------- #
export PIP_DISABLE_PIP_VERSION_CHECK=1

echo "==> Upgrading pip…"
python -m pip install --upgrade --retries 5 --timeout 60 pip >/dev/null || \
    echo "    (pip self-upgrade failed; continuing with existing pip)"

echo "==> Installing Python dependencies (this can take a few minutes)…"
python -m pip install --prefer-binary --retries 5 --timeout 60 -r "$DIR/requirements.txt"

# --------------------------------------------------------------------------- #
# 4.5 GPU acceleration (auto-detected)                                        #
# faster-whisper can use an NVIDIA GPU, but only via the x86_64 CUDA wheels.   #
# On aarch64 (e.g. Grace Blackwell / DGX) transcription uses the CPU (fast);   #
# Ollama still uses the GPU for summaries either way.                         #
# --------------------------------------------------------------------------- #
ARCH="$(uname -m)"
GPU_NAME=""
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
fi
if [[ -n "$GPU_NAME" ]]; then
    echo "==> NVIDIA GPU detected: $GPU_NAME  (arch: $ARCH)"
    if [[ "$ARCH" == "x86_64" ]]; then
        echo "    Installing CUDA math libraries for GPU transcription…"
        python -m pip install --prefer-binary --retries 5 --timeout 120 \
            nvidia-cublas-cu12 nvidia-cudnn-cu12 \
            || echo "    (CUDA libs failed to install; transcription will use CPU)"
    else
        echo "    ($ARCH: faster-whisper has no prebuilt GPU engine for this arch —"
        echo "     transcription uses the CPU, which is fast here. Summaries still"
        echo "     use the GPU via Ollama.)"
    fi
    echo
fi

# --------------------------------------------------------------------------- #
# 5. Smoke test — fail here, not at first click                               #
# --------------------------------------------------------------------------- #
# IMPORTANT: when a display is present we let Qt pick the REAL platform plugin
# (xcb/wayland) so the xcb libraries are genuinely exercised. Forcing
# 'offscreen' here would pass even with the xcb libs missing — useless.
echo "==> Verifying the GUI toolkit loads…"
SMOKE='import PySide6; from PySide6.QtWidgets import QApplication; QApplication([]); import faster_whisper; print("smoke test ok")'
smoke_ok=0
if [[ -n "${WAYLAND_DISPLAY:-}" || -n "${DISPLAY:-}" ]]; then
    if python -c "$SMOKE"; then smoke_ok=1; fi
else
    echo "    (no graphical display detected — running a headless check only)"
    if QT_QPA_PLATFORM=offscreen python -c "$SMOKE"; then smoke_ok=1; fi
fi

if [[ $smoke_ok -eq 1 ]]; then
    echo "==> Smoke test passed."
else
    echo "ERROR: the Qt/PySide6 smoke test failed." >&2
    echo "       This usually means a system library is still missing." >&2
    echo "       Try:  sudo apt-get install -y ${APT_PKGS[*]}" >&2
    echo "       Then re-run:  bash install.sh" >&2
    echo "       If it still fails, reply with this file: $LOG" >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# 6. Executable bit + per-user desktop launcher                               #
# --------------------------------------------------------------------------- #
chmod +x "$DIR/run.sh" 2>/dev/null || true

DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
DESKTOP_FILE="$DESKTOP_DIR/local-transcriber.desktop"

# Build the launcher WITHOUT sed so paths with spaces or shell/sed metacharacters
# (& | etc.) can never corrupt the Exec/Icon lines. Bash '//' replacement is literal.
launcher="$(cat "$DIR/local-transcriber.desktop.in")"
launcher="${launcher//@@EXEC@@/$DIR/run.sh}"
launcher="${launcher//@@ICON@@/$DIR/icon.svg}"
printf '%s\n' "$launcher" > "$DESKTOP_FILE"
chmod +x "$DESKTOP_FILE" 2>/dev/null || true

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------- #
# 6.5 Optional: local AI summaries (Ollama)                                   #
# Only on a FRESH, interactive install, and only if Ollama isn't already set  #
# up. Installs into the home folder (no root). Can also be done in-app later. #
# --------------------------------------------------------------------------- #
if [[ $FRESH_VENV -eq 1 ]] && [ -t 0 ] \
   && ! command -v ollama >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/ollama" ]; then
    echo
    echo "Optional: enable local AI summaries now?"
    echo "  Downloads Ollama + an AI model (a few GB) into your home folder and"
    echo "  runs entirely on your computer — no cloud, no password. You can also"
    echo "  turn it on later from inside the app ('Enable AI summaries')."
    printf "  Set it up now? [y/N] "
    read -r AI_ANS || AI_ANS=""
    case "$AI_ANS" in
        [Yy]*)
            echo "==> Setting up AI summaries (this can take several minutes)…"
            if "$VENV/bin/python" -m transcriber.ollama_setup --model llama3.2; then
                echo "==> AI summaries are ready."
            else
                echo "    (AI setup didn't finish — you can retry anytime from the"
                echo "     app: click 'Enable AI summaries'.)"
            fi
            ;;
        *) echo "  Skipped — enable it anytime from the app." ;;
    esac
fi

# --------------------------------------------------------------------------- #
# 7. Done                                                                     #
# --------------------------------------------------------------------------- #
echo
echo "============================================================"
echo " All set!  Two ways to launch the app:"
echo
echo "   • From your applications menu:  'Local Audio Transcriber'"
echo "   • From a terminal:              $DIR/run.sh"
echo
echo " Hardware detected (transcription / summary acceleration):"
"$VENV/bin/python" -m transcriber.gpu 2>/dev/null | sed 's/^/   /' || true
echo
echo " First-run note:"
echo "   The first time you transcribe, it downloads the Whisper"
echo "   speech model (a few hundred MB). That can take a few"
echo "   minutes with no visible progress — it is NOT frozen."
echo
echo " Optional — local AI summaries:"
echo "   Open the app and click 'Enable AI summaries' (no terminal, no"
echo "   password), or re-run ./install.sh to set it up here."
echo "============================================================"
