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
                        underlying: str = "NIFTY",
                        kite=None) -> Dict:
    """Returns {ok, legs, hedged_total, naked_total, benefit, note} or
    {ok: False, error}. `kite` injectable for tests.
    ── GENERIC_LEGS (2026-07-18) ── `legs` = [{side, action, premium_max,
    lots}] prices ANY structure (IC 4-leg, naked shorts, spreads): per leg,
    strike = highest LTP ≤ its cap on its side (BUY legs fall back to the
    cheapest real when nothing ≤ cap — flagged); basket ordered BUY legs
    first; naked = the SELL legs alone (benefit = hedge relief). Omitting
    `legs` keeps the original TMA sell+hedge behavior byte-compatible.
    ── STOCK_MARGIN (2026-08-15) ── `underlying` extends everything to F&O
    stocks: the ladder filters on that name, expiry resolves via the STOCK
    MONTHLY calendar (self-consistency with the GC stock runner), qty uses
    the instrument dump's own lot_size, and a leg may carry
    {"strike_mode": "atm", "atm_offset": N} instead of premium_max —
    moneyness resolution against the live spot LTP (CE offsets step up,
    PE down), matching GC_ATM_SELECT. Defaults keep NIFTY byte-identical."""
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

        from app.backtest.engine.expiry_calendar import (
            expected_expiry_for_day, expected_stock_monthly_expiry_for_day)
        from app.fetcher.zerodha_instruments import load_instruments_df
        und = str(underlying or "NIFTY").upper().strip()
        is_stock = und not in ("NIFTY", "BANKNIFTY")          # ── STOCK_MARGIN ──
        want = (expected_stock_monthly_expiry_for_day(date.today())
                if is_stock else expected_expiry_for_day(date.today()))
        df = load_instruments_df()
        # per-underlying lot from the instrument dump itself (never guessed)
        lot_rows = df[(df["name"] == und) & (df["segment"] == "NFO-OPT")]
        und_lot = int(lot_rows["lot_size"].iloc[0]) if len(lot_rows) else LOT_SIZE
        spot_ltp = None                                        # lazy, atm legs only

        def _ladder(want_side: str) -> List[Tuple[str, float, float]]:
            opt = df[(df["name"] == und) & (df["segment"] == "NFO-OPT")
                     & (df["instrument_type"] == want_side)]
            opt = opt[opt["expiry"].astype(str) == want.isoformat()]
            syms = list(opt["tradingsymbol"])
            strikes = list(opt["strike"])
            if not syms:
                return []
            ltps = kite.ltp([f"NFO:{s}" for s in syms])
            return [(s, float((ltps.get(f"NFO:{s}") or {}).get("last_price") or 0),
                     float(k))
                    for s, k in zip(syms, strikes)]

        def _spot() -> float:                                  # ── STOCK_MARGIN ──
            nonlocal spot_ltp
            if spot_ltp is None:
                key = "NSE:NIFTY 50" if und == "NIFTY" else (
                    "NSE:NIFTY BANK" if und == "BANKNIFTY" else f"NSE:{und}")
                q = kite.ltp([key])
                spot_ltp = float((q.get(key) or {}).get("last_price") or 0)
            return spot_ltp

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
                            "error": f"no {und} {lside} contracts for expiry "
                                     f"{want} — refresh instruments"}
            lad = [x for x in ladders[lside]
                   if x[0] not in {p["symbol"] for p in picked}]
            if str(l.get("strike_mode") or "").lower() == "atm":
                # ── STOCK_MARGIN ── moneyness resolution (GC_ATM_SELECT
                # semantics): anchor nearest live spot, offset OTM-ward.
                # FULL ladder, not the picked-exclusion view — removing an
                # already-picked strike SHIFTS every offset below it (found
                # in test: hedge ATM+3 resolved 13250 instead of 13500).
                sp = _spot()
                full = ladders[lside]
                if not sp or not full:
                    return {"ok": False, "error": f"no live {und} chain/spot "
                                                  f"for expiry {want}"}
                lad2 = sorted(full, key=lambda x: x[2])
                ai = min(range(len(lad2)), key=lambda i: abs(lad2[i][2] - sp))
                off = int(l.get("atm_offset") or 0)
                ti = ai + (off if lside == "CE" else -off)
                if not (0 <= ti < len(lad2)):
                    return {"ok": False, "error": f"ATM{off:+d} {lside} is "
                                                  f"outside the live ladder"}
                pick = (lad2[ti][0], lad2[ti][1])
            else:
                lad_pm = [(x[0], x[1]) for x in lad]
                pick = select_strike(lad_pm, float(l.get("premium_max") or 0))
                if pick is None and str(l.get("action")).upper() == "BUY":
                    pick = select_strike(lad_pm,
                                         float(l.get("premium_max") or 0),
                                         fallback_cheapest=True)
                    note_fb = note_fb or pick is not None
                if pick is None:
                    return {"ok": False,
                            "error": f"no strike ≤ {l.get('premium_max')} for "
                                     f"{l.get('action')} {lside} in the live "
                                     f"chain"}
            picked.append({"symbol": pick[0], "ltp": float(pick[1]),
                           "action": str(l.get("action")).upper(),
                           "side": lside,
                           "qty": int(l["lots"]) * und_lot})

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