# backend/app/engine/tsg/test_tsg_e2e.py
#
# ── E2E_TESTKIT_20261027 ── TSG_V1 end to end through the REAL
# ExecutionRouter over the Kite-shaped fake broker: enter_day (chain
# snapshot → plan → buys-first live entry) → on_minute MTM evaluation →
# exits (MTM_SL, IV_SL pair, EOD, KILL), entry unwinds, stuck exits and a
# mid-day restart. Run standalone:  python3 test_tsg_e2e.py
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.engine.tsg import tsg_manager as TM                     # noqa: E402
from app.engine.tsg.tsg_live_core import (                       # noqa: E402
    L_OPEN, L_CLOSED, D_OPEN, D_PARTIAL, D_CLOSED)
from app.execution.execution_router import ExecutionRouter       # noqa: E402
from app.execution.testkit_fake_kite import FakeKite, isolate    # noqa: E402

FAILS, ALERTS, BOOK = [], [], {"ins": [], "cls": []}
DAY = datetime(2026, 10, 28, 9, 20, tzinfo=TM.IST)
PX = {"NIFTY26OCT24300CE": 120.0, "NIFTY26OCT24400CE": 84.0, "NIFTY26OCT24500CE": 55.0,
      "NIFTY26OCT25500CE": 4.5, "NIFTY26OCT25600CE": 2.0,
      "NIFTY26OCT23700PE": 118.0, "NIFTY26OCT23600PE": 82.0, "NIFTY26OCT23500PE": 50.0,
      "NIFTY26OCT22500PE": 4.8, "NIFTY26OCT22400PE": 2.2}


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def setup(state_dir=None, kite=None, cfg_over=None):
    isolate()
    ALERTS.clear()
    d = Path(state_dir or tempfile.mkdtemp(prefix="tsge2e_"))
    TM.STATE_DIR, TM.SESSION_PATH = d, d / "TSG_V1_session.json"
    TM.TsgManager._sweep_stale_live_rows = lambda self: None
    TM.TsgManager._alert = lambda self, code, msg, **k: ALERTS.append((code, msg))
    TM.TsgManager._notify_group_entry = lambda self, *a, **k: None
    TM.TsgManager._notify_group_exit = lambda self, *a, **k: None
    TM._now_ist = lambda: DAY
    TM.is_day_blocked = lambda sid: False
    TM.check_strategy_max_loss = lambda sid: False
    TM.insert_trade = lambda **k: (BOOK["ins"].append(k), f"T{len(BOOK['ins'])}")[1]
    TM.close_trade = lambda **k: BOOK["cls"].append(k)
    TM.insert_paper_trade = lambda **k: f"P{len(BOOK['ins'])}"
    TM.close_paper_trade = lambda **k: None
    cfg = {"lots": 1, "lot_size": 65, "mtm_sl_per_lot": 3000, "mtm_sl_hard_mult": 0,
           "mtm_target": 0, "iv_sl_pct": 0, "iv_sl_delta_pts": 0, "min_entry_iv": 0,
           "entry_fill_timeout_s": 2, "entry_repeg_max": 1}
    cfg.update(cfg_over or {})
    TM.load_strategy_config = lambda sid: cfg
    kite = kite or FakeKite(dict(PX))
    m = TM.TsgManager(executor=ExecutionRouter(kite),
                      quote_fn=lambda syms: {s: kite.px[s] for s in syms if s in kite.px})
    m._sleep, m._clock = kite.clock.sleep, kite.clock
    return m, kite, d


def chain():
    rows = [{"tradingsymbol": s, "instrument_type": s[-2:], "strike": int(s[10:15]),
             "instrument_token": 1000 + int(s[10:15])} for s in PX]
    return ("2026-10-30", rows, dict(PX))


def legs(m):
    return {l: m._core.legs[l] for l in m._core.legs}


def shorts_then_wings(kite, start):
    ev = kite.kinds()[start:]
    first_wing_sell = next((i for i, e in enumerate(ev) if e[0] == "SELL"), 99)
    last_short_buy = max((i for i, e in enumerate(ev) if e[0] == "BUY"), default=-1)
    return last_short_buy < first_wing_sell


def minute(m, mm):
    m.on_minute(DAY.replace(minute=mm))


def t_entry():
    m, kite, _ = setup()
    ok = m.enter_day(chain(), mode="LIVE")
    ev = kite.kinds()
    L = legs(m)
    check("ENTRY: day opens with 4 legs", ok and m._core.state == D_OPEN and len(L) == 4)
    check("ENTRY: both wings bought BEFORE either short is sold",
          [e[0] for e in ev] == ["BUY", "BUY", "SELL", "SELL"], str(ev))
    check("ENTRY: each leg booked live at its fill", len(BOOK["ins"]) >= 4
          and all(L[i].entry_price == kite.px[L[i].symbol] for i in L))
    return m, kite


