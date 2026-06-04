# backend/app/api/scalp_v1_state.py
#
# Dedicated state endpoint for the SCALP_V1 dashboard panel.
#
# WHY THIS EXISTS
#   The generic /trade/state reads ONLY the live trade registry
#   (TradeStateManager._REGISTRY → manager.active_trade). SCALP_V1 paper
#   trades live in the `paper_trades` SQLite table and never touch the
#   registry, so /trade/state shows ARMED for every slot during paper
#   trading — the panel had nothing to render.
#
#   SCALP_V2 and HA already own per-strategy state endpoints; this brings
#   SCALP_V1 in line with that pattern instead of overloading the shared
#   /trade/state (which BB_V1/BB_V2 also read — left untouched).
#
# BEHAVIOUR (live-first, paper-fallback) — matches how _maybe_mtm_squareoff
# in the tick engine resolves SCALP_V1 mode:
#   1. If a slot in _REGISTRY["SCALP_V1"] has an active_trade → emit it
#      (live path; same getattr block as /trade/state, identical shape).
#   2. Otherwise read get_all_open_paper_trades("SCALP_V1") and emit those
#      (paper path).
#
# PAYLOAD SHAPE
#   Same prefixed-dict shape as /trade/state so the frontend's existing
#   prefix-stripping plumbing works unchanged. Each entry carries:
#       state, symbol, buy_price, sl_price, tp_price, qty, tp_hit
#
#   FIELD MAPPING (paper branch):
#     - buy_price  ← paper_trades.entry_price
#         The panel reads slot.buy_price and shows it under the "Entry"
#         label. For this SHORT (option-selling) strategy that value is the
#         SELL/entry price; "buy_price" is purely a legacy transport key,
#         never shown to the user as the word "buy".
#     - state 'OPEN' → 'IN_TRADE' so it lands in the panel's ACTIVE_STATES
#         (["BUY_PLACED","PROTECTED","BUY_FILLED","IN_TRADE"]).
#
# ISOLATION
#   Touches no other strategy. /trade/state, getTradeState, BB, HA, V2 are
#   all unaffected. Read-only — never mutates trade state. Never throws.

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log
from app.db.paper_trades_repo import get_all_open_paper_trades

router = APIRouter(tags=["scalp-v1-state"])

STRATEGY_ID = "SCALP_V1"


def _armed_slot():
    return {
        "state": "ARMED",
        "symbol": None,
        "buy_price": None,
        "sl_price": None,
        "tp_price": None,
        "qty": None,
        "tp_hit": False,
    }


@router.get("/api/scalp_v1/state")
def get_scalp_v1_state():
    """
    SCALP_V1 slot/trade state for the dashboard panel.

    Returns a flat dict keyed "SCALP_V1:<slot|symbol>", same overall shape
    as /trade/state. The panel matches open trades to selection cards by the
    `symbol` field, so the exact key string is not significant — only that
    each open entry carries the correct symbol/prices/state.

    MULTI-STRATEGY SAFE. Read-only. NEVER throws.
    """
    result = {}

    # ------------------------------------------------------------------
    # 1) LIVE PATH — registry slots with an active_trade.
    #    Same getattr block as /trade/state so live SCALP_V1 behaves
    #    identically to today. Symbols seen live are tracked so the paper
    #    fallback never double-emits the same contract.
    # ------------------------------------------------------------------
    live_symbols = set()
    try:
        from app.trading.trade_state_manager import TradeStateManager
        slots = TradeStateManager._REGISTRY.get(STRATEGY_ID, {})

        for slot_name, manager in slots.items():
            key = f"{STRATEGY_ID}:{slot_name}"
            payload = _armed_slot()

            try:
                trade = getattr(manager, "active_trade", None)
                if trade:
                    sym = getattr(trade, "symbol", None)
                    payload.update({
                        "state": getattr(trade, "state", "UNKNOWN"),
                        "symbol": sym,
                        "buy_price": getattr(trade, "buy_price", None),
                        "sl_price": getattr(trade, "sl_price", None),
                        "tp_price": getattr(trade, "tp_price", None),
                        "qty": getattr(trade, "qty", None),
                        "tp_hit": getattr(trade, "tp_triggered", False),
                    })
                    if sym:
                        live_symbols.add(sym)
            except Exception as e:
                write_audit_log(
                    f"[SCALP_V1_STATE][WARN] live slot read failed "
                    f"SLOT={key} ERR={e}"
                )

            result[key] = payload

    except Exception as e:
        write_audit_log(
            f"[SCALP_V1_STATE][WARN] registry access failed: {e}"
        )

    # ------------------------------------------------------------------
    # 2) PAPER FALLBACK — open rows from the paper_trades table.
    #    Emitted under "SCALP_V1:<symbol>" keys. The panel keys by symbol,
    #    so these slot into the right CE_1/CE_2/PE_1/PE_2 card. Any symbol
    #    already emitted live is skipped (live wins).
    # ------------------------------------------------------------------
    try:
        open_rows = get_all_open_paper_trades(STRATEGY_ID)
    except Exception as e:
        write_audit_log(
            f"[SCALP_V1_STATE][WARN] paper read failed: {e}"
        )
        open_rows = []

    for row in open_rows:
        try:
            sym = row.get("symbol")
            if not sym or sym in live_symbols:
                continue

            result[f"{STRATEGY_ID}:{sym}"] = {
                # OPEN → IN_TRADE so the panel's ACTIVE_STATES matches.
                "state": "IN_TRADE",
                "symbol": sym,
                # entry_price exposed under the legacy transport key buy_price;
                # shown as "Entry". For SHORT this is the sell/entry price.
                "buy_price": row.get("entry_price"),
                "sl_price": row.get("sl_price"),
                "tp_price": row.get("tp_price"),
                "qty": row.get("qty"),
                "tp_hit": False,
            }
        except Exception as e:
            write_audit_log(
                f"[SCALP_V1_STATE][WARN] paper row map failed "
                f"row={row!r} ERR={e}"
            )

    return result