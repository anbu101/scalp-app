#!/usr/bin/env python3
# =====================================================================
#  apply_display_fix.py  —  fences END_LABELS_20261117 + CARD_HEAD_20261117  (DEF-042, DEF-043)
#  REQUIRES: apply_select_accent.py (SELECT_ACCENT_20261117). Frontend only.
#
#  Modern Dashboard, two display bugs found on the 17 Nov mock-up
#  (Tigris Live, Timberwolf Paper+Live, Icarus + Velvet Paper):
#   DEF-042  the 'Live' and 'Paper' labels at the right end of the MTM
#            chart were drawn on top of each other when the two lines end
#            close together. The line that ends higher now takes its label
#            above its dot, the other one below it (stacked, never touching),
#            whichever book is higher. Desktop and phone.
#   DEF-043  on a desktop card the book label was cut ('Pap...') because the
#            state badge shared the header row. The badge now sits beside
#            the card's MTM figure; the header keeps name + book + carried.
#            Idle cards wrap instead of overflowing. Phone cards unchanged.
#
#  Files: components/calm/CalmChart.jsx, components/calm/StrategyCard.jsx,
#  uitest/calm_fixture.py (mix_scene), uitest/test_ui_calm.py (TC-CALM-0034..0036).
#
#  Run ONLY this script, outside market hours, after apply_select_accent.py:
#     cd ~/dev/scalp-app && python3 apply_display_fix.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
#  UI check (optional, app closed): cd frontend && CI=false npm run build &&
#     PYTHONPATH=../backend python3 uitest/test_ui_calm.py
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "END_LABELS_20261117"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmChart.jsx", "orig": "9590ad791ea2a4dd7449d4e7c8b27b0f71e8babb7e5744d1a04852eb103bb8a9", "new": "89c5a6bf194974ea7cd5cf0b4d9bed97bd7597bb15ae16b1be1b3b64e908446b", "blob": "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"}, {"path": "frontend/src/components/calm/StrategyCard.jsx", "orig": "955adc8d8c96406647ccd9eedb0ab1a4f2eb5b7ed278c3498483e9f353957184", "new": "8565fdb0abf02d5e4d4e3cfc83f860b4be5b774ca24a48c11ce55ed99b8d4fa0", "blob": "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"}, {"path": "frontend/uitest/calm_fixture.py", "orig": "db7c1f5253e8427176ff5a78c5db394841b14b5ca2cae587f4465cde239a866c", "new": "17b75353d948267cce8fbe915c25b0add7d286525536f977810abc792fad1086", "blob": "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"}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "6c9249ea2789657c556a07fb07c6663763dbc811df310081f1cce576e2c2a7b3", "new": "7a5105ce9112a82584af36df85b5574b909514353391e3edd79df894248a6f55", "blob": "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"}]''')
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

# ── prerequisite: apply_select_accent.py
if "--rollback" not in args:
    _k = os.path.join(repo, "frontend", "src", "components", "calm", "CalmHero.jsx")
    if "SELECT_ACCENT_20261117" not in (open(_k, encoding="utf-8").read() if os.path.exists(_k) else ""):
        die("run apply_select_accent.py first — this script builds on it")


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
tmp = tempfile.mkdtemp(prefix="display_fix_")
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
say("  Undo: python3 apply_display_fix.py --rollback")
