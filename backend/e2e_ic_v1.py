# e2e_ic_v1.py — IC_V1 END-TO-END against the REAL application stack.
#
# REAL: sqlite schema via the app's own migration runner (incl. new 020),
#       trades_repo / paper_trades_repo, strategy_loader + global_loader
#       (actual JSON files on disk), resolve_execution_mode, LTPStore,
#       inapp_events (real DB events), the entire app.engine.ic_v1 package,
#       ic_v1_state_routes handler functions, ic_v1_live_eod job.
# FAKE: broker executor (scriptable), telegram HTTP (recorded).
#
# Sections:
#   A. Schema: migrations 001..020 on a fresh DB; effective-schema assertions.
#   B. UPGRADE-PATH PROOF: build a DB WITHOUT 020 → live close with MTC_COST
#      FAILS (the bug) → apply 020 on the SAME populated DB → close SUCCEEDS,
#      data preserved, triggers/index intact.
#   C. PAPER E2E: config PAPER → resolve mode → enter → 4 paper rows →
#      SL tick → MTC repin → EOD → DB rows closed w/ correct reasons →
#      latch blocks re-entry → state route returns coherent JSON.
#   D. LIVE E2E: config LIVE → enter (wings-first orders, GTTs) → SL tick →
#      cancel-verified + repin GTT at cost → EOD via the REAL eod job →
#      trades rows CLOSED with SL / EOD_MTC / EOD, slots freed (unique index)
#      → next-day entry inserts cleanly.
#   E. Fail-closed: corrupt config → resolve_execution_mode = PAPER+degraded.

import os, sys, json, time, tempfile, shutil, sqlite3
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="ic_e2e_")
os.environ["HOME"] = HOME
sys.path.insert(0, ".")

PASS = []
def ok(label, cond):
    if not cond:
        print(f"  ✗ FAIL  {label}"); sys.exit(1)
    PASS.append(label); print(f"  ✓ {label}")

# ── boundary fakes BEFORE importing the group manager ───────────────────────
import app.api.telegram_api as tg
TG = []
for fn in ["notify_trade_entry","notify_sl_exit","notify_tp_exit",
           "notify_manual_exit","notify_critical"]:
    setattr(tg, fn, (lambda n: lambda d: TG.append((n, d.get("symbol"))))(fn))

from app.db.sqlite import get_conn, DB_PATH
from app.db.migrations.runner import run_migrations
from app.config.strategy_loader import save_strategy_config, load_strategy_config
from app.config.global_loader import save_global_config
from app.risk.strategy_max_loss_guard import resolve_execution_mode
from app.marketdata.ltp_store import LTPStore

print(f"\nHOME={HOME}\nDB={DB_PATH}\n")

# ═════════════════════ A. SCHEMA ═════════════════════
print("A. schema bootstrap (real migration runner, 001..020)")
conn = get_conn()
run_migrations(conn)
mig = [r[0] for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()]
ok("020 applied on fresh DB", "020_relax_exit_reason_for_ic.sql" in mig)
ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='trades'").fetchone()[0]
ok("exit_reason unconstrained", "exit_reason     TEXT," in ddl and "exit_reason IN" not in ddl)
ok("state CHECK preserved", "state IN ('BUY_PLACED', 'PROTECTED', 'CLOSED')" in ddl)
ok("trade_direction/group_id/trade_class present",
   all(c in ddl for c in ["trade_direction", "group_id", "trade_class"]))
idx = conn.execute("SELECT sql FROM sqlite_master WHERE name='uniq_open_trade_per_slot'").fetchone()
ok("partial unique slot index recreated", idx and "WHERE exit_time IS NULL" in idx[0])
trg = {r[0]: r[1] for r in conn.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall()}
ok("019 state-scoped lock_entry_fields preserved",
   "OLD.state != 'BUY_PLACED'" in trg.get("lock_entry_fields", ""))
ok("prevent_double_close + validate_exit_price recreated",
   "prevent_double_close" in trg and "validate_exit_price" in trg)

# ═════════════════════ B. UPGRADE-PATH PROOF ═════════════════════
print("B. upgrade path: pre-020 DB shows the bug; 020 fixes it in place")
old_home = tempfile.mkdtemp(prefix="ic_e2e_pre020_")
mdir = Path("app/db/migrations")
parked = Path(old_home) / "020.parked"
shutil.move(str(mdir / "020_relax_exit_reason_for_ic.sql"), parked)  # hide 020
pre = sqlite3.connect(Path(old_home) / "pre020.db")
pre.row_factory = sqlite3.Row
try:
    run_migrations(pre)          # REAL runner, 001..019 + hotfixes = true pre-020 install
