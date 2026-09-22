#!/usr/bin/env python3
# apply_ha_sell_removal.py — retire HA_SELL from the Backtest page entirely.
#
# Fence: HA_SELL_REMOVAL_20260922
# PREREQUISITES (asserted): revert_ha_cond1_flip.py and apply_ha_bt_form_trim.py
#   applied — HA_COND1_FLIP absent, HA_BT_FORM_TRIM_20260922 present in Backtest.jsx.
#
# Precedent: PST_REMOVAL_20260909. Same shape:
#   DELETED   backend/app/backtest/ha/backtest_ha_sell_runner.py
#   backend/app/api/backtest_routes.py   HA_SELL out of the /run/start allow-list
#                                        and message; the HA_SELL dispatch branch removed
#   backend/app/backtest/queue_worker.py HA_SELL dispatch branch removed; a stale queued
#                                        HA_SELL job now raises ValueError (fail LOUD,
#                                        never falls through to the generic SCALP run)
#   frontend/src/pages/Backtest.jsx      strategy card, saved-selection fallback list,
#                                        isHA / buildConfig ha → HA_V1 only, header text,
#                                        the HA_SELL-only Max SL cap field + max_sl_points
#                                        emission (HA_V1's runner never read it),
#                                        conditions-tab empty text
#   frontend/src/pages/backtest/RunComparison.jsx  HA_SELL filter chip removed
#   frontend/src/pages/backtest/Portfolio.jsx      HA_SELL out of LAUNCHABLE
#   frontend/src/pages/backtest/SweepBuilder.jsx   HAS const + every HAS axis entry
#                                                  removed; "Max SL cap" axis is now
#                                                  V1/V3 only (HA_V1 ignores it)
# KEPT on purpose (as PST_REMOVAL did): the STRAT_LABEL / colour entries in
# RunComparison, BacktestQueue, Portfolio and report_engine, so archived HA_SELL
# runs still list under ALL, label as "HAS" and plot. Live-side
# ha_trade_manager's op_name "HA_SELL_<reason>" is the HA_V1 exit-sell op, not
# this strategy — untouched. Comment-only mentions elsewhere left alone.
#
# GATES before any write: prerequisites; every anchor at its expected count;
# py_compile + pyflakes on both .py results; esbuild parse of the 4 JSX results;
# a 3-case functional check in a subprocess against the staged tree (queue
# dispatch raises for HA_SELL; route allow-list rejects it; nothing imports the
# deleted runner). Writes are all-or-nothing with .bak-FENCE backups; the
# runner is moved to backtest_ha_sell_runner.py.bak-FENCE (not destroyed);
# failed post-write verification restores everything.
#
# USAGE (repo root, non-trading hours):
#   python3 apply_ha_sell_removal.py --check
#   python3 apply_ha_sell_removal.py [--allow-dirty]
# Then: ./desktop/build-scalp.sh both

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tempfile, py_compile

FENCE = "HA_SELL_REMOVAL_20260922"
PARENT_FENCE = "HA_BT_FORM_TRIM_20260922"
ROOT = os.path.dirname(os.path.abspath(__file__))
RUNNER = "backend/app/backtest/ha/backtest_ha_sell_runner.py"
MIRROR_BACKEND = "desktop/src-tauri/backend"

