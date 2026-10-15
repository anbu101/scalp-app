# backend/app/engine/tsg/test_tsg_sl_per_lot.py — fence TSG_SL_PER_LOT_20261015
#
# TSG_V1: MTM SL entered PER LOT (Settings + Backtest).
#
#   1. resolve_mtm_sl (live, pure)       per-lot × day lots; legacy total × ratio
#   2. loader migration (real loader)    old file → per-lot derived once, mirror
#   3. TsgManager._enter_day_impl        the day's core.mtm_sl on normal and
#                                        expiry days (expiry_lots), legacy file
#   4. backtest runner on a synthetic corpus (never touches backtest.db):
#        legacy config ≡ ORIGINAL runner (.bak) row-for-row
#        per-lot × lots ≡ legacy total at the same lots (10 and 5 lots)
#        DTE × lots: scaled days stop at per-lot × scaled lots
#        lot compounding: per-lot ≡ legacy scaled; 'fixed ₹' does not freeze it
#        per-lot 0 wins over a stale legacy total (SL off)
#        parallel shards ≡ serial
#   5. Trade Replay overlay level from the trade's own SELL lots
#
# Run: cd backend && python3 app/engine/tsg/test_tsg_sl_per_lot.py

from __future__ import annotations

import importlib.util
import json
import os
import random
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[3])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

FENCE = "TSG_SL_PER_LOT_20261015"
FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


# ═════════════════════════════════════════════════════════════════════
# 1. resolve_mtm_sl
# ═════════════════════════════════════════════════════════════════════
def t_resolve():
    print("── 1. resolve_mtm_sl ──")
    from app.engine.tsg.tsg_live_core import resolve_mtm_sl as R
    check("per-lot 3500 × 10 lots = 35,000", R({"mtm_sl_per_lot": 3500}, 10, 10) == 35000)
    check("per-lot 3500 × 12 expiry lots = 42,000 (no ratio needed)", R({"mtm_sl_per_lot": 3500}, 12, 10) == 42000)
    check("per-lot 3500 × 1 lot = 3,500", R({"mtm_sl_per_lot": 3500, "mtm_sl": 99999}, 1, 1) == 3500)
    check("per-lot wins over a stale legacy total", R({"mtm_sl_per_lot": 3000, "mtm_sl": 35000}, 10, 10) == 30000)
    check("per-lot 0 = SL off even with a legacy total present", R({"mtm_sl_per_lot": 0, "mtm_sl": 35000}, 10, 10) == 0)
    check("negative per-lot is sign-tolerant", R({"mtm_sl_per_lot": -2500}, 4, 4) == 10000)
    check("legacy (no per-lot key): total × day/base (LD5a unchanged)", R({"mtm_sl": 35000}, 15, 10) == 52500)
    check("legacy: blank per-lot string counts as absent", R({"mtm_sl_per_lot": "", "mtm_sl": 35000}, 10, 10) == 35000)
    check("legacy default when nothing set = 35,000 × ratio", R({}, 10, 10) == 35000)


