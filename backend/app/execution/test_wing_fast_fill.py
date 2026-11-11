# backend/app/execution/test_wing_fast_fill.py
#
# ── WING_FAST_FILL_20261111 ── hedge wings are bought at best ask +
# max(₹1, 20%) (cap ₹3) and re-priced off a fresh ask every 2 s, so the
# short behind them never waits on a one-tick bid. Market model: a book with
# a SPREAD (FakeKite fills buys at LTP; here a buy fills only at the ask) —
# the shape of 11-Nov, where LTP + 1% sat at or below the offer.
# Run:  PYTHONPATH=backend python3 backend/app/execution/test_wing_fast_fill.py
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

LOG = []
import app.event_bus.audit_logger as AL                                 # noqa: E402
AL.write_audit_log = lambda m: LOG.append(m)
import app.execution.zerodha_executor as ZE                             # noqa: E402
ZE.write_audit_log = lambda m: LOG.append(m)
ZE._load_relay_config = lambda: None
import app.config.global_loader as GL                                   # noqa: E402
GL.load_global_config = lambda: {"trade_on": True}
from app.execution import testkit_fake_kite as FK                       # noqa: E402
from app.execution.execution_router import ExecutionRouter              # noqa: E402
from app.execution.wing_entry import place_wing, confirm_wing           # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ── a fake book with a spread ──────────────────────────────────────────
class SpreadKite(FK.FakeKite):
    """px = LTP = bid; ask = px + spread. A BUY fills only at/above the ask,
    at the ask. Wing pricing mirrors ZerodhaOrderExecutor exactly."""

    def __init__(self, prices, spread=None, **k):
        self.spread = dict(spread or {})
        super().__init__(prices, **k)

    def ask(self, s):
        return FK.r05(self.px[s] + self.spread.get(s, 0.0))

    def tick(self):
        for o in self.orders:
            if o["status"] != FK.WORKING:
                continue
            s = o["tradingsymbol"]
            if s not in self.px:
                continue
            buy = o["transaction_type"] == "BUY"
            fill_px = self.ask(s) if buy else self.px[s]
            if (buy and o["price"] >= fill_px) or (not buy and o["price"] <= fill_px):
                o.update(status="COMPLETE", filled_quantity=o["quantity"], pending_quantity=0,
                         average_price=fill_px)
                self.net[s] = self.net.get(s, 0) + (o["quantity"] if buy else -o["quantity"])

    def place_wing_buy(self, symbol, token, qty):
        lim = ZE.ZerodhaOrderExecutor._wing_limit_price(self.ask(symbol))
        return (self._new_order(symbol, "BUY", qty, lim), lim, qty)

    def fresh_wing_buy_limit(self, symbol):
        a = self.ask(symbol)
        return (ZE.ZerodhaOrderExecutor._wing_limit_price(a), a, "ask")

    def fresh_buy_entry_limit(self, symbol):
        a = self.ask(symbol)
        return (FK.r05(a * 1.01), a, "ask")


WING = "NIFTY26N1723400PE"


def drive(kite, oid, lim, cap=45.0):
    return confirm_wing(ExecutionRouter(kite), oid, WING, cap_s=cap, start_px=lim, poll_s=0.3,
                        log=LOG.append, sleep=kite.clock.sleep, clock=kite.clock)


# ── 1. the price itself ────────────────────────────────────────────────
def t_pricing():
    L = ZE.ZerodhaOrderExecutor._wing_limit_price
    cases = {5.00: 6.00, 5.05: 6.10, 0.80: 1.80, 7.50: 9.00, 8.00: 9.60, 20.0: 23.00, 120.0: 123.00}
    got = {k: L(k) for k in cases}
    check("wing limit = ask + max(₹1, 20%), capped ₹3, rounded UP to the tick",
          got == cases, str(got))
    check("every limit stays far inside NSE limit price protection (±₹20 under ₹50)",
          all(L(a) - a <= 3.0 + 1e-9 for a in (0.5, 5, 25, 49.95)))


# ── 2. the Zerodha executor places it off the ASK ──────────────────────
def t_executor_places_off_ask():
    class K:
        VARIETY_REGULAR, TRANSACTION_TYPE_BUY, ORDER_TYPE_LIMIT, PRODUCT_NRML = "r", "BUY", "LIMIT", "NRML"

        def __init__(self):
            self.orders = []

        def quote(self, k):
            return {k: {"last_price": 5.0, "depth": {"buy": [{"price": 4.95}], "sell": [{"price": 5.10}]}}}

        def ltp(self, k):
            return {k: {"last_price": 5.0}}

        def instruments(self, ex=None):
            return [{"tradingsymbol": WING, "lot_size": 65}]

        def place_order(self, **kw):
            self.orders.append(kw)
            return "OIDW"
    ZE._LOT_CSV_DONE.set()
    k = K()
    bm = types.SimpleNamespace(is_trade_ready=lambda: True, refresh=lambda: None,
                               get_trade_kite=lambda: k, get_data_kite=lambda: k)
    ex = ZE.ZerodhaOrderExecutor(bm)
    LOG.clear()
    oid, lim, q = ex.place_wing_buy(WING, 1, 130)
    o = k.orders[0]
    check("11-Nov wing: LTP 5.00 / ask 5.10 → BUY LIMIT 6.15 (was 5.05, below the ask)",
          (o["transaction_type"], o["price"], o["quantity"], lim) == ("BUY", 6.15, 130, 6.15), str(o))
    check("logged with ltp / ask / limit, and a latency line",
          any("[ZERODHA-WING-BUY]" in l and "ask=5.1" in l and "limit=6.15" in l for l in LOG)
          and any(l.startswith("[ZERODHA][LAT] BUY " + WING) for l in LOG), str(LOG))
    check("fresh_wing_buy_limit re-prices off the ask too", ex.fresh_wing_buy_limit(WING)[0] == 6.15)


