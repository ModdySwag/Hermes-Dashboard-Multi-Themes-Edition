#!/usr/bin/env python3
"""apply_lcars_skin.py - idempotent LCARS multi-theme reskin for the Hermes dashboard.

VISUAL ONLY. No assets, scripts, the session token, or config are modified.
All skin rules are scoped under `html.lcars-skin`, so the dashboard's own
styling is untouched when that class is absent.

WHAT IT INJECTS (stripped & re-added on every run, so it survives Hermes updates):
  * A flash-prevention script, after <title>.
  * A <style> block (scoped) before </head>: 13 per-theme palettes + controls.
  * A "Theme Options" toggle + panel (theme cycler + bg-opacity slider) before </body>.

SAFETY: no eval, no innerHTML with external data, no network calls. The only
DOM writes are textContent / CSS custom properties built from local constants.
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_JPG = os.path.join(HERE, "lcars-bg.jpg")

# Stable markers used to strip a previous run before re-injecting.
M_HEAD_S, M_HEAD_E = "<!-- LCARS_HEAD_START -->", "<!-- LCARS_HEAD_END -->"
M_STYLE_S, M_STYLE_E = "<!-- LCARS_STYLE_START -->", "<!-- LCARS_STYLE_END -->"
M_BODY_S, M_BODY_E = "<!-- LCARS_BODY_START -->", "<!-- LCARS_BODY_END -->"
# Chat view-stability add-on (own block: stripped/re-injected with the rest).
M_STABLE_S, M_STABLE_E = "<!-- HERMES_CHAT_VIEW_STABLE_START -->", "<!-- HERMES_CHAT_VIEW_STABLE_END -->"

# Where a "--no-chat-stability" choice is remembered. After every Hermes update
# the auto-heal runs this engine again through apply.py, with no flags at all,
# so without a marker on disk the add-on would be back within a minute. The
# path is overridable for tests.
STABLE_OFF = os.environ.get("LCARS_CHAT_STABILITY_MARKER",
                            os.path.join(HERE, "lcars_chat_stability.disabled"))


def copy_assets(target):
    """Copy the bundled wallpaper files into Hermes' web_dist/lcars-bg/ so the
    non-bridge themes (1-12) resolve at /lcars-bg/*.jpg. Idempotent, and skips
    files that are already in place unchanged (size + mtime)."""
    web_dist = os.path.dirname(os.path.abspath(target))
    dest = os.path.join(web_dist, "lcars-bg")
    src = os.path.join(HERE, "lcars-bg")
    if not os.path.isdir(src):
        return 0
    os.makedirs(dest, exist_ok=True)
    copied = 0
    for fn in os.listdir(src):
        if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        s, d = os.path.join(src, fn), os.path.join(dest, fn)
        try:
            if not (os.path.isfile(d) and os.path.getsize(d) == os.path.getsize(s)
                    and os.path.getmtime(d) >= os.path.getmtime(s)):
                shutil.copyfile(s, d)
                copied += 1
        except OSError:
            continue
    return len(os.listdir(dest))


def strip_block(s, start, end, separator_before=False):
    """Remove every span between start/end markers (idempotent re-apply).

    It also removes the whitespace separator the injector placed beside the
    block, which is what makes re-applying byte-stable. `main()` writes
    ``"\\n" + block`` after ``</title>`` and ``block + "\\n  "`` before
    ``</head>`` / ``</body>``; stripping only the markers left that separator
    behind and every further apply added another one, so the dashboard grew
    ~3 blank lines per manual re-run. `separator_before` names the side the
    injector's separator sits on (True for the ``</title>`` site, False for the
    two end-tag sites). On a pristine file there are no markers, so this is a
    no-op: the first apply is unchanged and --restore still returns the
    original bytes.
    """
    while True:
        i = s.find(start)
        if i == -1:
            return s
        j = s.find(end, i)
        if j == -1:
            return s
        j += len(end)
        if separator_before:
            a = i
            while a > 0 and s[a - 1] in " \t\r\n":
                a -= 1
            s = s[:a] + s[j:]
        else:
            k = j
            while k < len(s) and s[k] in " \t\r\n":
                k += 1
            s = s[:i] + s[k:]


def _repair_match(ref_name, disk_by_ext):
    """Find the on-disk asset that a missing ref should point at.

    Names are <family>-<hash>.<ext>; BOTH family (rolldown-runtime-*) and the
    8-char hash (index-Cfbh-Yd9) may contain dashes, so families are matched
    by shared leading dash-segments and the winner is the file whose name
    shares the longest leading prefix with the stale reference. Returns None
    when no sensible replacement exists.
    """
    rstem, dot, ext = ref_name.rpartition(".")
    if ext not in disk_by_ext:
        return None
    rparts = rstem.split("-")
    best, best_len = None, -1
    for fn in disk_by_ext[ext]:
        fparts = fn.rsplit(".", 1)[0].split("-")
        shared = 0
        while (shared < len(rparts) and shared < len(fparts)
               and rparts[shared] == fparts[shared]):
            shared += 1
        if shared == 0:
            continue                    # unrelated file
        common = 0
        for a, b in zip(ref_name, fn):
            if a != b:
                break
            common += 1
        if common > best_len or (common == best_len and fn < best):
            best, best_len = fn, common
    return best


def fix_stale_asset_references(html, web_dist):
    """Repair stale asset-hash references in index.html.

    Hermes' Vite/Rolldown build emits hashed filenames like
    ``index-Cfbh-Yd9.js``, ``rolldown-runtime-CbXtAM7H.js`` and
    ``react-vendor-BoVnYuL4.js`` and writes matching references into
    index.html. When the build is interrupted or the stamp check is fooled
    (e.g. by a source mtime that didn't actually change), index.html can end
    up referencing old hashes whose files no longer exist on disk. The
    browser then 404s on the JS/CSS, the React app never mounts, and the
    LCARS skin renders on top of an empty ``#root`` — the dashboard looks
    skinned but is completely blank and non-interactive.

    This function scans EVERY ``/assets/<name>.<ext>`` URL in the page
    (module script src, stylesheet href, modulepreload links). A reference
    whose file is missing on disk is rewritten to the current file of the
    same asset family (matched by shared dash-segments + longest common
    prefix). References that already resolve are never touched. It is a
    no-op when references are already correct.
    """
    assets_dir = os.path.join(web_dist, "assets")
    if not os.path.isdir(assets_dir):
        return html, 0

    on_disk = set(os.listdir(assets_dir))
    disk_by_ext = {}
    for fn in on_disk:
        ext = fn.rpartition(".")[2]
        if ext in ("js", "css"):
            disk_by_ext.setdefault(ext, []).append(fn)

    def _replacer(m):
        nonlocal fixes
        ref = m.group(1)
        if ref in on_disk:              # resolves fine - leave it alone
            return m.group(0)
        match = _repair_match(ref, disk_by_ext)
        if match:
            fixes += 1
            return "/assets/" + match
        return m.group(0)

    fixes = 0
    html = re.sub(r"/assets/([A-Za-z0-9_.-]+\.(?:js|css))", _replacer, html)
    return html, fixes


def bridge_datauri():
    if not os.path.isfile(BRIDGE_JPG):
        raise SystemExit("[LCARS] bridge image missing: " + BRIDGE_JPG)
    with open(BRIDGE_JPG, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")


def find_target(explicit=None):
    """Locate Hermes' web_dist/index.html on Windows / macOS / Linux."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("HERMES_HOME")
    if env:
        # HERMES_HOME may be the data dir itself, or its parent.
        candidates.append(os.path.join(env, "hermes-agent", "hermes_cli", "web_dist", "index.html"))
        candidates.append(os.path.join(env, "hermes_cli", "web_dist", "index.html"))
        if env.endswith("index.html"):
            candidates.append(env)
    home = os.path.expanduser("~")
    localapp = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    mac = os.path.join(home, "Library", "Application Support", "hermes")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    for base in (localapp, mac, xdg):
        candidates.append(os.path.join(base, "hermes", "hermes-agent", "hermes_cli", "web_dist", "index.html"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


# ----- themes -------------------------------------------------------------
# name + LCARS palette (orange/peach/lilac/blue/red/black) tuned to the
# source wallpaper's hue. bg is a CSS url() string (served at /lcars-bg/*).
# Theme 0 is the embedded Enterprise-D bridge photo.
THEMES = [
    {"name": "Enterprise Bridge", "bg": "__BRIDGE__",
     "orange": "#FF9900", "peach": "#FFCC99", "lilac": "#CC99CC", "blue": "#6699CC", "red": "#CC6666", "black": "#0A0805"},
    {"name": "Nebula Steel", "bg": "url('/lcars-bg/0803b9d13763f56b045def44d3d04719.jpg')",
     "orange": "#E0A060", "peach": "#E8D9C0", "lilac": "#9FA8D0", "blue": "#5B8FC0", "red": "#C0707A", "black": "#0C0F12"},
    {"name": "Deep Space Blue", "bg": "url('/lcars-bg/1-1.jpg')",
     "orange": "#FF9F4D", "peach": "#CFE0FF", "lilac": "#8FA6E0", "blue": "#3A6FD0", "red": "#D0607A", "black": "#060A14"},
    {"name": "Polar White", "bg": "url('/lcars-bg/1.jpg')",
     "orange": "#FF8A3D", "peach": "#FFE7C2", "lilac": "#B9C6E8", "blue": "#6E9BE8", "red": "#E06A6A", "black": "#0A0C10"},
    {"name": "Subspace Indigo", "bg": "url('/lcars-bg/11.jpg')",
     "orange": "#FFA24D", "peach": "#B9C8FF", "lilac": "#9AA6F0", "blue": "#2E5BD0", "red": "#D05A78", "black": "#04081A"},
    {"name": "Ice Cavern", "bg": "url('/lcars-bg/12-1.jpg')",
     "orange": "#FFB15C", "peach": "#DDEAF5", "lilac": "#A9C2E0", "blue": "#4E86C8", "red": "#D06A6A", "black": "#06101A"},
    {"name": "Void Navy", "bg": "url('/lcars-bg/12.jpg')",
     "orange": "#FF9A3D", "peach": "#C9D6F5", "lilac": "#7E93D8", "blue": "#2A52C8", "red": "#D05878", "black": "#02040C"},
    {"name": "Plasma Cyan", "bg": "url('/lcars-bg/1399248.jpg')",
     "orange": "#FF8C2E", "peach": "#BDE6FF", "lilac": "#8FE0E0", "blue": "#1FA8E8", "red": "#E0607A", "black": "#03121F"},
    {"name": "Laser Grid", "bg": "url('/lcars-bg/14585-laser-2560x1600-abstract-wallpaper.jpg')",
     "orange": "#FF9E33", "peach": "#C7D6E8", "lilac": "#7FA0D8", "blue": "#2E66C0", "red": "#D05A6A", "black": "#04070E"},
    {"name": "Signal Spectrum", "bg": "url('/lcars-bg/1470222.jpg')",
     "orange": "#FF8A1E", "peach": "#FFE08A", "lilac": "#B6E06A", "blue": "#4EC8FF", "red": "#FF4A4A", "black": "#060606"},
    {"name": "Glacier Mist", "bg": "url('/lcars-bg/1496262.jpg')",
     "orange": "#FF9A4D", "peach": "#EAF2F6", "lilac": "#B6CCDC", "blue": "#6BA6D6", "red": "#E06A6A", "black": "#0A0E12"},
    {"name": "Warp Crimson", "bg": "url('/lcars-bg/1840908.jpg')",
     "orange": "#FF7A2E", "peach": "#FFC9B0", "lilac": "#E08AA0", "blue": "#C84A5A", "red": "#FF3A3A", "black": "#0C0404"},
    {"name": "Ion Teal", "bg": "url('/lcars-bg/1_6.jpg')",
     "orange": "#FF9A3D", "peach": "#BEEEE6", "lilac": "#8FE0D4", "blue": "#1FB3A8", "red": "#E0607A", "black": "#02141A"},
]


def _bg(uri, spec):
    return "url('" + uri + "')" if spec == "__BRIDGE__" else spec


def build_head():
    return (
        M_HEAD_S + "\n"
        "    <!-- Apply the persisted LCARS theme before first paint (no flash) -->\n"
        "    <script>\n"
        "      try {\n"
        '        var t = parseInt(localStorage.getItem("hermes-lcars-theme"), 10) || 0;\n'
        '        document.documentElement.classList.add("lcars-skin", "theme-" + t);\n'
        "      } catch (e) {}\n"
        "    </script>\n"
        + M_HEAD_E
    )


def build_style(uri):
    theme_rules = "".join(
        "      html.lcars-skin.theme-%d {\n"
        "        --lcars-orange:%s; --lcars-peach:%s; --lcars-lilac:%s;\n"
        "        --lcars-blue:%s; --lcars-red:%s; --lcars-black:%s;\n"
        "        --lcars-bg:%s;\n"
        "        /* Hermes v0.21.0+ per-theme overrides */\n"
        "        --background:%s !important;\n"
        "        --background-base:%s !important;\n"
        "        --midground:%s !important;\n"
        "        --midground-base:%s !important;\n"
        "        --color-card-foreground:%s !important;\n"
        "        --color-text-secondary:%s !important;\n"
        "        --foreground:%s !important;\n"
        "        --foreground-base:%s !important;\n"
        "        --color-midground:%s !important;\n"
        "        --color-ring:%s !important;\n"
        "        --color-muted-foreground:%s !important;\n"
        "        --color-popover-foreground:%s !important;\n"
        "        --color-secondary-foreground:%s !important;\n"
        "      }\n" % (
            i, th["orange"], th["peach"], th["lilac"],
            th["blue"], th["red"], th["black"], _bg(uri, th["bg"]),
            th["black"], th["black"], th["peach"], th["lilac"],
            th["peach"], th["lilac"],
            th["peach"], th["peach"], th["orange"], th["orange"],
            th["lilac"], th["peach"], th["peach"],
        )
        for i, th in enumerate(THEMES)
    )
    return (M_STYLE_S + """
    <style>
      /* ===== LCARS multi-theme skin (all rules scoped under html.lcars-skin) ===== */
      html.lcars-skin {
        --lcars-orange:#FF9900; --lcars-peach:#FFCC99; --lcars-lilac:#CC99CC;
        --lcars-blue:#6699CC; --lcars-red:#CC6666; --lcars-black:#0A0805;
        --lcars-scrim:0.72;            /* dark scrim alpha over the active bg (slider-driven) */
        --lcars-bg:__BRIDGE__;
        /* ---- Legacy Radix/ShadCN variables (preserved for older Hermes builds) ---- */
        --bg-base:#000 !important; --bg-surface:#0a0a0a !important; --bg-elevated:#111 !important; --bg-subtle:#161616 !important;
        --text-primary:var(--lcars-peach) !important; --text-secondary:var(--lcars-lilac) !important; --text-muted:#7a7a6a !important;
        --border-subtle:rgba(102,153,204,0.25) !important; --border-strong:rgba(255,153,0,0.45) !important;
        --accent:var(--lcars-orange) !important; --accent-secondary:var(--lcars-blue) !important;
        /* ---- Hermes v0.21.0+ CSS variables (override the built-in orange theme) ---- */
        --background:var(--lcars-black) !important;
        --background-base:var(--lcars-black) !important;
        --midground:var(--lcars-peach) !important;
        --midground-base:var(--lcars-lilac) !important;
        --color-card:rgba(10,10,10,0.72) !important;
        --color-card-foreground:var(--lcars-peach) !important;
        --color-accent:var(--lcars-orange) !important;
        --color-accent-foreground:var(--lcars-black) !important;
        --color-primary:var(--lcars-orange) !important;
        --color-primary-foreground:var(--lcars-black) !important;
        --color-border:rgba(255,153,0,0.45) !important;
        --color-text-secondary:var(--lcars-lilac) !important;
        /* ---- Hermes v0.21.0: foreground / alpha / remaining tokens ---- */
        --foreground:var(--lcars-peach) !important;
        --foreground-base:var(--lcars-peach) !important;
        --foreground-alpha:0 !important;      /* dark-scheme switch (matches built-in dark themes) */
        --background-alpha:1 !important;
        --midground-alpha:1 !important;
        --color-midground:var(--lcars-orange) !important;
        --color-muted:rgba(10,10,10,0.60) !important;
        --color-muted-foreground:var(--lcars-lilac) !important;
        --color-input:rgba(10,10,10,0.72) !important;
        --color-ring:var(--lcars-orange) !important;
        --color-popover:rgba(10,10,10,0.72) !important;
        --color-popover-foreground:var(--lcars-peach) !important;
        --color-secondary:rgba(10,10,10,0.72) !important;
        --color-secondary-foreground:var(--lcars-peach) !important;
      }
      html.lcars-skin body {
        background-color: var(--lcars-black);
        background-image:
          radial-gradient(circle at 20% 0%, rgba(102,153,204,0.10), transparent 45%),
          radial-gradient(circle at 90% 100%, rgba(204,153,204,0.10), transparent 50%),
          linear-gradient(rgba(0,0,0,var(--lcars-scrim)), rgba(0,0,0,var(--lcars-scrim))),
          var(--lcars-bg);
        background-size: cover; background-position: center; background-repeat: no-repeat; background-attachment: fixed;
      }
      html.lcars-skin body::after {           /* faint scanlines, inert overlay */
        content:""; position:fixed; inset:0; z-index:9998; pointer-events:none;
        background: repeating-linear-gradient(to bottom, rgba(255,255,255,0.025) 0 1px, transparent 1px 3px);
        mix-blend-mode: overlay; opacity: 0.5;
      }
      html.lcars-skin body::before {          /* vignette, inert overlay */
        content:""; position:fixed; inset:0; z-index:9997; pointer-events:none;
        background: radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.55) 100%);
      }
      /* decorative header bar (inert) */
      html.lcars-skin .lcars-frame {
        position: fixed; top:0; left:0; right:0; height:34px; z-index:9999;
        display:flex; pointer-events:none;
        font-family:"Arial Narrow","Antonio Condensed","Oswald",system-ui,sans-serif; font-weight:700;
        letter-spacing:0.12em; text-transform:uppercase; color:#001020;
      }
      html.lcars-skin .lcars-frame .blk { background:var(--lcars-orange); flex:0 0 250px; border-radius:0 0 17px 0; display:flex; align-items:center; padding-left:14px; font-size:11px; letter-spacing:0.05em; }
      html.lcars-skin .lcars-frame .lil { background:var(--lcars-lilac); flex:0 0 46px; }
      html.lcars-skin .lcars-frame .blu { background:var(--lcars-blue); flex:1 1 auto; display:flex; align-items:center; padding-left:16px; color:#001020; font-size:12px; letter-spacing:0.12em; text-transform:uppercase; }
      html.lcars-skin .lcars-frame .pea { background:var(--lcars-peach); flex:0 0 90px; }
      html.lcars-skin .lcars-frame .red { background:var(--lcars-red); flex:0 0 40px; border-radius:0 0 0 17px; }
      html.lcars-skin #root { padding-top:34px; box-sizing:border-box; }
      /* The app root asks for 100dvh, but the frame already took 34px, so its
         bottom edge landed below the fold. On /chat that hid the TUI input line.
         Give it the height that is actually left. */
      html.lcars-skin #root > div { height:calc(100dvh - 34px) !important; }
      html.lcars-skin #root > div,
      html.lcars-skin .bg-background,
      html.lcars-skin [class*="bg-background"] { background-color:transparent !important; background-image:none !important; }
      html.lcars-skin .bg-card,
      html.lcars-skin [class*="bg-card"] { background-color:rgba(10,10,10,0.72) !important; }
      /* ---- Hermes v0.21.0: override new bg-* and text-* classes ---- */
      html.lcars-skin .bg-midground,
      html.lcars-skin [class*="bg-midground"] { background-color:rgba(10,10,10,0.72) !important; }
      html.lcars-skin .bg-muted,
      html.lcars-skin [class*="bg-muted"] { background-color:rgba(10,10,10,0.60) !important; }
      html.lcars-skin .bg-secondary,
      html.lcars-skin [class*="bg-secondary"] { background-color:rgba(10,10,10,0.65) !important; }
      html.lcars-skin .bg-primary,
      html.lcars-skin [class*="bg-primary"] { background-color:var(--lcars-black) !important; }
      html.lcars-skin .bg-popover,
      html.lcars-skin [class*="bg-popover"] { background-color:rgba(10,10,10,0.72) !important; }
      html.lcars-skin .text-foreground,
      html.lcars-skin [class*="text-foreground"] { color:var(--lcars-peach) !important; }
      html.lcars-skin .text-muted-foreground,
      html.lcars-skin [class*="text-muted-foreground"] { color:var(--lcars-lilac) !important; }
      html.lcars-skin .border-border,
      html.lcars-skin [class*="border-border"] { border-color:rgba(255,153,0,0.45) !important; }

      /* ===== per-theme palette overrides ===== */
__THEME_RULES__
      /* ===== controls ===== */
      #lcars-opts-btn, #lcars-theme-btn, #lcars-panel {
        font-family:"Arial Narrow","Antonio Condensed","Oswald",system-ui,sans-serif; font-weight:700;
        letter-spacing:0.12em; text-transform:uppercase; color:#ffe6c0;
        background:rgba(8,8,12,0.32); border:1px solid rgba(255,153,0,0.40);
        -webkit-backdrop-filter:blur(2px); backdrop-filter:blur(2px); -moz-backdrop-filter:blur(2px);
        box-shadow:0 2px 14px rgba(0,0,0,0.35);
        -webkit-user-select:none; -moz-user-select:none; user-select:none;
      }
      /* The toggle lives INSIDE the LCARS frame bar (right-aligned, in-flow),
         so it never floats over the dashboard's own header/controls. */
      #lcars-opts-btn { pointer-events:auto; flex:0 0 auto; margin-left:auto; display:inline-flex; align-items:center; gap:8px; padding:3px 12px; border-radius:16px; cursor:pointer; line-height:1; }
      #lcars-opts-btn:hover { border-color:#ff9900; color:#fff; }
      #lcars-opts-btn .ico { font-size:13px; line-height:1; }
      #lcars-opts-btn .chev { transition:transform 0.2s ease; font-size:10px; }
      #lcars-opts-btn[aria-expanded="true"] .chev { transform:rotate(180deg); }
      html.lcars-skin .lcars-frame .blu { gap:10px; padding-right:10px; }
      #lcars-panel {
        position:fixed; z-index:10000; display:none;
        flex-direction:column; gap:10px; padding:12px 14px; border-radius:15px;
        align-items:stretch; width:224px; font-size:11px;
        background:rgba(8,8,12,0.94); border:1px solid rgba(255,153,0,0.50);
        box-shadow:0 8px 28px rgba(0,0,0,0.60);
      }
      html.lcars-skin #lcars-panel.open { display:flex; }
      #lcars-panel .head { display:flex; align-items:center; justify-content:space-between; gap:8px; padding-bottom:6px; margin-bottom:2px; border-bottom:1px solid rgba(255,153,0,0.35); }
      #lcars-panel .head .title { font-size:10px; letter-spacing:0.18em; color:var(--lcars-peach); }
      #lcars-close-btn { pointer-events:auto; border:none; background:rgba(255,153,0,0.18); color:#ffe6c0; width:20px; height:20px; border-radius:50%; cursor:pointer; font-size:10px; line-height:1; display:inline-flex; align-items:center; justify-content:center; padding:0; }
      #lcars-close-btn:hover { background:rgba(255,153,0,0.45); color:#fff; }
      #lcars-panel .theme-cycle { display:inline-flex; align-items:center; gap:8px; cursor:pointer; padding:7px 12px; border-radius:16px; border:1px solid rgba(255,153,0,0.45); background:rgba(8,8,12,0.30); color:#ffe6c0; }
      #lcars-panel .theme-cycle:hover { border-color:#ff9900; color:#fff; }
      #lcars-panel .theme-cycle .ico { font-size:13px; line-height:1; }
      #lcars-panel .theme-cycle .name { color:#fff; min-width:96px; text-align:left; }
      #lcars-panel .row { display:flex; align-items:center; gap:8px; }
      #lcars-panel label { white-space:nowrap; color:#ffe6c0; }
      #lcars-panel input[type="range"] {
        -webkit-appearance:none; -moz-appearance:none; appearance:none;
        flex:1 1 auto; min-width:0; height:4px; border-radius:2px; cursor:pointer; outline:none;
        background:linear-gradient(90deg, var(--lcars-blue), var(--lcars-orange));
      }
      #lcars-panel input[type="range"]::-webkit-slider-thumb { -webkit-appearance:none; appearance:none; width:14px; height:14px; border-radius:50%; background:#ffcc99; border:2px solid #000; cursor:pointer; }
      #lcars-panel input[type="range"]::-moz-range-thumb { width:14px; height:14px; border-radius:50%; background:#ffcc99; border:2px solid #000; cursor:pointer; }
      #lcars-panel .val { min-width:34px; text-align:right; color:#fff; }
    </style>
""" + M_STYLE_E).replace("__BRIDGE__", _bg(uri, "__BRIDGE__")).replace("__THEME_RULES__", theme_rules)


