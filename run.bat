@echo off
REM run.bat - Hermes LCARS Dashboard launcher (no Python required to start).
REM Checks for Python; if missing, runs the bundled installer and waits
REM until Python is installed, then runs apply.py with any args you passed.
title LCARS Dashboard Installer
setlocal enabledelayedexpansion
set "DIR=%~dp0"
set "PY="
set "WAIT_COUNT=0"

where python >nul 2>&1 && set "PY=python"
if not defined PY (where py >nul 2>&1 && set "PY=py -3")

if not defined PY (
    echo.
    echo Python was not found on this computer.
    if not exist "%DIR%python\python-3.14.7-amd64.exe" (
        echo.
        echo [ERROR] The python\ folder is missing or incomplete.
        echo [ERROR] Re-download lcars-installer.zip from the GitHub Releases page.
        echo.
        echo Press any key to close this window.
        pause >nul
        exit /b 1
    )
    echo Installing Python from the bundled installer ^(quiet mode^)...
    echo Please wait - this takes 1-2 minutes. Do NOT close this window.
    echo.
    pushd "%DIR%python"
    python-3.14.7-amd64.exe /quiet InstallAllUsers=0 PrependPath=1
    set "INSTALL_RC=!ERRORLEVEL!"
    popd
    if !INSTALL_RC! NEQ 0 (
        echo.
        echo [ERROR] Python installer failed with exit code !INSTALL_RC!.
        echo [ERROR] Try running the installer manually:
        echo [ERROR]   Double-click: "%DIR%python\python-3.14.7-amd64.exe"
        echo.
        echo Press any key to close this window.
        pause >nul
        exit /b 1
    )
    echo.
    echo Python installation finished. Refreshing environment...
    if exist "%LOCALAPPDATA%\Python\Python314\python.exe" (
        set "PATH=%LOCALAPPDATA%\Python\Python314\Scripts;%LOCALAPPDATA%\Python\Python314;%PATH%"
    )
    if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python314\Scripts;%LOCALAPPDATA%\Programs\Python\Python314;%PATH%"
    )
)

:setpy
set /a WAIT_COUNT+=1 2>nul
where python >nul 2>&1 && set "PY=python"
if not defined PY (where py >nul 2>&1 && set "PY=py -3")
if not defined PY (
    if !WAIT_COUNT! GTR 60 (
        echo.
        echo [ERROR] Python installation timed out after 5 minutes.
        echo [ERROR] The installer may have failed. Check Windows Event Viewer
        echo [ERROR] or run the Python installer manually from the python/ folder.
        echo.
        echo Press any key to close this window.
        pause >nul
        exit /b 1
    )
    echo Still waiting for Python to become available. Check if the installer completed.
    timeout /t 5 /nobreak >nul
    goto setpy
)

echo.
echo Python detected - continuing.
%PY% "%DIR%apply.py" %*
set "APPLY_RC=!ERRORLEVEL!"
if !APPLY_RC! NEQ 0 goto :apply_failed

echo.
echo [LCARS] Skin applied successfully! Open your Hermes dashboard:
echo [LCARS]   http://127.0.0.1:9119/sessions
echo.
echo Press any key to close this window.
pause
goto :eof

:apply_failed
echo.
echo [LCARS] ERROR: the skin could not be applied.
echo [LCARS] Common causes:
echo [LCARS]   - Hermes Agent is not installed yet
echo [LCARS]   - Hermes dashboard (web_dist/index.html) was not found
echo [LCARS]   - Download Hermes from: https://hermes-agent.nousresearch.com/docs/user-guide/installation
echo [LCARS]   - Then re-run this installer
echo [LCARS] Run 'python apply.py' manually to see the full error.
echo.
echo Press any key to close this window.
pause >nul
exit /b 1