EDITS = [
    ('backend/app/api/backtest_routes.py', 'routes: allow-list', 1,
     '"SCALP_V5", "HA_V1", "HA_SELL", "IC_V1"',
     '"SCALP_V5", "HA_V1", "IC_V1"'),
    ('backend/app/api/backtest_routes.py', 'routes: message', 1,
     'HA_V1, HA_SELL, IC_V1',
     'HA_V1, IC_V1'),
    ('backend/app/api/backtest_routes.py', 'routes: dispatch', 1,
     '                elif req.strategy_id == "HA_SELL":\n                    # HA_SELL: HA_V1 signal inverted to SHORT (option selling).\n                    # Same selected contract sold at entry, bought back to exit.\n                    # SL/TP roles swap for the seller: TP = HA SL level (below,\n                    # triggers on 1m close, books at close); SL = HA TP level\n                    # (above, triggers on 1m high, books at the SL level).\n                    # Charges on the sell/entry leg. Returns the standard shape.\n                    from app.utils.app_paths import APP_HOME\n                    from app.backtest.ha.backtest_ha_sell_runner import run_ha_sell_backtest\n                    db = APP_HOME / "backtest" / "backtest.db"\n                    has = run_ha_sell_backtest(\n                        db_path=str(db), strategy_id=req.strategy_id,\n                        underlying=req.underlying, date_from=df, date_to=dt,\n                        config_override=(req.config_override or {}), progress_cb=_cb,\n                        cancel_cb=lambda: _JOBS.run.get("cancel", False),\n                    )\n                    result = {\n                        "run_id": has["run_id"],\n                        "summary": has["summary"],\n                        "config": has.get("config", (req.config_override or {})),\n                        "trades": has["trades"],\n                        "strategy_id": req.strategy_id,\n                    }\n',
     '                # ── HA_SELL_REMOVAL_20260922 ── HA_SELL branch removed (strategy retired; runner deleted).\n'),
    ('backend/app/api/backtest_routes.py', 'routes: comment tag', 1,
     '    # pst_v1_engine signal stack is gone (pst_indicators stays: TMA/VET use it).\n',
     '    # pst_v1_engine signal stack is gone (pst_indicators stays: TMA/VET use it).\n    # ── HA_SELL_REMOVAL_20260922 ── HA_SELL retired (never used); backtest_ha_sell_runner.py deleted.\n'),
    ('backend/app/backtest/queue_worker.py', 'queue: dispatch', 1,
     '    if strategy_id == "HA_SELL":\n        # HA_SELL: HA_V1 signal inverted to SHORT (option selling). Same\n        # selected contract, sold at entry, bought back to exit. SL/TP roles\n        # swap (seller TP = HA SL level below; seller SL = HA TP level above);\n        # TP triggers on 1m close and books at close, SL triggers on 1m high\n        # and books at the SL level. Charges on the sell/entry leg.\n        from app.backtest.ha.backtest_ha_sell_runner import run_ha_sell_backtest\n        ha = run_ha_sell_backtest(db_path=str(db), strategy_id=strategy_id, underlying=underlying,\n                                  date_from=df, date_to=dt, config_override=(config or {}),\n                                  progress_cb=progress_cb, cancel_cb=cancel_cb)\n        return {"run_id": ha["run_id"], "summary": ha["summary"],\n                "config": ha.get("config", (config or {})), "trades": ha["trades"],\n                "strategy_id": strategy_id}\n\n',
     ''),
    ('backend/app/backtest/queue_worker.py', 'queue: fail-loud', 1,
     '    if strategy_id in ("PST_SELL", "PST_HEDGE"):   # PST_REMOVAL_20260909\n        raise ValueError(f"{strategy_id} retired (PST_REMOVAL_20260909) — no backtest runner")\n',
     '    if strategy_id in ("PST_SELL", "PST_HEDGE"):   # PST_REMOVAL_20260909\n        raise ValueError(f"{strategy_id} retired (PST_REMOVAL_20260909) — no backtest runner")\n    if strategy_id == "HA_SELL":   # HA_SELL_REMOVAL_20260922\n        raise ValueError(f"{strategy_id} retired (HA_SELL_REMOVAL_20260922) — no backtest runner")\n'),
    ('frontend/src/pages/Backtest.jsx', 'bt: saved-id list', 1,
     '"SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "TSG_V1", "GC_V1"',
     '"SCALP_V5", "HA_V1", "IC_V1", "IC_V2", "TSG_V1", "GC_V1"'),
    ('frontend/src/pages/Backtest.jsx', 'bt: isHA', 1,
     '  const isHA = strategyId === "HA_V1" || strategyId === "HA_SELL";\n',
     '  const isHA = strategyId === "HA_V1";   // ── HA_SELL_REMOVAL_20260922 ── HA_SELL retired\n'),
    ('frontend/src/pages/Backtest.jsx', 'bt: buildConfig ha', 1,
     '    const ha = sid === "HA_V1" || sid === "HA_SELL";\n',
     '    const ha = sid === "HA_V1";   // ── HA_SELL_REMOVAL_20260922 ──\n'),
    ('frontend/src/pages/Backtest.jsx', 'bt: max_sl emit', 1,
     '        // ── HA_BT_FORM_TRIM_20260922 ── max_sl_points is read by the HA Sell runner only; the HA_V1\n        // buy runner ignores it, so it is emitted for HA_SELL alone (no stale cap chip).\n        ...(sid === "HA_SELL" ? { max_sl_points: Number(maxSl) } : {}),\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: header', 1,
     '            ? `${strategyId === "HA_SELL" ? "HA SELL · NIFTY · option-SELLING (SHORT)" : "HA_V1 · NIFTY · option-BUYING (LONG)"} · Heikin Ashi · 1-minute candles · conds ${haConds.map((c) => c.replace("COND", "C")).join("+")}`\n',
     '            ? `HA_V1 · NIFTY · option-BUYING (LONG) · Heikin Ashi · 1-minute candles · conds ${haConds.map((c) => c.replace("COND", "C")).join("+")}`\n'),
    ('frontend/src/pages/Backtest.jsx', 'bt: card', 1,
     '          { id: "HA_SELL", label: "HA Sell", sub: "short" },\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: max sl field', 1,
     '              {/* ── HA_BT_FORM_TRIM_20260922 ── Max SL cap is a seller stop clamp: HA Sell only */}\n              {strategyId === "HA_SELL" && (\n                <Field label="Max SL cap"><input type="number" style={inputStyle} value={maxSl} onChange={(e) => setMaxSl(e.target.value)} /></Field>\n              )}\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: tab empty text', 1,
     'No entry-condition data — this tab applies to HA_V1 / HA_SELL runs',
     'No entry-condition data — this tab applies to HA_V1 runs'),
    ('frontend/src/pages/backtest/RunComparison.jsx', 'cmp: chip', 1,
     '{["ALL", "SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1"',
     '{["ALL", "SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "IC_V1"'),
    ('frontend/src/pages/backtest/Portfolio.jsx', 'portfolio: launchable', 1,
     'const LAUNCHABLE = ["SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1"',
     'const LAUNCHABLE = ["SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "IC_V1"'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: const', 1,
     'const HA = "HA_V1", HAS = "HA_SELL", IC = "IC_V1"',
     'const HA = "HA_V1", IC = "IC_V1"'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: premium', 1,
     'strategies: [V1, V3, V5, HA, HAS],',
     'strategies: [V1, V3, V5, HA],'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: rr', 1,
     '{ key: "rr", label: "Risk:Reward", strategies: [V1, V3, HA, HAS],',
     '{ key: "rr", label: "Risk:Reward", strategies: [V1, V3, HA],'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: min_sl', 1,
     '{ key: "min_sl", label: "Min SL pts", strategies: [V1, V3, HA, HAS],',
     '{ key: "min_sl", label: "Min SL pts", strategies: [V1, V3, HA],'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: max_sl', 1,
     '{ key: "max_sl", label: "Max SL cap", strategies: [V1, V3, HA, HAS],',
     '{ key: "max_sl", label: "Max SL cap", strategies: [V1, V3],   // ── HA_SELL_REMOVAL_20260922 ── HA_V1 runner ignores max_sl_points'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: HA-only axes', 4,
     'strategies: [HA, HAS],',
     'strategies: [HA],'),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: rr warning', 1,
     '(strategyId === HA || strategyId === HAS)',
     '(strategyId === HA)'),
]