def build_body():
    names_json = json.dumps([t["name"] for t in THEMES])
    return (M_BODY_S + """
    <div class="lcars-frame" aria-hidden="true">
      <div class="blk">Moddys Dashboard</div>
      <div class="lil"></div>
      <div class="blu">
        <span id="lcars-banner-text">USS Agent &nbsp;·&nbsp; Main Bridge</span>
        <button id="lcars-opts-btn" type="button" aria-expanded="false" aria-controls="lcars-panel">
          <span class="ico">⚙</span><span class="label">Theme Options</span><span class="chev">▼</span>
        </button>
      </div>
      <div class="pea"></div>
      <div class="red"></div>
    </div>
    <div id="lcars-panel" role="dialog" aria-label="LCARS theme options">
      <div class="head">
        <span class="title">Theme Options</span>
        <button id="lcars-close-btn" type="button" aria-label="Close theme options">✕</button>
      </div>
      <button id="lcars-theme-btn" type="button" class="theme-cycle" aria-label="Change theme">
        <span class="ico">▣</span><span class="label">Theme</span><span class="name">Enterprise Bridge</span>
      </button>
      <div class="row">
        <label for="lcars-opacity">Bg opacity</label>
        <input id="lcars-opacity" type="range" min="0" max="0.9" step="0.01" value="0.6" />
        <span class="val" id="lcars-opacity-val">0.60</span>
      </div>
    </div>
    <script>
      (function () {
        var root = document.documentElement;
        var optsBtn = document.getElementById("lcars-opts-btn");
        var btn = document.getElementById("lcars-theme-btn");
        var closeBtn = document.getElementById("lcars-close-btn");
        var panel = document.getElementById("lcars-panel");
        var slider = document.getElementById("lcars-opacity");
        var valEl = document.getElementById("lcars-opacity-val");
        var banner = document.getElementById("lcars-banner-text");
        var THEMES = __THEMES__;

        function stripTheme(cn) {
          return cn.split(/\\s+/).filter(function (c) { return c && c.indexOf("theme-") !== 0; }).join(" ");
        }
        function currentTheme(cn) {
          var m = cn.match(/theme-(\\d+)/);
          return m ? parseInt(m[1], 10) : 0;
        }
        function applyTheme(idx, persist) {
          idx = ((idx % THEMES.length) + THEMES.length) % THEMES.length;
          var cn = stripTheme(root.className) + " theme-" + idx;
          if (cn.indexOf("lcars-skin") === -1) cn = "lcars-skin " + cn;
          root.className = cn;
          btn.querySelector(".name").textContent = THEMES[idx];
          if (banner) banner.textContent = "Skin · " + THEMES[idx];
          if (persist) { try { localStorage.setItem("hermes-lcars-theme", String(idx)); } catch (e) {} }
        }
        function applyOpacity(v, persist) {
          v = Math.max(0, Math.min(0.9, parseFloat(v)));
          root.style.setProperty("--lcars-scrim", (0.9 - v).toFixed(2));
          if (valEl) valEl.textContent = v.toFixed(2);
          if (persist) { try { localStorage.setItem("hermes-lcars-opacity", v.toFixed(2)); } catch (e) {} }
        }
        function placeControls() {
          if (!panel.classList.contains("open")) return;
          var br = optsBtn.getBoundingClientRect();
          var pw = panel.offsetWidth || 224;
          var ph = panel.offsetHeight || 103;
          var vw = document.documentElement.clientWidth;
          var vh = window.innerHeight;
          // Dropdown behaviour: left-aligned with the toggle, directly beneath it.
          var left = Math.round(br.left);
          var top = Math.round(br.bottom + 4);
          // Keep it fully on-screen.
          if (left + pw > vw - 8) left = Math.max(8, vw - pw - 8);
          if (top + ph > vh - 8) top = Math.max(8, Math.round(br.top - ph - 4));
          panel.style.left = left + "px";
          panel.style.top = top + "px";
        }
        function setPanel(open) {
          if (!open) { panel.classList.remove("open"); optsBtn.setAttribute("aria-expanded", "false"); return; }
          panel.classList.add("open");
          placeControls();
          optsBtn.setAttribute("aria-expanded", "true");
        }

        optsBtn.addEventListener("click", function () { setPanel(!panel.classList.contains("open")); });
        btn.addEventListener("click", function () { applyTheme(currentTheme(root.className) + 1, true); });
        if (closeBtn) closeBtn.addEventListener("click", function () { setPanel(false); });

        // Close affordances: Escape key, or clicking anywhere outside the panel.
        document.addEventListener("keydown", function (e) {
          if (e.key === "Escape") setPanel(false);
        });
        document.addEventListener("click", function (e) {
          if (!panel.classList.contains("open")) return;
          if (panel.contains(e.target) || optsBtn.contains(e.target)) return;
          setPanel(false);
        });

        // restore persisted choices
        try {
          var savedTheme = parseInt(localStorage.getItem("hermes-lcars-theme"), 10);
          applyTheme(isNaN(savedTheme) ? 0 : savedTheme, false);
          var savedOp = localStorage.getItem("hermes-lcars-opacity");
          if (savedOp === null) {
            var oldScrim = localStorage.getItem("hermes-lcars-scrim");
            savedOp = oldScrim !== null ? String(Math.max(0, 0.9 - parseFloat(oldScrim))) : "0.6";
          }
          if (slider) slider.value = savedOp;
        } catch (e) {}
        if (slider) {
          applyOpacity(slider.value, false);
          slider.addEventListener("input", function () { applyOpacity(slider.value, true); });
        }
        placeControls();
        window.addEventListener("resize", placeControls);
        window.addEventListener("load", placeControls);
      })();
    </script>
""" + M_BODY_E).replace("__THEMES__", names_json)


