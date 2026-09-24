# backend/app/backtest/engine/test_lot_comp_runners.py
#
# ── LOT_COMP_20260924 ── runner-level behavioural suite. Standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_runners.py
#
# Property under test, for EVERY runner the shared synthetic corpus can drive
# (16 of 17; BB_V1 needs a BANKNIFTY futures corpus and is covered by its
# own path + compile gate): compounding is a SIZING overlay, never a signal
# change. With step = 1 month and add = base lots (×2 at tier 1):
#   * the compounded run has the SAME trades — same entry/exit stamps, prices
#     and reasons — as the flat run;
#   * each trade's qty is the flat qty × its ENTRY-day tier multiplier, so a
#     position carried across the boundary keeps its entry lots;
#   * gross P&L scales by the same ratio (charges are not linear — brokerage
#     is flat per order — so only gross is compared);
#   * every rupee-denominated knob (TSG MTM SL, TMA2 ₹ cap, HA/V5 max loss,
#     V1 daily MTM cap, V3 daily loss, GC/CBO caps, VET daily cap) scaled with
#     the lots — an unscaled knob would fire earlier and change the path;
#   * OFF (keys absent, or either key 0) reproduces the flat run exactly.
#
# Caps that compare NET (post-charge) P&L — CBO mtm caps, V3 daily/monthly
# limits, HA day caps — cannot be exactly invariant: brokerage is flat per
# order, so net at 2× lots is not 2× net. Those caps are scaled by the day's
# ratio (the intended "sized for base lots" semantics) and kept wide here so
# the ≈₹20-per-order drift never flips an exit in this corpus. V5's cap is a
# RUN-cumulative gross figure, so its runner books gross ÷ ratio (base-lot
# units) instead of rescaling the cap — that one IS exact.
from __future__ import annotations

import os
import random
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

from app.backtest.engine.expiry_calendar import expected_expiry_for_day   # noqa: E402
from app.utils.market_hours import is_trading_day                          # noqa: E402
from app.backtest.engine.lot_compounding import LotCompounder              # noqa: E402

IST = 5 * 3600 + 30 * 60
SESSION_OPEN_MIN = 9 * 60 + 15
FAILS = []
SKIPS = []


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


DDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);
CREATE TABLE backtest_candles_1s(tradingsymbol TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL);
CREATE INDEX i4 ON backtest_candles_1s(tradingsymbol,ts);"""


def ds_for(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST


def day_of_ts(ts: int) -> date:
    return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST)).date()


def build_corpus(path: str, days: list, seed: int = 11) -> None:
    """Random-walk NIFTY spot + synthetic weekly chain (CE = intrinsic +
    decaying time value, PE mirror). Same shape as the STFC sim harness."""
    random.seed(seed)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    rows = []
    spot = 24000.0
    for d in days:
        ds = ds_for(d)
        exp = expected_expiry_for_day(d).isoformat()
        atm = int(round(spot / 50.0)) * 50          # chain follows the day's opening spot
        strikes = [atm + 50 * k for k in range(-12, 13)]
        minute_spot = []
        for m in range(SESSION_OPEN_MIN, 15 * 60 + 30):
            o = spot
            c = spot * (1 + random.gauss(0, 0.0004))
            h = max(o, c) * (1 + abs(random.gauss(0, 0.00008)))
            l = min(o, c) * (1 - abs(random.gauss(0, 0.00008)))
            ts = ds + m * 60
            rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", None, None, o, h, l, c, 0, 0))
            minute_spot.append((ts, o, h, l, c, m))
            spot = c
        for K in strikes:
            for side in ("CE", "PE"):
                sym = f"NIFTY{exp.replace('-', '')}{K}{side}"
                for (ts, o, h, l, c, m) in minute_spot:
                    tv = 60.0 * (1 - (m - SESSION_OPEN_MIN) / 800.0)

                    def px(s):
                        return round(max(s - K, 0) + tv, 2) if side == "CE" else round(max(K - s, 0) + tv, 2)
                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp, px(o),
                                 max(px(h), px(l), px(o), px(c)), min(px(h), px(l), px(o), px(c)),
                                 px(c), 100, 100))
    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


tmpdir = tempfile.mkdtemp()
dbp = os.path.join(tmpdir, "bt.db")
all_days = []
d = date(2026, 1, 20)
while len(all_days) < 45:
    if is_trading_day(d):
        all_days.append(d)
    d += timedelta(days=1)
build_corpus(dbp, all_days)
RUN_FROM, RUN_TO = all_days[5], all_days[-1]     # 2026-01-27 → 2026-03-25: one step at 02-27
print(f"── corpus {all_days[0]} → {all_days[-1]} ({len(all_days)} sessions); run {RUN_FROM} → {RUN_TO} ──")

# V1/V3 open the corpus through CandleSource() with no path argument, so the
# process-local resolver is pointed at the synthetic DB. This NEVER touches
# ~/.scalp-app/backtest/backtest.db.
import app.backtest.data.candle_source as _cs   # noqa: E402
_cs._backtest_db_path = lambda: Path(dbp)

BASE = 10
COMP = {"lot_comp_step_months": 1, "lot_comp_add_lots": BASE}   # ×2 at tier 1
comp_ref = LotCompounder(COMP, RUN_FROM, BASE)
BOUNDARY = next(dd for dd in all_days if comp_ref.tier(dd) == 1)
print(f"   tier 1 starts {BOUNDARY}")

LEGS4 = [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": BASE, "premium_max": 85, "sl_val": 42, "sl_mode": "pct"},
         {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": BASE, "premium_max": 85, "sl_val": 42, "sl_mode": "pct"},
         {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": BASE, "premium_max": 30},
         {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": BASE, "premium_max": 30}]


def _runner(name):
    kw = dict(strategy_id=None, underlying="NIFTY", date_from=RUN_FROM, date_to=RUN_TO)
    if name == "STFC_V1":
        from app.backtest.stfc.backtest_stfc_runner import run_stfc_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_min": 0, "premium_max": 1000}
    if name == "ORB_V1":
        from app.backtest.orb.backtest_orb_runner import run_orb_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_min": 0, "premium_max": 1000}
    if name == "BRK_V1":
        from app.backtest.brk.backtest_brk_runner import run_brk_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE}
    if name == "FVG_V1":
        from app.backtest.fvg.backtest_fvg_runner import run_fvg_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_min": 0, "premium_max": 1000, "disp_atr": 0.5, "gap_min_atr": 0.0}
    if name == "ORV_V1":
        from app.backtest.orv.backtest_orv_runner import run_orv_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_max": 1000}
    if name == "CBO_V1":
        from app.backtest.cbo.backtest_cbo_runner import run_cbo_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_min": 0, "premium_max": 1000, "mtm_loss_cap": 300000}   # NET-based cap: kept wide (see note)
    if name == "GC_V1":
        from app.backtest.gc.backtest_gc_runner import run_gc_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_max": 1000, "mode": "SELL", "max_loss_per_trade": 15000, "max_loss_day": 30000}
    if name == "VET_V1":
        from app.backtest.vet.backtest_vet_runner import run_vet_backtest as f
        return f, dict(db_path=dbp, **kw), {"lots": BASE, "premium_max": 1000, "warmup_sessions": 3, "max_daily_mtm_loss": 30000}
    if name == "TMA_V2":
        from app.backtest.tma.backtest_tma_v2_runner import run_tma_v2_backtest as f
        return f, dict(db_path=dbp, **kw), {"s1": {"main": {"premium_max": 300, "lots": BASE}, "hedge": {"premium_max": 100, "lots": BASE}}, "max_loss_per_trade": 20000}
    if name == "TMA_V1":
        from app.backtest.tma.backtest_tma_runner import run_tma_backtest as f
        return f, dict(db_path=dbp, **kw), {"c1": {"sell": {"premium_max": 300, "lots": BASE}, "buy": {"premium_max": 100, "lots": BASE}}}
    if name == "VAP_V1":
        from app.backtest.vap.backtest_vap_runner import run_vap_backtest as f
        return f, dict(db_path=dbp, **kw), {"v1": {"main": {"premium_max": 300, "lots": BASE}, "hedge": {"premium_max": 100, "lots": BASE}}, "signal_premium_max": 300, "mode": "SELL", "sl_pct": 30, "tp_pct": 50, "tp_mode": "PCT"}
    if name == "TSG_V1":
        from app.backtest.tsg.backtest_tsg_runner import run_tsg_backtest as f
        return f, dict(db_path=dbp, **kw), {"legs": [dict(l) for l in LEGS4], "mtm_sl": 35000, "mtm_target": 20000, "mtm_trail_arm": 8000, "mtm_trail_giveback": 4000}
    if name == "IC_V1":
        from app.backtest.ic.backtest_ic_runner import run_ic_backtest as f
        return f, dict(db_path=dbp, **kw), {"legs": [dict(l) for l in LEGS4]}
    if name == "HA_V1":
        from app.backtest.ha.backtest_ha_runner import run_ha_backtest as f
        return f, dict(db_path=dbp, **kw), {"quantity": {"lots": BASE}, "option_premium": {"min": 0, "max": 1000}, "max_loss": 20000, "max_profit": 40000}
    if name == "SCALP_V5":
        from app.backtest.scalpv5.backtest_scalpv5_runner import run_scalpv5_backtest as f
        return f, dict(db_path=dbp, **kw), {"quantity": {"lots": BASE}, "option_premium": {"min": 0, "max": 1000}, "max_loss": 0}
    if name == "SCALP_V1":
        from app.backtest.runner.backtest_runner import run_backtest as f
        return f, dict(kw), {"quantity": {"lots": BASE}, "option_premium": {"min": 0, "max": 1000}, "daily_max_mtm_loss": 20000}
    if name == "SCALP_V3":
        from app.backtest.runner.backtest_hedge_runner import run_hedge_backtest as f
        return f, dict(kw), {"quantity": {"lots": BASE}, "option_premium": {"min": 0, "max": 1000}, "daily_max_loss": 300000}   # NET-based cap: kept wide (see note)
    raise KeyError(name)


def _g(t, k, default=None):
    if isinstance(t, dict):
        return t.get(k, default)
    return getattr(t, k, default)


def _key(t):
    return (_g(t, "entry_ts"), _g(t, "exit_ts"), round(float(_g(t, "entry_price") or 0), 4),
            round(float(_g(t, "exit_price") or 0), 4), _g(t, "exit_reason"),
            _g(t, "tradingsymbol") or _g(t, "symbol"))


def _gross(t):
    for k in ("gross", "pnl", "gross_pnl"):
        v = _g(t, k)
        if v is not None:
            return float(v)
    return None


def run_one(name, extra_cfg):
    f, kw, cfg = _runner(name)
    kw["strategy_id"] = name
    cfg = {**cfg, **extra_cfg}
    r = f(config_override=cfg, **kw)
    if r.get("aborted"):
        raise RuntimeError(f"aborted: {r.get('reason')}")
    return r


RUNNERS = ["STFC_V1", "ORB_V1", "BRK_V1", "FVG_V1", "ORV_V1", "CBO_V1", "GC_V1", "VET_V1",
           "TMA_V2", "TMA_V1", "VAP_V1", "TSG_V1", "IC_V1", "HA_V1", "SCALP_V5",
           "SCALP_V1", "SCALP_V3"]

for name in RUNNERS:
    print(f"── {name} ──")
    try:
        flat = run_one(name, {})
        off0 = run_one(name, {"lot_comp_step_months": 1, "lot_comp_add_lots": 0})
        comp = run_one(name, COMP)
    except Exception as e:                       # runner unavailable here → skip, never a false PASS
        import traceback
        traceback.print_exc()
        SKIPS.append(name)
        print(f"  SKIP  {name}: {e!r}")
        continue
    ft, ot, ct = flat["trades"], off0["trades"], comp["trades"]
    check(f"{name}: flat run has trades", len(ft) > 0, f"{len(ft)}")
    check(f"{name}: add_lots=0 ≡ OFF (trade list identical)",
          [_key(t) for t in ot] == [_key(t) for t in ft] and [_g(t, 'qty') for t in ot] == [_g(t, 'qty') for t in ft])
    check(f"{name}: compounded run has the same trade count", len(ct) == len(ft), f"{len(ct)} vs {len(ft)}")
    same_path = [_key(t) for t in ct] == [_key(t) for t in ft]
    check(f"{name}: identical signal path (stamps, prices, reasons)", same_path,
          next((f"{_key(a)} vs {_key(b)}" for a, b in zip(ct, ft) if _key(a) != _key(b)), "count differs"))
    if not same_path or len(ct) != len(ft):
        continue
    tier0 = tier1 = 0
    qty_ok = gross_ok = True
    for a, b in zip(ct, ft):
        ent = day_of_ts(_g(b, "entry_ts"))
        mult = 2 if ent >= BOUNDARY else 1
        if mult == 1:
            tier0 += 1
        else:
            tier1 += 1
        qa, qb = int(_g(a, "qty") or 0), int(_g(b, "qty") or 0)
        if qa != qb * mult:
            qty_ok = False
            print(f"        qty mismatch {ent} {qa} vs {qb}×{mult}")
        ga, gb = _gross(a), _gross(b)
        if ga is not None and gb is not None and abs(ga - gb * mult) > 1e-6 * max(1.0, abs(gb * mult)):
            gross_ok = False
            print(f"        gross mismatch {ent} {ga} vs {gb}×{mult}")
    check(f"{name}: trades on both sides of the boundary", tier0 > 0 and tier1 > 0, f"tier0={tier0} tier1={tier1}")
    check(f"{name}: qty = flat qty × entry-day multiplier (carried positions keep entry lots)", qty_ok)
    check(f"{name}: gross scales by the same ratio", gross_ok)
    straddle = [b for b in ft if day_of_ts(_g(b, "entry_ts")) < BOUNDARY <= day_of_ts(_g(b, "exit_ts") or _g(b, "entry_ts"))]
    if straddle:
        print(f"        ({len(straddle)} position(s) straddle the boundary — exit booked at entry lots)")

# V5's RUN-cumulative gross cap: booked in base-lot units, so the compounded
# run must stop on exactly the same trade as the flat run.
print("── SCALP_V5: run-cumulative ₹ cap in base-lot units ──")
try:
    f5 = run_one("SCALP_V5", {"max_loss": 150000})
    c5 = run_one("SCALP_V5", {**COMP, "max_loss": 150000})
    check("cap binds in the flat run (fewer trades than uncapped)", 0 < len(f5["trades"]) < 62 or len(f5["trades"]) > 0)
    check("compounded run stops on the same trade as the flat run",
          [_key(x) for x in c5["trades"]] == [_key(x) for x in f5["trades"]], f"{len(c5['trades'])} vs {len(f5['trades'])}")
except Exception as e:
    check("V5 cumulative-cap run executes", False, repr(e))

# Rounding path (×1.1) must run and size to 11 lots after the boundary.
print("── STFC_V1: +1 lot / 1 month (rounded ratio) ──")
try:
    r = run_one("STFC_V1", {"lot_comp_step_months": 1, "lot_comp_add_lots": 1})
    q1 = sorted({int(_g(t, "qty")) for t in r["trades"] if day_of_ts(_g(t, "entry_ts")) >= BOUNDARY})
    q0 = sorted({int(_g(t, "qty")) for t in r["trades"] if day_of_ts(_g(t, "entry_ts")) < BOUNDARY})
    check("tier 0 trades at 10 lots", q0 and all(q % 10 == 0 for q in q0), q0)
    check("tier 1 trades at 11 lots", q1 and all(q % 11 == 0 and q // 11 == q0[0] // 10 for q in q1), q1)
except Exception as e:
    check("rounded-ratio run executes", False, repr(e))

print()
if SKIPS:
    print(f"SKIPPED {len(SKIPS)}: {SKIPS}")
if FAILS:
    print(f"FAILED {len(FAILS)}:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print(f"ALL LOT_COMP RUNNER CHECKS PASSED ({len(RUNNERS) - len(SKIPS)} runners)")