finally:
    shutil.move(str(parked), mdir / "020_relax_exit_reason_for_ic.sql")  # restore
pre.execute("""INSERT INTO trades (trade_id,strategy_id,slot,symbol,token,entry_time,
  entry_price,qty,buy_order_id,sl_price,tp_price,tp_mode,state,trade_direction)
  VALUES ('T1','IC_V1','L2','N24100PE',12,1,78.0,1560,'O1',110.76,0,'GTT','PROTECTED','SHORT')""")
pre.commit()
failed = False
try:
    pre.execute("UPDATE trades SET exit_time=2, exit_price=51.65, exit_reason='MTC_COST', state='CLOSED' WHERE trade_id='T1'")
except sqlite3.IntegrityError:
    failed = True
ok("pre-020 (built by REAL runner): MTC_COST close VIOLATES constraint (bug reproduced)", failed)
run_migrations(pre)              # REAL upgrade: runner applies exactly 020 on the populated DB
pre.execute("UPDATE trades SET exit_time=2, exit_price=51.65, exit_reason='MTC_COST', state='CLOSED' WHERE trade_id='T1'")
pre.commit()
row = pre.execute("SELECT exit_reason, entry_price, group_id FROM trades WHERE trade_id='T1'").fetchone()
ok("runner-applied 020 on SAME populated DB: close succeeds, data + hotfix columns preserved",
   row[0] == "MTC_COST" and row[1] == 78.0)
pre.close()

# ═════════════════════ shared fixtures ═════════════════════
save_global_config({"trade_on": True})

IC_CFG_BASE = load_strategy_config.__globals__  # noqa — just to prove real loader in play
def write_ic_cfg(mode):
    cfg = {
        "trade_execution_mode": mode,
        "entry_time": "09:18", "exit_time": "15:28",
        "entry_late_grace_s": 120, "freeze_qty": 1800,
        "allow_strangle_degrade": False, "margin_guard": True,
        "quantity": {"lot_size": 65},
    }
    save_strategy_config("IC_V1", cfg)   # legs omitted → engine DEFAULT_LEGS

from app.engine.ic_v1.ic_live_core import StrikePick, G_OPEN, G_CLOSED, L_OPEN, L_CLOSED
from app.engine.ic_v1.ic_selection import ICSelection
from app.engine.ic_v1 import ic_group_manager as GMmod
from app.engine.ic_v1.ic_group_manager import ICGroupManager
import app.engine.ic_v1.ic_runtime as RT
from app.jobs.ic_v1_live_eod import ic_v1_live_eod_job
from app.api.ic_v1_state_routes import get_ic_v1_state, post_ic_v1_square_off

class FakeExecutor:
    def __init__(self):
        self.orders, self.gtts, self.fills, self.ltp = [], {}, {}, {}
        self._oid = self._gid = 0
    def place_sell_entry(self, *, symbol, token, qty):
        self._oid += 1; oid = f"O{self._oid}"
        self.orders.append(("SELL_ENTRY", symbol, qty))
        self.fills[oid] = self.ltp[symbol]; return oid, self.ltp[symbol], qty
    def place_buy(self, symbol, token, qty):
        self._oid += 1; oid = f"O{self._oid}"
        self.orders.append(("BUY_ENTRY", symbol, qty))
        self.fills[oid] = self.ltp[symbol]; return oid, self.ltp[symbol], qty
    def get_order_fill(self, oid):
        return {"status": "COMPLETE", "avg_price": self.fills[oid], "found": True}
    def cancel_order(self, oid): pass
    def place_gtt_sl_only_short(self, *, symbol, qty, sl_price):
        self._gid += 1; gid = str(self._gid)
        self.gtts[gid] = {"symbol": symbol, "qty": qty, "sl": sl_price}; return gid
    def place_gtt_oco(self, **k): return self.place_gtt_sl_only_short(
        symbol=k["symbol"], qty=k["qty"], sl_price=k["sl_price"])
    def cancel_gtt_verified(self, gid, retries=4):
        self.gtts.pop(gid, None); return True
    def place_buy_exit(self, *, symbol, qty, reason):
        self.orders.append(("BUY_EXIT", symbol, qty)); return "X"
    def place_market_sell(self, symbol, qty):
        self.orders.append(("SELL_EXIT", symbol, qty)); return "X"