def _write_marker(path):
    """Remember a choice across re-applies (best effort, never fatal)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("chat view-stability add-on disabled by the user\n")
        return True
    except OSError as exc:
        print("[LCARS] warning: could not remember the chat-stability opt-out ("
              + str(exc) + ") - the auto-heal will re-enable it")


def _clear_marker(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        print("[LCARS] warning: could not clear " + path + " (" + str(exc) + ")")


def build_chat_stable():
    """Keeps the chat view where the reader left it.

    Alt-tabbing away and back used to move the terminal. The browser runs its
    own scroll-into-view pass on focus, xterm refits, and the reader lands
    somewhere they did not pick. So this block notes the view on the way out
    (page scroll, the offset of every scrollable box around the terminal, and
    how far the terminal sits from its bottom) and puts it back when focus
    returns, over a short burst of timers. Timers and not animation frames:
    the browser's reveal scroll and xterm's refit both land after the frame
    that raised the event.

    It writes nothing unless something actually moved, and it gives up instead
    of fighting the page. A view still moving between probes is left alone. A
    terminal that grew means live output is being followed. A record older than
    five minutes is dropped rather than replayed, which covers sleep, a monitor
    that was off, and a bfcache restore. Any wheel, touch, pointer or key press
    disarms it at once, and three corrections per window is the cap, so a page
    that disagrees with it cannot loop.

    The engine injects this as its own marker block, strip-and-reinject like
    the skin blocks, so re-applies and the auto-heal keep it. Pass
    --no-chat-stability to leave it out.
    """
    return M_STABLE_S + """
    <script>
      (function () {
        if (window.__hermesChatViewStable) return;
        var state = {
          v: 2,
          trace: [],          /* recent corrections, for field diagnosis */
          corrections: 0,     /* writes made in the current guard window */
          staleMs: 300000,    /* test seam: how old a record may be to be used */
          maxCorrections: 3,
        };
        window.__hermesChatViewStable = state;

        var PROBE_MS = [0, 60, 150, 350, 700, 1200];
        var EPS = 0.5;        /* scroll offsets are floats; below this is noise */
        var BOTTOM_SLACK = 2; /* a couple of px off the bottom still counts as the bottom */
        var timers = [];
        var rec = null;       /* numbers only - no DOM node is retained here */
        var armed = false;
        var lastSeen = null;

        function viewport() { return document.querySelector(".xterm-viewport"); }

        /* The terminal viewport is not in this chain on purpose: it has its own
           rules below (slack at the bottom, exact offset when scrolled up).
           What is captured here are the boxes around it, plus the document
           scroller, since the browser's focus-reveal pass can shift those. */
        function chain() {
          var out = [], el = viewport();
          el = el ? el.parentElement : document.scrollingElement;
          while (el && el !== document.documentElement) {
            out.push(el);
            el = el.parentElement;
          }
          var sc = document.scrollingElement;
          if (sc && out.indexOf(sc) === -1) out.push(sc);
          return out;
        }

        /* [scrollTop, scrollHeight] per box, as numbers, so nothing here keeps a
           DOM node alive between transitions. The scrollHeight rides along to
           spot a box that was re-laid-out. */
        function readOffsets() {
          return chain().map(function (el) { return [el.scrollTop, el.scrollHeight]; });
        }

        /* Put each offset back, in order. A box whose content height changed is
           skipped, and a chain that no longer has the same shape is left alone
           entirely: the layout moved for a reason, and the old numbers no longer
           mean what they meant. */
        function writeOffsets(want) {
          var els = chain(), moved = false;
          if (els.length !== want.length) return false;
          for (var i = 0; i < els.length; i++) {
            var el = els[i], w = want[i];
            if (el.scrollHeight === w[1] && Math.abs(el.scrollTop - w[0]) > EPS) {
              el.scrollTop = w[0];
              moved = true;
            }
          }
          return moved;
        }

        function capture() {
          try {
            var vp = viewport();
            var max = vp ? Math.max(0, vp.scrollHeight - vp.clientHeight) : 0;
            rec = {
              at: Date.now(),
              win: window.scrollY || 0,
              offsets: readOffsets(),
              vp: vp ? { top: vp.scrollTop, gap: max - vp.scrollTop, sh: vp.scrollHeight } : null,
            };
          } catch (e) { rec = null; }
        }

        function clearTimers() { while (timers.length) clearTimeout(timers.pop()); }

        function disarm() {
          armed = false;
          lastSeen = null;
          clearTimers();
        }

        function note(kind) {
          state.trace.push({ at: Date.now(), kind: kind, vpTop: rec && rec.vp ? rec.vp.top : null });
          if (state.trace.length > 20) state.trace.shift();
        }

        function restore() {
          if (!rec || !armed) return;
          try {
            if (Date.now() - rec.at > state.staleMs) { disarm(); return; }
            var vp = viewport();
            /* The terminal grew while we were away: output is being followed
               on purpose. Standing down beats pinning a stale position. */
            if (vp && rec.vp && vp.scrollHeight !== rec.vp.sh) { disarm(); return; }
            var now = vp ? vp.scrollTop : null;
            /* Still moving between probes (refit, reflow, streaming output):
               that is the page doing its job, not the artifact we correct. */
            if (now !== null && lastSeen !== null && Math.abs(now - lastSeen) > EPS) {
              lastSeen = now;
              return;
            }
            lastSeen = now;
            var moved = writeOffsets(rec.offsets);
            if (vp && rec.vp) {
              var max = Math.max(0, vp.scrollHeight - vp.clientHeight);
              if (rec.vp.gap <= BOTTOM_SLACK) {
                /* The reader was at the bottom. WebKit stores a scroll offset
                   rounded to a whole pixel, so a position a pixel off the
                   bottom is the bottom: leave it instead of nudging the view
                   for something nobody can see. */
                if (max - vp.scrollTop > BOTTOM_SLACK) {
                  vp.scrollTop = max;
                  moved = true;
                }
              } else {
                var want = Math.min(rec.vp.top, max);
                if (Math.abs(vp.scrollTop - want) > EPS) {
                  vp.scrollTop = want;
                  moved = true;
                }
              }
            }
            if (Math.abs((window.scrollY || 0) - rec.win) > EPS) {
              window.scrollTo(0, rec.win);
              moved = true;
            }
            if (moved) {
              state.corrections += 1;
              note("restore");
              /* Bound the damage if the page and this guard ever disagree. */
              if (state.corrections >= state.maxCorrections) disarm();
            }
          } catch (e) {}
        }

        function arm() {
          if (!rec) return;
          disarm();
          state.corrections = 0;
          armed = true;
          PROBE_MS.forEach(function (ms) { timers.push(setTimeout(restore, ms)); });
        }

        /* Real input always wins: disarm on anything the user does. Checked
           before touching the timer list because wheel/touchmove are hot. */
        ["wheel", "touchstart", "touchmove", "pointerdown", "mousedown", "keydown"].forEach(function (t) {
          window.addEventListener(t, function () {
            if (armed || timers.length) disarm();
          }, { capture: true, passive: true });
        });

        window.addEventListener("blur", function () { capture(); disarm(); }, true);
        window.addEventListener("pagehide", function () { capture(); disarm(); }, true);
        window.addEventListener("pageshow", function (e) {
          /* bfcache: the document came back with its own layout, so what is on
             screen now is the baseline. Replaying an hours-old anchor, or
             scrolling to a position the restored layout no longer has, is worse
             than doing nothing. */
          if (e && e.persisted) { capture(); disarm(); return; }
          if (!rec) capture();
          arm();
        }, true);
        document.addEventListener("visibilitychange", function () {
          if (document.visibilityState === "hidden") { capture(); disarm(); }
          else arm();
        });
        window.addEventListener("focus", function () { if (!rec) capture(); arm(); }, true);

        /* Resize: hold the reader's anchor (bottom stays bottom, a scrolled
           view keeps its distance) instead of snapping to the bottom. A
           resize that arrives while the guard is armed keeps the pre-resize
           record; a standalone one re-baselines on the current view. */
        var rz = null;
        window.addEventListener("resize", function () {
          if (!rec || !armed) capture();
          if (rz) clearTimeout(rz);
          rz = setTimeout(function () { rz = null; arm(); }, 0);
        });
      })();
    </script>
