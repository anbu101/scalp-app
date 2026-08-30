#!/usr/bin/env python3
# apply_vet_v1_live_wiring2_20260829.py
#
# ── VET_V1 LIVE WIRING, PART 2 ── the launch path + the safety surfaces
# ============================================================================
# PART 1 (apply_vet_v1_live_wiring_20260827.py) made VET_V1 exist: registry,
# config defaults, overnight exemption. THIS script makes it RUN and makes it
# KILLABLE and VISIBLE:
#
#   api_server.py     — router include, 15:25 cron (unique id
#                       vet_live_eod_squareoff), standalone loop launch under
#                       its own _boot_guard behind enabled+license gates
#   kill_switch.py    — "VET_V1" in KILL_STRATEGIES + _kill_vet adapter
#                       (static, TMA_V2 shape: works even when the loop never
#                       armed — reports "manager not running" honestly)
#   telegram_api.py   — VET_V1 in _ALL_STRATEGY_IDS (per-strategy toggles)
#   admin_ui.html     — VET_V1 in ALL_STRATEGIES (license server). A chip
#                       missing there is silently STRIPPED from a license on
#                       any save through that modal — the checklist's worst
#                       silent failure, which is why it ships in the same
#                       script as the launch path, not "later".
#
# NEW FILES (shipped separately, this script only WIRES them):
#   app/engine/vet/vet_selection_loop.py, app/api/vet_state_routes.py,
#   app/jobs/vet_live_eod.py, app/db/migrations/025_create_vet_trades.sql
#
# Idempotent, assert-anchored, staged compile, dual-tree aware.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_live_wiring2_20260829.py --dry-run
#   python3 apply_vet_v1_live_wiring2_20260829.py

import argparse
import os
import py_compile
import shutil
import sys
import tempfile

REPO = os.getcwd()
BE_TREES = [(os.path.join(REPO, "backend"), "backend"),
            (os.path.join(REPO, "desktop", "src-tauri", "backend"),
             "desktop-be")]

API = os.path.join("app", "api_server.py")
KILL = os.path.join("app", "execution", "kill_switch.py")
TG = os.path.join("app", "api", "telegram_api.py")
ADMIN = os.path.join(REPO, "license_server", "admin_ui.html")


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:90]}")


# ── api_server.py ───────────────────────────────────────────────────────
A_IMP_ROUTER = ("from app.api.tma2_state_routes import router as "
                "tma2_state_router     # ← NEW (TMA_V2)")
A_IMP_ROUTER_NEW = (A_IMP_ROUTER + "\n"
                    "from app.api.vet_state_routes import router as "
                    "vet_state_router       # ← NEW (VET_V1)")
A_IMP_JOB = "from app.jobs.tma2_live_eod import tma2_live_eod_job           # ← NEW (TMA_V2)"
A_IMP_JOB_NEW = (A_IMP_JOB + "\n"
                 "from app.jobs.vet_live_eod import vet_live_eod_job             "
                 "# ← NEW (VET_V1)")
A_IMP_LOOP = ("from app.engine.tma2.tma2_selection_loop import "
              "tma2_selection_loop  # ← NEW (TMA_V2)")
A_IMP_LOOP_NEW = (A_IMP_LOOP + "\n"
                  "from app.engine.vet.vet_selection_loop import "
                  "vet_selection_loop      # ← NEW (VET_V1)")
A_INC = "app.include_router(tma2_state_router)"
A_INC_NEW = A_INC + "\napp.include_router(vet_state_router)"

A_CRON_ANCHOR = "\n        # ── TMA_V2 END ──"
A_CRON_NEW = '''\n        # ── TMA_V2 END ──
        # ── VET_V1 BEGIN ── 15:25 safety net UNDER the coordinator's own
        # boundary exits (expiry 15:20, eod_square at exit_time). Positional
        # carries are a deliberate no-op inside the job itself; same-day
        # expiry always closes. Unique id — a reused id silently replaces
        # another strategy's cron (replace_existing=True).
        scheduler.add_job(
            vet_live_eod_job, trigger="cron", hour=15, minute=25,
            id="vet_live_eod_squareoff", replace_existing=True,
            day_of_week="mon-fri", timezone="Asia/Kolkata")
        # ── VET_V1 END ──'''

