# test_mutmut_safety.py — ── MUTMUT_20261117 ── (TD-043) TC-EXEC-0227…0233
# Tests for the most dangerous mutants that SURVIVED the first mutmut run on
# exit_order_driver.py: with each mutant in place every one of the 43 suites
# that reach the driver still passed. Each check below fails on its mutant and
# passes on main (scripts/mutation/prove_kills.py shows it).
#   0227 short buy-back priced off the BUY exit quote (mutants 8, 22, 24, 25, 27, 28)
#   0228 …and off the BUY entry quote on brokers without exit quotes (36, 41, 42)
#   0229 slow quote: re-read THIS order before modifying it (DEF-033 path; 155, 156, 159)
#   0230 re-peg ladder with the defaults IC/TMA2/TSG use (3, 4, 5, 6, 60, 144, 145, 147, 149)
#   0231 DEAD / FILLED results keep outcome, id, status, fill and reason (84…139)
#   0232 a re-peg never backs off the placed limit (61, 62)
#   0233 broker errors and the audit trail: symbol on modify/cancel, Angel-style
#        cancel, QUOTE_ERR / MODIFY_ERR / CANCEL_ERR / REPEG / UNRESOLVED lines
# Virtual clock on the real epoch (no sleeps): every time below is exact.
#   PYTHONPATH=backend python3 backend/app/execution/test_mutmut_safety.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SCALP_TEST_MODE", "1")

from app.execution import exit_order_driver as XD                      # noqa: E402

PASSED, FAILS = [0], []
S = "NIFTY26N2526000PE"
T0 = 1_795_000_000.0                       # a real epoch second (real-clock rule)


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Clock:
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Book:
    """One working order at a scripted broker. Quotes say which side they came from."""
    def __init__(self, clk, *, oid="O1", fill_at=None, quote_s=0.0, fill_in_quote=False,
                 exit_api=True, dead_at=None, dead=None, cancel_result=None, in_quote=None,
                 quote_err=0, modify_err=0, cancel_err=False):
        self.clk, self.oid = clk, oid
        self.fill_at = fill_at                  # BUY: fills once limit >= this; SELL: <=
        self.side = None
        self.quote_s, self.fill_in_quote = quote_s, fill_in_quote
        self.dead_at, self.dead = dead_at, dead or {}
        self.cancel_result = cancel_result      # status after cancel (None: stays OPEN)
        self.filled = None
        self.state = {"status": "OPEN", "filled_qty": 0}
        self.in_quote = in_quote                 # order state the broker reports after a quote
        self.quote_err, self.modify_err, self.cancel_err = quote_err, modify_err, cancel_err
        self.quotes, self.reads, self.modifies, self.attempts, self.cancels = [], [], [], [], 0
        self.mod_syms, self.cancel_args = [], []
        if exit_api:
            self.fresh_buy_exit_limit = lambda s: self._q("buy_exit", (10.0, 10.0, "ask"))
            self.fresh_sell_exit_limit = lambda s: self._q("sell_exit", (10.0, 10.0, "bid"))
        self.fresh_buy_entry_limit = lambda s: self._q("buy_entry", (10.0, 10.0, "ask"))
        self.fresh_sell_entry_limit = lambda s: self._q("sell_entry", (10.0, 10.0, "bid"))

    def _q(self, tag, q):
        self.quotes.append(tag)
        self.clk.sleep(self.quote_s)
        if self.quote_err > 0:
            self.quote_err -= 1
            raise TimeoutError("depth timed out")
        if self.fill_in_quote:
            self.state = {"status": "COMPLETE", "avg_price": 10.05, "filled_qty": 75}
        if self.in_quote:
            self.state = dict(self.in_quote)
        return q

    def get_order_fill(self, oid):
        self.reads.append((self.clk.now() - T0, oid))
        if str(oid) != self.oid:
            return {}                           # Kite knows no such order
        if self.dead_at is not None and self.clk.now() - T0 >= self.dead_at:
            self.state = dict(self.dead)
        return dict(self.state)

    def modify_order(self, oid, price, symbol=""):
        self.attempts.append(price)
        self.mod_syms.append(symbol)
        if self.modify_err > 0:
            self.modify_err -= 1
            raise RuntimeError("Too many requests")
        if self.state["status"] != "OPEN":
            raise RuntimeError("Order cannot be modified as it is being processed or is complete")
        self.modifies.append((self.clk.now() - T0, price))
        if self.fill_at is not None and (price >= self.fill_at if self.side == "BUY" else price <= self.fill_at):
            self.state = {"status": "COMPLETE", "avg_price": price, "filled_qty": 75}
        return True

    def cancel_order(self, oid, symbol=""):
        self.cancels += 1
        self.cancel_args.append((oid, symbol))
        if self.cancel_err:
            raise RuntimeError("cancel refused")
        if self.cancel_result:
            self.state = dict(self.cancel_result)


