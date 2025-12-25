import asyncio

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.brokers.zerodha_auth import get_kite
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection
from app.event_bus.audit_logger import write_audit_log


# =========================
# Constants
# =========================

INDEX_SYMBOL = "NIFTY"
TRADE_MODE = "BOTH"
ATM_RANGE = 800
STRIKE_STEP = 50
RECHECK_INTERVAL = 180  # seconds


# =========================
# Public API (used by UI)
# =========================

def recompute_selection():
    """
    Manual trigger (API / UI).
    """
    return asyncio.run(_compute_and_save_selection())


# =========================
# Background loop
# =========================

async def selection_loop():
    write_audit_log("[ENGINE] Selection engine started")

    while True:
        try:
            await _compute_and_save_selection()
        except Exception as e:
            write_audit_log(f"[ENGINE] ERROR {e}")

        await asyncio.sleep(RECHECK_INTERVAL)


# =========================
# Core logic
# =========================

async def _compute_and_save_selection():
    kite = get_kite()
    if not kite:
        write_audit_log("[ENGINE] Zerodha not connected")
        return None

    cfg = load_strategy_config()
    premium_cfg = cfg.get("option_premium", {})

    instruments = load_nifty_weekly_options(
        api_key=kite.api_key,
        access_token=kite.access_token,
    )

    selector = OptionSelector(
        instruments=instruments,
        price_min=premium_cfg.get("min", 0),
        price_max=premium_cfg.get("max", 1e9),
        trade_mode=TRADE_MODE,
        atm_range=ATM_RANGE,
        strike_step=STRIKE_STEP,
        index_symbol=INDEX_SYMBOL,
    )

    selection = selector.select()
    if not selection:
        write_audit_log("[ENGINE] No options found in premium range")
        return None

    # Expecting 2 CE + 2 PE
    save_selection(selection)

    write_audit_log(
        "[ENGINE] Updated selection: "
        + ", ".join(o["tradingsymbol"] for o in selection)
    )

    return selection
