import time
from threading import Thread

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.brokers.zerodha_auth import get_kite
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection
from app.trading.trade_registry import is_in_trade


INDEX_SYMBOL = "NIFTY"
TRADE_MODE = "BOTH"
ATM_RANGE = 800
STRIKE_STEP = 50
RECHECK_INTERVAL = 180  # 3 minutes


def selection_loop():
    print("[SELECTION] Scheduler started (3-minute loop)")

    kite = get_kite()

    while True:
        try:
            cfg = load_strategy_config()
            premium_cfg = cfg.get("option_premium", {})
            price_min = premium_cfg.get("min", 0)
            price_max = premium_cfg.get("max", 1e9)

            instruments = load_nifty_weekly_options(
                api_key=kite.api_key,
                access_token=kite.access_token,
            )

            selector = OptionSelector(
                instruments=instruments,
                price_min=price_min,
                price_max=price_max,
                trade_mode=TRADE_MODE,
                atm_range=ATM_RANGE,
                strike_step=STRIKE_STEP,
                index_symbol=INDEX_SYMBOL,
            )

            raw = selector.select()

            # ✅ NORMALIZE SELECTION
            if isinstance(raw, dict):
                selection = raw.get("options", [])
            elif isinstance(raw, list):
                selection = raw
            else:
                selection = []

            final = []
            for opt in selection:
                if not isinstance(opt, dict):
                    continue

                side = opt.get("type")
                if not side:
                    continue

                if is_in_trade(side):
                    continue

                final.append(opt)

            if final:
                save_selection(final)

        except Exception as e:
            print("[SELECTION] ERROR:", e)

        time.sleep(RECHECK_INTERVAL)


def start_selection_scheduler():
    Thread(target=selection_loop, daemon=True).start()