LOG = []


def drive(book, side, **kw):
    book.side = side
    clk = book.clk
    LOG.clear()
    args = dict(symbol=S, side=side, log=LOG.append, sleep=clk.sleep, clock=clk.now)
    args.update(kw)
    r = XD.drive_exit_order(book, "O1", **args)
    return r, clk.now() - T0


def prices(b):
    return [p for _, p in b.modifies]


# ── 0227 short buy-back: BUY exit quote, limits move UP from the ask ─────────
b = Book(Clock(), fill_at=10.40)
r, dt = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0227 short buy-back re-priced only off the BUY exit quote (ask side)",
      b.quotes and set(b.quotes) == {"buy_exit"}, b.quotes)
check("TC-EXEC-0227 buy-back limits 10.20 then 10.40 (up through the ask), FILLED at 10.40",
      prices(b) == [10.2, 10.4] and r.filled and r.avg_price == 10.4, f"{prices(b)} {r}")

# ── 0228 broker without exit quotes (Angel/Dhan): BUY entry quote, never a SELL quote
b = Book(Clock(), fill_at=10.40, exit_api=False)
r, dt = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0228 no exit quotes: buy-back falls back to the BUY entry quote, never a sell quote",
      b.quotes and set(b.quotes) == {"buy_entry"} and prices(b) == [10.2, 10.4] and r.filled,
      f"{b.quotes} {prices(b)} {r}")

# ── 0229 slow quote: the order filled meanwhile — read THIS order, never modify it
b = Book(Clock(), quote_s=XD.SLOW_QUOTE_S, fill_in_quote=True)      # exactly at the threshold
r, dt = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0229 quote took SLOW_QUOTE_S, order filled meanwhile: FILLED and NO modify attempted",
      r.filled and r.avg_price == 10.05 and not b.attempts, f"{r} attempts={b.attempts}")
check("TC-EXEC-0229 the re-read after the slow quote asks for this order id (O1)",
      b.reads and all(o == "O1" for _, o in b.reads), b.reads)
check("TC-EXEC-0229 that FILLED result keeps id O1, status COMPLETE and the 75 filled",
      (r.order_id, r.status, r.filled_qty) == ("O1", "COMPLETE", 75), r)
b = Book(Clock(), quote_s=XD.SLOW_QUOTE_S, in_quote={"status": "CANCELLED", "filled_qty": 50, "avg_price": 10.15,
                                                    "status_message": "Cancelled by exchange: freeze"})
r, dt = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0229 cancelled by the exchange during a slow quote: DEAD with id, status, 50 filled, price, reason;"
      " no modify",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price, r.message)
      == ("DEAD", "O1", "CANCELLED", 50, 10.15, "Cancelled by exchange: freeze") and not b.attempts, f"{r} {b.attempts}")
b = Book(Clock(), quote_s=XD.SLOW_QUOTE_S, in_quote={"status": "REJECTED", "filled_qty": 0})
r, dt = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0229 rejected during a slow quote, no reason given: DEAD, price 0.0, reason ''",
      (r.outcome, r.filled_qty, r.avg_price, r.message) == ("DEAD", 0, 0.0, ""), r)
b = Book(Clock())                                                     # fast quotes, never fills
r, dt = drive(b, "SELL", chase_s=10.0, repeg_every_s=4.0, settle_s=2.0)
chase_reads = [t for t, _ in b.reads if t < 10.0]
check("TC-EXEC-0229 fast quotes: one order read per poll, no extra read per re-peg",
      chase_reads == [float(i) for i in range(10)], chase_reads)

# ── 0230 the ladder IC/TMA2/TSG get (they pass chase/re-peg, not step/cap/poll/settle)
b = Book(Clock(), cancel_result=None)
r, dt = drive(b, "SELL", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0230 SELL exit, default 2% steps capped at 10%: 9.80 9.60 9.40 9.20 9.00, never lower",
      prices(b) == [9.8, 9.6, 9.4, 9.2, 9.0], prices(b))
check("TC-EXEC-0230 re-pegs exactly every 4 s from the start (4, 8, 12, 16, 20 s)",
      [t for t, _ in b.modifies] == [4.0, 8.0, 12.0, 16.0, 20.0], b.modifies)