# ═════════════════════════════════════════════════════════════════════
# 2. loader migration
# ═════════════════════════════════════════════════════════════════════
def t_loader():
    print("── 2. strategy_loader migration ──")
    import app.config.strategy_loader as L
    saved_dir = L.STRATEGY_DIR
    with tempfile.TemporaryDirectory() as td:
        L.STRATEGY_DIR = Path(td)
        p = Path(td) / "TSG_V1.json"
        try:
            def load(obj):
                if obj is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_text(json.dumps(obj))
                return L.load_strategy_config("TSG_V1")

            c = load({"mtm_sl": 35000, "lots": 10, "trade_execution_mode": "LIVE"})
            check("old file 35,000 @ 10 lots → 3,500/lot, mirror 35,000",
                  c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 35000, c)
            check("migration is read-only: file on disk untouched",
                  json.loads(p.read_text()) == {"mtm_sl": 35000, "lots": 10, "trade_execution_mode": "LIVE"})
            c = load({"mtm_sl": 3500, "lots": 1})
            check("old file 3,500 @ 1 lot → 3,500/lot", c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 3500, c)
            c = load({"mtm_sl": 35000, "lots": 12})
            check("non-integer division rounds to paise, base-day stop within ₹1",
                  c["mtm_sl_per_lot"] == 2916.67 and abs(c["mtm_sl"] - 35000) < 1, c)
            c = load({"mtm_sl_per_lot": 3000, "mtm_sl": 35000, "lots": 10})
            check("saved per-lot wins; mirror recomputed (30,000)", c["mtm_sl_per_lot"] == 3000 and c["mtm_sl"] == 30000, c)
            c = load({"mtm_sl_per_lot": 3500, "lots": 5})
            check("lots changed elsewhere → mirror follows (17,500)", c["mtm_sl"] == 17500, c)
            c = load({"mtm_sl_per_lot": 0, "mtm_sl": 35000, "lots": 10})
            check("per-lot 0 → SL off, mirror 0", c["mtm_sl_per_lot"] == 0 and c["mtm_sl"] == 0, c)
            c = load({"lots": 10})
            check("file with neither key → default 3,500/lot", c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 35000, c)
            c = load(None)
            check("first run (no file) → seeded default carries per-lot 3,500",
                  c["mtm_sl_per_lot"] == 3500 and json.loads(p.read_text()).get("mtm_sl_per_lot") == 3500, c)
            # Settings save path: POST merges into the loaded (migrated) config
            p.write_text(json.dumps({"mtm_sl": 35000, "lots": 10}))
            cur = L.load_strategy_config("TSG_V1")
            cur.update({"lots": 6})                       # a lots-only POST
            L.save_strategy_config("TSG_V1", cur)
            c = L.load_strategy_config("TSG_V1")
            check("lots-only save keeps 3,500/lot → 6 lots = 21,000",
                  c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 21000, c)
            other = L.load_strategy_config("TMA_V2")
            check("other strategies untouched (no per-lot key injected)", "mtm_sl_per_lot" not in other)
        finally:
            L.STRATEGY_DIR = saved_dir


# ═════════════════════════════════════════════════════════════════════
# 3. TsgManager entry
# ═════════════════════════════════════════════════════════════════════
def t_manager():
    print("── 3. TsgManager._enter_day_impl ──")
    import threading
    import app.engine.tsg.tsg_manager as TM
    TM.is_day_blocked = lambda sid: False
    TM.check_strategy_max_loss = lambda sid: False
    today = TM._now_ist().date()

    def entered(cfg, expiry):
        m = TM.TsgManager.__new__(TM.TsgManager)
        m.executor, m._quote_fn, m._lock = None, None, threading.RLock()
        m._core, m._paper, m._entry_date, m._expiry_iso = None, True, None, ""
        m._token_by_sym, m._sibling, m._paper_row_ids, m._live_row_ids = {}, {}, {}, {}
        m._group_id, m._lot_size = None, 65
        m._cfg = lambda: dict(cfg, min_entry_iv=0)
        m._alert = lambda *a, **k: None
        m._persist = lambda: None
        m._enter_paper = lambda planned, px: False           # stop before any row / notify
        rows, ltp = [], {}
        for k, (ce, pe) in enumerate([(120, 20), (80, 30), (40, 60), (4, 110)]):
            for it, px in (("CE", ce), ("PE", pe)):
                s = f"NIFTYX{24000 + 50 * k}{it}"
                rows.append({"tradingsymbol": s, "instrument_type": it, "strike": 24000 + 50 * k,
                             "expiry": expiry, "instrument_token": k})
                ltp[s] = px
        m._enter_day_impl((expiry, rows, ltp), mode="PAPER")
        return m._core

    nxt = (today + timedelta(days=3)).isoformat()
    tdy = today.isoformat()
    c = entered({"lots": 10, "expiry_lots": 12, "mtm_sl_per_lot": 3500}, nxt)
    check("normal day: 3,500/lot × 10 = 35,000", c is not None and c.mtm_sl == 35000, getattr(c, "mtm_sl", None))
    check("normal day: leg qty 650", c is not None and all(l.qty == 650 for l in c.legs.values()))
    c = entered({"lots": 10, "expiry_lots": 12, "mtm_sl_per_lot": 3500}, tdy)
    check("expiry day: 3,500/lot × 12 = 42,000", c is not None and c.mtm_sl == 42000, getattr(c, "mtm_sl", None))
    c = entered({"lots": 3, "expiry_lots": 0, "mtm_sl_per_lot": 3500}, nxt)
    check("3 lots: 10,500 — set once, follows lots", c is not None and c.mtm_sl == 10500, getattr(c, "mtm_sl", None))
    c = entered({"lots": 10, "expiry_lots": 15, "mtm_sl": 35000}, tdy)
    check("legacy config (no per-lot): unchanged LD5a 52,500", c is not None and c.mtm_sl == 52500, getattr(c, "mtm_sl", None))
    c = entered({"lots": 10, "mtm_sl_per_lot": 3500, "mtm_target": 20000, "expiry_lots": 12}, tdy)
    check("target still ratio-scaled (20,000 × 1.2 = 24,000)", c is not None and c.mtm_target == 24000)
    check("hard stop rides on the per-lot SL (42,000 × 1.05)",
          c is not None and abs(c.hard_stop_level() - 44100) < 1e-6, c.hard_stop_level() if c else None)


