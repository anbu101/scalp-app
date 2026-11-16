#!/usr/bin/env python3
# =====================================================================
#  apply_morning_alert_grace.py  —  fence MORNING_ALERT_GRACE_20261116
#  REQUIRES v11.8.2 or later (built on 11.8.6).
#
#  DEF-030 (16 Nov live, Icarus): "09:16 square-off NOT complete" CRITICAL
#  at 09:16:07 while the last wing was still closing — all three carried
#  legs were flat by 09:16:15. A false alarm on a correct square-off.
#  Now the CRITICAL is sent only after the close has stayed incomplete
#  for 30 s. ALERT ONLY: which orders go out, their order and their
#  timing are unchanged (the 2 s retry loop is untouched).
#
#   engine/ic/ic_engine.py                    alert waits 30 s
#   engine/ic/test_ic_engine_monitor_eod.py   +2 tests (fail on 11.8.2)
#     run: cd backend/app/engine/ic && python3 -m pytest -q test_ic_engine_monitor_eod.py
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_morning_alert_grace.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "MORNING_ALERT_GRACE_20261116"
ITEMS = json.loads(r'''[{"path": "backend/app/engine/ic/ic_engine.py", "orig": "28bbdb9be64d2c70c450e0cded71dc7173bc333c8baf80f497be7d1485bbd56a", "new": "6966f406ae3a99f5e5c92ce12c4928c264dae83185e7f3c690a1bcade68b261e", "blob": "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"}, {"path": "backend/app/engine/ic/test_ic_engine_monitor_eod.py", "orig": "3fb094465d65c21224146b507a3aebe15f3e9f675677dc660b277ef978eaec7f", "new": "94ff5ff00a0089f3ebace86fb0fde03b529236bebf675bdd061c88fe446a038a", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [         "app/execution/test_blind_stop.py",
         "app/engine/test_fleet_adversarial.py",
         "app/engine/test_fleet_live.py",
         "app/engine/test_order_path_budget.py",
         "app/execution/test_broker_truth.py",
         "app/execution/test_exit_latency.py",
         "app/engine/ic/test_ic_gtt_strict.py",
         "app/engine/tma2/test_tma2_e2e.py",
         "app/engine/tma2/test_tma2_exit_paths.py",
         "app/engine/tma2/test_tma_fill_truth.py",
         "app/engine/tma2/test_tma_gap_close.py",
         "app/engine/vet/test_vet_live_confirm.py",
         "app/engine/vet/test_vet_e2e.py",
         "app/execution/test_gtt_verify_strict.py",
         "app/execution/test_execution_router_contract.py",
         "app/engine/test_exit_once_restart.py",
         "app/engine/test_tigris_exit_restart.py",
         "app/engine/tsg/test_tsg_exit_confirm.py",
         "app/engine/tsg/test_tsg_e2e.py",
         "app/execution/test_instance_guard.py",
         "app/execution/test_order_once.py",
         "app/engine/test_entry_reservation.py",
         "app/execution/test_executor_pricing.py",
         "app/execution/test_order_latency.py",
         "app/execution/test_wing_fast_fill.py",
         "app/slippage/test_slip.py",
         "app/engine/tma2/test_tma2_entry_paths.py",
         "app/engine/vet/test_vet_manager.py",
         "app/engine/tsg/test_tsg_live_core.py",
         "app/engine/tsg/test_tsg_live_confirm.py",
         "app/engine/test_entry_fast_confirm.py",
         "app/services/test_relay_recovery.py",
         "app/engine/test_wing_reach.py",
         "app/engine/test_safety_layers.py",
         "app/execution/test_strict_reads_all.py",
         "app/engine/tma/test_tma_exit_paths.py",
         "app/engine/tma/test_tma_live_core.py",
         "app/trading/test_fleet_modes.py"
]


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

# ── prerequisite: v11.8.2
if "--rollback" not in args:
    _pf = os.path.join(repo, *['backend', 'app', 'engine', 'ic', 'ic_engine.py'])
    if not os.path.exists(_pf) or 'IC_CARRY_FLAG_20260814' not in open(_pf, encoding="utf-8").read():
        die("needs v11.8.2 — this script builds on it")


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
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="morning_grace_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
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
        if cur is not None:
            open(p + ".bak-" + FENCE, "wb").write(cur)
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")

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
    for t in TESTS:
        if not os.path.exists(os.path.join(backend, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
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
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: some suites need the backend's Python packages; re-run them with that Python")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.")
say("  Undo: python3 apply_morning_alert_grace.py --rollback")
