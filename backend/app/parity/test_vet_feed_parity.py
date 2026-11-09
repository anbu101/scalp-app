# backend/app/parity/test_vet_feed_parity.py
#
# ── VET_FEED_PARITY_20261109 ──
#   A. tick engine: NIFTY spot on FULL mode; a spot value is bucketed by its
#      exchange timestamp (09:29:59.8 arriving at 09:30:00.6 closes 09:29);
#      missing timestamp → arrival time, said once; options stay LTP
#   B. recorder: a special session (Sunday 2026-11-08) is probed once, every
#      minute Kite returns is kept (any hours), errors are retried, empty days
#      remembered; the probe window
#   C. runners: history_only_days → indicators include the day, nothing is
#      traded on it; no knob → byte-identical to the original runners
#   D. parity: special days → history only for Velvet / Timberwolf, dropped
#      from the pack for the others; hedges beside an unpaired short; timing
#      near-threshold (the 2026-11-09 14:10 bar, 0.04 pts) and the verdict
#
#   cd backend && PYTHONPATH=$PWD python3 app/parity/test_vet_feed_parity.py
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

TMP = tempfile.mkdtemp(prefix="vet_feed_parity_")
os.environ["HOME"] = TMP
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, BACKEND)
IST = timezone(timedelta(hours=5, minutes=30))
FAILS, OK, LOG = [], [0], []


def check(n, c, d=""):
    if c:
        OK[0] += 1
        print(f"  PASS  {n}")
    else:
        FAILS.append(n)
        print(f"  FAIL  {n}  [{str(d)[:400]}]")


def T(y, mo, dd, h, mi, s=0.0):
    return datetime(y, mo, dd, h, mi, int(s), int((s % 1) * 1e6), tzinfo=IST)


import app.event_bus.audit_logger as AL                       # noqa: E402
AL.write_audit_log = lambda m, *a, **k: LOG.append(str(m))
from app.utils.app_paths import APP_HOME                       # noqa: E402

# ── A ───────────────────────────────────────────────────────────────
print("── A. tick engine")
from app.engine.tma2 import tma2_tick_engine as TE             # noqa: E402
TE.write_audit_log = lambda m, *a, **k: LOG.append(str(m))


class WS:
    MODE_LTP, MODE_FULL = "ltp", "full"

    def __init__(self):
        self.modes = []

    def subscribe(self, t):
        pass

    def set_mode(self, m, t):
        self.modes.append((m, list(t)))


class WSNoFull(WS):
    MODE_FULL = None

    def set_mode(self, m, t):
        if m is None:
            raise ValueError("no full")
        self.modes.append((m, list(t)))


e = TE.TMA2TickEngine(None, None, lambda *a: None, log_tag="VET_TICK")
e._spot_token = 256265
e._builders = {256265: TE.CandleBuilder(), 111: TE.CandleBuilder()}
e._tok2sym = {111: "NIFTY26N1724900CE"}
ws = WS()
LOG.clear()
e._on_connect(ws, {})
check("every token on LTP, then the spot alone on FULL",
      ws.modes[0][0] == "ltp" and set(ws.modes[0][1]) == {256265, 111} and ws.modes[1] == ("full", [256265]), ws.modes)
check("connect line says so", any("(spot FULL, exchange timestamps)" in l for l in LOG), LOG)
e2 = TE.TMA2TickEngine(None, None, lambda *a: None, log_tag="VET_TICK")
e2._spot_token, e2._builders = 256265, {256265: TE.CandleBuilder()}
LOG.clear()
e2._on_connect(WSNoFull(), {})
check("FULL refused → spot stays LTP, logged, no crash",
      any("spot FULL mode failed" in l for l in LOG) and not any("spot FULL, exchange" in l for l in LOG), LOG)

TE._publish_ltps = lambda pairs: None
stored = []
e._store_candle = lambda tok, c: stored.append((tok, c))
real_time = TE.time.time
e._last_connect_ts = real_time() - 60
ticks = [(T(2026, 11, 9, 9, 29, 0.4), 24990.0), (T(2026, 11, 9, 9, 29, 30.0), 24995.0),
         (T(2026, 11, 9, 9, 29, 59.8), 25002.85), (T(2026, 11, 9, 9, 30, 0.9), 25004.0)]
