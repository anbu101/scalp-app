#!/usr/bin/env python3
# =====================================================================
#  apply_tsg_wing_test_date.py  —  fence TSG_WING_TEST_DATE_20261121
#
#  DEF-081. backend/app/engine/tsg/test_tsg_wing_guard.py (t_chain_band) builds
#  the 11-Nov option chain with expiry 17 Nov, but ic_selection.snapshot_weekly_chain
#  keeps only expiries >= date.today(). From 18 Nov the fixture chain came back
#  empty and the suite failed ("max() arg is an empty sequence"). The test now
#  freezes "today" at 11 Nov for that call, and a new check (TC-TSG-0127) proves
#  the fixture chain is non-empty. Test only; no app code changes.
#
#   backend/app/engine/tsg/test_tsg_wing_guard.py   frozen date in t_chain_band
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_tsg_wing_test_date.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "TSG_WING_TEST_DATE_20261121"
ITEMS = json.loads(r'''[{"path": "backend/app/engine/tsg/test_tsg_wing_guard.py", "orig": "d2ff359e8ee15736d61abf03c9ef33ddc9eec2a799d6eac0d4db9fee0608be99", "new": "f476aa85ddf55f34ec90683afebd9f0b84d4926fa2e3032b1411b9c53c0c2ab9", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["app/engine/tsg/test_tsg_wing_guard.py"]


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

# ── prerequisite: the wing guard (parent fence) on main ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "backend", "app", "engine", "tsg", "test_tsg_wing_guard.py")
    if not os.path.exists(_pf) or "TSG_WING_GUARD" not in open(_pf, encoding="utf-8").read():
        die("test_tsg_wing_guard.py (TSG_WING_GUARD_20261111) not found — pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="tsg_wing_test_date_")
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
say("  Nothing to build: this only changes a backend test.")
say("  Check: cd backend && python3 app/engine/tsg/test_tsg_wing_guard.py")
say("  Undo: python3 apply_tsg_wing_test_date.py --rollback")
