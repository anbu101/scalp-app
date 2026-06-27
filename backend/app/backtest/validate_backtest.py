#!/usr/bin/env python3
# backend/app/backtest/validate_backtest.py
#
# STANDALONE validation harness — NO UI, NO repo/DB writes.
#
# Purpose: prove the backtest reuses live SCALP_V1 logic FAITHFULLY by letting
# you diff its entries against your real paper-trade log over the same period.
#
# It runs run_backtest() for a date range and writes TWO CSVs:
#   1. <out>_entries.csv  — one row per ENTRY (symbol, entry_ts, entry IST,
#                           entry_price, sl, tp). This is the set you line up
#                           against paper_trades: same contract + same candle
#                           minute + same sl/tp == faithful reuse.
#   2. <out>_trades.csv   — full closed trades (entry+exit+pnl+ambiguous_fill).
#
# HOW TO VALIDATE
#   * Pick a recent period you ran SCALP_V1 in PAPER.
#   * Run this for that period with the SAME config the paper run used
#     (it loads the live on-disk SCALP_V1 config by default — so if you haven't
#      changed it since, it matches).
#   * Compare _entries.csv vs your paper_trades rows:
#       - Do the SAME contracts get entered on the SAME candle minutes?
#       - Are sl/tp identical (the engine computes them; they should match)?
#   * Matching entries  => engine reuse is faithful; trust the P&L.
#   * Divergent entries => we found drift (selection timing / warmup); fix
#     before building any UI on top.
#
# USAGE
#   cd backend
#   python3 -m app.backtest.validate_backtest \
#       --from 2026-04-01 --to 2026-04-30 \
#       --underlying NIFTY \
#       --out /Users/anbu/Desktop/scalp_v1_validate
#
#   Optional config sweep (does NOT touch on-disk config):
#       --premium-min 100 --premium-max 300 --rr 1.0 --max-sl 0
#
# NOTE: requires the backfill to have populated backtest_candles_1m first.

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta

IST = 5 * 3600 + 30 * 60


