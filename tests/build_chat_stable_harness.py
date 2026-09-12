"""Build (and print the path of) a headless harness page that drives the shipped
chat view-stability block against a synthetic terminal, so its behaviour is
checked rather than assumed.

The harness pulls the `<script>` out of the HERMES_CHAT_VIEW_STABLE marker block
in the dashboard's index.html, which is the same code the browser gets, and runs
it through twelve scenarios against real DOM scroll containers. Results land on
`window.__results`, in `#results` as JSON, and in `document.title` so a headless
browser can dump and read them (see run_chat_stable_harness.py).

Usage: python build_chat_stable_harness.py [live index.html] [out.html]
"""
import os
import re
import sys

DEFAULT_LIVE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "hermes", "hermes-agent", "hermes_cli", "web_dist", "index.html")

DRIVER = r"""
<script>
window.__results = [];
function vp() { return document.getElementById("vp"); }
function wrap() { return document.getElementById("wrap"); }
function max() { var v = vp(); return Math.max(0, v.scrollHeight - v.clientHeight); }
function bottom() { vp().scrollTop = max(); }
function fire(type, target) { (target || window).dispatchEvent(new Event(type)); }
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function drift() { wrap().scrollTop = 40; vp().scrollTop = Math.min(90, max()); }
function grow(px) {
  var c = document.getElementById("content");
  c.style.height = (parseInt(c.style.height, 10) + px) + "px";
}
function state() { return window.__hermesChatViewStable || {}; }
function traceLen() { return (state().trace || []).length; }
function report() {
  var body = JSON.stringify(window.__results);
  document.getElementById("results").textContent = body;
  // Same-origin POST so any browser can hand the results over: Firefox has no
  // --dump-dom, and this needs no flags at all.
  try { fetch("/results", { method: "POST", body: body }); } catch (e) {}
}
async function run(name, fn) {
  var rec = { name: name };
  try { await fn(rec); } catch (e) { rec.error = String(e); }
  window.__results.push(rec);
  report();
  document.title = (rec.ok ? "done:" : "FAILED:") + window.__results.length;
}
window.addEventListener("load", function () {
  document.getElementById("results").textContent = "";
  (async function () {
    await run("installed", async function (rec) {
      rec.version = state().v;
      rec.ok = !!window.__hermesChatViewStable && state().v === 2;
    });

    // 1. Reader at the bottom: stays at the bottom, no ancestor drift left behind.
    await run("bottom stays bottom", async function (rec) {
      bottom(); fire("blur"); drift(); fire("focus");
      await wait(1500);
      rec.vpTop = Math.round(vp().scrollTop); rec.vpMax = Math.round(max());
      rec.wrap = Math.round(wrap().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - max()) <= 1 && wrap().scrollTop === 0;
    });

    // 2. Reader scrolled up: keeps their exact place (never yanked to the bottom).
    await run("scrolled up keeps place", async function (rec) {
      vp().scrollTop = 120; fire("blur"); drift(); fire("focus");
      await wait(1500);
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - 120) <= 1 && wrap().scrollTop === 0;
    });

    // 3. User input wins: a real scroll disarms the guard.
    await run("user scroll wins", async function (rec) {
      bottom(); fire("blur"); fire("focus");
      window.dispatchEvent(new Event("wheel"));
      await wait(20); drift();
      await wait(1500);
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - Math.min(90, max())) <= 1;
    });

    // 4. Live output wins: a growing terminal is left alone.
    await run("live output wins", async function (rec) {
      vp().scrollTop = 120; fire("blur"); fire("focus");
      await wait(20); grow(200); drift();
      await wait(1500);
      rec.vpTop = Math.round(vp().scrollTop); rec.vpMax = Math.round(max());
      rec.ok = Math.abs(vp().scrollTop - Math.min(90, max())) <= 1;
    });

    // 5. Nothing moved -> nothing is written at all (zero motion).
    await run("no write when nothing moved", async function (rec) {
      bottom();
      var before = traceLen(), vpBefore = vp().scrollTop;
      fire("blur"); fire("focus");
      await wait(1500);
      rec.newTraceEntries = traceLen() - before;
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = rec.newTraceEntries === 0 && vp().scrollTop === vpBefore;
    });

    // 6a. A standalone resize keeps the reader's anchor (never snaps to bottom).
    await run("resize keeps anchor", async function (rec) {
      window.dispatchEvent(new Event("wheel"));   // user input disarms the guard
      vp().scrollTop = 150;
      fire("resize");
      await wait(300);
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - 150) <= 1;
    });

    // 6b. A resize right after a focus return holds the PRE-transition anchor.
    await run("resize during guard holds anchor", async function (rec) {
      bottom(); fire("blur"); fire("focus");
      await wait(20);
      vp().scrollTop = 150;                       // the refit moved it
      fire("resize");
      await wait(1500);
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - max()) <= 1;
    });

    // 7. Sub-pixel drift is noise, not motion: no write, no trace entry.
    //    A box only stores offsets its device pixel grid allows. Chromium keeps
    //    fractions, Firefox and WebKit round to whole pixels, so the drift is
    //    measured as stored and reported as `grid`. Either way, a drift that
    //    small must not produce a write.
    await run("sub-pixel drift is not a write", async function (rec) {
      bottom(); fire("blur");
      vp().scrollTop = max() - 0.4;
      var stored = vp().scrollTop, grid = max() - stored;
      var before = traceLen();
      fire("focus");
      await wait(1500);
      rec.grid = Math.round(grid * 1000) / 1000;
      rec.newTraceEntries = traceLen() - before;
      rec.vpTop = vp().scrollTop; rec.vpMax = max();
      rec.ok = rec.newTraceEntries === 0 && Math.abs(vp().scrollTop - stored) <= 0.01;
    });

    // 8. A stale record (sleep, monitor off, long bfcache) is dropped, not replayed.
    await run("stale record dropped", async function (rec) {
      var saved = state().staleMs;
      state().staleMs = 120;
      bottom(); fire("blur");
      vp().scrollTop = 90;
      await wait(300);
      fire("focus");
      await wait(1500);
      state().staleMs = saved;
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - 90) <= 1;
    });

    // 9. bfcache restore re-baselines on what is on screen.
    await run("bfcache restore re-baselines", async function (rec) {
      bottom(); fire("blur");
      vp().scrollTop = 90;
      var ev;
      try { ev = new PageTransitionEvent("pageshow", { persisted: true }); }
      catch (e) { ev = new Event("pageshow"); Object.defineProperty(ev, "persisted", { value: true }); }
      window.dispatchEvent(ev);
      await wait(1400);
      rec.vpTop = Math.round(vp().scrollTop);
      rec.ok = Math.abs(vp().scrollTop - 90) <= 1;
    });

    // 10. A burst of switches settles on the last record, with bounded writes.
    await run("switch burst settles", async function (rec) {
      var before = traceLen();
      for (var i = 0; i < 5; i++) {
        bottom(); fire("blur"); drift(); fire("focus");
        await wait(120);
      }
      await wait(1400);
      rec.newTraceEntries = traceLen() - before;
      rec.vpTop = Math.round(vp().scrollTop); rec.vpMax = Math.round(max());
      rec.ok = Math.abs(vp().scrollTop - max()) <= 1 && rec.newTraceEntries <= 5;
    });

    document.getElementById("results").textContent = JSON.stringify(window.__results);
    document.title = "done:" + window.__results.length;
    report();
  })();
});
</script>
"""

