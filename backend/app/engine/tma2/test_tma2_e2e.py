# backend/app/engine/tma2/test_tma2_e2e.py
#
# ── E2E_TESTKIT_20261027 ── TMA_V2 end to end, entry → exit, every flow,
# through the REAL ExecutionRouter over a Kite-shaped fake broker
# (app/execution/testkit_fake_kite.py). Signal → pending → fill → monitor →
# exit, driven through the manager's public API exactly as the minute
# coordinator does. Run standalone:  python3 test_tma2_e2e.py
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.config import strategy_loader as SL                     # noqa: E402
from app.engine.tma2 import tma2_trade_manager as TM             # noqa: E402
from app.engine.tma2 import tma2_gtt_monitor as GM               # noqa: E402
from app.engine.tma2.tma2_common import TMA2Repo                 # noqa: E402
from app.execution.execution_router import ExecutionRouter       # noqa: E402
from app.execution.testkit_fake_kite import FakeKite, isolate    # noqa: E402

FAILS, ALERTS, CRIT = [], [], []
SELL, HEDGE = "NIFTY26OCT22400CE", "NIFTY26OCT23200CE"
CE_CHAIN = {"NIFTY26OCT22300CE": 210.0, SELL: 160.0, "NIFTY26OCT22500CE": 120.0,
            "NIFTY26OCT23000CE": 20.0, HEDGE: 4.5}
PE_CHAIN = {"NIFTY26OCT22600PE": 150.0, "NIFTY26OCT21800PE": 4.0}


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def ist(hh, mm):
    return int(datetime(2026, 10, 27, hh, mm, tzinfo=timezone.utc).timestamp()) - 19800


class Chain:
    """Duck-typed option chain: flat candle at the broker's current price
    unless a scenario overrides (sym, ts)."""
    def __init__(self, kite):
        self.k, self.over, self.now = kite, {}, 0

    def symbols(self, side):
        return [s for s in self.k.px if s.endswith(side)]

    def candle(self, sym, ts):
        if (sym, ts) in self.over:
            return self.over[(sym, ts)]
        p = self.k.px.get(sym)
        return None if p is None else {"ts": ts, "open": p, "high": p, "low": p, "close": p}

    def meta(self, sym):
        return {"token": hash(sym) % 100000, "strike": int(sym[10:15]), "expiry": "2026-10-27"}

    def last_close_at_or_before(self, sym, ts, *a):
        return self.k.px.get(sym)


def setup(mode="LIVE", **cfg_over):
    isolate()                         # hermetic: no real DB / Telegram / audit log
    TM.record_alert = lambda code, msg="", **k: ALERTS.append((code, msg))
    TM.notify_critical = lambda d: CRIT.append(d["message"])
    for fn in ("notify_trade_entry", "notify_tp_exit", "notify_sl_exit", "notify_manual_exit"):
        setattr(TM, fn, lambda d: None)
    TM.write_audit_log = GM.write_audit_log = lambda m: None
    TM._ENTRY_FILL_CAP_S, TM._ENTRY_FILL_POLL_S = 0.3, 0.05
    ALERTS.clear(); CRIT.clear()
    cfg = {"s1": {"main": {"premium_max": 170, "lots": 1, "sl_pct": 12, "tp_pct": 10,
                           "sl_unit": "PCT", "tp_unit": "ABS"},
                  "hedge": {"premium_max": 5, "lots": 1}, "max_trades_per_day": 0},
           "trade_mode": "INTRADAY", "trade_execution_mode": mode, "exit_time": "15:25"}
    cfg.update(cfg_over)
    SL.load_strategy_config_ex = lambda sid: (cfg, False)
    kite = FakeKite({**CE_CHAIN, **PE_CHAIN})
    db = tempfile.mktemp(suffix=".db")
    m = TM.TMA2TradeManager({}, TMA2Repo(db_path=db), executor=ExecutionRouter(kite))
    m._sleep, m._clock = kite.clock.sleep, kite.clock
    return m, kite, Chain(kite), db


def enter(m, chain, at=(9, 26)):
    ts = ist(*at)
    m.on_signal({"ts": ts, "side": "PE", "cond": "E1", "spot": 22560.0}, chain)
    m.on_minute(ts, None, chain)
    return ts


def rows(db):
    return list(sqlite3.connect(db).execute(
        "SELECT direction,status,entry_price,exit_price,exit_reason,sell_gtt_id "
        "FROM tma2_trades ORDER BY id"))


def mon(m, kite):
    return GM.TMA2GTTMonitor(m.executor, m)