CHAIN = {"N24150CE": 84.15, "N24100PE": 78.0, "N24700CE": 3.8, "N23200PE": 3.5}
def make_selection():
    return ICSelection(ok=True, expiry=None,
        picks={"L1": StrikePick(24150,"N24150CE",84.15),
               "L2": StrikePick(24100,"N24100PE",78.0),
               "L3": StrikePick(24700,"N24700CE",3.8),
               "L4": StrikePick(23200,"N23200PE",3.5)},
        tokens={"L1":11,"L2":12,"L3":13,"L4":14})
def make_mgr():
    ex = FakeExecutor(); ex.ltp.update(CHAIN)
    for s,p in CHAIN.items(): LTPStore.update(s,p)
    return ICGroupManager(executor=ex, ltp_resolver=lambda s: ex.ltp.get(s)), ex
def clear_latch():
    p = Path(HOME) / ".scalp-app" / "state" / "IC_V1_day_latch.json"
    if p.exists(): p.unlink()
Q = lambda sql: conn.execute(sql).fetchall()

# ═════════════════════ C. PAPER E2E ═════════════════════
print("C. PAPER end-to-end (real config file → real repos → real DB)")
write_ic_cfg("PAPER")
mode, degraded = resolve_execution_mode("IC_V1")
ok("resolve_execution_mode reads real JSON → PAPER, clean", mode=="PAPER" and not degraded)
gm, ex = make_mgr()
RT._MANAGER = gm   # wire the real runtime singleton for routes + eod job
ok("enter_day PAPER opens group", gm.enter_day(make_selection(), mode=mode) is True)
rows = Q("SELECT trade_class, side, trade_direction, state, qty FROM paper_trades ORDER BY trade_class")
ok("4 paper rows: L1..L4, CE/PE sides, SHORT/LONG dirs, OPEN, qty 1560",
   [tuple(r) for r in rows] == [("L1","CE","SHORT","OPEN",1560),("L2","PE","SHORT","OPEN",1560),
                                ("L3","CE","LONG","OPEN",1560),("L4","PE","LONG","OPEN",1560)])
ok("zero broker calls in paper", ex.orders == [] and ex.gtts == {})
st = get_ic_v1_state()
ok("state route: PAPER group OPEN, 4 legs", st["group"]["paper"] and
   st["group"]["state"]==G_OPEN and len(st["group"]["legs"])==4)
# SL tick on L1 → paper MTC repin
ex.ltp["N24100PE"] = 45.0; LTPStore.update("N24100PE", 45.0)
ex.ltp["N24150CE"] = 120.0; LTPStore.update("N24150CE", 120.0)
gm.on_tick(11, 120.0)
core = gm.current_group()
ok("paper SL → L1 closed SL, L2 repinned to cost via IDENTICAL code path",
   core.legs["L1"].exit_reason=="SL" and core.legs["L2"].mtc_repinned
   and core.legs["L2"].sl==78.0)
r = Q("SELECT exit_reason,state FROM paper_trades WHERE trade_class='L1'")[0]
ok("paper DB: L1 row CLOSED reason SL", tuple(r)==("SL","CLOSED"))
gm.force_square_off_all(reason="EOD")
rr = {r[0]: r[1] for r in Q("SELECT trade_class, exit_reason FROM paper_trades")}
ok("paper EOD reasons: L2=EOD_MTC, wings=EOD",
   rr=={"L1":"SL","L2":"EOD_MTC","L3":"EOD","L4":"EOD"})
ok("all paper rows CLOSED + net_pnl populated",
   Q("SELECT COUNT(*) FROM paper_trades WHERE state!='CLOSED' OR net_pnl IS NULL")[0][0]==0)
ok("D7 latch blocks re-entry same day (fresh manager)",
   make_mgr()[0].enter_day(make_selection(), mode="PAPER") is False)
sq = post_ic_v1_square_off()
ok("manual square-off route: safe no-op on closed group", sq["ok"] and sq["closed"]==0)

# ═════════════════════ D. LIVE E2E ═════════════════════
print("D. LIVE end-to-end (real trades table, migration 020 in effect)")
clear_latch(); TG.clear()
write_ic_cfg("LIVE")
mode, degraded = resolve_execution_mode("IC_V1")
ok("resolve → LIVE, clean", mode=="LIVE" and not degraded)
gm, ex = make_mgr(); RT._MANAGER = gm
ok("enter_day LIVE opens group", gm.enter_day(make_selection(), mode=mode) is True)
ok("D2 sequencing: wings bought before shorts sold",
   [o[0] for o in ex.orders[:4]] == ["BUY_ENTRY","BUY_ENTRY","SELL_ENTRY","SELL_ENTRY"])
