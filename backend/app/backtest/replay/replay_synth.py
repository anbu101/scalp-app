# backend/app/backtest/replay/replay_synth.py
#
# ── TRADE_REPLAY_20260928 ── deterministic synthetic corpus for the replay
# suite: NIFTY SPOT 1m (trending + chopping regimes so every strategy has
# signals), a weekly ATM±N option chain priced with Black-76 on the
# expected-expiry calendar the runners use, and BANKNIFTYFUT 1m. Test-only.

from __future__ import annotations

import math
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

IST = timezone(timedelta(hours=5, minutes=30))
MONTH_CODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
              10: "O", 11: "N", 12: "D"}


def ds_of(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(spot: float, k: float, t_years: float, iv: float, call: bool) -> float:
    t = max(t_years, 1.0 / (365 * 24 * 60))
    v = iv * math.sqrt(t)
    d1 = (math.log(spot / k) + 0.5 * v * v) / v
    d2 = d1 - v
    if call:
        return spot * _ncdf(d1) - k * _ncdf(d2)
    return k * _ncdf(-d2) - spot * _ncdf(-d1)


def trading_days(d0: date, d1: date) -> List[date]:
    from app.utils.market_hours import is_trading_day
    out, d = [], d0
    while d <= d1:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def build(db_path: str, d0: date = date(2025, 1, 1), d1: date = date(2025, 2, 28),
          strikes_each_side: int = 6, seed: int = 20260928, with_fut: bool = True) -> List[date]:
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    schema = (Path(__file__).resolve().parents[1] / "repo" / "schema.sql").read_text()
    c = sqlite3.connect(db_path)
    c.executescript(schema)
    for col, ddl in (("condition", "TEXT"), ("synthetic", "INTEGER NOT NULL DEFAULT 0"), ("synth_kind", "TEXT")):
        try:
            c.execute(f"ALTER TABLE backtest_trades ADD COLUMN {col} {ddl}")
        except Exception:
            pass
    rnd = random.Random(seed)
    days = trading_days(d0, d1)
    spot = 23500.0
    fut = 49500.0
    token = [100000]
    tokens = {}
    rows = []
    regime_len, drift = 0, 0.0
    for di, d in enumerate(days):
        ds = ds_of(d)
        gap = rnd.gauss(0, 35)
        spot += gap
        exp = expected_expiry_for_day(d)
        exp_close = ds_of(exp) + (15 * 60 + 30) * 60
        atm = round(spot / 50) * 50
        strikes = [atm + 50 * i for i in range(-strikes_each_side, strikes_each_side + 1)]
        path = []
        for m in range(375):
            if regime_len <= 0:
                regime_len = rnd.randint(25, 90)
                drift = rnd.choice([0.0, 0.0, 1.1, -1.1, 1.8, -1.8])
            regime_len -= 1
            o = spot
            step = drift + rnd.gauss(0, 4.2)
            if rnd.random() < 0.012:          # displacement bars (FVG fodder)
                step += rnd.choice([-1, 1]) * rnd.uniform(18, 35)
            cl = o + step
            hi = max(o, cl) + abs(rnd.gauss(0, 2.2))
            lo = min(o, cl) - abs(rnd.gauss(0, 2.2))
            spot = cl
            path.append((ds + (555 + m) * 60, o, hi, lo, cl))
        for ts, o, hi, lo, cl in path:
            rows.append((1, ts, "NIFTY", "NIFTY 50", "SPOT", 0.0, "", round(o, 2), round(hi, 2),
                         round(lo, 2), round(cl, 2), 0, 0))
        if with_fut:
            for ts, o, hi, lo, cl in path:
                f = 2.1
                rows.append((2, ts, "BANKNIFTY", "BANKNIFTYFUT", "FUT", 0.0, "", round(o * f, 2),
                             round(hi * f, 2), round(lo * f, 2), round(cl * f, 2), 1000, 0))
        yy = exp.year % 100
        for k in strikes:
            for typ in ("CE", "PE"):
                sym = f"NIFTY{yy}{MONTH_CODE[exp.month]}{exp.day:02d}{int(k)}{typ}"
                tok = tokens.get(sym)
                if tok is None:
                    token[0] += 1
                    tok = tokens[sym] = token[0]
                prev = None
                for ts, o, hi, lo, cl in path:
                    t = max(exp_close - ts - 60, 60) / (365.0 * 86400)
                    iv = 0.13 + 0.02 * math.sin(di / 3.0)
                    call = typ == "CE"
                    po = bs(o, k, t + 60 / (365.0 * 86400), iv, call)
                    pc = bs(cl, k, t, iv, call)
                    ph = bs(hi if call else lo, k, t, iv, call)
                    pl = bs(lo if call else hi, k, t, iv, call)
                    po, pc = max(po, 0.05), max(pc, 0.05)
                    ph, pl = max(ph, po, pc), max(min(pl, po, pc), 0.05)
                    vol = rnd.randint(50, 5000) if rnd.random() > 0.05 else 0
                    rows.append((tok, ts, "NIFTY", sym, typ, float(k), exp.isoformat(), round(po, 2),
                                 round(ph, 2), round(pl, 2), round(pc, 2), vol, 0))
                    prev = pc
        if len(rows) > 200000:
            c.executemany("INSERT OR REPLACE INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            rows = []
    if rows:
        c.executemany("INSERT OR REPLACE INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return days