A_LAUNCH_ANCHOR = "\n    # ── TMA_V2 END ──"
A_LAUNCH_NEW = '''\n    # ── TMA_V2 END ──

    # ── VET_V1 BEGIN ── dual-EMA(10/20) + regime-channel trend following on
    # 5m NIFTY spot; parity-by-construction signals (the live engine re-runs
    # the backtest's vet_states over the growing day prefix, 10-session
    # warmup, prefix-stability guard that FREEZES on drift). One position at
    # a time; four sealed configs (buy/sell × intraday/positional) are all
    # Settings, not code. Ships mode=PAPER. No GTT layer exists (sl/tp are 0
    # by design), so the kill path is a plain flatten via the manager.
    # Own _boot_guard: a VET_V1 launch failure must never abort the launches
    # that follow it (v10.2.9 NameError incident).
    with _boot_guard("launch VET_V1"):
        if STRATEGIES.get("VET_V1", {}).get("enabled", False) and \\
                license_state.license_allows_strategy("VET_V1"):
            _supervise(asyncio.create_task(vet_selection_loop(zerodha_manager)), "vet_selection_loop")
            write_audit_log("[SYSTEM] VET_V1 standalone selection loop launched")
    # ── VET_V1 END ──'''


def edit_api(t):
    if "vet_selection_loop" in t:
        return t, 0
    for a, lbl in ((A_IMP_ROUTER, "router import"), (A_IMP_JOB, "job import"),
                   (A_IMP_LOOP, "loop import"), (A_INC, "include_router")):
        one(t, a, "api_server:" + lbl)
    # the cron anchor appears once (inside scheduler block, 8-space indent);
    # the launch anchor once (4-space indent) — they are DIFFERENT strings.
    one(t, A_CRON_ANCHOR, "api_server:cron anchor")
    one(t, A_LAUNCH_ANCHOR, "api_server:launch anchor")
    t = t.replace(A_IMP_ROUTER, A_IMP_ROUTER_NEW, 1)
    t = t.replace(A_IMP_JOB, A_IMP_JOB_NEW, 1)
    t = t.replace(A_IMP_LOOP, A_IMP_LOOP_NEW, 1)
    t = t.replace(A_INC, A_INC_NEW, 1)
    t = t.replace(A_CRON_ANCHOR, A_CRON_NEW, 1)
    t = t.replace(A_LAUNCH_ANCHOR, A_LAUNCH_NEW, 1)
    return t, 6


# ── kill_switch.py ──────────────────────────────────────────────────────
K_LIST_OLD = '''    "IC_V1", "IC_V2", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
    "TSG_V1",   # LD7: adapter registered by tsg_runtime at boot,'''
K_LIST_NEW = '''    "IC_V1", "IC_V2", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
    "VET_V1",   # static adapter below (works even if the loop never armed)
    "TSG_V1",   # LD7: adapter registered by tsg_runtime at boot,'''

K_ADAPTER_ANCHOR = '_ADAPTERS: Dict[str, Callable[[], dict]] = {'
K_ADAPTER_FN = '''def _kill_vet() -> dict:
    # ── VET_V1 ── one position, up to two legs (short + wing). manager.kill
    # closes SHORT FIRST then the wing (never leaves a naked short), and
    # FREEZES the manager against reopening. There is no GTT layer to race —
    # sl/tp are 0 by design — so a single flatten is the whole contract.
    from app.engine.vet.vet_selection_loop import get_manager
    m = get_manager()
    if m is None:
        return {"closed": 0, "remaining": 0, "detail": ["manager not running"]}
    had = 1 if getattr(m, "pos", None) else 0
    try:
        m.kill(int(time.time()))
    except Exception as ex:
        write_audit_log(f"[KILL][VET_V1][MGR_ERR] {ex!r}")
        return {"closed": 0, "remaining": had,
                "detail": [f"kill ERROR {ex!r}"]}
    still = 1 if getattr(m, "pos", None) else 0
    detail = ["manager frozen against re-entry"] if had else []
    return {"closed": had - still, "remaining": still, "detail": detail}


''' + K_ADAPTER_ANCHOR
K_MAP_OLD = '    "TMA_V2":    _kill_tma2,'
K_MAP_NEW = K_MAP_OLD + '\n    "VET_V1":    _kill_vet,'


