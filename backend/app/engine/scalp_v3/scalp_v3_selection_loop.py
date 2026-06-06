# backend/app/engine/scalp_v3/scalp_v3_selection_loop.py
#
# SCALP_V3 — Selection Loop (TEST option-BUYING hedge clone of SCALP_V1).
# ============================================================================
# Mirrors scalp_v2_selection_loop: builds — once — the SCALP_V3 tick engine,
# then keeps the selection (CE/PE within the premium range) refreshed every
# RECHECK_INTERVAL. After each save, the freshly-selected tokens are pushed to
# the engine (subscribe_additional_tokens) so they begin forming candles.
#
# Module accessors get_engine() / get_manager() are used by:
#   - scalp_v3_live_eod.py  (EOD square-off)
#   - the V3 API / frontend  (dashboard state) [later]
#
# Isolated: builds SCALP_V3's OWN engine + OWN KiteTicker. SCALP_V1 / BB / HA /
# V2 untouched. Launched as a standalone async task by api_server (deferred from
# StrategyRuntimeManager), exactly like SCALP_V2.
#
# Executor: uses the ROUTER (get_executor_for_broker) — V3's manager targets the
# router surface (resolve_ltp / place_market_sell / cancel_gtt / get_order_fill /
# place_gtt_oco with last_price+direction), all added additively in
# execution_router.py.
#
# CADENCE ALIGNMENT:
#   The steady-state tail sleep snaps to a fixed wall-clock grid (every
#   RECHECK_INTERVAL, phase_offset=30) so SCALP_V1 and SCALP_V3 take their LTP
#   snapshots at the same instants and select the same contracts when premiums
#   match. The RETRY sleeps (broker-not-ready, empty selector, etc.) stay as
#   plain fixed sleeps — those are retries, not aligned cycles. V3 remains fully
#   self-contained: it runs its OWN selection and has NO dependency on V1, so it
#   works correctly when run alone.
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

from app.engine.scalp_v3.scalp_v3_tick_engine import ScalpV3TickEngine

# 🔒 LICENSE
from app.license import license_state
from app.license.license_state import LicenseStatus


STRATEGY_ID  = "SCALP_V3"
INDEX_SYMBOL = "NIFTY"
ATM_RANGE    = 800
STRIKE_STEP  = 50
RECHECK_INTERVAL = 120  # seconds


# =========================
# Singletons (per-process)
# =========================

_ENGINE: Optional[ScalpV3TickEngine] = None


def get_engine() -> Optional[ScalpV3TickEngine]:
    return _ENGINE


def get_manager():
    return _ENGINE.manager if _ENGINE is not None else None


# =========================
# Cadence alignment
# =========================

