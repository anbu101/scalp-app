# backend/app/engine/scalp_v2/scalp_v2_selection_loop.py
#
# SCALP_V2 — Selection Loop (v2.0 — clone of V1 selection loop)
# ============================================================================
# Mirrors SCALP_V1's selection_loop, with ONE premium range (no A/B/C bands).
# Builds — once — the SCALP_V2 tick engine, group manager, and GTT backstop
# monitor, then keeps the selection (selected CE/PE within the premium range)
# refreshed every RECHECK_INTERVAL.
#
# Module accessors get_group_manager() / get_engine() are used by:
#   - scalp_v2_api.py        (dashboard state)
#   - scalp_v2_live_eod.py   (EOD square-off)
#   - scalp_v2_gtt_monitor   (backstop; built here)
#
# Isolated: builds SCALP_V2's OWN engine instance. SCALP_V1 / BB / HA untouched.
# ============================================================================

import asyncio
from typing import Optional, Set, Tuple

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import (
    load_nifty_weekly_options,
    load_nifty_weekly_universe,
)
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection, load_selection
from app.event_bus.audit_logger import write_audit_log
from app.brokers.zerodha_manager import ZerodhaManager

from app.engine.scalp_v2.scalp_v2_tick_engine import ScalpV2TickEngine
from app.engine.scalp_v2.scalp_v2_group_manager import ScalpV2GroupManager
from app.engine.scalp_v2.scalp_v2_gtt_monitor import ScalpV2GTTMonitor

# 🔒 LICENSE
from app.license import license_state
from app.license.license_state import LicenseStatus


STRATEGY_ID  = "SCALP_V2"
INDEX_SYMBOL = "NIFTY"
TRADE_MODE   = "BOTH"
ATM_RANGE    = 800
STRIKE_STEP  = 50
RECHECK_INTERVAL = 120  # seconds


# =========================
# Singletons (per-process)
# =========================

_ENGINE:        Optional[ScalpV2TickEngine]   = None
_GROUP_MANAGER: Optional[ScalpV2GroupManager] = None
_GTT_MONITOR:   Optional[ScalpV2GTTMonitor]   = None


def get_group_manager() -> Optional[ScalpV2GroupManager]:
    return _GROUP_MANAGER


def get_engine() -> Optional[ScalpV2TickEngine]:
    return _ENGINE


# =========================
# Timeframe helper
# =========================

def _get_timeframe_sec() -> int:
    """SCALP_V2 uses 1-minute candles (same default as V1)."""
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
# Selected-symbol provider (for the group manager's selection filter)
# =========================

def _selected_provider() -> Tuple[Set[str], Set[str]]:
    """Returns (ce_set, pe_set) of the currently selected SCALP_V2 symbols."""
    ce: Set[str] = set()
    pe: Set[str] = set()
    try:
        sel = load_selection(STRATEGY_ID)
        for o in sel.get("CE", []):
            s = o.get("symbol") or o.get("tradingsymbol")
            if s:
                ce.add(s)
        for o in sel.get("PE", []):
            s = o.get("symbol") or o.get("tradingsymbol")
            if s:
                pe.add(s)
    except Exception as e:
        write_audit_log(f"[V2_SELECT] selected_provider read failed: {e}")
    return ce, pe


# =========================
# Main async loop
# =========================

