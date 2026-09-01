@echo off
REM run.bat - Hermes LCARS Dashboard launcher (no Python required to start).
REM Checks for Python; if missing, runs the bundled installer and waits
REM until Python is installed, then runs apply.py with any args you passed.
setlocal
set "DIR=%~dp0"
set "PY="

where python >nul 2>&1 && set "PY=python"
if not defined PY (where py >nul 2>&1 && set "PY=py -3")
if not defined PY (where py >nul 2>&1 && set "PY=py")

if not defined PY (
    echo.
    echo Python was not found on this computer.
    echo Installing Python from the bundled installer ^(quiet mode^)...
    echo Please wait - this takes 1-2 minutes. Do NOT close this window.
    echo.
    pushd "%DIR%python"
    start "Python Installer" /wait python-3.14.7-amd64.exe /quiet InstallAllUsers=0 PrependPath=1
    popd
    echo.
    echo Python installation finished. Refreshing environment...
    REM Add common install locations to PATH for this session (PrependPath writes to
    REM the registry but does not refresh the current cmd.exe session's environment).
    if exist "%LOCALAPPDATA%\Python\Python314\python.exe" (
        set "PATH=%LOCALAPPDATA%\Python\Python314\Scripts;%LOCALAPPDATA%\Python\Python314;%PATH%"
    )
    if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python314\Scripts;%LOCALAPPDATA%\Programs\Python\Python314;%PATH%"
    )
)

:wait
where python >nul 2>&1 && set "PY=python"
if not defined PY (where py >nul 2>&1 && set "PY=py -3")
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY (
    echo Still waiting for Python to become available^^. Check if the installer completed.^^.
    timeout /t 5 /nobreak >nul
    goto wait
)

echo.
echo Python detected - continuing.
"%PY%" "%DIR%apply.py" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [LCARS] ERROR: the skin could not be applied.
    echo [LCARS] Common causes:
    echo [LCARS]   - Hermes Agent is not installed yet
    echo [LCARS]   - Hermes dashboard (web_dist/index.html) was not found
    echo [LCARS] Run 'python apply.py' manually to see the full error.
    echo.
    echo Press any key to close this window.
    pause >nul
    exit /b 1
)
echo.
echo [LCARS] Skin applied successfully! Open your Hermes dashboard:
echo [LCARS]   http://127.0.0.1:9119/sessions
echo.
echo Press any key to close this window.
pause
goto :eof