def _seconds_to_next_boundary(interval: int, phase_offset: int = 0) -> float:
    """
    Sleep target that snaps the steady-state cycle to a fixed wall-clock grid so
    SCALP_V1 and SCALP_V3 take their LTP snapshots at the same instants (both
    call this with the same interval + phase_offset). Independent of when each
    task started or how long the loop body took.

      interval=120, phase_offset=30  -> wakes at :30 past every even minute
      (09:30:30, 09:32:30, ...).
    """
    now = time.time()
    base = (int(now) // interval + 1) * interval + phase_offset
    if base - now < 1.0:        # offset landed us essentially "now" -> next grid slot
        base += interval
    return base - now


# =========================
# Timeframe helper
# =========================

def _get_timeframe_sec() -> int:
    """SCALP_V3 uses 1-minute candles (same default as V1)."""
    cfg = load_strategy_config(STRATEGY_ID)
    tf = cfg.get("timeframe", "1m")
    try:
        if isinstance(tf, str) and tf.endswith("m"):
            return int(tf[:-1]) * 60
        if isinstance(tf, str) and tf.endswith("s"):
            return int(tf[:-1])
    except (ValueError, IndexError):
        pass
    return 60


# =========================
# Main async loop
# =========================

async def scalp_v3_selection_loop(broker_manager: ZerodhaManager, *args, **kwargs):
    """
    Entry point spawned by api_server. Tolerant signature (*args/**kwargs) so the
    launcher can call it however it calls the V2 loop. Only broker_manager is
    used; the SCALP_V3 strategy_id is fixed internally.
    """
    global _ENGINE

    # yield immediately (Windows asyncio requirement)
    await asyncio.sleep(0)

    if license_state.LICENSE_STATUS != LicenseStatus.VALID:
        write_audit_log(
            f"[V3_SELECT] License not valid ({license_state.LICENSE_STATUS}) — engine not started"
        )
        return

    timeframe_sec = _get_timeframe_sec()
    write_audit_log(f"[V3_SELECT] Selection engine started ({STRATEGY_ID}) tf={timeframe_sec}s")

    while True:
        try:
            write_audit_log(f"[V3_SELECT] loop tick ({STRATEGY_ID})")

            if not broker_manager.is_ready():
                write_audit_log("[V3_SELECT] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            kite_data  = broker_manager.get_data_kite()
            if not kite_trade or not kite_data:
                write_audit_log("[V3_SELECT] Trade/Data session not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg         = load_strategy_config(STRATEGY_ID)
            premium_cfg = cfg.get("option_premium", {})
            trade_mode  = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()

            # ----- 1) LOAD OPTIONS -----
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )
            if not instruments:
                write_audit_log("[V3_SELECT][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            expiries = sorted({o["expiry"] for o in instruments})
            weekly_expiries = expiries[:2]
            instruments = [o for o in instruments if o["expiry"] in weekly_expiries]

            current_weekly_expiry = weekly_expiries[0] if weekly_expiries else None
            write_audit_log(f"[V3_SELECT] Current weekly expiry = {current_weekly_expiry}")

            # ----- 2) OPTION SELECTION (single premium range) -----
            # NOTE: trade_mode here gates which SIDES get SELECTED. With "BOTH"
            # we still select both CE and PE (we need the opposite side as the
            # hedge candidate even when only one side may signal). For "CE"/"PE"
            # the engine's _handle_signal further restricts the SIGNAL side; but
            # selection must keep BOTH sides so a hedge is always available.
            # Therefore we ALWAYS select BOTH here, regardless of trade_mode.
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
                write_audit_log("[V3_SELECT] selector returned empty")
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
                    write_audit_log("[V3_SELECT][FATAL] Weekly universe empty")
                    await asyncio.sleep(RECHECK_INTERVAL)
                    continue

                tokens = [o["instrument_token"] for o in universe]

                # Router executor (NOT raw) — V3 manager targets the router surface.
                executor = get_executor_for_broker("ZERODHA")

                engine = ScalpV3TickEngine(
                    kite_data=kite_data,
                    instrument_tokens=tokens,
                    executor=executor,
                    timeframe_sec=timeframe_sec,
                )
                engine.start()

                _ENGINE = engine
                write_audit_log(
                    f"[V3_SELECT] Engine started tokens={len(tokens)} tf={timeframe_sec}s"
                )

            # ----- 4) FINAL SELECTION (cap 2 per side, like V1) -----
            # 2 CE + 2 PE — exactly the SCALP_V1 cap (OptionSelector already
            # returns <=2 per side; the slice is a defensive belt).
            final = []
            final.extend(ce[:2])
            final.extend(pe[:2])

            if final:
                save_selection(STRATEGY_ID, final)
                write_audit_log(
                    f"[V3_SELECT] Updated selection ({STRATEGY_ID}): "
                    + ", ".join(o["tradingsymbol"] for o in final)
                )

                # Push selected tokens to the engine so they form candles.
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
                    write_audit_log(f"[V3_SELECT][SUBSCRIBE_ERR] {e}")

        except Exception as e:
            write_audit_log(f"[V3_SELECT] ERROR ({STRATEGY_ID}) {repr(e)}")

        # Steady-state cadence: snap to the shared wall-clock grid so V1 and V3
        # take their LTP snapshots together. (Retry sleeps above stay fixed.)
        await asyncio.sleep(_seconds_to_next_boundary(RECHECK_INTERVAL, phase_offset=30))