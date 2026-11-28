#!/usr/bin/env python3
# =====================================================================
#  apply_gate_fixture_exempt.py  —  fence GATE_FIXTURE_EXEMPT_20261128   (on main 3e2a331b, v12.0.30)
#  TD-049 (commit gate: ready for enforce)
#
#  Ten days of observe mode (78 commits): 2 would have been blocked. One was a real
#  catch (CL-22, 13857cb5, fixed a minute later). The other was a false positive:
#  CL-01 (hard-coded credential) and CL-10 (sub-second timing bound) fired on the
#  gate's OWN suite, whose sample lines exist to trip them. In enforce mode every
#  change to that suite would be blocked.
#   scripts/checklist/items.py                CL-01 and CL-10 skip scripts/test_reliability_checklist.py
#                                             (GATE_FIXTURES); every other file is checked as before
#   scripts/test_reliability_checklist.py     TC-GATE-0080 (the suite's fixtures are no finding),
#                                             TC-GATE-0081 (the same lines elsewhere still fail)
#  Tooling only: the app is unchanged, no release needed. MODE stays observe;
#  switching scripts/checklist/MODE to enforce is Anbu's call.
#  This script's own tests run with HOME = a temp folder (CL-22).
#  Note: this commit's own stamp may still show CL-01 / CL-10 fail — the gate reads
#  items.py from HEAD, i.e. before this fix (observe: never blocks).
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_gate_fixture_exempt.py
#  Then commit with `git add -A`.
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "GATE_FIXTURE_EXEMPT_20261128"
ITEMS = json.loads(r'''[{"path": "scripts/checklist/items.py", "orig": "d63d8e597d7a7dc67e5c58fca0052c162f578effef568d61d0338e942bc2d90a", "new": "19af3f80b1dc5014b2c77f68da5c2bb0849ceea434e24439666ae6d0ef6f9e5d", "blob": "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"}, {"path": "scripts/test_reliability_checklist.py", "orig": "1c51cdf759158677537ac0cb3c5d1bc257e47e08fd7dc684073f5edc1d6b75b6", "new": "ec93e1de723809a5d841ef4d7662efec090b7cf49fc805dea62a315d72d6a99c", "blob": "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"}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["suite", "scripts/test_reliability_checklist.py"]]   # suite = via scripts/run_suites.py; root = python3 <cmd> from the repo root   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"scripts/checklist/items.py": ["RELIABILITY_GATE_20261118", "TEST_HOME_GUARD_20261127"]}
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
tmp = tempfile.mkdtemp(prefix="gate_fixture_exempt_")
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
say("  Undo: python3 apply_gate_fixture_exempt.py --rollback")