# ═════════════════════════════════════════════════════════════════════
# 4. backtest runner — synthetic corpus
# ═════════════════════════════════════════════════════════════════════
IST = 5 * 3600 + 30 * 60
DDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""


def _ds(d):
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST


def _day(ts):
    return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST)).date()


def build_corpus(path, days, seed=7):
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    random.seed(seed)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    rows, spot, open_m = [], 24000.0, 9 * 60 + 15
    for d in days:
        ds = _ds(d)
        exp = expected_expiry_for_day(d).isoformat()
        atm = int(round(spot / 50.0)) * 50
        ms = []
        # half the days quiet (theta wins → EOD), half trending (the SL trips)
        drift = 0.0 if random.random() < 0.5 else random.choice((-1, 1)) * random.uniform(0.0002, 0.0005)
        for m in range(open_m, 15 * 60 + 30):
            o = spot
            c = spot * (1 + drift + random.gauss(0, 0.0003))
            ts = ds + m * 60
            rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", None, None, o, max(o, c), min(o, c), c, 0, 0))
            ms.append((ts, o, c, m))
            spot = c
        for K in [atm + 50 * k for k in range(-12, 13)]:
            for side in ("CE", "PE"):
                sym = f"NIFTY{exp.replace('-', '')}{K}{side}"
                for (ts, o, c, m) in ms:
                    tv = 60.0 * (1 - (m - open_m) / 800.0)
                    px = (lambda s: round(max(s - K, 0) + tv, 2)) if side == "CE" else (lambda s: round(max(K - s, 0) + tv, 2))
                    a, b = px(o), px(c)
                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp, a, max(a, b), min(a, b), b, 100, 100))
    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def legs(n, hedge=None):
    h = n if hedge is None else hedge
    return [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": n, "premium_max": 85},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": n, "premium_max": 85},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": h, "premium_max": 30},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": h, "premium_max": 30}]


def key(t):
    return (t.entry_ts, t.exit_ts, t.condition, t.qty, round(t.entry_price, 4),
            round(t.exit_price, 4), t.exit_reason)


