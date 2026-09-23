# backend/app/trading/test_fleet_modes.py
#
# ── FLEET_MODES_20260923 ── behavioural suite for the four execution modes
# (OFF / PAPER / LIVE / PAPER_LIVE) and the PAPER_LIVE shadow book.
# Hermetic: a temp app.db, strategy configs patched in memory, no broker,
# no TestClient.
#
#   cd backend && PYTHONPATH=$PWD python3 app/trading/test_fleet_modes.py

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ── 0. hermetic DB + config ─────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix="fleet_modes_")
DB = os.path.join(TMP, "app.db")
import app.db.sqlite as S
S.DB_PATH = Path(DB)
with sqlite3.connect(DB) as c:
    c.executescript("""
    CREATE TABLE paper_trades (paper_trade_id TEXT PRIMARY KEY, strategy_name TEXT, trade_mode TEXT,
      symbol TEXT, token INTEGER, side TEXT, entry_time INTEGER, entry_price REAL, candle_ts INTEGER,
      sl_price REAL, tp_price REAL, rr REAL, lots INTEGER, lot_size INTEGER, qty INTEGER,
      trade_direction TEXT, group_id TEXT, trade_class TEXT, state TEXT, created_at INTEGER,
      exit_time INTEGER, exit_price REAL, exit_reason TEXT, pnl_points REAL, pnl_value REAL,
      brokerage REAL, stt REAL, exchange_charges REAL, sebi_charges REAL, stamp_duty REAL, gst REAL,
      total_charges REAL, net_pnl REAL);
    CREATE TABLE trades (trade_id TEXT PRIMARY KEY, strategy_id TEXT, slot TEXT, symbol TEXT, token INTEGER,
      entry_time INTEGER, entry_price REAL, qty INTEGER, buy_order_id TEXT, sl_price REAL, sl_order_id TEXT,
      tp_price REAL, tp_mode TEXT, state TEXT, trade_direction TEXT, group_id TEXT, trade_class TEXT,
      exit_time INTEGER, exit_price REAL, exit_order_id TEXT, exit_reason TEXT);
    """)

