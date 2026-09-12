"""Run the chat view-stability harness in headless browsers and fail on the first
broken scenario.

The harness page is served over loopback HTTP, and the page POSTs its results
back as it goes. That works the same in Chrome, Edge, Brave and Firefox, needs
no Node, puppeteer or pip installs, and avoids `--dump-dom` (which Firefox does
not have). Browsers that are not installed are skipped with a loud line, never
counted as a pass.

Each browser runs twice: at device scale 1 and at 4. A box can only store scroll
offsets that sit on the device pixel grid, and at scale 1 the grid is a whole
pixel, so the sub-pixel tolerance is only really exercised in the second pass.

Usage:
    python run_chat_stable_harness.py [live index.html] [--browser chrome|firefox|all]
Exit codes: 0 all scenarios pass, 1 a scenario failed, 3 no browser at all.
"""
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_chat_stable_harness as harness  # noqa: E402

EXPECTED_SCENARIOS = 12
RESULT_TIMEOUT_S = 150

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge", "/snap/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
CHROME_NAMES = ["google-chrome", "chromium", "chromium-browser", "chrome", "msedge", "brave"]

FIREFOX_CANDIDATES = [
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Mozilla Firefox", "firefox.exe"),
    "/usr/bin/firefox", "/usr/bin/firefox-esr", "/snap/bin/firefox",
    "/Applications/Firefox.app/Contents/MacOS/firefox",
]
FIREFOX_NAMES = ["firefox", "firefox-esr"]

FIREFOX_PREFS = """\
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.startup.page", 0);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("browser.tabs.warnOnClose", false);
"""


def first_existing(candidates, names, env_var):
    override = os.environ.get(env_var)
    if override and os.path.isfile(override):
        return override
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_chrome():
    return first_existing(CHROME_CANDIDATES, CHROME_NAMES, "CHROME_PATH")


def find_firefox():
    return first_existing(FIREFOX_CANDIDATES, FIREFOX_NAMES, "FIREFOX_PATH")


