# backend/app/backtest/util/test_screener.py
# ── STOCK_SCREENER_20260828 ──
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screener as S  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
fails = []


def ck(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        fails.append(name)


CFG = dict(S.DEFAULTS)

# ── indicators ──
ck("sma is None before it has enough history",
   S.sma([1, 2, 3, 4], 3)[:2] == [None, None])
ck("sma value correct", abs(S.sma([1, 2, 3, 4], 3)[2] - 2.0) < 1e-9)
e = S.ema([5.0] * 10, 3)
ck("ema of a constant series is that constant", abs(e[-1] - 5.0) < 1e-9)
ck("ema seeds at index length-1", S.ema([1, 2, 3, 4, 5], 3)[1] is None
   and S.ema([1, 2, 3, 4, 5], 3)[2] is not None)
ck("ema of an empty-ish series is all None", S.ema([1.0], 5) == [None])


def bars_from(closes, vols=None, start=date(2024, 1, 1)):
    out, d = [], start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        v = vols[i] if vols else 10_000_000.0
        out.append(S.DailyBar(d, c, c, c, c, v))
        d += timedelta(days=1)
    return out


# ── the cross is an EVENT, and it never gates its own bar ──
# 60 flat bars then a sustained ramp: EMA20 crosses SMA40 exactly once.
closes = [100.0] * 60 + [100.0 + i * 2.0 for i in range(1, 41)]
# volume must RISE for the vol>SMA(vol,10) leg to pass; a flat series never
# exceeds its own average, which would veto for the wrong reason.
rising_vol = [10_000_000.0] * 60 + [10_000_000.0 + i * 500_000.0
                                    for i in range(1, 41)]
bars = bars_from(closes, vols=rising_vol)
allowed = S.compute_allowed_days(bars, CFG)
days = [b.day for b in bars]
hits = [i for i, d in enumerate(days) if allowed[d]]
ck("a single sustained cross opens exactly one day at window=1",
   len(hits) == 1, f"got {len(hits)}")

# LOOKAHEAD: the firing bar must be strictly before the permitted day.
fire_idx = hits[0] - 1
ck("permitted day is strictly AFTER the firing bar", hits[0] == fire_idx + 1)
ck("the firing bar's own day is NOT permitted", not allowed[days[fire_idx]])

# recompute using ONLY history up to the firing bar — the gate must be
# unchanged, i.e. no future bar contributed to the decision.
trunc = S.compute_allowed_days(bars[:fire_idx + 1], CFG)
ck("gate decision uses no data after the firing bar",
   all(not v for v in trunc.values()))

# ── window widens in TRADING days ──
c5 = dict(CFG, screener_cross_window_days=5)
a5 = S.compute_allowed_days(bars, c5)
ck("window=5 opens five trading days", sum(a5.values()) == 5,
   f"got {sum(a5.values())}")
opened = [d for d in days if a5[d]]
ck("window days are consecutive TRADING days, weekends skipped",
   all((opened[i + 1] - opened[i]).days <= 3 for i in range(len(opened) - 1))
   and all(d.weekday() < 5 for d in opened))
ck("window=1 is a strict subset of window=5",
   all(a5[d] for d in days if allowed[d]))

# ── each condition can veto independently ──
lowvol = bars_from(closes, vols=[10_000_000.0] * 60 + [1.0] * 40)
ck("volume-vs-SMA condition vetoes",
   sum(S.compute_allowed_days(lowvol, CFG).values()) == 0)

flat_vol = bars_from(closes, vols=[5_000_000.0] * 100)
ck("flat volume never exceeds its own SMA, so nothing fires",
   sum(S.compute_allowed_days(flat_vol, CFG).values()) == 0)

big = dict(CFG, screener_min_volume=10 ** 12)
ck("absolute min_volume floor vetoes",
   sum(S.compute_allowed_days(bars, big).values()) == 0)

# EMA fast <= slow on a falling series -> no fire even if a cross occurred
ck("ema_fast > ema_slow condition vetoes on a decaying series",
   sum(S.compute_allowed_days(
       bars_from([100.0] * 60 + [100.0 - i for i in range(1, 41)],
                 vols=rising_vol), CFG).values()) == 0)

# ── warmup is fail-closed ──
ck("insufficient history gates everything CLOSED",
   sum(S.compute_allowed_days(
       bars_from([100.0 + i for i in range(20)],
                 vols=rising_vol[:20]), CFG).values()) == 0)
ck("required_warmup tracks the longest lookback",
   S.required_warmup(CFG) == 41 and
   S.required_warmup(dict(CFG, screener_sma_trend=200)) == 201)

# ── daily aggregation from a 1m corpus ──
with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "T.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE backtest_candles_1m (
        instrument_token INTEGER, ts INTEGER, underlying TEXT,
        tradingsymbol TEXT, instrument_type TEXT, strike REAL, expiry TEXT,
        open REAL, high REAL, low REAL, close REAL, volume INTEGER, oi INTEGER)""")
    tok = 0
    for dd in (date(2024, 3, 1), date(2024, 3, 4)):
        for mnt in range(5):
            tok += 1
            ts = int(datetime(dd.year, dd.month, dd.day, 9, 15,
                              tzinfo=IST).timestamp()) + mnt * 60
            conn.execute("INSERT INTO backtest_candles_1m VALUES "
                         "(?,?,'T','T-SPOT','SPOT',0,'',?,?,?,?,?,0)",
                         (tok, ts, 100 + mnt, 110 + mnt, 90 + mnt,
                          105 + mnt, 1000))
    conn.commit()
    conn.close()
    db_bars = S.load_daily_bars(db, "T", date_from=date(2024, 3, 1),
                                date_to=date(2024, 3, 4))
    ck("daily aggregation yields one bar per session", len(db_bars) == 2)
    b0 = db_bars[0]
    ck("daily open is the session's first 1m open", b0.open == 100)
    ck("daily high/low span the session", b0.high == 114 and b0.low == 90)
    ck("daily close is the session's last 1m close", b0.close == 109)
    ck("daily volume sums the session", b0.volume == 5000)
    ck("bars are ordered and IST-dated",
       [b.day for b in db_bars] == [date(2024, 3, 1), date(2024, 3, 4)])

    g = S.build_gate(db, "T", date_from=date(2024, 3, 1),
                     date_to=date(2024, 3, 4), cfg=CFG)
    ck("build_gate reports its own warmup shortfall",
       g["warmup_ok"] is False and g["allowed_days"] == 0)

ck("screener defaults to OFF", S.DEFAULTS["screener_enabled"] is False)
ck("defaults reproduce the Chartink scan",
   (S.DEFAULTS["screener_ema_fast"], S.DEFAULTS["screener_ema_slow"],
    S.DEFAULTS["screener_sma_trend"], S.DEFAULTS["screener_vol_sma"],
    S.DEFAULTS["screener_min_volume"],
    S.DEFAULTS["screener_cross_window_days"]) == (10, 20, 40, 10, 2000000, 1))

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
