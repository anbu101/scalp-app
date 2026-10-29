# backend/app/engine/lvb/test_lvb.py
#
# ── LVB_V1_20261023 ── behavioural suite for "Tripwire". Standalone:
#   cd backend && PYTHONPATH=$PWD python3 app/engine/lvb/test_lvb.py
# Real LvbEngine + LvbManager + LvbRepo (sqlite :memory:) driven by a fake
# Kite on the REAL epoch clock (IST session times as true epoch seconds —
# TSG_BANK_FIX1 real-clock rule), plus the pure core functions.
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp(prefix="lvb_test_"))

from app.engine.lvb import lvb_core as core                       # noqa: E402
from app.engine.lvb import lvb_manager as M                       # noqa: E402
from app.engine.lvb import lvb_engine as E                        # noqa: E402
from app.engine.lvb.lvb_repo import LvbRepo                       # noqa: E402

TZ = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 10, 27)                       # a Tuesday (NIFTY weekly expiry)
NEXT = date(2026, 11, 3)
DS = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ).timestamp())
FAILS = []
LOG = []


def check(name, cond, extra=""):
    print(f"   {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{extra}]" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


def T(h, m, s=0, day_offset=0):
    return DS + day_offset * 86400 + h * 3600 + m * 60 + s


def bar_ts(h, m):
    return T(h, m)


# ── fakes ────────────────────────────────────────────────────────────────────
class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


class FakeKite:
    """historical_data over scripted 5m bars; ltp over a mutable price dict."""
    def __init__(self, clock):
        self.clock = clock
        self.bars = {256265: [], 265: []}      # token -> [(ts, o, h, l, c)]
        self.px = {}                            # 'NFO:SYM' / 'NSE:NIFTY 50' -> price
        self.calls = {"hist": 0, "ltp": 0}

    def historical_data(self, instrument_token, from_date, to_date, interval):
        assert interval == "5minute"
        self.calls["hist"] += 1
        out = []
        for ts, o, h, l, c in self.bars[int(instrument_token)]:
            if ts <= self.clock():               # Kite also returns the bar in progress
                out.append({"date": datetime.fromtimestamp(ts, TZ), "open": o, "high": h, "low": l, "close": c})
        return out

    def ltp(self, keys):
        self.calls["ltp"] += 1
        return {k: {"last_price": self.px[k]} for k in keys if self.px.get(k)}


def rows():
    out = []
    tok = 900000
    for inst, exch, seg, base, step, lot, exps in (
            ("NIFTY", "NFO", "NFO-OPT", 25000, 50, 65, (DAY, NEXT)),
            ("SENSEX", "BFO", "BFO-OPT", 82000, 100, 20, (date(2026, 10, 29), date(2026, 11, 5)))):
        for exp in exps + (DAY - timedelta(days=7),):       # an expired week must be ignored
            for i in range(-25, 26):
                k = base + i * step
                for t in ("CE", "PE"):
                    tok += 1
                    out.append({"tradingsymbol": f"{inst}{exp:%y%m%d}{k}{t}", "instrument_token": tok, "name": inst,
                                "exchange": exch, "segment": seg, "instrument_type": t, "strike": float(k),
                                "expiry": exp, "lot_size": lot})
    return out


ROWS = rows()


def sym(inst, k, t, exp=DAY):
    return f"{inst}{exp:%y%m%d}{k}{t}"


def set_chain(kite, inst="NIFTY", exp=DAY, prices=None, exch="NFO"):
    """Default NIFTY chain: CE 24950=205, 25000=180, 25050=160, 25100=140 …"""
    for r in ROWS:
        if r["name"] != inst or r["expiry"] != exp:
            continue
        kite.px[f"{exch}:{r['tradingsymbol']}"] = 0
    for (k, t), v in (prices or {}).items():
        kite.px[f"{exch}:{sym(inst, k, t, exp)}"] = v


NIFTY_CE = {(24950, "CE"): 205, (25000, "CE"): 180, (25050, "CE"): 160, (25100, "CE"): 140}
NIFTY_PE = {(24850, "PE"): 210, (24800, "PE"): 190, (24750, "PE"): 165, (24700, "PE"): 120}


class Recorder:
    def __init__(self):
        self.inserts, self.closes, self.alerts, self.notes = [], [], [], []

    def insert(self, **k): self.inserts.append(k)
    def close(self, **k): self.closes.append(k)
    def alert(self, code, msg, **k): self.alerts.append((code, msg, k))


class Notifier:
    def __init__(self): self.sent = []
    def __getattr__(self, name):
        return lambda payload: self.sent.append((name, payload))


class FakeExec:
    def __init__(self, name="ZerodhaOrderExecutor"):
        self.__class__ = type(name, (FakeExec,), {})
        self.orders, self.positions_result, self.fill = [], [], 181.0
        self.sell_fill = None

    def place_buy(self, symbol, token, qty):
        self.orders.append(("BUY", symbol, qty))
        self.positions_result = [{"tradingsymbol": symbol, "quantity": qty}]
        return "OID-B", 0.0, qty

    def place_market_sell(self, symbol, qty):
        self.orders.append(("SELL", symbol, qty))
        return "OID-S"

    def get_order_fill(self, oid):
        if oid == "OID-B":
            return {"status": "COMPLETE", "avg_price": self.fill, "found": True}
        return {"status": "COMPLETE", "avg_price": self.sell_fill or 0, "found": True}

    def get_open_positions_or_none(self):
        return self.positions_result


def harness(mode="PAPER", start=T(9, 0), conn=None, executor=None):
    clock = Clock(start)
    kite = FakeKite(clock)
    conn = conn or _mem()
    repo = LvbRepo(conn_fn=lambda: conn, clock=clock)
    rec = Recorder()
    M.insert_paper_trade, M.close_paper_trade, M.record_alert = rec.insert, rec.close, rec.alert
    M.write_audit_log = E.write_audit_log = LOG.append
    cfg = {"trade_execution_mode": mode}
    mgr = M.LvbManager(repo, executor=executor, cfg_fn=lambda: cfg, notifier=Notifier(), clock=clock,
                       sleep=lambda s: setattr(clock, "t", clock.t + s))
    mgr._allow_degraded = True
    feed = E.LvbFeed(lambda: kite, rows_fn=lambda: ROWS)
    eng = E.LvbEngine(mgr, repo, feed=feed, clock=clock, sleep=lambda s: None, trading_day_fn=lambda d: True)
    return clock, kite, repo, mgr, eng, rec, cfg


def _mem():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def run(eng, clock, until, step=1):
    while clock.t < until:
        eng.tick(clock.t)
        clock.t += step
    eng.tick(clock.t)


def addbar(kite, h, m, o, c, token=256265, hi=None, lo=None):
    kite.bars[token].append((bar_ts(h, m), o, hi or max(o, c), lo or min(o, c), c))


def cond(repo, clock, **k):
    base = {"instrument": "NIFTY", "upper": 25000, "lower": 24800}
    base.update(k)
    clean, err = core.validate_condition(base)
    assert not err, err
    return repo.create(DAY.isoformat(), clean)


# ── core ─────────────────────────────────────────────────────────────────────
def test_core():
    print("core")
    c, e = core.validate_condition({"instrument": "nifty", "upper": "25000"})
    check("C1 defaults: band 150–200, NIFTY SL 25 pts, NIFTY TP 50, EOD 15:15, 1 lot",
          e is None and c["prem_min"] == 150 and c["prem_max"] == 200 and c["sl_pts"] == 25 and c["tp_pts"] == 50
          and c["eod"] == "15:15" and c["lots"] == 1 and c["lower"] is None, e)
    c, e = core.validate_condition({"instrument": "SENSEX", "lower": 81000})
    check("C2 SENSEX defaults: TP 150, SL 75 pts", e is None and c["tp_pts"] == 150 and c["sl_pts"] == 75, e)
    check("C2b SL 0 / negative refused (a stop is mandatory)",
          core.validate_condition({"instrument": "NIFTY", "upper": 1, "sl_pts": 0})[1] is not None
          and core.validate_condition({"instrument": "NIFTY", "upper": 1, "sl_pts": -5})[1] is not None)
    check("C3 neither limit → error", core.validate_condition({"instrument": "NIFTY"})[1] is not None)
    check("C4 upper ≤ lower → error",
          core.validate_condition({"instrument": "NIFTY", "upper": 24800, "lower": 24800})[1] is not None)
    check("C5 fat-finger: 2500 vs spot 25000 refused",
          core.validate_condition({"instrument": "NIFTY", "upper": 2500}, spot=25000)[1] is not None)
    check("C6 EOD 15:25 refused (above 15:20), 09:19 refused",
          core.validate_condition({"instrument": "NIFTY", "upper": 1, "eod": "15:25"})[1] is not None
          and core.validate_condition({"instrument": "NIFTY", "upper": 1, "eod": "09:19"})[1] is not None)
    check("C7 lots 0 and 21 refused",
          core.validate_condition({"instrument": "NIFTY", "upper": 1, "lots": 0})[1] is not None
          and core.validate_condition({"instrument": "NIFTY", "upper": 1, "lots": 21})[1] is not None)
    check("C8 TP 0 allowed (no target)", core.validate_condition({"instrument": "NIFTY", "upper": 1, "tp_pts": 0})[0]
          ["tp_pts"] == 0)
    b = core.breakout
    check("C9 fresh cross up → CE; prev beyond → None; close == level → None",
          b(24990, 25010, 25000, None) == "CE" and b(25005, 25010, 25000, None) is None
          and b(24990, 25000, 25000, None) is None)
    check("C10 prev exactly AT the level counts as inside", b(25000, 25001, 25000, None) == "CE")
    check("C11 PE mirror", b(24810, 24790, None, 24800) == "PE" and b(24795, 24790, None, 24800) is None)
    bars = [{"ts": 0, "open": 25020, "close": 25030}, {"ts": 300, "open": 25030, "close": 25040}]
    check("C12 first bar's reference is its OPEN (gap open beyond the level never triggers)",
          core.reference_for(bars, 0) == 25020 and b(core.reference_for(bars, 0), 25030, 25000, None) is None)
    check("C13 TP measured from the LIMIT: 25000+50, 24800−50; tp 0 → None",
          core.target_spot("CE", 25000, 24800, 50) == 25050 and core.target_spot("PE", 25000, 24800, 50) == 24750
          and core.target_spot("CE", 25000, None, 0) is None)
    check("C14 spot stop measured from the LIMIT: CE 25000−25 = 24975, PE 24800+25 = 24825",
          core.stop_spot("CE", 25000, 24800, 25) == 24975 and core.stop_spot("PE", 25000, 24800, 25) == 24825)
    check("C15 spot SL: CE spot ≤ stop, PE spot ≥ stop; TP otherwise; missing prices never exit",
          core.exit_reason("CE", 150, 24975, None, 25050, 24975) == "SL"
          and core.exit_reason("CE", 150, 24976, None, 25050, 24975) is None
          and core.exit_reason("PE", 150, 24825, None, 24750, 24825) == "SL"
          and core.exit_reason("CE", 150, 25060, None, 25050, 24975) == "TP"
          and core.exit_reason("PE", 150, 24740, None, 24750, 24825) == "TP"
          and core.exit_reason("CE", None, None, None, 25050, 24975) is None)
    check("C15b legacy premium stop still honoured for old rows (sl_spot None)",
          core.exit_reason("CE", 120, 25010, 126.7, 25050, None) == "SL" and core.sl_price(181.0, 30) == 126.7)
    exp, cands = core.chain_rows(ROWS, "NIFTY", "CE", 25000, DAY)
    check("C16 chain: nearest listed expiry (expiry day itself), CE only, ±20 strikes",
          exp == DAY and cands and all(x["sym"].endswith("CE") for x in cands) and len(cands) == 41)
    exp, cands = core.chain_rows(ROWS, "SENSEX", "PE", 82000, DAY)
    check("C17 SENSEX chain = BFO, lot 20, expiry 29-Oct", exp == date(2026, 10, 29) and cands[0]["exchange"] == "BFO"
          and cands[0]["lot_size"] == 20)
    ltps = {sym("NIFTY", k, t): v for (k, t), v in NIFTY_CE.items()}
    exp, cands = core.chain_rows(ROWS, "NIFTY", "CE", 25010, DAY)
    p = core.pick_option(cands, ltps, 150, 200, 25010)
    check("C18 pick = highest premium inside ₹150–200 (25000CE @180, not 24950 @205)",
          p and p["strike"] == 25000 and p["ltp"] == 180)
    check("C19 nothing in band → None", core.pick_option(cands, ltps, 300, 400, 25010) is None)


# ── engine scenarios ─────────────────────────────────────────────────────────
def test_paper_ce_tp():
    print("paper — CE breakout → TP on spot")
    clock, kite, repo, mgr, eng, rec, _ = harness()
    cid = cond(repo, clock)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)                    # closes 09:25 above 25000 → CE
    run(eng, clock, T(9, 25, 4))
    check("E1 nothing before the 09:25 bar has settled", not mgr.pos)
    run(eng, clock, T(9, 25, 7))
    r = repo.get(cid)
    check("E2 CE bought on the 09:25 close: 25000CE @180, qty 65", r["status"] == "TRIGGERED"
          and r["symbol"] == sym("NIFTY", 25000, "CE") and r["entry_px"] == 180 and r["qty"] == 65, r and r["status"])
    check("E3 SL spot 24975 (limit − 25), TP spot 25050 (limit + 50), no premium stop",
          r["sl_spot"] == 24975 and r["tp_spot"] == 25050 and r["sl_px"] is None, (r["sl_spot"], r["sl_px"]))
    check("E4 paper_trades row: LVB_V1 PAPER, sl_price 0 and tp_price 0 (spot levels never stored as premiums)",
          rec.inserts and rec.inserts[0]["strategy_name"] == "LVB_V1" and rec.inserts[0]["trade_mode"] == "PAPER"
          and rec.inserts[0]["tp_price"] == 0.0 and rec.inserts[0]["sl_price"] == 0.0)
    kite.px["NSE:NIFTY 50"] = 25040
    kite.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 200
    run(eng, clock, T(9, 40))
    check("E5 spot 25040 < 25050 → still open", cid in mgr.pos)
    kite.px["NSE:NIFTY 50"] = 25051
    kite.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 214
    run(eng, clock, T(9, 40, 4))
    r = repo.get(cid)
    check("E6 spot 25051 ≥ 25050 → TP at the option LTP 214", r["exit_reason"] == "TP" and r["exit_px"] == 214
          and rec.closes and rec.closes[-1]["exit_reason"] == "TP")
    addbar(kite, 9, 25, 25012, 24990)
    addbar(kite, 9, 30, 24990, 25020)                     # a second fresh cross
    run(eng, clock, T(9, 40, 30))
    check("E7 one trade per condition: a later fresh cross does nothing", len(rec.inserts) == 1)


