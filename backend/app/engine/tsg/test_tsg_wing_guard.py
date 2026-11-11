# backend/app/engine/tsg/test_tsg_wing_guard.py
#
# ── TSG_WING_GUARD_20261111 ── 2026-11-11 Tigris paper entry went in with
# 3 legs (L4 BUY PE missing) and no alert. Cause: the entry chain is cut at
# spot ± 1500 (IC's band); at 4 sessions to expiry no PE <= the ₹8 wing cap
# existed inside that band, and plan_entry() dropped the wing silently.
#
# This suite replays that day and proves: the wider band finds the wing,
# a wing that still has nothing <= cap falls back to the cheapest real OTM
# strike (backtest fail-open parity) with an alert, a truly absent wing
# blocks the entry (fail closed) with an error alert, and nothing changes
# on a normal day. Run standalone:  python3 test_tsg_wing_guard.py
import sys
import tempfile
import types
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd                                                # noqa: E402
from app.engine.tsg import tsg_manager as TM                       # noqa: E402
from app.engine.tsg import tsg_engine as TE                        # noqa: E402
from app.engine.tsg.tsg_live_core import (                         # noqa: E402
    TsgDayCore, D_OPEN, D_SKIPPED)
from app.engine.ic import ic_selection as ICS                      # noqa: E402

FAILS, ALERTS = [], []
DAY = datetime(2026, 11, 11, 9, 16, 2, tzinfo=TM.IST)
EXP = "2026-11-17"
SPOT = 25350.0
CFG_LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "premium_max": 120},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "premium_max": 120},
    {"id": "L3", "action": "BUY", "opt_type": "CE", "premium_max": 8},
    {"id": "L4", "action": "BUY", "opt_type": "PE", "premium_max": 8},
]


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def sym(k, t):
    return f"NIFTY26N17{int(k)}{t}"


