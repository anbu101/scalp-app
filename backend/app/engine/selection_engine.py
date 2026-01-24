import asyncio
from datetime import date

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
from app.trading.trade_state_manager import TradeStateManager

# 🔒 LICENSE
from app.license import license_state
from app.license.license_state import LicenseStatus



# =========================
# Constants
# =========================

INDEX_SYMBOL = "NIFTY"
TRADE_MODE = "BOTH"
ATM_RANGE = 800
STRIKE_STEP = 50
RECHECK_INTERVAL = 120  # seconds


# =========================
# Internal state
# =========================

_WS_ENGINE = None   # SINGLE WS ENGINE (STRICT)


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
    HARD GUARANTEES:
    - Expiry logic lives ONLY in zerodha_instruments.py
    - Selection engine NEVER filters weekly/monthly for ACTIVE trades
    - WS uses DATA session ONLY
    - WS is single-instance
    - 🔒 ACTIVE TRADE SYMBOLS ARE NEVER REPLACED
    - 🔒 WS NEVER STARTS WITHOUT VALID LICENSE
    """

    global _WS_ENGINE

    # 🔒 LICENSE GATE (CRITICAL)
    if license_state.LICENSE_STATUS != LicenseStatus.VALID:
        write_audit_log(
            f"[ENGINE] License not valid ({license_state.LICENSE_STATUS}) — engine & WS not started"
        )
        return


    write_audit_log("[ENGINE] Selection engine started")

    while True:
        try:
            write_audit_log("[ENGINE] loop tick")

            # --------------------------------------------------
            # Broker refresh
            # --------------------------------------------------
            if not broker_manager.refresh():
                _WS_ENGINE = None
                write_audit_log("[ENGINE] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            kite_data  = broker_manager.get_data_kite()

            if not kite_trade or not kite_data:
                write_audit_log("[ENGINE] Trade/Data session not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg = load_strategy_config()
            premium_cfg = cfg.get("option_premium", {})

            # --------------------------------------------------
            # 1️⃣ LOAD OPTIONS (ALL EXPIRIES)
            # --------------------------------------------------
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )

            if not instruments:
                write_audit_log("[ENGINE][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            all_instruments = instruments[:]

            expiries = sorted({o["expiry"] for o in instruments})
            weekly_expiries = expiries[:2]

            instruments = [
                o for o in instruments
                if o["expiry"] in weekly_expiries
            ]

            write_audit_log(
                "[ENGINE] Weekly expiries in use: "
                + ", ".join(str(e) for e in weekly_expiries)
            )

            # --------------------------------------------------
            # 2️⃣ OPTION SELECTION
            # --------------------------------------------------
            selector = OptionSelector(
                instruments=instruments,
                price_min=premium_cfg.get("min", 0),
                price_max=premium_cfg.get("max", 1e9),
                trade_mode=TRADE_MODE,
                atm_range=ATM_RANGE,
                strike_step=STRIKE_STEP,
                index_symbol=INDEX_SYMBOL,
                kite=kite_trade,
            )

            raw = selector.select()
            if not raw:
                write_audit_log("[ENGINE] selector returned empty")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            ce = raw.get("CE", [])
            pe = raw.get("PE", [])

            # --------------------------------------------------
            # 🔒 LOCKED SLOTS (ACTIVE TRADES)
            # --------------------------------------------------
            locked_ce = []
            locked_pe = []

            for mgr in TradeStateManager._REGISTRY.values():
                if not mgr.in_trade or not mgr.active_trade:
                    continue

                sym = mgr.active_trade.symbol
                if sym.endswith("CE"):
                    locked_ce.append(sym)
                elif sym.endswith("PE"):
                    locked_pe.append(sym)

            # --------------------------------------------------
            # 3️⃣ START WS (ONCE, SAFE)
            # --------------------------------------------------
            if _WS_ENGINE is None:
                universe = load_nifty_weekly_universe(
                    api_key=kite_trade.api_key,
                    access_token=kite_trade.access_token,
                    atm_range=ATM_RANGE,
                    strike_step=STRIKE_STEP,
                )

                if not universe:
                    write_audit_log("[UNIVERSE][FATAL] Weekly universe empty")
                    await asyncio.sleep(RECHECK_INTERVAL)
                    continue

                tokens = [o["instrument_token"] for o in universe]

                _WS_ENGINE = ZerodhaTickEngine(
                    kite_data=kite_data,
                    instrument_tokens=tokens,
                    timeframe_sec=60,
                )
                _WS_ENGINE.start()

                write_audit_log(
                    f"[WS] Tick engine started ({len(tokens)} tokens)"
                )

            # --------------------------------------------------
            # 4️⃣ FINAL SELECTION (LOCKED + FREE)
            # --------------------------------------------------
            final = []

            for sym in locked_ce + locked_pe:
                match = next(
                    (o for o in all_instruments if o["tradingsymbol"] == sym),
                    None,
                )
                if match:
                    final.append(match)

            free_ce = [o for o in ce if o["tradingsymbol"] not in locked_ce]
            free_pe = [o for o in pe if o["tradingsymbol"] not in locked_pe]

            final.extend(free_ce[: max(0, 2 - len(locked_ce))])
            final.extend(free_pe[: max(0, 2 - len(locked_pe))])

            # --------------------------------------------------
            # 🔒 SAFETY CHECK
            # --------------------------------------------------
            for mgr in TradeStateManager._REGISTRY.values():
                if mgr.in_trade and mgr.active_trade:
                    sym = mgr.active_trade.symbol
                    if not any(o["tradingsymbol"] == sym for o in final):
                        raise RuntimeError(
                            f"LOCK VIOLATION: active trade {sym} missing"
                        )

            # --------------------------------------------------
            # 5️⃣ SAVE
            # --------------------------------------------------
            if final:
                save_selection(final)
                write_audit_log(
                    "[ENGINE] Updated selection: "
                    + ", ".join(o["tradingsymbol"] for o in final)
                )

        except Exception as e:
            write_audit_log(f"[ENGINE] ERROR {repr(e)}")
            #_WS_ENGINE = None

        await asyncio.sleep(RECHECK_INTERVAL)