PY_FILES = sorted({e[0] for e in EDITS if e[0].endswith(".py")})
JSX_FILES = sorted({e[0] for e in EDITS if e[0].endswith(".jsx")})

FUNC_TEST = r"""'''Post-removal checks against a checkout root (argv[1]):
  T1 queue_worker._dispatch_run_impl raises loudly for HA_SELL (no fall-through)
  T2 backtest_routes allow-list rejects HA_SELL (function-level, hand-built request)
  T3 runner file gone; no import of it anywhere in backend/
'''
import sys, types, os, re, glob
ROOT = sys.argv[1]; sys.path.insert(0, ROOT + "/backend")
def stub(name, **a):
    m = types.ModuleType(name); m.__dict__.update(a); m.__path__ = []; sys.modules[name] = m; return m
for p in ("app", "app.event_bus", "app.backtest", "app.backtest.repo"):
    stub(p)
sys.modules["app"].__path__ = [ROOT + "/backend/app"]
sys.modules["app.backtest"].__path__ = [ROOT + "/backend/app/backtest"]
stub("app.event_bus.audit_logger", write_audit_log=lambda m: None)
stub("app.backtest.repo.backtest_queue_repo")
import importlib
qw = importlib.import_module("app.backtest.queue_worker")
try:
    qw._dispatch_run_impl(strategy_id="HA_SELL", underlying="NIFTY", df="2026-01-01", dt="2026-01-02",
                          config={}, progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)
    raise SystemExit("T1 FAIL: HA_SELL did not raise")
except ValueError as err:
    assert "HA_SELL_REMOVAL_20260922" in str(err), err
    print("T1 PASS  queue dispatch for HA_SELL raises loudly:", err)

src = open(ROOT + "/backend/app/api/backtest_routes.py", encoding="utf-8").read()
m = re.search(r'if req\.strategy_id not in \((.*?)\):', src)
allowed = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
assert "HA_SELL" not in allowed and "HA_V1" in allowed, allowed
assert 'req.strategy_id == "HA_SELL"' not in src
print("T2 PASS  /run/start allow-list:", len(allowed), "strategies, HA_SELL absent, HA_V1 present")

assert not os.path.exists(ROOT + "/backend/app/backtest/ha/backtest_ha_sell_runner.py")
hits = [f for f in glob.glob(ROOT + "/backend/**/*.py", recursive=True)
        if "backtest_ha_sell_runner import" in open(f, encoding="utf-8", errors="replace").read()]
assert not hits, hits
print("T3 PASS  runner deleted, no backend import references it")
print("3/3 PASS")
"""