check("TC-EXEC-0230 one read per 1 s poll for the whole 25 s chase",
      [t for t, _ in b.reads if t < 25.0] == [float(i) for i in range(25)], b.reads[:30])
check("TC-EXEC-0230 still open at the deadline: cancelled once, 6 s settle, then UNRESOLVED at 31 s",
      b.cancels == 1 and r.outcome == "UNRESOLVED" and dt == 31.0, f"{r} cancels={b.cancels} dt={dt}")
check("TC-EXEC-0230 that UNRESOLVED reports nothing filled (0 @ 0.0): no phantom late fill for IC to book",
      (r.filled_qty, r.avg_price, r.order_id) == (0, 0.0, "O1"), r)
b = Book(Clock())
r, dt = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0230 BUY exit mirror: 10.20 10.40 10.60 10.80 11.00, never higher",
      prices(b) == [10.2, 10.4, 10.6, 10.8, 11.0], prices(b))

# ── 0231 results keep what the callers act on (TMA2 books a DEAD partial fill)
b = Book(Clock(), dead_at=3.0, dead={"status": "CANCELLED", "filled_qty": 75, "avg_price": 10.1,
                                     "status_message": "Cancelled by exchange: price band"})
r, _ = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0231 cancelled by the exchange mid-chase, part filled: DEAD with id, status, qty, price, reason",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price, r.message)
      == ("DEAD", "O1", "CANCELLED", 75, 10.1, "Cancelled by exchange: price band"), r)
b = Book(Clock(), dead_at=0.0, dead={"status": "REJECTED", "filled_qty": 0,
                                     "status_message": "Insufficient funds"})
r, _ = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0231 rejected: DEAD, no fill, price 0.0, reason kept",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price, r.message)
      == ("DEAD", "O1", "REJECTED", 0, 0.0, "Insufficient funds"), r)
b = Book(Clock(), fill_at=10.20)
r, _ = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0231 filled in the chase: FILLED with id O1, status COMPLETE, qty 75",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price) == ("FILLED", "O1", "COMPLETE", 75, 10.2), r)
b = Book(Clock(), cancel_result={"status": "CANCELLED", "filled_qty": 25, "avg_price": 10.3,
                                 "status_message": "Cancelled by user"})
r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0231 deadline cancel lands, part filled: DEAD with id, status, qty, price, reason",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price, r.message)
      == ("DEAD", "O1", "CANCELLED", 25, 10.3, "Cancelled by user"), r)
b = Book(Clock(), dead_at=0.0, dead={"status": "LAPSED", "filled_qty": 0})
r, _ = drive(b, "SELL", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0231 lapsed with no reason given: DEAD, reason '' (not made up)",
      (r.outcome, r.status, r.message) == ("DEAD", "LAPSED", ""), r)
b = Book(Clock(), cancel_result={"status": "COMPLETE", "filled_qty": 75, "avg_price": 9.9})
r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0231 filled in the cancel race: FILLED with id O1, status COMPLETE",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price) == ("FILLED", "O1", "COMPLETE", 75, 9.9), r)

b = Book(Clock(), cancel_result={"status": "OPEN", "filled_qty": 25, "avg_price": 10.3})
r, dt = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0231 cancel not confirmed, 25 already filled: UNRESOLVED keeps id, status OPEN, 25 @ 10.3"
      " (IC books that part fill)",
      (r.outcome, r.order_id, r.status, r.filled_qty, r.avg_price) == ("UNRESOLVED", "O1", "OPEN", 25, 10.3), r)
for label, kw in (("in the cancel race", dict(cancel_result={"status": "COMPLETE"})),
                  ("after a slow quote", dict(quote_s=XD.SLOW_QUOTE_S, in_quote={"status": "COMPLETE"}))):
    b = Book(Clock(), **kw)
    r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
    check(f"TC-EXEC-0231 COMPLETE {label} without price/qty from the broker: FILLED with 0.0 / 0 (never made up)",
          (r.outcome, r.avg_price, r.filled_qty) == ("FILLED", 0.0, 0), r)
b = Book(Clock(), cancel_result={"status": "CANCELLED"})
r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0231 deadline cancel lands, broker gives no price/reason: DEAD with 0.0 and ''",
      (r.outcome, r.avg_price, r.filled_qty, r.message) == ("DEAD", 0.0, 0, ""), r)

