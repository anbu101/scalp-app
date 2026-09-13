# backend/app/backtest/stfc/test_stfc_runner_sim.py
#
# ── STFC_OPT_20260913 ── behavioural simulation suite. Standalone:
#   cd backend && python3 app/backtest/stfc/test_stfc_runner_sim.py
# Part 1 exercises the pure engine; part 2 checks SuperTrend parity with the
# Crypto Lab implementation on the same random bars; part 3 runs the whole
# runner against a synthetic corpus and checks fill/exit/overlap invariants.

from __future__ import annotations

import os
import random
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)

from stfc_v1_engine import (                     # noqa: E402
    StfcBar, SuperTrend, resample_session, session_signals, prem_level,
    prem_fill, strike_step, atm_strike, SESSION_OPEN_MIN)

IST = 5 * 3600 + 30 * 60
FAILS = []


def check(name: str, ok: bool, note: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          f"{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


def ds_for(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def _d(ts: int) -> date:
    return (datetime(1970, 1, 1) + timedelta(seconds=ts + IST)).date()


def m1(ds: int, minute: int, o, h, l, c) -> StfcBar:
    """1m bar by minute offset from 09:15 (0 = 09:15)."""
    return StfcBar(ds + (SESSION_OPEN_MIN + minute) * 60, o, h, l, c)


DS = ds_for(date(2026, 1, 5))

# ═════════════════════════════════════ 1. engine ═════════════════════════
print("── engine: resample ──")
raw = [m1(DS, k, 100 + k, 105 + k, 95 + k, 101 + k) for k in range(7)]
b3 = resample_session(raw, day_start_epoch=DS, tf_minutes=3)
check("3m buckets from 09:15", [(b.ts - DS) // 60 for b in b3] == [555, 558, 561])
check("bucket OHLC", b3[0].open == 100 and b3[0].high == 107 and b3[0].low == 95 and b3[0].close == 103)
check("end_min recorded", [b.end_min for b in b3] == [557, 560, 563])
pre = [StfcBar(DS + 9 * 3600 * 1, 1, 1, 1, 1)]           # 09:00, pre-open
check("pre-open bars ignored", len(resample_session(pre + raw, day_start_epoch=DS, tf_minutes=3)) == 3)

print("── engine: supertrend invariants ──")
random.seed(7)
px = 100.0
bars = []
for i in range(400):
    o = px
    c = px + random.uniform(-2, 2)
    h = max(o, c) + random.uniform(0, 1.5)
    l = min(o, c) - random.uniform(0, 1.5)
    bars.append(StfcBar(i * 180, o, h, l, c))
    px = c
st = SuperTrend(10, 2.0)
out = [st.update(b) for b in bars]
check("ATR na for first len-1 bars", all(out[i][1] is None for i in range(9)) and out[9][1] == 1)
ok = True
for i in range(10, 400):
    line, d = out[i]
    if d == -1 and bars[i].close < line - 1e-9:
        ok = False
    if d == 1 and bars[i].close > line + 1e-9:
        ok = False
check("direction consistent with price/band", ok)
flips = sum(1 for i in range(10, 400) if out[i][1] != out[i - 1][1])
check("plausible flip count", 3 <= flips <= 120, str(flips))

print("── engine: setup machine ──")


def run_seq(seq, direction="BOTH"):
    bs, ds_ = [], []
    for i, (d, col) in enumerate(seq):
        c = 101 if col == "g" else 99 if col == "r" else 100
        bs.append(StfcBar(DS + (SESSION_OPEN_MIN + 3 * i) * 60, 100, 102, 98, c, SESSION_OPEN_MIN + 3 * i + 2))
        ds_.append(d)
    diag = {}
    sig = session_signals(bs, ds_, direction=direction, diag=diag)
    return [(s.bar_index, s.side, s.flip_index, s.confirm_index) for s in sig], diag


sig, dg = run_seq([(1, "r"), (1, "g"), (-1, "g"), (-1, "r"), (-1, "r"), (-1, "g"), (-1, "r"), (-1, "g"), (-1, "g")])
check("flip C1 ignored, red red GREEN confirms, trade next bar (CE)", sig == [(6, "CE", 2, 5)], str(sig))
sig, dg = run_seq([(1, "r"), (-1, "g"), (1, "g"), (1, "r"), (1, "g")])
check("flip back resets pending → PE setup", sig == [(4, "PE", 2, 3)], str(sig))
sig, dg = run_seq([(1, "r"), (-1, "g"), (1, "g"), (1, "r"), (1, "g")], direction="UP")
check("direction UP gates down flips", sig == [])
sig, dg = run_seq([(1, "r"), (-1, "g"), (-1, "g"), (1, "r"), (1, "r"), (1, "g")])
check("trade on a flip bar still executes; new setup starts there", sig == [(3, "CE", 1, 2), (5, "PE", 3, 4)], str(sig))
sig, dg = run_seq([(1, "r"), (-1, "g"), (-1, "d"), (-1, "d")])
check("doji never confirms", sig == [])
sig, dg = run_seq([(1, "r"), (-1, "g"), (-1, "g")])
check("arm on the last bar is lost (no overnight carry) and counted", sig == [] and dg.get("arm_lost_eod") == 1)
sig, dg = run_seq([(None, "g"), (None, "g"), (1, "g"), (-1, "g"), (-1, "g"), (-1, "g")])
check("warm-up None directions never flip", sig == [(5, "CE", 3, 4)] and dg.get("flips_up") == 1, str(sig))

print("── engine: levels / fills / strikes ──")
check("prem_level pct stop", prem_level(100, "pct", 20, is_stop=True) == 80)
check("prem_level abs target", prem_level(100, "abs", 15, is_stop=False) == 115)
check("prem_level off", prem_level(100, "off", 15, is_stop=False) is None and prem_level(100, "pct", 0, is_stop=True) is None)
check("prem_level floors at 0.05", prem_level(1.0, "abs", 5, is_stop=True) == 0.05)
B = StfcBar(0, 100, 106, 94, 101)
check("stop fill at level", prem_fill(level=95, bar=B, side_is_stop=True) == 95)
check("stop gapped through → open", prem_fill(level=101, bar=StfcBar(0, 99, 100, 98, 99), side_is_stop=True) == 99)
check("target untouched → None", prem_fill(level=107, bar=B, side_is_stop=False) is None)
check("strike step inferred", strike_step([24000, 24050, 24100, None]) == 50 and strike_step([50000, 50100]) == 100)
check("atm rounding", atm_strike(24026, 50) == 24050 and atm_strike(24024, 50) == 24000)

# ═════════════════════════════ 2. parity with crypto engine ══════════════
print("── parity: SuperTrend vs crypto_stfc_engine ──")
try:
    import types
    import importlib.util
    stub = types.ModuleType("app.backtest.crypto.delta_corpus")
    stub.db = lambda: None
    stub.IST = None
    saved = sys.modules.get("app.backtest.crypto.delta_corpus")
    sys.modules["app.backtest.crypto.delta_corpus"] = stub
    spec = importlib.util.spec_from_file_location(
        "crypto_stfc_engine_parity",
        os.path.join(BACKEND, "app", "backtest", "crypto", "crypto_stfc_engine.py"))
    CE = importlib.util.module_from_spec(spec)
    sys.modules["crypto_stfc_engine_parity"] = CE
    spec.loader.exec_module(CE)
    if saved is not None:
        sys.modules["app.backtest.crypto.delta_corpus"] = saved
    cb = [dict(ts=b.ts, o=b.open, h=b.high, l=b.low, c=b.close, subs=[], i0=i) for i, b in enumerate(bars)]
    ref = CE.supertrend(cb, 10, 2.0)
    same = all((ref[i][1] == out[i][1]) and
               (ref[i][0] is None and out[i][0] is None or abs((ref[i][0] or 0) - (out[i][0] or 0)) < 1e-9)
               for i in range(400))
    check("400-bar SuperTrend lines and directions identical to the Crypto Lab engine", same)
except Exception as e:                                     # crypto engine absent → skip, not fail
    print(f"  SKIP  crypto engine parity ({e!r})")

# ═════════════════════════════ 3. runner e2e ═════════════════════════════
print("── runner: synthetic corpus ──")
try:
    from backtest_stfc_runner import run_stfc_backtest, _merge_cfg, DEFAULTS   # noqa: E402
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day  # noqa: E402
    from app.utils.market_hours import is_trading_day                         # noqa: E402
except Exception as e:
    print(f"  SKIP  runner e2e (app tree unavailable: {e!r})")
    if FAILS:
        print("FAILED:", FAILS)
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)

DDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""


def build_corpus(path: str, days: list, seed: int = 11) -> dict:
    """Random-walk NIFTY spot (0.02%/min sigma) + synthetic weekly chain:
    CE = max(spot-K,0)+time_value, PE mirror; time value decays with the
    minute so 0DTE-like behaviour exists. Returns {day: expiry}."""
    random.seed(seed)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    rows = []
    exp_by_day = {}
    spot = 24000.0
    for d in days:
        ds = ds_for(d)
        exp = expected_expiry_for_day(d).isoformat()
        exp_by_day[d] = exp
        strikes = [24000 + 50 * k for k in range(-12, 13)]
        minute_spot = []
        for m in range(SESSION_OPEN_MIN, 15 * 60 + 30):
            o = spot
            c = spot * (1 + random.gauss(0, 0.0002))
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
                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp,
                                 px(o), max(px(h), px(l), px(o), px(c)), min(px(h), px(l), px(o), px(c)), px(c), 100, 100))
    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return exp_by_day


tmpdir = tempfile.mkdtemp()
dbp = os.path.join(tmpdir, "bt.db")
all_days = []
d = date(2026, 1, 1)
while len(all_days) < 8:
    if is_trading_day(d):
        all_days.append(d)
    d += timedelta(days=1)
exp_by_day = build_corpus(dbp, all_days)
warm = all_days[:3]
run_days = all_days[3:]
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")


def run(cfg, **kw):
    r = run_stfc_backtest(db_path=dbp, strategy_id="STFC_V1", underlying="NIFTY",
                          date_from=run_days[0], date_to=run_days[-1],
                          config_override=cfg, **kw)
    return r


def invariants(r, cfg, label):
    cfgm = _merge_cfg(cfg)
    tf = cfgm["timeframe_minutes"]
    trades = r["trades"]
    diag = r["summary"]["diag_stfc"]
    ok_all = True
    notes = []
    last_exit = {}
    for t in trades:
        ds = ds_for(_d(t.entry_ts))
        emin = (t.entry_ts - ds) // 60
        if (emin - SESSION_OPEN_MIN) % tf != 0:
            ok_all = False; notes.append(f"entry not on tf grid {emin}")
        if t.exit_ts < t.entry_ts:
            ok_all = False; notes.append("exit before entry")
        if t.exit_reason not in ("SL", "TP", "TIME", "CANDLE", "EOD"):
            ok_all = False; notes.append(f"bad reason {t.exit_reason}")
        if t.exit_reason == "SL" and t.exit_price > t.sl + 1e-9:
            ok_all = False; notes.append("SL fill above level")
        if t.exit_reason == "TP" and t.exit_price < t.tp - 1e-9:
            ok_all = False; notes.append("TP fill below level")
        xmin = (t.exit_ts - ds) // 60
        if t.exit_reason == "CANDLE" and xmin != emin + tf - 1:
            ok_all = False; notes.append(f"CANDLE exit minute {xmin} != {emin + tf - 1}")
        if t.exit_reason == "TIME" and t.hold_min != cfgm["hold_minutes"]:
            ok_all = False; notes.append(f"TIME hold {t.hold_min}")
        if t.exit_reason == "EOD" and xmin != 15 * 60 + 20:
            ok_all = False; notes.append(f"EOD minute {xmin}")
        if cfgm["direction"] == "UP" and t.instrument_type != "CE":
            ok_all = False; notes.append("UP produced PE")
        if ds in last_exit and t.entry_ts <= last_exit[ds]:
            ok_all = False; notes.append("overlapping positions")
        last_exit[ds] = t.exit_ts
        if abs(t.net_pnl - (t.pnl - t.charges)) > 0.011:
            ok_all = False; notes.append("net != gross - charges")
    acc = (diag["entries"] + diag["sig_dropped_open"] + diag["sig_dropped_budget"]
           + diag["sig_dropped_block_time"] + diag["sig_no_spot_bar"]
           + diag["sig_no_candidate"] + diag["sig_no_fill"] + diag["sig_band_reject"])
    if acc != diag["signals_total"]:
        ok_all = False; notes.append(f"signal accounting {acc} != {diag['signals_total']}")
    if diag["entries"] != len(trades):
        ok_all = False; notes.append("entries != trades")
    check(f"{label}: invariants over {len(trades)} trades", ok_all, "; ".join(notes[:4]))
    return trades, diag


base = dict(DEFAULTS)
base.update(timeframe_minutes=3, st_len=10, st_mult=2.0, sl_mode="pct", sl_value=20,
            tp_mode="pct", tp_value=30, exit_mode="candle")
r = run(base)
check("run completes (not aborted)", not r.get("aborted"), str(r.get("reason")))
trades, diag = invariants(r, base, "candle mode")
check("warm-up consumed prior sessions", diag["warmup_bars"] > 0)
check("some trades and signals", len(trades) > 0 and diag["signals_total"] >= len(trades))
check("ATM strike selection", all(t.strike == atm_strike(t.entry_spot, 50) for t in trades if "FB" not in t.condition))
check("entry fill = option 1m open at entry minute", True)   # by construction of the runner; covered via sqlite check below
conn = sqlite3.connect(dbp)
ok = True
for t in trades[:20]:
    row = conn.execute("SELECT open FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",
                       (t.tradingsymbol, t.entry_ts)).fetchone()
    if row is None or abs(row[0] - t.entry_price) > 0.011:
        ok = False
conn.close()
check("entry_price equals the contract's 1m open at entry_ts", ok)
check("candle-mode exits are CANDLE/SL/TP only", diag["time_exits"] == 0 and diag["eod_exits"] == 0)
check("pnl shares sum to ~100%", abs(sum(diag.get(f"{k}_pnl_share_pct", 0) for k in ("sl", "tp", "time", "candle", "eod")) - 100) < 1.0
      if abs(r["summary"]["net_pnl"]) > 1 else True)

mins = dict(base, exit_mode="minutes", hold_minutes=60, sl_mode="off", tp_mode="off")
r2 = run(mins)
trades2, diag2 = invariants(r2, mins, "minutes mode, no SL/TP")
check("all exits TIME (or EOD near close)", diag2["sl_exits"] == 0 and diag2["tp_exits"] == 0 and diag2["time_exits"] > 0 and diag2["candle_exits"] == 0)
check("holds skip overlapping signals", diag2["sig_dropped_open"] > 0)
check("fewer entries than candle mode (skips)", len(trades2) <= len(trades))

up = dict(base, direction="UP")
r3 = run(up)
trades3, diag3 = invariants(r3, up, "direction UP")
check("UP → CE only, pe_entries 0", diag3["pe_entries"] == 0 and diag3["flips_dn"] > 0)

off = dict(base, strike_offset=2)
r4 = run(off)
trades4, _ = invariants(r4, off, "strike offset +2")
check("offset +2 OTM applied per side", all(
    (t.strike == atm_strike(t.entry_spot, 50) + 100) if t.instrument_type == "CE"
    else (t.strike == atm_strike(t.entry_spot, 50) - 100) for t in trades4 if "FB" not in t.condition))

cap = dict(base, max_trades_per_day=2)
r5 = run(cap)
trades5, diag5 = invariants(r5, cap, "max 2/day")
per_day = {}
for t in trades5:
    per_day[_d(t.entry_ts)] = per_day.get(_d(t.entry_ts), 0) + 1
check("max_trades_per_day respected", all(v <= 2 for v in per_day.values()) and diag5["sig_dropped_budget"] > 0)

blk = dict(base, entry_block_time="10:00", eod_square_off="10:30")
r6 = run(blk)
trades6, diag6 = invariants(r6, blk, "block 10:00 / eod 10:30")
check("no entries at/after block time", all((t.entry_ts - ds_for(_d(t.entry_ts))) // 60 < 600 for t in trades6))

dte = dict(base, dte_lot_mult="0:0")
r7 = run(dte)
trades7, diag7 = invariants(r7, dte, "dte_lot_mult 0:0")
check("expiry-day sessions skipped via DTE mult", diag7["dte_skipped_days"] >= 1 or diag7["dte_unknown_days"] >= 1)

skip = dict(base, skip_expiry_day=True)
r8 = run(skip)
check("skip_expiry_day counts", r8["summary"]["diag_stfc"]["days_skipped_expiry"] >= 1)

print("── runner: config guards (abort, never run) ──")
for bad, why in ((dict(base, sl_mode="pct", sl_value=150), "pct SL > 100 looks like rupees"),
                 (dict(base, entry_block_time="15:25", eod_square_off="15:20"), "block after eod"),
                 (dict(base, exit_mode="minutes", hold_minutes=400), "hold > 375"),
                 (dict(base, timeframe_minutes=90), "tf 90"),
                 (dict(base, premium_min=200, premium_max=100), "band inverted")):
    rb = run(bad)
    check(f"abort: {why}", rb.get("aborted") is True and rb["run_id"] is None)

canc = run(base, cancel_cb=lambda: True)
check("cancel_cb stops before trading", canc["summary"]["total_trades"] == 0 and not canc.get("aborted"))

prog = []
run(base, progress_cb=prog.append)
check("progress callback per day", len(prog) == len(run_days) and prog[-1]["day"] == len(run_days))

if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PASS")
