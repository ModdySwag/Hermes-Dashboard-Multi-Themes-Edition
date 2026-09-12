#!/usr/bin/env python3
"""LCARS dashboard auto-heal watchdog.

Hermes updates overwrite hermes_cli/web_dist/index.html, which strips the LCARS
skin. This watchdog re-applies the skin ONLY when it is actually missing. When the
skin is present it stays completely silent (no output -> nothing delivered).

Why Python and not the old lcars_autoheal.sh: Hermes runs a cron job's .sh/.bash
script *through `wsl.exe` on Windows*. On a machine without a registered WSL
distribution that process dies before the script's first line —

    Windows Subsystem for Linux has no installed distributions.

— so the job reported "script failed" on every tick, forever, while the watchdog
itself was perfectly fine. Python jobs are launched with a real interpreter and
carry no WSL dependency, and this installer already requires Python 3.8+, so one
script now covers Windows, macOS and Linux.

Setup (one time):
    hermes cron create "*/30 * * * *" \
      --name "LCARS dashboard auto-heal" \
      --script "lcars_autoheal.py" --no-agent --deliver local

Exit status is always 0: the scheduler treats a non-zero exit as an error and
would deliver an alert on every tick. Real failures are printed instead.
"""
import glob
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "lcars_autoheal_state.json")
LOG = os.path.join(HERE, "lcars_autoheal.log")
SIDECAR = "lcars_install_dir.txt"
DISABLED = os.path.join(HERE, "lcars_autoheal.disabled")
NOTICE_EVERY = 24 * 3600          # a missing bundle is reported at most daily
REAPPLY_TIMEOUT = 300
LOG_MAX = 200_000                 # bytes before the log is trimmed


def _log(line):
    """Append one timestamped line to the watchdog log. Never raises.

    A console-less launch (Windows Task Scheduler, systemd, launchd) discards
    stdout, so the log is the only durable record that a heal happened - and
    with `has_skin()` returning silently on a healthy install, the absence of
    log lines is itself the evidence that nothing needed doing.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if os.path.getsize(LOG) > LOG_MAX:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-500:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except OSError:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp}  {line}\n")
    except OSError:
        pass


def _valid(d):
    """A bundle is usable only if it actually contains apply.py."""
    return bool(d) and os.path.isfile(os.path.join(d, "apply.py"))


def _is_temp(p):
    """True when a path lives under a temp folder.

    Journey/CI runs of this installer execute from a scratch copy under
    AppData/Local/Temp, and that copy writes its own location into the sidecar.
    Such a bundle must never win over a real installation - otherwise the
    watchdog would 'heal' a throwaway copy while the real dashboard stayed
    reverted.
    """
    if not p:
        return False
    return any(part.lower() in ("temp", "tmp") for part in os.path.normpath(p).split(os.sep))


def _sidecar(base):
    """Resolve a lcars_install_dir.txt sidecar to a usable bundle, or None."""
    if not base:
        return None
    p = os.path.join(base, SIDECAR)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            d = (f.readline() or "").strip().strip('"')
    except OSError:
        return None
    return d if _valid(d) else None


def _candidates():
    """Every place a bundle may live, most reliable source first."""
    env = os.environ.get("LCARS_INSTALL_DIR")
    if env:
        yield env
    hermes_home = os.environ.get("HERMES_HOME")
    yield _sidecar(HERE)
    yield _sidecar(hermes_home)
    yield _sidecar(os.path.join(hermes_home, "scripts") if hermes_home else None)

    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    yield os.path.join(localapp, "hermes", "lcars-installer")
    yield os.path.join(home, "lcars-installer")
    yield os.path.join(localapp, "hermes", "profiles", "deepseek", "lcars-installer")
    for c in glob.glob(os.path.join(home, "Desktop", "*", "lcars-installer")):
        yield c


def resolve_dir():
    """First usable bundle, preferring real installs over temp/scratch copies."""
    usable = [c for c in _candidates() if _valid(c)]
    if not usable:
        return None
    for c in usable:
        if not _is_temp(c):
            return c
    return usable[0]


def notice(msg):
    """Report at most once per NOTICE_EVERY, so a broken install can't spam."""
    st = {}
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
    if time.time() - float(st.get("notice_at", 0)) < NOTICE_EVERY:
        return
    _log(msg)
    print(msg)
    st["notice_at"] = time.time()
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass


def load_bundle(d):
    """Import <d>/apply.py by explicit path.

    Not `import apply`: that is keyed by module name, so once any bundle has been
    imported a different bundle resolved later would silently reuse the first one.
    """
    sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("lcars_apply",
                                                 os.path.join(d, "apply.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lcars_apply"] = mod
    spec.loader.exec_module(mod)
    return mod


def run():
    # The user reverted the skin on purpose (apply.py --remove / --restore wrote
    # this marker): stand down, or the watchdog would put the theme back within
    # a minute and the revert would look broken. Running the installer again
    # clears the marker.
    if os.path.isfile(DISABLED):
        return

    d = resolve_dir()
    if not d:
        notice("[LCARS auto-heal] could not locate the lcars-installer bundle. "
               "Set LCARS_INSTALL_DIR, or re-run the installer so it writes "
               "lcars_install_dir.txt next to this script. Nothing was applied.")
        return

    try:
        apply_mod = load_bundle(d)
    except Exception as e:                                   # noqa: BLE001 - report, never crash
        notice(f"[LCARS auto-heal] could not load apply.py from {d}: "
               f"{type(e).__name__}: {e}")
        return

    try:
        target = apply_mod.find_target()
    except Exception as e:                                   # noqa: BLE001
        notice(f"[LCARS auto-heal] apply.find_target() failed: {type(e).__name__}: {e}")
        return

    if not target:
        notice("[LCARS auto-heal] could not find the Hermes dashboard "
               "(hermes_cli/web_dist/index.html). Nothing was applied.")
        return

    try:
        if apply_mod.has_skin(target):
            return                                           # healthy: stay silent
    except OSError:
        pass

    # Skin missing (Hermes update/revert) -> re-apply, then verify the effect.
    output = ""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(d, "apply.py"), "--target", target],
            cwd=d, capture_output=True, text=True, timeout=REAPPLY_TIMEOUT)
        output = (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:                                   # noqa: BLE001
        output = f"{type(e).__name__}: {e}"

    try:
        healed = apply_mod.has_skin(target)
    except OSError:
        healed = False

    if healed:
        msg = ("[LCARS auto-heal] dashboard skin was missing (Hermes update or revert) "
               f"- re-applied to {target}")
        _log(msg)
        print(msg)
    else:
        msg = ("[LCARS auto-heal] WARNING: the skin is missing and re-applying did not "
               "restore it.")
        _log(msg)
        print(msg)
        for line in [l for l in output.strip().splitlines() if l.strip()][-3:]:
            _log("  " + line)
            print("  " + line)


def main():
    try:
        run()
    except Exception as e:                                   # noqa: BLE001
        print(f"[LCARS auto-heal] unexpected fault: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
