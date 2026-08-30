# backend/app/backtest/cbo/test_cbo_d10_filters.py
#
# ── CBO_D10_FILTERS_20260830 REGRESSION ── pins:
#   D10  ε=0 is byte-identical touch-fill; ε>0 requires trade-THROUGH and
#        still fills AT the limit; a touched-not-through trade keeps living
#        and can later lose (the whole point of the bound).
#   VWAP UP passes above session VWAP, blocked below; invert flips; the
#        gate uses the TRIGGER bar's own value (no lookahead).
#   EMA  warmup blocks as unmeasurable (counted); the ledger still balances.
#
# Run from the repo root:
#     python3 backend/app/backtest/cbo/test_cbo_d10_filters.py .

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
    run_cbo_backtest, resolve_exit, _day_start_epoch)
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


print("\n── 1. D10 unit semantics ─────────────────────────────────────────")
common = dict(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
              direction=UP, spot_bar=B(110, 112, 105, 111), sl_prem_px=None)
chk("ε=0: an exact touch (high==160) fills at 160 — today's model",
    resolve_exit(**common, opt_bar=B(155, 160.0, 154, 158),
                 tp_eps=0.0) == ("TP", 160))
chk("ε=0.5: the same exact touch does NOT fill",
    resolve_exit(**common, opt_bar=B(155, 160.0, 154, 158),
                 tp_eps=0.5) is None)
chk("ε=0.5: high 160.45 (through 0.45 < 0.5) still does NOT fill",
    resolve_exit(**common, opt_bar=B(155, 160.45, 154, 158),
                 tp_eps=0.5) is None)
chk("ε=0.5: high 160.50 fills — AT the limit 160, never better",
    resolve_exit(**common, opt_bar=B(155, 160.50, 154, 158),
                 tp_eps=0.5) == ("TP", 160))
chk("ε=1.0: needs high ≥ 161.00",
    resolve_exit(**common, opt_bar=B(155, 160.95, 154, 158),
                 tp_eps=1.0) is None
    and resolve_exit(**common, opt_bar=B(155, 161.0, 154, 158),
                     tp_eps=1.0) == ("TP", 160))
chk("short mirror: tp 140 with ε=0.5 needs low ≤ 139.50, fills at 140",
    resolve_exit(is_sell=True, entry_px=150, tp_px=140, spot_stop=100,
                 direction=UP, opt_bar=B(145, 146, 139.5, 141),
                 spot_bar=B(110, 112, 105, 111), sl_prem_px=None,
                 tp_eps=0.5) == ("TP", 140))
# same minute: option trades THROUGH the TP (161.5 > 160+0.5) AND spot
# breaches the stop (low 99 <= 100) -> the D3 tie-break must still hold.
_both = resolve_exit(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
                     direction=UP, opt_bar=B(155, 161.5, 130, 133),
                     spot_bar=B(110, 112, 99, 101), sl_prem_px=None,
                     tp_eps=0.5)
chk("ε never rescues the SL tie-break: SL still beats a through-TP",
    _both == ("SL_SPOT", 133), f"got {_both}")

print("\n── 2. end-to-end: ε converts a touch-win into a later loss ───────")
SCHEMA = """
CREATE TABLE backtest_candles_1m (
  ts INTEGER, tradingsymbol TEXT, underlying TEXT, instrument_type TEXT,
  strike REAL, expiry TEXT, open REAL, high REAL, low REAL, close REAL,
  volume INTEGER DEFAULT 0, oi INTEGER DEFAULT 0
);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol, ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying, instrument_type, ts);
"""


def build(db, rising=True, wick_only=True):
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    # SPOT: 09:15-19 ref (high 25050 / low 24950); UP break at 09:21.
    # rising=True keeps the session climbing so spot close > session VWAP at
    # the trigger; rising=False makes the morning fall first so the trigger
    # bar closes BELOW VWAP (for the VWAP-block case) while still breaking
    # the 5m high. Later (10:30) spot slips to 24940 -> SL_SPOT for any
    # still-open trade.
    rows = [(555, 25000, 25050, 24950, 25000)]
    rows += [(m, 25000, 25010, 24990, 25000) for m in (556, 557, 558, 559)]
    if rising:
        rows += [(560, 25000, 25020, 24995, 25010),
                 (561, 25010, 25060, 25005, 25055)]
        rows += [(m, 25055, 25060, 25045, 25052) for m in range(562, 570)]
    else:
        # fall hard first so VWAP sits well above, then wick-break the high
        # with a close back BELOW the running VWAP
        rows += [(m, 24990 - (m - 560) * 8, 24995 - (m - 560) * 8,
                  24975 - (m - 560) * 8, 24980 - (m - 560) * 8)
                 for m in range(560, 566)]
        rows += [(566, 24935, 25055, 24930, 24945)]   # wick-break, weak close
        rows += [(m, 24945, 24950, 24935, 24940) for m in range(567, 570)]
    rows += [(570, 25050 if rising else 24940, 25052 if rising else 24945,
              24938, 24940)]                          # spot stop 24950 hit
    for m in range(571, 930):
        rows.append((m, 24960, 24970, 24950.5, 24960))
    for m, o, h, l, cl in rows:
        c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,"
                  "underlying,instrument_type,open,high,low,close) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ts(m), "NIFTY-SPOT", "NIFTY", "SPOT", o, h, l, cl))
    # CE: fills 150; at 09:24 wicks EXACTLY to 160 (touch, close 156) —
    # never trades through; decays after; SL_SPOT minute prices ~140.
    ce = {555: (150, 151, 149, 150), 564: (155, 160.0, 154, 156),
          567: (150, 152, 148, 150), 570: (141, 142, 139, 140)}
    last = None
    for m in range(555, 935):
        if m in ce:
            last = ce[m]
        if last is None:
            continue
        o, h, l, cl = last
        for sym, ity in (("NIFC", "CE"), ("NIFP", "PE")):
            c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,"
                      "underlying,instrument_type,strike,expiry,open,high,"
                      "low,close) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ts(m), sym, "NIFTY", ity, 25000.0,
                       EXPIRY.isoformat(), o, h, l, cl))
    c.commit()
    c.close()


