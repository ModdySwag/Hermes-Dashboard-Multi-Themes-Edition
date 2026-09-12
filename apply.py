#!/usr/bin/env python3
"""apply.py - one-command installer for the Hermes LCARS Dashboard skin.

Assumes Hermes Agent (and its dashboard) is ALREADY installed and configured on
this machine. This script FINDS the dashboard, copies the LCARS skin into it,
and applies it. No venv, no pip, no network.

On every platform (Windows / macOS / Linux) it:
  1. checks the installer bundle is complete and uncorrupted,
  2. checks Python 3.8+ is running,
  3. locates Hermes' dashboard (web_dist/index.html) - standard locations
     first, then a bounded search of this computer,
  4. verifies the found file is a real, structurally sound Hermes dashboard,
  5. backs the current dashboard up, then applies the skin and verifies it.

If something required is missing, it pops up a dialog telling you exactly what
is needed and how to get/install it (and opens the right help page). On
headless/SSH sessions the same message is printed to the console instead.

Usage:
  python3 apply.py                 # find Hermes, back up, apply skin
  python3 apply.py --remove        # strip the skin entirely (restore original looks)
  python3 apply.py --restore       # revert to your most recent pre-skin backup
  python3 apply.py --restore NAME  # revert to a specific backup (see --list-backups)
  python3 apply.py --list-backups  # show saved backups
  python3 apply.py --check         # report bundle + dashboard readiness, change nothing
  python3 apply.py --print-target  # print the resolved dashboard path, change nothing
  python3 apply.py --no-chat-stability  # skin only; leave the chat view-stability add-on out
  python3 apply.py --chat-stability     # re-enable the add-on (forgets an earlier opt-out)
  python3 apply.py --target C:\\path\\to\\web_dist\\index.html   # explicit path

Set HERMES_HOME if auto-detect doesn't find your install.

Backups: before applying, your current web_dist/index.html (and any existing
lcars-bg/ folder) are copied to backups/<timestamp>/ next to the bundle AND
mirrored into HERMES_HOME/lcars-backups/<timestamp>/ so they survive the
bundle being moved or deleted. --restore puts them back. Nothing outside the
Hermes dashboard folder is ever modified.
"""

import sys

# Python-version gate BEFORE importing anything else: preflight.py is 3.8+ and
# the skin engine needs 3.8 features (dirs_exist_ok), so fail with clear
# instructions instead of a confusing traceback on older interpreters.
if sys.version_info < (3, 8):
    sys.stderr.write(
        "[LCARS] Python 3.8 or newer is required to run this installer.\n"
        "[LCARS] You are running Python {0}.{1}.\n"
        "[LCARS] Install it like this:\n"
        "[LCARS]   Windows: re-run run.bat - it installs the bundled Python for you.\n"
        "[LCARS]   macOS:   open the bundled  python/python-3.14.7-macos11.pkg\n"
        "[LCARS]   Linux:   sudo apt install python3    (Debian/Ubuntu/Mint)\n"
        "[LCARS]            sudo dnf install python3    (Fedora)\n"
        "[LCARS]            sudo pacman -S python       (Arch)\n"
        "[LCARS]   Or download Python from https://www.python.org/downloads/\n"
    ).format(sys.version_info[0], sys.version_info[1])
    sys.exit(1)

import glob
import os
import re
import shutil
import subprocess
import tempfile
import time

import preflight

HERE = os.path.dirname(os.path.abspath(__file__))
SKIN = os.path.join(HERE, "apply_lcars_skin.py")
AUTOHEAL_NAME = "lcars_autoheal.py"          # portable watchdog; see its header
LEGACY_AUTOHEAL_NAME = "lcars_autoheal.sh"   # retired: Hermes runs a cron job's .sh
                                             # through wsl.exe on Windows, which dies
                                             # outright when WSL has no distribution
SIDECAR_NAME = "lcars_install_dir.txt"
AUTOHEAL = os.path.join(HERE, AUTOHEAL_NAME)
BACKUPS = os.path.join(HERE, "backups")

MARKERS = [
    "<!-- LCARS_HEAD_START -->", "<!-- LCARS_HEAD_END -->",
    "<!-- LCARS_STYLE_START -->", "<!-- LCARS_STYLE_END -->",
    "<!-- LCARS_BODY_START -->", "<!-- LCARS_BODY_END -->",
    # Chat view-stability block injected by the engine alongside the skin.
    "<!-- HERMES_CHAT_VIEW_STABLE_START -->", "<!-- HERMES_CHAT_VIEW_STABLE_END -->",
]


def _data_roots():
    """Candidate Hermes data roots for this platform, newest preference first."""
    roots = []
    env = os.environ.get("HERMES_HOME")
    if env:
        roots.append(env)
    home = os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    mac = os.path.join(home, "Library", "Application Support", "hermes")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    roots += [os.path.join(localapp, "hermes"), mac, os.path.join(xdg, "hermes")]
    return roots