# ── 3. fill time: old one-tick bid vs the wing bid ────────────────────
def t_fill_time():
    k_old = SpreadKite({WING: 5.00}, {WING: 0.10})
    r_old = ExecutionRouter(k_old)
    oid = r_old.place_buy(WING, 1, 130)[0]                        # LTP+1% = 5.05 < ask 5.10
    t0 = k_old.clock()
    st = None
    while k_old.clock() - t0 < 45:
        st = k_old.get_order_fill(oid)["status"]
        if st == "COMPLETE":
            break
        k_old.clock.sleep(0.3)
    check("OLD (LTP+1%, no re-price): wing still unfilled after 45 s on a 2-tick spread",
          st != "COMPLETE", st)

    k = SpreadKite({WING: 5.00}, {WING: 0.10})
    oid, lim = place_wing(ExecutionRouter(k), WING, 1, 130)
    t0 = k.clock()
    r = drive(k, oid, lim)
    check("NEW: wing fills at once — at the ASK 5.10, not at the 6.15 limit",
          r.outcome == "FILLED" and r.avg_price == 5.10 and k.clock() - t0 < 0.5,
          f"{r} {k.clock() - t0:.1f}s")


# ── 4. the market runs away after the order is in ─────────────────────
def t_runaway_reprice():
    k = SpreadKite({WING: 5.00}, {WING: 0.10})
    k.px[WING] = 5.00
    lim = ZE.ZerodhaOrderExecutor._wing_limit_price(k.ask(WING))       # 6.15
    k.spread[WING] = 0.10
    oid = k._new_order(WING, "BUY", 130, lim, match=False)
    k.px[WING] = 7.00                                                   # ask jumps to 7.10 before any match
    t0 = k.clock()
    r = drive(k, oid, lim)
    mods = [l for l in LOG if "[EXIT_DRIVER][REPEG]" in l and WING in l]
    check("ask jumps past the limit → re-priced within ~2 s and filled",
          r.outcome == "FILLED" and k.clock() - t0 <= 2.6 and r.avg_price == 7.10,
          f"{r} {k.clock() - t0:.1f}s")
    check("…by one MODIFY to the fresh ask + buffer (8.55)", any("limit=8.55" in m for m in mods), str(mods))


def t_never_reprices_down():
    k = SpreadKite({WING: 5.00}, {WING: 0.10})
    k.modify_ok = True
    lim = 6.15
    oid = k._new_order(WING, "BUY", 130, lim, match=False)
    k.px[WING] = 9.00                                  # out of reach: never fills…
    seen = []
    real = k.modify_order
    k.modify_order = lambda o, price, symbol="": (seen.append(price), real(o, price, symbol))[1]
    k.px[WING] = 9.00
    k.spread[WING] = 0.10
    # …and then the ask falls back below the placed limit before the 2 s re-price
    k.px[WING] = 4.00
    k.spread[WING] = 3.00                              # ask 7.00 > 6.15 → still unfilled
    r = drive(k, oid, lim, cap=2.5)
    check("a re-price never LOWERS the wing bid", all(p >= lim for p in seen), str(seen))
    check("…and it goes up to the fresh ask + buffer and fills",
          r.outcome == "FILLED" and seen and seen[0] == 8.40, f"{r} {seen}")


def t_degrade_and_guards():
    k = FK.FakeKite({WING: 5.00})                      # an executor without wing pricing
    oid, lim = place_wing(ExecutionRouter(k), WING, 1, 130)
    check("executor without place_wing_buy → router degrades to place_buy (unchanged)",
          oid and k.orders[-1]["price"] == 5.05, str(k.orders[-1]))
    r = confirm_wing(ExecutionRouter(k), None, WING, cap_s=45, start_px=None, poll_s=0.3,
                     log=LOG.append, sleep=k.clock.sleep, clock=k.clock)
    check("no order id → DEAD at once (no 45 s poll)", r.outcome == "DEAD" and r.status == "NOT PLACED")


