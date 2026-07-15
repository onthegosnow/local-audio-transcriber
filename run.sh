#!/usr/bin/env bash
# Launch the Local Audio Transcriber GUI from its virtualenv.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$DIR/.venv" ]]; then
    echo "Virtualenv not found. Run ./install.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$DIR/.venv/bin/activate"

# Expose CUDA libraries from the nvidia-* pip wheels (installed on x86_64 + GPU)
# so faster-whisper's ctranslate2 can find libcudnn/libcublas at runtime.
# Harmless no-op when they aren't installed.
for _nvlib in "$DIR"/.venv/lib/python*/site-packages/nvidia/*/lib; do
    [[ -d "$_nvlib" ]] && LD_LIBRARY_PATH="$_nvlib:${LD_LIBRARY_PATH:-}"
done
export LD_LIBRARY_PATH

exec python -m transcriber "$@"
