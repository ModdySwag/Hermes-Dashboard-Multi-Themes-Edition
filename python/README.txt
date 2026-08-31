# Python installers (only if you don't have Python 3)

You only need one of these if `python apply.py` / `python3 apply.py` fails with
"command not found". If Python already works, ignore this folder.

- **Windows**  -> `python-3.14.7-amd64.exe`
  Double-click to install (tick "Add Python to PATH" if shown, then Install).
  When it finishes, reopen PowerShell and run `python apply.py`.

- **macOS**    -> `python-3.14.7-macos11.pkg`
  Double-click to install, then reopen Terminal and run `python3 apply.py`.

- **Linux**    -> already installed on almost all distros (`python3`).
  If it's missing: `sudo apt install python3` (Debian/Ubuntu) or
  `sudo dnf install python3` (Fedora). The included `Python-3.14.7.tar.xz`
  is the source build (advanced - compile it yourself only if you must).

All three are the official Python Software Foundation releases from python.org.
