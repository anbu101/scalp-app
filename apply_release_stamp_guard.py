#!/usr/bin/env python3
# =====================================================================
#  apply_release_stamp_guard.py  —  fence RELEASE_STAMP_GUARD_20261127   (on main 87e7fe8b)
#  DEF-112 — v12.0.22 installs said "you have v12.0.21"
#
#  v12.0.22 was cut with desktop/deploy-scalp.command, an older copy of the
#  release script that bumps only tauri.conf.json and never writes
#  backend/app/version_stamp.txt. The builds reported v12.0.21 to the backend,
#  so every install showed a false update banner (and the Fleet Monitor showed
#  the wrong version).
#
#   desktop/deploy-scalp.command           RETIRED: exits 1, points to ./deploy-scalp.command
#   scripts/checklist/items.py             CL-21: a tauri.conf.json version bump must change
#                                          version_stamp.txt to the same version
#   scripts/test_reliability_checklist.py  TC-GATE-0072…0076; 0038/0040 count 21 items
#
#  Run ONLY this script:  cd ~/dev/scalp-app && python3 apply_release_stamp_guard.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "RELEASE_STAMP_GUARD_20261127"
ITEMS = json.loads(r'''[{"path": "desktop/deploy-scalp.command", "orig": "f41ac5adf9bfc926afb9a56050ce5a8255470fda183a48a6626acbdb0d5f47a1", "new": "c5d3c14ad445fa44953cc67d70a145967b6f3c05ebec584fdfb1a4a3009b60e5", "exec": false, "blob": "eNp1kc1q20AUhfd+ilMZQgL2KNKihewMVkugqYPirM1YGqNpxnOHmbEUbUoeItBNn85P0itV6aI/ILQYznz3O3fm79JT8Ole21TZFnsZmtkctQpPkVxaK2eoX4ZKGicqOh6lrXF+ecX59YU/lMXnYvVQ7B62q7v73afHVbne5df5+yzLP7xlLtfFxyUfXHF6e1sW69mcJ2wbHdDJAGlBplYeFbkel3eyx0C4Ah0QGwWvjJJBIVReuyhwG7E/HZ2qQdb0TIry5DXL2YP4GshiULSqZWLnKSquVD0pW6fSuZRPgya7C1EenYjPcYFAaLNcXIs8XzCtOkV0OjbQkQeAa3yhdsEWjnzkoVM2Q6RRb6LfYJjYQ1tGG8Og0FDHeYmDNKyfWNWx0iQALi9bqY3cG5UwxFrlxbiWPxsPUfHvh5BxVCiL+w3KzWYrZqpqaPwhOf/4/mvJf+O8itqz2++XEcl0CXjk5AAlq974Q3d4ongDVDW+sUybjiZL3ikuLv7jl0w6z7zKbPYTxR7O5g=="}, {"path": "scripts/checklist/items.py", "orig": "6e7d6706408aa4d4ae4c332f526751e9fd205604d797e35d44776b77bd870340", "new": "8abcc2921a305858d3df11ab5af039af8e686dddb0b0cab1c905bdb975176136", "exec": false, "blob": "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"}, {"path": "scripts/test_reliability_checklist.py", "orig": "bc48d774205a08493c25a43017fb0996bfe9fd5397c88316bbfa9860f30db777", "new": "ba4e39b5b97d6ae7fbea36d513a40960e56755d0ce5becb8b0549bdd07839c50", "exec": false, "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "scripts/test_reliability_checklist.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"scripts/checklist/items.py": ["MONITOR_DEPLOY_FILES_20261127"]}
if "--rollback" not in args:
    for _rel, _fs in PARENTS.items():
        _pf = os.path.join(repo, _rel)
        _txt = open(_pf, encoding="utf-8").read() if os.path.exists(_pf) else ""
        _miss = [f for f in _fs if f not in _txt]
        if _miss:
            die(f"{_rel} lacks {', '.join(_miss)} — pull the latest main first")


def refresh_master():
    """MASTER_SUITE.md and REPO_MAP.md follow the suites on disk (a new uitest file)."""
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
                if os.path.isdir(os.path.join(repo, b)):        # the build tree exists: new folders too
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
tmp = tempfile.mkdtemp(prefix="release_stamp_guard_")
blobs = {}
for it in ITEMS:
    if it["new"] is None:                # a deletion: nothing to decode
        continue
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
            bak = p + ".bak-" + FENCE
            if old is None:
                os.remove(p)
            elif os.path.exists(bak):
                shutil.copy2(bak, p)                 # bytes + mode back (also for a deleted file)
            else:
                open(p, "wb").write(old)
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        _mode = os.stat(p).st_mode if cur is not None else None
        if cur is not None:
            shutil.copy2(p, p + ".bak-" + FENCE)            # keeps the mode for --rollback
        if it["new"] is None:                # a deletion: backed up above, then removed
            os.remove(p)
            written.append((p, cur))
            say(f"  deleted {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
            continue
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        if _mode is not None:
            os.chmod(t, _mode)
        os.replace(t, p)
        if it.get("exec"):
            os.chmod(p, 0o755)
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
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        if not os.path.exists(os.path.join(cwd, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.abspath(sys.executable) != os.path.abspath(py)):
            try:
                r = subprocess.run([sys.executable, t], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
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
        if it["new"] is not None and it["path"].startswith("frontend/src/") and it["path"].endswith((".jsx", ".js")):
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
say("  Then release with the REPO-ROOT script: ./deploy-scalp.command")
say("  Undo: python3 apply_release_stamp_guard.py --rollback")
