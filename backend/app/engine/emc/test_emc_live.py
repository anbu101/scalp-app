# backend/app/engine/emc/test_emc_live.py
#
# ── EMC_V1_LIVE_20261011 ── behavioural suite for the paper/live engine. Standalone:
#   cd backend && PYTHONPATH=$PWD python3 app/engine/emc/test_emc_live.py
# A fake Kite replays a synthetic Black-76 corpus at a simulated clock; the REAL
# EmcEngine + EmcManager trade it and are compared, trade by trade, with
# run_emc_backtest on the same corpus. Then: restart, LIVE order flow,
# reconcile-first exits, OFF mode, every fail-closed refusal.
import os, sys, sqlite3, tempfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

IST = 5 * 3600 + 30 * 60
TZ = timezone(timedelta(hours=5, minutes=30))


class Clock:
    def __init__(self, t=0): self.t = t
    def __call__(self): return self.t


class FakeKite:
    """historical_data / quote over a backtest corpus at the simulated time."""
    def __init__(self, db, clock, spot_token=256265):
        self.c = sqlite3.connect(db); self.c.row_factory = sqlite3.Row
        self.clock = clock
        self.spot_token = spot_token
        self.tok2sym, self.sym2tok = {}, {}
        for i, (s,) in enumerate(self.c.execute("SELECT DISTINCT tradingsymbol FROM backtest_candles_1m "
                                                "WHERE instrument_type IN ('CE','PE')")):
            self.tok2sym[100000 + i] = s; self.sym2tok[s] = 100000 + i
        self._cache = {}
        self.calls = {"hist": 0, "quote": 0}

    def _rows(self, sym_or_spot, day_start):
        key = (sym_or_spot, day_start)
        if key not in self._cache:
            if sym_or_spot == "SPOT":
                q = self.c.execute("SELECT ts,open,high,low,close FROM backtest_candles_1m WHERE underlying='NIFTY' "
                                   "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts", (day_start, day_start + 86400))
            else:
                q = self.c.execute("SELECT ts,open,high,low,close FROM backtest_candles_1m WHERE tradingsymbol=? "
                                   "AND ts>=? AND ts<? ORDER BY ts", (sym_or_spot, day_start, day_start + 86400))
            self._cache[key] = {r["ts"]: dict(r) for r in q}
        return self._cache[key]

    @staticmethod
    def _ds(ts): return ((int(ts) + IST) // 86400) * 86400 - IST

    @staticmethod
    def _epoch(dt):
        if dt.tzinfo is None:
            return int((dt - datetime(1970, 1, 1)).total_seconds()) - IST
        return int(dt.timestamp())

    def historical_data(self, instrument_token, from_date, to_date, interval):
        self.calls["hist"] += 1
        now = self.clock()
        if interval == "day":
            fr = self._epoch(datetime(from_date.year, from_date.month, from_date.day))
            to = self._epoch(datetime(to_date.year, to_date.month, to_date.day)) + 86400
            out = []
            for (ts,) in self.c.execute("SELECT DISTINCT (ts + ?) / 86400 FROM backtest_candles_1m WHERE "
                                        "instrument_type='SPOT' AND ts>=? AND ts<?", (IST, fr, min(to, now))):
                d = date(1970, 1, 1) + timedelta(days=int(ts))
                out.append({"date": datetime(d.year, d.month, d.day, tzinfo=TZ), "open": 1, "high": 1, "low": 1, "close": 1})
            return out
        fr, to = self._epoch(from_date), self._epoch(to_date)
        name = "SPOT" if int(instrument_token) == self.spot_token else self.tok2sym.get(int(instrument_token))
        out = []
        d = self._ds(fr)
        while d <= to:
            for ts, r in sorted(self._rows(name, d).items()):
                if fr <= ts <= to and ts + 60 <= now:
                    out.append({"date": datetime.fromtimestamp(ts, TZ), "open": r["open"], "high": r["high"],
                                "low": r["low"], "close": r["close"], "volume": 0})
            d += 86400
        return out

    def path_price(self, sym, t):
        """Intraminute path open(0–5s) → low(25s) → high(45s) → close(59s)."""
        m0 = t - t % 60
        r = self._rows(sym, self._ds(t)).get(m0)
        if r is None:
            prev = [v for k, v in self._rows(sym, self._ds(t)).items() if k < m0]
            return prev[-1]["close"] if prev else None
        s = t % 60
        o, h, l, c = r["open"], r["high"], r["low"], r["close"]
        if s <= 5: return o
        if s <= 25: return round(o + (l - o) * (s - 5) / 20, 2)
        if s <= 45: return round(l + (h - l) * (s - 25) / 20, 2)
        return round(h + (c - h) * (s - 45) / 14, 2)

    def quote(self, keys):
        self.calls["quote"] += 1
        now = self.clock()
        out = {}
        for k in keys:
            sym = k.split(":", 1)[1]
            p = self.path_price(sym, now)
            if p is not None:
                out[k] = {"last_price": p}
        return out


def make_chain_fn(kite, clock, expiry_fn, stf=5):
    """Ideal snapshot AT THE CLOSE of the minute just completed (the
    backtest's quotes_at rule: close at k, else last print in the spot bar)."""
    def chain():
        now = clock()
        ds = FakeKite._ds(now)
        k = ((now + IST) % 86400) // 60 - 1
        d = (datetime.utcfromtimestamp(now + IST)).date()
        exp = expiry_fn(d).isoformat()
        rows, ltp = [], {}
        for (sym, strike, it) in kite.c.execute("SELECT DISTINCT tradingsymbol, strike, instrument_type FROM "
                                                "backtest_candles_1m WHERE expiry=? AND ts>=? AND ts<?", (exp, ds, ds + 86400)):
            rows.append({"tradingsymbol": sym, "instrument_token": kite.sym2tok[sym], "strike": strike, "instrument_type": it})
            bars = kite._rows(sym, ds)
            px = None
            for m in range(k, k - stf, -1):
                b = bars.get(ds + m * 60)
                if b is not None and b["close"]:
                    px = float(b["close"]); break
            ltp[sym] = px or 0.0
        return date.fromisoformat(exp), rows, ltp
    return chain


# ═══════════════════════════════════ suite ═══════════════════════════════════
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
from app.backtest.replay import replay_synth                                   # noqa: E402
from app.backtest.emc.backtest_emc_runner import run_emc_backtest, _day_start_epoch, DEFAULTS as BT_DEFAULTS  # noqa: E402
from app.backtest.engine.expiry_calendar import expected_expiry_for_day        # noqa: E402
from app.utils.market_hours import is_trading_day                              # noqa: E402
import app.engine.emc.emc_manager as MG                                        # noqa: E402
from app.engine.emc.emc_engine import EmcEngine, EmcFeed                       # noqa: E402
from app.engine.emc.emc_live_core import live_params, CrossGuard, LiveSpotCrosses  # noqa: E402

FAILS, NPASS = [], [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        NPASS[0] += 1
    else:
        FAILS.append(name)


ROWS = {}


def fake_insert(**k):
    ROWS[k["paper_trade_id"]] = dict(k, exit_price=None, exit_reason=None)


def fake_close(paper_trade_id, exit_price, exit_reason, **k):
    ROWS[paper_trade_id].update(exit_price=exit_price, exit_reason=exit_reason)


MG.insert_paper_trade, MG.close_paper_trade = fake_insert, fake_close
MG._DEGRADED.clear()


class Notif:
    def __init__(self): self.sent = []
    def __getattr__(self, n): return lambda payload: self.sent.append((n, payload))


ALERTS = []
MG.record_alert = lambda code, msg, **k: ALERTS.append((code, msg, k.get("severity")))


SEALED = {"trade_execution_mode": "PAPER", "lots": 1, "sel_prem_min": 60, "sel_prem_max": 140,
          "ema_fast": 7, "ema_slow": 20, "sl_pts": 31, "tp_pts": 100, "entry_cutoff": "14:00",
          "max_losses_per_day": 4, "arm_skip_from": "09:30", "arm_skip_to": "10:00"}

tmp = tempfile.mkdtemp(prefix="emc_live_")
DB = os.path.join(tmp, "backtest.db")
replay_synth.build(DB, d0=date(2025, 1, 1), d1=date(2025, 2, 28), strikes_each_side=8, with_fut=False)


def rows_today(mgr):
    def today_rows(ds, rows=None):
        return [dict(r) for r in sorted(ROWS.values(), key=lambda r: r["candle_ts"])
                if r["candle_ts"] >= ds and not str(r["paper_trade_id"]).startswith("SHD:")]
    def open_rows_any_day():
        return [dict(r) for r in ROWS.values() if r["exit_price"] is None and r.get("exit_reason") is None]
    mgr.today_rows, mgr.open_rows_any_day = today_rows, open_rows_any_day


def build(cfg, clock, kite, executor=None):
    state = {"cfg": dict(cfg)}
    mgr = MG.EmcManager(executor=executor, cfg_fn=lambda: dict(state["cfg"]), notifier=Notif(), clock=clock,
                        sleep=lambda s: setattr(clock, "t", clock.t + s))
    mgr.state_cfg = state
    rows_today(mgr)
    feed = EmcFeed(lambda: kite, chain_fn=make_chain_fn(kite, clock, expected_expiry_for_day))
    eng = EmcEngine(mgr, feed=feed, clock=clock, sleep=lambda s: None, trading_day_fn=is_trading_day,
                    expected_expiry_fn=expected_expiry_for_day)
    return mgr, eng


def run_days(mgr, eng, clock, d0, d1, stop_at=None, start_at=None):
    d = d0
    while d <= d1:
        if is_trading_day(d):
            ds = _day_start_epoch(d)
            for m in range(9 * 60 + 5, 15 * 60 + 31):
                if start_at and ds + m * 60 < start_at:
                    continue
                busy = mgr.pos is not None
                secs = range(60) if busy else (0, 1, 3, 4, 5, 8, 15, 30)
                for s in secs:
                    t = ds + m * 60 + s
                    if stop_at and t >= stop_at:
                        return
                    clock.t = t
                    eng.tick(t)
                    if not busy and mgr.pos is not None:
                        for s2 in range(s + 1, 60):
                            clock.t = ds + m * 60 + s2
                            eng.tick(clock.t)
                        break
        d += timedelta(days=1)


def compare(live_rows, bt_trades):
    L = {(r["candle_ts"], r["symbol"]): r for r in live_rows}
    B = {(t.entry_ts, t.tradingsymbol): t for t in bt_trades}
    both = set(L) & set(B)
    same_reason = sum(1 for k in both if L[k]["exit_reason"] == B[k].exit_reason)
    entry_px = max((abs(L[k]["entry_price"] - B[k].entry_price) for k in both), default=0.0)
    return len(L), len(B), len(both), same_reason, entry_px


print("── config: live reads the backtest runner's vocabulary ──")
p, m, bad = live_params(SEALED)
check("sealed config accepted", bad is None and p is not None, bad)
check("DayParams mapped exactly (SL 31 / TP 100 / cutoff 14:00 / EOD 15:15 / cap 4 / no-arm 09:30–10:00)",
      p.sl_pts == 31 and p.tp_pts == 100 and p.block_min == 840 and p.eod_min == 915 and p.max_losses == 4
      and p.skip_from == 570 and p.skip_to == 600 and p.opt_min_bars == 20 and p.intrabar and p.fill == "next_open")
for nm, over in (("trailing SL refused", {"trail_on": True}), ("fill_mode close refused", {"fill_mode": "close"}),
                 ("runner guard: band inverted", {"sel_prem_min": 200, "sel_prem_max": 150}),
                 ("runner guard: blackout half set", {"arm_skip_to": ""})):
    check(nm, live_params(dict(SEALED, **over))[2] is not None)
g = CrossGuard()
g.check({559: 1, 604: -1}, 604)
check("prefix guard freezes on a restated cross", not g.check({559: 1, 604: 1}, 609) and g.frozen)

print("── parity: live engine vs backtest, trade by trade ──")
D0, D1 = date(2025, 1, 20), date(2025, 2, 28)
for name, cfg in (("sealed config", SEALED),
                  ("spec defaults (EMA 9/21, SL 20, TP 40, no cap, no blackout)",
                   dict(SEALED, ema_fast=9, ema_slow=21, sl_pts=20, tp_pts=40, entry_cutoff="14:30",
                        max_losses_per_day=0, arm_skip_from="", arm_skip_to=""))):
    ROWS.clear()
    clock = Clock()
    kite = FakeKite(DB, clock)
    mgr, eng = build(cfg, clock, kite)
    mgr.qty = lambda: (1, 65, 65)
    run_days(mgr, eng, clock, D0, D1)
    bt = run_emc_backtest(db_path=DB, strategy_id="EMC_V1", underlying="NIFTY", date_from=D0, date_to=D1,
                          config_override=dict(cfg))["trades"]
    nl, nb, nboth, sr, epx = compare(list(ROWS.values()), bt)
    check(f"{name}: same trades ({nl} live / {nb} backtest)", nl == nb == nboth and nl >= 15, (nl, nb, nboth))
    check(f"{name}: same exit reason on every trade", sr == nboth, (sr, nboth))
    check(f"{name}: entry price = the backtest's next-minute open", epx < 0.005, epx)
    check(f"{name}: every row closed, nothing carried overnight", all(r["exit_price"] is not None for r in ROWS.values()))
BASE_ROWS = {k: dict(v) for k, v in ROWS.items()}

print("── restart mid-position: adopt, keep counters, same exit ──")
ROWS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
day = date(2025, 1, 21)
for dd in (date(2025, 1, 21), date(2025, 1, 22), date(2025, 1, 23), date(2025, 1, 24), date(2025, 1, 27)):
    ROWS.clear()
    clock = Clock(); kite = FakeKite(DB, clock)
    mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
    run_days(mgr, eng, clock, dd, dd)
    full = sorted(ROWS.values(), key=lambda r: r["candle_ts"])
    if len(full) >= 2 and full[1]["exit_reason"] in ("SL", "TP", "XOVER", "EOD"):
        day = dd
        break
ref = sorted(ROWS.values(), key=lambda r: r["candle_ts"])
# restart 2 minutes after the SECOND entry
t_stop = ref[1]["candle_ts"] + 120
ROWS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day, stop_at=t_stop)
before = {k: dict(v) for k, v in ROWS.items()}
mgr2, eng2 = build(SEALED, clock, kite); mgr2.qty = lambda: (1, 65, 65)
run_days(mgr2, eng2, clock, day, day, start_at=t_stop)
after = sorted(ROWS.values(), key=lambda r: r["candle_ts"])
check("restart adopts the open row (no duplicate entry)", len([r for r in after if r["symbol"] == ref[1]["symbol"]
                                                                and r["candle_ts"] == ref[1]["candle_ts"]]) == 1)
check("adopted position exits for the same reason as the uninterrupted day",
      [r for r in after if r["candle_ts"] == ref[1]["candle_ts"]][0]["exit_reason"] == ref[1]["exit_reason"],
      ([r["exit_reason"] for r in after], [r["exit_reason"] for r in ref]))
check("day counters restored after restart", eng2.core is not None and eng2.core.day_entries >= 2)
check("no trade after the restart that the uninterrupted day did not also take (pending setups are not resurrected)",
      {(r["candle_ts"], r["symbol"]) for r in after} <= {(r["candle_ts"], r["symbol"]) for r in ref})

print("── LIVE order flow (fake executor) ──")


class FakeExec:
    def __init__(self, kite, clock, holding=True, positions_ok=True, fill=True):
        self.kite, self.clock = kite, clock
        self.orders, self.sells, self.holding, self.positions_ok, self.fill = [], [], holding, positions_ok, fill
        self.book = {}
    def place_buy(self, symbol, token, qty):
        px = self.kite.path_price(symbol, self.clock())
        self.orders.append((symbol, qty, px))
        if not self.fill:
            return "B1", 0, 0
        self.book[symbol] = qty
        return f"B{len(self.orders)}", px, qty
    def get_order_fill(self, oid):
        if not self.fill:
            return {"status": "OPEN", "found": True}
        return {"status": "COMPLETE", "avg_price": self._last_px, "found": True}
    def place_market_sell(self, symbol, qty):
        self._last_px = self.kite.path_price(symbol, self.clock())
        self.sells.append((symbol, qty, self._last_px))
        self.book.pop(symbol, None)
        return f"S{len(self.sells)}"
    def get_open_positions_or_none(self):
        if not self.positions_ok:
            return None
        return [{"tradingsymbol": s, "quantity": q} for s, q in self.book.items()] if self.holding else []


ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
ex = FakeExec(kite, clock)
mgr, eng = build(dict(SEALED, trade_execution_mode="LIVE"), clock, kite, executor=ex); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day)
live_rows = sorted(ROWS.values(), key=lambda r: r["candle_ts"])
check("LIVE: one buy and one sell per trade, rows booked as LIVE",
      len(ex.orders) == len(live_rows) == len(ex.sells) and all(r["trade_mode"] == "LIVE" for r in live_rows),
      (len(ex.orders), len(ex.sells), len(live_rows)))