# ── scenarios ───────────────────────────────────────────────────────
def t_paper_cycle():
    m, kite, ch, db = setup(mode="PAPER")
    ts = enter(m, ch)
    check("PAPER: entry opens a group with no broker orders",
          m.group is not None and not kite.events)
    kite.px[SELL] = 9.5
    m.on_minute(ts + 120, None, ch)
    r = rows(db)
    check("PAPER: TP exit closes both rows at model prices",
          m.group is None and [x[1] for x in r] == ["CLOSED", "CLOSED"]
          and r[0][4] == "TP", str(r))


def t_live_entry():
    m, kite, ch, db = setup()
    enter(m, ch)
    ev = kite.kinds()
    g = m.group
    check("LIVE entry: hedge BUY placed and filled BEFORE the SELL",
          ev[:2] == [("BUY", HEDGE), ("SELL", SELL)], str(ev))
    check("LIVE entry: SL GTT placed through the router (no AttributeError)",
          len(kite.active_gtts()) == 1 and g["sell"]["gtt_id"])
    gt = kite.active_gtts()[0] if kite.active_gtts() else {}
    check("LIVE entry: GTT limit buffer is 5% above the trigger",
          gt and abs(gt["limit"] / gt["trigger"] - 1.05) < 0.002, str(gt))
    r = rows(db)
    check("LIVE entry: both rows OPEN at broker fill prices, GTT id persisted",
          [x[1] for x in r] == ["OPEN", "OPEN"] and r[0][2] == 160.0 and r[0][5],
          str(r))
    return m, kite, ch, db


def t_live_tp():
    m, kite, ch, db = setup()
    ts = enter(m, ch)
    kite.set_px(SELL, 9.5)
    m.on_minute(ts + 120, None, ch)
    r = rows(db)
    check("TP: GTT cancelled before the app buy-back", not kite.active_gtts())
    check("TP: short bought back, then hedge sold", kite.kinds()[-2:] == [("BUY", SELL), ("SELL", HEDGE)],
          str(kite.kinds()))
    check("TP: broker flat, no working orders", kite.flat() and not kite.open_orders())
    check("TP: booked at broker fills", r[0][3] == 9.5 and r[0][4] == "TP", str(r))


def t_live_sl_gtt_fires():
    m, kite, ch, db = setup()
    ts = enter(m, ch)
    kite.set_px(SELL, 185.0)                    # crosses the GTT trigger at the broker
    mon(m, kite)._sweep()
    r = rows(db)
    check("SL via GTT: the broker GTT filled the buy-back",
          kite.net.get(SELL) == 0, str(kite.net))
    check("SL via GTT: monitor handed off, hedge sold, group closed",
          m.group is None and kite.flat())
    check("SL via GTT: booked at the GTT fill price", r and r[0][3] == 185.0, str(r))
    n_buys = len([e for e in kite.kinds(SELL) if e[0] == "BUY"])
    m.on_minute(ts + 240, None, ch)
    check("SL via GTT: no second (app) buy-back afterwards",
          len([e for e in kite.kinds(SELL) if e[0] == "BUY"]) == n_buys and kite.flat())


def t_live_sl_gtt_gap():
    m, kite, ch, db = setup()
    enter(m, ch)
    kite.set_px(SELL, 260.0)                    # gap far past the 5% GTT limit
    check("GTT gap: GTT triggered but its limit rests unfilled",
          kite.net.get(SELL) == -65 and len(kite.open_orders()) == 1)
    mon(m, kite)._sweep()
    buys = [e for e in kite.kinds(SELL) if e[0] == "BUY"]
    check("GTT gap: manager re-pegged the GTT's own order — no second buy-back",
          len(buys) == 1 and kite.net.get(SELL) == 0, str(buys))
    check("GTT gap: hedge sold after, group closed, broker flat",
          m.group is None and kite.flat() and not kite.open_orders())


def t_live_candle_sl_no_gtt():
    m, kite, ch, db = setup()
    kite.gtt_fail = True
    ts = enter(m, ch)
    check("No-GTT entry: alert raised, candle monitor protects",
          any(c == "TMA_GTT_FAIL" for c, _ in ALERTS))
    kite.set_px(SELL, 181.0)
    ch.over[(SELL, ts + 120)] = {"ts": ts + 120, "open": 170, "high": 181, "low": 170, "close": 181}
    m.on_minute(ts + 120, None, ch)
    check("Candle SL: app exit closes short then hedge, broker flat",
          m.group is None and kite.flat() and kite.kinds()[-2:] == [("BUY", SELL), ("SELL", HEDGE)])


def t_margin_block():
    m, kite, ch, db = setup()
    kite.margin_available = 40_000            # < 50k × 1.25
    enter(m, ch)
    check("Margin guard: entry skipped, no orders placed",
          m.group is None and not kite.events and m.diag["skipped_margin"] == 1)


