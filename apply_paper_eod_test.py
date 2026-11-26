#!/usr/bin/env python3
# =====================================================================
#  apply_paper_eod_test.py  —  fence PAPER_EOD_TEST_20261126   (on main 67aec043, v12.0.21)
#  DEF-107
#
#  backend/app/dev/test_paper_eod.py was not a test: a 9-line manual trigger
#  with no checks that ran the REAL 15:25 EOD paper square-off job against
#  whatever ~/.scalp-app/app.db it found (run by hand on this Mac it would
#  square off the open paper trades). Since every suite gets a throw-away HOME
#  (DEF-094) it failed with "no such table: paper_trades".
#  Deleted, and replaced by a hermetic test of the job itself (the square-off
#  logic is already covered by db/test_paper_trade_squareoff.py):
#   backend/app/jobs/test_paper_trade_eod.py   NEW — TC-JOB-0088…0091: holiday gate,
#                                              square-off then reconcile, a failing
#                                              square-off is not swallowed, 15:25 IST schedule
#   backend/app/dev/test_paper_eod.py          DELETED (backed up first; --rollback restores it)
#  Tests only: the app is unchanged.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_paper_eod_test.py
#  Then commit with `git add -A` (stages the deletion).
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "PAPER_EOD_TEST_20261126"
ITEMS = json.loads(r'''[{"path": "backend/app/jobs/test_paper_trade_eod.py", "orig": null, "new": "e8e7d775880bff99f4dafded16fae162eb2808741353c8b6d8867091d76633f7", "blob": "eNqdVutu00gU/u+nOJpK1F4S5wJlIav86LZBpRSomv5rI2tiTxpv7RkzM2npIlY8BM+wD8aT7DfjXNyGBUTExT4+1+9cd2jK02shsw6vqs5famo6VhibVLwSOrGaZyIRKourO/r6+Qt9/fIZf+h0/3R0lozeHSbno/F50u/2n/V6/Werz+Hh6GW71/09ovOD9vG7P9vd7vPnXz//2+2+6AU7wQ6dzwX19gb9PYIO8rYItin0DmzbjohrtZAZWQia9wuuRVvNZnjlFuqy6bbTNReYnOvuqxl46bfjUXuuijzjd3TFrWg5qmwobUGhJ2mRKpnmBVg4TOfWUFXwVJCSXhMAI5PORbYohI7pSOhS2Dwd0FTZORkrKuMFwQuVKS+AMteIBCHYxXQqshZJRRm3fMqNoNyQqoSsyXaeyytHqh3LyEUCNWfC+2Cc9U4mbpqB11jBWyq5XPCCrM6vrgDtbQ6HYArOptfGo0aay6VnZ6P9E48+v+K5NJZu8V3cQO6fTmzgd9V2xvA3zqaAgWYuFbFPJFGarSqIHj2i6s7OlXxCPyqmIC8rpS0ps3rSYvVk7kww06pEXdh5kcNkTT/FaxAcjc5GNPQvYZLMkJ4kiWItjCpuRBgFkI6dYIxQhLZhtwW0dejEQNdCWnPRn0QRrX87FMdxZxlEECgTC3mTayVjI2wmZnxR2JCND/ZPTutyf/PucMRaxHosClZOO3RcwPGDWIkbOqb7vx3k4j0f0Ohpt9+UX9i8MHHJ9bWwyVwttHHSb47oe/LBy/1XJ2MgcjEJggDu1lkOJS9RuKhglFMmLM+LIWPRIHAKKp1LG4aM0MjjMRGjfOZZSRQoRNCdUtAjekxOEf4L2QOuGdguPjpsa/XRxeBJtzv5NGFR5K2AG3XsJWqz7ue9jREtsPY+AsIANVaYFhXqyvhAWi6Y47gu/QSVn7jGaJaR4yt4Oc0Ag5deqWQbIWTnOF43cZIWyojs55SspbyOW51bkfBFltsEPq6lqBx4n1diZRQkWvAiyY03gA5O3JgZIofxfVoQWH1Xo7L1baM/G75VUgzoJQfgnvn4YX0lKLqwxrvOO2uMXDfsaDXsOnQrhCvxwXq8NGYLKb0ZeCgZ2AVbW1UuQipyKVgr2JSgh4uGLll+xnF5FzKpZHsZB6baHepFUolpofEvHh1UUYvCRrZRKcFaXZwWgutlMB7XJuEnYDrXi19A6QU1fB40F8HWGhA8nQOZFI9TYQGnpN1jDE5jubYi2/VQeMosl7nBatj9H9SaZYpJsqm3SYPdabt4AKDrKgYLNbiTWtkFtujkwi/lCTUccqq3vq1cY5NfyoWnuDGTTJUqw2jT2t/pwxWL5jkmx9lC2rwUI62VDhl2SqEwe7Ml3w/73hv2rOse+lHS17YzyG+6SXxIRWXv+TPY5l+X1cPSedHFqp1h9G16yV8l2Npu8JlbAKJuoSP0Z8vqVFjm0UY+wevUU6ZELagXklijDhgODk5zXszamevLjSkkeOmmU/Xt8nJprplaNQcSjRrA03cG0LfmWBAYneLb/V3am2CyMF7lCdYtjgYsd+b2Mc8SKz7YUMhUOQ1DtrCz9nMkuYQOLbBcuU7noWY886m6DC/Nb99IYQvk5SEzvGQp9vIlczS3IIfhZfY4cm9lLhdW1O9ABZ5GwVa+ett3mwPbJYNwEtUX6avxOfoCZv7w3PBgF4fZrfTXIpnrHGfdcpLW991ywJpo3evlqgTcXPI8YRlf4YKtwh4m4Oq5jysE2QpZb891an+P1SWx66rxb4gO2b7Jeee1Kq5xJLJd1/OIDBrQh/USn7FL+fEptQnXZeiXa/QJh5Px6f7YJLpC9U2GCeJpdfbdvSQ+5BaeLVWy/ZOT+sL31zmwo4Oj0cHrsb8WRodQ8R8Ds+MH"}, {"path": "backend/app/dev/test_paper_eod.py", "orig": "f4faf0690a0864d977b61573f6ac1d94b398c4d1d7e798c678bcee970c42dadd", "new": null, "blob": null}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["backend", "app/jobs/test_paper_trade_eod.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {}
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
tmp = tempfile.mkdtemp(prefix="paper_eod_test_")
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
say("  Commit with `git add -A` (stages the deletion): tests only, no release needed.")
say("  Undo: python3 apply_paper_eod_test.py --rollback")
