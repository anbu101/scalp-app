#!/usr/bin/env python3
# =====================================================================
#  apply_intl_date_cache.py  —  fence INTL_DATE_CACHE_20261127   (on main cf6f8ca0)   DEF-115
#  27 Nov ~14:10 the app window on Dhivakar's Mac (macOS 26.6.2) went blank grey.
#  Cause: macOS 26 WebKit leaks an ICU TimeZone (~20 KB) on every
#  Date#toLocale*String / new Intl.DateTimeFormat. The dashboard formats IST times
#  on every poll-driven render (~600 calls/s), so the web view grew ~30 MB a
#  minute to 8.2 GB and macOS killed it (heap: 9.85 M icu::TimeZone objects).
#  Fix: reuse date formatters (one per locale+options) for every call, with the
#  exact same text. Measured in JavaScriptCore on that Mac, 60,000 calls:
#  1.69 GB -> 32 MB. macOS 27 does not leak; nothing changes there.
#
#   frontend/src/utils/intlDateCache.js         NEW: the formatter cache, install()
#   frontend/src/index.js                       install it first, before the first render
#   frontend/scripts/test_intl_date_cache.mjs   NEW: TC-UI-INTL-0001…0006 (node)
#
#  Run ONLY this script:  cd ~/dev/scalp-app && python3 apply_intl_date_cache.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "INTL_DATE_CACHE_20261127"
ITEMS = json.loads(r'''[{"path": "frontend/src/utils/intlDateCache.js", "orig": null, "new": "f90fdef30f2b3e0251c399c7e570ae686517b4b301b4779a3810dce149ef4fb9", "exec": false, "blob": "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"}, {"path": "frontend/src/index.js", "orig": "bc8be0dd0784459f40ed8e9b8eb52a9ae6565861ab4c4eef90e44a2b12745663", "new": "49c2ca8eb68e85c6866a77f9285c5edc750347b103c60a456fdbe2cac6363a6c", "exec": false, "blob": "eNptUtFu2kAQfOcrVrzUSMEOVE2j0laCxElQ2yQCpD6iw17H15x96G6B0KhSP6Jf2C/pnEkgqiJZ1u3N7ni8M0lCf//8xkPj69nX+flwls7PhmdX6bx/3D/p9frvn/HoPL3o9nrvOnQxnkxnR7TgwjomVW+l1PUdoaqUeFKUK+EP5HjluZUkTfmECjtPUaWymyn1T+g7L75oIcPqPsyJrph+2prJ1sRrdluqeYOCByQlPrVcvvG04UVgXWtAdw4vsXRKlyMoyQHWQguj6vvAAfXXdt2JacpMK9HGJ7oWcw5BZyorOf7h45aultYJPZKuvShjSPnn4/hlM/2iwtmK2nHyClV78Ew0YZXJU6sL5wP0SBluhCfWyp6u6enmtkoyoyH/0D5cLvefxPkAoNZ1zg9x5v1LdizIbGclV3yE9at8KnAoby5eqJdQtwdElOzdn12l39L57dVwmvYa649P3/aewFZY9zBQNy4Y5aULa3NqiGiUXtxM0gYqtPOCL0ObI2+buw2U2g2MhJ+BqMB8yX6HOYvgLJVhJIM2pTagU9k9CEZY0eVKOViqNFKF/DQjix0cByr87txjFOnzsBKGR2utYLnoQmdKtK1vnV1rqOlAVaVga0Pi7cplSFlB4lZSNlwhPBruO+42awz5MVblpAtCRnNdFAgv4vJKNKLOoHXYffTf6qMO4FZmMUh4C2RgPZ8ot9mqguHxHUtqOBxH23EetR3+vY2Z3Uio0H1ITrQnQU9A493Go48hMMln3P4DUUpTiQ=="}, {"path": "frontend/scripts/test_intl_date_cache.mjs", "orig": null, "new": "2f6e625793bfe630040a8c8fff3fb0d54483178a9404771b69aa32440022e565", "exec": false, "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "frontend/scripts/test_intl_date_cache.mjs"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"frontend/src/index.js": ["THEME_PHASE1_20260831"]}
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
tmp = tempfile.mkdtemp(prefix="intl_date_cache_")
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
        if not os.path.exists(os.path.join(cwd, t)):
            continue
        try:
            r = subprocess.run((["node"] if t.endswith(".mjs") else [py]) + [t], cwd=cwd, env=env,
                               capture_output=True, text=True, timeout=900)
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
say("  App change (frontend): ships with the next release (./deploy-scalp.command, all 3 platforms).")
say("  Undo: python3 apply_intl_date_cache.py --rollback")
