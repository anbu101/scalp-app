# backend/app/engine/brk/test_brk_manager.py
#
# ── BRK_V1 MANAGER TESTS ── stubbed executor + in-memory paper_trades.
# Fence BRK_V1_LIVE_20260902. Covers the checklist's mandatory legs:
#   * paper entry → tick SL exit
#   * LIVE two-phase entry: place_buy → fill poll → row at REAL fill →
#     ONE OCO GTT with BOTH legs, id persisted as trade_class "GTT:<id>"
#   * engine exit orders GTT-cancel-VERIFIED **BEFORE** the market sell
#   * GTT-race: cancel unverifiable + broker flat → row closed, NO sell
#   * MID-DAY RESTART: resume_from_db rebuilds the position (incl. gtt id)
#     and the S2 gate's s1_result from closed rows
#   * kill path = close with reason KILL
#
# Run from repo root:
#     python3 backend/app/engine/brk/test_brk_manager.py .

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO / "backend" / "app" / "backtest" / "brk"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── in-memory paper_trades + module stubs (IC test pattern) ──────────────
DB = {}          # pid -> row dict
CALLS = []       # ordered (fn, args) record — the ordering assertions


def _mk(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    parts = name.split(".")
    for i in range(1, len(parts)):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
        setattr(sys.modules[pkg], parts[i], sys.modules.get(".".join(parts[:i + 1]), m))
    return m


_al = _mk("app.event_bus.audit_logger")
_al.write_audit_log = lambda msg: None
_ev = _mk("app.event_bus.inapp_events")
_ev.record_alert = lambda **k: None
_tg = _mk("app.api.telegram_api")
for fn in ("notify_trade_entry", "notify_sl_exit", "notify_tp_exit",
           "notify_manual_exit"):
    setattr(_tg, fn, (lambda n: lambda d: CALLS.append((n, d)))(fn))

_pr = _mk("app.db.paper_trades_repo")


def insert_paper_trade(**k):
    k["state"] = "OPEN"
    DB[k["paper_trade_id"]] = k
    CALLS.append(("insert", k["symbol"]))


def close_paper_trade(*, paper_trade_id, exit_price, exit_reason,
                      trade_direction=None):
    row = DB.get(paper_trade_id)
    if not row or row["state"] != "OPEN":
        CALLS.append(("close_SKIP", paper_trade_id))
        return
    row.update(state="CLOSED", exit_price=exit_price, exit_reason=exit_reason)
    CALLS.append(("close", (row["symbol"], exit_price, exit_reason)))


_pr.insert_paper_trade = insert_paper_trade
_pr.close_paper_trade = close_paper_trade


class _Row(dict):
    def keys(self):
        return super().keys()


class _Conn:
    def execute(self, q, args=()):
        class _Cur:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows
        rows = []
        for r in DB.values():
            rows.append(_Row(
                paper_trade_id=r["paper_trade_id"], symbol=r["symbol"],
                token=r["token"], side=r["side"], trade_mode=r["trade_mode"],
                entry_price=r["entry_price"], sl_price=r["sl_price"],
                tp_price=r["tp_price"], qty=r["qty"], lots=r["lots"],
                group_id=r.get("group_id"), trade_class=r.get("trade_class"),
                state=r["state"], exit_price=r.get("exit_price"),
                candle_ts=r["candle_ts"]))
        return _Cur(rows)


_db = _mk("app.db.database")
_db.get_conn = lambda: _Conn()

# fill packages so `from app.engine.brk.brk_manager import ...` resolves
for pkg in ("app", "app.engine", "app.engine.brk", "app.db", "app.api",
            "app.event_bus"):
    if pkg not in sys.modules:
        sys.modules[pkg] = types.ModuleType(pkg)

import importlib.util as _ilu  # noqa: E402


def _load(name, path):
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


HERE = Path(__file__).resolve().parent
_load("app.engine.brk.brk_live_core", HERE / "brk_live_core.py")
M = _load("app.engine.brk.brk_manager", HERE / "brk_manager.py")

FAILED = []


def chk(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{('  ' + extra) if extra else ''}")
    if not cond:
        FAILED.append(label)


CFG = {"sl_pts": 16, "tp_pts": 46, "quantity": {"lots": 1, "lot_size": 65}}


class StubExec:
    """Records every call, in order, for the ordering assertions."""

    def __init__(self, *, fill_immediately=True, cancel_ok=True, flat=False,
                 fill_price=182.35):
        self.fill_immediately = fill_immediately
        self.cancel_ok = cancel_ok
        self.flat = flat
        self.fill_price = fill_price
        self.gtts = {}

    def place_buy(self, symbol, token, qty):
        CALLS.append(("place_buy", symbol, qty))
        return ("OID1", self.fill_price if self.fill_immediately else 0.0,
                qty if self.fill_immediately else 0)

    def get_order_fill(self, order_id):
        CALLS.append(("get_order_fill", order_id))
        return {"status": "COMPLETE", "avg_price": self.fill_price,
                "filled_qty": 65, "pending_qty": 0, "found": True}

    def place_gtt_oco(self, symbol, qty, sl_price, tp_price,
                      last_price=None, direction="LONG"):
        CALLS.append(("place_gtt_oco", symbol, sl_price, tp_price, direction))
        gid = f"G{len(self.gtts) + 1}"
        self.gtts[gid] = (symbol, sl_price, tp_price)
        return gid

    def cancel_gtt_verified(self, gtt_id, retries=4):
        CALLS.append(("cancel_gtt_verified", gtt_id))
        return self.cancel_ok

    def get_positions(self):
        CALLS.append(("get_positions",))
        qty = 0 if self.flat else 65
        return {"net": [{"tradingsymbol": "NIFTY26SEP24500CE",
                         "quantity": qty}]}

    def place_market_sell(self, symbol, qty):
        CALLS.append(("place_market_sell", symbol, qty))
        return "SID1"


def mk(mode="PAPER", **ex_kw):
    ex = StubExec(**ex_kw)
    gm = M.BrkManager(executor=ex, cfg_fn=lambda: CFG,
                      mode_fn=lambda: mode,
                      quote_fn=lambda s: 190.0)
    return gm, ex


def order_of(name, occurrence=1):
    n = 0
    for i, c in enumerate(CALLS):
        if c[0] == name:
            n += 1
            if n == occurrence:
                return i
    return -1


print("── 1. paper lifecycle ────────────────────────────────────────────")
DB.clear(); CALLS.clear()
gm, _ = mk("PAPER")
ok = gm.open_trade(symbol="NIFTY26SEP24500CE", token=111, side="CE",
                   tag="BRK", ltp=181.0, sl_px=165.0, tp_px=227.0)
chk("1a. paper entry inserts an OPEN row at the decision LTP",
    ok and len(DB) == 1 and list(DB.values())[0]["entry_price"] == 181.0
    and list(DB.values())[0]["trade_mode"] == "PAPER"
    and list(DB.values())[0]["group_id"] == "BRK")
gm.close_trade(reason="SL", ltp=165.0)
row = list(DB.values())[0]
chk("1b. tick SL exit closes the row at the tick",
    row["state"] == "CLOSED" and row["exit_price"] == 165.0
    and row["exit_reason"] == "SL" and gm.pos is None)
chk("1c. s1_result = closed morning net", gm.s1_result() == (165.0 - 181.0) * 65)
chk("1d. telegram: entry + SL exit fired",
    any(c[0] == "notify_trade_entry" for c in CALLS)
    and any(c[0] == "notify_sl_exit" for c in CALLS))

print("── 2. LIVE entry: two-phase + ONE OCO GTT ────────────────────────")
DB.clear(); CALLS.clear()
gm, ex = mk("LIVE", fill_immediately=False, fill_price=182.35)
ok = gm.open_trade(symbol="NIFTY26SEP24500CE", token=111, side="CE",
                   tag="BRK", ltp=181.0, sl_px=165.0, tp_px=227.0)
row = list(DB.values())[0]
chk("2a. row records the REAL fill (182.35), not the decision LTP",
    ok and row["entry_price"] == 182.35 and row["trade_mode"] == "LIVE")
chk("2b. exits recomputed off the fill: sl 166.35 / tp 228.35",
    row["sl_price"] == 166.35 and row["tp_price"] == 228.35)
g = [c for c in CALLS if c[0] == "place_gtt_oco"]
chk("2c. exactly ONE OCO GTT with BOTH legs, direction LONG",
    len(g) == 1 and g[0][2] == 166.35 and g[0][3] == 228.35
    and g[0][4] == "LONG")
chk("2d. GTT placed AFTER the fill poll",
    order_of("place_gtt_oco") > order_of("get_order_fill"))
chk("2e. gtt id persisted in trade_class",
    row["trade_class"] == f"GTT:{gm.pos.gtt_id}" and gm.pos.gtt_id == "G1")

print("── 3. LIVE engine exit: cancel-verified BEFORE the sell ──────────")
gm.close_trade(reason="EOD", ltp=205.0)
chk("3a. order: cancel_gtt_verified → place_market_sell → close",
    -1 < order_of("cancel_gtt_verified") < order_of("place_market_sell"))
row = list(DB.values())[0]
chk("3b. row closed at the sell fill (poll returned 182.35 stub avg)",
    row["state"] == "CLOSED" and row["exit_reason"] == "EOD"
    and row["exit_price"] == 182.35)

print("── 4. GTT race: cancel unverifiable + broker flat → NO sell ──────")
DB.clear(); CALLS.clear()
gm, ex = mk("LIVE", cancel_ok=False, flat=True)
gm.open_trade(symbol="NIFTY26SEP24500CE", token=111, side="CE",
              tag="BRK", ltp=181.0, sl_px=165.0, tp_px=227.0)
CALLS.clear()
gm.close_trade(reason="TP", ltp=228.4)
chk("4a. GTT won the race: row closed, position cleared",
    list(DB.values())[0]["state"] == "CLOSED" and gm.pos is None)
chk("4b. NO market sell was placed",
    order_of("place_market_sell") == -1)
chk("4c. flatness was VERIFIED at the broker, not assumed",
    order_of("get_positions") > -1)

print("── 5. GTT race, position NOT flat: sell anyway (screaming) ───────")
DB.clear(); CALLS.clear()
gm, ex = mk("LIVE", cancel_ok=False, flat=False)
gm.open_trade(symbol="NIFTY26SEP24500CE", token=111, side="CE",
              tag="BRK", ltp=181.0, sl_px=165.0, tp_px=227.0)
CALLS.clear()
gm.close_trade(reason="EOD")
chk("5a. unverifiable cancel + open position → sell proceeds",
    order_of("place_market_sell") > -1
    and list(DB.values())[0]["state"] == "CLOSED")

print("── 6. MID-DAY RESTART: resume from DB ────────────────────────────")
DB.clear(); CALLS.clear()
gm, ex = mk("PAPER")                              # morning: paper, closes at the tick
gm.open_trade(symbol="NIFTY26SEP24500CE", token=111, side="CE",
              tag="BRK", ltp=181.0, sl_px=165.0, tp_px=227.0)
gm.close_trade(reason="SL", ltp=165.0)           # morning lost
gm2, _ = mk("LIVE")
gm2.open_trade(symbol="NIFTY26SEP24600CE", token=112, side="CE",
               tag="BRK·S2", ltp=175.0, sl_px=159.0, tp_px=221.0)
fresh, _ = mk("LIVE")                              # ← the restart
fresh.resume_from_db()
chk("6a. open S2 position rebuilt (symbol/entry/gtt id)",
    fresh.pos is not None and fresh.pos.symbol == "NIFTY26SEP24600CE"
    and fresh.pos.tag == "BRK·S2" and fresh.pos.gtt_id == "G1"
    and fresh.pos.mode == "LIVE")
chk("6b. s1_result recovered from the closed morning row (a loss)",
    fresh.s1_result() is not None and fresh.s1_result() < 0)
chk("6c. s1_open False after restart (open pos is S2, not S1)",
    fresh.s1_open() is False)
fresh.close_trade(reason="EOD", ltp=200.0)
chk("6d. resumed position closes through the full GTT-cancel path",
    any(c[0] == "cancel_gtt_verified" and c[1] == "G1" for c in CALLS))

print("── 7. kill + double-entry guard ──────────────────────────────────")
DB.clear(); CALLS.clear()
gm, ex = mk("PAPER")
gm.open_trade(symbol="A", token=1, side="CE", tag="BRK", ltp=181.0,
              sl_px=165.0, tp_px=227.0)
chk("7a. second entry refused while a position is open",
    gm.open_trade(symbol="B", token=2, side="PE", tag="BRK·S2", ltp=170.0,
                  sl_px=154.0, tp_px=216.0) is False and len(DB) == 1)
chk("7b. kill_all closes with reason KILL",
    gm.kill_all() == 1 and list(DB.values())[0]["exit_reason"] == "KILL")
chk("7c. kill on a flat book is a 0 no-op", gm.kill_all() == 0)

print(f"\n{'ALL PASS' if not FAILED else f'{len(FAILED)} FAILED: ' + ', '.join(FAILED)}")
sys.exit(1 if FAILED else 0)