#!/usr/bin/env python3
# =====================================================================
#  apply_mut_flaky_retry.py  —  fence MUT_FLAKY_RETRY_20261126   (on main 7847c62f)
#  DEF-084 · TD-106
#
#  Mutation runner, tooling only. On 22 Nov one suite's fork died importing
#  numpy ('Could not parse python long as longdouble', intermittent, worse
#  under load); mutmut stopped at "Failed to run clean test", tested NO mutant,
#  and survivors.txt said "0 survivors" — which reads like a perfect score.
#   * the suite shim retries a failed suite ONCE when no mutant is active
#     (mutmut's clean pass or stats pass); a mutant run and the forced-fail
#     check are never retried, so a killed mutant is never hidden
#   * a run whose log has "Failed to run clean test" gets the status
#     "FAILED: … NO mutants tested", and survivors.txt says so
#
#   scripts/mutation/mut_shim_suites.py   _no_mutant_active(); one retry
#   scripts/mutation/mutmut_run.py        clean_test_failed(); status + survivors.txt
#   scripts/mutation/test_mutmut_run.py   TC-MUT-0013…0015
#  Every edited file gets a .bak-MUT_FLAKY_RETRY_20261126 copy.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_mut_flaky_retry.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "MUT_FLAKY_RETRY_20261126"
ITEMS = json.loads(r'''[{"path": "scripts/mutation/mut_shim_suites.py", "orig": "75917df9a34090fcc9e6a35c87c8434ddbe8f0a5b08b8723a0124429a4b0db70", "new": "f296e73429cded69788f091da170e4dbb98339f5afc6beadb137983cd5ed8654", "blob": "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"}, {"path": "scripts/mutation/mutmut_run.py", "orig": "797f675459ad36678116bc9ee1cbfe439794cc3315056347d78fa687ca8edbb7", "new": "3b3a7832319a10b06d3955f66e6fa902534bbc77bde60d6f01abae2dffae65fb", "blob": "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"}, {"path": "scripts/mutation/test_mutmut_run.py", "orig": "2ccd3b7e6bb3678ad0c87ed22dd068096727238d80dc5844eb980a0f40bf1358", "new": "abd68a4aeec12ccac8a7e34d4cf0e0495730c53f8925a2969f46882afee496a2", "blob": "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"}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "scripts/mutation/test_mutmut_run.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"scripts/mutation/mutmut_run.py": ["MUTMUT_20261117", "MUT_TMP_SWEEP_20261122"], "scripts/mutation/mut_shim_suites.py": ["MUTMUT_20261117"]}
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
tmp = tempfile.mkdtemp(prefix="mut_flaky_retry_")
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
        _mode = os.stat(p).st_mode if cur is not None else None
        if cur is not None:
            shutil.copy2(p, p + ".bak-" + FENCE)            # keeps the mode for --rollback
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
say("  Tooling only: commit; nothing here ships in the app.")
say("  Undo: python3 apply_mut_flaky_retry.py --rollback")
