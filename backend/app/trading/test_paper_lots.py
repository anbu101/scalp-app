# backend/app/trading/test_paper_lots.py
#
# ── PAPER_LOTS_20261030 ── behavioural suite for separate paper-twin lots in
# PAPER_LIVE mode. Hermetic: temp app.db, configs patched in memory, no
# broker, no TestClient.
#
#   cd backend && PYTHONPATH=$PWD python3 app/trading/test_paper_lots.py

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


TMP = tempfile.mkdtemp(prefix="paper_lots_")
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
    """)

import app.config.strategy_loader as SL
CFG = {}
SL.load_strategy_config = lambda sid: dict(CFG.get(sid) or {})
SL.load_strategy_config_ex = lambda sid: (dict(CFG.get(sid) or {}), False)

from app.trading import paper_lots as PL
from app.trading import shadow_book as SB
from app.config import lots_whitelist as LW

R = PL.resolve_twin_lots


def twin(sid, ref):
    with sqlite3.connect(DB) as c:
        sh = c.execute("SELECT shadow_id FROM shadow_links WHERE live_ref=?", (ref,)).fetchone()
        if not sh:
            return None
        row = c.execute("SELECT lots, qty FROM paper_trades WHERE paper_trade_id=?", (sh[0],)).fetchone()
        return row


print("── 1. no paper_lots → identical to before")
cfg = {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lots": 2, "lot_size": 65}}
check("absent key → live lots", R("SCALP_V1", 2, cfg=cfg) == (2, None))
check("empty dict → live lots", R("SCALP_V1", 2, cfg={**cfg, "paper_lots": {}}) == (2, None))
check("0 = same as live", R("SCALP_V1", 2, cfg={**cfg, "paper_lots": {"quantity/lots": 0}})[0] == 2)
check("garbage value → live", R("SCALP_V1", 2, cfg={**cfg, "paper_lots": {"quantity/lots": "x"}})[0] == 2)
check("bool value → live", R("SCALP_V1", 2, cfg={**cfg, "paper_lots": {"quantity/lots": True}})[0] == 2)
check("unknown strategy → live", R("LVB_V1", 3, cfg={"paper_lots": {"lots": 9}}) == (3, None))

print("── 2. single-field strategies")
check("SCALP_V1 2→10", R("SCALP_V1", 2, cfg={**cfg, "paper_lots": {"quantity/lots": 10}}) == (10, "quantity.lots"))
c_orb = {"lots": 1, "paper_lots": {"lots": 5}}
check("ORB 1→5", R("ORB_V1", 1, cfg=c_orb)[0] == 5)
check("paper smaller than live (VET 10→2)",
      R("VET_V1", 10, cfg={"quantity": {"lots": 10}, "paper_lots": {"quantity/lots": 2}})[0] == 2)
check("DTE-scaled live row keeps ratio (ORB live 2 of base 1 → 10)", R("ORB_V1", 2, cfg=c_orb)[0] == 10)
check("ratio never rounds to 0", R("VET_V1", 1, cfg={"quantity": {"lots": 10}, "paper_lots": {"quantity/lots": 2}})[0] == 1)

print("── 3. multi-field strategies (one paper value per lots field)")
c_tma2 = {"s1": {"main": {"lots": 1}, "hedge": {"lots": 1}},
          "paper_lots": {"s1/main/lots": 10, "s1/hedge/lots": 12}}
check("TMA_V2 SELL → main", R("TMA_V2", 1, trade_class="SELL", cfg=c_tma2) == (10, "s1.main.lots"))
check("TMA_V2 BUY → hedge", R("TMA_V2", 1, trade_class="BUY", cfg=c_tma2) == (12, "s1.hedge.lots"))
check("TMA_V2 unknown class → live", R("TMA_V2", 1, trade_class="", cfg=c_tma2) == (1, None))
c_tma1 = {"c1": {"sell": {"lots": 2}, "buy": {"lots": 2}}, "paper_lots": {"c1/sell/lots": 4}}
check("TMA_V1 SELL 2→4", R("TMA_V1", 2, trade_class="SELL", cfg=c_tma1)[0] == 4)
check("TMA_V1 BUY blank = same", R("TMA_V1", 2, trade_class="BUY", cfg=c_tma1)[0] == 2)

c_bb2 = {"ce_lots": 1, "pe_lots": 2, "paper_lots": {"ce_lots": 5, "pe_lots": 6}}
check("BB_V2 CE by side", R("BB_V2", 1, side="CE", cfg=c_bb2)[0] == 5)
check("BB_V2 PE by symbol suffix", R("BB_V2", 2, side="", symbol="NIFTY26N0324000PE", cfg=c_bb2)[0] == 6)

legs = [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 2},
        {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 2},
        {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 2},
        {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 2}]
c_ic = {"legs": legs, "adjust": {"L1": {"lots": 3}, "L2": {"lots": 3}},
        "paper_lots": {"legs/0/lots": 10, "legs/1/lots": 10, "legs/2/lots": 11,
                       "legs/3/lots": 11, "adjust/L1/lots": 15}}
check("IC_V2 L1 → legs.0", R("IC_V2", 2, trade_class="L1", cfg=c_ic) == (10, "legs.0.lots"))
check("IC_V2 L4 → legs.3", R("IC_V2", 2, trade_class="L4", cfg=c_ic) == (11, "legs.3.lots"))
check("IC_V2 L1A → adjust.L1", R("IC_V2", 3, trade_class="L1A", cfg=c_ic) == (15, "adjust.L1.lots"))
check("IC_V2 L2A blank = same", R("IC_V2", 3, trade_class="L2A", cfg=c_ic)[0] == 3)
check("IC_V1 has no adjust path → live", R("IC_V1", 3, trade_class="L1A", cfg=c_ic) == (3, None))

print("── 4. TSG expiry-day lots")
c_tsg = {"lots": 2, "expiry_lots": 4, "paper_lots": {"lots": 10, "expiry_lots": 20}}
_orig = PL._is_expiry_today
PL._is_expiry_today = lambda: False
check("TSG normal day → lots", R("TSG_V1", 2, trade_class="L1", cfg=c_tsg) == (10, "lots"))
PL._is_expiry_today = lambda: True
check("TSG expiry day → expiry_lots", R("TSG_V1", 4, trade_class="L1", cfg=c_tsg) == (20, "expiry_lots"))
check("TSG live expiry_lots 0 → lots path even on expiry",
      R("TSG_V1", 2, cfg={"lots": 2, "expiry_lots": 0, "paper_lots": {"lots": 10, "expiry_lots": 20}}) == (10, "lots"))
PL._is_expiry_today = lambda: None
check("TSG calendar unknown → match by size", R("TSG_V1", 4, cfg=c_tsg) == (20, "expiry_lots"))
check("TSG calendar unknown, base size → lots", R("TSG_V1", 2, cfg=c_tsg) == (10, "lots"))
PL._is_expiry_today = _orig

print("── 5. BB_V1 leg split keeps the paper total")
c_bb1 = {"lots": 3, "paper_lots": {"lots": 10}}
check("BB_V1 1+2 of 3 → 3+7 = 10", R("BB_V1", 1, cfg=c_bb1)[0] + R("BB_V1", 2, cfg=c_bb1)[0] == 10)

print("── 6. every LOTS_PATHS field resolves for some row")
for sid, paths in LW.LOTS_PATHS.items():
    check(f"friend_paths {sid}", LW.friend_paths(sid) == list(paths) + [LW.paper_path(p) for p in paths])
check("paper_path shape", LW.paper_path("legs.0.lots") == "paper_lots.legs/0/lots")
check("paper_key ≡ paper_path tail", "paper_lots." + PL.paper_key("s1.main.lots") == LW.paper_path("s1.main.lots"))

print("── 7. shadow_book: twin booked at paper size, live untouched")
CFG["TMA_V2"] = {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lot_size": 65},
                 "s1": {"main": {"lots": 1}, "hedge": {"lots": 1}},
                 "paper_lots": {"s1/main/lots": 10, "s1/hedge/lots": 10}}
SB.mirror_open(live_ref="TMA_V2:1", strategy_id="TMA_V2", symbol="NIFTY26N0324000PE", side="PE",
               entry_price=120.0, qty=65, trade_direction="SHORT", trade_class="SELL")
SB.mirror_open(live_ref="TMA_V2:2", strategy_id="TMA_V2", symbol="NIFTY26N0323000PE", side="PE",
               entry_price=4.0, qty=65, trade_direction="LONG", trade_class="BUY")
check("SELL twin 10 lots / 650 qty", twin("TMA_V2", "TMA_V2:1") == (10, 650), twin("TMA_V2", "TMA_V2:1"))
check("BUY twin 10 lots / 650 qty", twin("TMA_V2", "TMA_V2:2") == (10, 650), twin("TMA_V2", "TMA_V2:2"))
SB.mirror_close(live_ref="TMA_V2:1", exit_price=100.0, exit_reason="TP", trade_direction="SHORT")
with sqlite3.connect(DB) as c:
    gp = c.execute("SELECT pnl_value, qty FROM paper_trades WHERE symbol='NIFTY26N0324000PE'").fetchone()
check("twin P&L uses twin qty (20 × 650 = 13,000)", gp is not None and abs((gp[0] or 0) - 13000) < 1, gp)

CFG["SCALP_V1"] = {"trade_execution_mode": "PAPER_LIVE", "quantity": {"lots": 2, "lot_size": 65}}
SB.mirror_open(live_ref="T-1", strategy_id="SCALP_V1", symbol="NIFTY26N0324000CE", side="",
               entry_price=150.0, qty=130)
check("no paper_lots → twin equals live (2 / 130)", twin("SCALP_V1", "T-1") == (2, 130), twin("SCALP_V1", "T-1"))

CFG["VET_V1"] = {"trade_execution_mode": "LIVE", "quantity": {"lots": 1, "lot_size": 65},
                 "paper_lots": {"quantity/lots": 10}}
SB.mirror_open(live_ref="VET_V1:9", strategy_id="VET_V1", symbol="NIFTY26N0324000CE", side="CE",
               entry_price=150.0, qty=65, lots=1, lot_size=65)
check("LIVE (not PAPER_LIVE) never twins even with paper_lots", twin("VET_V1", "VET_V1:9") is None)

print("── 8. non-admin whitelist: paper values clamped like live lots")
try:
    import app.routes.config_routes as CR
    CR.license_state.ENTITLEMENTS["max_lots"] = 5
    cur = {"s1": {"main": {"lots": 1}, "hedge": {"lots": 1}}, "secret": 42}
    inc = {"s1": {"main": {"lots": 3}, "hedge": {"lots": 3}},
           "paper_lots": {"s1/main/lots": 50, "s1/hedge/lots": 4}, "secret": 0}
    m = CR._whitelist_merge("TMA_V2", cur, inc)
    check("paper main clamped to licence cap 5", m.get("paper_lots", {}).get("s1/main/lots") == 5, m)
    check("paper hedge kept at 4", m.get("paper_lots", {}).get("s1/hedge/lots") == 4, m)
    check("live lots still merged", m["s1"]["main"]["lots"] == 3)
    check("non-whitelisted key untouched", m["secret"] == 42)
    inc2 = {"paper_lots": {"s1/main/lots": -2, "s1/hedge/lots": None}}
    m2 = CR._whitelist_merge("TMA_V2", m, inc2)
    check("negative / null paper rejected (stored value kept)",
          m2["paper_lots"] == {"s1/main/lots": 5, "s1/hedge/lots": 4}, m2)
    inc3 = {"paper_lots": {"s1/main/lots": 0}}
    m3 = CR._whitelist_merge("TMA_V2", m2, inc3)
    check("0 clears back to 'same as live'", m3["paper_lots"]["s1/main/lots"] == 0)
    masked = CR._mask_config_response("TMA_V2", {**m3, "secret": 42})
    check("mask exposes paper_lots", masked.get("paper_lots") == m3["paper_lots"], masked)
    check("mask still hides other keys", "secret" not in masked)
    check("IC_V1 rejects adjust paper path",
          "adjust/L1/lots" not in (CR._whitelist_merge("IC_V1", {}, {"paper_lots": {"adjust/L1/lots": 2}}).get("paper_lots") or {}))
except ModuleNotFoundError as e:
    print(f"  SKIP  config_routes import ({e}) — run on the build Mac")

print("── 9. licence override applier reinstates paper values")
try:
    from app.license import config_override_applier as CO
    check("owned paths include paper paths", "paper_lots.s1/main/lots" in CO._owned_lots_paths("TMA_V2"))
except ModuleNotFoundError as e:
    print(f"  SKIP  applier import ({e})")

print()
if FAILS:
    print(f"  {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  ALL CHECKS PASSED")