def edit_kill(t):
    if "_kill_vet" in t:
        return t, 0
    one(t, K_LIST_OLD, "kill:strategies list")
    one(t, K_ADAPTER_ANCHOR, "kill:adapters dict")
    one(t, K_MAP_OLD, "kill:tma2 mapping")
    t = t.replace(K_LIST_OLD, K_LIST_NEW, 1)
    t = t.replace(K_ADAPTER_ANCHOR, K_ADAPTER_FN, 1)
    t = t.replace(K_MAP_OLD, K_MAP_NEW, 1)
    return t, 3


# ── telegram_api.py ─────────────────────────────────────────────────────
T_OLD = '''    "PST_SELL", "PST_HEDGE", "IC_V1", "IC_V2", "TMA_V1", "TMA_V2", "TSG_V1",
'''
T_NEW = '''    "PST_SELL", "PST_HEDGE", "IC_V1", "IC_V2", "TMA_V1", "TMA_V2", "TSG_V1",
    "VET_V1",   # ← VET_V1 (2026-08-29): per-strategy notification toggle
'''


def edit_tg(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, T_OLD, "telegram:_ALL_STRATEGY_IDS")
    return t.replace(T_OLD, T_NEW, 1), 1


# ── license_server/admin_ui.html ────────────────────────────────────────
L_OLD = ('"TMA_V1","TMA_V2","TSG_V1"]; // V2 removed;')
L_NEW = ('"TMA_V1","TMA_V2","TSG_V1","VET_V1"]; // V2 removed;')
L_TAIL_OLD = "MUST stay in sync with the app: a chip missing here is silently STRIPPED from a license on any save through this modal"
L_TAIL_NEW = (L_TAIL_OLD + " — VET_V1 added 2026-08-29")


def edit_admin(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, L_OLD, "admin_ui:ALL_STRATEGIES")
    one(t, L_TAIL_OLD, "admin_ui:comment tail")
    t = t.replace(L_OLD, L_NEW, 1)
    t = t.replace(L_TAIL_OLD, L_TAIL_NEW, 1)
    return t, 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in BE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped (rsync target)")
            continue
        for rel, fn in ((API, edit_api), (KILL, edit_kill), (TG, edit_tg)):
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")
    if os.path.isfile(ADMIN):
        out, n = edit_admin(open(ADMIN).read())
        if n == 0:
            notes.append("[license] SKIP (already wired): admin_ui.html")
        else:
            writes[ADMIN] = out
            notes.append("[license] EDIT: admin_ui.html")
    else:
        notes.append("[license] admin_ui.html NOT PRESENT — REMEMBER to add "
                     "VET_V1 to ALL_STRATEGIES on the droplet copy")
    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── STAGED COMPILE ───────────────────────────────────────────")
    tmp = tempfile.mkdtemp(prefix="vet_w2_")
    try:
        for i, (dest, body) in enumerate(writes.items()):
            if not dest.endswith(".py"):
                continue
            stage = os.path.join(tmp, f"s{i}.py")
            open(stage, "w").write(body)
            try:
                py_compile.compile(stage, doraise=True)
            except py_compile.PyCompileError as e:
                die(f"compile FAILED for {dest}:\n{e}")
        print("  all python targets compile clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. VET_V1 now launches (PAPER) behind enabled+license gates.")


if __name__ == "__main__":
    main()
