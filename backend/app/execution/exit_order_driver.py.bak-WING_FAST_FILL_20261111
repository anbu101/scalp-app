# backend/app/execution/exit_order_driver.py
#
# ── EXIT_ORDER_DRIVER_20261026 ── drive a working EXIT order to a settled
# outcome: re-peg it toward the touch, and if it still has not filled by the
# deadline, CANCEL it and report what the broker says actually happened.
# ============================================================================
# WHY (2026-10-26 TMA_V2 incident): the SL buy-back of NIFTY26OCT22400CE was
# placed as a LIMIT 1% above LTP (182.75) on a fast move. The price ran to
# ~209 and the order rested unfilled. The manager's fill-confirm timed out
# after 45s and simply MOVED ON — the order was never re-priced and never
# cancelled. Consequences:
#   * the hedge sell fired with the short still open → broker rejected it on
#     margin (naked-short margin 1.84L > 1.6L available);
#   * the app booked both legs CLOSED at model prices;
#   * after the user flattened manually at 09:30, the forgotten 182.75 order
#     FILLED at 09:35:49 when price dipped back, opening an untracked LONG.
#
# CONTRACT: an order handed to drive_exit_order() is never abandoned in an
# unknown state silently. The caller gets exactly one of:
#   FILLED      — COMPLETE; avg_price / filled_qty valid.
#   DEAD        — REJECTED / CANCELLED / LAPSED (incl. our own timeout
#                 cancel). filled_qty may be > 0 (partial fill, then cancel):
#                 the caller MUST re-read the broker position before acting.
#   UNRESOLVED  — could not confirm a terminal state even after cancelling.
#                 The order may STILL BE WORKING. The caller must keep the
#                 order id and must NOT place a second order for the same leg
#                 until a later call resolves it.
#
# Uses only router-forwarded executor methods: get_order_fill, modify_order,
# cancel_order, fresh_buy_entry_limit / fresh_sell_entry_limit. Any of the
# optional ones missing degrades to "no re-peg" (wait, then cancel).
# ============================================================================

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

DEAD_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}
TICK = 0.05


@dataclass
class DriveResult:
    outcome: str                 # FILLED | DEAD | UNRESOLVED
    order_id: str
    avg_price: float = 0.0
    filled_qty: int = 0
    status: Optional[str] = None
    message: str = ""

    @property
    def filled(self) -> bool:
        return self.outcome == "FILLED"


def _tick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


def _read(executor, order_id) -> dict:
    try:
        return executor.get_order_fill(order_id) or {}
    except Exception:
        return {}


def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,
                     chase_s: float = 25.0, repeg_every_s: float = 4.0,
                     step_pct: float = 0.02, max_extra_pct: float = 0.10,
                     poll_s: float = 1.0, settle_s: float = 6.0,
                     repeg: bool = True,
                     log: Callable[[str], None] = print,
                     sleep: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.time) -> DriveResult:
    """side: "BUY" (buying back a short) or "SELL" (selling a long).

    Re-peg k uses the executor's fresh touch-based limit (ask+buffer for BUY,
    bid−buffer for SELL) pushed a further k×step_pct through the touch, capped
    at max_extra_pct. The limit only ever moves in the aggressive direction:
    it is a cap, so a more aggressive limit never fills at a worse price than
    the book offers."""
    side = side.upper()
    oid = str(order_id)
    fresh = getattr(executor, ("fresh_buy_entry_limit" if side == "BUY"
                               else "fresh_sell_entry_limit"), None)
    modify = getattr(executor, "modify_order", None)
    if not repeg:                       # confirm-only (entries): wait, cancel, settle
        fresh = modify = None

    start = clock()
    deadline = start + chase_s
    last_repeg = start
    k = 0
    last_px: Optional[float] = None
    info: dict = {}

    while clock() < deadline:
        info = _read(executor, oid)
        status = (info.get("status") or "").upper() or None
        if status == "COMPLETE":
            return DriveResult("FILLED", oid, float(info.get("avg_price") or 0.0),
                               int(info.get("filled_qty") or 0), status)
        if status in DEAD_STATUSES:
            return DriveResult("DEAD", oid, float(info.get("avg_price") or 0.0),
                               int(info.get("filled_qty") or 0), status,
                               str(info.get("status_message") or ""))
        if (callable(fresh) and callable(modify)
                and clock() - last_repeg >= repeg_every_s):
            last_repeg = clock()
            k += 1
            q = None
            try:
                q = fresh(symbol)
            except Exception as e:
                log(f"[EXIT_DRIVER][QUOTE_ERR] {symbol} {e}")
            if q:
                base = float(q[0])
                extra = min(step_pct * k, max_extra_pct)
                tgt = _tick(base * (1 + extra)) if side == "BUY" \
                    else max(TICK, _tick(base * (1 - extra)))
                if last_px is not None:
                    tgt = max(tgt, last_px) if side == "BUY" else min(tgt, last_px)
                if tgt != last_px:
                    ok = None
                    try:
                        ok = modify(oid, price=tgt, symbol=symbol)
                    except Exception as e:
                        log(f"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}")
                    if ok:
                        last_px = tgt
                        log(f"[EXIT_DRIVER][REPEG] {symbol} {side} order={oid} "
                            f"#{k} limit={tgt} (touch-limit {base}, +{extra:.0%})")
        sleep(poll_s)

    # Deadline: never leave it working unattended — cancel, then settle.
    log(f"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after "
        f"{chase_s:.0f}s — cancelling")
    try:
        executor.cancel_order(oid, symbol=symbol)
    except TypeError:
        try:
            executor.cancel_order(oid)
        except Exception as e:
            log(f"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}")
    except Exception as e:
        log(f"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}")

    settle_end = clock() + settle_s
    while True:
        info = _read(executor, oid)
        status = (info.get("status") or "").upper() or None
        if status == "COMPLETE":           # filled in the cancel race
            return DriveResult("FILLED", oid, float(info.get("avg_price") or 0.0),
                               int(info.get("filled_qty") or 0), status)
        if status in DEAD_STATUSES:
            return DriveResult("DEAD", oid, float(info.get("avg_price") or 0.0),
                               int(info.get("filled_qty") or 0), status,
                               str(info.get("status_message") or ""))
        if clock() >= settle_end:
            break
        sleep(poll_s)
    log(f"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status="
        f"{status} after cancel — order may STILL BE WORKING")
    return DriveResult("UNRESOLVED", oid, float(info.get("avg_price") or 0.0),
                       int(info.get("filled_qty") or 0), status)
