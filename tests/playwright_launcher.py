"""Open one URL in a Playwright browser and let the page do the talking.

Playwright bundles its own Chrome, Firefox and WebKit builds, which is what makes
a three-engine check possible on one machine (a system Firefox CLI cannot report
page state on Windows, and Chrome for Testing installed by CI actions fails to
bootstrap its helper processes on macOS). The bundle does not depend on
Playwright: this launcher is standalone, used only when an interpreter with it
exists.

The page is expected to report its own results over HTTP (that is what the
harness does), so this opens it, waits for the runner's /done endpoint, and gets
out of the way.

Usage:
    python playwright_launcher.py <engine> <url> [seconds] [--scale N]
"""
import sys
import time
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write("Playwright is not installed for this interpreter.\n")
    sys.exit(3)

ENGINES = ("chromium", "firefox", "webkit")


def main():
    args = [a for a in sys.argv[1:]]
    scale = 1
    if "--scale" in args:
        i = args.index("--scale")
        scale = float(args[i + 1]) if i + 1 < len(args) else 1
        del args[i:i + 2]
    if len(args) < 2:
        sys.stderr.write(__doc__)
        return 2
    engine, url = args[0], args[1]
    hold = float(args[2]) if len(args) > 2 else 40.0
    if engine not in ENGINES:
        sys.stderr.write("unknown engine %r (expected one of %s)\n" % (engine, ", ".join(ENGINES)))
        return 2

    with sync_playwright() as p:
        browser = getattr(p, engine).launch(headless=True)
        context = browser.new_context(device_scale_factor=scale)
        page = context.new_page()
        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception as exc:
            sys.stderr.write("navigation failed: %s\n" % exc)
        base = url.rsplit("/", 1)[0]
        deadline = time.time() + hold
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                with urllib.request.urlopen(base + "/done", timeout=5) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
