# backend/app/backtest/cbo/test_cbo_skew_atm.py
#
# ── CBO_SKEW_ATM_FIX_20260830 REGRESSION ── reproduces the exact corpus
# shape that killed 86% of the calendar in the first four sweep runs, and
# pins the fixed behaviour.
#
# THE SHAPE (a 2023-style day): spot 18000, ATM premium ~70 — BELOW the
# band min of 150. The contracts the band selects are therefore ITM on
# each side at DISJOINT strikes (CE at 17850, PE at 18150). The old gate
# looked the opposite leg up at the pick's strike inside the band snapshot,
# found None, and fail-closed every signal of every such day: blocked_skew
# == signals-in-session, trades == 0, invisibly. The fixed gate measures
# ATM CE vs ATM PE at the strike nearest spot from the FULL expiry chain.
#
# Run from the repo root:
#     python3 backend/app/backtest/cbo/test_cbo_skew_atm.py .

from __future__ import annotations

import sqlite3
import sys
import tempfile
import types
from contextlib import contextmanager
from datetime import date
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FAILED = []


def chk(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          f"{('  ' + extra) if extra else ''}")
    if not cond:
        FAILED.append(label)


_mh = types.ModuleType("app.utils.market_hours")
_mh.is_trading_day = lambda d=None: True
sys.modules["app.utils.market_hours"] = _mh
_al = types.ModuleType("app.event_bus.audit_logger")
_al.write_audit_log = lambda *a, **k: None


@contextmanager
def _muted():
    yield


_al.audit_muted = _muted
sys.modules["app.event_bus.audit_logger"] = _al

import app.backtest.engine.expiry_calendar as _ec  # noqa: E402
from backtest_cbo_runner import run_cbo_backtest, _day_start_epoch  # noqa: E402

DAY = date(2023, 5, 10)
EXPIRY = date(2023, 5, 11)
_ec.expected_expiry_for_day = lambda d: EXPIRY
DS = _day_start_epoch(DAY)


def ts(minute):
    return DS + minute * 60


SCHEMA = """
CREATE TABLE backtest_candles_1m (
  ts INTEGER, tradingsymbol TEXT, underlying TEXT, instrument_type TEXT,
  strike REAL, expiry TEXT, open REAL, high REAL, low REAL, close REAL,
  volume INTEGER DEFAULT 0, oi INTEGER DEFAULT 0
);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol, ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying, instrument_type, ts);
"""


def flat_opt(c, sym, strike, itype, px, m0=555, m1=935):
    """A contract quoted every minute at a constant price — liquidity is
    deliberately NOT the variable in this test."""
    for m in range(m0, m1):
        c.execute(
            "INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
            "instrument_type,strike,expiry,open,high,low,close) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ts(m), sym, "NIFTY", itype, strike, EXPIRY.isoformat(),
             px, px + 1, px - 1, px))


def build(db):
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    # SPOT: reference bar 09:15-09:19 high 18050 / low 17950, breakout UP at
    # 09:21 through 18050, then calm (no SL touch at 17950).
    rows = [(555, 18000, 18050, 17950, 18000)]
    rows += [(m, 18000, 18010, 17990, 18000) for m in (556, 557, 558, 559)]
    rows += [(560, 18000, 18020, 17995, 18010),
             (561, 18010, 18060, 18005, 18055)]        # BREAK 18050
    rows += [(m, 18055, 18065, 18045, 18055) for m in range(562, 930)]
    for m, o, h, l, cl in rows:
        c.execute(
            "INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
            "instrument_type,open,high,low,close) VALUES(?,?,?,?,?,?,?,?)",
            (ts(m), "NIFTY-SPOT", "NIFTY", "SPOT", o, h, l, cl))
    # ATM strikes: premium ~70, OUTSIDE the 150-200 band, CE dearer than PE
    # (75 vs 65) so a raw UP gate measured AT ATM must PASS. Both 18000 and
    # 18050 are quoted because the gate rounds SPOT AT SIGNAL TIME to the
    # grid — after the 09:21 breakout spot is 18055, so true ATM is 18050
    # (the first version of this test omitted it, and the runner correctly
    # fail-closed with blocked_skew_unmeasurable=1: sparse synthetic chains
    # get caught, which is exactly the counter doing its job).
    flat_opt(c, "N23ATMCE", 18000, "CE", 75.0)
    flat_opt(c, "N23ATMPE", 18000, "PE", 65.0)
    flat_opt(c, "N23ATM2CE", 18050, "CE", 75.0)
    flat_opt(c, "N23ATM2PE", 18050, "PE", 65.0)
    # Band contracts: ITM at DISJOINT strikes — the killer shape.
    flat_opt(c, "N23ITMCE", 17850, "CE", 170.0)   # only CE at 17850
    flat_opt(c, "N23ITMPE", 18150, "PE", 170.0)   # only PE at 18150
    c.commit()
    c.close()


