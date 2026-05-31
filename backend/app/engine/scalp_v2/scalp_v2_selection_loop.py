# backend/app/engine/scalp_v2/scalp_v2_selection_loop.py
#
# SCALP_V2 — Selection Loop (auto per-class)
# ============================================================================
# Mirrors the SCALP_V1 selection_loop structure, but:
#
#   - Runs OptionSelector ONCE PER CLASS (A/B/C), each with that class's own
#     premium band (classes.{A,B,C}.premium.min/max from the SCALP_V2 config).
#   - Writes class-suffixed selection files via save_selection:
#         save_selection("SCALP_V2_A", final_a) -> SCALP_V2_A_selected_ce.json / _pe.json
#     ...which is exactly what scalp_v2_tick_engine.read_class_selection reads.
#   - Builds the single shared-ticker ScalpV2TickEngine once, wiring it to the
#     ScalpV2GroupManager (Model B). The engine's selection_provider /
#     candidate_provider are used by the group manager for election + slave
#     resolution.
#
# ISOLATION:
#   - SCALP_V1's selection_loop is NOT modified. This is a parallel loop with
#     its own _WS_ENGINE_V2 singleton and its own state files.
#   - Reuses OptionSelector, save_selection, load_nifty_weekly_* unchanged.
#   - No TradeStateManager involvement (Model B).
# ============================================================================

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
from app.brokers.zerodha_manager import ZerodhaManager
from app.strategy.strategy_registry import STRATEGIES

from app.execution.executor_factory import get_executor_for_broker

from app.engine.scalp_v2.scalp_v2_group_manager import ScalpV2GroupManager
from app.engine.scalp_v2.scalp_v2_tick_engine import ScalpV2TickEngine
from app.engine.scalp_v2.scalp_v2_gtt_monitor import ScalpV2GTTMonitor

# 🔒 LICENSE
from app.license import license_state
from app.license.license_state import LicenseStatus


STRATEGY_ID      = "SCALP_V2"
INDEX_SYMBOL     = "NIFTY"
TRADE_MODE       = "BOTH"
ATM_RANGE        = 800
STRIKE_STEP      = 50
RECHECK_INTERVAL = 120  # seconds
CLASSES          = ("A", "B", "C")


# Singletons (one per process — V2 trades one group at a time)
_WS_ENGINE_V2   = None
_GROUP_MANAGER  = None
_GTT_MONITOR    = None


def _get_timeframe_sec() -> int:
    reg = STRATEGIES.get(STRATEGY_ID, {})
    if "timeframe_sec" in reg:
        return int(reg["timeframe_sec"])
    tf = reg.get("timeframe", "1m")
    try:
        if tf.endswith("m"):
            return int(tf[:-1]) * 60
        if tf.endswith("s"):
            return int(tf[:-1])
    except (ValueError, IndexError):
        pass
    return 60


def _class_band(cfg: dict, trade_class: str) -> tuple:
    c = (cfg.get("classes") or {}).get(trade_class, {})
    band = c.get("premium", {})
    return float(band.get("min", 0)), float(band.get("max", 0))