# ── 0232 never back off the placed limit (start_px)
b = Book(Clock())
r, _ = drive(b, "BUY", chase_s=25.0, repeg_every_s=4.0, start_px=10.5)
check("TC-EXEC-0232 BUY placed at 10.50: no re-peg below it — first modify 10.60, then 10.80, 11.00",
      prices(b) == [10.6, 10.8, 11.0], prices(b))
b = Book(Clock())
r, _ = drive(b, "SELL", chase_s=25.0, repeg_every_s=4.0, start_px=9.5)
check("TC-EXEC-0232 SELL placed at 9.50: no re-peg above it — first modify 9.40, then 9.20, 9.00",
      prices(b) == [9.4, 9.2, 9.0], prices(b))

# ── 0233 broker errors and the audit trail
b = Book(Clock(), quote_err=1, modify_err=1)
r, dt = drive(b, "SELL", chase_s=25.0, repeg_every_s=4.0)
check("TC-EXEC-0233 quote error at the 1st re-peg is logged (QUOTE_ERR + symbol), the chase goes on",
      any(m.startswith("[EXIT_DRIVER][QUOTE_ERR]") and S in m and "depth timed out" in m for m in LOG), LOG[:4])
check("TC-EXEC-0233 modify refused at the 2nd: MODIFY_ERR with the order id; limit not taken as placed",
      any(m.startswith("[EXIT_DRIVER][MODIFY_ERR] O1") and "Too many requests" in m for m in LOG)
      and prices(b) == [9.4, 9.2, 9.0], f"{LOG[:6]} {prices(b)}")
check("TC-EXEC-0233 every modify names the contract", b.mod_syms and set(b.mod_syms) == {S}, b.mod_syms)
check("TC-EXEC-0233 each accepted re-peg is logged: REPEG, symbol, side, order id, new limit",
      [m for m in LOG if m.startswith("[EXIT_DRIVER][REPEG]")] and all(
          S in m and " SELL " in m and "order=O1" in m and "limit=" in m
          for m in LOG if m.startswith("[EXIT_DRIVER][REPEG]"))
      and [m.split("limit=")[1].split(" ")[0] for m in LOG if "[REPEG]" in m] == ["9.4", "9.2", "9.0"], LOG)
check("TC-EXEC-0233 deadline: TIMEOUT logged, cancel sent for O1 with the symbol",
      any(m.startswith("[EXIT_DRIVER][TIMEOUT]") and "order=O1" in m for m in LOG)
      and b.cancel_args == [("O1", S)], f"{b.cancel_args} {LOG[-3:]}")
check("TC-EXEC-0233 still working after the settle: UNRESOLVED logged as 'order may STILL BE WORKING'",
      r.outcome == "UNRESOLVED" and any(m.startswith("[EXIT_DRIVER][UNRESOLVED]") and "order=O1" in m
                                        and "STILL BE WORKING" in m for m in LOG), LOG[-2:])


class AngelBook(Book):
    def cancel_order(self, oid):                 # Angel/Dhan: no symbol argument
        self.cancels += 1
        self.cancel_args.append((oid, None))
        self.state = {"status": "CANCELLED", "filled_qty": 0}


b = AngelBook(Clock())
r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0233 broker whose cancel takes only the id: cancel of O1 still sent, DEAD CANCELLED",
      b.cancel_args == [("O1", None)] and (r.outcome, r.status) == ("DEAD", "CANCELLED"), f"{b.cancel_args} {r}")
class AngelBadCancel(AngelBook):
    def cancel_order(self, oid):
        self.cancels += 1
        raise RuntimeError("angel cancel refused")


b = AngelBadCancel(Clock())
r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0233 id-only cancel refused too: CANCEL_ERR with the order id and reason, then UNRESOLVED",
      any(m.startswith("[EXIT_DRIVER][CANCEL_ERR] O1") and "angel cancel refused" in m for m in LOG)
      and r.outcome == "UNRESOLVED" and b.cancels == 1, f"{r} {LOG[-3:]}")
b = Book(Clock(), cancel_err=True)
r, _ = drive(b, "SELL", chase_s=5.0, repeg_every_s=4.0)
check("TC-EXEC-0233 cancel refused: CANCEL_ERR with the order id and reason, then UNRESOLVED (never FILLED/DEAD)",
      any(m.startswith("[EXIT_DRIVER][CANCEL_ERR] O1") and "cancel refused" in m for m in LOG)
      and r.outcome == "UNRESOLVED", f"{r} {LOG[-3:]}")

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL MUTMUT SAFETY CHECKS PASSED")
