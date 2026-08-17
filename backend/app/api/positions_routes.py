from fastapi import APIRouter
from app.brokers.zerodha_auth import get_kite
from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["positions"])


@router.get("/positions/today")
def positions_today():
    kite = get_kite()
    if not kite:
        return _empty_response()

    try:
        positions = kite.positions().get("net", [])
    except Exception:
        return _empty_response()

    open_pos = []
    closed_pos = []

    realised   = 0.0
    unrealised = 0.0

    # --------------------------------------------------
    # POS_DECOMP BEGIN
    # A single Kite net row can carry BOTH a realised leg (matched buy/sell
    # qty closed earlier today) AND an open leg (current non-zero quantity)
    # for the SAME tradingsymbol — happens when a strike is closed and then
    # re-entered the same day. We split every row into realised + open parts.
    #
    #   matched_qty  = min(day_buy_quantity, day_sell_quantity)   -> closed leg
    #   open_qty     = quantity (signed)                          -> open leg
    #
    # Realised is computed from SETTLED day prices (not Kite's cached
    # `unrealised` field, which can be stale):
    #
    #   realised = matched_qty * (day_sell_price - day_buy_price) * multiplier
    #
    # `multiplier` comes from the row (NFO options = 1, but never assume —
    # read it). A symbol may legitimately appear in BOTH closed_pos and
    # open_pos.
    # --------------------------------------------------
    raw_open   = []   # rows with a live open leg (quantity != 0)
    raw_closed = []   # synthetic realised-leg rows (one per symbol w/ matched qty)

    for p in positions:
        p = dict(p)

        day_buy_qty   = int(p.get("day_buy_quantity")  or 0)
        day_sell_qty  = int(p.get("day_sell_quantity") or 0)
        day_buy_price  = float(p.get("day_buy_price")  or 0)
        day_sell_price = float(p.get("day_sell_price") or 0)
        qty           = int(p.get("quantity") or 0)
        multiplier    = float(p.get("multiplier") or 1)
        matched_qty   = min(day_buy_qty, day_sell_qty)

        # ---- realised leg (closed portion) ----
        if matched_qty > 0:
            realised_leg = round(
                matched_qty * (day_sell_price - day_buy_price) * multiplier, 2
            )

            closed_row = dict(p)
            # PositionRow renders day_buy_quantity as the closed-row qty;
            # show the matched (closed) size, not gross buys.
            closed_row["day_buy_quantity"] = matched_qty
            closed_row["realised"] = realised_leg
            closed_row["pnl"]      = realised_leg
            raw_closed.append(closed_row)

        elif qty == 0 and (day_buy_qty > 0 or day_sell_qty > 0):
            # Fully-closed row where matched_qty is 0 only if one side is 0,
            # which shouldn't reach here, but guard anyway: fall back to Kite's
            # realised/pnl so a fully-closed symbol is never dropped.
            realised_leg = round(float(p.get("realised") or p.get("pnl") or 0), 2)
            closed_row = dict(p)
            closed_row["day_buy_quantity"] = day_buy_qty or day_sell_qty
            closed_row["realised"] = realised_leg
            closed_row["pnl"]      = realised_leg
            raw_closed.append(closed_row)

        # ---- open leg (live portion) ----
        if qty != 0:
            raw_open.append(p)
    # POS_DECOMP END

    # --------------------------------------------------
    # Fetch live LTP for ALL open positions in one call.
    #
    # WHY:
    #   Zerodha's positions() response contains an `unrealised` field that
    #   is computed on their servers and cached — it does NOT update on
    #   every API call. kite.ltp() is always real-time.
    # --------------------------------------------------
    live_ltps: dict = {}

    if raw_open:
        symbols = [f"NFO:{p['tradingsymbol']}" for p in raw_open]

        for i in range(0, len(symbols), 50):
            batch = symbols[i:i + 50]
            try:
                ltp_data = kite.ltp(batch)
                for full_sym, data in ltp_data.items():
                    plain = full_sym.split(":", 1)[-1]
                    price = data.get("last_price")
                    if price and price > 0:
                        live_ltps[plain] = float(price)
            except Exception as e:
                write_audit_log(f"[POSITIONS] kite.ltp() failed: {e}")

    # --------------------------------------------------
    # Build open positions with live P&L
    #
    # IMPORTANT: avg_price here is `average_price` = the avg of the CURRENT
    # open leg only (Kite recomputes it after a square-off+reopen), so
    # (ltp - avg_price) * qty is the unrealised of the open leg ONLY and does
    # not double-count the realised leg already booked in closed_pos.
    # --------------------------------------------------
    for p in raw_open:
        sym       = p["tradingsymbol"]
        avg_price = float(p.get("average_price") or 0)
        qty       = int(p["quantity"])
        ltp       = live_ltps.get(sym)

        if ltp is not None and ltp > 0:
            live_pnl = round((ltp - avg_price) * qty, 2)
        else:
            live_pnl = float(p.get("unrealised") or p.get("pnl") or 0)
            write_audit_log(
                f"[POSITIONS] No live LTP for {sym} — falling back to cached pnl"
            )

        p["pnl"]        = live_pnl
        p["unrealised"] = live_pnl
        p["ltp"]        = ltp if ltp else p.get("last_price")

        slot      = _map_position_to_slot(p)
        p["slot"]    = slot
        p["managed"] = slot is not None
        p["account"] = "ZERODHA"   # ACC2_D9

        open_pos.append(p)
        unrealised += live_pnl

    # --------------------------------------------------
    # Build closed positions (realised P&L is final — no LTP needed)
    # --------------------------------------------------
    for p in raw_closed:
        p["managed"] = False
        p["account"] = "ZERODHA"   # ACC2_D9
        closed_pos.append(p)
        realised += float(p.get("realised") or p.get("pnl") or 0)

    return {
        "open": open_pos,
        "closed": closed_pos,
        "totals": {
            "realised":   round(realised,   2),
            "unrealised": round(unrealised, 2),
            "total":      round(realised + unrealised, 2),
        },
        "slots": _compute_slot_health(),
        # ============================================================
        # ACC2_D9 BEGIN — account visibility for the dashboard.
        # W3 will merge normalized Angel getPosition rows here (tagged
        # account="ANGELONE") and set partial=True when a configured,
        # positions-supported Angel account cannot be read.
        # ============================================================
        "accounts": _acc2_account_status(),
        "partial": _merge_angel_positions(open_pos, closed_pos),
        # ACC2_D9 END
    }