def run(db, **over):
    cfg = {"option_premium": {"min": 100, "max": 200}, "target_value": 10.0,
           "session_start": "09:15", "session_end": "09:25",
           "eod_square_off": "15:15"}
    cfg.update(over)
    return run_cbo_backtest(db_path=db, strategy_id="CBO_V1",
                            underlying="NIFTY", date_from=DAY, date_to=DAY,
                            config_override=cfg)


db = str(Path(tempfile.mkdtemp()) / "bt.db")
build(db, rising=True)
r0 = run(db)
chk("ε=0 baseline: the 09:24 exact-touch wick books the WIN at 160",
    len(r0["trades"]) == 1 and r0["trades"][0].exit_reason == "TP"
    and r0["trades"][0].exit_price == 160.0)
r5 = run(db, tp_fill_through_pts=0.5)
t5 = r5["trades"][0] if r5["trades"] else None
chk("ε=0.5: SAME trade — the touch doesn't fill, the trade lives on and "
    "LOSES at the spot stop later (win -> loss, the honesty bound)",
    t5 is not None and t5.exit_reason == "SL_SPOT" and t5.net_pnl < 0,
    f"reason={getattr(t5,'exit_reason',None)} net={getattr(t5,'net_pnl',None)}")

print("\n── 3. VWAP filter end-to-end ─────────────────────────────────────")
rv = run(db, vwap_filter={"enabled": True, "min_pts": 0.0, "invert": False})
chk("rising session: UP trigger closes ABOVE session VWAP -> passes, "
    "trade unchanged",
    len(rv["trades"]) == 1
    and rv["summary"]["diag_cbo"]["blocked_vwap"] == 0)
rvi = run(db, vwap_filter={"enabled": True, "min_pts": 0.0, "invert": True})
chk("invert flips the same day to BLOCKED (verdict, not data)",
    len(rvi["trades"]) == 0
    and rvi["summary"]["diag_cbo"]["blocked_vwap"] >= 1
    and rvi["summary"]["diag_cbo"]["blocked_vwap_unmeasurable"] == 0)

db2 = str(Path(tempfile.mkdtemp()) / "bt.db")
build(db2, rising=False)
# direction=UP isolates the scenario: the falling tape fires DOWN
# signals first, and DOWN legitimately PASSES a below-VWAP gate — the
# first version of this test asserted on those and "failed" against
# correct behaviour.
rb = run(db2, session_end="09:30", direction="UP",
         vwap_filter={"enabled": True, "min_pts": 0.0, "invert": False})
db2_base = run(db2, session_end="09:30", direction="UP")
chk("falling-then-wick-break day: baseline takes the trade, VWAP gate "
    "blocks it (trigger closes below session VWAP)",
    len(db2_base["trades"]) >= 1 and len(rb["trades"]) == 0
    and rb["summary"]["diag_cbo"]["blocked_vwap"] >= 1,
    f"base={len(db2_base['trades'])} gated={len(rb['trades'])}")

print("\n── 4. EMA gate warmup is unmeasurable, counted, fail-closed ──────")
re_ = run(db, ema_gate={"enabled": True, "period": 144, "slope_window": 10,
                        "min_slope": 0.0, "invert": False})
de = re_["summary"]["diag_cbo"]
chk("a 09:21 trigger cannot have a warm EMA(144)+10 -> blocked as "
    "UNMEASURABLE, never silently",
    len(re_["trades"]) == 0 and de["blocked_ema_unmeasurable"] >= 1
    and de["blocked_ema"] == 0)
re2 = run(db, ema_gate={"enabled": True, "period": 3, "slope_window": 2,
                        "min_slope": 0.0, "invert": False})
chk("a short EMA(3)+2 IS warm by 09:21 on a rising tape -> passes",
    len(re2["trades"]) == 1
    and re2["summary"]["diag_cbo"]["blocked_ema"] == 0)
re3 = run(db, ema_gate={"enabled": True, "period": 3, "slope_window": 2,
                        "min_slope": 0.0, "invert": True})
chk("EMA invert flips the verdict",
    len(re3["trades"]) == 0
    and re3["summary"]["diag_cbo"]["blocked_ema"] >= 1)

print("\n── 5. ledger balance with every instrument engaged ───────────────")
for tag, res in (("eps", r5), ("vwap-inv", rvi), ("ema-warmup", re_),
                 ("vwap-block", rb)):
    dd = res["summary"]["diag_cbo"]
    raw = dd["signals_raw"]
    acc = dd["entries"] + sum(v for k, v in dd.items()
                              if k.startswith("blocked_"))
    chk(f"[{tag}] signals_raw == entries + Σ blocked_*", raw == acc,
        f"{raw} vs {acc}")

print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CBO D10+FILTERS REGRESSION CHECKS PASSED")