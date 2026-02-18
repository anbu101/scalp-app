from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.brokers.zerodha_auth import get_kite
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.trading.slot_executor import SlotExecutor
from app.event_bus.audit_logger import write_audit_log

import traceback
import sys


# --------------------------------------------------
# Constants
# --------------------------------------------------

STRATEGY_ID = "SCALP_V1"

INDEX_SYMBOL = "NIFTY"
TRADE_MODE = "BOTH"
ATM_RANGE = 400
STRIKE_STEP = 50


# --------------------------------------------------
# Global exception hook
# --------------------------------------------------

def excepthook(type, value, tb):
    traceback.print_tb(tb)
    print(type, value)

sys.excepthook = excepthook


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    print("[MAIN] Starting infra (DRY MODE)")

    # --------------------------------------------------
    # Zerodha connection
    # --------------------------------------------------
    kite = get_kite()
    profile = kite.profile()
    print(f"[ZERODHA] Connected as {profile['user_name']}")

    # --------------------------------------------------
    # Load strategy config (STRATEGY-SCOPED)
    # --------------------------------------------------
    cfg = load_strategy_config(STRATEGY_ID)

    premium_cfg = cfg.get("option_premium", {})
    price_min = premium_cfg.get("min", 0)
    price_max = premium_cfg.get("max", 1e9)

    print(f"[CONFIG] Premium range {price_min} → {price_max}")

    # --------------------------------------------------
    # Load weekly NIFTY options
    # --------------------------------------------------
    instruments = load_nifty_weekly_options(
        api_key=kite.api_key,
        access_token=kite.access_token,
    )

    if not instruments:
        raise RuntimeError("No Zerodha NIFTY options loaded")

    print(f"[MAIN] Zerodha NIFTY options loaded: {len(instruments)}")

    # --------------------------------------------------
    # Select up to 2 CE + 2 PE
    # --------------------------------------------------
    selector = OptionSelector(
        instruments=instruments,
        price_min=price_min,
        price_max=price_max,
        trade_mode=TRADE_MODE,
        atm_range=ATM_RANGE,
        strike_step=STRIKE_STEP,
        index_symbol=INDEX_SYMBOL,
    )

    selection = selector.select()
    if not selection:
        raise RuntimeError("No options selected")

    ce_list = selection.get("CE", [])
    pe_list = selection.get("PE", [])

    if not ce_list and not pe_list:
        raise RuntimeError("No CE or PE available after filtering")

    print(f"[SELECTED] CE count = {len(ce_list)}")
    for ce in ce_list:
        print("[SELECTED][CE]", ce)

    print(f"[SELECTED] PE count = {len(pe_list)}")
    for pe in pe_list:
        print("[SELECTED][PE]", pe)

    # --------------------------------------------------
    # Persist selection
    # --------------------------------------------------
    all_selected = ce_list + pe_list
    save_selection(all_selected)

    # --------------------------------------------------
    # Bind selections to slots (DRY MODE)
    # --------------------------------------------------
    executor = ZerodhaOrderExecutor(
        api_key=kite.api_key,
        api_secret=None,
        dry_run=True,
    )

    slot_executor = SlotExecutor(executor)
    slot_executor.bind_from_saved_selection()

    print("[MAIN] Slots bound and ready (DRY MODE)")


if __name__ == "__main__":
    main()
