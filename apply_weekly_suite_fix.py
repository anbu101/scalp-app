#!/usr/bin/env python3
# =====================================================================
#  apply_weekly_suite_fix.py  —  fence WEEKLY_SUITE_FIX_20261128   (on main a4c06063, v12.0.31)
#  TD-131 (DEF-119, DEF-120): the weekly Master Test Suite's false failures
#
#  27 Nov 16:00 weekly run: 211 passed, 3 failed — all three pass on main by hand.
#   backend/app/utils/test_test_home_guard.py    DEF-119: judged "the real home" as any folder
#                                                under /Users/<you>; the weekly run keeps each
#                                                suite's TMPDIR + HOME in ~/scalp-regression, so
#                                                the guard's own throw-away home counted as real.
#                                                Now: HOME must not be the real home, and app
#                                                paths / credentials must not be in the real
#                                                ~/.scalp-app (what the guard protects).
#                                                TC-UTIL-0037, 0038, 0042 + the save checks.
#   backend/app/engine/test_entry_fast_confirm.py   DEF-120: TMA_V2 / IC_V2 / TSG fast-confirm
#                                                bound 0.7 s -> 1.5 s (fast ~0.6 s, old >= 2 s);
#                                                TSG 1.6 s fill: "within 1 s of it" (the poll
#                                                count already proves the 0.3 s cadence).
#   backend/app/execution/test_exit_repeg_fast.py   DEF-120: D1 "no extra wait" counted (no
#                                                driver wait after the slow quote, one quote),
#                                                not timed; D3 / D4 / D4b on a simulated clock
#                                                (the driver's own clock + sleep), so fills,
#                                                re-pegs and polls land at the same moments.
#  Checked: each still fails when the guard is off, the driver waits after a slow quote,
#  fast quotes are treated as slow, the exit quote is dropped, or the polls go back to 2 s / 1 s.
#  Tests only: the app is unchanged, no release needed.
#  This script's own tests run with HOME = a temp folder (CL-22).
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_weekly_suite_fix.py
#  Then commit with `git add -A`.
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "WEEKLY_SUITE_FIX_20261128"
ITEMS = json.loads(r'''[{"path": "backend/app/utils/test_test_home_guard.py", "orig": "a105f0a7780eaf99e472110318603ad3ab88b299e11d9a89d984e1cc8497f2ec", "new": "8252b76801cf7f4f6ba939fbbb2df84595472526ef86553fd14205602b6298c9", "blob": "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"}, {"path": "backend/app/engine/test_entry_fast_confirm.py", "orig": "822b64890a9a532d17c4025464fa6435f562456e56638a573f9fe735a9d2236f", "new": "4aac580ed87da4b6f03912677fd953312fb39d81e28f953bcb9616e2b3fbc3ca", "blob": "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"}, {"path": "backend/app/execution/test_exit_repeg_fast.py", "orig": "53212432b8b75fc8c7a9a8f791855dc6dd97287a17d54354c38f2c54843a0c57", "new": "f14d2fb3257e264580d53d4de653d1bbffa8836e8ece83c859645d0991b80066", "blob": "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"}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["suite", "backend/app/utils/test_test_home_guard.py"], ["suite", "backend/app/engine/test_entry_fast_confirm.py"], ["suite", "backend/app/execution/test_exit_repeg_fast.py"]]   # suite = via scripts/run_suites.py; root = python3 <cmd> from the repo root   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"backend/app/utils/test_test_home_guard.py": ["TEST_HOME_GUARD_20261127"], "backend/app/engine/test_entry_fast_confirm.py": ["ENTRY_FAST_CONFIRM_20261109"], "backend/app/execution/test_exit_repeg_fast.py": ["EXIT_REPEG_FAST_20261116"]}
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
tmp = tempfile.mkdtemp(prefix="weekly_suite_fix_")
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
    _home = tempfile.mkdtemp(prefix="apply_home_")   # DEF-113: tests never get the real HOME (CL-22)
    env["HOME"] = _home
    env["USERPROFILE"] = _home
    failed, envmiss = [], []
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        cmd = ([sys.executable, os.path.join(repo, "scripts", "run_suites.py"), t] if where == "suite" else
               [sys.executable] + t.split() if where == "root" else [py, t])
        if not os.path.exists(os.path.join(cwd, t.split()[0])):
            continue
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
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
say("  Commit with `git add -A`: tests only, no release needed.")
say("  Undo: python3 apply_weekly_suite_fix.py --rollback")
