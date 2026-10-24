# backend/app/risk/test_live_sizer.py
#
# ── LIVE_SIZER_20261024 ── behavioural suite for risk/live_sizer.py and its routes.
# Real epoch clock (IST) everywhere; temp app.db; legs / configs injected
# through the module's seams. No TestClient (Build-Mac rule): routes are
# called as plain functions.
#
#   cd backend && PYTHONPATH=$PWD python3 app/risk/test_live_sizer.py

import calendar
import os
import sys
import tempfile
import types
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="live_sizer_"))
os.environ.setdefault("SCALP_APP_HOME", str(TMP / "home"))

import app.risk.live_sizer as LS                       # noqa: E402

FAILS, OK = [], [0]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        OK[0] += 1
    else:
        FAILS.append(name)


def ist(day, hh=10, mm=0):
    y, m, d = (int(x) for x in day.split("-"))
    return calendar.timegm((y, m, d, hh, mm, 0)) - LS.IST_OFF


DB = TMP / "app.db"
LS._db_path = lambda: str(DB)
LEGS = {}
CFGS = {}
LS._legs_reader = lambda sid, warns: list(LEGS.get(sid, []))
LS._config_reader = lambda sid: CFGS.get(sid, {})
NOW = [ist("2026-10-28", 9, 5)]
LS._now = lambda: NOW[0]


def leg(rid, entry_day, exit_day, gross, charges=40.0, book="LIVE", open_=False, sym="NIFTY26O2723000CE", eh=9, xh=15):
    return {"id": f"{book}:{rid}", "book": book, "symbol": sym, "entry_ts": ist(entry_day, eh, 20),
            "exit_ts": None if open_ else ist(exit_day, xh, 10), "open": open_, "gross": gross,
            "charges": charges, "approx": False}


IC_CFG = {"trade_execution_mode": "LIVE", "quantity": {"lot_size": 65},
          "legs": [{"id": "L1", "lots": 3}, {"id": "L2", "lots": 3}, {"id": "L3", "lots": 3}, {"id": "L4", "lots": 3}],
          "adjust": {"L1": {"lots": 3}, "L2": {"lots": 3}}}
TMA_CFG = {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lot_size": 65},
           "s1": {"main": {"lots": 3}, "hedge": {"lots": 3}, "max_trades_per_day": 0}}
VET_CFG = {"trade_execution_mode": "LIVE", "quantity": {"lot_size": 65, "lots": 2}}
PLAN = {"start_date": "2026-10-26", "account_start": 1000000, "sizing_capital": 1200000, "floor_start": True,
        "max_total": 0, "members": {"IC_V2": {"start": 3, "max": 15}, "TMA_V2": {"start": 3, "max": 15},
                                    "VET_V1": {"start": 2, "max": 10}}}

print("\n── 1. the rule (parity with Backtest → Portfolio shared equity) ──")
cfg = LS.validate_config(PLAN)
check("₹10L on a ₹12L plan, floor on → 3/3/2", LS.suggest_lots(1000000, cfg) == {"IC_V2": 3, "TMA_V2": 3, "VET_V1": 2})
check("₹16L → Icarus/Timberwolf 4, Velvet 2", LS.suggest_lots(1600000, cfg) == {"IC_V2": 4, "TMA_V2": 4, "VET_V1": 2})
check("₹15,99,999 → still 3/3/2 (rounded down)", LS.suggest_lots(1599999, cfg) == {"IC_V2": 3, "TMA_V2": 3, "VET_V1": 2})
check("₹18L → 4/4/3", LS.suggest_lots(1800000, cfg) == {"IC_V2": 4, "TMA_V2": 4, "VET_V1": 3})
check("₹1 Cr → capped 15/15/10", LS.suggest_lots(10000000, cfg) == {"IC_V2": 15, "TMA_V2": 15, "VET_V1": 10})
off = dict(cfg, floor_start=False)
check("floor off: ₹10L → ⌊2.5⌋/⌊2.5⌋/⌊1.67⌋ = 2/2/1", LS.suggest_lots(1000000, off) == {"IC_V2": 2, "TMA_V2": 2, "VET_V1": 1})
check("floor off: never below 1", LS.suggest_lots(10000, off) == {"IC_V2": 1, "TMA_V2": 1, "VET_V1": 1})
mt = dict(cfg, max_total=10)
check("Max total 10 caps the scale at 10/8 → 3/3/2", LS.suggest_lots(5000000, mt) == {"IC_V2": 3, "TMA_V2": 3, "VET_V1": 2})
check("next threshold: Icarus at 3 → ₹16L", LS.next_threshold("IC_V2", cfg, 3) == 1600000)
check("next threshold: Velvet at 2 → ₹18L", LS.next_threshold("VET_V1", cfg, 2) == 1800000)
check("next threshold at the cap → None", LS.next_threshold("IC_V2", cfg, 15) is None)
check("next threshold blocked by Max total → None", LS.next_threshold("IC_V2", mt, 3) is None)

