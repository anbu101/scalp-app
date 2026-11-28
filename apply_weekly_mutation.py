#!/usr/bin/env python3
# =====================================================================
#  apply_weekly_mutation.py  —  fence WEEKLY_MUTATION_20261128   (on main a4c06063, v12.0.31)
#  TD-133: mutation testing as a weekly suite, Saturday 10:00 IST
#
#  The Master Suite runs every Friday (com.scalp.weekly-regression); mutation testing
#  had no schedule and its results were overwritten run by run. New:
#   scripts/mutation/weekly.py        install / status / run-now / run / snapshot / uninstall
#                                     a launchd job com.scalp.weekly-mutation (Saturday 10:00,
#                                     caffeinate, background priority) that runs
#                                     mutmut_run.py --fresh (all 5 modules, ~2.5 h) and keeps
#                                     ~/scalp-mutation-weekly/<date>/ (report.md, result.json,
#                                     SUMMARY.md, survivors, log tails; latest -> newest; 12 kept)
#                                     with the change since last week and every new survivor
#   scripts/mutation/test_weekly.py   TC-MUT-0016…0022 (no mutmut; temp HOME and folders)
#  Tooling only: the app is unchanged, no release needed. The job is NOT installed by
#  this script; after it, once:
#     python3 scripts/mutation/weekly.py install
#  (it also keeps the results already in ~/scalp-mutation as the first run to compare with).
#  Claude's Saturday review (a Claude app scheduled task) reads ~/scalp-mutation-weekly/latest.
#  This script's own tests run with HOME = a temp folder (CL-22).
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_weekly_mutation.py
#  Then commit with `git add -A`.
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "WEEKLY_MUTATION_20261128"
ITEMS = json.loads(r'''[{"path": "scripts/mutation/weekly.py", "orig": null, "new": "e069c477b35f94f820c072cc094cbc883be2fa027282c23ae8864e40a4b88045", "blob": "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"}, {"path": "scripts/mutation/test_weekly.py", "orig": null, "new": "8324c1f17c2aac5eb12eaedb4f0512197d87490d40c77bcf76a051b751e8c7de", "blob": "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"}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["suite", "scripts/mutation/test_weekly.py"], ["suite", "scripts/mutation/test_mutmut_run.py"]]   # suite = via scripts/run_suites.py; root = python3 <cmd> from the repo root   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"scripts/mutation/mutmut_run.py": ["MUTMUT_20261117", "MUT_FLAKY_RETRY_20261126"]}
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
tmp = tempfile.mkdtemp(prefix="weekly_mutation_")
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
say("  Commit with `git add -A`: tooling only, no release needed.")
say("  Then install the Saturday 10:00 job once: python3 scripts/mutation/weekly.py install")
say("  Undo: python3 apply_weekly_mutation.py --rollback   (uninstall the job first: python3 scripts/mutation/weekly.py uninstall)")
