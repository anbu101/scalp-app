from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.brokers.zerodha_auth import get_kite
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection


INDEX_SYMBOL = "NIFTY"
ATM_RANGE = 800
STRIKE_STEP = 50


def recompute_selection(strategy_id: str):
    """
    Recomputes option selection for a specific strategy.
    """

    kite = get_kite()

    # 🔒 Strategy-specific config
    cfg = load_strategy_config(strategy_id)

    premium_cfg = cfg.get("option_premium", {})
    price_min = premium_cfg.get("min", 0)
    price_max = premium_cfg.get("max", 1e9)

    trade_mode = cfg.get("trade_side_mode", "BOTH")

    instruments = load_nifty_weekly_options(
        api_key=kite.api_key,
        access_token=kite.access_token,
    )

    if not instruments:
        raise RuntimeError("No instruments loaded from Zerodha")

    selector = OptionSelector(
        instruments=instruments,
        price_min=price_min,
        price_max=price_max,
        trade_mode=trade_mode,
        atm_range=ATM_RANGE,
        strike_step=STRIKE_STEP,
        index_symbol=INDEX_SYMBOL,
    )

    selection = selector.select()

    if not selection:
        raise RuntimeError("No options found in premium range")

    # 🔒 Persist full selection list (2 CE + 2 PE)
    save_selection(selection)

    return selection
