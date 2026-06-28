# backend/app/engine/scalpv5/scalpv5_selection_loop.py
#
# SCALP_V5 — Selection Loop (TEST option-BUYING strategy, 3-minute candles).
# ============================================================================
# Mirrors scalp_v3_selection_loop: builds — once — the SCALP_V5 tick engine,
# then keeps the selection (CE/PE within the premium range) refreshed every
# RECHECK_INTERVAL. After each save, the freshly-selected tokens are pushed to
# the engine (subscribe_additional_tokens) so they begin forming 3m candles.
#
# DIFFERENCES FROM V3:
#   - timeframe_sec resolves to 180 (3m) from config.
#   - V5 trades the signalling contract directly, so selection only needs the
#     premium-band 2-CE / 2-PE set (no opposite-side hedge requirement). We
#     still select BOTH sides so either side can signal; trade_side_mode gates
#     the traded side in the engine.
#
# Module accessors get_engine() / get_manager() are used by:
#   - scalpv5_live_eod.py  (EOD square-off)
#   - the V5 API / frontend (dashboard state)
#
# Isolated: builds SCALP_V5's OWN engine + OWN KiteTicker. SCALP_V1..V4 / BB /
# HA untouched. Launched as a standalone async task by api_server (deferred from
# StrategyRuntimeManager), exactly like SCALP_V3.
# ============================================================================

import asyncio
import time
from typing import Optional

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import (
    load_nifty_weekly_options,
    load_nifty_weekly_universe,
)
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection
from app.event_bus.audit_logger import write_audit_log
from app.brokers.zerodha_manager import ZerodhaManager
from app.execution.executor_factory import get_executor_for_broker

from app.engine.scalpv5.scalpv5_tick_engine import ScalpV5TickEngine

# 🔒 LICENSE
from app.license import license_state
from app.license.license_state import LicenseStatus


STRATEGY_ID  = "SCALP_V5"
INDEX_SYMBOL = "NIFTY"
ATM_RANGE    = 800
STRIKE_STEP  = 50
RECHECK_INTERVAL = 120  # seconds


# =========================
# Singletons (per-process)
# =========================

_ENGINE: Optional[ScalpV5TickEngine] = None


def get_engine() -> Optional[ScalpV5TickEngine]:
    return _ENGINE


def get_manager():
    return _ENGINE.manager if _ENGINE is not None else None


# =========================
# Cadence alignment
# =========================

