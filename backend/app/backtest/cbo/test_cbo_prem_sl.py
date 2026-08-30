# backend/app/backtest/cbo/test_cbo_prem_sl.py
#
# ── CBO_PREM_SL_20260830 REGRESSION ── pins the premium stop's semantics:
# trigger-at-level fill, pct-of-entry mode, tighter-wins vs the spot stop,
# worse-fill tie-break, and — most importantly for reading the upcoming
# results honestly — the WINNER-CONVERSION case: a trade that reaches TP in
# the baseline is stopped out first once the premium SL exists. The stop is
# NOT a free reduction of the loss tail; it also costs winners, and this
# test makes that mechanical fact undeniable before any sweep is read.
#
# Run from the repo root:
#     python3 backend/app/backtest/cbo/test_cbo_prem_sl.py .

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
from backtest_cbo_runner import (  # noqa: E402
    run_cbo_backtest, resolve_exit, sl_prem_price, _day_start_epoch)
from cbo_v1_engine import UP  # noqa: E402

DAY = date(2026, 3, 12)
EXPIRY = date(2026, 3, 17)
_ec.expected_expiry_for_day = lambda d: EXPIRY
DS = _day_start_epoch(DAY)


def ts(m):
    return DS + m * 60


class B:
    def __init__(s, o, h, l, c):
        s.open, s.high, s.low, s.close = o, h, l, c


print("\n── 1. sl_prem_price helper ───────────────────────────────────────")
chk("mode=off returns None (stop disabled, baseline byte-identical)",
    sl_prem_price(150, is_sell=False, mode="off", value=10) is None)
chk("value=0 returns None even in abs mode",
    sl_prem_price(150, is_sell=False, mode="abs", value=0) is None)
chk("long abs: stop BELOW entry (150-12=138)",
    sl_prem_price(150, is_sell=False, mode="abs", value=12) == 138.0)
chk("long pct: % of ENTRY premium (150-10% = 135)",
    sl_prem_price(150, is_sell=False, mode="pct", value=10) == 135.0)
chk("short abs: stop ABOVE entry — its loss direction (150+12=162)",
    sl_prem_price(150, is_sell=True, mode="abs", value=12) == 162.0)
chk("short pct: % of premium COLLECTED (150+10% = 165, D3b)",
    sl_prem_price(150, is_sell=True, mode="pct", value=10) == 165.0)
chk("long stop floored at 0.05 (can never silently disable itself)",
    sl_prem_price(3, is_sell=False, mode="abs", value=50) == 0.05)


print("\n── 2. resolve_exit ladder ────────────────────────────────────────")
# long: entry 150, TP 160, spot stop 100, premium stop 138
common = dict(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
              direction=UP)
chk("premium stop fires when the option's LOW reaches it; fill AT the "
    "level (trigger convention, mirror of TP-at-limit)",
    resolve_exit(**common, opt_bar=B(150, 151, 137, 142),
                 spot_bar=B(110, 112, 105, 111),
                 sl_prem_px=138) == ("SL_PREM", 138))
chk("no premium stop configured -> minute is quiet (baseline unchanged)",
    resolve_exit(**common, opt_bar=B(150, 151, 137, 142),
                 spot_bar=B(110, 112, 105, 111),
                 sl_prem_px=None) is None)
chk("spot stop alone still labels SL_SPOT and fills at option CLOSE",
    resolve_exit(**common, opt_bar=B(150, 151, 145, 146),
                 spot_bar=B(110, 112, 99, 101),
                 sl_prem_px=138) == ("SL_SPOT", 146))
chk("SL beats TP in the same minute — the D3 tie-break holds for the "
    "premium stop too",
    resolve_exit(**common, opt_bar=B(150, 165, 137, 158),
                 spot_bar=B(110, 112, 105, 111),
                 sl_prem_px=138) == ("SL_PREM", 138))
chk("both stops in one minute -> WORSE fill: option close 130 < level "
    "138, so SL_SPOT at 130",
    resolve_exit(**common, opt_bar=B(150, 151, 128, 130),
                 spot_bar=B(110, 112, 99, 101),
                 sl_prem_px=138) == ("SL_SPOT", 130))
chk("both stops, close ABOVE the level -> worse is the level: SL_PREM 138",
    resolve_exit(**common, opt_bar=B(150, 151, 136, 141),
                 spot_bar=B(110, 112, 99, 101),
                 sl_prem_px=138) == ("SL_PREM", 138))
chk("short: premium stop on the option's HIGH, fill at level",
    resolve_exit(is_sell=True, entry_px=150, tp_px=140, spot_stop=100,
                 direction=UP, opt_bar=B(150, 163, 149, 158),
                 spot_bar=B(110, 112, 105, 111),
                 sl_prem_px=162) == ("SL_PREM", 162))


