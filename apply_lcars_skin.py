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


def copy_assets(target):
    """Copy the bundled wallpaper files into Hermes' web_dist/lcars-bg/ so the
    non-bridge themes (1-12) resolve at /lcars-bg/*.jpg. Safe to re-run."""
    web_dist = os.path.dirname(os.path.abspath(target))
    dest = os.path.join(web_dist, "lcars-bg")
    src = os.path.join(HERE, "lcars-bg")
    if os.path.isdir(src):
        os.makedirs(dest, exist_ok=True)
        for fn in os.listdir(src):
            if fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                shutil.copyfile(os.path.join(src, fn), os.path.join(dest, fn))
        return len(os.listdir(dest))
    return 0


def strip_block(s, start, end):
    """Remove every span between start/end markers (idempotent re-apply)."""
    while True:
        i = s.find(start)
        if i == -1:
            return s
        j = s.find(end, i)
        if j == -1:
            return s
        s = s[:i] + s[j + len(end):]


def fix_stale_asset_references(html, web_dist):
    """Repair stale asset-hash references in index.html.

    Hermes' Vite/Rolldown build emits hashed filenames like
    ``index-BvV5s0y.js`` and writes matching references into index.html.
    When the build is interrupted or the stamp check is fooled (e.g. by a
    source mtime that didn't actually change), the index.html can end up
    referencing old hashes whose files no longer exist on disk.  The
    browser then 404s on the JS/CSS, the React app never mounts, and the
    LCARS skin renders on top of an empty ``#root`` — the dashboard looks
    skinned but is completely blank and non-interactive.

    This function scans for ``/assets/index-*.{js,css}`` URLs, checks that
    each referenced file exists on disk, and if it doesn't, replaces the
    stale hash with the actual current ``index-*.{js,css}`` file that *does*
    exist.  It is a no-op when references are already correct.
    """
    assets_dir = os.path.join(web_dist, "assets")
    if not os.path.isdir(assets_dir):
        return html, 0

    # Build a map of extension -> actual hash for index-*.{js,css}
    actual = {}
    for fn in os.listdir(assets_dir):
        m = re.match(r"^index-([A-Za-z0-9]+)\.(js|css)$", fn)
        if m:
            actual[m.group(2)] = m.group(1)

    fixes = 0
    def _replacer(m):
        nonlocal fixes
        ext = m.group(2)
        ref_hash = m.group(1)
        if ext in actual and ref_hash != actual[ext]:
            fixes += 1
            return "/assets/index-" + actual[ext] + "." + ext
        return m.group(0)

    html = re.sub(r"/assets/index-([A-Za-z0-9]+)\.(js|css)", _replacer, html)
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
        "      }\n" % (
            i, th["orange"], th["peach"], th["lilac"],
            th["blue"], th["red"], th["black"], _bg(uri, th["bg"]),
            th["black"], th["black"], th["peach"], th["lilac"],
            th["peach"], th["lilac"],
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
      #lcars-opts-btn { position:fixed; z-index:10000; display:inline-flex; align-items:center; gap:8px; padding:4px 12px; border-radius:16px; cursor:pointer; line-height:1; }
      #lcars-opts-btn:hover { border-color:#ff9900; color:#fff; }
      #lcars-opts-btn .ico { font-size:13px; line-height:1; }
      #lcars-opts-btn .chev { transition:transform 0.2s ease; font-size:10px; }
      #lcars-opts-btn[aria-expanded="true"] .chev { transform:rotate(180deg); }
      #lcars-panel {
        position:fixed; top:40px; z-index:10000; display:none;
        flex-direction:column; gap:10px; padding:11px 13px; border-radius:15px;
        align-items:stretch; width:200px; font-size:11px;
      }
      html.lcars-skin #lcars-panel.open { display:flex; }
      #lcars-panel .theme-cycle { display:inline-flex; align-items:center; gap:8px; cursor:pointer; padding:7px 12px; border-radius:16px; border:1px solid rgba(255,153,0,0.45); background:rgba(8,8,12,0.30); color:#ffe6c0; }
      #lcars-panel .theme-cycle:hover { border-color:#ff9900; color:#fff; }
      #lcars-panel .theme-cycle .ico { font-size:13px; line-height:1; }
      #lcars-panel .theme-cycle .name { color:#fff; min-width:96px; text-align:left; }
      #lcars-panel .row { display:flex; align-items:center; gap:8px; }
      #lcars-panel label { white-space:nowrap; color:#ffe6c0; }
      #lcars-panel input[type="range"] {
        -webkit-appearance:none; -moz-appearance:none; appearance:none;
        flex:1 1 auto; height:4px; border-radius:2px; cursor:pointer; outline:none;
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
      <div class="blu"><span id="lcars-banner-text">USS Agent &nbsp;·&nbsp; Main Bridge</span></div>
      <div class="pea"></div>
      <div class="red"></div>
    </div>
    <button id="lcars-opts-btn" type="button" aria-expanded="false" aria-controls="lcars-panel">
      <span class="ico">⚙</span><span class="label">Theme Options</span><span class="chev">▼</span>
    </button>
    <div id="lcars-panel">
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
          var frame = document.querySelector(".lcars-frame");
          var txt = document.getElementById("lcars-banner-text");
          if (!frame || !txt) return;
          var fr = frame.getBoundingClientRect();
          var br = optsBtn.getBoundingClientRect();
          var top = fr.top + Math.max(0, (fr.height - br.height) / 2);
          optsBtn.style.top = Math.round(top) + "px";
          optsBtn.style.left = Math.round(txt.getBoundingClientRect().right + 14) + "px";
        }
        function setPanel(open) {
          if (!open) { panel.classList.remove("open"); optsBtn.setAttribute("aria-expanded", "false"); return; }
          placeControls();
          panel.style.left = optsBtn.getBoundingClientRect().left + "px";
          panel.classList.add("open");
          optsBtn.setAttribute("aria-expanded", "true");
        }

        optsBtn.addEventListener("click", function () { setPanel(!panel.classList.contains("open")); });
        btn.addEventListener("click", function () { applyTheme(currentTheme(root.className) + 1, true); });

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