def find_hermes_home():
    """Locate HERMES_HOME (the data directory, not the install directory)."""
    for root in _data_roots():
        if os.path.isdir(root):
            return root
    return None


def find_target(explicit=None):
    if explicit:
        return explicit
    for root in _data_roots():
        # Standard layout: <root>/hermes-agent/hermes_cli/web_dist.
        # Also accept <root>/hermes_cli/web_dist when HERMES_HOME points
        # directly at the install directory.
        for rel in (("hermes-agent", "hermes_cli"), ("hermes_cli",)):
            cand = os.path.join(root, *rel, "web_dist", "index.html")
            if os.path.isfile(cand):
                return cand
    return None


def hermes_root_from_target(target):
    """Derive the Hermes data root from a dashboard path found outside the
    standard locations (e.g. by the PC search), so backups and the auto-heal
    watchdog still land in the real Hermes data folder.

    Accepts <root>/hermes-agent/hermes_cli/web_dist or <root>/hermes_cli/web_dist.
    Returns None when the path follows neither layout.
    """
    web_dist = os.path.dirname(os.path.abspath(target))
    layout_agent = os.path.normpath(os.path.join("hermes-agent", "hermes_cli", "web_dist"))
    layout_direct = os.path.normpath(os.path.join("hermes_cli", "web_dist"))

    # Pass 1: the canonical <root>/hermes-agent/hermes_cli/web_dist layout.
    cur = web_dist
    for _ in range(4):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        if os.path.normpath(os.path.relpath(web_dist, parent)) == layout_agent:
            return parent
        cur = parent
    # Pass 2: <root>/hermes_cli/web_dist (HERMES_HOME points at the data dir
    # which contains hermes_cli directly). Must not mistake hermes-agent/ for
    # the root, hence the separate pass.
    cur = web_dist
    for _ in range(4):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        if os.path.normpath(os.path.relpath(web_dist, parent)) == layout_direct:
            return parent
        cur = parent
    return None


def has_skin(target):
    try:
        return MARKERS[0] in open(target, encoding="utf-8", errors="replace").read()
    except OSError:
        return False


def hermes_home_dir(target=None):
    """Hermes data root + lcars-backups, or None when Hermes cannot be located."""
    h = find_hermes_home()
    if not h and target:
        h = hermes_root_from_target(target)
    return os.path.join(h, "lcars-backups") if h else None