def run(db, skew):
    return run_cbo_backtest(
        db_path=db, strategy_id="CBO_V1", underlying="NIFTY",
        date_from=DAY, date_to=DAY,
        config_override={
            "option_premium": {"min": 150, "max": 200},
            "target_value": 10.0, "session_start": "09:15",
            "session_end": "09:25", "eod_square_off": "15:15",
            "atm_skew_filter": skew})


db = str(Path(tempfile.mkdtemp()) / "bt.db")
build(db)

print("\n── the killer shape: ATM premium below the band min ──────────────")
r_off = run(db, {"enabled": False})
chk("with skew OFF the day trades (baseline sanity)",
    len(r_off["trades"]) == 1)
chk("the pick is the band's ITM CE at the DISJOINT strike 17850",
    r_off["trades"] and r_off["trades"][0].strike == 17850.0)

r_on = run(db, {"enabled": True, "min_diff_pts": 0.0, "invert": False,
                "parity_adjust": False, "carry_pts": 6.5})
d = r_on["summary"]["diag_cbo"]
chk("FIXED: with raw skew ON the day STILL trades — ATM CE (75) is dearer "
    "than ATM PE (65), so the rule as written passes",
    len(r_on["trades"]) == 1,
    f"trades={len(r_on['trades'])} blocked_skew={d['blocked_skew']} "
    f"unmeasurable={d['blocked_skew_unmeasurable']}")
chk("no signal died as unmeasurable (both ATM legs were quoted)",
    d["blocked_skew_unmeasurable"] == 0)
chk("the traded contract is still the BAND's pick (17850 CE) — the fix "
    "changes where skew is MEASURED, never what is TRADED",
    r_on["trades"] and r_on["trades"][0].strike == 17850.0)

print("\n── the verdict still has teeth ───────────────────────────────────")
r_inv = run(db, {"enabled": True, "min_diff_pts": 0.0, "invert": True,
                 "parity_adjust": False, "carry_pts": 6.5})
di = r_inv["summary"]["diag_cbo"]
chk("inverted gate BLOCKS the same UP signal (verdict, not data)",
    len(r_inv["trades"]) == 0 and di["blocked_skew"] >= 1
    and di["blocked_skew_unmeasurable"] == 0)

print("\n── ledger balance (BUG 2 + BUG 3) ────────────────────────────────")
for tag, res in (("skew OFF", r_off), ("skew ON", r_on), ("inverted", r_inv)):
    dd = res["summary"]["diag_cbo"]
    raw = dd["signals_raw"]
    acc = dd["entries"] + sum(v for k, v in dd.items()
                              if k.startswith("blocked_"))
    chk(f"[{tag}] signals_raw == entries + Σ blocked_*",
        raw == acc, f"{raw} vs {acc} (after_eod={dd['blocked_after_eod']})")

print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CBO SKEW-ATM REGRESSION CHECKS PASSED")