check("LIVE: same trades as PAPER on that day", [(r["candle_ts"], r["symbol"]) for r in live_rows]
      == [(r["candle_ts"], r["symbol"]) for r in ref])

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
ex = FakeExec(kite, clock, holding=False)
mgr, eng = build(dict(SEALED, trade_execution_mode="LIVE"), clock, kite, executor=ex); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day)
check("reconcile-first: broker shows no holding → row closed, NO sell order placed",
      ex.orders and not ex.sells and any(c == "BROKER_FLAT" for c, _, _ in ALERTS))

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
ex = FakeExec(kite, clock, positions_ok=False)
mgr, eng = build(dict(SEALED, trade_execution_mode="LIVE"), clock, kite, executor=ex); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day)
check("unreadable broker book → NO sell order, backoff + alert, position kept open",
      ex.orders and not ex.sells and any(c == "BROKER_UNREADABLE" for c, _, _ in ALERTS) and mgr.pos is not None)

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
ex = FakeExec(kite, clock, fill=False)
mgr, eng = build(dict(SEALED, trade_execution_mode="LIVE"), clock, kite, executor=ex); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day)
check("unfilled LIVE buy → no position, no row, critical alert", not ROWS and mgr.pos is None
      and any(c == "FILL_TIMEOUT" and s == "critical" for c, _, s in ALERTS))