def make_backup(target):
    """Snapshot the current dashboard BEFORE skinning.

    Written in TWO places for resilience: next to the bundle (backups/) and
    mirrored into HERMES_HOME/lcars-backups/ so it survives the bundle being
    moved or deleted. A small target.txt records which dashboard the backup
    belongs to, so --restore/--remove pick the right one on multi-install
    machines. Best-effort: a failed mirror is never fatal.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    for base in (BACKUPS, hermes_home_dir(target)):
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
    prune_backups(target)
    return ts


def prune_backups(target=None, max_keep=5):
    """Keep the newest `max_keep` backups plus the oldest (usually the ORIGINAL
    pre-skin dashboard) in each store, so growth stays bounded on long-lived
    machines. Best-effort; a failed prune is never fatal."""
    for base in (BACKUPS, hermes_home_dir(target)):
        if not base or not os.path.isdir(base):
            continue
        names = sorted(n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n)))
        drop = names[1:max(0, len(names) - max_keep)]
        for n in drop:
            shutil.rmtree(os.path.join(base, n), ignore_errors=True)


def list_backups(target=None):
    """All timestamped backups, newest first (bundle copies preferred)."""
    names = set()
    for base in (BACKUPS, hermes_home_dir(target)):
        if not base or not os.path.isdir(base):
            continue
        for n in os.listdir(base):
            p = os.path.join(base, n)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, "index.html")):
                names.add(n)
    return sorted(names, reverse=True)


def backup_path(name, target=None):
    """Existing backup dir for a timestamp — bundle copy first, mirror second."""
    for base in (BACKUPS, hermes_home_dir(target)):
        if not base:
            continue
        p = os.path.join(base, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "index.html")):
            return p
    return None


def restore(target, name):
    names = list_backups(target)
    if not names:
        sys.stderr.write("No backups found. Run apply.py once to create one.\n")
        sys.exit(1)
    if not name or name == "latest":
        # Prefer a backup taken from THIS dashboard (multi-install safety).
        wanted = os.path.abspath(target).replace("\\", "/").lower()
        name = names[0]
        for n in names:
            p = backup_path(n, target)
            try:
                with open(os.path.join(p, "target.txt"), encoding="utf-8") as f:
                    if f.read().strip().replace("\\", "/").lower() == wanted:
                        name = n
                        break
            except OSError:
                continue
    bd = backup_path(name, target)
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


def _hermes_cli():
    """Locate the hermes CLI, or None. A GUI double-click has a thin PATH."""
    found = shutil.which("hermes")
    if found:
        return [found]
    home = os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA", "")
    for cand in (
        os.path.join(localapp, "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe") if localapp else "",
        os.path.join(home, ".local", "bin", "hermes"),
        "/usr/local/bin/hermes",
    ):
        if cand and os.path.isfile(cand):
            return [cand]
    return None


def migrate_autoheal_jobs():
    """Re-point cron jobs still registered against lcars_autoheal.sh at the .py.

    Installs made with an older bundle registered the shell watchdog. On Windows
    such a job fails on every tick with "Windows Subsystem for Linux has no
    installed distributions" while looking like a script bug, so repairing it
    beats leaving a silently dead job behind. Best effort: needs the hermes CLI
    and is never fatal.

    Returns the number of jobs re-pointed.
    """
    cli = _hermes_cli()
    if not cli:
        return 0
    try:
        listing = subprocess.run(cli + ["cron", "list", "--all"],
                                 capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return 0

    fixed = 0
    job = None
    for line in (listing or "").splitlines():
        m = re.match(r"^\s{2}([0-9a-f]{12})\b", line)
        if m:
            job = m.group(1)
            continue
        if job and re.search(r"\bScript:\s*\S*" + re.escape(LEGACY_AUTOHEAL_NAME), line):
            try:
                subprocess.run(cli + ["cron", "edit", job, "--script", AUTOHEAL_NAME],
                               capture_output=True, text=True, timeout=60)
                print("[LCARS] auto-heal cron job " + job + " re-pointed at "
                      + AUTOHEAL_NAME)
                fixed += 1
            except (OSError, subprocess.SubprocessError):
                pass
            job = None
    return fixed


def _scripts_dirs(hermes_home):
    """Every Hermes scripts/ folder the watchdog is synced into."""
    dirs = [os.path.join(hermes_home, "scripts")]
    profiles = os.path.join(hermes_home, "profiles")
    if os.path.isdir(profiles):
        for name in sorted(os.listdir(profiles)):
            dirs.append(os.path.join(profiles, name, "scripts"))
    return dirs


def sync_autoheal(target=None):
    """Copy the auto-heal watchdog (+ a bundle-location sidecar) into every
    Hermes scripts/ folder — the default home AND every profile — so the
    cron watchdog always runs the latest version from this bundle no matter
    which profile registered the job or where this bundle was extracted.
    Also retires a stale lcars_autoheal.sh and re-points any job still using it.
    Safe to call every time."""
    if not os.path.isfile(AUTOHEAL):
        return False
    hermes_home = find_hermes_home()
    if not hermes_home and target:
        hermes_home = hermes_root_from_target(target)
    if not hermes_home:
        return False
    scripts_dirs = _scripts_dirs(hermes_home)
    ok = False
    for sd in scripts_dirs:
        try:
            os.makedirs(sd, exist_ok=True)
            dest = os.path.join(sd, AUTOHEAL_NAME)
            shutil.copyfile(AUTOHEAL, dest)
            os.chmod(dest, 0o755)
            with open(os.path.join(sd, SIDECAR_NAME), "w", encoding="utf-8") as f:
                f.write(HERE + "\n")
            legacy = os.path.join(sd, LEGACY_AUTOHEAL_NAME)
            if os.path.isfile(legacy):
                try:
                    os.remove(legacy)
                except OSError:
                    pass
            ok = True
        except OSError:
            continue
    try:
        with open(os.path.join(hermes_home, SIDECAR_NAME), "w", encoding="utf-8") as f:
            f.write(HERE + "\n")
        ok = True
    except OSError:
        pass
    if ok:
        # Installing the watchdog re-arms it: clear a stand-down left by a
        # previous --remove / --restore.
        _write_disabled_markers(hermes_home, False)
        migrate_autoheal_jobs()
    return ok


AUTOSTART_TASK = "LCARS_Dashboard_AutoHeal"   # Windows Scheduled Task name
AUTOSTART_LABEL = "com.moddyswag.lcars-autoheal"   # launchd label / systemd unit base
AUTOSTART_MINUTES = 1                        # how often it re-checks for the skin
AUTOSTART_DISABLED_NAME = "lcars_autoheal.disabled"   # stand-down marker


def _pick_pythonw():
    """A ``pythonw.exe`` to run the watchdog on, or None.

    Deliberately NOT the Hermes venv interpreter: `hermes update` replaces and
    locks exactly that venv, so a healer pointing at it would break the moment
    it is needed. Preference: PATH, the standard per-user Python installs,
    then this interpreter's own ``pythonw`` (only when it is not the venv).
    """
    cands = [shutil.which("pythonw") or ""]
    localapp = os.environ.get("LOCALAPPDATA") or ""
    for base in (os.path.join(localapp, "Programs", "Python") if localapp else "",
                 os.path.join(os.environ.get("ProgramFiles") or "", "Python")):
        if base and os.path.isdir(base):
            for name in sorted(os.listdir(base), reverse=True):
                cands.append(os.path.join(base, name, "pythonw.exe"))
    if sys.executable.lower().endswith("python.exe"):
        cands.append(os.path.join(os.path.dirname(sys.executable), "pythonw.exe"))
    for cand in cands:
        if not cand or not os.path.isfile(cand):
            continue
        lowered = cand.replace("/", "\\").lower()
        if "hermes-agent" in lowered or "\\venv\\" in lowered:
            continue                      # the venv an update swaps out
        return cand
    return None


def _is_temp_path(p):
    """True when a path lives under a temp folder.

    Journey/CI runs of this installer execute from a scratch copy under Temp,
    and sync_autoheal() writes copies + a sidecar there. Such a copy must never
    capture the machine-wide scheduled task - it would be deleted with the
    scratch dir and silently stop healing the real dashboard.
    """
    if not p:
        return False
    return any(part.lower() in ("temp", "tmp") for part in os.path.normpath(p).split(os.sep))


def _healer_script(hermes_home):
    """The synced watchdog copy the scheduled task should run.

    The default home's ``scripts/`` copy is the most stable address (it is not
    tied to a profile name), so prefer it and fall back to any profile's copy.
    """
    default = os.path.join(hermes_home, "scripts", AUTOHEAL_NAME)
    if os.path.isfile(default) and not _is_temp_path(default):
        return default
    profiles = os.path.join(hermes_home, "profiles")
    if os.path.isdir(profiles):
        for name in sorted(os.listdir(profiles)):
            cand = os.path.join(profiles, name, "scripts", AUTOHEAL_NAME)
            if os.path.isfile(cand) and not _is_temp_path(cand):
                return cand
    return None


def _task_command(task_name):
    """(Command, Arguments) of an existing task, or None when it does not exist."""
    try:
        proc = subprocess.run(["schtasks", "/Query", "/TN", task_name, "/XML"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    cmd = re.search(r"<Command>(.*?)</Command>", proc.stdout or "")
    arg = re.search(r"<Arguments>(.*?)</Arguments>", proc.stdout or "")
    return ((cmd.group(1).strip() if cmd else ""),
            (arg.group(1).strip() if arg else ""))


def _register_task(task_name, pythonw, script):
    """Create/refresh the scheduled task. Returns True on success."""
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo>\n'
        '    <Description>Re-applies the LCARS dashboard skin whenever a Hermes update '
        '(or anything else) rebuilds hermes_cli/web_dist/index.html. Runs OUTSIDE the '
        'Hermes process tree on a non-venv Python, so it heals even when every Hermes '
        'process has been stopped for the update.</Description>\n'
        '    <URI>\\' + task_name + '</URI>\n'
        '  </RegistrationInfo>\n'
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        '      <UserId>' + user + '</UserId>\n'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '    </Principal>\n'
        '  </Principals>\n'
        '  <Settings>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <AllowStartOnDemand>true</AllowStartOnDemand>\n'
        '    <Enabled>true</Enabled>\n'
        '    <WakeToRun>false</WakeToRun>\n'
        '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
        '    <Priority>7</Priority>\n'
        '    <IdleSettings>\n'
        '      <StopOnIdleEnd>false</StopOnIdleEnd>\n'
        '      <RestartOnIdle>false</RestartOnIdle>\n'
        '    </IdleSettings>\n'
        '  </Settings>\n'
        '  <Triggers>\n'
        '    <LogonTrigger>\n'
        '      <Enabled>true</Enabled>\n'
        '      <UserId>' + user + '</UserId>\n'
        '    </LogonTrigger>\n'
        '    <TimeTrigger>\n'
        '      <StartBoundary>' + time.strftime("%Y-%m-%dT%H:%M:%S") + '</StartBoundary>\n'
        '      <Enabled>true</Enabled>\n'
        '      <Repetition>\n'
        '        <Interval>PT' + str(AUTOSTART_MINUTES) + 'M</Interval>\n'
        '      </Repetition>\n'
        '    </TimeTrigger>\n'
        '  </Triggers>\n'
        '  <Actions Context="Author">\n'
        '    <Exec>\n'
        '      <Command>"' + pythonw + '"</Command>\n'
        '      <Arguments>"' + script + '"</Arguments>\n'
        '    </Exec>\n'
        '  </Actions>\n'
        '</Task>\n'
    )
    path = os.path.join(tempfile.gettempdir(), task_name + ".xml")
    try:
        with open(path, "w", encoding="utf-16") as f:
            f.write(xml)
        proc = subprocess.run(["schtasks", "/Create", "/TN", task_name, "/XML", path, "/F"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def ensure_autostart_healer(target=None, platform=None):
    """Make the auto-heal watchdog run even when NO Hermes process is alive.

    WHY this exists next to the cron job: Hermes' cron ticker only runs inside a
    live gateway or desktop backend (hermes_cli/cron.py - there is no standalone
    cron daemon), and `hermes update` both wipes web_dist and stops those
    processes. So the cron watchdog cannot fire until a gateway is back, and
    then only on its next tick - up to 30 minutes of a reverted dashboard.
    Each platform therefore gets an OS-level scheduler that runs OUTSIDE the
    Hermes process tree, on a Python that is not the venv:

      Windows  Scheduled Task (schtasks), every AUTOSTART_MINUTES
      macOS    LaunchAgent in ~/Library/LaunchAgents, every AUTOSTART_MINUTES
      Linux    systemd user timer, every AUTOSTART_MINUTES

    Best-effort and idempotent: the scheduler is only touched when something is
    missing or points elsewhere. Never fatal. Set LCARS_NO_AUTOSTART=1 to skip
    it (the journey/CI tests do, so they leave nothing on the host).

    `platform` is injectable so the cross-OS tests can drive each branch from
    one host; production callers omit it.

    Returns True when a healer outside Hermes is in place.
    """
    if os.environ.get("LCARS_NO_AUTOSTART"):
        return False
    plat = platform or sys.platform
    if not (plat == "win32" or plat == "darwin" or plat.startswith("linux")):
        return False
    hermes_home = find_hermes_home() or (hermes_root_from_target(target) if target else None)
    if not hermes_home:
        return False
    script = _healer_script(hermes_home)
    if not script:
        return False

    if plat == "win32":
        pythonw = _pick_pythonw()
        if not pythonw:
            print("[LCARS] note: no non-Hermes Python found - the scheduled-task "
                  "auto-heal was skipped (the cron watchdog still works).")
            return False
        existing = _task_command(AUTOSTART_TASK)
        if existing == ('"' + pythonw + '"', '"' + script + '"'):
            return True                   # already current
        if not _register_task(AUTOSTART_TASK, pythonw, script):
            print("[LCARS] note: could not register the scheduled-task auto-heal - "
                  "the cron watchdog still works.")
            return False
        print("[LCARS] scheduled-task auto-heal " +
              ("re-pointed at " + script if existing else
               "registered: " + AUTOSTART_TASK + " (every " + str(AUTOSTART_MINUTES) +
               " min, runs without Hermes - survives updates with every Hermes process stopped)"))
        return True

    python3 = _pick_python3()
    if not python3:
        print("[LCARS] note: no python3 outside the Hermes venv was found - the "
              "auto-heal was left to the hermes cron job.")
        return False

    if plat == "darwin":
        if not _write_if_changed(_plist_path(), _plist_text(python3, script)):
            return True                   # already current
        ok = _register_launchd()
        if ok:
            print("[LCARS] LaunchAgent auto-heal registered: " + AUTOSTART_LABEL +
                  " (every " + str(AUTOSTART_MINUTES) + " min, runs without Hermes)")
        else:
            print("[LCARS] note: wrote " + _plist_path() + " but could not load it - "
                  "load it yourself with: launchctl load -w " + _plist_path())
        return ok

    # linux: a systemd user timer (service + timer unit pair)
    d = _systemd_dir()
    svc = os.path.join(d, AUTOSTART_LABEL + ".service")
    changed = _write_if_changed(svc, _systemd_service_text(python3, script))
    changed = _write_if_changed(os.path.join(d, AUTOSTART_LABEL + ".timer"),
                                _systemd_timer_text()) or changed
    if not changed:
        return True                       # already current
    if _register_systemd():
        print("[LCARS] systemd user timer auto-heal registered: " + AUTOSTART_LABEL +
              ".timer (every " + str(AUTOSTART_MINUTES) + " min, runs without Hermes)")
        return True
    print("[LCARS] note: wrote " + svc + " and the matching .timer but could not enable "
          "them - enable them yourself with: systemctl --user enable --now "
          + AUTOSTART_LABEL + ".timer")
    return False


def _pick_python3():
    """A ``python3`` outside the Hermes venv (macOS / Linux), or None.

    Same reasoning as ``_pick_pythonw``: the venv is what `hermes update`
    replaces and locks, so a healer must never depend on it.
    """
    cands = [shutil.which("python3") or "",
             "/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"]
    cands.append(sys.executable or "")
    for cand in cands:
        if not cand or not os.path.isfile(cand):
            continue
        lowered = cand.replace("\\", "/").lower()
        if "hermes-agent" in lowered or "/venv/" in lowered:
            continue
        return cand
    return None


def _plist_path():
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents",
                        AUTOSTART_LABEL + ".plist")


def _systemd_dir():
    return os.path.join(os.path.expanduser("~"), ".config", "systemd", "user")


def _read_text(path):
    """File contents, or "" when it cannot be read (missing = absent)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _write_if_changed(path, text):
    """Write *text* to *path* unless it already matches. True when written."""
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                if f.read() == text:
                    return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return True
    except OSError:
        return False


