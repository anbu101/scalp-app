#!/usr/bin/env python3
# =====================================================================
#  apply_unlisted_live_pnl.py  —  fence UNLISTED_LIVE_PNL_20261124  (on main dbe1a83c, v12.0.14)
#  DEF-101 · TD-086
#
#  24 Nov, Dhivakar's Mac: Dashboard "This month · Live" read −₹18,586.62
#  while the trades add up to −₹22,453.77 (same charges model). His licence
#  now lists IC_V2, TMA_V2 and VET_V1, and /api/fleet/today skipped every
#  strategy the licence does not list, so TSG_V1's real 17 Nov live trade
#  (4 legs, −₹3,867.15 net) vanished from the week/month figures. The
#  licence decides what may RUN, not what money was booked.
#
#   backend/app/api/fleet_today_routes.py        _unlisted_rows(): an unlisted
#        strategy with a LIVE leg closed in the month window gets one LIVE row,
#        always IDLE and flagged "unlisted" — only its week/month count (in
#        _totals); never a tile, an idle entry, a sample or a path. Paper
#        legs of an unlisted strategy stay out. The idle list skips it.
#   backend/app/api/test_fleet_today_routes.py   section 17, TC-API-0324…0333
#  Build copies under desktop/src-tauri/ are written too when present.
#  Every edited file gets a .bak-UNLISTED_LIVE_PNL_20261124 copy.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_unlisted_live_pnl.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh backend, release.
# =====================================================================
ITEMS = [
    {"path": "backend/app/api/fleet_today_routes.py",
     "orig": "6dbe9975e216a92d5b206bad5f6c26137c717f1a6dfaf12d629f0b94e0fa85f2",
     "new": "d0b33da7c9ec0a3db023e16b17dc911476afd7467429cc2624b3e35b2c7f9a25",
     "blob": "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"},
    {"path": "backend/app/api/test_fleet_today_routes.py",
     "orig": "aaa5d2605be5a5e37de0087db438a0341f4451dde8ab41b20ae4883cb51a8cba",
     "new": "7039e21298081f5d60fee2431caf3e003e1004b5e1a1626131050ee4804d9b06",
     "blob": "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"},
]
import base64, hashlib, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "UNLISTED_LIVE_PNL_20261124"
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/"}
PARENTS = {"backend/app/api/fleet_today_routes.py": ["FLEET_KPIS_PERIOD_20260928", "INDEX_PATH_20261117"]}
TESTS = ["backend/app/api/test_fleet_today_routes.py", "backend/app/tests/test_eod_card_v2.py",
         "backend/app/api/test_fleet_fresh_marks.py", "backend/app/api/test_fleet_ready.py",
         "backend/app/api/test_strategy_visibility.py", "backend/app/api/test_index_path.py",
         "backend/app/recon/test_recon_value.py"]


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


def refresh_master():
    """scripts/regression/MASTER_SUITE.md lists every suite's hash + TC IDs."""
    ms = os.path.join(repo, "scripts", "regression", "master_suite.py")
    if not os.path.exists(ms):
        return
    r = subprocess.run([sys.executable, ms, "refresh"], cwd=repo, capture_output=True, text=True, timeout=300)
    msg = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    say("  " + (msg[-1] if msg else "MASTER_SUITE.md refreshed") if r.returncode == 0
        else "  !! MASTER_SUITE.md refresh failed — pre-commit will refresh it")