""" + M_STABLE_E


def main():
    ap = argparse.ArgumentParser(description="Apply the LCARS multi-theme reskin to the Hermes dashboard (visual only).")
    ap.add_argument("--target", help="Explicit path to web_dist/index.html (auto-detected if omitted).")
    ap.add_argument("--print-target", action="store_true", help="Print the resolved target path and exit.")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--no-chat-stability", action="store_true",
                       help="Skin the dashboard but leave the chat view-stability add-on out. "
                            "Remembered, so the auto-heal keeps honouring it.")
    group.add_argument("--chat-stability", action="store_true",
                       help="Re-enable the chat view-stability add-on and forget an earlier opt-out.")
    args = ap.parse_args()

    target = find_target(args.target)
    if args.print_target:
        print(target or "(not found)")
        return
    if not target:
        raise SystemExit(
            "LCARS: could not find Hermes' dashboard (web_dist/index.html).\\n"
            "This usually means Hermes Agent is not installed yet.\\n\\n"
            "To install Hermes Agent:\\n"
            "  1. Download from: https://hermes-agent.nousresearch.com/docs/user-guide/installation\\n"
            "  2. Install it and start the Hermes dashboard\\n"
            "  3. Re-run this installer\\n\\n"
            "If Hermes IS installed but in a custom location, pass --target PATH\\n"
            "to the full path of the dashboard's index.html file."
        )

    uri = bridge_datauri()
    n = copy_assets(target)
    web_dist = os.path.dirname(os.path.abspath(target))
    html = open(target, encoding="utf-8").read()

    # A marker pair that appears an unequal number of times means the dashboard
    # was edited by hand (or a previous write was interrupted). Stripping leaves
    # the unmatched marker where it is, so say so instead of silently layering a
    # second copy of the block on top of it.
    for _s, _e in ((M_HEAD_S, M_HEAD_E), (M_STYLE_S, M_STYLE_E),
                   (M_BODY_S, M_BODY_E), (M_STABLE_S, M_STABLE_E)):
        if html.count(_s) != html.count(_e):
            print("[LCARS] warning: unmatched marker " + _s + " ("
                  + str(html.count(_s)) + " start / " + str(html.count(_e))
                  + " end) - left untouched, check the file by hand")

    html, asset_fixes = fix_stale_asset_references(html, web_dist)

    for s, e, sep_before in ((M_HEAD_S, M_HEAD_E, True),
                             (M_STYLE_S, M_STYLE_E, False),
                             (M_BODY_S, M_BODY_E, False),
                             (M_STABLE_S, M_STABLE_E, False)):
        html = strip_block(html, s, e, separator_before=sep_before)

    for anchor in ("<title>", "</head>", '<div id="root">', "</body>"):
        if anchor not in html:
            raise SystemExit("LCARS cannot find anchor " + repr(anchor) + " in target")

    html = re.sub(r"<title>.*?</title>", "<title>Moddys Dashboard</title>", html, count=1, flags=re.S)
    html = html.replace("</title>", "</title>\n" + build_head(), 1)
    html = html.replace("</head>", build_style(uri) + "\n  </head>", 1)
    body = build_body()
    # Honour (and record) the opt-out: an explicit --no-chat-stability writes the
    # marker, --chat-stability clears it, and a plain run - which is what the
    # auto-heal does - follows whatever the user last chose.
    if args.no_chat_stability:
        _write_marker(STABLE_OFF)
    elif args.chat_stability:
        _clear_marker(STABLE_OFF)
    stability_on = not (args.no_chat_stability or os.path.isfile(STABLE_OFF))
    if stability_on:
        body += "\n" + build_chat_stable()
    html = html.replace("</body>", body + "\n  </body>", 1)

    tmp = target + ".tmp"
    open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, target)

    print("[LCARS] skin applied to " + target)
    print("[LCARS] themes: " + str(len(THEMES)))
    print("[LCARS] wallpaper files copied: " + str(n))
    print("[LCARS] asset references repaired: " + str(asset_fixes))
    print("[LCARS] bridge photo embedded: " + ("yes" if "data:image/jpeg" in html else "NO"))
    print("[LCARS] theme cycler present: " + ("yes" if 'id="lcars-theme-btn"' in html else "NO"))
    print("[LCARS] options panel present: " + ("yes" if 'id="lcars-panel"' in html else "NO"))
    print("[LCARS] chat view stability present: "
          + ("yes" if M_STABLE_S in html else "no" + ("" if stability_on else " (opted out; "
             + STABLE_OFF + " - run with --chat-stability to restore)")))


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
            "If this persists, run:  python3 apply_lcars_skin.py --target PATH\n"
            "(PATH = full path to your web_dist/index.html), or open an issue at\n"
            "https://github.com/ModdySwag/Hermes-Dashboard-Multi-Themes-Edition/issues\n".format(exc)
        )
        sys.exit(1)