def t_backtest():
    print("── 4. backtest runner (synthetic corpus) ──")
    from app.utils.market_hours import is_trading_day
    import app.backtest.data.candle_source as _cs
    from app.backtest.tsg.backtest_tsg_runner import run_tsg_backtest as NEW
    bak = Path(HERE.parents[2] / "backtest" / "tsg" / f"backtest_tsg_runner.py.bak-{FENCE}")
    ORIG = None
    if bak.exists():
        from importlib.machinery import SourceFileLoader   # .bak has no .py suffix
        ldr = SourceFileLoader("tsg_runner_orig", str(bak))
        spec = importlib.util.spec_from_loader("tsg_runner_orig", ldr)
        mod = importlib.util.module_from_spec(spec)
        ldr.exec_module(mod)
        ORIG = mod.run_tsg_backtest

    td = tempfile.mkdtemp()
    dbp = os.path.join(td, "bt.db")
    days, d = [], date(2026, 1, 20)
    while len(days) < 32:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    build_corpus(dbp, days)
    _cs._backtest_db_path = lambda: Path(dbp)
    D0, D1 = days[2], days[-1]

    def run(fn, cfg):
        base = {"entry_time": "09:16", "exit_time": "15:25", "mtm_target": 0}
        r = fn(db_path=dbp, strategy_id="TSG_V1", underlying="NIFTY", date_from=D0, date_to=D1,
               config_override={**base, **cfg})
        assert not r.get("aborted"), r.get("reason")
        return r

    def K(r):
        return sorted(key(t) for t in r["trades"])

    def sl_days(r):
        return {_day(t.entry_ts) for t in r["trades"] if t.exit_reason == "MTM_SL"}

    leg10 = run(NEW, {"legs": legs(10), "mtm_sl": 3000})
    n_sl = len(sl_days(leg10))
    n_days = len({_day(t.entry_ts) for t in leg10["trades"]})
    check(f"corpus has both SL and non-SL days ({n_sl} MTM_SL of {n_days})", 3 <= n_sl < n_days)
    if ORIG:
        o = run(ORIG, {"legs": legs(10), "mtm_sl": 3000})
        check("legacy config ≡ ORIGINAL runner row-for-row", K(o) == K(leg10))
        check("legacy summary net identical", o["summary"]["net_pnl"] == leg10["summary"]["net_pnl"])
        check("legacy diag keys identical (no per-lot key leaks)",
              set(o["summary"].get("diag_tsg", {})) == set(leg10["summary"].get("diag_tsg", {})))
    else:
        print("  ! .bak not found — ORIGINAL comparison skipped")

    pl10 = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300})
    check("per-lot 300 × 10 lots ≡ legacy 3,000 @ 10 (row-for-row)", K(pl10) == K(leg10))
    check("per-lot diag carries mtm_sl_per_lot 300 and base mtm_sl 3,000",
          pl10["summary"].get("diag_tsg", {}).get("mtm_sl_per_lot") == 300
          and pl10["summary"].get("diag_tsg", {}).get("mtm_sl") == 3000, pl10["summary"].get("diag_tsg", {}).get("mtm_sl"))
    pl5 = run(NEW, {"legs": legs(5), "mtm_sl_per_lot": 300})
    lg5 = run(NEW, {"legs": legs(5), "mtm_sl": 1500})
    check("per-lot 300 × 5 lots ≡ legacy 1,500 @ 5", K(pl5) == K(lg5))
    plh = run(NEW, {"legs": legs(5, hedge=10), "mtm_sl_per_lot": 300})
    lgh = run(NEW, {"legs": legs(5, hedge=10), "mtm_sl": 1500})
    check("lots counted on the SELL legs (5 short / 10 wing → 1,500)", K(plh) == K(lgh))
    check("per-lot 0 beats a stale legacy total → no MTM_SL exits",
          not sl_days(run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 0, "mtm_sl": 3000})))
    check("legacy 'mtm_sl' ignored when per-lot present",
          K(run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "mtm_sl": 999999})) == K(pl10))

    # DTE × lots: every DTE doubled ≡ a 20-lot book with a 6,000 stop
    all2 = "0:2,1:2,2:2,3:2,4:2,5:2,6:2"
    plx2 = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "dte_lot_mult": all2})
    lg20 = run(NEW, {"legs": legs(20), "mtm_sl": 6000})
    check("DTE ×2 on every day ≡ 20 lots @ 6,000 (SL follows the scaled lots)", K(plx2) == K(lg20))
    lgx2 = run(NEW, {"legs": legs(10), "mtm_sl": 3000, "dte_lot_mult": all2})
    check("…whereas the legacy total does NOT follow DTE scaling (the old trap)", K(lgx2) != K(lg20))
    # mixed: only some DTEs scaled — each day must match the book of its own size
    mix = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "dte_lot_mult": "0:2,2:2"})
    big = {_day(t.entry_ts) for t in mix["trades"] if t.qty == 20 * 65}
    small = {_day(t.entry_ts) for t in mix["trades"] if t.qty == 10 * 65}
    want = sorted([key(t) for t in lg20["trades"] if _day(t.entry_ts) in big]
                  + [key(t) for t in leg10["trades"] if _day(t.entry_ts) in small])
    check(f"mixed DTE (0:2, 2:2): {len(big)} scaled + {len(small)} base days each stop at their own per-lot level",
          big and small and K(mix) == want)

    # lot compounding (calendar, ×2 after one month)
    COMP = {"lot_comp_step_months": 1, "lot_comp_add_lots": 10}
    a = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, **COMP})
    b = run(NEW, {"legs": legs(10), "mtm_sl": 3000, **COMP})
    check("compounding: per-lot ≡ legacy ratio-scaled total", K(a) == K(b) and any(t.qty == 1300 for t in a["trades"]))
    c = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, **COMP, "lot_comp_scale_rs": "fixed"})
    check("compounding 'fixed ₹' does not freeze a per-lot SL", K(c) == K(a))

    # parallel shards
    p2 = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "dte_lot_mult": "0:2,2:2", "parallel_workers": 2})
    check("parallel ×2 ≡ serial (per-lot + DTE)", K(p2) == K(mix))
    check("parallel diag carries mtm_sl_per_lot", p2["summary"].get("diag_tsg", {}).get("mtm_sl_per_lot") == 300)
    return mix


