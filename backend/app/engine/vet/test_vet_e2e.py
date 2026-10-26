# backend/app/engine/vet/test_vet_e2e.py
#
# ── E2E_TESTKIT_20261027 ── VET_V1 end to end, every flow, through the REAL
# ExecutionRouter over the Kite-shaped fake broker. Decisions are fed exactly
# as the selection loop does (on_decision / check_sltp / eod / expiry / kill /
# resume_from_db / retry_exit). Run standalone:  python3 test_vet_e2e.py
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.engine.vet import vet_manager as VM                     # noqa: E402
from app.engine.vet.vet_common import VetRepo                    # noqa: E402
from app.engine.vet.vet_live_core import ENTER, EXIT, FLIP       # noqa: E402
from app.execution.execution_router import ExecutionRouter       # noqa: E402
from app.execution.testkit_fake_kite import FakeKite, isolate    # noqa: E402

FAILS, PAGES = [], []
SPOT = 24000


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def prices():
    out = {}
    for k in range(23000, 25050, 100):
        for side in ("CE", "PE"):
            itm = (SPOT - k) if side == "CE" else (k - SPOT)
            out[f"NIFTY26OCT{k}{side}"] = round(max(1.0, 120 + 0.5 * itm - 0.002 * itm * itm
                                                     if abs(itm) < 600 else 1.0), 2)
    return out


def setup(**cfg_over):
    isolate()
    PAGES.clear()
    kite = FakeKite(prices())
    cfg = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 3.0,
           "atm_offset": 0, "eod_square": True, "_spot": SPOT,
           "quantity": {"lots": 1, "lot_size": 65}}
    cfg.update(cfg_over)
    db = os.path.join(tempfile.mkdtemp(prefix="vete2e_"), "v.db")
    repo = VetRepo(db)
    repo.ensure_schema()

    def chain(side, ts):
        return [{"tradingsymbol": s, "token": 1000 + int(s[10:15]), "strike": float(s[10:15]),
                 "expiry": "2026-10-27", "instrument_type": side, "ltp": p}
                for s, p in kite.px.items() if s.endswith(side)]

    m = VM.VetManager(cfg, repo=repo, chain_fn=chain, quote_fn=lambda s: kite.px.get(s),
                      executor=ExecutionRouter(kite), mode="LIVE")
    m._sleep, m._clock = kite.clock.sleep, kite.clock
    m._page = lambda msg, code="": PAGES.append(msg)
    return m, kite, db


def rows(db):
    return list(sqlite3.connect(db).execute(
        "SELECT leg_role,direction,status,entry_price,exit_price,exit_reason,entry_order_id "
        "FROM vet_trades ORDER BY id"))


def dec(action, side="PE", ts=1000):
    return {"action": action, "side": side, "bar_ts": ts, "condition": 1}


def t_enter_and_signal_exit():
    m, kite, db = setup()
    m.on_decision(dec(ENTER), ts=1000)
    main, wing = m.pos["main"]["tradingsymbol"], m.pos["wing"]["tradingsymbol"]
    check("ENTER: wing bought, then short sold", kite.kinds()[:2] == [("BUY", wing), ("SELL", main)],
          str(kite.kinds()))
    r = rows(db)
    check("ENTER: both legs saved with string order ids and fill prices",
          len(r) == 2 and all(isinstance(x[6], str) for x in r)
          and r[1][3] == kite.px[main], str(r))
    kite.set_px(main, kite.px[main] - 20)
    m.on_decision(dec(EXIT), ts=2000)
    r = rows(db)
    check("SIGNAL_EXIT: short bought back, THEN wing sold",
          kite.kinds()[-2:] == [("BUY", main), ("SELL", wing)], str(kite.kinds()))
    check("SIGNAL_EXIT: flat, rows CLOSED at broker fills",
          kite.flat() and m.pos is None and all(x[2] == "CLOSED" for x in r)
          and r[1][4] == kite.px[main], str(r))


def t_flip():
    m, kite, db = setup()
    m.on_decision(dec(ENTER, "PE"), ts=1000)
    old = m.pos["main"]["tradingsymbol"]
    m.on_decision(dec(FLIP, "CE", 2000), ts=2000)
    check("FLIP: old PE spread closed and new CE spread opened",
          m.pos and m.pos["side"] == "CE" and kite.net.get(old) == 0
          and kite.net.get(m.pos["main"]["tradingsymbol"]) == -65)


def t_sl_tp():
    m, kite, db = setup(sl_pct=20, tp_pct=50)
    m.on_decision(dec(ENTER), ts=1000)
    main = m.pos["main"]["tradingsymbol"]
    e = m.pos["main"]["entry_price"]
    kite.set_px(main, round(e * 1.25, 1))
    hit = m.check_sltp(1500)
    check("SL: 5m-close mark above SL closes the position (short first)",
          hit == "SL" and m.pos is None and kite.flat() and rows(db)[1][5] == "SL")
    m.on_decision(dec(ENTER, "CE", 3000), ts=3000)
    main = m.pos["main"]["tradingsymbol"]
    kite.set_px(main, round(m.pos["main"]["entry_price"] * 0.4, 1))
    check("TP: mark below TP closes the position", m.check_sltp(3500) == "TP" and kite.flat())