print("── modes, refusals, stale rows, late data, kill ──")
ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(dict(SEALED, trade_execution_mode="OFF"), clock, kite); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day)
check("OFF: no new entries", not ROWS)

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
t_mid = ref[0]["candle_ts"] + 60
run_days(mgr, eng, clock, day, day, stop_at=t_mid)
had_pos = mgr.pos is not None
mgr.state_cfg["cfg"]["trade_execution_mode"] = "OFF"
run_days(mgr, eng, clock, day, day, start_at=t_mid)
check("OFF with a position open: it is still managed to its exit, nothing new opens",
      had_pos and len(ROWS) == 1 and all(r["exit_price"] is not None for r in ROWS.values()))

for nm, over, code in (("trailing SL", {"trail_on": True}, "CFG_REFUSED"),
                       ("DTE skip", {"dte_lot_mult": {str(i): 0 for i in range(0, 8)}}, "DTE_SKIP")):
    ROWS.clear(); ALERTS.clear()
    clock = Clock(); kite = FakeKite(DB, clock)
    mgr, eng = build(dict(SEALED, **over), clock, kite); mgr.qty = lambda: (1, 65, 65)
    run_days(mgr, eng, clock, day, day)
    check(f"{nm}: day refused, zero trades, alerted", not ROWS and eng.phase() == "REFUSED"
          and any(c == code for c, _, _ in ALERTS), ALERTS[:2])

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
_sess = eng.feed.sessions
eng.feed.sessions = lambda token, days: [] if int(token) == 256265 else _sess(token, days)
run_days(mgr, eng, clock, day, day)
check("no spot warm-up: stays BOOTING/REFUSED, zero trades, alerted",
      not ROWS and eng.phase() in ("BOOTING", "REFUSED") and any(c in ("WARMUP", "NO_WARMUP") for c, _, _ in ALERTS),
      (eng.phase(), ALERTS[:3]))

