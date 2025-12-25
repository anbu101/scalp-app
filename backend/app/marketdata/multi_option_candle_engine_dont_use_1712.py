import asyncio

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.config.strategy_loader import load_strategy_config
from app.brokers.zerodha_auth import get_kite
from app.utils.selection_persistence import save_selection
from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.zerodha_tick_engine import ZerodhaTickEngine


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

_WS_ENGINE_STARTED = False


# =========================
# API compatibility
# =========================

def recompute_selection():
    return {"status": "selection engine runs automatically"}


# =========================
# Main async loop
# =========================

async def selection_loop():
    global _WS_ENGINE_STARTED

    write_audit_log("[ENGINE] Selection engine started")

    while True:
        try:
            kite = get_kite()
            if not kite:
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

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

            # -------------------------
            # Normalize output
            # -------------------------
            ce, pe = [], []

            if isinstance(raw, dict):
                ce = raw.get("CE", []) or []
                pe = raw.get("PE", []) or []
            elif isinstance(raw, list):
                for o in raw:
                    if o.get("type") == "CE":
                        ce.append(o)
                    elif o.get("type") == "PE":
                        pe.append(o)

            if not ce and not pe:
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            snapshot = TradeStateManager.snapshot()
            final = []

            if not snapshot:
                final.extend(ce[:2])
                final.extend(pe[:2])
            else:
                for i, opt in enumerate(ce, start=1):
                    if snapshot.get(f"CE_{i}") == "ARMED":
                        final.append(opt)

                for i, opt in enumerate(pe, start=1):
                    if snapshot.get(f"PE_{i}") == "ARMED":
                        final.append(opt)

            if final:
                save_selection(final)
                write_audit_log(
                    "[ENGINE] Updated selection: "
                    + ", ".join(o["tradingsymbol"] for o in final)
                )

                # 🔥 START ZERODHA WS CANDLE ENGINE (ONCE)
                if not _WS_ENGINE_STARTED:
                    tokens = [o["token"] for o in final if "token" in o]
                    if tokens:
                        ws_engine = ZerodhaTickEngine(
                            api_key=kite.api_key,
                            access_token=kite.access_token,
                            instrument_tokens=tokens,
                            exchange="NFO",
                            timeframe_sec=60,
                            candle_base_dir="app/logs/candles",
                        )
                        ws_engine.start()

                        write_audit_log(
                            f"[WS] 1M candle engine started for {len(tokens)} tokens"
                        )
                        _WS_ENGINE_STARTED = True

        except Exception as e:
            write_audit_log(f"[ENGINE] ERROR {e}")

        await asyncio.sleep(RECHECK_INTERVAL)
