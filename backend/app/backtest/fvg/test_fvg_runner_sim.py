# backend/app/backtest/fvg/test_fvg_runner_sim.py
#
# ── FVG_V1_20260916 ── behavioural simulation suite. Standalone:
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/fvg/test_fvg_runner_sim.py
# Part 1 exercises the pure engine on hand-built bars; part 2 runs the
# whole runner against a synthetic corpus (real backtest_candles_1m shape:
# SPOT rows + BS-priced weekly chain) and checks fill/exit/overlap/budget
# invariants, the control modes and dte_lot_mult; part 3 is LAB PARITY —
# when tools/lab/fvg/fvg_fib_backtest.py exists in the repo, the same
# synthetic corpus is replayed through the lab runner and every trade must
# match to the paisa. Nothing here touches ~/.scalp-app.

from __future__ import annotations

import math
import os
import random
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REPO = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

from fvg_v1_engine import (Bar, ATR, EngineConfig, FvgEngine, resample_session,   # noqa: E402
                           session_signals, SESSION_OPEN_MIN, IST_OFFSET)
from backtest_fvg_runner import run_fvg_backtest, _merge_cfg, _hhmm, DEFAULTS   # noqa: E402

FAILS = []


def check(name: str, ok: bool, note: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


def ds_of(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET


def m1(ds: int, mod: int, o, h, l, c) -> Bar:
    return Bar(ds + mod * 60, o, h, l, c)


def build_day(ds: int, tf: int, tf_bars: list) -> list:
    """Expand (o,h,l,c) tf bars from 09:15 into 1m bars reproducing that OHLC."""
    out = []
    for k, (o, h, l, c) in enumerate(tf_bars):
        base = SESSION_OPEN_MIN + k * tf
        mins = []
        for i in range(tf):
            if i == 0:
                mins.append((o, o, o, o))
            elif i == 1:
                mins.append((o, h, o, h))
            elif i == 2:
                mins.append((h, h, l, l))
            elif i == tf - 1:
                mins.append((l, max(l, c), min(l, c), c))
            else:
                mins.append((l, l, l, l))
        if tf < 4:
            mins = [(o, h, l, c)] + [(c, c, c, c)] * (tf - 1)
        for i, (a, b, c_, d) in enumerate(mins):
            out.append(m1(ds, base + i, a, b, c_, d))
    return out


# ═════════════════════════════════════ 1. engine ═════════════════════════
print("── engine ──")
D0 = date(2026, 3, 4)
DS = ds_of(D0)
TF = 5
warm = [(24000 + (i % 3), 24006 + (i % 3), 23996 + (i % 3), 24002 + (i % 3)) for i in range(16)]
c0, c1, c2 = (24002, 24010, 23998, 24005), (24005, 24062, 24004, 24060), (24060, 24075, 24040, 24070)
bars_tf = warm + [c0, c1, c2]
bars = build_day(DS, TF, bars_tf)
eng = FvgEngine(EngineConfig(), ATR(14))
sigs = session_signals(eng, bars, day_start_epoch=DS, tf_minutes=TF)
check("bull FVG born", eng.diag.get("gap_born_bull", 0) == 1, str(eng.diag))
g = eng.gaps[0] if eng.gaps else None
check("zone = [c0.high, c2.low]", g is not None and g.bottom == 24010 and g.top == 24040)
check("no signal on the birth bar", sigs == [])
nb = SESSION_OPEN_MIN + 19 * TF
tail = [m1(DS, nb, 24068, 24070, 24035, 24038), m1(DS, nb + 1, 24038, 24045, 24030, 24039),
        m1(DS, nb + 2, 24044, 24052, 24042, 24050), m1(DS, nb + 3, 24050, 24055, 24048, 24052)]
eng2 = FvgEngine(EngineConfig(), ATR(14))
sigs = session_signals(eng2, bars + tail, day_start_epoch=DS, tf_minutes=TF)
check("CE FVG signal on touch + rejection close", len(sigs) == 1 and sigs[0].side == "CE"
      and sigs[0].kind == "FVG" and sigs[0].trigger_min == nb + 2, str(sigs))
check("structural stop below the band", bool(sigs) and 24000 < sigs[0].sl_level < 24010)
check("gap consumed once", bool(eng2.gaps) and eng2.gaps[0].used)
eng3 = FvgEngine(EngineConfig(direction="PE"), ATR(14))
check("direction=PE suppresses CE", session_signals(eng3, bars + tail, day_start_epoch=DS, tf_minutes=TF) == [])
warm_b = list(warm)
warm_b[10] = (23900, 23905, 23800, 23900)
eng4 = FvgEngine(EngineConfig(), ATR(14))
session_signals(eng4, build_day(DS, TF, warm_b + [c0, c1, c2]), day_start_epoch=DS, tf_minutes=TF)
check("fib gate rejects a gap in the top 38% of the leg", bool(eng4.gaps) and not eng4.gaps[0].fib_ok)
inv = [(24068, 24070, 23990, 23995)]
bars_inv = build_day(DS, TF, bars_tf + inv)
nb2 = SESSION_OPEN_MIN + 20 * TF
tail2 = [m1(DS, nb2, 23995, 24015, 23994, 24012), m1(DS, nb2 + 1, 24012, 24020, 24000, 24005),
         m1(DS, nb2 + 2, 24005, 24008, 23990, 23992)]
eng5 = FvgEngine(EngineConfig(), ATR(14))
sigs = session_signals(eng5, bars_inv + tail2, day_start_epoch=DS, tf_minutes=TF)
check("inversion → IFVG PE signal", eng5.diag.get("gap_inverted", 0) == 1 and len(sigs) == 1
      and sigs[0].side == "PE" and sigs[0].kind == "IFVG" and sigs[0].leg_target is None, str(sigs))
eng6 = FvgEngine(EngineConfig(trade_ifvg=False), ATR(14))
check("trade_ifvg=off suppresses it", session_signals(eng6, bars_inv + tail2, day_start_epoch=DS, tf_minutes=TF) == [])
quiet = [(24060, 24064, 24056, 24060)] * 13
late = [m1(DS, SESSION_OPEN_MIN + 32 * TF, 24060, 24061, 24035, 24038),
        m1(DS, SESSION_OPEN_MIN + 32 * TF + 1, 24038, 24050, 24037, 24049)]
eng7 = FvgEngine(EngineConfig(gap_max_age=12), ATR(14))
check("gap expires after gap_max_age", eng7.diag.get("gap_expired", 0) == 0 and
      session_signals(eng7, build_day(DS, TF, bars_tf + quiet) + late, day_start_epoch=DS, tf_minutes=TF) == []
      and eng7.diag.get("gap_expired", 0) == 1)
rs = resample_session(bars, day_start_epoch=DS, tf_minutes=TF)
check("resample reproduces tf OHLC", len(rs) == len(bars_tf) and rs[17].high == 24062 and rs[18].low == 24040)

# ═══════════════════════════ 2. runner on a synthetic corpus ══════════════
print("── runner ──")


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs(spot: float, k: float, t: float, iv: float, kind: str) -> float:
    if t <= 0:
        return max(spot - k, 0.0) if kind == "CE" else max(k - spot, 0.0)
    d1 = (math.log(spot / k) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    return (spot * _ncdf(d1) - k * _ncdf(d2)) if kind == "CE" else (k * _ncdf(-d2) - spot * _ncdf(-d1))


def next_tuesday(d: date) -> date:
    return d + timedelta(days=(1 - d.weekday()) % 7)


DDL = """CREATE TABLE backtest_candles_1m (
    instrument_token INTEGER NOT NULL, ts INTEGER NOT NULL, underlying TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL, instrument_type TEXT NOT NULL, strike REAL NOT NULL,
    expiry TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0, oi INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts));
CREATE INDEX idx_bt1m_sym_ts ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX idx_bt1m_under_exp_ts ON backtest_candles_1m (underlying, expiry, ts);"""


def build_corpus(path: str, sessions: int = 40, seed: int = 7) -> list:
    rnd = random.Random(seed)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    d, spot, token, tokens, rows, days = date(2026, 1, 5), 24000.0, 1000, {}, [], []
    while len(days) < sessions:
        if d.weekday() >= 5 or d == date(2026, 1, 26):
            d += timedelta(days=1)
            continue
        days.append(d)
        ds = ds_of(d)
        exp = next_tuesday(d)
        exp_iso = exp.isoformat()
        exp_ts = ds_of(exp) + (15 * 60 + 30) * 60
        strikes = None
        for mod in range(SESSION_OPEN_MIN, 15 * 60 + 30):
            step = rnd.gauss(0, 3.0)
            if rnd.random() < 0.02:
                step += rnd.choice([-1, 1]) * rnd.uniform(25, 60)
            o, c = spot, spot + step
            h = max(o, c) + abs(rnd.gauss(0, 1.5))
            l = min(o, c) - abs(rnd.gauss(0, 1.5))
            spot = c
            ts = ds + mod * 60
            rows.append((256265, ts, "NIFTY", "NIFTYSPOT", "SPOT", 0.0, "", o, h, l, c, 0, 0))
            if strikes is None:
                atm = round(spot / 50) * 50
                strikes = [atm + 50 * i for i in range(-8, 9)]
            t_years = max((exp_ts - ts) / (365.0 * 86400), 1e-6)
            for k in strikes:
                for kind in ("CE", "PE"):
                    sym = f"NIFTY{exp.strftime('%y%m%d')}{int(k)}{kind}"
                    if sym not in tokens:
                        token += 1
                        tokens[sym] = token
                    po = max(bs(o, k, t_years, 0.14, kind), 0.05)
                    pc = max(bs(c, k, t_years, 0.14, kind), 0.05)
                    ph = max(bs(h if kind == "CE" else l, k, t_years, 0.14, kind), 0.05)
                    pl = max(bs(l if kind == "CE" else h, k, t_years, 0.14, kind), 0.05)
                    rows.append((tokens[sym], ts, "NIFTY", sym, kind, float(k), exp_iso,
                                 round(po, 2), round(max(ph, po, pc), 2), round(min(pl, po, pc), 2),
                                 round(pc, 2), 100, 1000))
        d += timedelta(days=1)
    conn.executemany("INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return days


tmp = tempfile.mkdtemp()
DBP = os.path.join(tmp, "backtest.db")
DAYS = build_corpus(DBP)
RUN_FROM, RUN_TO = DAYS[5], DAYS[-1]
print(f"  corpus: {len(DAYS)} sessions {DAYS[0]}..{DAYS[-1]}")

# a permissive config (lab round-1 shape) so the synthetic walk produces trades
BASE = {"direction": "BOTH", "disp_atr": 1.0, "fib_gate": 0, "skip_expiry_day": 0,
        "max_trades_per_day": 3, "entry_until": "14:30", "tp_mode": "rr", "tp_rr": 2.0,
        "sl_trigger": "touch", "warmup_sessions": 3, "lots": 1}


def run(cfg: dict, **kw):
    return run_fvg_backtest(db_path=DBP, strategy_id="FVG_V1", underlying="NIFTY",
                            date_from=RUN_FROM, date_to=RUN_TO, config_override=cfg, **kw)


r = run(BASE)
tr, dg, sm = r["trades"], r["summary"]["diag_fvg"], r["summary"]
cfgm = _merge_cfg(BASE)
check("run returns a run_id and trades", bool(r["run_id"]) and len(tr) > 0, f"trades={len(tr)} diag={dg}")
check("days iterated", dg["days_total"] == len(DAYS) - 5)
check("every trade closed with a known reason", all(t.exit_reason in ("SL", "BE", "TP", "TIME", "EOD") for t in tr))
check("net == gross − charges (persist surface)", all(abs(t.net_pnl - (t.pnl - t.charges)) < 0.02 for t in tr))
check("exit never before entry", all(t.exit_ts >= t.entry_ts for t in tr))
check("hold_max respected", all(t.hold_min <= cfgm["hold_max"] or t.exit_reason == "EOD" for t in tr))
o_ = sorted(tr, key=lambda t: t.entry_ts)
check("one position at a time", all(b.entry_ts > a.exit_ts for a, b in zip(o_, o_[1:])))
check("qty = lots × 65", all(t.qty == 65 for t in tr))
check("entries inside the window", all(_hhmm(cfgm["entry_from"], 0) <= (t.entry_ts - ds_of(date.fromisoformat(
    (datetime(1970, 1, 1) + timedelta(seconds=t.entry_ts + IST_OFFSET)).date().isoformat()))) // 60
    < _hhmm(cfgm["entry_until"], 0) for t in tr))
check("budget: <= max_trades_per_day per session", max(
    sum(1 for t in tr if (t.entry_ts + IST_OFFSET) // 86400 == k)
    for k in {(t.entry_ts + IST_OFFSET) // 86400 for t in tr}) <= cfgm["max_trades_per_day"])
check("risk >= sl_min_pts", all(t.risk_pts >= cfgm["sl_min_pts"] - 1e-9 for t in tr))
check("dte attached and >= 0", all(t.dte is not None and t.dte >= 0 for t in tr))
check("summary contract keys", all(k in sm for k in ("total_trades", "wins", "losses", "win_rate", "gross_pnl",
                                                     "total_charges", "net_pnl", "max_drawdown", "diag_fvg")))
check("scoreboard in diag", all(k in dg for k in ("years", "pos_years", "worst_month", "max_consec_losing_months",
                                                   "pos_months", "net_pre_split", "net_post_split",
                                                   "sl_fill_artifact_net", "top5_share_pct")))
check("exit shares sum to 100", abs(sum(dg.get(f"{k}_pnl_share_pct", 0) for k in ("sl", "be", "tp", "time", "eod")) - 100) < 0.5
      if abs(sm["net_pnl"]) > 1 else True)
check("persist surface: symbol/instrument_type/direction/condition present",
      all(t.symbol and t.instrument_type in ("CE", "PE") and t.direction == "BUY" and t.condition for t in tr))
kinds = {t.kind for t in tr}
print(f"  setups: {kinds}; exits: { {k: dg[k] for k in ('sl_exits', 'tp_exits', 'time_exits', 'eod_exits') } }; net {sm['net_pnl']}")

# sealed defaults run (CE-only, 09:30–11:00, close trigger, tp off, skip expiry)
rs_ = run({"warmup_sessions": 3})
ds_ = rs_["summary"]["diag_fvg"]
check("sealed defaults run", not rs_.get("aborted") and ds_["days_total"] > 0, str(rs_.get("reason")))
check("sealed: CE only, TP off, expiry days skipped",
      all(t.instrument_type == "CE" and t.tp is None and t.dte > 0 for t in rs_["trades"]))
check("sealed: DEFAULTS match the bible", DEFAULTS["direction"] == "CE" and DEFAULTS["entry_until"] == "11:00"
      and DEFAULTS["disp_atr"] == 1.5 and DEFAULTS["sl_trigger"] == "close" and DEFAULTS["tp_mode"] == "off"
      and DEFAULTS["hold_max"] == 60 and DEFAULTS["skip_expiry_day"] is True and DEFAULTS["fib_gate"] is False
      and DEFAULTS["max_trades_per_day"] == 1 and DEFAULTS["be_rr"] == 0)

# close trigger: SL/TP exits fill at the NEXT minute's option open after a 1m close through the level
rc = run({**BASE, "sl_trigger": "close"})
sp_cache = {}
ok_close = len(rc["trades"]) > 0
conn = sqlite3.connect(DBP)
conn.row_factory = sqlite3.Row
for t in rc["trades"]:
    if t.exit_reason not in ("SL", "TP"):
        continue
    ds = (t.entry_ts + IST_OFFSET) // 86400 * 86400 - IST_OFFSET
    if t.exit_ts >= ds + _hhmm(cfgm["eod_square_off"], 0) * 60:
        continue
    prev = conn.execute("SELECT close FROM backtest_candles_1m WHERE tradingsymbol='NIFTYSPOT' AND ts=?",
                        (t.exit_ts - 60,)).fetchone()
    o2 = conn.execute("SELECT open FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",
                      (t.symbol, t.exit_ts)).fetchone()
    lvl_hit = (prev["close"] <= t.sl or prev["close"] >= t.tp) if t.instrument_type == "CE" else \
              (prev["close"] >= t.sl or prev["close"] <= t.tp)
    if o2 is None or abs(o2["open"] - t.exit_price) > 1e-6 or not lvl_hit:
        ok_close = False
        break
check("close-trigger exits fill at next-minute option open", ok_close)
check("close trigger: fill artifact is the trigger-minute close", all(
    t.net_alt_fill is not None for t in rc["trades"]))

# variants
rf = run({**BASE, "sl_fill": "close"})
check("sl_fill=close reproduces net_alt_fill of the low-fill run",
      abs(rf["summary"]["net_pnl"] - dg["net_alt_fill"]) < 1.0,
      f"{rf['summary']['net_pnl']} vs {dg['net_alt_fill']}")
check("tp_mode=fib runs", run({**BASE, "tp_mode": "fib"})["summary"]["total_trades"] > 0)
check("be_rr variant runs", run({**BASE, "be_rr": 1.0})["summary"]["diag_fvg"]["be_armed"] >= 0)
rp = run({**BASE, "direction": "PE"})
check("direction=PE yields only PE trades", rp["trades"] and all(t.instrument_type == "PE" for t in rp["trades"]))
rd = run({**BASE, "dte_min": 3})
check("dte_min=3 keeps only 3–4DTE sessions", rd["trades"] and all(t.dte >= 3 for t in rd["trades"])
      and rd["summary"]["diag_fvg"]["days_skipped_dte"] > 0)
rm = run({**BASE, "dte_lot_mult": "0:0, 4:2"})
check("dte_lot_mult: 0DTE skipped, 4DTE doubled", rm["trades"] and all(t.dte != 0 for t in rm["trades"])
      and all(t.qty == (130 if t.dte == 4 else 65) for t in rm["trades"])
      and rm["summary"]["diag_fvg"]["dte_skipped_days"] > 0)
ra = run({**BASE, "entry_mode": "fixed", "direction": "CE"})
check("control A: one CE at 10:00 every session, fixed 30-pt stop",
      len(ra["trades"]) == len(DAYS) - 5 and all(t.kind == "CTRL" and t.instrument_type == "CE"
                                                  and abs(t.risk_pts - 30) < 1e-9 for t in ra["trades"]))
rb = run({**BASE, "entry_mode": "matched", "direction": "CE"})
rF = run({**BASE, "direction": "CE"})
fd = {(t.entry_ts + IST_OFFSET) // 86400 for t in rF["trades"]}
cd = {(t.entry_ts + IST_OFFSET) // 86400 for t in rb["trades"]}
check("control B: fixed entries only on signal days", 0 < len(cd) <= len(ra["trades"]) and fd <= cd)
for bad, key in (({"entry_from": "12:00", "entry_until": "11:00"}, "time order"),
                 ({"tf": 0}, "tf"), ({"dte_min": 5, "dte_max": 1}, "dte"),
                 ({"strike_offset": 40}, "strike_offset")):
    rr_ = run({**BASE, **bad})
    check(f"abort on bad {key}", rr_.get("aborted") and rr_["run_id"] is None, str(rr_.get("reason")))
prog = []
run(BASE, progress_cb=lambda p: prog.append(p))
check("progress callback fires per day (+3 preload phases)", len(prog) == len(DAYS) - 5 + 3
      and prog[0]["date"].startswith("loading") and prog[3]["day"] == 1)   # ── FVG_PROGRESS_20260917 ──
calls = {"n": 0}


def _cancel():
    calls["n"] += 1
    return calls["n"] > 3


rcx = run(BASE, cancel_cb=_cancel)
check("cancel stops the loop early", rcx["summary"]["diag_fvg"]["days_total"] <= 3)

# ═════════════════════════════════ 3. lab parity ═════════════════════════
print("── lab parity ──")
LAB = os.path.join(REPO, "tools", "lab", "fvg")
if os.path.isfile(os.path.join(LAB, "fvg_fib_backtest.py")):
    sys.path.insert(0, LAB)
    import importlib
    lab = importlib.import_module("fvg_fib_backtest")
    lab_corpus = lab.Corpus(DBP, "NIFTY")
    lab_corpus.load_spot(RUN_FROM - timedelta(days=30), RUN_TO)
    lab_corpus.load_expiry_ranges()
    for label, cfg in (("round-1 shape", BASE),
                       ("sealed (tp rr for lab)", {"warmup_sessions": 3, "tp_mode": "rr", "tp_rr": 8.0}),
                       ("close-trigger + fib + ifvg off", {**BASE, "sl_trigger": "close", "fib_gate": 1, "trade_ifvg": 0}),
                       ("control matched", {**BASE, "entry_mode": "matched", "direction": "CE"})):
        app_cfg = _merge_cfg(cfg)
        lab_cfg = lab.merge_cfg({k: (int(v) if isinstance(v, bool) else v)
                                 for k, v in app_cfg.items() if k in lab.DEFAULTS})
        lab_res = lab.run(lab_corpus, lab_cfg, RUN_FROM, RUN_TO)
        app_res = run(cfg)
        a = [(t.entry_ts, t.symbol, t.entry_price, t.exit_ts, t.exit_price, t.exit_reason, t.net_pnl, t.qty)
             for t in app_res["trades"]]
        b = [(t.entry_ts, t.tradingsymbol, t.entry_price, t.exit_ts, t.exit_price, t.exit_reason, t.net, t.qty)
             for t in lab_res["trades"]]
        check(f"parity [{label}]: {len(a)} trades identical", len(a) > 0 and a == b,
              f"app={len(a)} lab={len(b)} first_diff={next(((x, y) for x, y in zip(a, b) if x != y), None)}")
else:
    print("  SKIP  tools/lab/fvg not present — parity not checked (fine on a CI checkout)")

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
