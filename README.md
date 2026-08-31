# Hermes LCARS Dashboard — Multi-Theme Skin

A Star-Trek / LCARS themed, multi-theme reskin for the **Hermes Agent** web dashboard.
Visual-only, safe to apply, and it never touches your Hermes config, sessions, or keys.

Built by Moddy — http://www.moddys.net · Licensed MIT.

## What it does
- 13 LCARS themes (an embedded "Enterprise Bridge" photo + 12 wallpaper themes).
- One-command install: `run.bat` (Windows) or `run.sh` (macOS / Linux).
- Auto-detects your Hermes dashboard, backs it up, and applies the skin.
- Revert any time: `python3 apply.py --restore` · Remove: `python3 apply.py --remove`.

## Quick start
1. Download `lcars-installer.zip` from the [Releases](../../releases) page and extract it.
2. Double-click `run.bat` (Windows) or `run.sh` (macOS / Linux).
3. Refresh your dashboard at `http://127.0.0.1:9119/sessions`.

Full step-by-step (including a Linux-noob walkthrough) is in **`read me.txt`** shipped in the zip.

## System & browser compatibility

**Operating systems** (where Hermes Agent runs):
- Windows 10 / 11 — `run.bat`
- macOS 12 Monterey and newer — `run.sh`
- Linux — any modern distro (Debian/Ubuntu, Fedora, Mint, Arch, …) — `bash run.sh`
- Requires **Python 3.8+**. If missing, the launcher installs it from the bundled
  `python/` installers — no internet needed.

**Browsers** (the dashboard is viewed in your browser):
- Google Chrome — fully supported
- Microsoft Edge — fully supported
- Mozilla Firefox — fully supported
- Safari / WebKit — fully supported

Tested headless in Chromium, Firefox, and WebKit with **zero script errors**.

**Dashboard requirements:**
- Hermes Agent with the web dashboard feature enabled.
- The skin injects a scoped `html.lcars-skin` layer and never modifies Hermes' own
  logic, config, sessions, API keys, or data.
- Your original dashboard is auto-backed-up on first apply; `--restore` returns it exactly.

**Privacy & safety:**
- No network calls from the skin. No telemetry. No external fonts.
- Wallpapers are original works by Moddy (MIT licensed) — see `assets/ATTRIBUTION.md`.
- Security notes: `SECURITY.md`.

## Thanks & credits

   _/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_
  /                                                             /
 /   Thanks for checking out this release                    /
/   Enjoy the LCARS theme on your Hermes dashboard :)        /
 \                                                         /
  \_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/

      ___  ___  ___    _  _  ___  ___  _   _  ___
     | _ \| __|/ _ \  | \| |/ _ \| __|| | | |/ __|
     |   /| _|| (_) | | .` | (_) | _| | |_| | (_ |
     |_|_\\|___|\___/  |_|\_|\___/|___| \___/ \___|
