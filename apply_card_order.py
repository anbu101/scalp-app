#!/usr/bin/env python3
# =====================================================================
#  apply_card_order.py  —  fence CARD_ORDER_20261120   (on READY_PANEL_20261118, 12.0.8)
#
#  Dashboard strategy cards were sorted by the size of today's P/L, so they
#  swapped places every few seconds as prices moved. The order is now fixed:
#    1. strategies in a trade (either book open)
#    2. strategies that traded today and are closed
#    3. waiting (idle) strategies
#  and by name inside each group. P/L never moves a card.
#
#   frontend/src/pages/CalmDashboard.jsx   orderCards(), orderIdle()
#   frontend/uitest/test_ui_calm.py        TC-CALM-0007 (new order), TC-CALM-0041 NEW
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_card_order.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh frontend, release 12.0.9.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "CARD_ORDER_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/pages/CalmDashboard.jsx", "orig": "9ebf03152d5713f4aa9de0b33451ef56fa4cb9361cdf68a356f449b3f228d463", "new": "00e9d5d2630a726104a24f7ed57c3b3094fd33256f03b810477102660a8872e4", "blob": "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"}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "2a3a31da231e869fecd20f1cdf8f29f38665de430722d37c47a97134a69191e3", "new": "64eec5134a9782aa2bfbc8be41042a4fb5bfd7573071ed5af19557c19e65933c", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = []


def sha(b):
    return hashlib.sha256(b).hexdigest()


def say(m):
    print(m, flush=True)


def die(m):
    say(f"\n✗ ABORTED — {m}\n  Nothing was changed.")
    sys.exit(1)


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo (no backend/app) — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")

# ── prerequisite: READY_PANEL_20261118 (the Dashboard this patches) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "src", "pages", "CalmDashboard.jsx")
    if not os.path.exists(_pf) or "READY_PANEL_20261118" not in open(_pf, encoding="utf-8").read():
        die("frontend/src/pages/CalmDashboard.jsx lacks READY_PANEL_20261118 — pull the latest main first")


def targets():
    """(item, absolute path, is_build_copy)"""
    out = []
    for it in ITEMS:
        out.append((it, os.path.join(repo, it["path"]), False))
        for a, b in BUILD_COPIES.items():
            if it["path"].startswith(a):
                p = os.path.join(repo, b + it["path"][len(a):])
                if os.path.isdir(os.path.dirname(p)):
                    out.append((it, p, True))
    return out


# ── rollback mode ────────────────────────────────────────────────────
if "--rollback" in args:
    later = []
    for it, p, build in targets():
        if build or not os.path.exists(p) or not os.path.exists(p + ".bak-" + FENCE):
            continue
        with open(p, "rb") as f:
            if sha(f.read()) != it["new"]:
                later.append(os.path.relpath(p, repo))
    if later:
        die("cannot roll back — changed again after this script (by a later fix or a local edit): "
            + ", ".join(later) + ". Roll the later fixes back first, newest first. Nothing was changed.")
    n = 0
    for it, p, _ in targets():
        bak = p + ".bak-" + FENCE
        if os.path.exists(bak):
            shutil.copy2(bak, p)
            os.remove(bak)
            n += 1
        elif it["orig"] is None and os.path.exists(p):
            with open(p, "rb") as f:
                if sha(f.read()) == it["new"]:
                    os.remove(p)
                    n += 1
    say(f"rolled back {n} file(s)")
    sys.exit(0)

# ── 1. state of every target ─────────────────────────────────────────
plan, done, skipped, later = [], 0, [], []
_applied = False                         # this script's marker is in the tree → it was applied
for it, p, build in targets():
    if not build and os.path.exists(p):
        with open(p, "rb") as f:
            if FENCE.encode() in f.read():
                _applied = True
for it, p, build in targets():
    cur = open(p, "rb").read() if os.path.exists(p) else None
    cs = sha(cur) if cur is not None else None
    rel = os.path.relpath(p, repo)
    if cs == it["new"]:
        done += 1
        continue
    if cs == it["orig"]:
        plan.append((it, p, cur, build))
        continue
    if build:
        skipped.append(rel)              # a stale build copy — the build re-syncs it
        continue
    if cur is None:
        die(f"{rel} is missing")
    if _applied:
        later.append(rel)                # applied, then changed by a later fix
        continue
    die(f"{rel} differs from GitHub main and from the patched version — local edits? "
        f"(commit/stash them or pull main, then re-run)")
if not plan:
    say(f"✓ already applied ({done} file(s) carry {FENCE}"
        + (f"; {len(later)} changed again by later fixes" if later else "") + ") — nothing to do")
    sys.exit(0)
if later:
    die("partly applied: " + ", ".join(later) + " changed after this script while other files are "
        "not patched — check the tree (git status) before going on. Nothing was changed.")

# ── 2. git status: an uncommitted change on a target is a stop sign ──
if "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for _, p, _, b in plan if not b]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        # a planned file was just proven byte-identical to its expected base (sha == orig): only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="card_order_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, it["path"].replace("/", "_"))
        open(f, "wb").write(b)
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
say(f"payloads verified and compiled ({len(ITEMS)} files)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                os.remove(p)
            else:
                open(p, "wb").write(old)
            bak = p + ".bak-" + FENCE
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        if cur is not None:
            open(p + ".bak-" + FENCE, "wb").write(cur)
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")

# ── 5. tests ─────────────────────────────────────────────────────────
backend = os.path.join(repo, "backend")
py = next((c for c in (os.path.join(backend, "venv", "bin", "python"), os.path.join(backend, ".venv", "bin", "python"),
                       os.path.join(repo, "venv", "bin", "python"), os.path.join(repo, ".venv", "bin", "python"))
           if os.path.exists(c)), sys.executable)
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    say(f"running tests with {py}")
    env = dict(os.environ, PYTHONPATH=backend, SCALP_TEST_MODE="1")
    failed, envmiss = [], []
    for t in TESTS:
        if not os.path.exists(os.path.join(backend, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.realpath(sys.executable) != os.path.realpath(py)):
            try:
                r = subprocess.run([sys.executable, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
                outp = (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired:
                r, outp = None, "timeout"
        last = (outp.strip().splitlines() or [""])[-1]
        if r is not None and r.returncode == 0:
            say(f"  ✓ {t}  {last}")
        elif "ModuleNotFoundError" in outp and "No module named 'app" not in outp:
            envmiss.append(t)
            say(f"  ? {t}  could not run here: {last}")
        else:
            failed.append(t)
            say(f"  ✗ {t}\n" + "\n".join("      " + l for l in outp.strip().splitlines()[-25:]))
    if failed:
        restore()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: some suites need the backend's Python packages; re-run them with that Python")

# ── 5b. JSX parse gate (esbuild from frontend/node_modules, if installed) ──
_esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if os.path.exists(_esb) and "--skip-tests" not in args:
    bad = []
    for it in ITEMS:
        if it["path"].startswith("frontend/src/") and it["path"].endswith((".jsx", ".js")):
            r = subprocess.run([_esb, os.path.join(repo, it["path"]), "--loader:.js=jsx", "--loader:.jsx=jsx",
                                "--log-level=error"], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(it["path"] + ": " + (r.stderr or "").strip()[:300])
    if bad:
        restore()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Next: ./desktop/build-scalp.sh frontend, then release 12.0.9.")
say("  UI check (app closed, after the build): cd frontend && PYTHONPATH=../backend python3 uitest/test_ui_calm.py")
say("  Undo: python3 apply_card_order.py --rollback")
