@echo off
setlocal
if not defined A3GAME_GODOT_PROJECT (
  echo Set A3GAME_GODOT_PROJECT. 1>&2
  exit /b 2
)
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\..\.."
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"
set "PYTHON_BIN=python"
if defined A3GAME_PYTHON set "PYTHON_BIN=%A3GAME_PYTHON%"
"%PYTHON_BIN%" "%REPO_ROOT%\scripts\import_generated_asset.py" --engine godot --godot-project "%A3GAME_GODOT_PROJECT%" %*