def day_prices():
    """A 4-DTE chain shaped like 2026-11-11 09:16 (spot ~25,350): the ₹8 CE
    wing sits ~1,450 out, the PE side carries skew so the first PE <= ₹8 is
    ~1,650 below spot — OUTSIDE ±1500."""
    px = {}
    for k in range(int(SPOT) // 50 * 50 - 3000, int(SPOT) // 50 * 50 + 3050, 50):
        d = k - SPOT
        ce = max(0.3, 112 * (0.9975 ** max(0, d - 350))) if d >= 350 else 112 + (350 - d) * 0.6
        pe = max(0.4, 114 * (0.9980 ** max(0, -d - 300))) if d <= -300 else 114 + (d + 300) * 0.6
        px[sym(k, "CE")], px[sym(k, "PE")] = round(ce, 2), round(pe, 2)
    return px


def rows_of(px):
    return [{"tradingsymbol": s, "instrument_type": s[-2:], "strike": float(s[10:15]),
             "instrument_token": 7000 + i, "expiry": date(2026, 11, 17),
             "exchange": "NFO", "name": "NIFTY"} for i, s in enumerate(px)]


def setup(cfg_over=None):
    ALERTS.clear()
    d = Path(tempfile.mkdtemp(prefix="tsgwing_"))
    TM.STATE_DIR, TM.SESSION_PATH = d, d / "TSG_V1_session.json"
    TM.TsgManager._sweep_stale_live_rows = lambda self: None
    TM.TsgManager._alert = lambda self, code, msg, **k: ALERTS.append((code, msg, k.get("severity")))
    TM.TsgManager._notify_group_entry = lambda self, *a, **k: None
    TM.TsgManager._notify = lambda self, msg: ALERTS.append(("NOTIFY", msg, "info"))
    TM._now_ist = lambda: DAY
    TM.is_day_blocked = lambda sid: False
    TM.check_strategy_max_loss = lambda sid: False
    TM.insert_paper_trade = lambda **k: "P"
    TM.close_paper_trade = lambda **k: None
    cfg = {"lots": 10, "lot_size": 65, "mtm_sl_per_lot": 3500, "mtm_target": 0,
           "iv_sl_pct": 0, "iv_sl_delta_pts": 0, "min_entry_iv": 0, "legs": CFG_LEGS}
    cfg.update(cfg_over or {})
    TM.load_strategy_config = lambda sid: cfg
    return TM.TsgManager(executor=None, quote_fn=lambda syms: {})


def band(px, rng):
    keep = {s: p for s, p in px.items() if abs(float(s[10:15]) - SPOT) <= rng}
    return (EXP, rows_of(keep), keep)


def codes():
    return [a[0] for a in ALERTS]


# ── the incident, replayed ─────────────────────────────────────────────
def t_incident_old_band_now_alerts_and_falls_back():
    px = day_prices()
    first_pe = max(float(s[10:15]) for s, p in px.items() if s.endswith("PE") and p <= 8)
    check("fixture: first PE <= ₹8 lies outside ±1500 (the 11-Nov shape)", SPOT - first_pe > 1500,
          f"gap {SPOT - first_pe}")
    m = setup()
    ok = m.enter_day(band(px, 1500), mode="PAPER")
    L = m._core.legs
    check("OLD BAND: entry still happens with all 4 legs (L4 = fallback)",
          ok and m._core.state == D_OPEN and set(L) == {"L1", "L2", "L3", "L4"}, str(set(L)))
    l4 = L.get("L4")
    check("OLD BAND: fallback L4 is further OTM than the L2 short",
          l4 is not None and l4.strike < L["L2"].strike)
    check("OLD BAND: fallback L4 is the cheapest PE left in the band",
          l4 is not None and l4.entry_price == min(p for s, p in band(px, 1500)[2].items()
                                                  if s.endswith("PE")))
    check("OLD BAND: a TSG_WING_FALLBACK alert was raised (no more silence)",
          "TSG_WING_FALLBACK" in codes(), str(codes()))
    check("OLD BAND: the ENTERED notice names the fallback",
          any(c == "NOTIFY" and "FALLBACK" in msg for c, msg, _ in ALERTS))


def t_incident_new_band_clean():
    px = day_prices()
    m = setup()
    ok = m.enter_day(band(px, 3000), mode="PAPER")
    L = m._core.legs
    check("NEW BAND (±3000): all 4 legs, L4 a real strike <= ₹8",
          ok and set(L) == {"L1", "L2", "L3", "L4"} and L["L4"].entry_price <= 8, str(set(L)))
    check("NEW BAND: no wing alert on a clean entry",
          not any(c.startswith("TSG_WING") for c in codes()), str(codes()))
    check("NEW BAND: shorts unchanged by the wider band (selection parity)",
          L["L1"].symbol == setup_pick(px, 1500, "L1") and L["L2"].symbol == setup_pick(px, 1500, "L2"))


def setup_pick(px, rng, leg_id):
    m = setup({"wing_fallback_cheapest": True})
    m.enter_day(band(px, rng), mode="PAPER")
    return m._core.legs[leg_id].symbol


def t_absent_wing_blocks_entry():
    px = day_prices()
    k2 = None
    m0 = setup()
    m0.enter_day(band(px, 1500), mode="PAPER")
    k2 = m0._core.legs["L2"].strike
    # chain with NO PE further out than the short: the wing cannot exist
    keep = {s: p for s, p in band(px, 1500)[2].items()
            if not (s.endswith("PE") and float(s[10:15]) < k2)}
    m = setup()
    ok = m.enter_day((EXP, rows_of(keep), keep), mode="LIVE")
    check("ABSENT WING: NO ENTRY (fail closed, nothing planned stays in the book)",
          ok is False and m._core.state == D_SKIPPED and not m._core.legs)
    check("ABSENT WING: error alert TSG_WING_MISSING names L4",
          any(c == "TSG_WING_MISSING" and "L4" in msg and sev == "error" for c, msg, sev in ALERTS),
          str(ALERTS))
    check("ABSENT WING: day latched (no retry loop)", m.latched_today())


def t_absent_wing_enter_policy():
    px = day_prices()
    keep = {s: p for s, p in band(px, 1500)[2].items()
            if not (s.endswith("PE") and p < 110)}
    m = setup({"wing_absent_policy": "ENTER"})
    ok = m.enter_day((EXP, rows_of(keep), keep), mode="PAPER")
    check("POLICY ENTER: enters 3 legs on purpose", ok and set(m._core.legs) == {"L1", "L2", "L3"})
    check("POLICY ENTER: still alerts as an error",
          any(c == "TSG_WING_MISSING" and sev == "error" for c, _, sev in ALERTS), str(ALERTS))


def t_fallback_off():
    px = day_prices()
    m = setup({"wing_fallback_cheapest": False})
    ok = m.enter_day(band(px, 1500), mode="PAPER")
    check("FALLBACK OFF: wing absent → fail closed with an error alert",
          ok is False and "TSG_WING_MISSING" in codes())


# ── pure core ──────────────────────────────────────────────────────────
def t_core():
    meta = {"C2": {"strike": 25700}, "C9": {"strike": 26900}, "P2": {"strike": 25050},
            "P8": {"strike": 23900}, "PX": {"strike": 25400}}
    lad = {"CE": [("C2", 110.0), ("C9", 6.0)], "PE": [("P2", 112.0), ("P8", 9.5), ("PX", 300.0)]}
    c = TsgDayCore()
    out = c.plan_entry(lad, CFG_LEGS, 65, 10, meta, wing_fallback=True)
    ids = {l.leg_id: l.symbol for l in out}
    check("CORE: fallback takes the cheapest OTM PE (P8 ₹9.5), never the ITM/short",
          ids.get("L4") == "P8", str(ids))
    check("CORE: wing_notes records the fallback", any("L4" in n and "FALLBACK" in n for n in c.wing_notes))
    c2 = TsgDayCore()
    out2 = c2.plan_entry(lad, CFG_LEGS, 65, 10, meta)            # default: no fallback
    check("CORE: default call keeps old shape (absent) but records it",
          "L4" not in {l.leg_id for l in out2} and any("ABSENT" in n for n in c2.wing_notes))
    c3 = TsgDayCore()
    c3.plan_entry({"CE": [("C2", 110.0)], "PE": [("P2", 112.0)]}, CFG_LEGS, 65, 10, meta,
                  wing_fallback=True)
    check("CORE: fallback never reuses the short's own contract",
          {l.symbol for l in c3.legs.values()} == {"C2", "P2"} and len(c3.wing_notes) == 2)
    d = c.to_state()
    d.pop("wing_notes")
    check("CORE: a session snapshot from before this fence still restores",
          TsgDayCore.from_state(d).wing_notes == [])


# ── chain band plumbing ────────────────────────────────────────────────
def t_chain_band():
    px = day_prices()
    df = pd.DataFrame(rows_of(px))
    stub = types.ModuleType("app.fetcher.zerodha_instruments")   # no kiteconnect needed
    stub.load_instruments_df = lambda *a, **k: df
    stub.get_nifty_spot = lambda *a, **k: SPOT
    _prev = sys.modules.get("app.fetcher.zerodha_instruments")
    sys.modules["app.fetcher.zerodha_instruments"] = stub
    kite = types.SimpleNamespace(ltp=lambda x: {"NSE:NIFTY 50": {"last_price": SPOT}})
    q = lambda syms: {s: px[s] for s in syms}
    _, r1, _ = ICS.snapshot_weekly_chain(kite, "k", "t", quote_fn=q)
    _, r2, _ = ICS.snapshot_weekly_chain(kite, "k", "t", quote_fn=q, strike_range=3000)
    w1 = max(abs(r["strike"] - SPOT) for r in r1)
    w2 = max(abs(r["strike"] - SPOT) for r in r2)
    check("CHAIN: default band unchanged (±1500) for IC/ORB/BRK/EMC/GC", w1 <= 1500, str(w1))
    check("CHAIN: strike_range=3000 widens it", 1500 < w2 <= 3000, str(w2))
    check("CHAIN: ±3000 stays within one kite.quote call (<400 symbols)", len(r2) < 400, str(len(r2)))
    if _prev is not None:
        sys.modules["app.fetcher.zerodha_instruments"] = _prev

    seen = {}
    ICS.snapshot_weekly_chain = lambda *a, **k: seen.update(k) or (None, [], {})
    TE.load_strategy_config = lambda sid: {}
    eng = TE.TsgEngine.__new__(TE.TsgEngine)
    eng.broker = types.SimpleNamespace(get_data_kite=lambda: types.SimpleNamespace(api_key="k", access_token="t"))
    eng._chain()
    check("ENGINE: TSG asks for the ±3000 band by default", seen.get("strike_range") == 3000, str(seen))


if __name__ == "__main__":
    for fn in (t_core, t_incident_old_band_now_alerts_and_falls_back, t_incident_new_band_clean,
               t_absent_wing_blocks_entry, t_absent_wing_enter_policy, t_fallback_off, t_chain_band):
        fn()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL TSG_WING_GUARD CHECKS PASSED")