def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def apply_edits(src, path):
    for p, name, n, old, new in EDITS:
        if p != path:
            continue
        c = src.count(old)
        if c != n:
            fail(f"{path}: anchor [{name}] x{c}, expected x{n} — the file drifted from the "
                 f"expected state; inspect by hand")
        src = src.replace(old, new)
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry run, write nothing")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="proceed although git shows local edits on a target "
                         "(expected if the two earlier HA patches are not yet committed)")
    a = ap.parse_args()

    targets = sorted({e[0] for e in EDITS})
    done = os.path.join(ROOT, f".{FENCE}.done")
    if os.path.exists(done):
        print(f"  SKIP   {FENCE} already applied — nothing to do")
        return
    srcs = {}
    for t in targets:
        p = os.path.join(ROOT, t)
        if not os.path.exists(p):
            fail(f"{t} not found — run from the scalp-app repo root")
        srcs[t] = open(p, encoding="utf-8").read()
    bt = srcs["frontend/src/pages/Backtest.jsx"]
    if any(FENCE in s for s in srcs.values()) and not os.path.exists(os.path.join(ROOT, RUNNER)):
        print(f"  SKIP   {FENCE} already present and runner gone — already applied")
        return
    if "HA_COND1_FLIP" in bt:
        fail("HA_COND1_FLIP still in Backtest.jsx — run revert_ha_cond1_flip.py first")
    if PARENT_FENCE not in bt:
        fail(f"{PARENT_FENCE} not in Backtest.jsx — run apply_ha_bt_form_trim.py first")
    if not os.path.exists(os.path.join(ROOT, RUNNER)):
        fail(f"{RUNNER} not found — partial state; inspect by hand")

    try:
        r = subprocess.run(["git", "status", "--porcelain", "--"] + targets + [RUNNER], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        status = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        status = ""
    if status and not a.allow_dirty:
        fail("uncommitted edits on target files:\n         " + status.replace("\n", "\n         ")
             + "\n         commit the earlier HA patches first (preferred) or re-run with --allow-dirty")
    print("  OK     prerequisites verified, tree clean" + (" (dirty allowed)" if status else ""))

    staged = {t: apply_edits(s, t) for t, s in srcs.items()}
    print(f"  OK     {len(EDITS)} anchors verified in {len(targets)} files")

    stage = tempfile.mkdtemp(prefix="ha_sell_removal_")
    # Stage = a copy of backend/ + the 4 frontend files, with edits applied and
    # the runner removed, so the functional check sees the real end state.
    shutil.copytree(os.path.join(ROOT, "backend", "app"), os.path.join(stage, "backend", "app"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak-*", "dist", "build", "_internal"))
    os.remove(os.path.join(stage, RUNNER))
    for t, s in staged.items():
        sp = os.path.join(stage, t)
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(s)

    for t in PY_FILES:
        try:
            py_compile.compile(os.path.join(stage, t), doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"py_compile {t}: {e}")
    print("  OK     py_compile gate passed")
    try:
        import pyflakes  # noqa
        r = subprocess.run([sys.executable, "-m", "pyflakes"] + [os.path.join(stage, t) for t in PY_FILES],
                           capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() if "undefined name" in l]
        if bad:
            fail("pyflakes undefined names:\n         " + "\n         ".join(bad))
        print("  OK     pyflakes undefined-name gate passed")
    except ImportError:
        print("  WARN   pyflakes not installed — undefined-name gate skipped")

    tp = os.path.join(stage, "func_test.py")
    with open(tp, "w", encoding="utf-8") as f:
        f.write(FUNC_TEST)
    r = subprocess.run([sys.executable, tp, stage], capture_output=True, text=True)
    if r.returncode != 0 or "3/3 PASS" not in r.stdout:
        fail(f"functional check failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    for line in r.stdout.strip().splitlines():
        print("         " + line)
    print("  OK     functional check 3/3")

    esb, npx = shutil.which("esbuild"), shutil.which("npx")
    cmd = ([esb] if esb else [npx, "--yes", "esbuild"] if npx else None)
    if cmd is None:
        print("  WARN   esbuild unavailable — JSX gate skipped")
    else:
        for t in JSX_FILES:
            g = subprocess.run(cmd + ["--loader:.jsx=jsx", os.path.join(stage, t), "--outfile=" + os.devnull],
                               capture_output=True, text=True, cwd=os.path.join(ROOT, "frontend"))
            if g.returncode != 0:
                fail(f"esbuild gate {t}:\n{g.stderr[-2000:]}")
        print(f"  OK     esbuild JSX gate passed ({len(JSX_FILES)} files)")

    if a.check:
        for t in targets:
            print(f"  WOULD  edit   {t}")
        print(f"  WOULD  remove {RUNNER}  (kept as .bak-{FENCE})")
        print("  CHECK  dry run complete — no files written")
        return

    written = []
    runner_p = os.path.join(ROOT, RUNNER)
    try:
        for t in targets:
            p = os.path.join(ROOT, t)
            shutil.copy2(p, p + f".bak-{FENCE}")
            with open(p, "w", encoding="utf-8") as f:
                f.write(staged[t])
            written.append(p)
            print(f"  WROTE  {t}")
        shutil.move(runner_p, runner_p + f".bak-{FENCE}")
        print(f"  MOVED  {RUNNER} → .bak-{FENCE}")
        for t in targets:
            if open(os.path.join(ROOT, t), encoding="utf-8").read() != staged[t]:
                raise RuntimeError(f"post-write verification failed on {t}")
        if os.path.exists(runner_p):
            raise RuntimeError("runner still present after move")
    except Exception as e:
        for p in written:
            shutil.copy2(p + f".bak-{FENCE}", p)
        if os.path.exists(runner_p + f".bak-{FENCE}") and not os.path.exists(runner_p):
            shutil.move(runner_p + f".bak-{FENCE}", runner_p)
        fail(f"{e} — everything restored from backup")

    # gitignored mirror (rsync'd by build-scalp.sh; kept in step best-effort)
    for t in PY_FILES:
        mp = os.path.join(ROOT, MIRROR_BACKEND, t[len("backend/"):])
        if os.path.exists(mp):
            ms = open(mp, encoding="utf-8").read()
            if all(ms.count(o) == n for p, _, n, o, _ in EDITS if p == t):
                shutil.copy2(mp, mp + f".bak-{FENCE}")
                with open(mp, "w", encoding="utf-8") as f:
                    f.write(apply_edits(ms, t))
                print(f"  WROTE  mirror {t}")
            else:
                print(f"  NOTE   mirror {t} not patched (anchors differ) — build-scalp.sh re-syncs it")
    mr = os.path.join(ROOT, MIRROR_BACKEND, RUNNER[len("backend/"):])
    if os.path.exists(mr):
        shutil.move(mr, mr + f".bak-{FENCE}")
        print("  MOVED  mirror runner → .bak")

    with open(done, "w") as f:
        f.write(FENCE + "\n")
    print()
    print("  DONE   HA_SELL retired from the Backtest page.")
    print("         Rebuild: ./desktop/build-scalp.sh both")
    print("         Backtest → strategy strip: 'HA Sell' card gone; Compare Runs / Portfolio: no HA_SELL")
    print("         chip; archived HA_SELL runs still list under ALL as 'HAS'. A stale queued HA_SELL")
    print("         job will error with 'retired' instead of running.")
    print(f"         Backups: *.bak-{FENCE}; the runner is at {RUNNER}.bak-{FENCE}")


if __name__ == "__main__":
    main()