def t_hedge_dead():
    m, kite, ch, db = setup()
    kite.reject[HEDGE] = "RMS: blocked"
    enter(m, ch)
    check("Hedge rejected: no SELL ever placed, nothing open",
          m.group is None and not [e for e in kite.kinds() if e[0] == "SELL"] and kite.flat())


def t_sell_rejected_unwinds():
    m, kite, ch, db = setup()
    kite.reject[SELL] = "Insufficient funds"
    enter(m, ch)
    check("Sell rejected after hedge fill: hedge unwound, broker flat",
          m.group is None and kite.flat() and kite.kinds(HEDGE) == [("BUY", HEDGE), ("SELL", HEDGE)])


def t_eod():
    m, kite, ch, db = setup()
    enter(m, ch)
    m.on_minute(ist(15, 25), None, ch)
    r = rows(db)
    check("EOD: hard close at exit_time, GTT cancelled, flat",
          m.group is None and kite.flat() and not kite.active_gtts() and r[0][4] == "EOD", str(r))


def t_kill():
    m, kite, ch, db = setup()
    enter(m, ch)
    m.kill_close(ist(11, 0))
    check("KILL: flattens immediately, GTT cancelled, nothing working",
          m.group is None and kite.flat() and not kite.active_gtts() and not kite.open_orders())


def t_stuck_then_recovers():
    m, kite, ch, db = setup()
    ts = enter(m, ch)
    kite.gtt_fail = False
    kite.modify_ok = False
    kite.stale_ltp[SELL] = 9.5
    kite.set_px(SELL, 12.0)                    # TP touched on the candle, market moved up
    ch.over[(SELL, ts + 120)] = {"ts": ts + 120, "open": 11, "high": 12, "low": 9.5, "close": 12}
    m.on_minute(ts + 120, None, ch)
    check("Stuck exit: nothing booked, hedge kept, page sent",
          m.group is not None and kite.net.get(HEDGE) == 65
          and any("SHORT IS STILL OPEN" in c for c in CRIT))
    check("Stuck exit: the unfilled buy-back was cancelled", not kite.open_orders())
    kite.modify_ok = True
    m.on_minute(ts + 180, None, ch)
    check("Stuck exit: next minute completes it, flat, recovery page",
          m.group is None and kite.flat() and any("COMPLETED" in c for c in CRIT))


def t_restart_adoption():
    m, kite, ch, db = setup()
    ts = enter(m, ch)
    m2 = TM.TMA2TradeManager({}, TMA2Repo(db_path=db), executor=m.executor)
    m2._sleep, m2._clock = kite.clock.sleep, kite.clock
    m2.adopt_rows(TMA2Repo(db_path=db).open_legs(), kite=kite)
    check("Restart: open group adopted with its GTT, not disabled",
          m2.group is not None and not m2.disabled and m2.group["sell"]["gtt_id"])
    kite.set_px(SELL, 9.5)
    m2.on_minute(ts + 180, None, ch)
    check("Restart: adopted group exits normally", m2.group is None and kite.flat())
    m3, kite3, ch3, db3 = setup()
    enter(m3, ch3)
    kite3.net[HEDGE] = 0                       # someone sold the hedge in Kite
    m4 = TM.TMA2TradeManager({}, TMA2Repo(db_path=db3), executor=m3.executor)
    m4.adopt_rows(TMA2Repo(db_path=db3).open_legs(), kite=kite3)
    check("Restart: broker mismatch disables the manager (fail closed)", m4.disabled)


def t_positions_unreadable():
    m, kite, ch, db = setup()
    ts = enter(m, ch)
    kite.positions_readable = False
    kite.set_px(SELL, 9.5)
    m.on_minute(ts + 120, None, ch)
    check("Positions unreadable: exit still confirmed by order fills",
          m.group is None and kite.flat())


def t_recon_stray():
    m, kite, ch, db = setup()
    ts = enter(m, ch)
    kite.set_px(SELL, 9.5)
    m.on_minute(ts + 120, None, ch)
    kite.net[SELL] = 65                        # a stray order fills later
    m._recon_tick(ts + 300); m._recon_tick(ts + 420)
    check("Recon: stray position after close is paged",
          any(c == "TMA_RECON_MISMATCH" for c, _ in ALERTS))


ALL = [t_paper_cycle, t_live_entry, t_live_tp, t_live_sl_gtt_fires, t_live_sl_gtt_gap,
       t_live_candle_sl_no_gtt, t_margin_block, t_hedge_dead, t_sell_rejected_unwinds,
       t_eod, t_kill, t_stuck_then_recovers, t_restart_adoption, t_positions_unreadable,
       t_recon_stray]

if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            import traceback; traceback.print_exc()
            check(f"{t.__name__} raised {e!r}", False)
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL TMA_V2 E2E FLOWS PASSED ({len(ALL)} scenarios)")
