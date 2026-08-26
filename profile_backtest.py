#!/usr/bin/env python3
# profile_backtest.py — cProfile harness for ONE short backtest run.
#
# The runners have no CLI entry (they are dispatched by queue_worker), which
# is why `python3 -m cProfile -s cumtime` alone crashed: cProfile profiles a
# SCRIPT, and none was given. This is that script.
#
# Usage (from repo root — D13 perf pass, one representative month):
#   cd backend
#   python3 -m cProfile -s cumtime ../profile_backtest.py SCALP_V1 2026-03-02 2026-03-31 | head -40
#   python3 -m cProfile -s cumtime ../profile_backtest.py SCALP_V3 2026-03-02 2026-03-31 | head -40
#
# parallel_workers is FORCED to 1: cProfile only sees THIS process; a sharded
# run hides all the real work inside spawn children and profiles nothing but
# the pool bookkeeping. (Serial is also what we want for the per-call cost
# breakdown — worker count is a separate, already-known knob.)
#
# Paste the top ~20 cumtime lines back and the perf work starts with data.

import sys
from datetime import date

sys.path.insert(0, ".")


def main():
    if len(sys.argv) != 4:
        print("usage: profile_backtest.py SCALP_V1|SCALP_V3 YYYY-MM-DD YYYY-MM-DD")
        sys.exit(2)
    strategy = sys.argv[1].upper()
    d1 = date.fromisoformat(sys.argv[2])
    d2 = date.fromisoformat(sys.argv[3])

    if strategy == "SCALP_V1":
        from app.backtest.runner.backtest_runner import run_backtest as _run
    elif strategy == "SCALP_V3":
        from app.backtest.runner.backtest_hedge_runner import run_hedge_backtest as _run
    else:
        print(f"unsupported strategy: {strategy}")
        sys.exit(2)

    res = _run(strategy_id=strategy, underlying="NIFTY",
               date_from=d1, date_to=d2,
               config_override={"parallel_workers": 1})
    s = (res or {}).get("summary", {}) or {}
    print(f"[PROFILE] {strategy} {d1}..{d2} "
          f"trades={s.get('total_trades')} net={float(s.get('net_pnl') or 0):.2f} "
          f"elapsed={s.get('elapsed_s')}s")


if __name__ == "__main__":
    main()