async def scalp_v2_selection_loop(broker_manager: ZerodhaManager, *args, **kwargs):
    """
    Entry point spawned by api_server. Named `scalp_v2_selection_loop` to match
    the existing import in api_server.py.

    Signature is tolerant (*args/**kwargs) so that however the launcher calls it
    today (e.g. an old call site that passed a strategy_id positional, or none),
    it still binds. Only broker_manager is used; SCALP_V2 strategy_id is fixed
    internally. Extra args are ignored.
    """
    global _ENGINE, _GROUP_MANAGER, _GTT_MONITOR

    # 🔑 yield immediately (Windows asyncio requirement)
    await asyncio.sleep(0)

    if license_state.LICENSE_STATUS != LicenseStatus.VALID:
        write_audit_log(
            f"[V2_SELECT] License not valid ({license_state.LICENSE_STATUS}) — engine not started"
        )
        return

    timeframe_sec = _get_timeframe_sec()
    write_audit_log(f"[V2_SELECT] Selection engine started ({STRATEGY_ID}) tf={timeframe_sec}s")

    while True:
        try:
            write_audit_log(f"[V2_SELECT] loop tick ({STRATEGY_ID})")

            if not broker_manager.is_ready():
                write_audit_log(f"[V2_SELECT] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            kite_data  = broker_manager.get_data_kite()
            if not kite_trade or not kite_data:
                write_audit_log(f"[V2_SELECT] Trade/Data session not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg = load_strategy_config(STRATEGY_ID)
            premium_cfg = cfg.get("option_premium", {})

            # ----- 1) LOAD OPTIONS -----
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )
            if not instruments:
                write_audit_log("[V2_SELECT][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            expiries = sorted({o["expiry"] for o in instruments})
            weekly_expiries = expiries[:2]
            instruments = [o for o in instruments if o["expiry"] in weekly_expiries]

            current_weekly_expiry = weekly_expiries[0] if weekly_expiries else None
            write_audit_log(f"[V2_SELECT] Current weekly expiry = {current_weekly_expiry}")

            # ----- 2) OPTION SELECTION (single premium range) -----
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
                write_audit_log("[V2_SELECT] selector returned empty")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            ce = raw.get("CE", [])
            pe = raw.get("PE", [])

            # ----- 3) START ENGINE + GROUP MANAGER + MONITOR (once) -----
            if _ENGINE is None:
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

                tokens = [o["instrument_token"] for o in universe]

                # Order executor for SCALP_V2 (live order placement). Built the
                # same way the rest of the app builds it: ZerodhaOrderExecutor
                # takes the broker manager. The group manager only calls
                # place_sell_entry / place_gtt_oco(direction="SHORT") /
                # cancel_gtt / place_buy_exit / get_gtts / get_open_positions /
                # get_orders — all provided by this executor (same one SCALP_V1
                # short uses). PAPER mode never calls the executor.
                from app.execution.zerodha_executor import ZerodhaOrderExecutor
                executor = ZerodhaOrderExecutor(broker_manager)

                # Build engine FIRST (needs to exist to provide providers), but
                # the group manager needs the engine's providers. Resolve the
                # chicken-and-egg by building the engine, then the manager with
                # engine-backed providers, then attaching the manager to engine.
                engine = ScalpV2TickEngine(
                    kite_data=kite_data,
                    instrument_tokens=tokens,
                    group_manager=None,        # set just below
                    timeframe_sec=timeframe_sec,
                )

                group_manager = ScalpV2GroupManager(
                    executor=executor,
                    candidate_provider=engine.candidate_provider,
                    instrument_provider=engine.instrument_provider,
                    selected_provider=_selected_provider,
                    candle_provider=engine.candle_provider,
                )

                engine.group_manager = group_manager
                engine.start()

                monitor = ScalpV2GTTMonitor(executor=executor, group_manager=group_manager)
                monitor.start()

                _ENGINE        = engine
                _GROUP_MANAGER = group_manager
                _GTT_MONITOR   = monitor

                write_audit_log(
                    f"[V2_SELECT] Engine + group manager + monitor started "
                    f"tokens={len(tokens)} tf={timeframe_sec}s"
                )

            # ----- 4) FINAL SELECTION (cap a few per side, like V1) -----
            final = []
            final.extend(ce[:4])
            final.extend(pe[:4])

            if final:
                save_selection(STRATEGY_ID, final)
                write_audit_log(
                    f"[V2_SELECT] Updated selection ({STRATEGY_ID}): "
                    + ", ".join(o["tradingsymbol"] for o in final)
                )

        except Exception as e:
            write_audit_log(f"[V2_SELECT] ERROR ({STRATEGY_ID}) {repr(e)}")

        await asyncio.sleep(RECHECK_INTERVAL)