PAGE = """<!doctype html>
<html style="height:100%%;overflow:hidden">
<head><meta charset="utf-8"><title>pending</title></head>
<body style="margin:0;height:100%%;overflow:hidden">
  <div id="wrap" style="height:200px;overflow:hidden">
    <div id="host" style="height:220px">
      <div class="xterm-viewport" id="vp" style="height:200px;overflow-y:scroll">
        <div id="content" style="height:600px"></div>
      </div>
    </div>
  </div>
  <pre id="results"></pre>
%s
%s
</body>
</html>
"""


def extract_block(live):
    html = open(live, encoding="utf-8").read()
    m = re.search(
        r"<!-- HERMES_CHAT_VIEW_STABLE_START -->(.*?)<!-- HERMES_CHAT_VIEW_STABLE_END -->",
        html, flags=re.S)
    if not m:
        raise SystemExit("chat view-stability block not found in " + live)
    return m.group(1)


def build(live=None, out=None):
    live = live or DEFAULT_LIVE
    out = out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "chat_view_stable_test.html")
    open(out, "w", encoding="utf-8").write(PAGE % (extract_block(live), DRIVER))
    return out


def main():
    live = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LIVE
    out = sys.argv[2] if len(sys.argv) > 2 else None
    print(build(live, out))


if __name__ == "__main__":
    main()
