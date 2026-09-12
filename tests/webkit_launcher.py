"""Open one URL in Playwright's WebKit and let the page do the talking.

WebKit is the third engine, and the only Safari-family one available on Windows.
It needs Playwright, which the bundle does not depend on, so this launcher is
standalone: run it with any interpreter that has `playwright` installed and a
WebKit build downloaded (`python -m playwright install webkit`).

The page is expected to report its own results over HTTP (that is what the
harness does), so this script only opens it, holds it open for a while, and gets
out of the way.

Usage:
    python webkit_launcher.py <url> [seconds] [--scale N]
"""
import sys
import time
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write("Playwright is not installed for this interpreter.\n")
    sys.exit(3)


def main():
    args = [a for a in sys.argv[1:]]
    scale = 1
    if "--scale" in args:
        i = args.index("--scale")
        scale = float(args[i + 1]) if i + 1 < len(args) else 1
        del args[i:i + 2]
    url = args[0] if args else None
    hold = float(args[1]) if len(args) > 1 else 30.0
    if not url:
        sys.stderr.write(__doc__)
        return 2

    with sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        context = browser.new_context(device_scale_factor=scale)
        page = context.new_page()
        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception as exc:
            sys.stderr.write("navigation failed: %s\n" % exc)
        # The page reports its results over HTTP; ask the same server when the
        # run is finished instead of guessing with a fixed sleep.
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
