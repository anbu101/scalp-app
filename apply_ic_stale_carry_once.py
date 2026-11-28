#!/usr/bin/env python3
# =====================================================================
#  apply_ic_stale_carry_once.py  —  fence IC_STALE_CARRY_ONCE_20261128   (on main fd3e0b5f)   TD-134
#  Karthik's app, back after a month (28 Nov), raised "IC_V2: refusing to
#  restore a 103-day-old carry snapshot. Check Kite positions NOW." — and would
#  raise it again at EVERY start: a refused snapshot (> 7 days) was never moved.
#  Now: still refused, still one CRITICAL, then the file is kept aside as
#  <sid>_carry.stale-<YYYYMMDD-HHMMSS>.json (renamed, not deleted) and the
#  alert says it will not repeat. If it cannot be renamed, it stays and the
#  alert repeats (the safe side).
#
#   backend/app/engine/ic/ic_carry_store.py               archive_stale_carry()
#   backend/app/engine/ic/ic_engine.py                    refused branch: alert once, keep aside
#   backend/app/engine/ic/test_ic_engine_monitor_eod.py   ET12d, ET12e
#
#  Run ONLY this script:  cd ~/dev/scalp-app && python3 apply_ic_stale_carry_once.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "IC_STALE_CARRY_ONCE_20261128"
ITEMS = json.loads(r'''[{"path": "backend/app/engine/ic/ic_carry_store.py", "orig": "67abf9f4543c17f07136e5b83902419ae824deb3343bb93f440170b7c2384895", "new": "b70ff427534496ddc785acd7a6aa3d27fa9df691ff7bca8ccb278b55623a3265", "exec": false, "blob": "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"}, {"path": "backend/app/engine/ic/ic_engine.py", "orig": "03aa01cb1d22feecea0d9b32eb3da1e946f00256a4f351e43441d4708ce85b7d", "new": "f86e692f78f88e77f58709027b278d7fc5a15fd23f85672a2063c1408be720c5", "exec": false, "blob": "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"}, {"path": "backend/app/engine/ic/test_ic_engine_monitor_eod.py", "orig": "bf1dc51e450cf7e697de27d065f28722af59076a72d020bb409e4564f70a93d0", "new": "01c639fa2f5731f5c0ddacfccaa3decd91575145f65494d37ce9028d33b1f5c9", "exec": false, "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["here", "backend/app/engine/ic/test_ic_engine_monitor_eod.py"], ["here", "backend/app/engine/ic/test_ic_group_manager.py"]]   # here = the test's own folder (stub-tree suites)   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"backend/app/engine/ic/ic_engine.py": ["TRADING_DAY_GATE_20260816", "MORNING_ALERT_GRACE_20261116"],
           "backend/app/engine/ic/ic_carry_store.py": ["IC_RESTART"]}
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
tmp = tempfile.mkdtemp(prefix="ic_stale_carry_once_")
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
    env["HOME"] = tempfile.mkdtemp(prefix="apply_home_")   # DEF-113: tests never get the real HOME (CL-22)
    failed, envmiss = [], []
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        if where == "here":                       # suites that stub the app tree run from their own folder
            cwd, t = os.path.join(repo, os.path.dirname(t)), os.path.basename(t)
        if not os.path.exists(os.path.join(cwd, t)):
            continue
        try:
            # here-suites: as scripts/run_suites.py does: python -m pytest from the test's folder, folder on the path
            py_t = py
            if where == "here" and subprocess.run([py, "-c", "import pytest"], capture_output=True).returncode != 0:
                py_t = sys.executable                # the project venv has no pytest (DEF-098): use this Python
            cmd = ([py_t, "-m", "pytest", "-q", "-p", "no:cacheprovider", t] if where == "here" else [py, t])
            env_t = dict(env, PYTHONPATH=env["PYTHONPATH"] + os.pathsep + ".") if where == "here" else env
            r = subprocess.run(cmd, cwd=cwd, env=env_t, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.abspath(sys.executable) != os.path.abspath(py)):
            try:
                cmd2 = ([sys.executable] + cmd[1:]) if where == "here" else [sys.executable, t]
                r = subprocess.run(cmd2, cwd=cwd, env=env_t, capture_output=True, text=True, timeout=900)
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
say("  App change: ships with the next release (./deploy-scalp.command, all 3 platforms).")
say("  Undo: python3 apply_ic_stale_carry_once.py --rollback")
