#!/usr/bin/env python3
# =====================================================================
#  apply_watchdog_restart.py  —  fence WATCHDOG_RESTART_20261126   (on main 7dd4dd1f, v12.0.18)
#  DEF-029 · TD-094
#
#  The desktop watchdog could never restart a crashed backend:
#   1. start_backend() set BACKEND_STARTING on every start and cleared it only
#      on its early returns — never after a successful spawn, nor when the
#      backend binary was not found — so every later call said "already
#      starting" and returned.
#   2. BACKEND_PROCESS kept the handle of a backend that had crashed, so the
#      "process already exists" check refused the restart too.
#  Now a guard clears the flag on every return path, and the handle of an
#  exited backend is reaped first. A backend that is still running is never
#  killed or doubled (a slow boot is not a crash).
#  The watchdog is also PAUSED once the backend is stopped for an update
#  install (and on window close / destroy), so it cannot restart the backend
#  while the installer replaces its files. The pause lasts for the rest of the
#  process: a good install relaunches the app; a failed one already tells the
#  user to quit and reopen Scalp before trading.
#
#   desktop/src-tauri/src/runtime.rs   StartingGuard, reap_exited, pause_watchdog, watchdog_step
#                                      + Rust unit tests TC-DESK-0001…0006
#   desktop/src-tauri/src/main.rs      pause before stop_backend_for_update / on close / on destroy
#   scripts/test_desktop_watchdog.py   NEW — TC-DESK-0007…0010 (source wiring; --cargo runs the Rust tests)
#  Every edited file gets a .bak-WATCHDOG_RESTART_20261126 copy.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_watchdog_restart.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback  --cargo (also run the Rust tests)
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "WATCHDOG_RESTART_20261126"
ITEMS = json.loads(r'''[{"path": "desktop/src-tauri/src/runtime.rs", "orig": "3cb70d50fddfd0bb148a512cb534e13960d019805e95bb659d01e16e42c8f157", "new": "da16bf652dc895b3159a7edd54c8462eece26ba80b65381c3c37fec4a245653b", "blob": "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"}, {"path": "desktop/src-tauri/src/main.rs", "orig": "b08905b8b0a6555e080cbd1a26bfd6407971a9c0e2a8aa9df8ab643fd0b8824e", "new": "9a6bc4d26b01cdf0999f7be3af36370b6919deaa7e83a2a149f7abf6ecdf977a", "blob": "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"}, {"path": "scripts/test_desktop_watchdog.py", "orig": null, "new": "a043218b8599d2c3b8d63d5d5862f7681578d070155ad87e160fdc6752c77643", "blob": "eNqNVutu00gU/p+nOGukXXtxXKegAoEglTYBBKKoDeJHqczEniSzccbe8bhpVFXiIXiGfTCeZL+ZsZO2lEuVJjP2uV++c+79sVNXamci5A6X51Su9byQDzr3qEqVKHW1o3mlk4xXC12UyYrpdJ4Vs6hc07cvX+nb1y/40Mf98cGrw6OXyfHwZLx/PE524929Xm93ryXwD4ejbrz7JKTxYTd+8jCg8UH3cHjyphvH8aNvX/6L417cuQe14zmnRhu12mhZV5omnNgk56QLUrCJKU2MUsWqOc9owtIFl1lITGaOHBRryGMrtqbVXICRSarLjGlOQuJtnleRVTfhc3YuilqRqKiWQneNzxAqJB1DFKT4jUk7lUq7mtVKmNOOqqUWSx6pKrzuUM86FO/16XPK1KwgI+9z8JT0XFQmtDrLxYSqWsCWEsbgBaeVUELOjFJzq2BPan7cO84X+Rp+z+B6JQpJFecVMQhT/JwjFCuBxNUmJMZkBKnI0zkTMqKPeEPdrrNEgCKHUFhutBYVb+hhYRVBHLUl8OsCOG2EnnXEsixgQ1G1J8XbUzWvtcg3t3pSqiKFD5sn66rTOT46GtMA/FHJ9DzKhJJsyf0f3dmkMr9+kkyR1yQJgqBzcnxwTcI/hZC+kRqS15ju4bjJXnPxgs4IXHGn08n4lNL5wtdpSMtqFlKxCJGLaTHwvKDfQVxolhcTltPIXkokS/u+RyDE1SMxNSeeI6J4ONp//Za8gO7T1LvU6RVdQuiVh7vv3aCdgrj7nC6NqisPjhjhI7oPq66T9WAiogUPSy79G27Cc3izLUUvCInLtMhQTQOv1tPuYy+IFGeZH3SW8icylqZefiGgMymyNWQofap0JGTGL3yvrCc0lWSbMmla0Q+MX8e8qnP9zIfIE23qG64innewori2nKA565h0eNdxwmbtlg7NFtz1j4OEac5muKqins2hEo+g9GXNVEaFRAv5sgBYKENeKL6TFssSt4RfoFvkDLmzCfD0nJleUlzXSpIJFKXAj2ohysCBDOJRVgZT+IXQWwQCmkwh2Fr0F8tN1NaGBO31FwqMpwsvdCpuGNfvs/TfWijuB56BABtlo8Z7sX/wZvjuMLHA+vrdy8ga7pEsdEto5Vni2+7cIHMkxu7E2ez/uQRiNA2ZzIwdW+2h/T7tP4zjM6R9lf1O0jf44HLY/wH1TarvM/3YZNriXjsB8gLzwATT5brB8ZLVFbduZTwVmS0El/mWMQGQl4hpE/PNoHq//+FkeBjlBcusx3DPRucmn5VvhkouznlIfzOt+RKYGDQ8If43EYJNCNFSni5l6zSr1jK9Xd4J6iNxDsCwoL8lB6FpQfMUmJpY7QChxHBDNHiiKUj9ttn7fUuyDafp3DuobjYXQO92vJ+4zrrTRhfkZkS16XgxHB0dD800MVzuZcP6FKNIZsWK0rxoktP0FPKjVbFusibskILimJ4NqPGWnrXu2jkuo7SAEz9zmJ4P6EFI/q1wAUg7dqaZquU2IBj4vjIxBsbbeZZ8yi4fXgUJbFD6u6D04rYI7YA0e4GbkndNejKIgpK4hsKu4hobBnQ69Vq1l7IfP8wwDBBkkpbN9KrfC+lRcBY6HtgP+PeaEWvrDaMywuXcTSM3zwfNgI2w4qRz33PUboqA33S/feR4toMLU8dgGW33E4uM7lpIu6mgGNO5kDxo5JlBtJWjjO7NOI/guX9q2U3UIM9Er9vFXmkOrLTzd9NdzQKXWFcdpfs2D7poYtPpg553Fm703fGXrrLBb098tEbKSsA5T7AnlbUejFXNTbQvNkekDu8GD/biONgoNlsVigi4m5njfXfkSm0obrdTWxXGgGsB/qH3KnJzBiOXm1qJXdfsoVMq4I9NPlSH5uu0+ziO+wZvmkR+km6r2H/7lowB46P3m32cDl4ND96c0Pv9E6Cd3TtGTkGze1yOruyq8gGLu1k+TImZ2eD3HK0lQyz+BxwL7P0="}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "scripts/test_desktop_watchdog.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"desktop/src-tauri/src/runtime.rs": ["TS_FUNNEL_SCOPE_20261017"], "desktop/src-tauri/src/main.rs": ["UPDATE_CONFIRM_FIX_20261116"]}
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
tmp = tempfile.mkdtemp(prefix="watchdog_restart_")
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
                and "No module named 'app" not in outp and os.path.realpath(sys.executable) != os.path.realpath(py)):
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

# ── 5a. Rust unit tests (opt-in: --cargo; compiles the desktop crate, minutes on a cold cache) ──
if "--cargo" in args and "--skip-tests" not in args:
    _cargo = shutil.which("cargo")
    if not _cargo:
        say("  (--cargo: no cargo on this machine — the release build compiles runtime.rs)")
    else:
        say("  cargo test watchdog_restart_tests (desktop/src-tauri) …")
        _r = subprocess.run([_cargo, "test", "--bin", "app", "watchdog_restart_tests", "--", "--test-threads=1"],
                            cwd=os.path.join(repo, "desktop", "src-tauri"), capture_output=True, text=True, timeout=3600)
        _o = (_r.stdout or "") + (_r.stderr or "")
        if _r.returncode != 0 or "6 passed" not in _o:
            restore()
            refresh_master()
            die("Rust watchdog tests failed — every file restored:\n" + "\n".join("      " + l for l in _o.strip().splitlines()[-30:]))
        say("  ✓ cargo test: 6 passed (TC-DESK-0001…0006)")

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
say("  Next: release with ./deploy-scalp.command (all three platforms) — the build compiles runtime.rs.")
say("  Rust tests by hand: cd desktop/src-tauri && cargo test --bin app watchdog_restart_tests -- --test-threads=1")
say("  Undo: python3 apply_watchdog_restart.py --rollback")