print("\n── 2. validation (exact reasons) ──")
for bad, frag in (({"members": {"IC_V2": {"start": 3, "max": 2}}}, "below its start"),
                  ({"members": {"XYZ_V9": {"start": 1}}}, "not supported"),
                  ({"start_date": "26/10/2026"}, "YYYY-MM-DD"),
                  ({"sizing_capital": 0}, "above ₹0"),
                  ({"members": {}}, "at least one")):
    try:
        LS.validate_config(bad)
        check(f"rejects {bad}", False)
    except ValueError as e:
        check(f"rejects with '{frag}'", frag in str(e), str(e))

print("\n── 3. ledger: LIVE closed legs since start, before today ──")
LS.save_config(PLAN)
CFGS.update({"IC_V2": IC_CFG, "TMA_V2": TMA_CFG, "VET_V1": VET_CFG})
LEGS["IC_V2"] = [leg(1, "2026-10-26", "2026-10-26", 20000), leg(2, "2026-10-26", "2026-10-27", -5000),
                 leg(3, "2026-10-28", "2026-10-28", 7000, eh=9, xh=9),          # closes TODAY → not in equity
                 leg(4, "2026-10-23", "2026-10-23", 99999),                     # before the start date
                 leg(5, "2026-10-26", "2026-10-26", 55555, book="PAPER"),       # paper book
                 leg(6, "2026-10-27", None, 0, open_=True)]                     # still open
LEGS["TMA_V2"] = [leg(10, "2026-10-26", "2026-10-27", 12000, charges=100)]
LEGS["VET_V1"] = [leg(20, "2026-10-23", "2026-10-26", -3000)]                   # entered before start, closed after → counts
r = LS.compute()
exp_ic = (20000 - 40) + (-5000 - 40)
check("Icarus realised = two LIVE legs closed 26–27 Oct, net of charges", r["rows"][0]["realised_since_start"] == exp_ic, r["rows"][0])
check("today's close shown separately, not in equity", r["rows"][0]["realised_today"] == 7000 - 40)
check("paper, open and pre-start legs ignored", r["realised"] == exp_ic + (12000 - 100) + (-3000 - 40), r["realised"])
check("equity = start + realised", r["equity"] == 1000000 + r["realised"])
r2 = LS.compute()
check("idempotent: a second ingest changes nothing", r2["equity"] == r["equity"])
LEGS["IC_V2"] = LEGS["IC_V2"][2:]                                               # older legs fell out of the reader's window
r3 = LS.compute()
check("ledger keeps legs the reader no longer returns", r3["equity"] == r["equity"])
NOW[0] = ist("2026-10-29", 9, 5)
r4 = LS.compute()
check("next morning: yesterday's close joins equity", r4["equity"] == r["equity"] + (7000 - 40))
NOW[0] = ist("2026-10-28", 9, 5)

print("\n── 4. adjustments ──")
a1 = LS.add_adjustment("2026-10-27", "DEPOSIT", 500000, "top-up")
a2 = LS.add_adjustment("2026-10-27", "TAX", 20000)
LS.add_adjustment("2026-10-28", "WITHDRAW", 100000)                              # dated today → from tomorrow
r = LS.compute()
check("deposit − tax dated before today count", r["adjustments_total"] == 480000, r["adjustments_total"])
check("equity includes them", r["equity"] == 1000000 + r["realised"] + 480000)
check("₹15.25L+: still 3/3/2 (threshold ₹16L)", [x["suggested"] for x in r["rows"]] == [3, 3, 2])
try:
    LS.add_adjustment("2026-10-27", "GIFT", 1)
    check("rejects unknown kind", False)
except ValueError:
    check("rejects unknown kind", True)
check("delete works", LS.delete_adjustment(a1["id"]) and not LS.delete_adjustment(a1["id"]))
LS.delete_adjustment(a2["id"])
for a in LS.list_adjustments():
    LS.delete_adjustment(a["id"])

print("\n── 5. current lots, change flag, per-leg preview ──")
LS.add_adjustment("2026-10-27", "DEPOSIT", 700000)                               # equity ≈ ₹17.2L → 4/4/2
r = LS.compute()
ic, tma, vet = r["rows"]
check("Icarus current 3 → suggested 4, change flagged", ic["current"] == 3 and ic["suggested"] == 4 and ic["change"])
check("Icarus preview scales every condor and ADJ leg to 4",
      {p["field"]: p["to"] for p in ic["preview"]} == {"legs.L1.lots": 4, "legs.L2.lots": 4, "legs.L3.lots": 4,
                                                        "legs.L4.lots": 4, "adjust.L1.lots": 4, "adjust.L2.lots": 4}, ic["preview"])
