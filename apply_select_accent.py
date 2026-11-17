#!/usr/bin/env python3
# =====================================================================
#  apply_select_accent.py  —  fence SELECT_ACCENT_20261117  (DEF-041)
#  REQUIRES: apply_index_overlay.py (INDEX_PATH_20261117). Frontend only.
#
#  Modern Dashboard: the selected period tab (Today / This week / This
#  month), the selected index chip (NIFTY / BANKNIFTY), the All/Live/Paper
#  filter and the phone Live|Paper switch were outlined in the text colour
#  (white on dark themes). They now take the theme's accent colour, the
#  same one that rings the selected strategy card (blue on Dark, pink on
#  Arcade, blue on Light, and so on).
#
#  Files: components/calm/CalmHero.jsx, pages/CalmDashboard.jsx,
#  uitest/test_ui_index.py (+ TC-IDX-0024).
#
#  Run ONLY this script, outside market hours, after apply_index_overlay.py:
#     cd ~/dev/scalp-app && python3 apply_select_accent.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
#  UI check (optional, app closed): cd frontend && CI=false npm run build &&
#     PYTHONPATH=../backend python3 uitest/test_ui_index.py
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "SELECT_ACCENT_20261117"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmHero.jsx", "orig": "4757ec75108f56c8d6d26b8d201f70cc6942c874b7e30d3e1aa5e813b5ebff5e", "new": "12efbdd9ce87566e6ae91a5b68b4282ec5c4fedc6cec9b10dbf6d11c8272dbf3", "blob": "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"}, {"path": "frontend/src/pages/CalmDashboard.jsx", "orig": "2b99b26883bb3dadb3f9313cf9c070c7fed8e3a6fb815d6c5efa4e471a82449e", "new": "60e04d85a50374a692de8dc67177797125dbc649611a9fc1cc53af30061bfa56", "blob": "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"}, {"path": "frontend/uitest/test_ui_index.py", "orig": "9ed96d765290ec636123516e4d6aaa4029904390a8234d5abff8e9bf8a1c6f51", "new": "87c452ab6115054be3528e1e1389808ad00c37b8fa0bdb1850bcc0f10f0bb0cb", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = []   # frontend only: the JSX parse gate runs; UI suite optional (header)


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

# ── prerequisite: apply_index_overlay.py
if "--rollback" not in args:
    _k = os.path.join(repo, "frontend", "src", "components", "calm", "CalmHero.jsx")
    if "INDEX_PATH_20261117" not in (open(_k, encoding="utf-8").read() if os.path.exists(_k) else ""):
        die("run apply_index_overlay.py first — this script builds on it")


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
        # the earlier scripts' output is expected to be uncommitted still: every planned
        # file was just proven byte-identical to it (sha == orig), so only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="select_accent_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
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
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.")
say("  Undo: python3 apply_select_accent.py --rollback")
