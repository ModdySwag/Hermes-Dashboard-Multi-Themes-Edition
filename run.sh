#!/bin/bash
# run.sh - Hermes LCARS Dashboard launcher (no Python required to start).
# Checks for Python; if missing, opens the bundled installer and waits until
# Python is installed, then runs apply.py with any args you passed.
DIR="$(cd "$(dirname "$0")" && pwd)"
# Normalise to a Windows path when running under a POSIX shell on Windows
# (git-bash / MSYS) so the native python can resolve apply.py correctly.
case "$DIR" in
  /[a-zA-Z]/*) command -v cygpath >/dev/null 2>&1 && DIR="$(cygpath -w "$DIR")";;
esac
PY=""

if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python  >/dev/null 2>&1; then PY=python; fi

if [ -z "$PY" ]; then
    echo
    echo "Python was not found on this computer."
    if [ "$(uname)" = "Darwin" ]; then
        echo "Opening the bundled Python installer -- please run it and finish the setup."
        echo "After it completes, this window will continue automatically."
        open "$DIR/python/python-3.14.7-macos11.pkg"
        echo "Waiting for Python to be installed..."
    elif [ "$(uname)" = "Linux" ] && sudo -n true 2>/dev/null; then
        # Auto-install on Linux if we have passwordless sudo
        echo "Attempting to install Python via package manager..."
        if command -v apt-get >/dev/null 2>&1; then
            sudo apt-get update -qq && sudo apt-get install -y -qq python3
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y python3
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -S --noconfirm python
        else
            echo "  Or build from the bundled source: $DIR/python/Python-3.14.7.tar.xz"
            echo "  Opening the python/ folder so you can install it..."
            xdg-open "$DIR/python" 2>/dev/null || true
        fi
        echo "Waiting for Python to be installed..."
    else
        echo "Python is not installed. Install it (you will be asked for your password):"
        echo "  Debian/Ubuntu/Mint:  sudo apt install python3"
        echo "  Fedora/RHEL:         sudo dnf install python3"
        echo "  Or build from the bundled source: $DIR/python/Python-3.14.7.tar.xz"
        echo "Opening the python/ folder so you can install it..."
        xdg-open "$DIR/python" 2>/dev/null || true
        echo "Waiting for Python to be installed..."
    fi
    echo "(To cancel, press Ctrl-C and run 'python3 apply.py' later.)"
    echo
    echo "Checking every 3 seconds... (timeout: 5 minutes)"
    WAIT_COUNT=0
    while true; do
        sleep 3
        WAIT_COUNT=$((WAIT_COUNT + 1))
        if [ $WAIT_COUNT -gt 100 ]; then
            echo
            echo "[ERROR] Python installation timed out after 5 minutes."
            echo "[ERROR] If using the bundled source, build it manually from:"
            echo "[ERROR]   $DIR/python/Python-3.14.7.tar.xz"
            echo
            read -r
            exit 1
        fi
        if command -v python3 >/dev/null 2>&1; then PY=python3; break; fi
        if command -v python  >/dev/null 2>&1; then PY=python; break; fi
        echo "  ...still waiting for Python to be installed..."
    done
    echo "Python detected -- continuing."
fi

"$PY" "$DIR/apply.py" "$@"
RC=$?
if [ $RC -ne 0 ]; then
    echo
    echo "[LCARS] ERROR: the skin could not be applied."
    echo "[LCARS] Common causes:"
    echo "[LCARS]   - Hermes Agent is not installed yet"
    echo "[LCARS]   - Hermes dashboard (web_dist/index.html) was not found"
    echo "[LCARS] Run 'python3 apply.py' manually to see the full error."
    echo
    echo "Press Enter to close."
    read -r
    exit $RC
fi
echo
echo "[LCARS] Skin applied successfully! Open your Hermes dashboard:"
echo "[LCARS]   http://127.0.0.1:9119/sessions"
echo
echo "Press Enter to close."
read -r
exit 0
