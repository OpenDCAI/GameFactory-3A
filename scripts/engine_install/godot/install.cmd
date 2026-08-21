@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON_BIN=python"
if defined A3GAME_PYTHON set "PYTHON_BIN=%A3GAME_PYTHON%"
"%PYTHON_BIN%" "%SCRIPT_DIR%install.py" %*
