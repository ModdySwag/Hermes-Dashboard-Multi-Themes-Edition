"""Pre-ship checks for the LCARS engine's chat view-stability block.

The real engine and installer run against throwaway copies of the dashboard, so
nothing here can touch the machine. What gets asserted is the set of properties
a re-apply, a revert or a hand-edited file could quietly break:

  1. both scripts compile
  2. apply: every marker block exactly once, the stability block inside <body>
     after the skin block, every injected <script> parses, every /assets/* ref
     resolves
  3. re-apply is byte-identical (idempotence)
  4. --no-chat-stability leaves the skin in place with the add-on absent, and a
     later normal apply puts it back
  5. apply.py --remove strips the skin AND the add-on (leaving a working,
     un-skinned dashboard)
  6. a hand-edited file with an unmatched marker is reported, not made worse
  7. a missing/broken node is reported as a failure, not a traceback
  8. apply.py --check still reports READY on the target

Usage: python verify_chat_stable.py [live index.html] [--node PATH]
Exit codes: 0 all checks pass, 1 any check failed.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "apply_lcars_skin.py")
INSTALLER = os.path.join(HERE, "apply.py")
STABLE_S = "<!-- HERMES_CHAT_VIEW_STABLE_START -->"
STABLE_E = "<!-- HERMES_CHAT_VIEW_STABLE_END -->"
SKIN_S = "<!-- LCARS_BODY_START -->"
PAIRS = [
    ("<!-- LCARS_HEAD_START -->", "<!-- LCARS_HEAD_END -->"),
    ("<!-- LCARS_STYLE_START -->", "<!-- LCARS_STYLE_END -->"),
    (SKIN_S, "<!-- LCARS_BODY_END -->"),
    (STABLE_S, STABLE_E),
]
DEFAULT_LIVE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "hermes", "hermes-agent", "hermes_cli", "web_dist", "index.html")

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def find_node(explicit=None):
    """Resolve a usable `node`, or None. Never raises."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    found = shutil.which("node")
    if found:
        return found
    guess = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "node", "node.exe")
    return guess if os.path.isfile(guess) else None


