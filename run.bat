@echo off
REM run.bat - Hermes LCARS Dashboard launcher (no Python required to start).
REM Checks for Python; if missing, opens the bundled installer and waits until
REM Python is installed, then runs apply.py with any args you passed.
setlocal
set "DIR=%~dp0"
set "PY="

where python >nul 2>&1 && set "PY=python"
if not defined PY (where py >nul 2>&1 && set "PY=py")

if not defined PY (
    echo.
    echo Python was not found on this computer.
    echo Opening the bundled Python installer - it will install automatically.
    echo You may see a brief permission prompt. When it finishes, this window continues.
    echo To cancel, close this window and run python apply.py later.
    echo.
    start "" "%DIR%python\python-3.14.7-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1
)

:wait
if not defined PY (
    ping -n 4 127.0.0.1 >nul
    where python >nul 2>&1 && set "PY=python"
    if not defined PY (where py >nul 2>&1 && set "PY=py")
    if not defined PY goto wait
)

echo Python detected - continuing.
"%PY%" "%DIR%apply.py" %*
goto :eof