def _seconds_to_next_boundary(interval: int, phase_offset: int = 0) -> float:
    now = time.time()
    base = (int(now) // interval + 1) * interval + phase_offset
    if base - now < 1.0:
        base += interval
    return base - now


# =========================
# Timeframe helper
# =========================

def _get_timeframe_sec() -> int:
    """SCALP_V5 uses 3-minute candles."""
    cfg = load_strategy_config(STRATEGY_ID)
    tf = cfg.get("timeframe", "3m")
    try:
        if isinstance(tf, str) and tf.endswith("m"):
            return int(tf[:-1]) * 60
        if isinstance(tf, str) and tf.endswith("s"):
            return int(tf[:-1])
    except (ValueError, IndexError):
        pass
    return 180


# =========================
# Main async loop
# =========================

async def scalpv5_selection_loop(broker_manager: ZerodhaManager, *args, **kwargs):
    """
    Entry point spawned by api_server. Tolerant signature so the launcher can
    call it however it calls the V2/V3 loops. Only broker_manager is used; the
    SCALP_V5 strategy_id is fixed internally.
    """
    global _ENGINE

    # yield immediately (Windows asyncio requirement)
    await asyncio.sleep(0)

    if not license_state.is_usable():
        write_audit_log(
            f"[V5_SELECT] License not usable ({license_state.LICENSE_STATUS}) — engine not started"
        )
        return

    timeframe_sec = _get_timeframe_sec()
    write_audit_log(f"[V5_SELECT] Selection engine started ({STRATEGY_ID}) tf={timeframe_sec}s")

    while True:
        try:
            write_audit_log(f"[V5_SELECT] loop tick ({STRATEGY_ID})")

            if not broker_manager.is_ready():
                write_audit_log("[V5_SELECT] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            kite_data  = broker_manager.get_data_kite()
            if not kite_trade or not kite_data:
                write_audit_log("[V5_SELECT] Trade/Data session not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg         = load_strategy_config(STRATEGY_ID)
            premium_cfg = cfg.get("option_premium", {})

            # ----- 1) LOAD OPTIONS -----
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )
            if not instruments:
                write_audit_log("[V5_SELECT][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            expiries = sorted({o["expiry"] for o in instruments})
            weekly_expiries = expiries[:2]
            instruments = [o for o in instruments if o["expiry"] in weekly_expiries]

            current_weekly_expiry = weekly_expiries[0] if weekly_expiries else None
            write_audit_log(f"[V5_SELECT] Current weekly expiry = {current_weekly_expiry}")

            # ----- 2) OPTION SELECTION (single premium range, BOTH sides) -----
            # Always select BOTH sides; the engine's trade_side_mode gate
            # restricts which side may actually TRADE.
            selector = OptionSelector(
                instruments=instruments,
                price_min=premium_cfg.get("min", 0),
                price_max=premium_cfg.get("max", 1e9),
                trade_mode="BOTH",
                atm_range=ATM_RANGE,
                strike_step=STRIKE_STEP,
                index_symbol=INDEX_SYMBOL,
                kite=kite_trade,
            )
            raw = selector.select()
            if not raw:
                write_audit_log("[V5_SELECT] selector returned empty")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            ce = raw.get("CE", [])
            pe = raw.get("PE", [])

            # ----- 3) START ENGINE (once) -----
            if _ENGINE is None:
                universe = load_nifty_weekly_universe(
                    api_key=kite_trade.api_key,
                    access_token=kite_trade.access_token,
                    atm_range=ATM_RANGE,
                    strike_step=STRIKE_STEP,
                )
                if not universe:
                    write_audit_log("[V5_SELECT][FATAL] Weekly universe empty")
                    await asyncio.sleep(RECHECK_INTERVAL)
                    continue

                tokens = [o["instrument_token"] for o in universe]

                # Router executor (NOT raw) — V5 manager targets the router surface.
                executor = get_executor_for_broker("ZERODHA")

                engine = ScalpV5TickEngine(
                    kite_data=kite_data,
                    instrument_tokens=tokens,
                    executor=executor,
                    timeframe_sec=timeframe_sec,
                )
                engine.start()

                _ENGINE = engine
                write_audit_log(
                    f"[V5_SELECT] Engine started tokens={len(tokens)} tf={timeframe_sec}s"
                )

            # ----- 4) FINAL SELECTION (cap 2 per side, like V1/V3) -----
            final = []
            final.extend(ce[:2])
            final.extend(pe[:2])

            if final:
                save_selection(STRATEGY_ID, final)
                write_audit_log(
                    f"[V5_SELECT] Updated selection ({STRATEGY_ID}): "
                    + ", ".join(o["tradingsymbol"] for o in final)
                )

                try:
                    sel_tokens = []
                    for o in final:
                        sym = o.get("tradingsymbol") or o.get("symbol")
                        tok = _ENGINE.resolve_token(sym) if sym else None
                        if tok is not None:
                            sel_tokens.append(tok)
                    if sel_tokens:
                        _ENGINE.subscribe_additional_tokens(sel_tokens)
                except Exception as e:
                    write_audit_log(f"[V5_SELECT][SUBSCRIBE_ERR] {e}")

        except Exception as e:
            write_audit_log(f"[V5_SELECT] ERROR ({STRATEGY_ID}) {repr(e)}")

        await asyncio.sleep(_seconds_to_next_boundary(RECHECK_INTERVAL, phase_offset=30))