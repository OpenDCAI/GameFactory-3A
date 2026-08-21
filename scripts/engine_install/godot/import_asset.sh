#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${A3GAME_PYTHON:-$(command -v python3 || command -v python)}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON_BIN}" "${REPO_ROOT}/scripts/import_generated_asset.py" \
  --engine godot \
  --godot-project "${A3GAME_GODOT_PROJECT:?set A3GAME_GODOT_PROJECT}" "$@"
