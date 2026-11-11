# backend/app/engine/test_wing_reach.py
#
# ── WING_REACH_20261111 ── every hedge-buying core strategy looks ±3000
# points for its wing, and a wing that is missing or above its cap is never
# silent again. Companion of TSG_WING_GUARD_20261111 (Tigris).
#
#   IC_V1/IC_V2  entry + adjustment chain at ±3000; fallback wing alerted
#   TMA_V2       tick band ±60 strikes; hedge fallback / missing alerted
#   VET_V1       same tick band (shared engine); never-bare skip alerted
#                once per side per day
#
# The 11-Nov chain shape (spot ~25,350, 4 sessions out, put skew) is
# replayed for the IC selector. Run standalone:
#     PYTHONPATH=backend python3 backend/app/engine/test_wing_reach.py
import os
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))           # backend/
sys.path.insert(0, str(HERE / "tma2"))             # tma2 e2e harness

import pandas as pd                                                    # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


SPOT = 25350.0


def chain_px():
    """Same shape as test_tsg_wing_guard: first PE <= ₹4 ~2,000 below spot."""
    px = {}
    base = int(SPOT) // 50 * 50
    for k in range(base - 3000, base + 3050, 50):
        d = k - SPOT
        ce = max(0.3, 112 * (0.9975 ** max(0, d - 350))) if d >= 350 else 112 + (350 - d) * 0.6
        pe = max(0.4, 114 * (0.9980 ** max(0, -d - 300))) if d <= -300 else 114 + (d + 300) * 0.6
        px[f"NIFTY26N17{k}CE"], px[f"NIFTY26N17{k}PE"] = round(ce, 2), round(pe, 2)
    return px


# ── IC selection on the day's shape ────────────────────────────────────
def t_ic_selection():
    from app.engine.ic.ic_selection import build_chain_candidates, select_ic_strikes
    px = chain_px()
    legs = [{"id": "L1", "action": "SELL", "opt_type": "CE", "premium_max": 100, "lots": 5},
            {"id": "L2", "action": "SELL", "opt_type": "PE", "premium_max": 100, "lots": 5},
            {"id": "L3", "action": "BUY", "opt_type": "CE", "premium_max": 4, "lots": 5},
            {"id": "L4", "action": "BUY", "opt_type": "PE", "premium_max": 4, "lots": 5}]

    def sel(rng):
        keep = {s: p for s, p in px.items() if abs(float(s[10:15]) - SPOT) <= rng}
        rows = [{"tradingsymbol": s, "instrument_token": i, "strike": float(s[10:15]),
                 "instrument_type": s[-2:]} for i, s in enumerate(keep)]
        ce, pe, tok = build_chain_candidates(rows, keep)
        return select_ic_strikes(legs, ce, pe, tok, date(2026, 11, 17))

    old, new = sel(1500), sel(3000)
    check("IC ±1500 (old): L4 is a FALLBACK above the ₹4 cap (the silent case)",
          old.picks["L4"].fallback and old.picks["L4"].ltp > 4, str(old.picks["L4"]))
    check("IC ±3000 (new): every wing real and <= ₹4",
          all(not new.picks[l].fallback and new.picks[l].ltp <= 4 for l in ("L3", "L4")),
          str({l: new.picks[l] for l in ("L3", "L4")}))
    check("IC: shorts identical in both bands (selection parity)",
          all(old.picks[l].symbol == new.picks[l].symbol for l in ("L1", "L2")))
    check("IC: ±3000 L4 is further OTM than the old fallback was",
          new.picks["L4"].strike < old.picks["L4"].strike)


# ── IC engine plumbing: both snapshot calls ask for ±3000 ──────────────
def t_ic_engine_reach():
    import app.engine.ic.ic_engine as IE
    IE.write_audit_log = lambda m: None
    seen = []
    IE.snapshot_weekly_chain = lambda *a, **k: (seen.append(k), (None, [], {}))[1]
    IE.load_strategy_config = lambda sid: {}
    e = IE.ICEngine.__new__(IE.ICEngine)
    e.sid = "IC_V2"
    e.broker = types.SimpleNamespace(get_data_kite=lambda: object())
    e._data_creds = lambda *a: ("k", "t")
    e._chain_provider()
    check("IC engine: adjustment chain provider asks for ±3000",
          seen and seen[-1].get("strike_range") == 3000, str(seen))
    check("IC engine: chain_strike_range in config overrides it",
          (setattr(IE, "load_strategy_config", lambda sid: {"chain_strike_range": 2500})
           or e._wing_reach()) == 2500)
    src = Path(IE.__file__).read_text()
    check("IC engine: the entry snapshot passes strike_range too",
          src.count("strike_range=self._wing_reach()") == 2)


