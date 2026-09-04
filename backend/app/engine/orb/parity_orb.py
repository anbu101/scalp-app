# backend/app/engine/orb/parity_orb.py
#
# ── ORB_V1 PARITY HARNESS ── Fence: ORB_LIVE3_20260903
# Compares a day's PAPER rows against the backtest run of the SAME day with
# the LIVE config. Divergences beyond the ledger are integration bugs.
# Usage (from backend/, PYTHONPATH=$PWD):
#   python3 app/engine/orb/parity_orb.py 2026-09-04 [2026-09-05 ...]

from __future__ import annotations
import sys
from datetime import date


def run(days):
    from app.engine.pst.pst_common import canonical_db_path
    from app.utils.app_paths import APP_HOME
    from app.config.strategy_loader import STRATEGY_CONFIG
    from app.backtest.orb.backtest_orb_runner import run_orb_backtest
    import sqlite3
    cfg = dict(STRATEGY_CONFIG.get("ORB_V1", {}))
    dbp = str(APP_HOME / "backtest" / "backtest.db")
    conn = sqlite3.connect(canonical_db_path())
    conn.row_factory = sqlite3.Row
    for d in days:
        dd = date.fromisoformat(d)
        rows = conn.execute(
            "SELECT symbol, side, entry_price, exit_price, exit_reason,"
            " candle_ts, qty FROM paper_trades WHERE strategy_name='ORB_V1'"
            " AND date(candle_ts, 'unixepoch', '+330 minutes')=?"
            " ORDER BY candle_ts", (d,)).fetchall()
        bt = run_orb_backtest(db_path=dbp, strategy_id="ORB_V1",
                              underlying=str(cfg.get("underlying", "NIFTY")),
                              date_from=dd, date_to=dd, config_override=cfg)
        bts = bt.get("trades", [])
        print(f"\n== {d}: paper {len(rows)} vs backtest {len(bts)} trades ==")
        for i in range(max(len(rows), len(bts))):
            p = rows[i] if i < len(rows) else None
            b = bts[i] if i < len(bts) else None
            if p and b:
                dts = (p["candle_ts"] - b.entry_ts)
                print(f"  #{i+1} side {p['side']}/{b.instrument_type}"
                      f"  entry_ts Δ{dts:+d}s"
                      f"  entry {p['entry_price']:.2f}/{b.entry_price:.2f}"
                      f"  exit {p['exit_reason']}/{b.exit_reason}"
                      f" {p['exit_price'] or 0:.2f}/{b.exit_price or 0:.2f}"
                      + ("   <-- CHECK" if (p["side"] != b.instrument_type
                                            or p["exit_reason"] != b.exit_reason
                                            or abs(dts) > 120) else ""))
            else:
                print(f"  #{i+1} {'PAPER-ONLY: ' + p['symbol'] if p else 'BACKTEST-ONLY: ' + b.tradingsymbol}   <-- CHECK")
        print("  ledger: entry px spread-crossed; TP exits close-vs-intrabar;"
              " sub-second wick entries may differ. Everything else must match.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__ or "pass ISO dates"); sys.exit(1)
    run(sys.argv[1:])
