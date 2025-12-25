import asyncio

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import (
    load_nifty_weekly_options,
    load_nifty_weekly_universe,
)
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.zerodha_tick_engine import ZerodhaTickEngine
from app.brokers.zerodha_manager import ZerodhaManager


# =========================
# Constants
# =========================

INDEX_SYMBOL = "NIFTY"
TRADE_MODE = "BOTH"
ATM_RANGE = 800
STRIKE_STEP = 50
RECHECK_INTERVAL = 180  # seconds


# =========================
# Internal state
# =========================

_WS_ENGINE = None   # FULL-universe WS engine (singleton)


# =========================
# API compatibility
# =========================

def recompute_selection():
    return {"status": "selection engine runs automatically"}


# =========================
# Main async loop
# =========================

async def selection_loop(broker_manager: ZerodhaManager):
    """
    Selection engine responsibilities:
    - Start ONE global Zerodha WS (LTP only)
    - Periodically select best CE / PE options
    - Persist selection for strategy engine to consume

    ❌ Does NOT:
    - build candles
    - calculate EMA / RSI
    - trigger trades
    """

    global _WS_ENGINE

    write_audit_log("[ENGINE] Selection engine started")

    while True:
        write_audit_log("[ENGINE] loop tick")

        try:
            # --------------------------------------------------
            # Zerodha session guard
            # --------------------------------------------------
            if not broker_manager.is_ready():
                write_audit_log(
                    "[ENGINE] Broker not ready → skipping selection"
                )
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite = broker_manager.get_kite()

            cfg = load_strategy_config()
            premium_cfg = cfg.get("option_premium", {})

            # --------------------------------------------------
            # 1️⃣ PURE OPTION SELECTION (FIRST)
            # --------------------------------------------------
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
                kite=kite,
            )

            raw = selector.select()

            if not raw:
                write_audit_log("[ENGINE] selector returned NONE / EMPTY")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            ce = raw.get("CE", [])
            pe = raw.get("PE", [])

            write_audit_log(
                f"[ENGINE] selector returned CE={len(ce)} PE={len(pe)}"
            )

            # --------------------------------------------------
            # 2️⃣ START FULL UNIVERSE WS (ONLY AFTER FIRST SELECTION)
            # --------------------------------------------------
            if _WS_ENGINE is None:
                universe = load_nifty_weekly_universe(
                    api_key=kite.api_key,
                    access_token=kite.access_token,
                    atm_range=ATM_RANGE,
                    strike_step=STRIKE_STEP,
                )

                if not universe:
                    write_audit_log("[UNIVERSE] Empty universe — retry later")
                    await asyncio.sleep(RECHECK_INTERVAL)
                    continue

                tokens = [o["instrument_token"] for o in universe]

                write_audit_log(
                    f"[UNIVERSE] Loaded {len(tokens)} instruments"
                )

                _WS_ENGINE = ZerodhaTickEngine(
                    api_key=kite.api_key,
                    access_token=kite.access_token,
                    instrument_tokens=tokens,
                    exchange="NFO",
                    timeframe_sec=60,
                    # candle_base_dir="/app/app/state/candles",
                )
                _WS_ENGINE.start()

                write_audit_log(
                    f"[WS] Tick engine started for FULL universe ({len(tokens)} tokens)"
                )

            # --------------------------------------------------
            # 3️⃣ PICK TOP 2 CE + TOP 2 PE
            # --------------------------------------------------
            final = []
            final.extend(ce[:2])
            final.extend(pe[:2])

            # --------------------------------------------------
            # 4️⃣ PERSIST SELECTION (ONLY)
            # --------------------------------------------------
            if final:
                save_selection(final)

                write_audit_log(
                    "[ENGINE] Updated selection: "
                    + ", ".join(o["tradingsymbol"] for o in final)
                )

        except Exception as e:
            write_audit_log(f"[ENGINE] ERROR {repr(e)}")

        await asyncio.sleep(RECHECK_INTERVAL)
