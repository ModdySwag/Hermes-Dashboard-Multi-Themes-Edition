# Security Policy

## Scope
This project is a **visual skin** for the Hermes Agent web dashboard. It edits
only the dashboard's `index.html` and copies wallpaper images next to it. It
does **not** read, transmit, or modify any Hermes config, sessions, API keys,
or credentials.

## What it does NOT do
- No network calls at apply time or at runtime (the dashboard is served locally).
- No `eval`, no `innerHTML` with external data, no shell-out with user input.
- No reading of files outside the Hermes dashboard directory.
- No collection of telemetry or analytics.

## Known surfaces
- `apply.py` writes a timestamped backup of `web_dist/index.html` into
  `backups/` next to the bundle and mirrors it into `HERMES_HOME/lcars-backups/`,
  then applies the skin via `apply_lcars_skin.py`. `--remove` / `--restore`
  reverse it.
- `run.bat` (Windows) may launch the bundled Python installer with
  `/quiet PrependPath=1` only when Python is absent. Review the flags if you
  repackage.

## Reporting a vulnerability
Email **admin@moddys.net** with STEPS TO REPRODUCE. Please do not open public
issues for security reports.

## Supply-chain notes
- The bundled Python installers (`python/`) are the official Python Software
  Foundation releases from python.org. Verify checksums against
  https://www.python.org/downloads/ before trusting a re-hosted copy.
- Wallpaper images (`lcars-bg/`, `lcars-bg.jpg`) are original works by Moddy
  (http://www.moddys.net), licensed MIT - see `assets/ATTRIBUTION.md`. No
  third-party imagery is included.