# ── 5. every hedge-wing caller uses it; shorts and option buyers do not ──
def t_wiring():
    root = HERE.parents[0]
    src = {n: (root / p).read_text() for n, p in {
        "TSG": "engine/tsg/tsg_manager.py", "IC": "engine/ic/ic_group_manager.py",
        "TMA2": "engine/tma2/tma2_trade_manager.py", "VET": "engine/vet/vet_manager.py"}.items()}
    check("TSG, IC, TMA2 and VET place wings through place_wing()",
          all("place_wing(self.executor" in s for s in src.values()))
    check("IC, TMA2 and VET confirm wings through confirm_wing() (TSG: its own 2 s re-peg loop)",
          all("confirm_wing(self.executor" in src[n] for n in ("IC", "TMA2", "VET"))
          and "wing_fill_timeout_s" in src["TSG"])
    others = ["engine/orb/orb_engine.py", "engine/brk/brk_engine.py", "engine/emc/emc_engine.py",
              "engine/lvb/lvb_manager.py", "engine/ha_options/ha_trade_manager.py"]
    check("option-BUYING strategies untouched (no wing pricing on a ₹150 buy)",
          not any("place_wing" in ((root / p).read_text() if (root / p).exists() else "") for p in others))


def t_tsg_e2e_wing_first():
    import importlib
    sys.path.insert(0, str(HERE.parents[0] / "engine" / "tsg"))
    H = importlib.import_module("test_tsg_e2e")
    kite = SpreadKite(dict(H.PX), {s: 0.10 for s in H.PX})
    m, kite, _ = H.setup(kite=kite)
    ok = m.enter_day(H.chain(), mode="LIVE")
    buys = [o for o in kite.orders if o["transaction_type"] == "BUY"]
    check("TSG LIVE: both wings fill on the spread book, then the shorts go",
          ok and len(buys) == 2 and all(o["status"] == "COMPLETE" for o in buys)
          and [e[0] for e in kite.events][:2] == ["BUY", "BUY"], str(kite.events))
    check("TSG wings bid at ask + buffer, filled at the ask",
          all(o["price"] == ZE.ZerodhaOrderExecutor._wing_limit_price(kite.ask(o["tradingsymbol"]))
              and o["average_price"] == kite.ask(o["tradingsymbol"]) for o in buys), str(buys))


def t_tma2_e2e_hedge():
    import importlib
    sys.path.insert(0, str(HERE.parents[0] / "engine" / "tma2"))
    H = importlib.import_module("test_tma2_e2e")
    real = H.FakeKite
    H.FakeKite = lambda px: SpreadKite(px, {s: 0.10 for s in px})
    try:
        m, kite, ch, _ = H.setup()
        H.enter(m, ch)
    finally:
        H.FakeKite = real
    buy = next((o for o in kite.orders if o["transaction_type"] == "BUY"), None)
    check("TMA2 LIVE: hedge filled on the spread book and the SELL followed",
          m.group is not None and buy and buy["status"] == "COMPLETE"
          and buy["average_price"] == kite.ask(buy["tradingsymbol"]), str(kite.orders))


def t_vet_wing():
    import app.engine.vet.vet_manager as VM
    from app.engine.vet.vet_common import VetRepo
    import os
    VM.write_audit_log = lambda m: None
    k = SpreadKite({f"NIFTY26N17{x}PE": p for x, p in ((25400, 223.0), (23400, 5.0))},
                   {"NIFTY26N1723400PE": 0.10})
    k.lot = 65

    def chain(side, ts):
        return [{"tradingsymbol": s, "token": 1, "strike": float(s[10:15]), "expiry": "2026-11-17",
                 "instrument_type": s[-2:], "ltp": p} for s, p in k.px.items() if s.endswith(side)]
    cfg = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 5.0, "atm_offset": 0,
           "eod_square": False, "quantity": {"lots": 2, "lot_size": 65}, "_spot": 25400.0}
    repo = VetRepo(os.path.join(tempfile.mkdtemp(prefix="wff_"), "v.db"))
    repo.ensure_schema()
    m = VM.VetManager(cfg, repo=repo, chain_fn=chain, quote_fn=lambda s: k.px.get(s),
                      executor=ExecutionRouter(k), mode="LIVE")
    m._sleep, m._clock = k.clock.sleep, k.clock
    if hasattr(m, "wing_search_fn"):
        m.wing_search_fn = None
    t0 = k.clock()
    p = m.open_position("PE", ts=1_794_000_000, bar_ts=1_794_000_000, condition=1)
    buy = next((o for o in k.orders if o["transaction_type"] == "BUY"), None)
    check("VET LIVE: wing BUY at ask + buffer, filled at the ask, short follows at once",
          p is not None and buy and buy["price"] == 6.15 and buy["average_price"] == 5.10
          and [e[0] for e in k.events][:2] == ["BUY", "SELL"] and k.clock() - t0 < 1.0,
          f"{p} {k.orders} {k.clock() - t0:.1f}s")


if __name__ == "__main__":
    for fn in (t_pricing, t_executor_places_off_ask, t_fill_time, t_runaway_reprice,
               t_never_reprices_down, t_degrade_and_guards, t_wiring, t_tsg_e2e_wing_first,
               t_tma2_e2e_hedge, t_vet_wing):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__}: {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL WING_FAST_FILL CHECKS PASSED")
