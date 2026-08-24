#!/usr/bin/env python3
# apply_scalp_v1_parallel_20260823.py
#
# SCALP_V1 backtest SPEED — fence: SCALP_V1_PARALLEL_20260823
#
# Two independent optimizations, results BYTE-IDENTICAL to serial:
#
#  1. PARALLEL DAY CHUNKS (the IC_PARALLEL fleet pattern, reused verbatim in
#     shape): new SCALP_V1 config key `parallel_workers` (default 1 = serial,
#     exactly today's behavior). N>1 shards the date range into N contiguous
#     chunks run in separate SPAWNED processes, each recursively calling
#     run_backtest with workers forced to 1 and audit muted. Trades merged
#     and sorted by (entry_ts, symbol); summary computed by the parent.
#     Sound because SCALP_V1 positions NEVER cross days (EOD square-off) and
#     each worker opens its own read-only CandleSource connection. Requires
#     the freeze_support() guard already present in main.py. A spawn failure
#     aborts LOUDLY (raises) — no silent serial fallback, per IC precedent.
#     Determinism (D7) is preserved: same per-day work, order-independent
#     merge. Acceptance: workers=4 run must equal workers=1 run row-for-row.
#
#  2. 1s-PROBE ELIMINATION: the exit path queried the 1s table
#     (has_1s_for_minute + seconds_for_minute) on EVERY in-trade candle —
#     hundreds of thousands of SQLite probes per full run. But
#     resolve_exit_on_candle only READS seconds in the BOTH-TOUCHED case
#     (high>=sl AND low<=tp), which is a handful of candles per run. The
#     probe is now gated on both-touched first. Behavior-identical by
#     construction; pure query elimination.
#
# FILES: backend/app/backtest/runner/backtest_runner.py,
#        backend/app/config/strategy_loader.py,
#        frontend/src/pages/Backtest.jsx (Workers field, V1-only),
#        (+ desktop rsync tree if present locally)
#
# parallel_workers is deliberately NOT added to RunComparison PARAM_KEYS or
# queue labels: it cannot change results, so two runs differing only in
# workers must keep comparing as identical params.
#
# PREREQS: all three earlier fences applied (anchors match that state).
# Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_PARALLEL_20260823"
PREREQS = ["SCALP_V1_BT_FILTERS_20260823", "SCALP_V1_DIAG_20260823",
           "SCALP_V1_DETERMINISM_20260823"]
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/runner/backtest_runner.py"
LD_REL = "app/config/strategy_loader.py"
BT_JSX = ROOT / "frontend" / "src" / "pages" / "Backtest.jsx"

TREES = [ROOT / "backend"]
_desktop = ROOT / "desktop" / "src-tauri" / "backend"
if (_desktop / RN_REL).exists():
    TREES.append(_desktop)


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def _replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return text.replace(old, new, 1)


# ═══ backtest_runner.py ════════════════════════════════════════════════════

# ── P1: module-level parallel worker + chunk helper (before _Ctx) ──────────
P1_OLD = "# ── SCALP_V1_BT_FILTERS_20260823 END: helpers ──"
P1_NEW = '''# ── SCALP_V1_BT_FILTERS_20260823 END: helpers ──


# ── SCALP_V1_PARALLEL_20260823 BEGIN: parallel-days machinery ──
# IC_PARALLEL pattern: module-level worker (spawn-picklable) that recursively
# runs its contiguous chunk SERIALLY with audit muted, returning picklable
# ClosedTrade dataclasses + the chunk's coverage dict.
def _scalp_parallel_worker(strategy_id: str, underlying: str,
                           date_from_iso: str, date_to_iso: str,
                           cfg: dict) -> dict:
    child_cfg = dict(cfg)
    child_cfg["parallel_workers"] = 1          # child MUST run serial
    try:
        from app.event_bus.audit_logger import audit_muted
        _mute = audit_muted()
    except Exception:                          # audit_muted unavailable → run unmuted
        import contextlib
        _mute = contextlib.nullcontext()
    with _mute:
        out = run_backtest(
            strategy_id=strategy_id, underlying=underlying,
            date_from=date.fromisoformat(date_from_iso),
            date_to=date.fromisoformat(date_to_iso),
            config_override=child_cfg, progress_cb=None)
    return {"trades": out["trades"],
            "coverage": out["summary"].get("coverage", {})}
# ── SCALP_V1_PARALLEL_20260823 END: parallel-days machinery ──'''

