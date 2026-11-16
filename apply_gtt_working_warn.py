#!/usr/bin/env python3
# =====================================================================
#  apply_gtt_working_warn.py  —  fence GTT_WORKING_WARN_20261116
#  REQUIRES v11.8.2 or later. Independent of the other scripts.
#
#  DEF-036 (Low, Anbu's machine 16 Nov 09:15:01): Timberwolf's stop on
#  NIFTY26N1725050CE triggered at the gap-up open; one second later its order
#  was still OPEN PENDING (market above its limit) and the app sent a CRITICAL
#  "triggered but did NOT fill". The app took the order over, it filled at
#  251.05 at 09:15:07 and the hedge was sold at 09:15:09 — no problem.
#  Now: the stop's order still WORKING → the app takes it over at once, AS
#  BEFORE, but says so as a WARNING ("has not filled yet … re-pricing it").
#  The CRITICAL still comes from the exit itself if the buy-back cannot
#  complete, and stays CRITICAL when the stop's order is dead or missing.
#  Timberwolf (TMA_V2) and TMA_V1. Orders and timing unchanged.
#
#   engine/tma2/tma2_gtt_monitor.py           alert severity/wording
#   engine/tma/tma_gtt_monitor.py             same
#   engine/tma2/test_tma2_exit_paths.py       +4 checks (run for TMA_V2 and TMA_V1)
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_gtt_working_warn.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "GTT_WORKING_WARN_20261116"
ITEMS = json.loads(r'''[{"path": "backend/app/engine/tma/tma_gtt_monitor.py", "orig": "988a48a47ce62a60fdeb560b03091b822f66fb08f12744124494bfa9fc62fd9c", "new": "6c60757bd24d9d48bb61982613ff77a2d446579827ad6bbe73f8a0bdc58987b9", "blob": "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"}, {"path": "backend/app/engine/tma2/test_tma2_exit_paths.py", "orig": "d763479f6fbf31abf49411231dc36a9a87e77c24f584b666d5fa9fe09f72bf2c", "new": "9fd4fbc1968026db5ac17283da356f8f07d77e75f2b064a82400b42f3634f377", "blob": "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"}, {"path": "backend/app/engine/tma2/tma2_gtt_monitor.py", "orig": "4895535e3f06598eacbc8e9ba4d1bc72a4992204adc0ecbac4925c11c4dc07f1", "new": "aff4f3c9cf6cbee62d28127c4aa728af608b9a7f52060a825a45a2278c7f818c", "blob": "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"}]''')
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
         "app/trading/test_fleet_modes.py",
         "app/engine/test_ic_late_start.py",
         "app/db/test_close_conflict.py",
         "app/execution/test_exit_repeg_fast.py"
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
    _pf = os.path.join(repo, *['backend', 'app', 'engine', 'tma2', 'tma2_gtt_monitor.py'])
    if not os.path.exists(_pf) or 'GTT_MONITOR_PROOF_20261115' not in open(_pf, encoding="utf-8").read():
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
tmp = tempfile.mkdtemp(prefix="gtt_warn_")
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
say("  Undo: python3 apply_gtt_working_warn.py --rollback")
