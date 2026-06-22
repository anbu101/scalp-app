"""
ZERODHA ORDER WEBSOCKET LISTENER
app/execution/zerodha_order_listener.py

CURRENT MODE (unchanged):
  - GTT-only exits, NO SL-M orders, NO order-driven state mutation.
  - This listener does NOT close trades, handle SL, or write trade rows.

WHAT'S NEW:
  - REJECTION ALERTS. Every order postback flows through on_order_update
    (subscribed in zerodha_ws.py). When an order is REJECTED, we resolve which
    strategy placed it (order_id -> strategy via order_strategy_lookup) and
    fire a Telegram order-rejection alert under the channel 'criticalAlerts'
    toggle. If the strategy can't be resolved (order rejected before its row
    was persisted, or a manual order), strategy_id is None and the alert is
    treated as SYSTEM-WIDE (fires to all criticalAlerts-on channels).

  Still NO trade-state mutation here — alerting only.

DEDUP:
  Reuses the existing (order_id, status) processed-set so a repeated postback
  for the same terminal state fires at most once.
"""

from typing import Dict, Set, Tuple

from app.event_bus.audit_logger import write_audit_log

# Idempotency for postbacks (order_id, status) — unchanged behaviour.
_PROCESSED_EVENTS: Set[Tuple[str, str]] = set()

# Broker statuses that represent a rejected order. CANCELLED/LAPSED are NOT
# treated as rejections here — those are normal lifecycle outcomes (GTT cancels,
# unfilled timeouts) that the trade managers already handle; alerting on them
# would be noise. Only REJECTED carries a broker error worth surfacing.
_REJECTION_STATUSES = {"REJECTED"}


def on_order_update(update: Dict):
    """
    Zerodha ORDER WebSocket handler.

    Logging-only for state; additionally emits a Telegram rejection alert on
    REJECTED. Never raises (a notification path must never break the order
    stream).
    """
    order_id = update.get("order_id")
    status = update.get("status")

    if not order_id or not status:
        return

    key = (str(order_id), str(status))
    if key in _PROCESSED_EVENTS:
        return
    _PROCESSED_EVENTS.add(key)

    # Log only — do NOT mutate trade state.
    write_audit_log(f"[ORDER-UPDATE][IGNORED] ORDER_ID={order_id} STATUS={status}")

    # ── REJECTION ALERT (additive; alerting only) ────────────────────
    status_up = str(status).upper()
    if status_up in _REJECTION_STATUSES:
        try:
            _emit_rejection_alert(update)
        except Exception as e:
            # Defensive: never let alerting break the WS callback.
            write_audit_log(f"[ORDER-UPDATE][REJECT_ALERT_ERR] ORDER_ID={order_id} ERR={e}")

    return


def _emit_rejection_alert(update: Dict):
    """
    Resolve the strategy that placed the rejected order and fire the Telegram
    rejection alert. Imports are LAZY so this module stays importable even if
    telegram/db deps shift, and so a cold import error can't crash the WS path.
    """
    order_id = str(update.get("order_id"))
    symbol = (
        update.get("tradingsymbol")
        or update.get("trading_symbol")
        or update.get("symbol")
        or "—"
    )
    status_message = (
        update.get("status_message")
        or update.get("status_message_raw")
        or "Order rejected by broker"
    )

    # order_id -> strategy_id (None if unresolved -> system-wide alert)
    strategy_id = None
    try:
        from app.execution.order_strategy_lookup import find_strategy_by_order_id
        strategy_id = find_strategy_by_order_id(order_id)
    except Exception as e:
        write_audit_log(f"[ORDER-UPDATE][REJECT_LOOKUP_ERR] ORDER_ID={order_id} ERR={e}")

    write_audit_log(
        f"[ORDER-UPDATE][REJECTED] ORDER_ID={order_id} SYMBOL={symbol} "
        f"STRATEGY={strategy_id or 'SYSTEM-WIDE'} MSG={status_message}"
    )

    from app.api.telegram_api import notify_order_rejection
    notify_order_rejection({
        "strategy_id": strategy_id,
        "symbol": symbol,
        "status_message": status_message,
        "order_id": order_id,
    })