# ── P2: parallel branch in run_backtest, BEFORE the config-override token ──
# (parent never installs the override; each child installs its own, so an
#  early return here cannot leak the token.)
P2_OLD = """    # ── BT_CONFIG_OVERRIDE: on_candle reads load_strategy_config(strategy_id)
    # INLINE; install this run's merged cfg so the engine's SL/RR gates use the
    # Backtest page params, not the on-disk Settings file. Cleared before return. ──"""
P2_NEW = '''    # ── SCALP_V1_PARALLEL_20260823 BEGIN: shard days across processes ──
    try:
        _n_workers = int(cfg.get("parallel_workers", 1) or 1)
    except (TypeError, ValueError):
        _n_workers = 1
    if _n_workers > 1:
        _all_days = _trading_days(date_from, date_to)
        if len(_all_days) > _n_workers:
            import math as _math
            from concurrent.futures import ProcessPoolExecutor, as_completed
            from multiprocessing import get_context
            _step = _math.ceil(len(_all_days) / _n_workers)
            _chunks = [_all_days[i:i + _step]
                       for i in range(0, len(_all_days), _step)]
            write_audit_log(
                f"[BACKTEST] START run={run_id} {strategy_id}/{underlying} "
                f"{date_from}..{date_to} days={len(_all_days)} "
                f"PARALLEL workers={_n_workers} chunks={len(_chunks)}")
            _merged: list = []
            _cov_m = {"days_total": len(_all_days), "days_covered": 0,
                      "days_skipped": 0, "skipped": []}
            _days_done = 0
            try:
                with ProcessPoolExecutor(
                        max_workers=len(_chunks),
                        mp_context=get_context("spawn")) as _pool:
                    _futs = {_pool.submit(
                        _scalp_parallel_worker, strategy_id, underlying,
                        ch[0].isoformat(), ch[-1].isoformat(), cfg): ch
                        for ch in _chunks}
                    for _fut in as_completed(_futs):
                        _out = _fut.result()
                        _merged.extend(_out["trades"])
                        _c = _out.get("coverage") or {}
                        _cov_m["days_covered"] += _c.get("days_covered", 0)
                        _cov_m["days_skipped"] += _c.get("days_skipped", 0)
                        _cov_m["skipped"].extend(_c.get("skipped", []))
                        _days_done += len(_futs[_fut])
                        if progress_cb:
                            progress_cb({"day": _days_done,
                                         "total_days": len(_all_days),
                                         "date": _futs[_fut][-1].isoformat(),
                                         "watched": 0})
            except Exception as _exc:
                # LOUD, not silent-serial: a quiet fallback would mask a
                # missing freeze_support guard and silently cost the user
                # the speedup they configured (IC_PARALLEL precedent).
                raise RuntimeError(
                    f"{strategy_id} parallel execution failed: {_exc!r} — "
                    f"rerun with parallel_workers=1") from _exc
            _merged.sort(key=lambda t: (t.entry_ts, t.symbol))
            _cov_m["skipped"].sort(key=lambda s: s.get("date", ""))
            summary = _summarize(_merged, started)
            write_audit_log(
                f"[BACKTEST] DONE run={run_id} trades={len(_merged)} "
                f"gross={summary['summary']['gross_pnl']:.2f} "
                f"charges={summary['summary']['total_charges']:.2f} "
                f"net={summary['summary']['net_pnl']:.2f} "
                f"win_rate={summary['summary']['win_rate']:.1f}% "
                f"workers={_n_workers} "
                f"elapsed={summary['summary']['elapsed_s']}s")
            summary["run_id"] = run_id
            summary["trades"] = _merged
            summary["config"] = cfg
            summary["summary"]["coverage"] = _cov_m
            write_audit_log(
                f"[BACKTEST][COVERAGE] days_total={_cov_m['days_total']} "
                f"covered={_cov_m['days_covered']} "
                f"skipped={_cov_m['days_skipped']}")
            return summary
    # ── SCALP_V1_PARALLEL_20260823 END (serial path continues below) ──

    # ── BT_CONFIG_OVERRIDE: on_candle reads load_strategy_config(strategy_id)
    # INLINE; install this run's merged cfg so the engine's SL/RR gates use the
    # Backtest page params, not the on-disk Settings file. Cleared before return. ──'''