import app.config.strategy_loader as SL
CFG = {
    "SCALP_V1": {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lots": 2, "lot_size": 65}},
    "ORB_V1":   {"trade_execution_mode": "PAPER_LIVE", "lots": 1, "lot_size": 65},
    "BRK_V1":   {"trade_execution_mode": "LIVE"},
    "HA_V1":    {"trade_execution_mode": "OFF"},
    "TSG_V1":   {"trade_execution_mode": "paper+live"},
    "SCALP_V5": {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lots": 1, "lot_size": 65}},
    "TMA_V2":   {"trade_execution_mode": "PAPER_LIVE"},
    "VET_V1":   {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lots": 10, "lot_size": 65}},
    "IC_V2":    {"trade_execution_mode": "OFF"},
}
DEGRADED = set()
SL.load_strategy_config = lambda sid: dict(CFG.get(sid) or {})
SL.load_strategy_config_ex = lambda sid: (dict(CFG.get(sid) or {}), sid in DEGRADED)

from app.risk import execution_modes as XM
from app.risk import strategy_max_loss_guard as G
from app.trading import shadow_book as SB


def paper_rows(sid, state=None):
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM paper_trades WHERE strategy_name=?"
        args = [sid]
        if state:
            q += " AND state=?"
            args.append(state)
        return [dict(r) for r in c.execute(q, args)]


def links(sid):
    with sqlite3.connect(DB) as c:
        return c.execute("SELECT COUNT(*) FROM shadow_links WHERE strategy_id=?", (sid,)).fetchone()[0]


print("── 1. vocabulary")
check("aliases normalise", XM.normalize("paper+live") == "PAPER_LIVE" and XM.normalize("Both") == "PAPER_LIVE"
      and XM.normalize(" live ") == "LIVE" and XM.normalize("off") == "OFF")
check("unknown → default (fail-closed PAPER)", XM.normalize("garbage") == "PAPER" and XM.normalize(None, "OFF") == "OFF"
      and XM.normalize("garbage", "LIVE") == "LIVE")
check("wants_live", XM.wants_live("LIVE") and XM.wants_live("PAPER_LIVE") and not XM.wants_live("PAPER") and not XM.wants_live("OFF"))
check("entries_allowed only OFF blocks", not XM.entries_allowed("OFF") and all(XM.entries_allowed(m) for m in ("PAPER", "LIVE", "PAPER_LIVE")))
check("book", XM.book("PAPER_LIVE") == "LIVE" and XM.book("OFF") == "PAPER" and XM.book("PAPER") == "PAPER")
check("boot_mode", XM.boot_mode("PAPER_LIVE", allow_off=False) == "LIVE" and XM.boot_mode("OFF", allow_off=False) == "PAPER"
      and XM.boot_mode("OFF", allow_off=True) == "OFF" and XM.boot_mode("LIVE", allow_off=False) == "LIVE")

print("── 2. resolvers")
check("guard resolver: PAPER_LIVE places live orders", G.resolve_execution_mode("SCALP_V1") == ("LIVE", False))
check("guard resolver: OFF never resolves LIVE", G.resolve_execution_mode("HA_V1") == ("PAPER", False))
check("plan: OFF is a real state", XM.resolve_execution_plan("HA_V1") == ("OFF", False))
check("plan: alias in the file reads as PAPER_LIVE", XM.resolve_execution_plan("TSG_V1") == ("PAPER_LIVE", False))
DEGRADED.add("SCALP_V1")
check("degraded read configured live → PAPER + degraded flag", XM.resolve_execution_plan("SCALP_V1") == ("PAPER", True))
check("degraded read never twins", XM.shadow_active("SCALP_V1") is False)
DEGRADED.discard("SCALP_V1")
check("shadow_active only on PAPER_LIVE", XM.shadow_active("SCALP_V1") and not XM.shadow_active("BRK_V1") and not XM.shadow_active("HA_V1"))
check("today_realised_pnl reads the live book in PAPER_LIVE", G._strategy_mode("SCALP_V1") == "PAPER_LIVE")

print("── 3. trades table (SCALP_V1 / BB / HA / IC / TSG live rows) → twin")
from app.db.trades_repo import insert_trade, close_trade
insert_trade(trade_id="t1", strategy_id="SCALP_V1", slot="CE_1", symbol="NIFTY2692523400CE", token=1,
             entry_price=100.0, qty=130, buy_order_id="o1", sl_price=120.0, tp_price=70.0, tp_mode="GTT",
             trade_direction="SHORT")
tw = paper_rows("SCALP_V1")
check("one twin row, PAPER book, SHD id, same qty/side/direction/sl/tp",
      len(tw) == 1 and tw[0]["trade_mode"] == "PAPER" and tw[0]["paper_trade_id"].startswith("SHD:")
      and tw[0]["qty"] == 130 and tw[0]["lots"] == 2 and tw[0]["trade_direction"] == "SHORT"
      and tw[0]["side"] == "CE" and tw[0]["sl_price"] == 120.0 and tw[0]["tp_price"] == 70.0
      and tw[0]["state"] == "OPEN", str(tw))
check("link recorded", links("SCALP_V1") == 1)
insert_trade(trade_id="t2", strategy_id="BRK_V1", slot="BRK", symbol="NIFTY2692523400PE", token=1,
             entry_price=100.0, qty=65, buy_order_id="o2", sl_price=90.0, tp_price=0.0, tp_mode="GTT")
check("LIVE-only strategy gets NO twin", paper_rows("BRK_V1") == [] and links("BRK_V1") == 0)
close_trade(trade_id="t1", exit_price=80.0, exit_order_id="x1", exit_reason="TP")
tw = paper_rows("SCALP_V1")
check("live close → twin closed at the same price, SHORT sign, link gone",
      tw[0]["state"] == "CLOSED" and tw[0]["exit_price"] == 80.0 and tw[0]["exit_reason"] == "TP"
      and tw[0]["pnl_value"] == 20 * 130 and links("SCALP_V1") == 0, str(tw))
close_trade(trade_id="t1", exit_price=80.0, exit_order_id="x1", exit_reason="TP")
check("double live close is a no-op (row already closed)", len(paper_rows("SCALP_V1")) == 1)

print("── 4. void: a rejected / unfilled live entry leaves no paper record")
insert_trade(trade_id="t3", strategy_id="SCALP_V1", slot="PE_1", symbol="NIFTY2692523300PE", token=1,
             entry_price=90.0, qty=130, buy_order_id="o3", sl_price=110.0, tp_price=60.0, tp_mode="GTT",
             trade_direction="SHORT")
check("twin open", len(paper_rows("SCALP_V1", "OPEN")) == 1)
close_trade(trade_id="t3", exit_price=None, exit_order_id=None, exit_reason="ENTRY_REJECTED")
check("twin deleted on void, link gone", len(paper_rows("SCALP_V1", "OPEN")) == 0
      and len(paper_rows("SCALP_V1")) == 1 and links("SCALP_V1") == 0)

print("── 5. paper logic may close the twin first (SCALP_V1 DB-driven SL/TP)")
from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade
insert_trade(trade_id="t4", strategy_id="SCALP_V1", slot="CE_1", symbol="NIFTY2692523500CE", token=1,
             entry_price=50.0, qty=130, buy_order_id="o4", sl_price=60.0, tp_price=35.0, tp_mode="GTT",
             trade_direction="SHORT")
sid4 = SB.link_for("t4")
close_paper_trade(paper_trade_id=sid4, exit_price=60.0, exit_reason="SL")
check("paper close of the twin does not recurse or unlink live", sid4 and links("SCALP_V1") == 1)
close_trade(trade_id="t4", exit_price=61.0, exit_order_id="x4", exit_reason="SL")
r = [x for x in paper_rows("SCALP_V1") if x["paper_trade_id"] == sid4][0]
check("live close afterwards: paper exit stands (60, not 61), link cleared",
      r["exit_price"] == 60.0 and links("SCALP_V1") == 0)

print("── 6. paper_trades table with trade_mode=LIVE (ORB / BRK) → twin")
insert_paper_trade(paper_trade_id="orb-live-1", strategy_name="ORB_V1", trade_mode="LIVE",
                   symbol="NIFTY2692523400CE", token=1, side="CE", entry_price=180.0, candle_ts=0,
                   sl_price=23350.0, tp_price=270.0, rr=1.0, lots=1, lot_size=65, qty=65,
                   trade_direction="LONG", group_id="ORB")
rows = paper_rows("ORB_V1", "OPEN")
check("LIVE row + PAPER twin, twin carries lots/lot_size/group", len(rows) == 2
      and sorted(r["trade_mode"] for r in rows) == ["LIVE", "PAPER"]
      and [r for r in rows if r["trade_mode"] == "PAPER"][0]["group_id"] == "ORB", str(rows))
insert_paper_trade(paper_trade_id="orb-paper-1", strategy_name="ORB_V1", trade_mode="PAPER",
                   symbol="NIFTY2692523450CE", token=1, side="CE", entry_price=180.0, candle_ts=0,
                   sl_price=0, tp_price=0, rr=0, lots=1, lot_size=65, qty=65)
check("a PAPER row is never twinned", len(paper_rows("ORB_V1", "OPEN")) == 3 and links("ORB_V1") == 1)
close_paper_trade(paper_trade_id="orb-live-1", exit_price=270.0, exit_reason="TP")
rows = paper_rows("ORB_V1", "CLOSED")
check("live row close → twin closed too", len(rows) == 2 and all(r["exit_price"] == 270.0 for r in rows)
      and links("ORB_V1") == 0, str(rows))

print("── 7. private stores: SCALP_V5, TMA_V2, VET_V1")
from app.db.scalpv5_repo import insert_v5_trade, close_v5_trade
insert_v5_trade(v5_trade_id="v5-1", paper=False, symbol="NIFTY2692523400CE", token=1, side="CE", qty=65,
                entry_price=100.0, sl_price=90.0, tp_price=120.0, entry_candle_ts=0, order_id="o")
check("V5 live insert → twin", len(paper_rows("SCALP_V5", "OPEN")) == 1 and paper_rows("SCALP_V5")[0]["sl_price"] == 90.0)
insert_v5_trade(v5_trade_id="v5-2", paper=True, symbol="NIFTY2692523400PE", token=1, side="PE", qty=65,
                entry_price=100.0, sl_price=None, tp_price=None, entry_candle_ts=0, order_id=None)
check("V5 paper insert → no twin", len(paper_rows("SCALP_V5")) == 1)
close_v5_trade(v5_trade_id="v5-1", exit_price=110.0, exit_order_id="x", exit_reason="TP")
check("V5 live close → twin closed", paper_rows("SCALP_V5")[0]["exit_price"] == 110.0 and links("SCALP_V5") == 0)
close_v5_trade(v5_trade_id="v5-2", exit_price=95.0, exit_order_id=None, exit_reason="SL")

from app.engine.tma2.tma2_common import TMA2Repo
tr = TMA2Repo(db_path=os.path.join(TMP, "tma2.db"))
sid_leg = tr.insert_leg({"group_id": "g1", "mode": "LIVE", "trend_side": "CE", "expiry": "2026-09-30",
                         "entry_ts": 1, "condition": "E1", "direction": "SELL",
                         "tradingsymbol": "NIFTY2693023000PE", "token": 1, "instrument_type": "PE",
                         "strike": 23000, "qty": 130, "entry_price": 80.0, "sl": 96.0, "tp": 64.0})
check("TMA2 live SELL leg → SHORT twin", len(paper_rows("TMA_V2", "OPEN")) == 1
      and paper_rows("TMA_V2")[0]["trade_direction"] == "SHORT" and paper_rows("TMA_V2")[0]["qty"] == 130, str(paper_rows("TMA_V2")))
pid_leg = tr.insert_leg({"group_id": "g0", "mode": "PAPER", "trend_side": "CE", "expiry": "2026-09-30",
                         "entry_ts": 1, "condition": "E1", "direction": "BUY",
                         "tradingsymbol": "NIFTY2693022800PE", "token": 1, "instrument_type": "PE",
                         "strike": 22800, "qty": 130, "entry_price": 5.0, "sl": None, "tp": None})
check("TMA2 paper leg → no twin", len(paper_rows("TMA_V2")) == 1)
tr.close_leg(sid_leg, exit_ts=2, exit_price=64.0, exit_reason="TP", ambiguous=False, pnl=0, charges=0, net_pnl=0)
check("TMA2 live close → twin closed", paper_rows("TMA_V2")[0]["exit_price"] == 64.0 and links("TMA_V2") == 0)

from app.engine.vet.vet_common import VetRepo
vr = VetRepo(db_path=os.path.join(TMP, "vet.db"))
vr.ensure_schema()
vid = vr.insert_leg({"group_id": "vg1", "leg_role": "MAIN", "mode": "LIVE", "direction": "SHORT",
                     "tradingsymbol": "NIFTY2692523400CE", "token": 1, "instrument_type": "CE",
                     "strike": 23400, "expiry": "2026-09-25", "qty": 650, "lots": 10, "lot_size": 65,
                     "entry_ts": 1, "entry_price": 100.0, "entry_order_id": "o", "signal_bar_ts": 1,
                     "condition": 1, "leg_action": "SELL"})
check("VET live MAIN → twin (lots honoured)", len(paper_rows("VET_V1")) == 1 and paper_rows("VET_V1")[0]["lots"] == 10)
vr.close_leg(vid, exit_ts=2, exit_price=90.0, exit_reason="SIGNAL_EXIT")
check("VET live close → twin closed", paper_rows("VET_V1")[0]["exit_price"] == 90.0 and links("VET_V1") == 0)

print("── 8. OFF gates (fresh config read, no new entries)")
from app.engine.orb.orb_manager import OrbManager
m = OrbManager(executor=None, cfg_fn=lambda: {"trade_execution_mode": "OFF", "lots": 1, "lot_size": 65})
m._allow_degraded = True
check("ORB mode() reports OFF", m.mode() == "OFF")
m2 = OrbManager(executor=None, cfg_fn=lambda: {"trade_execution_mode": "PAPER_LIVE", "lots": 1, "lot_size": 65})
check("ORB PAPER_LIVE runs the LIVE book", m2.mode() == "LIVE")
from app.engine.tma2.tma2_trade_manager import TMA2TradeManager
CFG["TMA_V2"] = {"trade_execution_mode": "OFF", "s1": {"main": {"lots": 1, "premium_max": 100},
                                                        "hedge": {"lots": 1, "premium_max": 20}}}
tm = TMA2TradeManager(dict(CFG["TMA_V2"]), tr)
snap = tm._cfg_snapshot()
check("TMA2 snapshot: OFF → exec_mode OFF, book PAPER", snap["exec_mode"] == "OFF" and snap["mode"] == "PAPER")
CFG["TMA_V2"]["trade_execution_mode"] = "PAPER_LIVE"
snap = tm._cfg_snapshot()
check("TMA2 snapshot: PAPER_LIVE → book LIVE", snap["exec_mode"] == "PAPER_LIVE" and snap["mode"] == "LIVE")
from app.engine.vet.vet_manager import VetManager
vm = VetManager({"quantity": {"lots": 1, "lot_size": 65}}, repo=vr, chain_fn=lambda side, ts: [], mode="LIVE")
vm.entries_gate = lambda: False
check("VET gate refuses entry while OFF", vm.open_position("CE", ts=1, bar_ts=1, condition=1) is None)
from app.execution import kill_switch as KS
check("kill switch: PAPER_LIVE is live-eligible", KS._mode("SCALP_V1") == "PAPER_LIVE")

print("── 9. server-side vocabulary")
# text scan: config_routes imports the broker SDK; the suite stays hermetic
_app = Path(__file__).resolve().parents[1]
_cr = (_app / "routes" / "config_routes.py").read_text(encoding="utf-8")
_coa = (_app / "license" / "config_override_applier.py").read_text(encoding="utf-8")
check("config route accepts PAPER_LIVE + live_trading wall covers it",
      '_MODE_VALUES = {"OFF", "PAPER", "LIVE", "PAPER_LIVE"}' in _cr
      and 'm in ("LIVE", "PAPER_LIVE") and not license_state.ENTITLEMENTS' in _cr)
check("override applier accepts PAPER_LIVE + wall",
      '_MODE_VALUES = {"OFF", "PAPER", "LIVE", "PAPER_LIVE"}' in _coa
      and 'm in ("LIVE", "PAPER_LIVE") and not license_state.ENTITLEMENTS' in _coa)
from app.db.paper_trade_squareoff import OVERNIGHT_EXEMPT_STRATEGIES
check("TMA_V1 twins survive the 15:25 sweep (positional carry)", "TMA_V1" in OVERNIGHT_EXEMPT_STRATEGIES)

print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")
