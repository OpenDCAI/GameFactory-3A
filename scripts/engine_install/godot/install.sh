#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${A3GAME_PYTHON:-$(command -v python3 || command -v python)}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/install.py" "$@"