ROWS.clear(); ALERTS.clear()
ROWS["OLD"] = {"paper_trade_id": "OLD", "strategy_name": "EMC_V1", "trade_mode": "LIVE", "symbol": "NIFTYOLD",
               "token": 1, "side": "CE", "entry_price": 150.0, "candle_ts": _day_start_epoch(day) - 86400 + 40000,
               "sl_price": 119, "tp_price": 250, "qty": 65, "lots": 1, "exit_price": None, "exit_reason": None}
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day, stop_at=_day_start_epoch(day) + 9 * 3600 + 20 * 60)
check("prior-day open row closed as STALE at entry, never adopted, critical alert",
      ROWS["OLD"]["exit_reason"] == "STALE" and ROWS["OLD"]["exit_price"] == 150.0 and mgr.pos is None
      and any(c == "STALE_ROW" and s == "critical" for c, _, s in ALERTS))

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
first_dec = ref[0]["candle_ts"]                      # boundary of the first trade's decision minute
lag = {"on": True}
_h = kite.historical_data
def lagged(**k):
    out = _h(**k)
    if lag["on"] and k.get("interval") == "minute":
        cut = clock() - 150                            # data arrives 2.5 min late around the decision
        if first_dec - 300 < clock() < first_dec + 400:
            out = [r for r in out if int(r["date"].timestamp()) + 60 <= cut]
    return out