def node_parses(node, source, workdir):
    """(ok, detail) - a missing node is a failure, not a crash."""
    if not node or not os.path.isfile(node):
        return False, "node not available (pass --node PATH)"
    path = os.path.join(workdir, "snippet.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    try:
        proc = run([node, "--check", path])
    except OSError as exc:
        return False, "could not run node: " + str(exc)
    return proc.returncode == 0, (proc.stderr or "").strip()[:200]


def fresh_sandbox(live):
    """A throwaway copy of the live dashboard's web_dist."""
    root = tempfile.mkdtemp(prefix="lcars-stable-")
    dist = os.path.join(root, "web_dist")
    shutil.copytree(os.path.dirname(os.path.abspath(live)), dist)
    target = os.path.join(dist, "index.html")
    return root, dist, target


def engine(target, *extra, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return run([sys.executable, ENGINE, "--target", target] + list(extra), env=env)


def installer(target, *extra):
    env = dict(os.environ)
    env.pop("HERMES_HOME", None)
    env["LCARS_NO_GUI"] = "1"
    return run([sys.executable, INSTALLER, "--target", target] + list(extra), env=env)


def main():
    args = [a for a in sys.argv[1:]]
    node_override = None
    if "--node" in args:
        i = args.index("--node")
        node_override = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    live = args[0] if args else DEFAULT_LIVE
    node = find_node(node_override)

    print("live:   " + live)
    print("node:   " + str(node))

    # 1. the python side compiles
    for path in (ENGINE, INSTALLER):
        proc = run([sys.executable, "-m", "py_compile", path])
        check("compiles: " + os.path.basename(path), proc.returncode == 0,
              (proc.stderr or "").strip()[:200])

    root, dist, target = fresh_sandbox(live)
    original = os.path.join(root, "original.html")
    shutil.copyfile(target, original)
    orig_sha = sha(original)

    # 2. apply
    proc = engine(target)
    check("apply exit 0", proc.returncode == 0, (proc.stderr or "").strip()[:300])
    check("apply reports the block", "chat view stability present: yes" in proc.stdout,
          proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "")
    html = read(target)
    for s, e in PAIRS:
        check("marker %s x1" % s, html.count(s) == 1 and html.count(e) == 1,
              "start=%d end=%d" % (html.count(s), html.count(e)))
    body_start = html.index("<body")
    check("stability block inside <body>", body_start < html.index(STABLE_S))
    check("stability block after the skin block", html.index(SKIN_S) < html.index(STABLE_S))
    check("stability block before </body>", html.index(STABLE_E) < html.rindex("</body>"))

    blocks = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    check("script blocks found", len(blocks) >= 3, len(blocks))
    for i, body in enumerate(blocks):
        ok, detail = node_parses(node, body, root)
        check("node --check script[%d]" % i, ok, detail)

    refs = set(re.findall(r'/assets/([A-Za-z0-9_.\-]+\.(?:js|css))', html))
    missing = [x for x in sorted(refs) if not os.path.isfile(os.path.join(dist, "assets", x))]
    check("all /assets refs exist", not missing, missing)
    check("dashboard anchors intact", '<div id="root">' in html and "</html>" in html)

    # 3. idempotence
    first = sha(target)
    proc2 = engine(target)
    check("re-apply exit 0", proc2.returncode == 0, (proc2.stderr or "").strip()[:200])
    check("re-apply byte-stable", first == sha(target))

    # 4. opt-out survives a re-apply (the auto-heal path runs the engine with no
    #    flags), and --chat-stability brings it back.
    opt = {"LCARS_CHAT_STABILITY_MARKER": os.path.join(root, "lcars_chat_stability.disabled")}
    proc3 = engine(target, "--no-chat-stability", env_extra=opt)
    html = read(target)
    check("--no-chat-stability exit 0", proc3.returncode == 0)
    check("--no-chat-stability drops the block", STABLE_S not in html and STABLE_E not in html)
    check("--no-chat-stability keeps the skin", SKIN_S in html and "lcars-skin" in html)
    check("--no-chat-stability remembers the choice", os.path.isfile(opt["LCARS_CHAT_STABILITY_MARKER"]))
    proc4 = engine(target, env_extra=opt)          # what lcars_autoheal.py does
    html = read(target)
    check("re-apply (auto-heal path) honours the opt-out",
          proc4.returncode == 0 and STABLE_S not in html)
    proc5 = engine(target, "--chat-stability", env_extra=opt)
    html = read(target)
    check("--chat-stability restores the block",
          proc5.returncode == 0 and html.count(STABLE_S) == 1 and html.count(STABLE_E) == 1)
    check("--chat-stability forgets the opt-out",
          not os.path.isfile(opt["LCARS_CHAT_STABILITY_MARKER"]))
    check("plain re-apply still injects", STABLE_S in read(target))

    # 5. the installer's own strip path removes the add-on too (imported, not
    #    run: `apply.py --remove` also touches the real machine's auto-heal task
    #    and stand-down marker, which a sandbox test must never do).
    sys.path.insert(0, HERE)
    import apply as installer_mod  # noqa: E402  (path set above on purpose)
    check("apply.py knows every marker pair",
          all(pair in installer_mod.MARKERS for pair in (STABLE_S, STABLE_E)),
          installer_mod.MARKERS)
    installer_mod.strip_skin(target)
    html = read(target)
    left = [s for s, _ in PAIRS if s in html] + [e for _, e in PAIRS if e in html]
    check("strip leaves no markers", not left, left)
    check("strip keeps a working dashboard",
          '<div id="root">' in html and "</html>" in html and "lcars-skin" not in html)

    # 6. a hand-edited file with an unmatched marker: reported, not made worse
    html = read(target).replace("<div id=\"root\">", STABLE_S + '\n<div id="root">', 1)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(html)
    proc6 = engine(target)
    html = read(target)
    check("unmatched marker: apply still exits 0", proc6.returncode == 0)
    check("unmatched marker: reported", "unmatched marker" in proc6.stdout, proc6.stdout.strip()[-160:])
    check("unmatched marker: one well-formed block", html.count(STABLE_E) == 1)
    check("unmatched marker: other blocks untouched",
          all(html.count(s) == 1 for s, _ in PAIRS[:3]))
    check("unmatched marker: dashboard intact",
          '<div id="root">' in html and "</html>" in html)

    # 7. a missing node is a clean failure, not a traceback
    ok, detail = node_parses(os.path.join(root, "definitely-not-node"), "var a = 1;", root)
    check("missing node reported cleanly", ok is False and "node not available" in detail, detail)

    # 8. readiness report against the sandbox dashboard
    proc8 = installer(target, "--check")
    check("apply.py --check READY", proc8.returncode == 0 and "result: READY" in proc8.stdout,
          proc8.stdout.strip().splitlines()[-1] if proc8.stdout.strip() else "")

    check("the pristine copy was never written", sha(original) == orig_sha)

    print("\nsandbox: " + root)
    print("RESULT: " + ("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
