# backend/app/execution/wing_entry.py
#
# ── WING_FAST_FILL_20261111 ── one way to buy a hedge wing, shared by
# TSG_V1, IC_V1/IC_V2, TMA_V2 and VET_V1.
#
#   place_wing()    executor.place_wing_buy (ask + max(₹1, 20%), cap ₹3);
#                   executors without it degrade to place_buy.
#   confirm_wing()  waits for the fill and RE-PRICES every WING_REPEG_S
#                   seconds off a fresh ask (never below the placed limit),
#                   up to cap_s; on timeout the order is cancelled and the
#                   outcome confirmed (the shared exit driver's teardown).
#
# Before this, IC_V2 and TMA_V2 placed the wing at LTP + 1% and waited up to
# 45 s with NO re-price while the short waited behind it.
from __future__ import annotations

from typing import Callable, Optional, Tuple

try:
    from app.execution.exit_order_driver import drive_exit_order, DriveResult
except ImportError:                                        # standalone tests
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _P
    _m = _sys.modules.get("_exit_order_driver")
    if _m is None:
        _sp = _ilu.spec_from_file_location(
            "_exit_order_driver", _P(__file__).resolve().parent / "exit_order_driver.py")
        _m = _ilu.module_from_spec(_sp)
        _sys.modules["_exit_order_driver"] = _m
        _sp.loader.exec_module(_m)
    drive_exit_order, DriveResult = _m.drive_exit_order, _m.DriveResult

FENCE = "WING_FAST_FILL_20261111"
WING_REPEG_S = 2.0


def place_wing(executor, symbol: str, token, qty: int) -> Tuple[Optional[str], Optional[float]]:
    """(order_id or None, limit or None). Raises like the executor does."""
    fn = getattr(executor, "place_wing_buy", None)
    out = fn(symbol, token, qty) if callable(fn) else executor.place_buy(symbol, token, qty)
    if isinstance(out, (tuple, list)):
        oid = out[0]
        lim = out[1] if len(out) > 1 else None
    else:
        oid, lim = out, None
    if oid is None or oid == "":
        return None, None                 # executor's "not placed" (no exception)
    return str(oid), (float(lim) if lim else None)


def confirm_wing(executor, order_id: str, symbol: str, *, cap_s: float,
                 start_px: Optional[float], poll_s: float,
                 log: Callable[[str], None],
                 sleep: Callable[[float], None],
                 clock: Callable[[], float],
                 repeg_every_s: float = WING_REPEG_S) -> DriveResult:
    """Drive a working wing BUY to a fill. Returns the driver's DriveResult
    (FILLED / DEAD / UNRESOLVED) — callers keep their own partial/late-fill
    handling on top of it."""
    if not order_id or str(order_id) == "None":
        log(f"[WING][FILL] {symbol}: no order id — nothing to confirm")
        return DriveResult("DEAD", "", status="NOT PLACED", message="no order id")
    fresh = getattr(executor, "fresh_wing_buy_limit", None)
    if not callable(fresh):
        fresh = getattr(executor, "fresh_buy_entry_limit", None)
    t0 = clock()
    r = drive_exit_order(executor, str(order_id), symbol=symbol, side="BUY",
                         chase_s=cap_s, repeg_every_s=repeg_every_s,
                         step_pct=0.0, max_extra_pct=0.0, poll_s=poll_s,
                         repeg=True, log=log, sleep=sleep, clock=clock,
                         fresh_fn=fresh, start_px=start_px)
    try:
        log(f"[WING][FILL] {symbol} order={order_id} {r.outcome} "
            f"avg={r.avg_price} wait={(clock() - t0):.1f}s limit0={start_px}")
    except Exception:
        pass
    return r
