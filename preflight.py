#!/usr/bin/env python3
"""preflight.py - bundle integrity, PC search and user alerts for the LCARS installer.

Everything the installers (run.bat / run.sh / apply.py) need BEFORE they can
safely touch a dashboard:

  check_bundle(here)     - verify every file the apply needs is present, non-empty
                           and (for wallpapers) actually referenced by the skin.
  search_dashboard()     - bounded PC search for web_dist/index.html when the
                           standard Hermes locations come up empty. Returns
                           (best_match, all_matches, searched_bases).
  missing_anchors(path)  - does the file look like a real Hermes dashboard?
  notify(...)            - GUI pop-up on Windows / macOS / Linux, with a console
                           fallback (set LCARS_NO_GUI=1 to force the fallback,
                           e.g. SSH sessions or automated tests).
  open_url(url)          - best-effort "redirect" to the page that fixes the
                           problem (Hermes install docs, GitHub Releases, ...).

Python 3.8+ only (guarded by apply.py before importing this module).
"""

import os
import platform
import subprocess
import sys

# --------------------------------------------------------------------------
# Shared URLs (kept here so apply.py and future callers stay in sync).
# --------------------------------------------------------------------------
HERMES_INSTALL_URL = "https://hermes-agent.nousresearch.com/docs/user-guide/installation"
RELEASES_URL = "https://github.com/ModdySwag/Hermes-Dashboard-Multi-Themes-Edition/releases"

# --------------------------------------------------------------------------
# Console / GUI reporting
# --------------------------------------------------------------------------
NO_GUI = os.environ.get("LCARS_NO_GUI") == "1"

WARNING_ICON = "warning"
INFO_ICON = "info"


def _console(title, message):
    """Always print a console copy so nothing is lost when no GUI exists."""
    sys.stdout.write("[LCARS] " + title + "\n")
    for line in str(message).splitlines():
        sys.stdout.write("[LCARS]   " + line + "\n")
    sys.stdout.flush()


def _windows_popup(title, message, icon):
    import ctypes
    user32 = ctypes.windll.user32
    flags = 0x40000 | 0x10000            # MB_TOPMOST | MB_SETFOREGROUND
    flags |= 0x30 if icon == WARNING_ICON else 0x40   # MB_ICONWARNING / MB_ICONINFORMATION
    user32.MessageBoxW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_uint)
    user32.MessageBoxW(None, str(message), str(title), flags)


def _mac_popup(title, message, icon):
    # display dialog blocks until dismissed - that is the point of a pop-up.
    def _as_str(s):
        # AppleScript double-quoted string: escape \ and ", keep newlines.
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    script = ("display dialog " + _as_str(message) +
              " with title " + _as_str(title) +
              ' buttons {"OK"} default button "OK" with icon ' +
              ("caution" if icon == WARNING_ICON else "note"))
    subprocess.run(["osascript", "-e", script], check=False)


def _linux_popup(title, message, icon):
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if not display:
        return False
    msg = str(message)
    try:
        if subprocess.call(["zenity", "--width", "480",
                            "--warning" if icon == WARNING_ICON else "--info",
                            "--title", str(title), "--text", msg],
                           timeout=120) == 0:
            return True
    except Exception:
        pass
    try:
        if subprocess.call(["kdialog", "--title", str(title),
                            "--sorry" if icon == WARNING_ICON else "--msgbox", msg],
                           timeout=120) == 0:
            return True
    except Exception:
        pass
    try:
        subprocess.call(["xmessage", "-center", "-buttons", "OK",
                         str(title) + ":\n" + msg], timeout=120)
        return True
    except Exception:
        pass
    return False


def notify(title, message, icon=WARNING_ICON, gui=True):
    """Pop-up on the desktop when possible; always write a console copy.

    gui=False keeps this console-only (used by --check / --print-target and
    other power-user paths). LCARS_NO_GUI=1 forces console-only everywhere.
    """
    _console(title, message)
    if not gui or NO_GUI:
        return
    try:
        system = platform.system()
        if system == "Windows":
            _windows_popup(title, message, icon)
        elif system == "Darwin":
            _mac_popup(title, message, icon)
        elif system == "Linux":
            _linux_popup(title, message, icon)
    except Exception:
        pass   # pop-ups are best-effort; the console copy already went out.