check("Timberwolf preview: main + hedge", {p["field"]: p["to"] for p in tma["preview"]} == {"s1.main.lots": 4, "s1.hedge.lots": 4})
check("Velvet unchanged at 2, no change flag", vet["current"] == 2 and vet["suggested"] == 2 and not vet["change"])
check("next threshold after the step: Icarus 5 lots at ₹20L", ic["next_at"] == 2000000, ic["next_at"])
check("any_change true", r["any_change"])
CFGS["VET_V1"] = dict(VET_CFG, trade_execution_mode="PAPER")
r = LS.compute()
check("a PAPER-mode member is warned about", any("VET_V1 is in PAPER" in w for w in r["warnings"]))
CFGS["VET_V1"] = VET_CFG
check("the sizer never wrote a config", CFGS["IC_V2"]["legs"][0]["lots"] == 3 and IC_CFG["adjust"]["L1"]["lots"] == 3)
for a in LS.list_adjustments():
    LS.delete_adjustment(a["id"])

print("\n── 6. unconfigured / broken inputs never raise ──")
LS.save_config(dict(PLAN, start_date=""))
r = LS.compute()
check("no start date → equity = account start, warning shown", r["equity"] == 1000000 and any("start date" in w for w in r["warnings"]))
LS.save_config(PLAN)


def boom(sid, warns):
    raise RuntimeError("reader down")


LS._legs_reader = boom
r = LS.compute()
check("a failing reader → ok, warning, ledger still used", r["ok"] and any("legs read failed" in w for w in r["warnings"]))
LS._legs_reader = lambda sid, warns: list(LEGS.get(sid, []))

print("\n── 7. scheduler job ──")
alerts = []
fake_inapp = types.ModuleType("app.event_bus.inapp_events")
fake_inapp.record_alert = lambda code, msg, **k: alerts.append((code, msg, k))
fake_audit = types.ModuleType("app.event_bus.audit_logger")
fake_audit.write_audit_log = lambda m: None
trading = [False]
fake_mh = types.ModuleType("app.utils.market_hours")
fake_mh.is_trading_day = lambda d=None: trading[0]
saved = {k: sys.modules.get(k) for k in ("app.event_bus.inapp_events", "app.event_bus.audit_logger", "app.utils.market_hours")}
sys.modules.update({"app.event_bus.inapp_events": fake_inapp, "app.event_bus.audit_logger": fake_audit,
                    "app.utils.market_hours": fake_mh})
try:
    check("holiday / weekend → nothing", LS.live_sizer_job("morning") is None and not alerts)
    trading[0] = True
    LS.add_adjustment("2026-10-27", "DEPOSIT", 700000)
    res = LS.live_sizer_job("morning")
    check("trading day → snapshot + one bell alert naming the change",
          res is not None and len(alerts) == 1 and "IC_V2 3 → 4" in alerts[0][1], alerts)
    LS.live_sizer_job("morning")
    check("second run the same day → no second alert", len(alerts) == 1)
    check("snapshot stored in history", LS.history()[0]["day"] == "2026-10-28")
    check("close run ingests only", LS.live_sizer_job("close") is None and len(alerts) == 1)
    LS.save_config(dict(PLAN, enabled=False))
    check("disabled → nothing", LS.live_sizer_job("morning") is None)
    LS.save_config(PLAN)
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
for a in LS.list_adjustments():
    LS.delete_adjustment(a["id"])

print("\n── 8. routes (plain calls) ──")
try:
    from fastapi import HTTPException
    import app.api.live_sizer_routes as R
    g = R.get_live_sizer()
    check("GET returns rows + history + supported", len(g["rows"]) == 3 and "history" in g and "IC_V2" in g["supported"])
    try:
        R.put_config({"members": {"IC_V2": {"start": 3, "max": 1}}})
        check("PUT invalid → 400", False)
    except HTTPException as e:
        check("PUT invalid → 400 with reason", e.status_code == 400 and "below its start" in e.detail, e.detail)
    check("PUT valid saves", R.put_config(PLAN)["config"]["sizing_capital"] == 1200000)
    p = R.post_adjustment({"day": "2026-10-27", "kind": "DEPOSIT", "amount": 1000})
    check("POST adjustment", p["adjustment"]["amount"] == 1000)
    check("DELETE adjustment", R.delete_adjustment(p["adjustment"]["id"])["ok"])
    try:
        R.delete_adjustment(999999)
        check("DELETE missing → 404", False)
    except HTTPException as e:
        check("DELETE missing → 404", e.status_code == 404)
except ImportError as e:
    print(f"  note: routes skipped ({e}) — fastapi not importable here")

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
