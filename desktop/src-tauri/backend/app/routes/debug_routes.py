# routes/debug_routes.py
from fastapi import APIRouter, HTTPException
from typing import Any, Dict

from datastore import DataStore
from engine.strategy_v2 import StrategyV2
from brokers.mock_broker import MockBroker

router = APIRouter(prefix="/api", tags=["debug"])

# NOTE: we create local instances here that mirror your main app singletons.
# If you already have shared singletons (store/strategy/broker) exported somewhere,
# you can import those instead of re-instantiating.
store = DataStore("trades.db")
strategy = StrategyV2()
broker = MockBroker(store)

@router.get("/eval_current", response_model=Dict[str, Any])
async def eval_current():
    """
    Re-evaluate currently selected CE/PE strikes using StrategyV2 and return
    detailed debug information (candles, strategy output, candidate SL/TP).
    """
    cfg = store.get_config() or {}
    sel = cfg.get("selected_strikes") or {}
    if not isinstance(sel, dict):
        sel = {}

    ce_entry = sel.get("ce") or {}
    pe_entry = sel.get("pe") or {}
    ce_sym = ce_entry.get("symbol") if isinstance(ce_entry, dict) else None
    pe_sym = pe_entry.get("symbol") if isinstance(pe_entry, dict) else None

    if not ce_sym and not pe_sym:
        raise HTTPException(status_code=404, detail="No selected_strikes configured (ce or pe missing)")

    # how many candles to request for debugging
    lookback = int(cfg.get("debug_lookback", 200))

    def _fetch(sym):
        try:
            candles = store.get_recent_candles(sym, lookback) or []
            # reduce size returned to keep payload small (last 100)
            sample = candles[-100:] if len(candles) > 100 else candles
            return sample
        except Exception as e:
            return {"error": f"fetch_error: {str(e)}"}

    out = {}

    # Evaluate CE
    if ce_sym:
        ce_candles = _fetch(ce_sym)
        if isinstance(ce_candles, dict) and ce_candles.get("error"):
            out["CE"] = {"symbol": ce_sym, "error": ce_candles["error"]}
        else:
            try:
                res_ce = strategy.evaluate(ce_candles, config=cfg)
            except Exception as e:
                res_ce = {"error": f"strategy.evaluate error: {str(e)}"}

            detail = {"symbol": ce_sym, "candles_count": len(ce_candles), "candles_sample": ce_candles, "strategy_result": res_ce}

            # compute candidate SL & TP if buy signalled
            if isinstance(res_ce, dict) and res_ce.get("buy") and res_ce.get("buyPrice") is not None:
                try:
                    candidate_sl = strategy.find_candidate_sl(ce_candles, res_ce["buyPrice"], cfg)
                    tp = strategy.compute_tp(res_ce["buyPrice"], candidate_sl, cfg)
                    detail["candidate_sl"] = candidate_sl
                    detail["computed_tp"] = tp
                except Exception as e:
                    detail["candidate_sl_error"] = str(e)

            out["CE"] = detail

    # Evaluate PE
    if pe_sym:
        pe_candles = _fetch(pe_sym)
        if isinstance(pe_candles, dict) and pe_candles.get("error"):
            out["PE"] = {"symbol": pe_sym, "error": pe_candles["error"]}
        else:
            try:
                res_pe = strategy.evaluate(pe_candles, config=cfg)
            except Exception as e:
                res_pe = {"error": f"strategy.evaluate error: {str(e)}"}

            detail = {"symbol": pe_sym, "candles_count": len(pe_candles), "candles_sample": pe_candles, "strategy_result": res_pe}

            if isinstance(res_pe, dict) and res_pe.get("buy") and res_pe.get("buyPrice") is not None:
                try:
                    candidate_sl = strategy.find_candidate_sl(pe_candles, res_pe["buyPrice"], cfg)
                    tp = strategy.compute_tp(res_pe["buyPrice"], candidate_sl, cfg)
                    detail["candidate_sl"] = candidate_sl
                    detail["computed_tp"] = tp
                except Exception as e:
                    detail["candidate_sl_error"] = str(e)

            out["PE"] = detail

    return out