def t_eod_expiry_kill():
    m, kite, db = setup()
    m.on_decision(dec(ENTER), ts=1000)
    check("EOD: square-off flattens", m.eod_square_off(5000) is not None and kite.flat())
    m.on_decision(dec(ENTER, "CE", 6000), ts=6000)
    check("EXPIRY: expiry exit flattens", m.expiry_exit(7000) is not None and kite.flat())
    m.on_decision(dec(ENTER, "PE", 8000), ts=8000)
    m.kill(9000)
    check("KILL: flattens and freezes", kite.flat() and m.frozen)
    check("KILL: no new entry after kill",
          m.open_position("PE", ts=9500, bar_ts=9500, condition=1) is None)


def t_restart_resume():
    m, kite, db = setup()
    m.on_decision(dec(ENTER), ts=1000)
    gid = m.pos["group_id"]
    m2 = VM.VetManager(m.cfg, repo=VetRepo(db), chain_fn=m.chain_fn, quote_fn=m.quote_fn,
                       executor=m.executor, mode="LIVE")
    m2._sleep, m2._clock = kite.clock.sleep, kite.clock
    m2._page = lambda msg, code="": PAGES.append(msg)
    check("RESTART: resume_from_db rebuilds the LIVE position (rows exist now)",
          m2.resume_from_db() is not None and m2.pos["group_id"] == gid)
    check("RESTART: no second position opened",
          m2.open_position("PE", ts=1500, bar_ts=1500, condition=1) is None
          and len([e for e in kite.kinds() if e[0] == "SELL"]) == 1)
    m2.on_decision(dec(EXIT), ts=2000)
    check("RESTART: resumed position exits cleanly", kite.flat() and m2.pos is None)


def t_entry_failures():
    m, kite, db = setup()
    wing_syms = [s for s, p in kite.px.items() if s.endswith("PE") and p <= 3.0]
    for s in wing_syms:
        kite.reject[s] = "RMS"
    m.on_decision(dec(ENTER), ts=1000)
    check("Wing rejected: no short sent, nothing open",
          m.pos is None and not [e for e in kite.kinds() if e[0] == "SELL"] and kite.flat())
    m, kite, db = setup()
    for s in kite.px:
        if s.endswith("PE") and kite.px[s] > 3.0:
            kite.reject[s] = "Insufficient funds"
    m.on_decision(dec(ENTER), ts=1000)
    check("Short rejected: wing sold back, flat, no position",
          m.pos is None and kite.flat())


def t_stuck_exit_then_manual():
    m, kite, db = setup()
    m.on_decision(dec(ENTER), ts=1000)
    main, wing = m.pos["main"]["tradingsymbol"], m.pos["wing"]["tradingsymbol"]
    kite.modify_ok = False
    kite.stale_ltp[main] = kite.px[main]
    kite.set_px(main, kite.px[main] * 1.3)
    m.on_decision(dec(EXIT), ts=2000)
    check("Stuck exit: position kept, wing kept, no working order, page sent",
          m.exit_pending and kite.net.get(wing) == 65 and not kite.open_orders() and PAGES)
    kite.manual = lambda s, t, q: kite._new_order(s, t, q, kite.px[s] * (1.5 if t == "BUY" else 0.5))
    kite.manual(main, "BUY", 65)
    kite.manual(wing, "SELL", 65)
    m.retry_exit(2060)
    check("Stuck exit: after a manual flatten the retry books it and stops",
          m.pos is None and kite.flat() and all(x[2] == "CLOSED" for x in rows(db)))


def t_buy_mode():
    m, kite, db = setup(leg_action="BUY", hedge_enabled=False)
    m.on_decision(dec(ENTER, "CE"), ts=1000)
    s = m.pos["main"]["tradingsymbol"]
    check("BUY mode: single long, no wing", m.pos["wing"] is None and kite.net.get(s) == 65)
    m.on_decision(dec(EXIT, "CE"), ts=2000)
    check("BUY mode: exit sells it, flat", kite.flat() and m.pos is None)


def t_naked_sell():
    m, kite, db = setup(hedge_enabled=False)
    m.on_decision(dec(ENTER), ts=1000)
    check("Naked SELL: short only", m.pos["wing"] is None
          and kite.net.get(m.pos["main"]["tradingsymbol"]) == -65)
    m.on_decision(dec(EXIT), ts=2000)
    check("Naked SELL: buy-back flattens", kite.flat())


ALL = [t_enter_and_signal_exit, t_flip, t_sl_tp, t_eod_expiry_kill, t_restart_resume,
       t_entry_failures, t_stuck_exit_then_manual, t_buy_mode, t_naked_sell]

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
    print(f"\nALL VET_V1 E2E FLOWS PASSED ({len(ALL)} scenarios)")
