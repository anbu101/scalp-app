# backend/app/backtest/tma/margin_estimate.py
#
# ── TMA_MARGIN_ESTIMATE ── live "what would this spread block TODAY"
# figure for the Backtest page, via Zerodha's basket margin API (the engine
# behind Kite's Basket calculator). HONESTY CONTRACT: this is a PRESENT-DAY
# proxy — SPAN is point-in-time, so it ranks configs by return-on-margin
# fairly but is NOT the historical average requirement; high-vol periods in
# a backtest window would have blocked more. Strike selection reuses the
# BACKTEST's own rules (highest premium ≤ cap; hedge same side deeper OTM)
# on the CURRENT weekly chain, era-aware expiry. Read-only: quotes + margin
# preview only, no orders are placed. Needs a valid Kite session.

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

LOT_SIZE = 65


def pick_margin_legs(ladder: List[Tuple[str, float]], sell_cap: float,
                     buy_cap: float) -> Optional[Dict]:
    """ladder = [(tradingsymbol, ltp)] one side of the current weekly chain.
    Sell = highest LTP ≤ sell_cap; hedge = highest LTP ≤ buy_cap among the
    remaining strikes (cheapest real if none — flagged). Pure + tested."""
    from app.backtest.ic.ic_v1_engine import select_strike
    live = [(s, p) for s, p in ladder if p and p > 0]
    sell = select_strike(live, sell_cap)
    if sell is None:
        return None
    rest = [(s, p) for s, p in live if s != sell[0]]
    hedge = select_strike(rest, buy_cap)
    fb = False
    if hedge is None:
        hedge = select_strike(rest, buy_cap, fallback_cheapest=True)
        fb = hedge is not None
    if hedge is None:
        return None
    return {"sell_symbol": sell[0], "sell_ltp": float(sell[1]),
            "buy_symbol": hedge[0], "buy_ltp": float(hedge[1]),
            "hedge_fallback": fb}


def _order(symbol: str, txn: str, qty: int) -> dict:
    return {"exchange": "NFO", "tradingsymbol": symbol,
            "transaction_type": txn, "variety": "regular",
            "product": "NRML", "order_type": "MARKET",
            "quantity": qty, "price": 0, "trigger_price": 0}


def estimate_tma_margin(*, sell_cap: float = 0, buy_cap: float = 0,
                        sell_lots: int = 0, buy_lots: int = 0,
                        side: str = "PE", legs: Optional[List[dict]] = None,
                        kite=None) -> Dict:
    """Returns {ok, legs, hedged_total, naked_total, benefit, note} or
    {ok: False, error}. `kite` injectable for tests.
    ── GENERIC_LEGS (2026-07-18) ── `legs` = [{side, action, premium_max,
    lots}] prices ANY structure (IC 4-leg, naked shorts, spreads): per leg,
    strike = highest LTP ≤ its cap on its side (BUY legs fall back to the
    cheapest real when nothing ≤ cap — flagged); basket ordered BUY legs
    first; naked = the SELL legs alone (benefit = hedge relief). Omitting
    `legs` keeps the original TMA sell+hedge behavior byte-compatible."""
    try:
        if kite is None:
            from kiteconnect import KiteConnect
            from app.brokers.zerodha_manager import (
                load_access_token, load_credentials)
            api_key = (load_credentials() or {}).get("api_key")
            token = load_access_token()
            if not api_key or not token:
                return {"ok": False,
                        "error": "Zerodha session not available — log in first"}
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(token)

        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
        from app.fetcher.zerodha_instruments import load_instruments_df
        want = expected_expiry_for_day(date.today())
        df = load_instruments_df()

        def _ladder(want_side: str) -> List[Tuple[str, float]]:
            opt = df[(df["name"] == "NIFTY") & (df["segment"] == "NFO-OPT")
                     & (df["instrument_type"] == want_side)]
            opt = opt[opt["expiry"].astype(str) == want.isoformat()]
            syms = list(opt["tradingsymbol"])
            if not syms:
                return []
            ltps = kite.ltp([f"NFO:{s}" for s in syms])
            return [(s, float((ltps.get(f"NFO:{s}") or {}).get("last_price") or 0))
                    for s in syms]

        # ── GENERIC_LEGS ── normalize the request into leg specs
        if legs is None:
            legs = [{"side": side, "action": "SELL",
                     "premium_max": sell_cap, "lots": sell_lots},
                    {"side": side, "action": "BUY",
                     "premium_max": buy_cap, "lots": buy_lots}]
        legs = [l for l in legs if int(l.get("lots") or 0) > 0]
        if not legs:
            return {"ok": False, "error": "no legs with lots > 0"}

        from app.backtest.ic.ic_v1_engine import select_strike
        ladders: Dict[str, list] = {}
        picked, note_fb = [], False
        for l in legs:
            lside = str(l.get("side") or "PE").upper()
            if lside not in ladders:
                ladders[lside] = _ladder(lside)
                if not ladders[lside]:
                    return {"ok": False,
                            "error": f"no NIFTY {lside} contracts for expiry "
                                     f"{want} — refresh instruments"}
            lad = [x for x in ladders[lside]
                   if x[0] not in {p["symbol"] for p in picked}]
            pick = select_strike(lad, float(l.get("premium_max") or 0))
            if pick is None and str(l.get("action")).upper() == "BUY":
                pick = select_strike(lad, float(l.get("premium_max") or 0),
                                     fallback_cheapest=True)
                note_fb = note_fb or pick is not None
            if pick is None:
                return {"ok": False,
                        "error": f"no strike ≤ {l.get('premium_max')} for "
                                 f"{l.get('action')} {lside} in the live chain"}
            picked.append({"symbol": pick[0], "ltp": float(pick[1]),
                           "action": str(l.get("action")).upper(),
                           "side": lside,
                           "qty": int(l["lots"]) * LOT_SIZE})

        # BUY legs FIRST — the live sequencing that earns the spread benefit
        picked.sort(key=lambda x: 0 if x["action"] == "BUY" else 1)
        basket = [_order(x["symbol"], x["action"], x["qty"]) for x in picked]
        hedged = kite.basket_order_margins(basket, consider_positions=False)
        shorts = [x for x in picked if x["action"] == "SELL"]
        naked = kite.basket_order_margins(
            [_order(x["symbol"], "SELL", x["qty"]) for x in shorts],
            consider_positions=False) if shorts else {"final": {"total": 0}}
        first_sell = next((x for x in picked if x["action"] == "SELL"), picked[0])
        first_buy = next((x for x in picked if x["action"] == "BUY"), None)
        legs_out = {"sell_symbol": first_sell["symbol"],
                    "sell_ltp": first_sell["ltp"],
                    "buy_symbol": first_buy["symbol"] if first_buy else None,
                    "buy_ltp": first_buy["ltp"] if first_buy else None,
                    "hedge_fallback": note_fb,
                    "all": picked}

        def _tot(resp) -> float:
            fin = (resp or {}).get("final") or {}
            return float(fin.get("total") or 0)
        h, n = _tot(hedged), _tot(naked)
        return {"ok": True, "legs": legs_out, "expiry": want.isoformat(),
                "side": side, "hedged_total": round(h, 2),
                "naked_total": round(n, 2),
                "benefit": round(max(0.0, n - h), 2),
                "note": ("hedge had no strike ≤ cap; cheapest real used"
                         if note_fb else None)}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "error": f"margin estimate failed: {e}"}