# ── P3: 1s probe gated on both-touched (behavior-identical) ────────────────
P3_OLD = """                    book.update_extremes(sym, c.close)
                    minute_start = (ts // 60) * 60
                    seconds = (src.seconds_for_minute(sym, minute_start)
                               if src.has_1s_for_minute(sym, minute_start) else None)"""
P3_NEW = """                    book.update_extremes(sym, c.close)
                    # ── SCALP_V1_PARALLEL_20260823 ── the 1s series is only
                    # READ by resolve_exit_on_candle in the BOTH-TOUCHED case
                    # (high>=sl AND low<=tp); probing the 1s table on every
                    # in-trade candle was hundreds of thousands of needless
                    # SQLite queries per full run. Gate the probe on the same
                    # predicate — identical fills by construction.
                    seconds = None
                    if c.high >= open_pos.sl and c.low <= open_pos.tp:
                        minute_start = (ts // 60) * 60
                        seconds = (src.seconds_for_minute(sym, minute_start)
                                   if src.has_1s_for_minute(sym, minute_start) else None)"""

# ═══ strategy_loader.py ════════════════════════════════════════════════════

L1_OLD = '''        "max_trades_per_day": 0,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''
L1_NEW = '''        "max_trades_per_day": 0,
        # ── SCALP_V1_PARALLEL_20260823 ── backtest-only: N>1 shards the date
        # range into N processes; results byte-identical to serial. 1 = off.
        "parallel_workers": 1,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''

# ═══ Backtest.jsx ══════════════════════════════════════════════════════════

J1_OLD = "  const [v1MaxTradesDay, setV1MaxTradesDay] = useState(saved.v1MaxTradesDay ?? 0);"
J1_NEW = """  const [v1MaxTradesDay, setV1MaxTradesDay] = useState(saved.v1MaxTradesDay ?? 0);
  // ── SCALP_V1_PARALLEL_20260823 ── backtest speed knob; results identical.
  const [v1Workers, setV1Workers] = useState(saved.v1Workers ?? 4);"""

J2_OLD = "      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay });   // ── SCALP_V1_BT_FILTERS_UI_20260823 ──"
J2_NEW = "      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay,   // ── SCALP_V1_BT_FILTERS_UI_20260823 ──\n      v1Workers });   // ── SCALP_V1_PARALLEL_20260823 ──"

J3_OLD = "      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay]);   // ── SCALP_V1_BT_FILTERS_UI_20260823 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"
J3_NEW = "      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay,   // ── SCALP_V1_BT_FILTERS_UI_20260823 ──\n      v1Workers]);   // ── SCALP_V1_PARALLEL_20260823 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"

J4_OLD = "      if (Number(v1MaxTradesDay) > 0) cfg.max_trades_per_day = Number(v1MaxTradesDay);\n    }"
J4_NEW = """      if (Number(v1MaxTradesDay) > 0) cfg.max_trades_per_day = Number(v1MaxTradesDay);
      // ── SCALP_V1_PARALLEL_20260823 ── always emitted (default 1 = serial);
      // deliberately NOT in RunComparison PARAM_KEYS — cannot change results.
      cfg.parallel_workers = Number(v1Workers) || 1;
    }"""