def webkit_python():
    """An interpreter that can `import playwright`, or None.

    WebKit needs Playwright, which this bundle does not depend on, so the check
    is optional: this interpreter first, then whatever LCARS_PLAYWRIGHT_PYTHON
    points at. No interpreter with Playwright means WebKit is skipped loudly.
    """
    candidates = [sys.executable]
    env = os.environ.get("LCARS_PLAYWRIGHT_PYTHON")
    if env:
        candidates.append(env)
    for exe in candidates:
        if not exe or not os.path.exists(exe):
            continue
        probe = subprocess.run([exe, "-c", "import playwright"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return exe
    return None


class _Results:
    """Latest results body posted by the page, plus the moment it arrived."""

    def __init__(self):
        self.raw = None
        self.at = 0.0
        self.lock = threading.Lock()

    def set(self, body):
        with self.lock:
            self.raw = body
            self.at = time.time()

    def snapshot(self):
        with self.lock:
            return self.raw, self.at


def serve(results, page_path):
    """Loopback server for the harness page and its results POST."""
    body = open(page_path, "rb").read()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, payload=b"", ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def do_GET(self):
            if self.path.startswith("/harness"):
                self._send(200, body, "text/html; charset=utf-8")
            elif self.path.startswith("/done"):
                # Lets a launcher that cannot read the page's state (WebKit runs
                # through Playwright) know when to close the browser.
                raw, _ = results.snapshot()
                done = False
                if raw:
                    try:
                        done = len(json.loads(raw)) >= EXPECTED_SCENARIOS
                    except ValueError:
                        done = False
                self._send(200 if done else 204)
            else:
                self._send(404)

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(size) if size else b""
            if self.path.startswith("/results"):
                results.set(payload.decode("utf-8", "replace"))
            self._send(204)

        def log_message(self, *args):
            pass

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def chrome_cmd(exe, profile, scale, url):
    return [exe, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            "--user-data-dir=" + profile,
            "--force-device-scale-factor=%s" % scale,
            url]


def webkit_cmd(python, scale, url, hold=40):
    launcher = os.path.join(HERE, "webkit_launcher.py")
    return [python, launcher, url, str(hold), "--scale", str(scale)]


def firefox_cmd(exe, profile, scale, url):
    with open(os.path.join(profile, "user.js"), "w", encoding="utf-8") as fh:
        fh.write(FIREFOX_PREFS)
        if float(scale) != 1:
            fh.write('user_pref("layout.css.devPixelsPerPx", "%s");\n' % scale)
    return [exe, "-headless", "-no-remote", "-profile", profile, url]


def run_pass(browser, exe, page_path, scale):
    """One browser, one device scale. Returns (results, note)."""
    results = _Results()
    httpd, port = serve(results, page_path)
    profile = tempfile.mkdtemp(prefix="chat-stable-%s-" % browser)
    url = "http://127.0.0.1:%d/harness.html" % port
    if browser == "chrome":
        cmd = chrome_cmd(exe, profile, scale, url)
    elif browser == "webkit":
        cmd = webkit_cmd(exe, scale, url)
    else:
        cmd = firefox_cmd(exe, profile, scale, url)
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + RESULT_TIMEOUT_S
        last = None
        stable_since = None
        while time.time() < deadline:
            raw, at = results.snapshot()
            if raw and raw != last:
                last, stable_since = raw, time.time()
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                if parsed and len(parsed) >= EXPECTED_SCENARIOS:
                    return parsed, ""
            elif raw and stable_since and time.time() - stable_since > 5:
                # Page stopped reporting: hand back what arrived, the caller
                # reports the shortfall.
                try:
                    return json.loads(raw), "page stopped after %s scenarios" % len(json.loads(raw))
                except ValueError:
                    return [], "unreadable results body"
            time.sleep(0.25)
        raw, _ = results.snapshot()
        try:
            return json.loads(raw), "timed out" if raw else "no results within %ss" % RESULT_TIMEOUT_S
        except (ValueError, TypeError):
            return [], "timed out with no readable results"
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(profile, ignore_errors=True)


def apply_and_build(live):
    """Skin a throwaway copy of `live` and take the block the engine just wrote,
    so the harness tests the engine's current output rather than whatever is on
    disk (and so CI can seed it with the fixture)."""
    live = live or harness.DEFAULT_LIVE
    root = tempfile.mkdtemp(prefix="chat-stable-apply-")
    dist = os.path.join(root, "web_dist")
    shutil.copytree(os.path.dirname(os.path.abspath(live)), dist)
    target = os.path.join(dist, "index.html")
    engine = os.path.join(os.path.dirname(HERE), "apply_lcars_skin.py")
    proc = subprocess.run([sys.executable, engine, "--target", target],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("apply_lcars_skin.py failed: " + (proc.stderr or proc.stdout).strip()[:400])
    if "chat view stability present: yes" not in proc.stdout:
        raise SystemExit("the engine did not inject the stability block: "
                         + proc.stdout.strip()[-300:])
    return harness.build(target)


def main():
    args = sys.argv[1:]
    browsers_wanted = "all"
    if "--browser" in args:
        i = args.index("--browser")
        browsers_wanted = args[i + 1] if i + 1 < len(args) else "all"
        del args[i:i + 2]
    live = args[0] if args else None

    page = apply_and_build(live)
    print("harness: " + page)

    available = []
    if browsers_wanted in ("all", "chrome"):
        exe = find_chrome()
        if exe:
            available.append(("chrome", exe))
        else:
            print("skip: no Chromium-family browser (set CHROME_PATH)")
    if browsers_wanted in ("all", "firefox"):
        exe = find_firefox()
        if exe:
            available.append(("firefox", exe))
        else:
            print("skip: no Firefox (set FIREFOX_PATH)")
    if browsers_wanted in ("all", "webkit"):
        exe = webkit_python()
        if exe:
            available.append(("webkit", exe))
        else:
            print("skip: no interpreter with Playwright for WebKit "
                  "(pip install playwright; playwright install webkit; "
                  "set LCARS_PLAYWRIGHT_PYTHON to that interpreter)")
    if not available:
        print("SKIP: nothing to run the harness in")
        return 3
    # A browser that quietly failed to install would otherwise shrink the run and
    # still report green. CI sets this so a missing engine is a failure.
    required = [b.strip() for b in os.environ.get("LCARS_REQUIRE_BROWSERS", "").split(",") if b.strip()]
    missing = [b for b in required if b not in {name for name, _ in available}]
    if missing:
        print("\nFAIL: required browser(s) not available: %s" % ", ".join(missing))
        return 1

    failures = 0
    ran = 0
    for browser, exe in available:
        print("\n=== %s ===\n%s" % (browser, exe))
        for scale in (1, 4):
            label = "dpr%s" % scale
            results, note = run_pass(browser, exe, page, scale)
            print("\n--- %s %s ---" % (browser, label))
            if not results:
                print("FAIL: no results (%s)" % (note or "unknown"))
                failures += 1
                continue
            for rec in results:
                ok = rec.get("ok") is True
                failures += 0 if ok else 1
                ran += 1
                extra = ", ".join("%s=%s" % (k, v) for k, v in rec.items()
                                  if k not in ("name", "ok"))
                print("%-4s %-34s %s" % ("PASS" if ok else "FAIL", rec.get("name"), extra))
            if len(results) < EXPECTED_SCENARIOS:
                print("FAIL: only %d of %d scenarios reported (%s)"
                      % (len(results), EXPECTED_SCENARIOS, note or "cut short"))
                failures += 1
    print("\nscenarios run: %d, failures: %d" % (ran, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