# ── TMA_V2 / VET_V1 tick band ──────────────────────────────────────────
def t_tick_band():
    from app.engine.tma2 import tma2_tick_engine as TE
    df_rows = [{"name": "NIFTY 50", "segment": "INDICES", "tradingsymbol": "NIFTY 50",
                "instrument_token": 256265, "expiry": "", "strike": 0.0, "instrument_type": "EQ"}]
    for k in range(18000, 33050, 50):
        for t in ("CE", "PE"):
            df_rows.append({"name": "NIFTY", "segment": "NFO-OPT", "tradingsymbol": f"NIFTY26N17{k}{t}",
                            "instrument_token": k * 10 + (1 if t == "CE" else 2), "expiry": "2026-11-17",
                            "strike": float(k), "instrument_type": t})
    eng = TE.TMA2TickEngine(None, pd.DataFrame(df_rows), lambda *a: None)
    n = eng.resolve_universe(SPOT, "2026-11-17")
    ks = [m["strike"] for m in eng.chain._meta.values()]
    check("TICK: band is ±3000 around ATM (TMA_V2 and VET_V1 share it)",
          min(ks) == 22350 and max(ks) == 28350, f"{min(ks)}..{max(ks)}")
    check("TICK: ~242 contracts — far under Kite's 3000/socket, no new socket", n == 242, str(n))


# ── TMA_V2 hedge alerts (real trade manager via the e2e harness) ───────
def t_tma2_alerts():
    import test_tma2_e2e as H
    s1 = {"main": {"premium_max": 170, "lots": 1, "sl_pct": 12, "tp_pct": 10,
                   "sl_unit": "PCT", "tp_unit": "ABS"},
          "hedge": {"premium_max": 3, "lots": 1}, "max_trades_per_day": 0}
    m, kite, ch, _ = H.setup(mode="PAPER", s1=s1)
    H.enter(m, ch)
    check("TMA2: hedge above cap → still enters with the cheapest real hedge",
          m.group is not None and m.group["hedge"]["fallback"])
    check("TMA2: …and raises TMA2_HEDGE_FALLBACK (was audit-log only)",
          any(c == "TMA2_HEDGE_FALLBACK" for c, _ in H.ALERTS), str(H.ALERTS))
    m, kite, ch, _ = H.setup(mode="PAPER", s1=s1, wing_mode="skip")
    H.enter(m, ch)
    check("TMA2: wing_mode=skip → no entry and TMA2_HEDGE_MISSING (error)",
          m.group is None and any(c == "TMA2_HEDGE_MISSING" for c, _ in H.ALERTS), str(H.ALERTS))
    m, kite, ch, _ = H.setup(mode="PAPER")
    H.enter(m, ch)
    check("TMA2: hedge within cap → no wing alert (normal days unchanged)",
          m.group is not None and not any(c.startswith("TMA2_HEDGE") for c, _ in H.ALERTS))


# ── VET_V1 never-bare skip alert ───────────────────────────────────────
def t_vet_alert():
    import app.engine.vet.vet_manager as VM
    from app.engine.vet.vet_common import VetRepo
    import app.event_bus.inapp_events as IA
    import app.api.telegram_api as TG
    got, tg = [], []
    VM.write_audit_log = lambda m: None            # hermetic: no real audit log
    IA.record_alert = lambda code, msg="", **k: got.append((code, k.get("severity")))
    TG.notify_critical = lambda d: tg.append(d)
    IST = timezone(timedelta(hours=5, minutes=30))
    ts = int(datetime(2026, 11, 11, 12, 0, tzinfo=IST).timestamp())

    def band(side, t):   # no PE <= ₹3 anywhere in the armed band
        out = []
        for k in range(22350, 28400, 50):
            d = k - SPOT
            p = (max(4.5, 180 - abs(d) * 0.1) if (side == "PE" and d < 0) or (side == "CE" and d > 0)
                 else 180 + abs(d) * 0.9)
            out.append({"tradingsymbol": f"NIFTY26N17{k}{side}", "token": k, "strike": float(k),
                        "expiry": "2026-11-17", "instrument_type": side, "ltp": round(p, 2)})
        return out
    cfg = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 3.0, "atm_offset": -1,
           "eod_square": False, "quantity": {"lots": 10, "lot_size": 65}, "_spot": SPOT}
    repo = VetRepo(os.path.join(tempfile.mkdtemp(prefix="wingreach_"), "v.db"))
    repo.ensure_schema()
    m = VM.VetManager(cfg, repo=repo, chain_fn=band, quote_fn=lambda s: 100.0, mode="PAPER")
    if hasattr(m, "wing_search_fn"):
        m.wing_search_fn = None
    p1 = m.open_position("PE", ts=ts, bar_ts=ts, condition=1)
    p2 = m.open_position("PE", ts=ts + 300, bar_ts=ts + 300, condition=1)
    codes = [c for c, _ in got if c == "VET_WING_MISSING"]
    check("VET: no wing → entry skipped (never bare, unchanged)", p1 is None and p2 is None and m.pos is None)
    check("VET: VET_WING_MISSING raised as an error (was audit-log only)",
          codes and all(s == "error" for c, s in got if c == "VET_WING_MISSING"), str(got))
    check("VET: once per side per day, not every 5-minute retry", len(codes) == 1, str(codes))
    m._alert_wing_missing("PE", ts + 86400)
    check("VET: next day alerts again", len([c for c, _ in got if c == "VET_WING_MISSING"]) == 2)


if __name__ == "__main__":
    for fn in (t_ic_selection, t_ic_engine_reach, t_tick_band, t_tma2_alerts, t_vet_alert):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__} raised {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL WING_REACH CHECKS PASSED")
