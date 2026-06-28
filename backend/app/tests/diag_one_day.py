#!/usr/bin/env python3
# Fast single-day check of the V5 selection union BEFORE a full rebuild.
# Confirms timeline["all_symbols"] is populated and how many boundaries select.
#
# Run from ANYWHERE — it locates backend/ itself:
#   python3 app/tests/diag_one_day.py 2024-04-01
#   python3 /Users/anbu/dev/scalp-app/backend/app/tests/diag_one_day.py 2024-04-01

import sys, os
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# ── make `app` importable no matter the CWD ──
# Walk up from this file until we find a dir that contains an `app/` package.
_here = Path(__file__).resolve()
_backend = None
for parent in _here.parents:
    if (parent / "app" / "__init__.py").exists():
        _backend = parent
        break
if _backend is None:
    sys.exit("Could not locate the backend/ dir (no app/__init__.py above this file).")
sys.path.insert(0, str(_backend))

IST = timezone(timedelta(hours=5, minutes=30))
DB = str(Path.home() / ".scalp-app" / "backtest" / "backtest.db")
day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2024, 4, 1)
def day_start(d): return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())

from app.backtest.data.candle_source import CandleSource
from app.backtest.engine.backtest_selector import build_selection_timeline

src = CandleSource(DB)
lo = day_start(day)
tl = build_selection_timeline(
    src=src, underlying="NIFTY", day_start_epoch=lo,
    cfg={"option_premium": {"min": 150, "max": 200}, "trade_side_mode": "BOTH"},
    strategy_id="SCALP_V5")

print(f"=== {day} ===")
print(f"DB: {DB}")
print(f"covered: {tl.get('covered')}  skip_reason: {tl.get('skip_reason')}")
print(f"expected_expiry: {tl.get('expected_expiry')}")
print(f"boundaries: {len(tl.get('boundaries', []))}")
print(f"all_symbols (watched union): {len(tl.get('all_symbols', set()))}")
nonempty = sum(1 for s in tl.get('snapshots', {}).values() if s)
print(f"boundaries with a non-empty selection: {nonempty}/{len(tl.get('boundaries', []))}")
syms = sorted(tl.get('all_symbols', set()))
print("sample watched:", syms[:8])

try:
    src.close()
except Exception:
    pass