print("\n── 3. end-to-end: winner conversion + attribution ────────────────")
SCHEMA = """
CREATE TABLE backtest_candles_1m (
  ts INTEGER, tradingsymbol TEXT, underlying TEXT, instrument_type TEXT,
  strike REAL, expiry TEXT, open REAL, high REAL, low REAL, close REAL,
  volume INTEGER DEFAULT 0, oi INTEGER DEFAULT 0
);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol, ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying, instrument_type, ts);
"""
db = str(Path(tempfile.mkdtemp()) / "bt.db")
c = sqlite3.connect(db)
c.executescript(SCHEMA)
# SPOT: breakout UP at 09:21 through 25050; spot NEVER nears the 24950 spot
# stop, so the baseline outcome is decided purely on the option's path.
rows = [(555, 25000, 25050, 24950, 25000)]
rows += [(m, 25000, 25010, 24990, 25000) for m in (556, 557, 558, 559)]
rows += [(560, 25000, 25020, 24995, 25010),
         (561, 25010, 25060, 25005, 25055)]
rows += [(m, 25055, 25065, 25045, 25055) for m in range(562, 930)]
for m, o, h, l, cl in rows:
    c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
              "instrument_type,open,high,low,close) VALUES(?,?,?,?,?,?,?,?)",
              (ts(m), "NIFTY-SPOT", "NIFTY", "SPOT", o, h, l, cl))
# CE: fills 150 at 09:22, DIPS to 139 at 09:25 (through a 141.5 premium
# stop, NOT through any spot level), then rallies to hit 160 TP at 09:40.
# quoted from the session open — the 09:20:30 selection boundary needs a
# print or the snapshot's CE side is empty and the entry dies as
# blocked_no_selection (the diag named this exact gate on the first
# version of this corpus, which started quoting only at 09:22).
ce = {555: (150, 151, 149, 150), 565: (146, 147, 139, 144),
      568: (146, 148, 145, 147), 580: (158, 166, 157, 165)}
last = None
for m in range(555, 935):
    if m in ce:
        last = ce[m]
    if last is None:
        continue
    o, h, l, cl = last
    c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
              "instrument_type,strike,expiry,open,high,low,close) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (ts(m), "NIF26CE", "NIFTY", "CE", 25000.0, EXPIRY.isoformat(),
               o, h, l, cl))
    c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
              "instrument_type,strike,expiry,open,high,low,close) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (ts(m), "NIF26PE", "NIFTY", "PE", 25000.0, EXPIRY.isoformat(),
               150, 151, 149, 150))
c.commit()
c.close()


def run(sl_mode, sl_value):
    return run_cbo_backtest(
        db_path=db, strategy_id="CBO_V1", underlying="NIFTY",
        date_from=DAY, date_to=DAY,
        config_override={"option_premium": {"min": 100, "max": 200},
                         "target_value": 10.0,
                         "session_start": "09:15", "session_end": "09:25",
                         "eod_square_off": "15:15",
                         "sl_prem_mode": sl_mode, "sl_prem_value": sl_value})


base = run("off", 0)
chk("baseline (stop off): the dip survives and the trade WINS at TP 160",
    len(base["trades"]) == 1 and base["trades"][0].exit_reason == "TP"
    and base["trades"][0].exit_price == 160.0)

capped = run("abs", 8.5)          # stop at 150-8.5 = 141.5, above the 139 dip
t = capped["trades"][0] if capped["trades"] else None
chk("WINNER CONVERSION: the same trade with an 8.5pt premium stop is "
    "stopped at 141.5 BEFORE the rally — the stop costs this winner",
    t is not None and t.exit_reason == "SL_PREM" and t.exit_price == 141.5,
    f"reason={getattr(t,'exit_reason',None)} exit={getattr(t,'exit_price',None)}")
chk("the loss is bounded: 8.5 x 65 = 552.5 gross",
    t is not None and abs(t.pnl + 552.5) < 0.01, f"gross={t.pnl}")
d = capped["summary"]["diag_cbo"]
chk("attribution: sl_prem_exits=1, sl_spot_exits=0 — the decomposition "
    "shows WHICH stop acted",
    d["sl_prem_exits"] == 1 and d["sl_spot_exits"] == 0)
chk("legacy aggregate still counts it (sl_exits=1) so existing reports work",
    d["sl_exits"] == 1)
chk("sl_prem P&L share is reported for falsification",
    "sl_prem_pnl_share_pct" in d)

wide = run("abs", 15)             # stop at 135, below the 139 dip
chk("a WIDER stop (15pt -> 135) survives the dip and the trade wins again "
    "— tighter-wins semantics, no phantom triggers",
    len(wide["trades"]) == 1 and wide["trades"][0].exit_reason == "TP")

pct = run("pct", 5.6)             # 5.6% of 150 = 8.4 -> stop 141.6 > 139 dip
chk("pct mode: 5.6% of entry = stop 141.6, also converts the winner",
    len(pct["trades"]) == 1 and pct["trades"][0].exit_reason == "SL_PREM"
    and abs(pct["trades"][0].exit_price - 141.6) < 0.01)

for tag, res in (("off", base), ("abs8.5", capped), ("abs15", wide)):
    dd = res["summary"]["diag_cbo"]
    raw = dd["signals_raw"]
    acc = dd["entries"] + sum(v for k, v in dd.items()
                              if k.startswith("blocked_"))
    chk(f"[{tag}] ledger: signals_raw == entries + Σ blocked_*",
        raw == acc, f"{raw} vs {acc}")

print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CBO PREMIUM-SL REGRESSION CHECKS PASSED")