def open_url(url):
    """Open the given page in the default browser (best effort)."""
    if not url or NO_GUI:
        return
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(url)                       # noqa: S606 - user-requested redirect
        elif system == "Darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Bundle integrity
# --------------------------------------------------------------------------
def check_bundle(here):
    """Return (fatals, warnings): every bundled file the apply needs.

    Fatal  - apply cannot succeed without it (engine, bridge image, the
             wallpapers the themes reference).
    Warning - apply still works now, but a feature or a future re-run is
             degraded (auto-heal script, bundled Python installers).
    """
    fatals, warnings = [], []

    def _missing_file(rel, what):
        path = os.path.join(here, rel)
        if not os.path.isfile(path):
            fatals.append(what + " is missing from the installer bundle: " + rel)
        elif os.path.getsize(path) == 0:
            fatals.append(what + " is empty (0 bytes) - the bundle is damaged: " + rel)

    _missing_file("apply_lcars_skin.py", "The skin engine (apply_lcars_skin.py)")
    _missing_file("lcars-bg.jpg", "The bridge wallpaper (lcars-bg.jpg)")

    # Every wallpaper the 13 themes actually reference must be in lcars-bg/,
    # or that theme shows a broken background image after install.
    bg_dir = os.path.join(here, "lcars-bg")
    if os.path.isdir(bg_dir):
        try:
            sys.path.insert(0, here)
            try:
                import apply_lcars_skin as _engine
                referenced = set()
                for theme in getattr(_engine, "THEMES", []):
                    spec = theme.get("bg", "")
                    if spec.startswith("url('/lcars-bg/"):
                        fn = spec.split("/lcars-bg/", 1)[1].rsplit("'", 1)[0]
                        if fn:
                            referenced.add(fn)
                for fn in sorted(referenced):
                    p = os.path.join(bg_dir, fn)
                    if not os.path.isfile(p):
                        fatals.append("Theme wallpaper is missing from lcars-bg/: " + fn)
                    elif os.path.getsize(p) == 0:
                        fatals.append("Theme wallpaper is empty (0 bytes): " + fn)
            except Exception as exc:
                fatals.append("Could not read the skin's theme list: " + str(exc))
            finally:
                try:
                    sys.path.remove(here)
                except ValueError:
                    pass
        except Exception:
            pass
    else:
        fatals.append("The wallpaper folder lcars-bg/ is missing from the installer bundle.")

    if not os.path.isfile(os.path.join(here, "lcars_autoheal.py")):
        warnings.append("lcars_autoheal.py is missing - the update watchdog "
                        "will not be installed (the skin still applies).")

    py_dir = os.path.join(here, "python")
    if not os.path.isdir(py_dir) or not os.listdir(py_dir):
        warnings.append("The python/ folder is missing or empty - fine if Python "
                        "3.8+ is already installed; otherwise run.bat/run.sh cannot "
                        "install Python for you.")

    return fatals, warnings


# --------------------------------------------------------------------------
# Dashboard search (bounded PC search)
# --------------------------------------------------------------------------
PRUNE_DIRS = {
    "node_modules", "site-packages", "venv", ".venv", "env", ".env",
    ".git", "__pycache__", ".cache", ".npm", ".m2",
    "temp", "tmp", "packages", "windows", "program files",
    "program files (x86)", "appdata", "library", "system volume information",
    "$recycle.bin", "trash", "docker", "onedrivecache",
}

SIGNATURE_NEEDLES = (b'id="root"', b"/assets/")


def _home_dir():
    for key in ("USERPROFILE", "HOME"):
        val = os.environ.get(key)
        if val:
            return val
    return os.path.expanduser("~")


