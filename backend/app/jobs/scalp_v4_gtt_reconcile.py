# backend/app/jobs/scalp_v4_gtt_reconcile.py
"""
SCALP_V4 — Hedge GTT Reconciliation Loop
========================================
WHY THIS EXISTS
---------------
SCALP_V4 protects its LONG hedge with an SL-only GTT at the broker. When that
GTT fires, the broker sells the hedge — the position goes flat — but NOTHING in
the V4 engine notices on its own:

  - _watch_exit() in the tick engine handles the hedge's own SL only in PAPER
    mode ("live uses the broker GTT"). In LIVE it watches only the SIGNAL
    contract's SL/TP.
  - The single-trade gate is the OPEN row in scalp_v4_trades. close_hedge_trade
    is only called from _watch_exit (SIG_SL/SIG_TP, or hedge-SL in PAPER) and
    from EOD.

So in LIVE, when the hedge SL-GTT fills, the row stays OPEN, the gate stays
held, and no new V4 trade can enter until the SIGNAL contract later happens to
hit its own SL/TP (which closes it via the ALREADY_FLAT path) — or until EOD.
That can block the slot for a long time / the rest of the day.

This is the detector V4 was missing. V1 has reconcile_with_broker, V2 has its
GTT monitor, BB has GTTMonitor — V4 had none. This loop is the V4 analogue.

WHAT IT DOES (every RECONCILE_INTERVAL_SEC, LIVE only)
------------------------------------------------------
  1. get_manager() — the live manager, fresh each cycle (None before start).
  2. get_open_v4_trade(paper=False) — the single live open trade, if any.
  3. Skip if no hedge_gtt_id yet (entry still pending; the SL-GTT is linked only
     after fill-confirm). This avoids a false close during the entry window.
  4. get_gtts() — find the hedge GTT; act only if status is triggered/disabled.
  5. Cross-check the hedge position is actually flat at the broker (belt +
     suspenders, like BB verifies position before acting on a triggered GTT).
  6. close_hedge_trade(reason="HEDGE_SL") — the EXISTING close path. Its
     ALREADY_FLAT branch records the exit, tags HEDGE_SL, closes the row, and
     frees the gate. No new exit logic is introduced here.

SAFETY (mirrors BB's cardinal rule: "a network error must NEVER close a trade")
  - Any get_gtts()/get_open_positions() exception SKIPS the cycle and retries in
    RECONCILE_INTERVAL_SEC. A missed detection is harmless (it retries); a false
    close on a transient API error would wrongly free the gate mid-trade.
  - The signal-vs-reconcile race (signal SL/TP firing at ~the same instant) is a
    no-op: close_hedge_trade guards on state != "OPEN" and returns early for
    whichever path runs second. No extra locking needed.
  - PAPER trades are ignored here — they exit via _watch_exit's paper hedge-SL
    branch, exactly as before.

LAUNCH
  Started by api_server as a standalone asyncio task next to the SCALP_V4
  selection loop, under the same enabled + license gate. Uses get_manager()
  internally, so it takes no handle (mirrors how the EOD job reaches the
  manager). Self-contained: SCALP_V1 / BB / HA / V2 untouched.
"""

import asyncio

from app.event_bus.audit_logger import write_audit_log
from app.engine.scalp_v4.scalp_v4_selection_loop import get_manager
from app.db.scalp_v4_repo import get_open_v4_trade


STRATEGY_ID = "SCALP_V4"
RECONCILE_INTERVAL_SEC = 10

# Broker GTT statuses that mean the SL-only GTT has fired.
_FIRED_GTT_STATUSES = ("triggered", "disabled")


def _hedge_flat_at_broker(manager, hedge_symbol: str) -> bool:
    """
    True iff the broker shows NO open position for hedge_symbol.

    FAIL-CLOSED on uncertainty: if positions cannot be read, return False
    (treat as still-open) so we do NOT close the row on an API hiccup. A real
    fill will still be caught on a later cycle once the position reads cleanly.
    """
    try:
        positions = manager.executor.get_open_positions() or []
    except Exception as e:
        write_audit_log(
            f"[V4_RECON][POS_CHECK_ERR] {hedge_symbol} ERR={e} — treating as still open"
        )
        return False

    for p in positions:
        if p.get("tradingsymbol") == hedge_symbol and p.get("quantity", 0) != 0:
            return False
    return True


