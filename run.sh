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
exec python -m transcriber "$@"
