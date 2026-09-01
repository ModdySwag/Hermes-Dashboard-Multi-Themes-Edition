#!/usr/bin/env python3
"""apply.py - one-command installer for the Hermes LCARS Dashboard skin.

Assumes Hermes Agent (and its dashboard) is ALREADY installed and configured on
this machine. This script just FINDS the dashboard, copies the LCARS skin into
it, and applies it. No venv, no pip, no network.

Usage:
  python3 apply.py                 # find Hermes, back up current config, apply skin
  python3 apply.py --remove        # strip the skin entirely (restore original looks)
  python3 apply.py --restore       # revert to your most recent pre-skin backup
  python3 apply.py --restore NAME  # revert to a specific backup (see --list-backups)
  python3 apply.py --list-backups  # show saved backups
  python3 apply.py --target C:\\path\\to\\web_dist\\index.html   # explicit path

Set HERMES_HOME if auto-detect doesn't find your install.

Backups: before applying, your current web_dist/index.html (and any existing
lcars-bg/ folder) are copied to backups/<timestamp>/ next to the bundle AND
mirrored into HERMES_HOME/lcars-backups/<timestamp>/ so they survive the
bundle being moved or deleted. --restore puts them back. Nothing outside the
Hermes dashboard folder is ever modified.
"""
import os
import sys
import time
import shutil
import subprocess

# Ensure we're running Python 3, not Python 2
if sys.version_info[0] < 3:
    sys.stderr.write(
        "LCARS: Python 3 is required but Python 2 was detected.\n"
        "Install Python 3 from the python/ folder in this bundle, then run apply.py again.\n"
    )
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
SKIN = os.path.join(HERE, "apply_lcars_skin.py")
AUTOHEAL = os.path.join(HERE, "lcars_autoheal.sh")
BACKUPS = os.path.join(HERE, "backups")

MARKERS = [
    "<!-- LCARS_HEAD_START -->", "<!-- LCARS_HEAD_END -->",
    "<!-- LCARS_STYLE_START -->", "<!-- LCARS_STYLE_END -->",
    "<!-- LCARS_BODY_START -->", "<!-- LCARS_BODY_END -->",
]


def find_hermes_home():
    """Locate HERMES_HOME (the data directory, not the install directory)."""
    env = os.environ.get("HERMES_HOME")
    if env and os.path.isdir(env):
        return env
    home = os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    mac = os.path.join(home, "Library", "Application Support", "hermes")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    for b in (localapp, mac, xdg):
        candidate = os.path.join(b, "hermes")
        if os.path.isdir(candidate):
            return candidate
    return None


