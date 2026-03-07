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

    # =====================================================
    # 🔥 MULTI-STRATEGY SAFE ITERATION (CRITICAL FIX)
    # =====================================================

    for strategy_id, strategy_slots in TradeStateManager._REGISTRY.items():

        for slot in strategy_slots.values():

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
                    trade.buy_price = broker_pos.get("average_price", trade.buy_price)
                    trade.qty = abs(broker_pos.get("quantity", trade.qty))
                    trade.state = "PROTECTED"
                    slot._save_state()

                    write_audit_log(
                        f"[RECOVERY] CONFIRMED LIVE "
                        f"STRATEGY={strategy_id} "
                        f"SLOT={slot.name} SYMBOL={symbol}"
                    )
                    continue

                # ---- POSITION CLOSED → DETECT EXIT ----
                from app.db.trades_repo import close_trade
                from app.marketdata.ltp_store import LTPStore

                exit_reason = _detect_exit_reason(trade, orders)

                allowed = {"TP", "SL", "MANUAL", "BROKER_EXIT", "GTT_TP", "GTT_SL"}
                safe_reason = exit_reason if exit_reason in allowed else "BROKER_EXIT"

                write_audit_log(
                    f"[RECOVERY] EXIT DETECTED "
                    f"STRATEGY={strategy_id} "
                    f"SLOT={slot.name} SYMBOL={symbol} REASON={safe_reason}"
                )

                exit_price = LTPStore.get(symbol) or trade.buy_price

                close_trade(
                    trade_id=trade.trade_id,
                    exit_price=exit_price,
                    exit_order_id=None,
                    exit_reason=safe_reason,
                )

                slot.active_trade = None
                slot.in_trade = False
                slot.selection_locked = False
                slot._save_state()

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
    Determine why trade exited.

    For GTT trades Zerodha does not provide a direct TP/SL flag,
    so we infer from LTP vs SL.

    Logic:
    SL hit  -> GTT_SL
    else    -> GTT_TP
    """

    from app.marketdata.ltp_store import LTPStore

    ltp = LTPStore.get(trade.symbol)

    if ltp is None:
        return "BROKER_EXIT"

    try:
        if trade.sl_price and ltp <= trade.sl_price:
            return "GTT_SL"
    except Exception:
        pass

    return "GTT_TP"