# ═════════════════════════════════════════════════════════════════════
# 5. Trade Replay overlay
# ═════════════════════════════════════════════════════════════════════
def t_overlay(mix):
    print("── 5. Trade Replay overlay ──")
    from types import SimpleNamespace
    import app.backtest.replay.overlays as OV

    def lv(cfg, n):
        rows = [{"entry_ts": 1769485560, "qty": n * 65, "direction": a, "condition": f"TSG·L{i}·{a}"}
                for i, a in ((1, "SELL"), (2, "SELL"), (3, "BUY"), (4, "BUY"))]
        ctx = SimpleNamespace(cfg=cfg, legs=rows, underlying="NIFTY", run_from=None)
        out = OV._tsg(ctx)
        return {x["label"]: x["value"] for x in out["mtm_levels"]}, out

    v, out = lv({"legs": legs(10), "mtm_sl_per_lot": 300, "mtm_sl_hard_mult": 1.05}, 20)
    check("per-lot overlay uses the trade's own SELL lots (20 → −6,000)", -6000 in v.values(), v)
    check("hard-stop line on the same base (−6,300)", any(abs(x + 6300) < 1e-6 for x in v.values()), v)
    check("config brief says ₹/lot", "/lot" in out["config_brief"], out["config_brief"])
    v, _ = lv({"legs": legs(10), "mtm_sl": 3000}, 10)
    check("legacy overlay unchanged (−3,000)", -3000 in v.values(), v)


if __name__ == "__main__":
    t_resolve()
    t_loader()
    t_manager()
    mix = t_backtest()
    t_overlay(mix)
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} TSG PER-LOT SL CHECKS PASSED")