def find_target(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("HERMES_HOME")
    home = os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    mac = os.path.join(home, "Library", "Application Support", "hermes")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    bases = []
    if env:
        bases += [env, os.path.join(env, "hermes-agent", "hermes_cli", "web_dist"),
                  os.path.join(env, "hermes_cli", "web_dist")]
    bases += [localapp, mac, xdg]
    for b in bases:
        cand = os.path.join(b, "hermes", "hermes-agent", "hermes_cli", "web_dist", "index.html")
        if os.path.isfile(cand):
            return cand
        cand2 = os.path.join(b, "hermes-agent", "hermes_cli", "web_dist", "index.html")
        if os.path.isfile(cand2):
            return cand2
    return None


def has_skin(target):
    try:
        return MARKERS[0] in open(target, encoding="utf-8", errors="replace").read()
    except OSError:
        return False


def make_backup(target):
    """Snapshot the current dashboard BEFORE skinning.

    Written in TWO places for resilience: next to the bundle (backups/) and
    mirrored into HERMES_HOME/lcars-backups/ so it survives the bundle being
    moved or deleted. A small target.txt records which dashboard the backup
    belongs to, so --restore/--remove pick the right one on multi-install
    machines. Best-effort: a failed mirror is never fatal.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    for base in (BACKUPS, hermes_home_dir()):
        if not base:
            continue
        try:
            bd = os.path.join(base, ts)
            os.makedirs(bd, exist_ok=True)
            shutil.copyfile(target, os.path.join(bd, "index.html"))
            with open(os.path.join(bd, "target.txt"), "w", encoding="utf-8") as f:
                f.write(os.path.abspath(target) + "\n")
            bg = os.path.join(os.path.dirname(os.path.abspath(target)), "lcars-bg")
            if os.path.isdir(bg):
                shutil.copytree(bg, os.path.join(bd, "lcars-bg"), dirs_exist_ok=True)
        except OSError:
            continue
    return ts


def hermes_home_dir():
    """HERMES_HOME/lcars-backups, or None when Hermes cannot be located."""
    h = find_hermes_home()
    return os.path.join(h, "lcars-backups") if h else None


def list_backups():
    """All timestamped backups, newest first (bundle copies preferred)."""
    names = set()
    for base in (BACKUPS, hermes_home_dir()):
        if not base or not os.path.isdir(base):
            continue
        for n in os.listdir(base):
            p = os.path.join(base, n)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, "index.html")):
                names.add(n)
    return sorted(names, reverse=True)


def backup_path(name):
    """Existing backup dir for a timestamp — bundle copy first, mirror second."""
    for base in (BACKUPS, hermes_home_dir()):
        if not base:
            continue
        p = os.path.join(base, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "index.html")):
            return p
    return None


def restore(target, name):
    names = list_backups()
    if not names:
        sys.stderr.write("No backups found. Run apply.py once to create one.\n")
        sys.exit(1)
    if not name or name == "latest":
        # Prefer a backup taken from THIS dashboard (multi-install safety).
        wanted = os.path.abspath(target).replace("\\", "/").lower()
        name = names[0]
        for n in names:
            p = backup_path(n)
            try:
                with open(os.path.join(p, "target.txt"), encoding="utf-8") as f:
                    if f.read().strip().replace("\\", "/").lower() == wanted:
                        name = n
                        break
            except OSError:
                continue
    bd = backup_path(name)
    if not bd:
        sys.stderr.write("Backup not found or incomplete: " + name + "\n")
        sys.exit(1)
    src = os.path.join(bd, "index.html")
    shutil.copyfile(src, target)
    bg = os.path.join(os.path.dirname(os.path.abspath(target)), "lcars-bg")
    bb = os.path.join(bd, "lcars-bg")
    if os.path.isdir(bb):
        shutil.copytree(bb, bg, dirs_exist_ok=True)
    else:
        shutil.rmtree(bg, ignore_errors=True)
    print("[LCARS] restored dashboard from backup " + name)


def strip_skin(target):
    html = open(target, encoding="utf-8").read()
    for i in range(0, len(MARKERS), 2):
        s, e = MARKERS[i], MARKERS[i + 1]
        while True:
            a = html.find(s)
            if a == -1:
                break
            b = html.find(e, a)
            if b == -1:
                break
            html = html[:a] + html[b + len(e):]
    tmp = target + ".tmp"
    open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, target)
    bg = os.path.join(os.path.dirname(os.path.abspath(target)), "lcars-bg")
    if os.path.isdir(bg):
        shutil.rmtree(bg, ignore_errors=True)


def parse_target(args):
    for i, a in enumerate(args):
        if a == "--target" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--target="):
            return a.split("=", 1)[1]
    return None


def sync_autoheal():
    """Copy lcars_autoheal.sh (+ a bundle-location sidecar) into every
    Hermes scripts/ folder — the default home AND every profile — so the
    cron watchdog always runs the latest version from this bundle no matter
    which profile registered the job or where this bundle was extracted.
    Safe to call every time."""
    if not os.path.isfile(AUTOHEAL):
        return False
    hermes_home = find_hermes_home()
    if not hermes_home:
        return False
    scripts_dirs = [os.path.join(hermes_home, "scripts")]
    profiles = os.path.join(hermes_home, "profiles")
    if os.path.isdir(profiles):
        for name in sorted(os.listdir(profiles)):
            scripts_dirs.append(os.path.join(profiles, name, "scripts"))
    ok = False
    for sd in scripts_dirs:
        try:
            os.makedirs(sd, exist_ok=True)
            dest = os.path.join(sd, "lcars_autoheal.sh")
            shutil.copyfile(AUTOHEAL, dest)
            os.chmod(dest, 0o755)
            with open(os.path.join(sd, "lcars_install_dir.txt"), "w", encoding="utf-8") as f:
                f.write(HERE + "\n")
            ok = True
        except OSError:
            continue
    try:
        with open(os.path.join(hermes_home, "lcars_install_dir.txt"), "w", encoding="utf-8") as f:
            f.write(HERE + "\n")
        ok = True
    except OSError:
        pass
    return ok


def main():
    args = sys.argv[1:]
    target = parse_target(args)
    if not target:
        target = find_target()
    if not target:
        sys.stderr.write(
            "Could not find Hermes' dashboard (web_dist/index.html).\n"
            "This usually means Hermes Agent is not installed yet.\n\n"
            "To install Hermes Agent:\n"
            "  1. Download from: https://hermes-agent.nousresearch.com/docs/user-guide/installation\n"
            "  2. Install it (run the installer and start the Hermes dashboard)\n"
            "  3. Then re-run this installer (run.bat / run.sh)\n\n"
            "If Hermes IS installed but in a custom location, set HERMES_HOME:\n"
            "  Windows:  set HERMES_HOME=%LOCALAPPDATA%\\hermes\n"
            "  macOS:    export HERMES_HOME=~/Library/Application Support/hermes\n"
            "  Linux:    export HERMES_HOME=~/.local/share/hermes\n"
        )
        sys.exit(1)

    if not os.path.isfile(target):
        sys.stderr.write("Target file not found: " + target + "\n")
        sys.exit(1)

    if "--print-target" in args:
        print(target or "(not found)")
        return

    if "--list-backups" in args:
        names = list_backups()
        print("Saved backups:" if names else "No backups yet.")
        for n in names:
            print("  " + n)
        return

    if "--restore" in args:
        i = args.index("--restore")
        name = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else None
        restore(target, name)
        return

    if "--remove" in args:
        # Full revert to the user's pre-skin state when a backup exists;
        # otherwise strip the injected markers in place.
        names = list_backups()
        if names:
            restore(target, names[0])
            print("[LCARS] skin removed; original dashboard restored at " + target)
        else:
            strip_skin(target)
            print("[LCARS] skin removed; original dashboard restored at " + target)
        return

    if not os.path.isfile(SKIN):
        sys.stderr.write("apply_lcars_skin.py not found next to apply.py. Keep the bundle together.\n")
        sys.exit(1)

    # Back up the user's CURRENT config only the first time (before skin exists).
    if not has_skin(target):
        ts = make_backup(target)
        print("[LCARS] backed up current dashboard to backups/" + ts)

    try:
        proc = subprocess.run([sys.executable, SKIN, "--target", target])
    except FileNotFoundError:
        sys.stderr.write(
            "Could not start Python to apply the skin.\n"
            "Install Python 3 from the python/ folder in this bundle (or https://www.python.org/downloads/),\n"
            "then run apply.py again.\n"
        )
        sys.exit(1)
    if proc.returncode == 0 and sync_autoheal():
        print("[LCARS] auto-heal watchdog synced to Hermes scripts/ (survives updates)")
    if proc.returncode == 0 and has_skin(target):
        print("[LCARS] verified: skin markers present in dashboard — done.")
    elif proc.returncode == 0:
        print("[LCARS] WARNING: skin markers not found after apply — please report this.")
    sys.exit(proc.returncode)


def _main():
    main()


if __name__ == "__main__":
    try:
        _main()
    except KeyboardInterrupt:
        sys.stderr.write("\n[LCARS] cancelled.\n")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:  # never show a raw traceback to end users
        sys.stderr.write(
            "\n[LCARS] unexpected error: {0}\n"
            "If this persists, run:  python3 apply.py --target PATH\n"
            "(PATH = full path to your web_dist/index.html), or open an issue at\n"
            "https://github.com/ModdySwag/Hermes-Dashboard-Multi-Themes-Edition/issues\n".format(exc)
        )
        sys.exit(1)
