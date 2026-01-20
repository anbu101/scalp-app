from fastapi import APIRouter
from app.marketdata.market_indices_state import MarketIndicesState

router = APIRouter()

@router.get("/market_indices")
def get_market_indices():
    """
    Ultra-fast snapshot of market indices.
    Pure in-memory read.
    SAFE to poll frequently (UI polls every ~500ms).
    """

    snap = MarketIndicesState.snapshot()

    # --------------------------------------------------
    # 🔒 UI CONTRACT
    # Always return all indices with stable keys
    # --------------------------------------------------
    return {
        "NIFTY": snap.get("NIFTY", {
            "ltp": None,
            "prev_close": None,
            "change": None,
            "change_pct": None,
        }),
        "BANKNIFTY": snap.get("BANKNIFTY", {
            "ltp": None,
            "prev_close": None,
            "change": None,
            "change_pct": None,
        }),
        "SENSEX": snap.get("SENSEX", {
            "ltp": None,
            "prev_close": None,
            "change": None,
            "change_pct": None,
        }),
    }