async def scalp_v2_selection_loop(broker_manager: ZerodhaManager):
    """
    Async loop: auto-selects per-class contracts and runs the V2 engine.
    Launched from api_server startup (StrategyRuntimeManager-style) only when
    SCALP_V2 is enabled in STRATEGIES.
    """
    global _WS_ENGINE_V2, _GROUP_MANAGER, _GTT_MONITOR

    await asyncio.sleep(0)  # Windows asyncio yield

    if license_state.LICENSE_STATUS != LicenseStatus.VALID:
        write_audit_log(
            f"[V2_SELECT] License not valid ({license_state.LICENSE_STATUS}) — not started"
        )
        return

    timeframe_sec = _get_timeframe_sec()
    write_audit_log(f"[V2_SELECT] Selection loop started timeframe={timeframe_sec}s")

    while True:
        try:
            if not broker_manager.is_ready():
                write_audit_log("[V2_SELECT] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            kite_data  = broker_manager.get_data_kite()
            if not kite_trade or not kite_data:
                write_audit_log("[V2_SELECT] Trade/Data session not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg = load_strategy_config(STRATEGY_ID)

            # --------------------------------------------------
            # 1️⃣ LOAD OPTIONS (current + next week, like SCALP_V1)
            # --------------------------------------------------
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )
            if not instruments:
                write_audit_log("[V2_SELECT][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            expiries        = sorted({o["expiry"] for o in instruments})
            weekly_expiries = expiries[:2]
            instruments     = [o for o in instruments if o["expiry"] in weekly_expiries]

            # --------------------------------------------------
            # 2️⃣ PER-CLASS SELECTION (one OptionSelector per band)
            # --------------------------------------------------
            for trade_class in CLASSES:
                lo, hi = _class_band(cfg, trade_class)
                if hi <= 0 or hi < lo:
                    write_audit_log(
                        f"[V2_SELECT] class={trade_class} invalid band {lo}-{hi} — skip"
                    )
                    continue

                selector = OptionSelector(
                    instruments=instruments,
                    price_min=lo,
                    price_max=hi,
                    trade_mode=TRADE_MODE,
                    atm_range=ATM_RANGE,
                    strike_step=STRIKE_STEP,
                    index_symbol=INDEX_SYMBOL,
                    kite=kite_trade,
                )

                raw = selector.select()
                if not raw:
                    write_audit_log(f"[V2_SELECT] class={trade_class} selector empty (band {lo}-{hi})")
                    continue

                final = (raw.get("CE", []) or []) + (raw.get("PE", []) or [])
                if final:
                    # save_selection derives filenames from the id:
                    #   "SCALP_V2_A" -> SCALP_V2_A_selected_ce.json / _pe.json
                    save_selection(f"{STRATEGY_ID}_{trade_class}", final)
                    write_audit_log(
                        f"[V2_SELECT] class={trade_class} band={lo}-{hi}: "
                        + ", ".join(o["tradingsymbol"] for o in final)
                    )

            # --------------------------------------------------
            # 3️⃣ START ENGINE + GROUP MANAGER + MONITOR (once)
            # --------------------------------------------------
            if _WS_ENGINE_V2 is None:
                universe = load_nifty_weekly_universe(
                    api_key=kite_trade.api_key,
                    access_token=kite_trade.access_token,
                    atm_range=ATM_RANGE,
                    strike_step=STRIKE_STEP,
                )
                if not universe:
                    write_audit_log("[V2_SELECT][FATAL] Weekly universe empty")
                    await asyncio.sleep(RECHECK_INTERVAL)
                    continue

                tokens   = [o["instrument_token"] for o in universe]
                executor = get_executor_for_broker(
                    STRATEGIES.get(STRATEGY_ID, {}).get("broker", "ZERODHA")
                )

                # Build group manager first; wire providers from the engine.
                # Two-step: engine needs group_manager, group_manager needs
                # engine's providers. Resolve by creating the engine, then the
                # group manager bound to its bound methods, then attaching the
                # group manager onto the engine.
                engine = ScalpV2TickEngine(
                    kite_data=kite_data,
                    instrument_tokens=tokens,
                    group_manager=None,           # set just below
                    timeframe_sec=timeframe_sec,
                )

                group_manager = ScalpV2GroupManager(
                    executor=executor,
                    selection_provider=engine.selection_provider,
                    candidate_provider=engine.candidate_provider,
                )

                engine.group_manager = group_manager   # complete the wiring

                monitor = ScalpV2GTTMonitor(
                    executor=executor,
                    group_manager=group_manager,
                )

                engine.start()
                monitor.start()

                _WS_ENGINE_V2  = engine
                _GROUP_MANAGER = group_manager
                _GTT_MONITOR   = monitor

                write_audit_log(
                    f"[V2_SELECT] Engine + group manager + monitor started "
                    f"tokens={len(tokens)}"
                )

        except Exception as e:
            write_audit_log(f"[V2_SELECT] ERROR {repr(e)}")

        await asyncio.sleep(RECHECK_INTERVAL)


# --------------------------------------------------
# Accessors (for EOD job / status routes)
# --------------------------------------------------

def get_group_manager():
    return _GROUP_MANAGER


def get_engine():
    return _WS_ENGINE_V2