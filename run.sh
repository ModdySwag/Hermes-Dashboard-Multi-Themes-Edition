#!/bin/bash
# run.sh - Hermes LCARS Dashboard launcher (no Python required to start).
# Checks for Python; if missing, opens the bundled installer and waits until
# Python is installed, then runs apply.py with any args you passed.
DIR="$(cd "$(dirname "$0")" && pwd)"
# Normalise to a Windows path when running under a POSIX shell on Windows
# (git-bash / MSYS) so the native python can resolve apply.py correctly.
case "$DIR" in
  /[a-zA-Z]/*) command -v cygpath >/dev/null 2>&1 && DIR="$(cygpath -w "$DIR")" ;;
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
    else
        echo "Python is not installed. Install it (you will be asked for your password):"
        echo "  Debian/Ubuntu/Mint:  sudo apt install python3"
        echo "  Fedora/RHEL:         sudo dnf install python3"
        echo "  Or build from the bundled source: $DIR/python/Python-3.14.7.tar.xz"
        echo "Opening the python/ folder so you can install it..."
        xdg-open "$DIR/python" 2>/dev/null || true
    fi
    echo "(To cancel, press Ctrl-C and run 'python3 apply.py' later.)"
    echo
    while true; do
        sleep 3
        command -v python3 >/dev/null 2>&1 && PY=python3
        command -v python  >/dev/null 2>&1 && PY=python
        [ -n "$PY" ] && break
    done
    echo "Python detected -- continuing."
fi

"$PY" "$DIR/apply.py" "$@"