def t_mtm_sl():
    m, kite = t_entry()
    n0 = len(kite.events)
    L = legs(m)
    for i in ("L1", "L2"):
        kite.set_px(L[i].symbol, kite.px[L[i].symbol] + 40)      # −₹5,200 > ₹3,000 SL
    minute(m, 30)
    check("MTM_SL: all four legs closed, broker flat",
          m._core.state == D_CLOSED and kite.flat() and not kite.open_orders())
    check("MTM_SL: shorts bought back before any wing sold", shorts_then_wings(kite, n0),
          str(kite.kinds()[n0:]))
    check("MTM_SL: exits booked at broker fills",
          L["L1"].exit_price == kite.px[L["L1"].symbol] and L["L1"].exit_reason == "MTM_SL")


def t_iv_pair_then_eod():
    m, kite = t_entry()
    m._core.iv_armed_used = True
    m._execute_exits(["L1", "L3"], "IV_SL")
    L = legs(m)
    check("IV_SL pair: L1 short and its L3 hedge closed, L2/L4 still open",
          L["L1"].state == L_CLOSED and L["L3"].state == L_CLOSED
          and L["L2"].state == L_OPEN and m._core.state == D_PARTIAL)
    check("IV_SL pair: hedge exit tagged IV_SL_HEDGE", L["L3"].exit_reason == "IV_SL_HEDGE")
    m.square_off_all("EOD")
    check("EOD: survivors closed, flat", m._core.state == D_CLOSED and kite.flat())


def t_kill():
    m, kite = t_entry()
    res = m.kill_all()
    check("KILL: reports flat, broker flat, nothing working",
          res["ok"] and res["flat"] and kite.flat() and not kite.open_orders())


def t_entry_unwind():
    m, kite, _ = setup()
    kite.reject["NIFTY26OCT23600PE"] = "Insufficient funds"     # L2 short dies
    ok = m.enter_day(chain(), mode="LIVE")
    check("ENTRY UNWIND: L2 rejected → day aborted, every filled leg flattened",
          not ok and kite.flat() and not kite.open_orders())
    check("ENTRY UNWIND: alert names the leg and the broker reason",
          any(c == "TSG_ENTRY_UNWIND" and "Insufficient funds" in msg for c, msg in ALERTS))


def t_stuck_exit_retries():
    m, kite = t_entry()
    L = legs(m)
    s1 = L["L1"].symbol
    kite.modify_ok = False
    kite.stale_ltp[s1] = kite.px[s1]
    kite.set_px(s1, kite.px[s1] + 60)
    kite.set_px(L["L2"].symbol, kite.px[L["L2"].symbol] + 10)
    minute(m, 31)
    check("STUCK: L1 open, its hedge L3 held, others closed, nothing working",
          L["L1"].state == L_OPEN and L["L3"].state == L_OPEN
          and L["L2"].state == L_CLOSED and L["L4"].state == L_CLOSED
          and not kite.open_orders(), str({k: v.state for k, v in L.items()}))
    check("STUCK: page names the open short and the manual order",
          any(c == "TSG_EXIT_STUCK" and "FIRST" in msg for c, msg in ALERTS))
    kite.modify_ok = True
    minute(m, 32)
    check("STUCK: next minute closes L1 then L3, day closed, flat",
          m._core.state == D_CLOSED and kite.flat())


def t_restart_midday():
    m, kite = t_entry()
    d = TM.STATE_DIR
    m2, _, _ = setup(state_dir=d, kite=kite)
    check("RESTART: same-day session restored with all legs open",
          m2._core is not None and len(m2._core.open_ids()) == 4 and not m2._paper)
    check("RESTART: no second entry today", not m2.enter_day(chain(), mode="LIVE"))
    m2.kill_all()
    check("RESTART: restored book flattens", kite.flat())


def t_paper():
    m, kite, _ = setup()
    ok = m.enter_day(chain(), mode="PAPER")
    m.square_off_all("EOD")
    check("PAPER: full day with zero broker orders", ok and not kite.events
          and m._core.state == D_CLOSED)


ALL = [t_entry, t_mtm_sl, t_iv_pair_then_eod, t_kill, t_entry_unwind,
       t_stuck_exit_retries, t_restart_midday, t_paper]

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
    print(f"\nALL TSG_V1 E2E FLOWS PASSED ({len(ALL)} scenarios)")