def _ist_str(epoch: int) -> str:
    return (datetime(1970, 1, 1) + timedelta(seconds=epoch + IST)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    ap = argparse.ArgumentParser(description="SCALP_V1 backtest validation (CSV dump, no UI)")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY"])
    ap.add_argument("--out", required=True, help="output path prefix (no extension)")
    # optional config overrides (sweep without touching on-disk config)
    ap.add_argument("--premium-min", type=float, default=None)
    ap.add_argument("--premium-max", type=float, default=None)
    ap.add_argument("--rr", type=float, default=None)
    ap.add_argument("--min-sl", type=float, default=None)
    ap.add_argument("--max-sl", type=float, default=None)
    ap.add_argument("--risk-max-sl", type=float, default=None)
    args = ap.parse_args()

    # Import here so a missing dep / wrong cwd gives a clear message.
    try:
        from app.backtest.runner.backtest_runner import run_backtest
    except Exception as e:
        sys.exit(
            f"Could not import the backtest runner: {e!r}\n"
            f"Run this from the backend/ directory: "
            f"`python3 -m app.backtest.validate_backtest ...`"
        )

    # Build optional config override (only keys the user set).
    override: dict = {}
    prem = {}
    if args.premium_min is not None:
        prem["min"] = args.premium_min
    if args.premium_max is not None:
        prem["max"] = args.premium_max
    if prem:
        override["option_premium"] = prem
    if args.rr is not None:
        override["risk_reward_ratio"] = args.rr
    if args.min_sl is not None:
        override["min_sl_points"] = args.min_sl
    if args.max_sl is not None:
        override["max_sl_points"] = args.max_sl
    if args.risk_max_sl is not None:
        override["risk_max_sl_points"] = args.risk_max_sl

    df_, dt_ = _parse_date(args.date_from), _parse_date(args.date_to)

    print(f"[VALIDATE] SCALP_V1 {args.underlying} {df_}..{dt_}")
    if override:
        print(f"[VALIDATE] config override: {override}")
    print("[VALIDATE] running backtest (this replays day-by-day)...")

    def _progress(p):
        # light progress to stderr so stdout stays clean
        if p.get("day", 0) % 5 == 0 or p.get("day") == p.get("total_days"):
            print(f"  day {p['day']}/{p['total_days']} {p['date']} "
                  f"selected={p.get('selected')}", file=sys.stderr)

    result = run_backtest(
        strategy_id="SCALP_V1",
        underlying=args.underlying,
        date_from=df_,
        date_to=dt_,
        config_override=override or None,
        progress_cb=_progress,
    )

    trades = result["trades"]
    s = result["summary"]

    # ── entries CSV ───────────────────────────────────────────────
    entries_path = f"{args.out}_entries.csv"
    with open(entries_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "strike", "type", "expiry",
                    "entry_ts_epoch", "entry_ist", "entry_price", "sl", "tp",
                    "exit_ist", "exit_price", "exit_reason",
                    "gross_pnl", "charges", "net_pnl", "ambiguous_fill"])
        for t in sorted(trades, key=lambda x: x.entry_ts):
            w.writerow([t.symbol, t.strike, t.instrument_type, t.expiry,
                        t.entry_ts, _ist_str(t.entry_ts),
                        f"{t.entry_price:.2f}", f"{t.sl:.2f}", f"{t.tp:.2f}",
                        _ist_str(t.exit_ts) if t.exit_ts else "",
                        f"{t.exit_price:.2f}" if t.exit_price is not None else "",
                        t.exit_reason or "",
                        f"{t.pnl:.2f}",
                        f"{getattr(t,'charges',0.0):.2f}",
                        f"{getattr(t,'net_pnl',t.pnl):.2f}",
                        int(t.ambiguous_fill)])

    # ── full trades CSV ───────────────────────────────────────────
    trades_path = f"{args.out}_trades.csv"
    with open(trades_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "strike", "type", "expiry", "direction",
                    "entry_ist", "entry_price", "sl", "tp",
                    "exit_ist", "exit_price", "exit_reason",
                    "gross_pnl", "charges", "net_pnl",
                    "ambiguous_fill", "max_adverse", "max_favorable", "qty"])
        for t in sorted(trades, key=lambda x: x.entry_ts):
            w.writerow([
                t.symbol, t.strike, t.instrument_type, t.expiry, t.direction,
                _ist_str(t.entry_ts), f"{t.entry_price:.2f}",
                f"{t.sl:.2f}", f"{t.tp:.2f}",
                _ist_str(t.exit_ts) if t.exit_ts else "",
                f"{t.exit_price:.2f}" if t.exit_price is not None else "",
                t.exit_reason or "",
                f"{t.pnl:.2f}",
                f"{getattr(t,'charges',0.0):.2f}",
                f"{getattr(t,'net_pnl',t.pnl):.2f}",
                int(t.ambiguous_fill), f"{t.max_adverse:.2f}",
                f"{t.max_favorable:.2f}", t.qty,
            ])

    # ── summary to stdout ─────────────────────────────────────────
    print("\n[VALIDATE] ===== SUMMARY =====")
    print(f"  trades        : {s['total_trades']}")
    print(f"  wins / losses : {s['wins']} / {s['losses']}")
    print(f"  win rate      : {s['win_rate']:.1f}%")
    print(f"  GROSS P&L     : {s['gross_pnl']:.2f}")
    print(f"  charges       : -{s['total_charges']:.2f}")
    print(f"  NET P&L       : {s['net_pnl']:.2f}")
    print(f"  avg net/trade : {s['avg_net_pnl']:.2f}")
    print(f"  max drawdown  : {s['max_drawdown']:.2f}  (on NET equity)")
    print(f"  ambiguous fills (pessimistic SL-first, no 1s) : {s['ambiguous_fills']}")
    print(f"  elapsed       : {s['elapsed_s']}s")
    print(f"\n[VALIDATE] entries CSV : {entries_path}")
    print(f"[VALIDATE] trades  CSV : {trades_path}")
    print("\n[VALIDATE] Next: diff _entries.csv against your paper_trades rows for")
    print("           the same period. Same contract + same entry minute + same")
    print("           sl/tp  ==>  faithful reuse. Divergence ==> selection/warmup")
    print("           drift to fix before building the UI.")

    if s["ambiguous_fills"] > 0:
        pct = 100.0 * s["ambiguous_fills"] / max(1, s["total_trades"])
        print(f"\n[VALIDATE][NOTE] {s['ambiguous_fills']} trades ({pct:.0f}%) had BOTH "
              f"SL & TP inside the exit minute and were resolved pessimistically "
              f"(assumed SL first). Record 1-second data forward to resolve these "
              f"for real.")


if __name__ == "__main__":
    main()