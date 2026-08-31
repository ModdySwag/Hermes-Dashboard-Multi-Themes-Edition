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
lcars-bg/ folder) are copied to bundle/backups/<timestamp>/. --restore puts them
back. Nothing outside the Hermes dashboard folder is ever touched.
"""
import os
import sys
import time
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SKIN = os.path.join(HERE, "apply_lcars_skin.py")
BACKUPS = os.path.join(HERE, "backups")

MARKERS = [
    "<!-- LCARS_HEAD_START -->", "<!-- LCARS_HEAD_END -->",
    "<!-- LCARS_STYLE_START -->", "<!-- LCARS_STYLE_END -->",
    "<!-- LCARS_BODY_START -->", "<!-- LCARS_BODY_END -->",
]


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
    os.makedirs(BACKUPS, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    bd = os.path.join(BACKUPS, ts)
    os.makedirs(bd, exist_ok=True)
    shutil.copyfile(target, os.path.join(bd, "index.html"))
    bg = os.path.join(os.path.dirname(os.path.abspath(target)), "lcars-bg")
    if os.path.isdir(bg):
        shutil.copytree(bg, os.path.join(bd, "lcars-bg"), dirs_exist_ok=True)
    return ts


def list_backups():
    if not os.path.isdir(BACKUPS):
        return []
    return sorted(os.listdir(BACKUPS), reverse=True)


def restore(target, name):
    names = list_backups()
    if not names:
        sys.stderr.write("No backups found. Run apply.py once to create one.\n")
        sys.exit(1)
    if not name or name == "latest":
        name = names[0]
    bd = os.path.join(BACKUPS, name)
    src = os.path.join(bd, "index.html")
    if not os.path.isdir(bd) or not os.path.isfile(src):
        sys.stderr.write("Backup not found or incomplete: " + name + "\n")
        sys.exit(1)
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


def main():
    args = sys.argv[1:]
    target = parse_target(args)
    if not target:
        target = find_target()
    if not target:
        sys.stderr.write(
            "Could not find Hermes' dashboard (web_dist/index.html).\n"
            "Make sure Hermes Agent is installed, or set HERMES_HOME, e.g.:\n"
            "  Windows:  set HERMES_HOME=%LOCALAPPDATA%\\hermes\n"
            "  macOS:    export HERMES_HOME=~/Library/Application Support/hermes\n"
            "  Linux:    export HERMES_HOME=~/.local/share/hermes\n"
        )
        sys.exit(1)

    if not os.path.isfile(target):
        sys.stderr.write("Target file not found: " + target + "\n")
        sys.exit(1)

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
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