for ts, px in ticks:
    e._on_ticks(None, [{"instrument_token": 256265, "last_price": px, "exchange_timestamp": ts.replace(tzinfo=None)
                        if False else ts}])
c929 = [c for tok, c in stored if tok == 256265 and c["ts"] == int(T(2026, 11, 9, 9, 29).timestamp())]
check("the 09:29:59.8 value closes 09:29 (exchange time), arriving after the boundary or not",
      c929 and c929[0]["close"] == 25002.85 and c929[0]["open"] == 24990.0, c929)
naive = datetime.fromtimestamp(T(2026, 11, 9, 9, 31, 0.2).timestamp())     # kiteconnect gives naive local time
e._on_ticks(None, [{"instrument_token": 256265, "last_price": 25010.0, "exchange_timestamp": naive}])
c930 = [c for tok, c in stored if tok == 256265 and c["ts"] == int(T(2026, 11, 9, 9, 30).timestamp())]
check("a naive (local-time) exchange timestamp maps to the same epoch", c930 and c930[0]["close"] == 25004.0, stored)
LOG.clear()
e._spot_ts_missing_logged = False
e._on_ticks(None, [{"instrument_token": 256265, "last_price": 25011.0}])
e._on_ticks(None, [{"instrument_token": 256265, "last_price": 25012.0}])
check("a spot tick without a timestamp → arrival time, logged ONCE",
      sum("[VET_TICK][SPOT_TS]" in l for l in LOG) == 1, LOG)
LOG.clear()
e._spot_ts_missing_logged = False
e._last_connect_ts = real_time()
e._on_ticks(None, [{"instrument_token": 256265, "last_price": 25013.0}])
check("…not in the first seconds after connect (mode switch settling)", not any("SPOT_TS" in l for l in LOG), LOG)
LOG.clear()
e._last_connect_ts = real_time() - 60
e._spot_ts_missing_logged = False
e._on_ticks(None, [{"instrument_token": 111, "last_price": 101.0}])
check("an option tick without a timestamp is normal (LTP mode) — no log", not LOG, LOG)

# ── B ───────────────────────────────────────────────────────────────
print("── B. recorder")
from app.recorder import rec_db as RD, rec_pull as RP, rec_jobs as RJ   # noqa: E402
RP.time.sleep = lambda s: None
SUN = date(2026, 11, 8)


class Kite:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, 0

    def historical_data(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Gateway Time-out")
        return self.rows


mrows = [{"date": T(2026, 11, 8, 18, 15) + timedelta(minutes=k), "open": 25000 + k, "high": 25001 + k,
          "low": 24999 + k, "close": 25000.5 + k, "volume": 0} for k in range(60)]
c = RD.connect()
r = RP.pull_special_spot(c, Kite(fail=True), SUN, now=int(T(2026, 11, 9, 20, 0).timestamp()))
check("Kite error → not marked probed (retried next pass)", r.get("error") and not RP.special_probed(c, SUN), r)
k = Kite(mrows)
r = RP.pull_special_spot(c, k, SUN, now=int(T(2026, 11, 9, 20, 0).timestamp()))
got = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM main.backtest_candles_1m WHERE instrument_type='SPOT' "
                "AND ts>=? AND ts<?", (RP.day_start(SUN), RP.day_start(SUN) + 86400)).fetchone()
check("Muhurat 18:15–19:14 kept in full (outside 09:15–15:29)",
      r["special"] == 60 and got[0] == 60 and got[1] == int(T(2026, 11, 8, 18, 15).timestamp()), (r, got))