kite.historical_data = lagged
run_days(mgr, eng, clock, day, day)
check("data stall around a decision → that entry is forfeited (LATE_DECISION), never taken stale",
      not any(r["candle_ts"] == first_dec for r in ROWS.values()) and any(c == "LATE_DECISION" for c, _, _ in ALERTS))
kite.historical_data = _h

ROWS.clear(); ALERTS.clear()
clock = Clock(); kite = FakeKite(DB, clock)
mgr, eng = build(SEALED, clock, kite); mgr.qty = lambda: (1, 65, 65)
run_days(mgr, eng, clock, day, day, stop_at=ref[0]["candle_ts"] + 60)
res = mgr.kill_adapter()
check("kill adapter flattens and reports the kill_switch contract",
      res["closed"] == 1 and res["remaining"] == 0 and isinstance(res["detail"], list) and mgr.pos is None)
check("a manager-originated close is counted by the day core", eng.core.pos is None and len(eng.core.trades) == 1)

print("── state route ──")
try:
    import app.engine.emc.emc_runtime as RT
    from app.api.emc_state_routes import emc_state
    RT._MANAGER, RT._ENGINE = mgr, eng
    st = emc_state()
    check("state route composes a payload from the live objects",
          st.get("running") and st.get("phase") and "day" in st and st.get("mode") == "PAPER", st.get("error"))
    RT._MANAGER = RT._ENGINE = None
    check("state route is sane when the runtime never launched", emc_state().get("running") is False)