def main():
    ap = argparse.ArgumentParser(description="Apply the LCARS multi-theme reskin to the Hermes dashboard (visual only).")
    ap.add_argument("--target", help="Explicit path to web_dist/index.html (auto-detected if omitted).")
    ap.add_argument("--print-target", action="store_true", help="Print the resolved target path and exit.")
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

    html, asset_fixes = fix_stale_asset_references(html, web_dist)

    for s, e in ((M_HEAD_S, M_HEAD_E), (M_STYLE_S, M_STYLE_E), (M_BODY_S, M_BODY_E)):
        html = strip_block(html, s, e)

    for anchor in ("<title>", "</head>", '<div id="root">', "</body>"):
        if anchor not in html:
            raise SystemExit("LCARS cannot find anchor " + repr(anchor) + " in target")

    html = re.sub(r"<title>.*?</title>", "<title>Moddys Dashboard</title>", html, count=1, flags=re.S)
    html = html.replace("</title>", "</title>\n" + build_head(), 1)
    html = html.replace("</head>", build_style(uri) + "\n  </head>", 1)
    html = html.replace("</body>", build_body() + "\n  </body>", 1)

    tmp = target + ".tmp"
    open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, target)

    print("[LCARS] skin applied to " + target)
    print("[LCARS] themes: " + str(len(THEMES)))
    print("[LCARS] wallpaper files copied: " + str(n))
    print("[LCARS] asset references repaired: " + str(asset_fixes))
    print("[LCARS] bridge photo embedded: " + ("yes" if "data:image/jpeg" in html else "NO"))
    print("[LCARS] change-theme button present: " + ("yes" if 'id="lcars-theme-btn"' in html else "NO"))


if __name__ == "__main__":
    main()