check("…and remembered", RP.special_probed(c, SUN))
r0 = RP.pull_special_spot(c, Kite([]), date(2026, 11, 7), now=int(T(2026, 11, 9, 20, 0).timestamp()))
check("an empty Saturday is remembered as 0 minutes", r0["special"] == 0 and RP.special_probed(c, date(2026, 11, 7)))
now_mon = int(T(2026, 11, 10, 7, 0).timestamp())
sd = RJ.special_days(c, date(2026, 11, 10), now_mon)
check("probe window: unprobed non-trading days only (Sat 7th / Sun 8th already done)",
      date(2026, 11, 8) not in sd and date(2026, 11, 7) not in sd and date(2026, 11, 1) in sd
      and all(not RJ.is_trading_day(x) for x in sd), sd)
sun_now = int(T(2026, 11, 15, 19, 0).timestamp())
check("a non-trading TODAY waits until 21:00 (the evening session must be over)",
      date(2026, 11, 15) not in RJ.special_days(c, date(2026, 11, 15), sun_now)
      and date(2026, 11, 15) in RJ.special_days(c, date(2026, 11, 15), int(T(2026, 11, 15, 21, 5).timestamp())))
check("day_status untouched (the special day is not a recorded session)",
      not c.execute("SELECT 1 FROM day_status WHERE day=?", (SUN.isoformat(),)).fetchone())
c.close()

# ── C ───────────────────────────────────────────────────────────────
print("── C. runners")
from app.backtest.replay.replay_synth import build                # noqa: E402
from app.backtest.vet import backtest_vet_runner as VR             # noqa: E402
from app.backtest.tma import backtest_tma_v2_runner as TR          # noqa: E402
from app.parity import parity_core as PC                           # noqa: E402
from app.config.strategy_loader import load_strategy_config        # noqa: E402

corpus = os.path.join(TMP, "corpus.db")
days = build(corpus, date(2025, 1, 1), date(2025, 3, 14))
SPECIAL = date(2025, 2, 9)                                         # a Sunday
FRI = max(x for x in days if x < SPECIAL)
cc = sqlite3.connect(corpus)
fri = cc.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts",
                 (PC.day_start(FRI), PC.day_start(FRI) + 86400)).fetchall()
cols = [x[1] for x in cc.execute("PRAGMA table_info(backtest_candles_1m)")]
it = cols.index("ts")
shift = PC.day_start(SPECIAL) - PC.day_start(FRI) + 9 * 3600           # 09:15 → 18:15
for row in fri[:60]:
    row = list(row)
    row[it] = row[it] + shift
    for k_ in ("open", "high", "low", "close"):
        row[cols.index(k_)] = row[cols.index(k_)] + 400.0                # a rally in the evening session
    cc.execute(f"INSERT INTO backtest_candles_1m ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", row)
cc.commit()
cc.close()


def _orig(name, rel):
    from importlib.machinery import SourceFileLoader
    path = os.path.join(BACKEND, rel + ".orig")
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, path))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m                                   # dataclasses need the module registered
    spec.loader.exec_module(m)
    return m


have_orig = os.path.exists(os.path.join(BACKEND, "app/backtest/vet/backtest_vet_runner.py.orig"))
VLIVE = PC.to_runner_config("VET_V1", dict(load_strategy_config("VET_V1")), SPECIAL)[0]
TLIVE = PC.to_runner_config("TMA_V2", dict(load_strategy_config("TMA_V2")), SPECIAL)[0]
D0, D1 = SPECIAL - timedelta(days=12), SPECIAL + timedelta(days=12)


def trades(res):
    out = []
    for t in res.get("trades") or []:
        g = (lambda *n: next((t.get(x) if isinstance(t, dict) else getattr(t, x, None)) for x in n))
        out.append((g("symbol", "tradingsymbol"), g("entry_ts", "entry_time"), g("exit_ts", "exit_time")))
    return sorted(out, key=str)


def on_day(tr, d):
    lo, hi = PC.day_start(d), PC.day_start(d) + 86400
    return [x for x in tr if any(isinstance(v, (int, float)) and lo <= v < hi for v in x[1:])]


rv = VR.run_vet_backtest(db_path=corpus, strategy_id="VET_V1", underlying="NIFTY", date_from=D0, date_to=D1,
                         config_override=dict(VLIVE, history_only_days=[SPECIAL.isoformat()]))