except Exception as e:  # pragma: no cover
    check("state route import", False, repr(e))

# ── EMC_WARM_CLIP_20261030 ── warm sessions hold session minutes only
print("── EMC_WARM_CLIP_20261030")
try:
    from app.engine.emc.emc_engine import EmcFeed as _Feed
    from app.engine.emc.emc_live_core import option_warm_from_sessions as _owfs

    class _K:
        """Kite-like: every minute 09:08–15:39 of the asked days (pre-open and
        post-close prints included, as Kite returns for options)."""
        def historical_data(self, instrument_token, from_date, to_date, interval):
            out, d = [], from_date.date()
            while d <= to_date.date():
                for m in range(9 * 60 + 8, 15 * 60 + 40):
                    dt = datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=TZ)
                    out.append({"date": dt, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + (m % 7)})
                d += timedelta(days=1)
            return out

    _f = _Feed(lambda: _K())
    _days = [date(2026, 10, 28), date(2026, 10, 29)]
    _ss = _f.sessions(111, _days)
    check("live warm sessions: 375 one-minute bars per session (09:15–15:29), nothing pre-open or after 15:29",
          [len(cs) for _, cs in _ss] == [375, 375], [len(cs) for _, cs in _ss])
    _w = _owfs(_ss, 5)
    check("…→ 75 five-minute bars per session, 150 for 2 sessions (30 Oct live log said 154)", len(_w) == 150, len(_w))
except Exception as e:  # pragma: no cover
    check("warm clip", False, repr(e))

if FAILS:
    print(f"\n{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print(f"\nALL {NPASS[0]} EMC LIVE CHECKS PASSED")