def test_gap_and_fresh_cross():
    print("gap open / alert created while already beyond")
    clock, kite, repo, mgr, eng, rec, _ = harness()
    cid = cond(repo, clock)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 25020, 25030)                     # gap OPEN above 25000
    addbar(kite, 9, 20, 25030, 25045)
    run(eng, clock, T(9, 30, 10))
    check("G1 gap open beyond the level → no trade", not rec.inserts and repo.get(cid)["status"] == "ARMED")
    addbar(kite, 9, 25, 25045, 24995)                     # back inside
    addbar(kite, 9, 30, 24995, 25008)                     # fresh cross
    run(eng, clock, T(9, 35, 10))
    check("G2 comes back inside, crosses again → CE", len(rec.inserts) == 1)

    clock, kite, repo, mgr, eng, rec, _ = harness(start=T(10, 2))
    set_chain(kite, prices=NIFTY_CE)
    t = 9 * 60 + 15
    while t < 10 * 60 + 5:                                # spot above 25000 all morning
        addbar(kite, t // 60, t % 60, 25010, 25020)
        t += 5
    cid = cond(repo, clock)                               # created 10:02, spot already beyond
    addbar(kite, 10, 0, 25020, 25030)
    run(eng, clock, T(10, 5, 10))
    check("G3 alert created while beyond → waits (no trade)", not rec.inserts)
    addbar(kite, 10, 5, 25030, 24980)
    addbar(kite, 10, 10, 24980, 25004)
    run(eng, clock, T(10, 15, 10))
    check("G4 … then trades the next fresh cross", len(rec.inserts) == 1 and repo.get(cid)["status"] == "TRIGGERED")


def test_pe_sl_eod():
    print("PE breakout → SL; another → EOD")
    clock, kite, repo, mgr, eng, rec, _ = harness()
    a = cond(repo, clock, upper=None, lower=24800)
    b = cond(repo, clock, upper=None, lower=24800, eod="11:00", sl_pts=60)
    set_chain(kite, prices=NIFTY_PE)
    addbar(kite, 9, 15, 24830, 24810)
    addbar(kite, 9, 20, 24810, 24790)
    run(eng, clock, T(9, 25, 7))
    ra, rb = repo.get(a), repo.get(b)
    check("P1 both conditions trade the same bar (each its own trade) — 24800PE @190",
          ra["status"] == rb["status"] == "TRIGGERED" and ra["symbol"] == sym("NIFTY", 24800, "PE")
          and ra["entry_px"] == 190 and len(rec.inserts) == 2)
    check("P2 TP spot = Lower − 50 = 24750; SL spot A = Lower + 25 = 24825, B = Lower + 60 = 24860",
          ra["tp_spot"] == 24750 and ra["sl_spot"] == 24825 and rb["sl_spot"] == 24860)
    kite.px["NSE:NIFTY 50"] = 24824
    kite.px[f"NFO:{sym('NIFTY', 24800, 'PE')}"] = 168
    run(eng, clock, T(9, 29))
    check("P3a spot 24824 < A's stop 24825 → both still open", a in mgr.pos and b in mgr.pos)
    kite.px["NSE:NIFTY 50"] = 24826
    kite.px[f"NFO:{sym('NIFTY', 24800, 'PE')}"] = 163
    run(eng, clock, T(9, 30))
    check("P3 spot 24826 ≥ 24825 → A stops out at the option LTP 163; B (stop 24860) holds",
          repo.get(a)["exit_reason"] == "SL" and repo.get(a)["exit_px"] == 163 and b in mgr.pos)
    kite.px[f"NFO:{sym('NIFTY', 24800, 'PE')}"] = 150
    run(eng, clock, T(10, 59, 58))
    check("P4 B still open at 10:59", b in mgr.pos)
    run(eng, clock, T(11, 0, 3))
    check("P5 B squared off at its own EOD 11:00 @150", repo.get(b)["exit_reason"] == "EOD"
          and repo.get(b)["exit_px"] == 150)


def test_skips_stay_armed():
    print("untradeable breakouts leave the condition ARMED")
    clock, kite, repo, mgr, eng, rec, _ = harness()
    cid = cond(repo, clock, prem_min=300, prem_max=400)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    r = repo.get(cid)
    check("S1 no strike in ₹300–400 → ARMED with a NO_STRIKE note", r["status"] == "ARMED"
          and "NO_STRIKE" in (r["note"] or ""), r["note"])

    clock, kite, repo, mgr, eng, rec, _ = harness()
    cid = cond(repo, clock, tp_pts=10)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25015)                     # close 25015 ≥ target 25010
    run(eng, clock, T(9, 25, 8))
    check("S2 breakout candle already past the spot target → not traded, ARMED",
          not rec.inserts and repo.get(cid)["status"] == "ARMED" and "target" in (repo.get(cid)["note"] or ""))

    clock, kite, repo, mgr, eng, rec, cfg = harness(mode="OFF")
    cid = cond(repo, clock)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    check("S3 mode OFF → breakout ignored, noted, ARMED", not rec.inserts and repo.get(cid)["status"] == "ARMED"
          and "OFF" in (repo.get(cid)["note"] or ""))