check("Velvet: nothing traded on the history-only day", not on_day(trades(rv), SPECIAL), on_day(trades(rv), SPECIAL))
check("…its bars were seen (diag)", (rv.get("summary") or {}).get("diag", rv.get("summary", {})).get("history_only_bars", 0) > 0
      or "history_only_bars" in json.dumps(rv.get("summary") or {}), list((rv.get("summary") or {}).keys())[:20])
rt = TR.run_tma_v2_backtest(db_path=corpus, strategy_id="TMA_V2", underlying="NIFTY", date_from=D0, date_to=D1,
                            config_override=dict(TLIVE, history_only_days=[SPECIAL.isoformat()]))
check("Timberwolf: nothing traded on the history-only day", not on_day(trades(rt), SPECIAL), on_day(trades(rt), SPECIAL))
if have_orig:
    OV = _orig("orig_vet_runner", "app/backtest/vet/backtest_vet_runner.py")
    OT = _orig("orig_tma2_runner", "app/backtest/tma/backtest_tma_v2_runner.py")
    for nm, new, old, cfg_ in (("Velvet", VR.run_vet_backtest, OV.run_vet_backtest, VLIVE),
                               ("Timberwolf", TR.run_tma_v2_backtest, OT.run_tma_v2_backtest, TLIVE)):
        a = new(db_path=corpus, strategy_id="VET_V1" if nm == "Velvet" else "TMA_V2", underlying="NIFTY",
                date_from=D0, date_to=D1, config_override=dict(cfg_))
        b = old(db_path=corpus, strategy_id="VET_V1" if nm == "Velvet" else "TMA_V2", underlying="NIFTY",
                date_from=D0, date_to=D1, config_override=dict(cfg_))
        check(f"{nm}: no knob → identical trades to the original runner ({len(trades(a))} trades)",
              trades(a) == trades(b) and len(trades(a)) > 0, (len(trades(a)), len(trades(b))))
else:
    print("  (original runners not staged — equivalence checks skipped)")

# ── D ───────────────────────────────────────────────────────────────
print("── D. parity")
pd_ = max(x for x in days if x > SPECIAL and x < D1)
pack, pinfo = PC.build_pack(pd_, corpus, d_from=FRI)
check("pack reports the special session", pinfo.get("special_days") == [SPECIAL.isoformat()], pinfo.get("special_days"))
PC._drop_special_spot(pack, pinfo["special_days"])
p = sqlite3.connect(pack)
left = p.execute("SELECT COUNT(*) FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
                 (PC.day_start(SPECIAL), PC.day_start(SPECIAL) + 86400)).fetchone()[0]
p.close()
check("…dropped for strategies without cross-day indicators", left == 0, left)
os.unlink(pack)                                                   # never leave a day pack behind
check("history-only strategies are Velvet and Timberwolf", PC.HISTORY_ONLY_SIDS == {"VET_V1", "TMA_V2"})
src = open(os.path.join(HERE, "parity_core.py"), encoding="utf-8").read()
check("run_day wires both paths", "cfg = dict(cfg, history_only_days=sp)" in src and "_drop_special_spot(pack, pinfo[\"special_days\"])" in src)


def leg(sym, side, ets, ent, xts=None, ext=None, reason=None):
    return {"symbol": sym, "side": side, "type": sym[-2:], "strike": float(sym[-7:-2]), "qty": 650,
            "entry_ts": ets, "exit_ts": xts, "entry": ent, "exit": ext, "reason": reason, "synthetic": False,
            "wing_modelled": False, "book": "PAPER", "open": False}


ts = lambda h, m: int(T(2026, 11, 9, h, m).timestamp())             # noqa: E731
bt = [leg("NIFTY26N0924850CE", "SELL", ts(12, 34), 87.25, ts(14, 14), 157.95, "FLIP"),
      leg("NIFTY26N0925050PE", "SELL", ts(14, 14), 75.35, ts(15, 14), 41.30, "EXPIRY_EXIT")]