def _plist_text(python3, script):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>Label</key>\n'
        '  <string>' + AUTOSTART_LABEL + '</string>\n'
        '  <key>ProgramArguments</key>\n'
        '  <array>\n'
        '    <string>' + python3 + '</string>\n'
        '    <string>' + script + '</string>\n'
        '  </array>\n'
        '  <key>RunAtLoad</key>\n'
        '  <true/>\n'
        '  <key>StartInterval</key>\n'
        '  <integer>' + str(AUTOSTART_MINUTES * 60) + '</integer>\n'
        '</dict>\n'
        '</plist>\n'
    )


def _systemd_service_text(python3, script):
    return (
        '[Unit]\n'
        'Description=Re-apply the LCARS dashboard skin after a Hermes update\n'
        '\n'
        '[Service]\n'
        'Type=oneshot\n'
        'ExecStart=' + python3 + ' ' + script + '\n'
    )


def _systemd_timer_text():
    return (
        '[Unit]\n'
        'Description=Check every ' + str(AUTOSTART_MINUTES) + ' min whether the LCARS '
        'dashboard skin is missing\n'
        '\n'
        '[Timer]\n'
        'OnBootSec=' + str(AUTOSTART_MINUTES) + 'min\n'
        'OnUnitActiveSec=' + str(AUTOSTART_MINUTES) + 'min\n'
        'AccuracySec=10s\n'
        'Unit=' + AUTOSTART_LABEL + '.service\n'
        '\n'
        '[Install]\n'
        'WantedBy=timers.target\n'
    )


