#!/usr/bin/env python3
# =====================================================================
#  apply_idx_test_fix.py  —  fence IDX_TEST_FIX_20261126   (on main 7dd4dd1f, v12.0.18)
#  DEF-104 · TD-097
#
#  test_ui_index.py failed 2 of 15 checks on main since HERO_SPLIT_20261120
#  (65cb5af5, 20 Nov); the app is right, the checks were stale:
#   TC-IDX-0008  "no ▲ anywhere" also hit the hero's own "Open ▲ ₹56,518".
#                Now it looks for the old badge's shape ("▲ +102.5 (").
#   TC-IDX-0025  compared the NIFTY chip with the closed / open line's BOX,
#                which is pulled 21 px left to hide its first divider
#                (box 11 px, text 32 px, chip 32 px). Now it compares the
#                chip with the line's first label.
#  Also: with no CHROME set the suite looked only at /usr/bin/google-chrome;
#  it now finds Chrome on a Mac as test_ui_debug_menu.py does.
#
#   frontend/uitest/test_ui_index.py   (15/15 on Anbu's Mac, 26 Nov)
#  Build copy under desktop/src-tauri/ is written too when present.
#  Test-only: nothing in the app changes, no release needed.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_idx_test_fix.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "IDX_TEST_FIX_20261126"
ITEMS = json.loads(r'''[{"path": "frontend/uitest/test_ui_index.py", "orig": "88039b19e7901e747ad36b72a1c6549e6f8a42b03f30a639b8903bdf6ce38332", "new": "788fd0c9e677c8d03797372c01a4bc03cbbd7059ecb55c44a9f8e8f669fb3f2a", "blob": "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"}]''')

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

# ── parent fences: the code this patches ─────────────────────────────
PARENTS = {"frontend/uitest/test_ui_index.py": ["DASH_TIDY_20261126", "HERO_FIT_20261120"],
           "frontend/src/components/calm/CalmHero.jsx": ["HERO_SPLIT_20261120", "DASH_TIDY_20261126"]}
if "--rollback" not in args:
    for _rel, _fs in PARENTS.items():
        _pf = os.path.join(repo, _rel)
        _txt = open(_pf, encoding="utf-8").read() if os.path.exists(_pf) else ""
        _miss = [f for f in _fs if f not in _txt]
        if _miss:
            die(f"{_rel} lacks {', '.join(_miss)} — pull the latest main first")


def refresh_master():
    """MASTER_SUITE.md (each suite's hash + TC IDs) follows the suites on disk."""
    for gen, a_, name in (("scripts/regression/master_suite.py", ["refresh"], "MASTER_SUITE.md"),
                          ("scripts/gen_repo_map.py", [], "REPO_MAP.md")):
        g = os.path.join(repo, gen)
        if not os.path.exists(g):
            continue
        r = subprocess.run([sys.executable, g] + a_, cwd=repo, capture_output=True, text=True, timeout=300)
        msg = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        say("  " + (msg[-1] if msg else f"{name} refreshed") if r.returncode == 0
            else f"  !! {name} refresh failed — pre-commit will refresh it")


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
    refresh_master()
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
tmp = tempfile.mkdtemp(prefix="idx_test_fix_")
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
refresh_master()

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
        refresh_master()
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
        refresh_master()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Test-only change: no release needed. Commit it.")
say("  UI check, APP CLOSED: cd frontend && CI=false npm run build && PYTHONPATH=../backend python3 uitest/test_ui_index.py   (15/15 expected)")
say("  Undo: python3 apply_idx_test_fix.py --rollback")
