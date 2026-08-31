#!/usr/bin/env bash
# LCARS dashboard auto-heal watchdog.
#
# Hermes updates overwrite hermes_cli/web_dist/index.html, which strips the
# LCARS skin. This script runs on a schedule (via `hermes cron`) and re-applies
# the skin ONLY if it detects the dashboard is currently NOT skinned. When the
# skin is already present it stays completely silent (no output -> no delivery),
# so it does not spam. If a Hermes update reverted the skin, it re-applies and
# emits one line so you know an auto-heal fired.
#
# Cron jobs survive `hermes update` (see update_cmd.py: restore_cron_jobs_if_emptied),
# so this watchdog keeps working across updates on Windows / macOS / Linux.
#
# The LCARS installer bundle lives wherever `lcars-installer/` is. We resolve it
# in this order: LCARS_INSTALL_DIR env -> $LOCALAPPDATA/hermes/lcars-installer
# -> ~/lcars-installer (the actual install on MODDY's PC) -> search common roots.

set -u

resolve_dir() {
    if [ -n "${LCARS_INSTALL_DIR:-}" ] && [ -f "${LCARS_INSTALL_DIR}/apply.py" ]; then
        echo "$LCARS_INSTALL_DIR"; return 0
    fi
    local home="${HOME:-$(cygpath -u "$USERPROFILE" 2>/dev/null)}"
    local localapp="${LOCALAPPDATA:-$home/AppData/Local}"
    local candidates=(
        "$localapp/hermes/lcars-installer"
        "$home/lcars-installer"
        "$home/AppData/Local/hermes/lcars-installer"
    )
    for c in "${candidates[@]}"; do
        if [ -f "$c/apply.py" ]; then echo "$c"; return 0; fi
    done
    return 1
}

DIR="$(resolve_dir)" || { echo "[LCARS auto-heal] could not locate lcars-installer bundle; skipping." >&2; exit 0; }
cd "$DIR" || exit 0

if command -v python >/dev/null 2>&1; then PY=python
elif command -v python3 >/dev/null 2>&1; then PY=python3
else exit 0
fi

# Probe: is the dashboard currently skinned? (1 = yes, 0 = no/reverted)
SKINNED=$("$PY" - <<'PY' 2>/dev/null
import sys, os
sys.path.insert(0, os.getcwd())
try:
    import apply as a
    t = a.find_target()
    print("1" if (t and a.has_skin(t)) else "0")
except Exception:
    print("0")
PY
)

if [ "$SKINNED" = "1" ]; then
    exit 0
fi

# Skin missing (likely reverted by a Hermes update) — re-apply it.
OUT=$("$PY" apply.py 2>&1) || exit 0
if echo "$OUT" | grep -q "skin applied"; then
    echo "[LCARS auto-heal] dashboard skin was missing — re-applied after update/revert."
fi