def _search_bases():
    """Directories worth walking, plus cheap stat-only locations."""
    bases, stat_targets = [], []
    home = _home_dir()
    system = platform.system()

    if system == "Windows":
        for key in ("LOCALAPPDATA", "APPDATA"):
            val = os.environ.get(key)
            if val and os.path.isdir(val):
                bases.append(val)
        if os.path.isdir(home):
            bases.append(home)                     # AppData subtree is pruned; rest is shallow
        for key in ("ProgramData", "ProgramFiles", "ProgramFiles(x86)"):
            val = os.environ.get(key)
            if val:
                stat_targets.append(os.path.join(val, "hermes"))
    elif system == "Darwin":
        for base in (os.path.join(home, "Library", "Application Support"), home):
            if os.path.isdir(base):
                bases.append(base)
        stat_targets.append("/opt/hermes")
    else:  # Linux
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
        for base in (xdg, home):
            if os.path.isdir(base):
                bases.append(base)
        stat_targets += ["/opt/hermes", "/usr/local/share/hermes",
                         "/usr/share/hermes", "/usr/local/lib/hermes"]

    seen, unique_bases = set(), []
    for base in bases:
        rp = os.path.realpath(base)
        if rp not in seen:
            seen.add(rp)
            unique_bases.append(base)
    return unique_bases, stat_targets


def _is_dashboard_index(path):
    """index.html only counts if it looks like a Vite-served Hermes dashboard."""
    try:
        if os.path.getsize(path) > 3 * 1024 * 1024:      # index.html is ~1.5 KB
            return False
        with open(path, "rb") as fh:
            data = fh.read(2 * 1024 * 1024)
        return all(needle in data for needle in SIGNATURE_NEEDLES)
    except OSError:
        return False


def _hermes_ish(path):
    """Rank a candidate higher when its path mentions Hermes (real installs do)."""
    return 2 if "hermes" in os.path.dirname(path).lower() else 1


def _quick_stat_targets(stat_targets):
    found = []
    for root in stat_targets:
        for rel in ("hermes-agent/hermes_cli/web_dist/index.html",
                    "hermes_cli/web_dist/index.html"):
            cand = os.path.join(root, rel)
            if os.path.isfile(cand) and _is_dashboard_index(cand):
                found.append(cand)
    return found


def search_dashboard(max_depth=6):
    """PC search for Hermes' web_dist/index.html.

    Returns (best, all_matches, searched_summary). all_matches is sorted with
    the best first. Bounded: pruned dir list, no symlink/junction following,
    depth cap and a directory budget so it can never scan a whole disk.
    """
    bases, stat_targets = _search_bases()
    searched = [os.path.realpath(b) for b in bases]
    all_matches = _quick_stat_targets(stat_targets)

    visited = set(searched)
    budget = [30000]

    def _walk(base):
        stack = [(base, 0)]
        while stack:
            if budget[0] <= 0:
                return
            current, depth = stack.pop()
            budget[0] -= 1
            try:
                with os.scandir(current) as scan:
                    subdirs = [e for e in scan if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for entry in sorted(subdirs, key=lambda e: e.name.lower()):
                name = entry.name
                if name.startswith(".") or name.lower() in PRUNE_DIRS:
                    continue
                child_depth = depth + 1
                if child_depth > max_depth:
                    continue
                if name.lower() == "web_dist":
                    idx = os.path.join(entry.path, "index.html")
                    if _is_dashboard_index(idx):
                        all_matches.append(idx)
                    continue                      # never descend inside web_dist
                try:
                    real = os.path.realpath(entry.path)
                except OSError:
                    continue
                if real in visited:
                    continue
                visited.add(real)
                stack.append((entry.path, child_depth))

    for base in bases:
        _walk(base)

    # Rank: Hermes-named paths first, then discovery order.
    all_matches = sorted(set(all_matches),
                         key=lambda p: (-_hermes_ish(p), all_matches.index(p)))
    best = all_matches[0] if all_matches else None

    summary = ("Searched " + str(len(searched)) + " location(s): "
               + "; ".join(searched[:6])
               + ("" if len(searched) <= 6 else "; ..."))
    return best, all_matches, summary


# --------------------------------------------------------------------------
# Target sanity (does the file look like the dashboard we inject into?)
# --------------------------------------------------------------------------
ANCHORS = ("<title>", "</head>", '<div id="root">', "</body>")


def missing_anchors(path):
    """Return the list of structural anchors absent from the dashboard HTML."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    except OSError:
        return list(ANCHORS)
    return [anchor for anchor in ANCHORS if anchor not in html]