def _run_quiet(cmd):
    """Run a scheduler command best-effort. True on exit 0."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _register_launchd():
    """Load the LaunchAgent (bootstrap on modern macOS, load -w as fallback)."""
    plist = _plist_path()
    if hasattr(os, "getuid"):
        if _run_quiet(["launchctl", "bootstrap", "gui/" + str(os.getuid()), plist]):
            return True
    return _run_quiet(["launchctl", "load", "-w", plist])


def _register_systemd():
    """Reload the user manager and enable + start the timer."""
    _run_quiet(["systemctl", "--user", "daemon-reload"])
    return _run_quiet(["systemctl", "--user", "enable", "--now",
                       AUTOSTART_LABEL + ".timer"])


def _all_scripts_dirs(hermes_home):
    """Synced scripts/ dirs across the resolved home AND the platform defaults.

    HERMES_HOME can point at a single profile, so a stand-down marker written
    only under that home would be invisible to a watchdog copy running from the
    platform-default root (and vice versa). Covering both keeps "auto-heal is
    off" true for every copy that could run.

    A scratch home (journey/CI run under a temp dir) is the exception: it must
    never write into the real machine's roots, so it only ever touches its own.
    """
    if _is_temp_path(hermes_home):
        return _scripts_dirs(hermes_home)
    dirs = []
    for home in [hermes_home] + [r for r in _data_roots() if r and os.path.isdir(r)]:
        for d in _scripts_dirs(home):
            if d not in dirs:
                dirs.append(d)
    return dirs


def _write_disabled_markers(hermes_home, disabled):
    """(Un)set the watchdog stand-down marker in every synced scripts/ folder.

    `--remove` / `--restore` mean the user does not want the skin any more, and
    a watchdog that re-applies a missing skin would put the theme straight back
    - within a minute, now that the scheduled task exists - so the revert would
    look broken. The marker makes BOTH heal layers (cron job and task) stand
    down until the installer is run again. Best effort, never fatal.
    """
    for sd in _all_scripts_dirs(hermes_home):
        target = os.path.join(sd, AUTOSTART_DISABLED_NAME)
        try:
            if disabled:
                if os.path.isdir(sd):
                    with open(target, "w", encoding="utf-8") as f:
                        f.write("The LCARS dashboard skin was reverted with "
                                "apply.py --remove / --restore.\n"
                                "Re-run the installer to switch auto-heal back on.\n")
            elif os.path.isfile(target):
                os.remove(target)
        except OSError:
            continue


def delete_autostart_healer(platform=None):
    """Remove the OS-level healer for this platform. True when it is gone.

    Windows: delete the scheduled task. macOS: bootout/unload the LaunchAgent
    and remove the plist. Linux: disable + stop the timer and remove the unit
    files. Best effort - a missing scheduler entry is not an error.
    """
    plat = platform or sys.platform
    if plat == "win32":
        try:
            proc = subprocess.run(["schtasks", "/Delete", "/TN", AUTOSTART_TASK, "/F"],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0
    if plat == "darwin":
        plist = _plist_path()
        gone = not os.path.isfile(plist)
        for cmd in (["launchctl", "bootout", "gui/" + str(getattr(os, "getuid", lambda: 0)()),
                     plist],
                    ["launchctl", "unload", "-w", plist]):
            _run_quiet(cmd)
        try:
            if os.path.isfile(plist):
                os.remove(plist)
            return True
        except OSError:
            return gone
    if plat.startswith("linux"):
        _run_quiet(["systemctl", "--user", "disable", "--now", AUTOSTART_LABEL + ".timer"])
        removed = True
        for name in (AUTOSTART_LABEL + ".service", AUTOSTART_LABEL + ".timer"):
            path = os.path.join(_systemd_dir(), name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                removed = False
        _run_quiet(["systemctl", "--user", "daemon-reload"])
        return removed
    return False


def disable_autoheal(target=None):
    """Pause both heal layers after a revert. Returns True when Hermes was found."""
    hermes_home = find_hermes_home() or (hermes_root_from_target(target) if target else None)
    if not hermes_home:
        return False
    _write_disabled_markers(hermes_home, True)
    # The OS-level healer is machine-wide (Windows task) or a per-user agent on
    # the real home, so a scratch/sandbox run (journey test, CI, a bundle
    # extracted under Temp) must never remove the real one. LCARS_NO_AUTOSTART
    # opts out entirely.
    if not (_is_temp_path(hermes_home) or os.environ.get("LCARS_NO_AUTOSTART")):
        delete_autostart_healer()
    print("[LCARS] auto-heal paused - the theme will stay off until you run the "
          "installer again.")
    return True


def autostart_status(target=None, platform=None):
    """One-line readout of how (or whether) the skin recovers after an update."""
    plat = platform or sys.platform
    hermes_home = find_hermes_home() or (hermes_root_from_target(target) if target else None)
    if not hermes_home:
        return "unknown - Hermes data folder not found"
    script = _healer_script(hermes_home)
    if not script:
        return "NOT synced - run the installer (without --check) to set it up"
    if os.path.isfile(os.path.join(os.path.dirname(script), AUTOSTART_DISABLED_NAME)):
        return "paused (last run reverted the skin; re-run the installer to re-arm)"
    every = " every " + str(AUTOSTART_MINUTES) + " min"
    if plat == "win32":
        pythonw = _pick_pythonw() or ""
        if _task_command(AUTOSTART_TASK) == ('"' + pythonw + '"', '"' + script + '"'):
            return "scheduled task " + AUTOSTART_TASK + every
        return "cron job only - re-run the installer to register " + AUTOSTART_TASK
    if plat == "darwin":
        if _plist_text(_pick_python3() or "", script) == _read_text(_plist_path()):
            return "LaunchAgent " + AUTOSTART_LABEL + every
        return "cron job only - re-run the installer to write the LaunchAgent"
    if plat.startswith("linux"):
        if (_systemd_timer_text() == _read_text(os.path.join(_systemd_dir(), AUTOSTART_LABEL + ".timer"))
                and os.path.isfile(os.path.join(_systemd_dir(), AUTOSTART_LABEL + ".service"))):
            return "systemd user timer " + AUTOSTART_LABEL + every
        return "cron job only - re-run the installer to write the systemd timer"
    return "cron watchdog (hermes cron)"


def _not_found(target, explicit, gui):
    """Dashboard could not be found anywhere: tell the user what is needed and
    how to get/install it (pop-up + console + open the install guide)."""
    msg = (
        "The installer could not find your Hermes dashboard "
        "(web_dist/index.html).\n\n"
        "This skin is applied ON TOP of the Hermes Agent web dashboard - "
        "Hermes must be installed and its web dashboard started at least once "
        "before this installer can do anything.\n\n"
        "What to do:\n"
        "  1. Install Hermes Agent from:\n"
        "     " + preflight.HERMES_INSTALL_URL + "\n"
        "  2. Start Hermes and open the web dashboard "
        "(http://127.0.0.1:9119)\n"
        "  3. Run this installer again (run.bat / run.sh)\n\n"
        "Already installed somewhere unusual? Point at it directly:\n"
        "  python3 apply.py --target FULL/PATH/TO/web_dist/index.html\n"
        "  (or set HERMES_HOME to your Hermes data folder)"
    )
    preflight.notify("Hermes Dashboard Not Found", msg, gui=gui)
    if gui:
        preflight.open_url(preflight.HERMES_INSTALL_URL)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    explicit = parse_target(args)
    check_only = "--check" in args
    print_only = "--print-target" in args
    # A bare run (double-click / run.bat / run.sh) is a GUI session; flag-based
    # power-user commands keep to the console.
    gui = not (explicit or check_only or print_only or "--list-backups" in args)

    target = explicit or find_target()

    # PC search when the standard locations came up empty.
    if not target and not explicit:
        target, matches, summary = preflight.search_dashboard()
        if target:
            print("[LCARS] Dashboard not in the usual folders - searched this PC"
                  " and found it at:")
            print("[LCARS]   " + target)
            if len(matches) > 1:
                print("[LCARS] (also found " + str(len(matches) - 1)
                      + " other candidate(s) - using the first)")
        else:
            print("[LCARS] " + summary)

    if not target:
        if check_only:
            print("[LCARS] target: NOT FOUND")
            print("[LCARS] result: NOT READY (Hermes dashboard not installed)")
            sys.exit(1)
        if print_only:
            print("(not found)")
            sys.exit(1)
        _not_found(target, explicit, gui)

    if not os.path.isfile(target):
        preflight.notify(
            "Target File Not Found",
            "The dashboard file you pointed at does not exist:\n  " + target
            + "\n\nCheck the path - it should end in web_dist/index.html "
              "inside your Hermes install. Re-run with the correct path, or "
              "run without --target and let the installer search for it.",
            gui=gui)
        sys.exit(1)

    if print_only:
        print(target)
        return

    if "--list-backups" in args:
        names = list_backups(target)
        print("Saved backups:" if names else "No backups yet.")
        for n in names:
            print("  " + n)
        return

    if "--restore" in args:
        i = args.index("--restore")
        name = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else None
        restore(target, name)
        disable_autoheal(target)
        return

    if "--remove" in args:
        # Prefer a full revert to the user's pre-skin state (backup); if none
        # exists, strip the injected markers in place.
        names = list_backups(target)
        if names:
            restore(target, names[0])
        else:
            strip_skin(target)
        print("[LCARS] skin removed; original dashboard restored at " + target)
        disable_autoheal(target)
        return

    # ---- default: apply the skin -------------------------------------------
    if check_only:
        fatals, warnings = preflight.check_bundle(HERE)
        print("[LCARS] ---- installer check ----")
        print("[LCARS] python: " + sys.version.split()[0] + " (3.8+ required: ok)")
        print("[LCARS] target: " + target)
        missing = preflight.missing_anchors(target)
        print("[LCARS] dashboard structure: "
              + ("ok" if not missing else "MISSING " + ", ".join(missing)))
        print("[LCARS] auto-heal after a Hermes update: " + autostart_status(target))
        for w in warnings:
            print("[LCARS] warning: " + w)
        if fatals:
            for f in fatals:
                print("[LCARS] FATAL: " + f)
            print("[LCARS] result: NOT READY (" + str(len(fatals))
                  + " fatal problem(s))")
            sys.exit(1)
        print("[LCARS] result: READY")
        return

    fatals, warnings = preflight.check_bundle(HERE)
    for w in warnings:
        print("[LCARS] warning: " + w)
    if fatals:
        msg = (
            "This installer bundle is incomplete or damaged.\n\n"
            "Missing or broken:\n"
            + "\n".join("  - " + f for f in fatals)
            + "\n\nWhat to do:\n"
            "  1. Re-download the latest lcars-installer.zip from:\n"
            "     " + preflight.RELEASES_URL + "\n"
            "  2. Extract it into a fresh folder (never mix old and new files)\n"
            "  3. Run run.bat (Windows) or run.sh (macOS / Linux) again"
        )
        preflight.notify("LCARS Installer Bundle Damaged", msg, gui=gui)
        if gui:
            preflight.open_url(preflight.RELEASES_URL)
        sys.exit(1)

    # Verify the target really is a structurally sound Hermes dashboard before
    # backing anything up or writing anything.
    missing = preflight.missing_anchors(target)
    if missing:
        preflight.notify(
            "Not a Hermes Dashboard",
            "The file found at:\n  " + target
            + "\n\ndoes not look like a Hermes dashboard (missing: "
            + ", ".join(missing) + ").\n\n"
            "The installer never modifies files it cannot verify, so nothing "
            "was changed.\n\nWhat to do:\n"
            "  - If you used --target, point it at the real web_dist/index.html\n"
            "  - If Hermes was updated, reinstall/repair it and re-run this installer\n"
            "  - Restore a backup if the dashboard looks broken:\n"
            "      python3 apply.py --restore",
            gui=gui)
        sys.exit(1)

    # Back up the user's CURRENT config only the first time (before skin exists).
    if not has_skin(target):
        ts = make_backup(target)
        print("[LCARS] backed up current dashboard to backups/" + ts)

    try:
        cmd = [sys.executable, SKIN, "--target", target]
        for flag in ("--no-chat-stability", "--chat-stability"):
            if flag in args:
                cmd.append(flag)
        proc = subprocess.run(cmd)
    except FileNotFoundError:
        sys.stderr.write(
            "Could not start Python to apply the skin.\n"
            "Install Python 3 from the python/ folder in this bundle (or https://www.python.org/downloads/),\n"
            "then run apply.py again.\n"
        )
        sys.exit(1)
    if proc.returncode == 0 and sync_autoheal(target):
        print("[LCARS] auto-heal watchdog synced to Hermes scripts/ (survives updates)")
        # …and make it run outside Hermes too: a Hermes update wipes web_dist
        # AND stops the gateway whose cron ticker would otherwise heal it.
        ensure_autostart_healer(target)
    if proc.returncode == 0 and has_skin(target):
        print("[LCARS] verified: skin markers present in dashboard — done.")
    elif proc.returncode == 0:
        print("[LCARS] WARNING: skin markers not found after apply — please report this.")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stderr.write("\n[LCARS] cancelled.\n")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:  # never show a raw traceback to end users
        gui = not any(a.startswith("--") for a in sys.argv[1:])
        preflight.notify(
            "[LCARS] unexpected error",
            "Something went wrong: " + str(exc) + "\n\n"
            "If this persists:\n"
            "  - run  python3 apply.py --check  for a readiness report\n"
            "  - run  python3 apply.py --target FULL/PATH/TO/web_dist/index.html\n"
            "  - or open an issue at "
            "https://github.com/ModdySwag/Hermes-Dashboard-Multi-Themes-Edition/issues",
            gui=gui)
        sys.exit(1)