app = [leg("NIFTY26N0924850CE", "SELL", ts(12, 34), 87.25, ts(14, 19), 205.25, "FLIP"),
       leg("NIFTY26N0925050PE", "SELL", ts(14, 19), 51.30, ts(15, 15), 35.50, "EXPIRY_EXIT"),
       leg("NIFTY26N0924900CE", "SELL", ts(9, 44), 105.80, ts(11, 39), 174.90, "FLIP"),
       leg("NIFTY26N0925450CE", "BUY", ts(9, 44), 4.20, ts(11, 39), 3.05, "FLIP"),
       leg("NIFTY26N0925250CE", "BUY", ts(12, 34), 4.90, ts(14, 19), 4.90, "FLIP")]
rows = PC.mark_exit_missed(PC.reclass_hedges(PC.match(bt, app), bt))
by = {(r["app"] or r["bt"])["symbol"][-7:]: r for r in rows}
check("the wing of an UNPAIRED short is a hedge (25450CE beside 24900CE)", by["25450CE"]["status"] == "HEDGE", by["25450CE"]["status"])
check("…the unpaired short itself stays extra", by["24900CE"]["status"] == "EXTRA")
b1410 = {"bar_ts": ts(14, 10), "cond": 1, "prev": -1, "open": 24949.45, "close": 24983.10, "ch_top": 24949.41,
         "ch_bot": 24907.72, "ema_gap": 9.71, "to_up": 0.04, "to_down": -75.38}
b1140 = {"bar_ts": ts(9, 40), "cond": 1, "prev": 1, "open": 24915.2, "close": 24924.9, "ch_top": 24863.67,
         "ch_bot": 24820.23, "ema_gap": 27.86, "to_up": 27.86, "to_down": -104.68}
idx = {"sell": True, "tf": 5, "bars": {ts(14, 14): b1410, ts(9, 44): b1140}}
rows = PC.tag_vet_near(rows, idx, date(2026, 11, 9))
by = {(r["app"] or r["bt"])["symbol"][-7:]: r for r in rows}
check("24850CE exit 5 min apart → timing measured on the 14:10 bar, 0.04 pts, backtest first",
      by["24850CE"].get("tnear") and by["24850CE"]["tnear_pts"] == 0.04 and by["24850CE"]["tnear_which"] == "exit"
      and by["24850CE"]["tnear_first"] == "backtest" and by["24850CE"]["tnear_bar"] == "14:10", by["24850CE"])
check("25050PE entry 5 min apart → the same bar (entry)", by["25050PE"].get("tnear_which") == "entry"
      and by["25050PE"]["tnear_pts"] == 0.04, by["25050PE"])
check("…its expiry exit 1 min apart is not a bar decision (not measured)", by["25050PE"]["tnear_which"] != "exit")
check("the extra 24900CE (09:44, app first) is measured on the bearish test: a clear bar (104.68 pts), not near",
      by["24900CE"].get("near_pts") == 104.68 and not by["24900CE"].get("near"), by["24900CE"].get("near_pts"))
s = PC.summarise(rows)
check("a clear extra trade keeps 'trades differ'", s["verdict"] == "trades differ" and s["timing_near"] == 2, s)
rows2 = [r for r in rows if (r["app"] or r["bt"])["symbol"][-7:] in ("24850CE", "25050PE", "25250CE")]
s2 = PC.summarise(rows2)
check("only near-threshold timing left → 'differs at a near-threshold bar' (0.04 pts)",
      s2["verdict"] == "differs at a near-threshold bar" and s2["near_min_pts"] == 0.04, s2)
r2 = PC.tag_vet_near([dict(by["24850CE"], tnear=None, tnear_pts=None)],
                     {"sell": True, "bars": {ts(14, 14): dict(b1410, to_up=9.5)}}, date(2026, 11, 9))
s3 = PC.summarise(r2)
check("same timing on a clear bar (9.5 pts) → 'same trades, timing differs'",
      s3["verdict"] == "same trades, timing differs" and not r2[0]["tnear"], (s3["verdict"], r2[0].get("tnear_pts")))

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL VET FEED / PARITY CHECKS PASSED")