# ============================================================
# ACC2_W3 BEGIN — Angel branch of the aggregator.
# Inert while ANGEL_POSITIONS_SUPPORTED is False. When on:
#   - open rows (netqty != 0) are normalized into the Kite row shape
#     the frontend reads (tradingsymbol / quantity / average_price /
#     pnl / account) with pnl priced the same open-leg way;
#   - realised/closed legs are NOT merged until the W2 probe confirms
#     Angel's day-buy/sell field equivalents (POS_DECOMP question) —
#     partial realised data would corrupt the totals silently.
# Returns True (-> "partial") when a configured, supported Angel
# account could not be read: fail-visible, never silently short.
# ============================================================

def _merge_angel_positions(open_pos: list, closed_pos: list) -> bool:
    if not ANGEL_POSITIONS_SUPPORTED:
        return False
    try:
        from app.config.angel_credentials_store import load_credentials
        if load_credentials() is None:
            return False
        from app.execution.executor_factory import _get_angel_executor
        rows = _get_angel_executor().get_open_positions()
    except Exception as e:
        write_audit_log(f"[POSITIONS][ACC2][WARN] Angel read failed ERR={e}")
        return True  # configured + supported + unreadable -> partial

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    for r in rows:
        qty = 0
        for k in _ANGEL_QTY_KEYS:
            if k in r:
                qty = int(_f(r[k]))
                break
        if qty == 0:
            continue
        avg = 0.0
        for k in _ANGEL_AVG_KEYS:
            if k in r and _f(r[k]) > 0:
                avg = _f(r[k])
                break
        sym = str(r.get("tradingsymbol") or "")
        ltp = _f(r.get("ltp"))
        pnl = round((ltp - avg) * qty, 2) if (ltp > 0 and avg > 0) else 0.0
        open_pos.append({
            "tradingsymbol": sym, "quantity": qty,
            "average_price": avg, "ltp": ltp or None,
            "pnl": pnl, "unrealised": pnl,
            "slot": None, "managed": False,
            "account": "ANGELONE",
        })
    return False
# ACC2_W3 END


# --------------------------------------------------
# Helpers
# --------------------------------------------------

# ============================================================
# ACC2_D9 BEGIN — per-account status block for the dashboard.
# positions_supported stays False until the W2 order-path probe
# verifies Angel getPosition; until then Angel can hold no LIVE
# positions, so `partial` never fires and totals stay honest.
# Lazy imports + broad except: this READ route must never break
# because ACC2 machinery is absent or unconfigured.
# ============================================================

ANGEL_POSITIONS_SUPPORTED = True  # flip to True after the W2 probe
# ── ACC2_W3 ── field-name candidates for Angel position rows; the W2
# probe's T5 output confirms/corrects these before the flag is flipped.
_ANGEL_QTY_KEYS = ("netqty", "netquantity")
_ANGEL_AVG_KEYS = ("netprice", "avgnetprice", "buyavgprice", "netvalue_avg")


def _acc2_account_status() -> dict:
    zerodha_ok = True  # caller only reaches here with a live kite session
    angel = {"configured": False, "connected": False,
             "positions_supported": ANGEL_POSITIONS_SUPPORTED}
    try:
        from app.config.angel_credentials_store import load_credentials
        angel["configured"] = load_credentials() is not None
        if angel["configured"]:
            from app.execution.executor_factory import get_angel_manager
            angel["connected"] = get_angel_manager().is_trade_ready()
    except Exception:
        pass
    return {"zerodha": {"ok": zerodha_ok}, "angelone": angel}
# ACC2_D9 END


def _empty_response():
    return {
        "open": [],
        "closed": [],
        "totals": {
            "realised":   0.0,
            "unrealised": 0.0,
            "total":      0.0,
        },
        "slots": {},
        "accounts": {"zerodha": {"ok": False},                     # ACC2_D9
                     "angelone": {"configured": False,
                                  "connected": False,
                                  "positions_supported": False}},
        "partial": False,                                          # ACC2_D9
    }


def _map_position_to_slot(position: dict):
    """
    Read-only mapping: broker position -> TradeStateManager slot
    MULTI-STRATEGY SAFE
    """
    symbol = position.get("tradingsymbol")
    qty    = abs(position.get("quantity", 0))

    for strategy_slots in TradeStateManager._REGISTRY.values():
        for name, mgr in strategy_slots.items():
            trade = mgr.active_trade
            if not trade:
                continue
            if trade.symbol == symbol and trade.qty == qty:
                return f"{mgr.strategy_id}:{name}"

    return None


def _compute_slot_health():
    """
    UI-only reconciliation view.
    MULTI-STRATEGY SAFE
    """
    health = {}

    for strategy_id, strategy_slots in TradeStateManager._REGISTRY.items():
        for slot_name, mgr in strategy_slots.items():
            trade = mgr.active_trade
            key   = f"{strategy_id}:{slot_name}"
            health[key] = trade.state if trade else "ARMED"

    return health