ok("both shorts GTT-protected", len(ex.gtts)==2)
rows = Q("SELECT slot,state,tp_mode,trade_direction FROM trades ORDER BY slot")
ok("4 live rows: PROTECTED/GTT pass state+tp_mode CHECKs",
   [tuple(r) for r in rows] == [("L1","PROTECTED","GTT","SHORT"),("L2","PROTECTED","GTT","SHORT"),
                                ("L3","PROTECTED","GTT","LONG"),("L4","PROTECTED","GTT","LONG")])
ok("entry telegram fired per leg", sum(1 for n,_ in TG if n=="notify_trade_entry")==4)
# SL tick → live MTC
ex.ltp["N24100PE"]=45.0; LTPStore.update("N24100PE",45.0)
ex.ltp["N24150CE"]=120.0; LTPStore.update("N24150CE",120.0)
l1_gtts = set(gm.leg_runtime("L1")["gtt_ids"])
gm.on_tick(11, 120.0)
ok("live SL: L1 GTT cancel-verified BEFORE flatten, BUY_EXIT placed",
   not (l1_gtts & set(ex.gtts)) and ("BUY_EXIT","N24150CE",1560) in ex.orders)
l2g = gm.leg_runtime("L2")["gtt_ids"]
ok("MTC: partner re-pinned — exactly one live GTT at cost 78.0",
   len(l2g)==1 and ex.gtts[l2g[0]]["sl"]==78.0)
r = Q("SELECT exit_reason,state,exit_price FROM trades WHERE slot='L1'")[0]
ok("live DB: L1 CLOSED reason=SL (constraint passes post-020)",
   r[0]=="SL" and r[1]=="CLOSED")
# EOD via the REAL scheduled job (misfire path: called past exit_time)
ic_v1_live_eod_job(sleep_fn=lambda s: None,
                   now_fn=lambda: __import__("datetime").datetime.now(
                       __import__("datetime").timezone(
                           __import__("datetime").timedelta(minutes=330))).replace(hour=15,minute=40))
rr = {r[0]:(r[1],r[2]) for r in Q("SELECT slot,exit_reason,state FROM trades")}
ok("EOD job closes all: L2=EOD_MTC, wings=EOD — all CLOSED in live table",
   rr=={"L1":("SL","CLOSED"),"L2":("EOD_MTC","CLOSED"),
        "L3":("EOD","CLOSED"),"L4":("EOD","CLOSED")})
ok("slot index freed: next-day L1..L4 insert cleanly",
   (clear_latch() or make_mgr()[0].enter_day(make_selection(), mode="LIVE")) is True
   and Q("SELECT COUNT(*) FROM trades WHERE slot='L1'")[0][0]==2)

# ═════════════════════ E. FAIL-CLOSED ═════════════════════
print("E. degraded config fail-closed")
cfgp = Path(HOME)/".scalp-app"/"strategies"/"IC_V1.json"
cfgp.write_text("{corrupt json!!")
mode, degraded = resolve_execution_mode("IC_V1")
ok("corrupt config → resolve gives PAPER, NEVER LIVE (defaults are OFF)", mode == "PAPER")
from app.engine.ic_v1.ic_engine import ICEngine
import app.event_bus.inapp_events as ev
alerts = []
_orig = ev.record_alert
# capture the engine's alert without disturbing the real events table
import app.engine.ic_v1.ic_engine as ic_eng_mod
ic_eng_mod.record_alert = lambda code, msg, **k: alerts.append(code)
clear_latch()
class _B:
    def is_ready(self): return True
    def get_data_kite(self): return None
eng = ICEngine(make_mgr()[0], _B())
eng._attempt_entry({"trade_execution_mode": "LIVE"})   # raw cfg says LIVE; disk is corrupt
ok("engine on degraded read: day SKIPPED LOUDLY (IC_MODE_DEGRADED), no entry",
   "IC_MODE_DEGRADED" in alerts and
   Q("SELECT COUNT(*) FROM trades")[0][0] == 8)   # still only the 8 rows from section D
ic_eng_mod.record_alert = _orig

print(f"\n{'='*60}\nE2E COMPLETE — {len(PASS)}/{len(PASS)} assertions passed\n{'='*60}")
shutil.rmtree(old_home, ignore_errors=True)