def test_late_and_expiry():
    print("restart late / EOD expiry")
    clock, kite, repo, mgr, eng, rec, _ = harness(start=T(10, 30))
    conn_cid = None
    set_chain(kite, prices=NIFTY_CE)
    cid = repo.create(DAY.isoformat(), core.validate_condition({"instrument": "NIFTY", "upper": 25000})[0])
    repo.conn().execute("UPDATE lvb_conditions SET created_at=? WHERE id=?", (T(9, 0), cid))
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)                     # breakout at 09:25 while the app was down
    t = 9 * 60 + 25
    while t < 10 * 60 + 25:
        addbar(kite, t // 60, t % 60, 25012, 25015)
        t += 5
    run(eng, clock, T(10, 30, 10))
    r = repo.get(cid)
    check("L1 breakout from before the restart is NOT traded (late), still ARMED with a note",
          not rec.inserts and r["status"] == "ARMED" and "late" in (r["note"] or ""), r["note"])
    addbar(kite, 10, 30, 25015, 24990)
    addbar(kite, 10, 35, 24990, 25003)
    run(eng, clock, T(10, 40, 10))
    check("L2 the next fresh cross trades", len(rec.inserts) == 1)

    clock, kite, repo, mgr, eng, rec, _ = harness()
    cid = cond(repo, clock, eod="09:30")
    run(eng, clock, T(9, 30, 2))
    check("X1 ARMED past its EOD → EXPIRED", repo.get(cid)["status"] == "EXPIRED")
    old = repo.create((DAY - timedelta(days=1)).isoformat(),
                      core.validate_condition({"instrument": "NIFTY", "upper": 25000})[0])
    eng.booted = False
    eng.tick(clock.t)
    check("X2 an earlier day's ARMED condition expires at boot", repo.get(old)["status"] == "EXPIRED")


def test_restart_resume():
    print("restart resume / stale")
    conn = _mem()
    clock, kite, repo, mgr, eng, rec, _ = harness(conn=conn)
    cid = cond(repo, clock)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    check("R0 position open", cid in mgr.pos)
    clock2, kite2, repo2, mgr2, eng2, rec2, _ = harness(conn=conn, start=T(9, 50))
    set_chain(kite2, prices={(25000, "CE"): 170})
    kite2.px["NSE:NIFTY 50"] = 25010
    run(eng2, clock2, T(9, 50, 3))
    check("R1 a new process adopts the open trade from lvb_conditions (spot stop + target)", cid in mgr2.pos
          and mgr2.pos[cid].sl_spot == 24975 and mgr2.pos[cid].tp_spot == 25050)
    kite2.px["NSE:NIFTY 50"] = 24974
    kite2.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 120
    run(eng2, clock2, T(9, 50, 8))
    check("R2 … and still exits it by the spot SL", repo2.get(cid)["exit_reason"] == "SL")

    conn = _mem()
    clock, kite, repo, mgr, eng, rec, _ = harness(conn=conn)
    cid = cond(repo, clock)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    clock2, kite2, repo2, mgr2, eng2, rec2, _ = harness(conn=conn, start=T(9, 20, 0, day_offset=1))
    run(eng2, clock2, T(9, 20, 2, day_offset=1))
    check("R3 a prior day's open trade → STALE at entry + critical alert", repo2.get(cid)["exit_reason"] == "STALE"
          and any(a[0] == "STALE_ROW" and a[2].get("severity") == "critical" for a in rec2.alerts))


def test_live():
    print("LIVE orders")
    ex = FakeExec()
    clock, kite, repo, mgr, eng, rec, _ = harness(mode="LIVE", executor=ex)
    cid = cond(repo, clock)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    r = repo.get(cid)
    check("V1 LIVE buy at the broker fill 181 (not the quote)", ex.orders[0] == ("BUY", sym("NIFTY", 25000, "CE"), 65)
          and r["entry_px"] == 181 and r["mode"] == "LIVE" and rec.inserts[0]["trade_mode"] == "LIVE")
    ex.positions_result = None
    kite.px["NSE:NIFTY 50"] = 24960
    kite.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 100
    run(eng, clock, T(9, 26, 0))
    check("V2 SL hit but holdings unreadable → NO sell order, backoff", len(ex.orders) == 1 and cid in mgr.pos)
    ex.positions_result = [{"tradingsymbol": sym("NIFTY", 25000, "CE"), "quantity": 65}]
    ex.sell_fill = 99.5
    run(eng, clock, T(9, 26, 20))
    check("V3 holdings readable again → sells, books the fill 99.5", ex.orders[-1][0] == "SELL"
          and repo.get(cid)["exit_px"] == 99.5 and repo.get(cid)["exit_reason"] == "SL")

    ex = FakeExec("AngelOneExecutor")
    clock, kite, repo, mgr, eng, rec, _ = harness(mode="LIVE", executor=ex)
    cid = cond(repo, clock, instrument="SENSEX", upper=82000, lower=None)
    set_chain(kite, inst="SENSEX", exp=date(2026, 10, 29), exch="BFO", prices={(82000, "CE"): 190})
    addbar(kite, 9, 15, 81960, 81990, token=265)
    addbar(kite, 9, 20, 81990, 82020, token=265)
    run(eng, clock, T(9, 25, 8))
    check("V4 SENSEX LIVE on a non-Zerodha executor → refused, ZERO orders, still ARMED",
          not ex.orders and repo.get(cid)["status"] == "ARMED" and "EXCHANGE_UNSUPPORTED" in (repo.get(cid)["note"] or ""))

    ex = FakeExec()
    ex.fill = 190.5
    clock, kite, repo, mgr, eng, rec, _ = harness(mode="LIVE", executor=ex)
    cid = cond(repo, clock, instrument="SENSEX", upper=82000, lower=None, lots=2)
    set_chain(kite, inst="SENSEX", exp=date(2026, 10, 29), exch="BFO", prices={(82000, "CE"): 190})
    addbar(kite, 9, 15, 81960, 81990, token=265)
    addbar(kite, 9, 20, 81990, 82020, token=265)
    run(eng, clock, T(9, 25, 8))
    r = repo.get(cid)
    check("V5 SENSEX LIVE on Zerodha: BFO contract, 2 lots × 20 = 40, TP spot 82150",
          ex.orders == [("BUY", sym("SENSEX", 82000, "CE", date(2026, 10, 29)), 40)] and r["exchange"] == "BFO"
          and r["tp_spot"] == 82150)
    ex.positions_result = []
    out = eng.request_close(cid)
    check("V6 manual square-off, broker already flat → BROKER_FLAT, nothing sold",
          out.get("ok") and repo.get(cid)["exit_reason"] == "BROKER_FLAT" and ex.orders[-1][0] == "BUY")


def test_manual_and_kill():
    print("manual square-off / kill")
    clock, kite, repo, mgr, eng, rec, _ = harness()
    a, b = cond(repo, clock), cond(repo, clock, lots=3)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    kite.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 185
    out = eng.request_close(a)
    check("K1 manual square-off one condition @ LTP 185", out.get("exit_price") == 185
          and repo.get(a)["exit_reason"] == "MANUAL" and b in mgr.pos)
    res = mgr.kill_adapter()
    check("K2 kill adapter flattens the rest", res["closed"] == 1 and res["remaining"] == 0
          and repo.get(b)["exit_reason"] == "KILL")
    check("K3 3 lots → qty 195", rec.inserts[1]["qty"] == 195)


def test_legacy_and_migration():
    print("legacy premium-% rows / table migration (LVB_SL_SPOT_20261029)")
    old_schema = """CREATE TABLE lvb_conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, instrument TEXT NOT NULL, upper REAL, lower REAL,
        prem_min REAL NOT NULL, prem_max REAL NOT NULL, sl_pct REAL NOT NULL, tp_pts REAL NOT NULL, eod TEXT NOT NULL,
        lots INTEGER NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER, note TEXT,
        side TEXT, trigger_bar_ts INTEGER, trigger_close REAL, mode TEXT, symbol TEXT, token INTEGER, exchange TEXT,
        expiry TEXT, strike REAL, lot_size INTEGER, qty INTEGER, entry_ts INTEGER, entry_px REAL, entry_spot REAL,
        sl_px REAL, tp_spot REAL, paper_trade_id TEXT, exit_ts INTEGER, exit_px REAL, exit_spot REAL,
        exit_reason TEXT, mark REAL, mark_spot REAL)"""
    conn = _mem()
    conn.execute(old_schema)
    conn.execute("INSERT INTO lvb_conditions (day, instrument, upper, lower, prem_min, prem_max, sl_pct, tp_pts, eod,"
                 " lots, status, created_at) VALUES (?, 'NIFTY', 25000, NULL, 150, 200, 30, 50, '15:15', 1, 'ARMED', ?)",
                 (DAY.isoformat(), T(9, 0)))
    clock, kite, repo, mgr, eng, rec, _ = harness(conn=conn)
    cols = {r[1] for r in repo.conn().execute("PRAGMA table_info(lvb_conditions)").fetchall()}
    check("M1 an existing old-shape table gains sl_pts + sl_spot in place", {"sl_pts", "sl_spot"} <= cols)
    new = cond(repo, clock, upper=None, lower=24800)
    check("M2 new conditions insert fine into the migrated table (sl_pct written 0)",
          repo.get(new)["sl_pts"] == 25 and repo.get(new)["sl_pct"] == 0)
    set_chain(kite, prices=NIFTY_CE)
    addbar(kite, 9, 15, 24960, 24990)
    addbar(kite, 9, 20, 24990, 25012)
    run(eng, clock, T(9, 25, 8))
    r = repo.get(1)
    check("M3 the legacy row trades with its premium stop (180 × 0.70 = 126), no spot stop",
          r["status"] == "TRIGGERED" and r["sl_px"] == 126.0 and r["sl_spot"] is None, (r["sl_px"], r["sl_spot"]))
    kite.px["NSE:NIFTY 50"] = 24950                  # below where a spot stop would be — must NOT matter
    kite.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 130
    run(eng, clock, T(9, 26))
    check("M4 legacy row ignores spot; holds at LTP 130 > 126", 1 in mgr.pos)
    kite.px[f"NFO:{sym('NIFTY', 25000, 'CE')}"] = 125
    run(eng, clock, T(9, 26, 4))
    check("M5 … and stops out on the premium (LTP 125 ≤ 126)", repo.get(1)["exit_reason"] == "SL")
    ok = repo.update_armed(new, core.validate_condition({"instrument": "NIFTY", "lower": 24790, "sl_pts": 40})[0])
    check("M6 editing stores sl_pts", ok and repo.get(new)["sl_pts"] == 40)


if __name__ == "__main__":
    for fn in (test_core, test_legacy_and_migration, test_paper_ce_tp, test_gap_and_fresh_cross, test_pe_sl_eod, test_skips_stay_armed,
               test_late_and_expiry, test_restart_resume, test_live, test_manual_and_kill):
        fn()
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
    sys.exit(1 if FAILS else 0)
