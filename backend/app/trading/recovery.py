from app.trading.trade_state_manager import TradeStateManager, Trade
from app.event_bus.audit_logger import write_audit_log
from app.brokers.zerodha_manager import ZerodhaManager


def recover_trades_from_zerodha():
    """
    Rebuild TradeStateManager state from Zerodha broker truth.

    HARD GUARANTEES (WHEN BROKER READY):
    - Slot state == broker reality
    - No ghost trades
    - No FIFO assumptions
    - Correct SL / TP / MANUAL exit detection

    SAFE BEHAVIOR (WHEN BROKER NOT READY):
    - No broker calls
    - No slot reset
    - No error logs
    """

    zerodha = ZerodhaManager()

    # --------------------------------
    # BROKER NOT READY → SKIP CLEANLY
    # --------------------------------
    if not zerodha.is_ready():
        write_audit_log(
            "[RECOVERY] Broker not ready → skipping recovery"
        )
        return

    kite = zerodha.get_kite()

    try:
        positions = kite.positions().get("net", [])
        orders = kite.orders()
    except Exception as e:
        write_audit_log(
            f"[RECOVERY] Broker fetch failed → skipping recovery ({e})"
        )
        return

    # -------------------------
    # LIVE POSITIONS MAP
    # -------------------------
    live_positions = {
        p["tradingsymbol"]: p
        for p in positions
        if p.get("quantity", 0) != 0 and p.get("exchange") == "NFO"
    }

    write_audit_log(
        f"[RECOVERY] Live broker positions: {list(live_positions.keys())}"
    )

    # -------------------------
    # PROCESS EACH SLOT
    # -------------------------
    for slot in TradeStateManager._REGISTRY.values():
        trade = slot.active_trade

        # --------------------------------
        # SLOT HAS ACTIVE TRADE
        # --------------------------------
        if trade:
            symbol = trade.symbol
            broker_pos = live_positions.get(symbol)

            # ---- POSITION STILL OPEN ----
            if broker_pos:
                slot.in_trade = True
                slot.selection_locked = True
                trade.buy_price = broker_pos["average_price"]
                trade.qty = abs(broker_pos["quantity"])
                trade.state = "BUY_FILLED"
                slot._save_state()

                write_audit_log(
                    f"[RECOVERY] CONFIRMED LIVE "
                    f"SLOT={slot.name} SYMBOL={symbol}"
                )
                continue

            # ---- POSITION CLOSED → DETECT EXIT ----
            exit_reason = _detect_exit_reason(trade, orders)

            write_audit_log(
                f"[RECOVERY] EXIT DETECTED "
                f"SLOT={slot.name} SYMBOL={symbol} REASON={exit_reason}"
            )

            slot._close_trade(exit_reason)
            continue

        # --------------------------------
        # SLOT EMPTY → CLEAN STATE
        # --------------------------------
        slot.in_trade = False
        slot.selection_locked = False
        slot._save_state()

    write_audit_log("[RECOVERY] COMPLETE")


# =========================
# EXIT DETECTION
# =========================

def _detect_exit_reason(trade: Trade, orders: list) -> str:
    """
    Determine why trade exited using broker orders.
    Priority:
      SL → TP → MANUAL → BROKER_EXIT
    """

    # ---- SL HIT ----
    if trade.sl_order_id:
        for o in orders:
            if (
                o["order_id"] == trade.sl_order_id
                and o["status"] == "COMPLETE"
            ):
                return "SL"

    # ---- TP / MANUAL EXIT ----
    if trade.exit_order_id:
        for o in orders:
            if (
                o["order_id"] == trade.exit_order_id
                and o["status"] == "COMPLETE"
            ):
                return trade.exit_reason or "TP"

    # ---- FALLBACK ----
    return "BROKER_EXIT"