def rd(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


# ── rollback ─────────────────────────────────────────────────────────
if "--rollback" in args:
    later = [os.path.relpath(p, repo) for it, p, build in targets()
             if not build and os.path.exists(p + ".bak-" + FENCE) and sha(rd(p) or b"") != it["new"]]
    if later:
        die("cannot roll back — changed again after this script: " + ", ".join(later)
            + ". Roll the later fixes back first, newest first.")
    n = 0
    for it, p, _ in targets():
        bak = p + ".bak-" + FENCE
        if os.path.exists(bak):
            shutil.copy2(bak, p)
            os.remove(bak)
            n += 1
    refresh_master()
    say(f"rolled back {n} file(s). Then: ./desktop/build-scalp.sh backend")
    sys.exit(0)

# ── 1. state of every target ─────────────────────────────────────────
plan, done, skipped, later = [], 0, [], []
_applied = any(not b and FENCE.encode() in (rd(p) or b"") for _, p, b in targets())
for it, p, build in targets():
    cur = rd(p)
    cs = sha(cur) if cur is not None else None
    rel = os.path.relpath(p, repo)
    if cs == it["new"]:
        done += 1
    elif cs == it["orig"]:
        plan.append((it, p, cur, build))
    elif build:
        if cur is not None:
            skipped.append(rel)           # stale build copy — the build re-syncs it
    elif cur is None:
        die(f"{rel} is missing")
    elif _applied:
        later.append(rel)
    else:
        die(f"{rel} is not the version this script was built on (main dbe1a83c) — "
            "pull the latest main and ask for a rebuilt script")
if not plan:
    say(f"✓ already applied ({done} file(s) in place"
        + (f"; {len(later)} changed again by later fixes" if later else "") + ") — nothing to do")
    sys.exit(0)
if later:
    die("partly applied: " + ", ".join(later) + " changed after this script while other files are not "
        "patched — check git status first")
for rel, fences in PARENTS.items():
    miss = [f for f in fences if f.encode() not in (rd(os.path.join(repo, rel)) or b"")]
    if miss:
        die(f"{rel} lacks {', '.join(miss)} — pull the latest main first")

# ── 2. git status: an uncommitted change on a target is a stop sign ──
if "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for _, p, _, b in plan if not b] + ["scripts/regression/MASTER_SUITE.md"]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        dirty = [ln for ln in st.splitlines() if ln[:2].strip() and not ln.startswith("??")]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty)
                + "\n  (commit/stash them, or --allow-dirty)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="unlisted_live_pnl_")
blobs = {}
try:
    for it in ITEMS:
        b = zlib.decompress(base64.b64decode(it["blob"]))
        if sha(b) != it["new"]:
            die(f"payload for {it['path']} is corrupt")
        blobs[it["path"]] = b
        f = os.path.join(tmp, os.path.basename(it["path"]))
        with open(f, "wb") as h:
            h.write(b)
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
say(f"payloads verified and compiled ({len(ITEMS)} files)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []


def restore():
    for p, old in reversed(written):
        try:
            with open(p, "wb") as h:
                h.write(old)
            bak = p + ".bak-" + FENCE
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        bak = p + ".bak-" + FENCE
        if not os.path.exists(bak):
            shutil.copy2(p, bak)
        t = p + ".tmp-" + FENCE
        with open(t, "wb") as h:
            h.write(blobs[it["path"]])
        shutil.copymode(p, t)
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")
refresh_master()

# ── 5. tests (scripts/run_suites.py: right runner per suite, throw-away HOME) ──
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    def _pytest_direct(rel):
        """run_suites runs pytest files with backend/venv's Python; when that
        venv has no pytest, run the file with this Python instead, in a
        throw-away HOME (never the live ~/.scalp-app)."""
        import importlib.util
        if importlib.util.find_spec("pytest") is None:
            return None, "pytest is not installed for " + sys.executable
        home = tempfile.mkdtemp(prefix="ulp_home_")
        try:
            env = {k: v for k, v in os.environ.items()
                   if k not in ("SCALP_APP_HOME", "DB_PATH", "SCALP_DATA_DIR", "SCALP_LOG_DIR", "SCALP_STATE_DIR")}
            env.update(HOME=home, USERPROFILE=home, SCALP_TEST_MODE="1",
                       PYTHONPATH=os.pathsep.join([os.path.join(repo, "backend"), "."]))
            full = os.path.join(repo, rel)
            rr = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                 os.path.basename(full)], cwd=os.path.dirname(full), env=env,
                                capture_output=True, text=True, timeout=900)
            return rr.returncode, ((rr.stdout or "") + (rr.stderr or "")).strip()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    failed, envmiss = [], []
    for t in TESTS:
        r = subprocess.run([sys.executable, os.path.join(repo, "scripts", "run_suites.py"), t],
                           cwd=repo, capture_output=True, text=True, timeout=1800)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0 and "No module named pytest" in out:
            rc, out2 = _pytest_direct(t)
            last = (out2.splitlines() or [""])[-1]
            if rc is None:
                envmiss.append(t)
                say(f"  ? {t}: not runnable here ({out2})")
            elif rc == 0:
                say(f"  ✓ {t}  {last}  (run with {sys.executable}; the backend venv has no pytest)")
            else:
                failed.append(t)
                say(f"  ✗ {t}\n" + "\n".join("      " + ln for ln in out2.splitlines()[-25:]))
            continue
        last = (out.splitlines() or [""])[-1]
        if r.returncode == 0:
            say(f"  ✓ {t}  {last}")
        else:
            failed.append(t)
            say(f"  ✗ {t}\n" + "\n".join("      " + ln for ln in out.splitlines()[-25:]))
    if failed:
        restore()
        refresh_master()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: " + ", ".join(envmiss) + " could not run here (no pytest) — run them where pytest is installed")

say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh backend, then release.")
say("  Undo: python3 apply_unlisted_live_pnl.py --rollback")
