# Hermes LCARS Dashboard — Multi-Theme Skin

> **Prerequisite:** This is a skin for the **Hermes Agent web dashboard**. You must have
> Hermes Agent installed and the web dashboard enabled *before* using this mod.
>
> Install/setup info: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard

An LCARS themed, multi-theme reskin for the **Hermes Agent** web dashboard.
Visual-only, safe to apply, and it never touches your Hermes config, sessions, or keys.

Built by Moddy — http://www.moddys.net · Licensed MIT.

## What it does
- Several LCARS themes (an embedded "Enterprise Bridge" photo + wallpaper themes).
- One-command install: `run.bat` (Windows) or `run.sh` (macOS / Linux).
- Auto-detects your Hermes dashboard, backs it up, and applies the skin.
- **Survives Hermes updates:** a bundled auto-heal watchdog (`lcars_autoheal.sh`) re-skins the dashboard after any `hermes update` — on launch and via an optional scheduled cron job. See `read me.txt` for setup.
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

  Thanks for checking out this release
  Enjoy the LCARS theme on your Hermes dashboard :) Cheers Moddy !