def _reconcile_once() -> None:
    """One reconcile pass. Fully wrapped: never raises, never false-closes."""
    manager = get_manager()
    if manager is None:
        return  # engine not started yet this cycle

    # LIVE only. PAPER hedge-SL is handled by the tick engine's _watch_exit.
    try:
        row = get_open_v4_trade(paper=False)
    except Exception as e:
        write_audit_log(f"[V4_RECON][OPEN_READ_ERR] {e} — skipping cycle")
        return
    if not row:
        return

    v4_id        = row.get("v4_trade_id")
    hedge_symbol = row.get("hedge_symbol")
    hedge_gtt_id = row.get("hedge_gtt_id")

    # Entry still pending: the SL-only GTT is linked only AFTER the hedge fill
    # is confirmed. No GTT to watch yet — skip (this is the entry-window guard
    # that prevents a false close before protection exists).
    if not hedge_gtt_id:
        return

    # Has the hedge SL-only GTT fired at the broker?
    try:
        gtts = manager.executor.get_gtts() or []
    except Exception as e:
        # Cardinal rule: a fetch error must NEVER close a trade. Retry next cycle.
        write_audit_log(
            f"[V4_RECON][GTT_FETCH_ERR] id={v4_id} gtt={hedge_gtt_id} ERR={e} "
            f"— NOT closing; retry in {RECONCILE_INTERVAL_SEC}s"
        )
        return

    g = next((x for x in gtts if str(x.get("id")) == str(hedge_gtt_id)), None)
    if g is None:
        # GTT not in the list. Could be a propagation lag right after firing, or
        # it was purged post-fill. Do NOT close on absence alone — that's the BB
        # "missing GTT needs position-verify + threshold" lesson. Cross-check the
        # position: only close if the hedge is genuinely flat.
        if _hedge_flat_at_broker(manager, hedge_symbol):
            write_audit_log(
                f"[V4_RECON][GTT_ABSENT_FLAT] id={v4_id} hedge={hedge_symbol} "
                f"gtt={hedge_gtt_id} not in book AND position flat — closing as HEDGE_SL"
            )
            _safe_close(manager, v4_id, hedge_symbol)
        return

    status = (g.get("status") or "").lower()
    if status not in _FIRED_GTT_STATUSES:
        return  # still armed (active) — nothing to do

    # GTT shows fired. Cross-check the hedge is actually flat before closing.
    if not _hedge_flat_at_broker(manager, hedge_symbol):
        write_audit_log(
            f"[V4_RECON][GTT_FIRED_BUT_POS_OPEN] id={v4_id} hedge={hedge_symbol} "
            f"gtt={hedge_gtt_id} status={status} but position still shows open "
            f"— deferring one cycle"
        )
        return

    write_audit_log(
        f"[V4_RECON][HEDGE_SL_DETECTED] id={v4_id} hedge={hedge_symbol} "
        f"gtt={hedge_gtt_id} status={status}, position flat — closing trade, freeing gate"
    )
    _safe_close(manager, v4_id, hedge_symbol)


def _safe_close(manager, v4_id: str, hedge_symbol: str) -> None:
    """
    Call the EXISTING close path. close_hedge_trade is idempotent (guards on
    state != 'OPEN') and its ALREADY_FLAT branch records the exit + frees the
    gate. Wrapped so a close failure can't kill the loop.
    """
    try:
        manager.close_hedge_trade(v4_trade_id=v4_id, exit_reason="HEDGE_SL")
    except Exception as e:
        write_audit_log(
            f"[V4_RECON][CLOSE_ERR] id={v4_id} hedge={hedge_symbol} ERR={e} "
            f"— will retry next cycle"
        )


async def scalp_v4_gtt_reconcile_loop():
    """
    Continuous ~10s poll that detects the hedge SL-only GTT firing in LIVE mode
    and closes the V4 trade so the single-trade gate is freed for the next
    signal. Started as a standalone asyncio task by api_server, next to the
    SCALP_V4 selection loop, under the same enabled + license gate.
    """
    # Yield immediately (Windows asyncio requirement; matches the selection loop).
    await asyncio.sleep(0)
    write_audit_log("[V4_RECON] Hedge-GTT reconciliation loop started (SCALP_V4)")

    while True:
        try:
            _reconcile_once()
        except Exception as e:
            # Belt: _reconcile_once is already fully wrapped, but never let the
            # loop die.
            write_audit_log(f"[V4_RECON][LOOP_ERR] {e!r}")
        await asyncio.sleep(RECONCILE_INTERVAL_SEC)