J5_OLD = "      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay]);   // ── SCALP_V1_BT_FILTERS_UI_20260823 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"
J5_NEW = "      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay,   // ── SCALP_V1_BT_FILTERS_UI_20260823 ──\n      v1Workers]);   // ── SCALP_V1_PARALLEL_20260823 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"

J6_OLD = '              <Field label="Max trades/day"><input type="number" min="0" style={inputStyle} value={v1MaxTradesDay} onChange={(e) => setV1MaxTradesDay(e.target.value)} /></Field>'
J6_NEW = '''              <Field label="Max trades/day"><input type="number" min="0" style={inputStyle} value={v1MaxTradesDay} onChange={(e) => setV1MaxTradesDay(e.target.value)} /></Field>
              {/* ── SCALP_V1_PARALLEL_20260823 ── 1 = serial; N>1 = N processes,
                  identical results. Sensible ceiling: number of CPU cores. */}
              <Field label="Workers"><input type="number" min="1" max="16" style={inputStyle} value={v1Workers} onChange={(e) => setV1Workers(e.target.value)} /></Field>'''


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")

    staged = []
    for tree in TREES:
        rn_p, ld_p = tree / RN_REL, tree / LD_REL
        rn, ld = rn_p.read_text(), ld_p.read_text()
        if FENCE in rn or FENCE in ld:
            _die(f"fence {FENCE} already present under {tree} — already applied")
        for pf in PREREQS:
            if pf not in rn:
                _die(f"prerequisite fence {pf} MISSING in {rn_p} — apply earlier scripts first")
        rn = _replace_once(rn, P1_OLD, P1_NEW, f"{tree.name}:P1")
        rn = _replace_once(rn, P2_OLD, P2_NEW, f"{tree.name}:P2")
        rn = _replace_once(rn, P3_OLD, P3_NEW, f"{tree.name}:P3")
        ld = _replace_once(ld, L1_OLD, L1_NEW, f"{tree.name}:L1")
        staged.append((rn_p, rn))
        staged.append((ld_p, ld))

    jsx = BT_JSX.read_text()
    if FENCE in jsx:
        _die(f"fence {FENCE} already present in {BT_JSX.name} — already applied")
    for label, old, new in [("J1", J1_OLD, J1_NEW), ("J2", J2_OLD, J2_NEW),
                            ("J3", J3_OLD, J3_NEW), ("J4", J4_OLD, J4_NEW),
                            ("J5", J5_OLD, J5_NEW), ("J6", J6_OLD, J6_NEW)]:
        jsx = _replace_once(jsx, old, new, f"jsx:{label}")
    staged.append((BT_JSX, jsx))

    # anchors verified AND staged Python compiled BEFORE any write
    for path, text in staged:
        if path.suffix == ".py":
            try:
                compile(text, str(path), "exec")
            except SyntaxError as e:
                _die(f"staged content for {path} does not compile: {e}")
    for path, text in staged:
        path.write_text(text)
        print(f"PATCHED: {path}")

    print()
    print(f"DONE — fence {FENCE} applied.")
    print()
    print("ACCEPTANCE TEST:")
    print("  1. Short range (e.g. one month): run Workers=1 and Workers=4;")
    print("     exports must be row-identical. Determinism (D7) makes this a")
    print("     strict byte comparison, not a statistical one.")
    print("  2. Then the full-range baseline with Workers=4.")
    print()
    print("NOTES:")
    print(" * Expect ~3.5-4x on 4 workers for the day loop, plus the 1s-probe")
    print("   elimination which also speeds SERIAL runs.")
    print(" * Sweeps multiply: the queue runs jobs sequentially, each job now")
    print("   using its own workers setting.")
    print(" * Sensible worker count = physical cores (Air: 4-6). Each worker")
    print("   opens its own read-only SQLite connection; WAL handles this.")


if __name__ == "__main__":
    main()
