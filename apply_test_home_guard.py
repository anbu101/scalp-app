#!/usr/bin/env python3
# =====================================================================
#  apply_test_home_guard.py  —  fence TEST_HOME_GUARD_20261127   (on main fae7a63e, v12.0.23)
#  DEF-113
#
#  Zerodha login failed with "Invalid `api_key`" and the key + secret had to
#  be set up again. Not the dead battery: apply scripts ran their tests with
#  the REAL HOME, and test_tg_personal_alerts.py / test_kite_login_nudge.py
#  write a fake key into ~/.scalp-app/zerodha/credentials.json (or delete it)
#  and a fake Telegram config into ~/.scalp-app/telegram_config.json. The
#  running app keeps its token, so it showed only at the next Kite login.
#
#   backend/app/__init__.py              a test run (SCALP_TEST_MODE=1, pytest, test_*.py)
#        whose HOME is the account's real home gets a throw-away HOME before any
#        backend module works out a path. Never the packaged app (sys.frozen),
#        never a HOME that is already isolated (run_suites, mutation runners).
#   backend/app/config/zerodha_credentials_store.py   save_credentials is all or
#        nothing (temp file + fsync + replace; the file ends up owner-only, 0600)
#   backend/app/utils/test_test_home_guard.py   NEW — TC-UTIL-0037…0044
#   scripts/checklist/items.py           CL-22: an apply script that runs tests must
#        point their HOME at a temp folder
#   scripts/test_reliability_checklist.py   TC-GATE-0077…0079; 22 items
#  This script's own tests run with HOME = a temp folder.
#  Build copies under desktop/src-tauri/ are written too when present.
#  Every edited file gets a .bak-TEST_HOME_GUARD_20261127 copy.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_test_home_guard.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "TEST_HOME_GUARD_20261127"
ITEMS = json.loads(r'''[{"path": "backend/app/__init__.py", "orig": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "new": "e9d10d93173922f78a654d44399454b4ffece54a49110f38442c545d6ea1c314", "blob": "eNqFVuuO2kYU/s9THHl/1G5Zb3ZbtSoVlWhgk1WTgHZJKyWKrAEPMMXMWDNjvKRqlYfIM/TB8iQ95wwXE0iLVouZy7l8lxlfwERMl1LnV6Isr7JMaeWzLC038OnDR/j08QP+wXjwMM6eD18Osmeve/f97ObJzffX1zc/7Obj/uD28vr626R10bqAHnjpPNhKg5ZracFKkTswFmqrcAr8QtJYAX9fpW4qivISc6e898GsJLiKl/FqmImlhDfSmnwhYCk3Dq5AhNGxLOTcihVMjZ6pOSjtDQfHcBhrZoocs8dUTebnWSmtM1oUmSik9Q57bHOl2RLzZIWZK53pKp9Lnvn04Z8khfFCbqCWVmI8J2YSjC5wYCE1J8LiMRaUBlPLHAgiEB7r83JVbgv4iZDIQk+Ea26kw2iM2ZMfv0vaVC4GdVOrSu9A6BwW9A+3OchVDtr4Ng/vgMPtTeiu3gd4rqZW5lJ7JQqX/oG9wkROBQL6ZyRKlSF4UQei5XXE3f3V4XhaPnqM9ythzRggtqrAZmrlFxDd6bUosIZdgAPXgbABErzZaQhWJq8KjKPWyF+lCX6/UA5KnBdz2QZnwgD3NpEzY5EtvQEzw1hYzgpqY5colopQLIVfpDDUDOhWUvXCOBmQxjjOq6IInE+nptL+K7dDaIFSaoeFMYH3+mFwP7of3t69GCRY6FoCqgUDL6ypL0UtNlu+uMgRZaYIcULIYzjJjVJFMKlU4VEJoHygngqwMgACzWKhCw9Pey9GGTvo5bA/6F63odzwgsKIXOZtMobYsg8a6cqDKr9GsXC8YCKqOMd41OsWTmKlhHi0udPOsxI74DYunVnzXupkG5kR8AuUpXIc7/gjCrLnhkR2MKbREuKDbNFyq8oLr7BnHNVoJHLhmWi8HjkwtQ4e4OxYHfpWOAJMuXaQVs0leXASRZsnLbUqjUVYeWFm3G7Aha0ZfrdaWW80yka98fPst979A8IRRwFgGqdcqO2o/wsvoccw2e+Ne1n/7v4w8mL47HjgYdwbD8JQ66Sr3aKnw1e3d59tfDO4H/afh/BJq9XK5Qwy5TLmEMFCAV3+DBNjig4HVjNqLpV6razR6Vz6XQt7jUQJdLsQXUedfSlW+spqGNtK8thKoFG7HIlNMhFOknZigikVdr5+++RdQskwgfDe8gQWTVNY/CtkOAFZoJWiqNXIEAVxRniYMuRpsDQf35Q0RS7x8CQG44h7PHRN0smCaULlURSNj6xJsqAFe6uRCFjeQSckk5hqo8pRG1OhSZcT8mqRJ2m0LdbbzQGbrU7KOv8crj08VBk9xLiIIC/rSuUxTeMPekyStKyzXNmEY8jHqUQ3DvgLVX9CBNW4a5saIsYpSUw/ThmnJmjmy4QyKt0zFXPALUPozO4RzLv4PIXHIeWh0k7yhPDdcDY2QCOPzvC4h///XMDvSuemdh1MQ2jzG8LRVQfMc3C6MwVSGyjxuOJcb7vsxAI9I/7IRFOO1FC8K57CoAzpZ1OGNPwNR3eyTPZqnFfC5kdyPHVDOCnRD7d4ZSJvGJ0yHvv3PPmfkXbk58ZVEyU7x5M92dca3cXOO9nHB1jSFM0ZcX25nC2nCPuj8s0Rt6jwnjzH+5GwzmuPSuFGm6dFYHTP32qZ03NcWjlTj90ovJoQhJe09XLbVKPht6HZd8C0ns42IWwuwjcGWPLhdHQTdP5Lt+FF4vB6yP3S1blVbW2qIofJphTO8UG0R3h9ht1lcjh5ZriCX9U4Ij6cYrhOmlrlhQexdo7ummam0pTxcntOn6LTuDBOYAz0YwFz5by0cSA/tStvJb4Q4bI2HztHPqPNeLs2PdP6F5z/0IY="}, {"path": "backend/app/config/zerodha_credentials_store.py", "orig": "d36188f69ca80077c4ef3742a14e23a93e03688d1270f4914c589a16757fce90", "new": "28b2b334a3afe96d78cbf4a5b956d27a29a70608a382aa0e8cb88c1a6f2223ae", "blob": "eNqlVe1q8zYU/u+rOLh/bOY4awcbBDIwjfu2o4uDk26wMYxqy40WWxKS3DQbG7uIXeGuZEeOncZJX9jaEBJJ5zlfj845uoBHkm8oL8ZEynEueMmexr9RJYo1yXJFC8oNI5XOtBGKhnLnOKyWQhn4VQver4XuV4bWsmQVdUolapDErCv2CJ1wgdu9oCCGGlbTXtLv91Kzk4w/9bJEGiY4qfYyDDOkzxhV9tjokDQFM1klnp6o6vFbxQzNDpJXtcawClWkzGxcusdHi0V2m3wfB0C5bhSqIqJgSjuOcwHT//1BpeXd/NN9DMvkIb2OIbmBVfqwugXvYRmnYH35CPpzHOqcVHJkme8oHx9RHrYMvysC5yQTz3ecn+I0md1G2ewuhekhaRiD2/l2jyFhvUFFTxKF0ejpSjUU+Xlh2mRi027R5HUaz+L56i66X2aLCBOcwrEXNH2ajvt+Tu+TaPZONgpaQiVIcVzQng+jbw+19XPBcvPLxAH8XEjFuPHcnqKJGxzo8geI0/wt8vRsqBFbBvVbuLAlF8NCXq0GK4EL83nYPtZj2zfYdlAIqlvFFuf6B5SiplEc5oLTvQOjdq82FNni5Z05UxRJM/TFeP6ZuzT6EW7usMqvk/kKtWxSaOYViD1N0Ki99tCyr72BuLeziNJlPINZtIqsCat1FrU9bM/oS06lgbj9w5sDooGeU/HdMplDnKZJak3Sz7Dwgf6Ofog/UIuaPNNBLRLJsg3dTUAbFYDdaYpy0x50V93R+fshF7fTcifQrUJEM+n5wRCzt9XB9ps3kI20Q7jIiEX28xhnZs7F1vNDpkUpVE1Mr/THvowu4J+//8IvrOLlqu2R7NNDlM6yqy+vvr68vPqml3uz+GZ0efmVD6SqQChbpWsc8xgW5IrotT2TYouDPG9MZ7tmxciyBRXFXw1mTUFUBdgnJgCOD4FC9TWpypEd+4ZywKsNW+2yCMDUEjnrHyUcatquPRxtU2TAOyv4/bzzA5CKluxlegbgBB+tL8ANu9YadNGWGcxCh2UhJOWeDcDdur4t0vIV1cYWtq+U1zZH0dRSe/Z+A2DcFsX0yvdP8GXV6LU3PLWu9I7nHooxPS68IzUUKiorklMPWQjgrXw7eMlwBFZHieDwQXX7SvbzBk34wxQGmQ+c1uK59TkMtmvdZBkrJdS5qiT6Ay/u9X0cpR9oyRwLTA3fh0k/h//DDD6DNLxifIP39S+2ObFC"}, {"path": "backend/app/utils/test_test_home_guard.py", "orig": null, "new": "a105f0a7780eaf99e472110318603ad3ab88b299e11d9a89d984e1cc8497f2ec", "blob": "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"}, {"path": "scripts/checklist/items.py", "orig": "8abcc2921a305858d3df11ab5af039af8e686dddb0b0cab1c905bdb975176136", "new": "d63d8e597d7a7dc67e5c58fca0052c162f578effef568d61d0338e942bc2d90a", "blob": "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"}, {"path": "scripts/test_reliability_checklist.py", "orig": "ba4e39b5b97d6ae7fbea36d513a40960e56755d0ce5becb8b0549bdd07839c50", "new": "1c51cdf759158677537ac0cb3c5d1bc257e47e08fd7dc684073f5edc1d6b75b6", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["backend", "app/utils/test_test_home_guard.py"], ["backend", "app/jobs/test_tg_personal_alerts.py"], ["backend", "app/jobs/test_kite_login_nudge.py"], ["root", "scripts/test_reliability_checklist.py"]]


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
PARENTS = {"scripts/checklist/items.py": ["RELEASE_STAMP_GUARD_20261127"], "scripts/test_reliability_checklist.py": ["RELEASE_STAMP_GUARD_20261127"]}
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
tmp = tempfile.mkdtemp(prefix="test_home_guard_")
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
    _home = tempfile.mkdtemp(prefix="apply_home_")   # DEF-113: tests never get the real HOME (CL-22)
    env["HOME"] = _home
    env["USERPROFILE"] = _home
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
if "--skip-tests" not in args:
    shutil.rmtree(_home, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Next: commit, then release with ./deploy-scalp.command (backend/app/__init__.py and the")
say("  credentials store are in the app; the packaged app is never redirected).")
say("  Undo: python3 apply_test_home_guard.py --rollback")
