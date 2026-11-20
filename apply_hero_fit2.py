#!/usr/bin/env python3
# =====================================================================
#  apply_hero_fit2.py  —  fence HERO_FIT2_20261120   (on HERO_FIT_20261120)
#
#  HERO_FIT_20261120 never took effect in the real Dashboard: CalmDashboard
#  always passes the Ready panel as `aside` (the panel returns null outside its
#  hours), and the fit was switched off whenever `aside` was set, so the chart
#  stayed 268 px and the empty space under the tickers remained. The standalone
#  renders passed no `aside`, so they looked right.
#  Now the fit is off only while the panel actually has height, and the plot
#  height is measured from where the plot starts (any gap above it allowed for).
#  The existing TC-IDX-0027 (real Dashboard) covers it.
#
#   frontend/src/components/calm/CalmHero.jsx  fit measured; data-hero-aside on the panel slot
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_hero_fit2.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "HERO_FIT2_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmHero.jsx", "orig": "0fee3269e301e2e8fdbd861dc57bb3273929b0c3f5ec1c876a988d000ff65051", "new": "2c8997a71d9a884c4a100dd4ffa7b1e33eb2d2b748a6176a4c908601f7e17916", "blob": "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"}]''')
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

# ── prerequisite: HERO_FIT_20261120 (the hero this patches) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "src", "components", "calm", "CalmHero.jsx")
    if not os.path.exists(_pf) or "HERO_FIT_20261120" not in open(_pf, encoding="utf-8").read():
        die("frontend/src/components/calm/CalmHero.jsx lacks HERO_FIT_20261120 — run apply_hero_fit.py / pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="hero_fit2_")
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
say("  Next: release with ./deploy-scalp.command (all three platforms).")
say("  UI check, APP CLOSED: cd frontend && CI=false npm run build && PYTHONPATH=../backend python3 uitest/test_ui_index.py")
say("  Undo: python3 apply_hero_fit2.py --rollback")
