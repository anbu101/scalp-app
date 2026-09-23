# backend/app/engine/ha_options/ha_selection_loop.py
#
# ── HA_OWN_SELECT_20260923 ── HA_V1's OWN selection loop.
# ============================================================================
# WHY: until 2026-09-23 HA_V1 read SCALP_V1's selection files and took the
# first in-band row per side. That coupled HA to SCALP_V1's premium band and
# loop, and — because HA fed its evaluator for ONE contract per side and reset
# it on every rotation — diverged from backtest_ha_runner, which keeps HA state
# on the whole selected union and gates only the ENTRY on snapshot membership.
#
# WHAT: the same OptionSelector rule every other loop uses and the one
# backtest_selector.py replays (premium band → nearest weekly expiry →
# median-ATM → ATM±800 → nearest 2 per side), run on HA_V1's OWN
# option_premium band every RECHECK_INTERVAL on the shared wall-clock grid,
# saved to HA_V1_selected_{ce,pe}.json. Lock carve-out: a contract with an open
# HA trade stays in the selection until it closes (matches the backtest's
# LOCK CARVE-OUT and live SCALP_V1's locked slots).
#
# MARKET DATA: this loop opens NO WebSocket. Ticks and the universe come from
# the shared weekly-universe ZerodhaTickEngine (ws_registry). Kite allows 3
# sockets per api_key as a soft limit and the app already runs five on one
# key; HA must not add a sixth. ZerodhaTickEngine also carries SCALP_V1's
# trading logic keyed on its strategy_id, so HA must NEVER construct one under
# its own name. When SCALP_V1 is retired, keep that engine as the data spine
# (remove its signal path) — separate task.
#
# Isolated: nothing here touches SCALP_V1 / V3 / V5 / TMA / VET code paths.
# Launched from StrategyRuntimeManager.start("HA_V1") next to start_ha_runtime.
# ============================================================================
import asyncio
import time
from typing import Dict, List, Optional

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection, load_selection
from app.event_bus.audit_logger import write_audit_log
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY
from app.marketdata.ws_registry import get_ws_engines

# 🔒 LICENSE
from app.license import license_state

STRATEGY_ID      = "HA_V1"
INDEX_SYMBOL     = "NIFTY"
TRADE_MODE       = "BOTH"      # always select BOTH sides; trade_side_mode gates in the engine
ATM_RANGE        = 800
STRIKE_STEP      = 50
RECHECK_INTERVAL = 120         # seconds — same grid as SCALP_V1 / V3 / V5
PHASE_OFFSET     = 30          # :30 past every even minute, same as the other loops
PER_SIDE         = 2           # nearest-2-per-side: what backtest_selector replays

_LAST_SAVED: Optional[List[str]] = None


def _seconds_to_next_boundary(interval: int, phase_offset: int = 0) -> float:
    now = time.time()
    base = (int(now) // interval + 1) * interval + phase_offset
    if base - now < 1.0:
        base += interval
    return base - now


def _ha_locked_symbols() -> set:
    '''Contracts with an open HA trade (paper or live), from every running HA
    engine. Fail-open to an empty set: a read error must never drop the
    selection, the engine's own _active_trade_symbols keeps monitoring.'''
    locked: set = set()
    for eng in list(HA_ENGINE_REGISTRY):
        try:
            with eng._active_trade_lock:
                locked |= set(eng._active_trade_symbols)
        except Exception:
            continue
    return locked


def _locked_rows(locked: set, prev: Dict[str, List[Dict]], instruments: List[Dict]) -> List[Dict]:
    '''Rows for locked contracts: prefer the previously saved row (has ltp /
    selected_at), else build one from the instrument master.'''
    rows: List[Dict] = []
    prev_by_sym = {}
    for side in ("CE", "PE"):
        for r in prev.get(side, []) or []:
            sym = r.get("tradingsymbol") or r.get("symbol")
            if sym:
                prev_by_sym[sym] = r
    inst_by_sym = {i.get("tradingsymbol"): i for i in instruments}
    for sym in sorted(locked):
        r = prev_by_sym.get(sym)
        if r is None:
            i = inst_by_sym.get(sym)
            if i is None:
                continue
            exp = i.get("expiry")
            r = {
                "symbol": sym, "tradingsymbol": sym,
                "strike": float(i.get("strike") or 0),
                "type": i.get("instrument_type"),
                "expiry": exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                "ltp": None,
                "instrument_token": i.get("instrument_token"),
            }
        rows.append(dict(r))
    return rows


def _attach_tokens(rows: List[Dict], instruments: List[Dict]) -> None:
    '''OptionSelector rows carry no instrument_token; the engine resolves a
    missing token from the instrument master anyway, but attaching it here
    saves that lookup and lets the shared WS engine subscribe if needed.'''
    by_sym = {i.get("tradingsymbol"): i.get("instrument_token") for i in instruments}
    for r in rows:
        if not r.get("instrument_token"):
            tok = by_sym.get(r.get("tradingsymbol") or r.get("symbol"))
            if tok:
                r["instrument_token"] = int(tok)


def _ensure_subscribed(rows: List[Dict]) -> None:
    '''Best-effort: ask the shared weekly-universe engine to subscribe any
    selected token it does not already stream (a band edge just outside
    ATM±800). Never raises; the engine's universe rarely misses these.'''
    engines = get_ws_engines()
    if not engines:
        return
    eng = engines[0]
    try:
        have = set(getattr(eng, "builders", {}).keys())
        missing = [int(r["instrument_token"]) for r in rows
                   if r.get("instrument_token") and int(r["instrument_token"]) not in have]
        if missing and hasattr(eng, "subscribe_additional_tokens"):
            eng.subscribe_additional_tokens(missing)
            write_audit_log(f"[HA_SELECT] asked shared WS engine ({getattr(eng, 'strategy_id', '?')}) "
                            f"to subscribe {len(missing)} selected token(s)")
    except Exception as e:
        write_audit_log(f"[HA_SELECT][SUBSCRIBE_WARN] {e}")


def select_once(cfg: dict, instruments: List[Dict], kite_trade, locked: set,
                prev: Dict[str, List[Dict]]) -> Optional[List[Dict]]:
    '''Pure selection step (no I/O besides kite.ltp inside OptionSelector).
    Returns the final row list to persist, or None when nothing selectable.'''
    premium_cfg = cfg.get("option_premium", {}) or {}
    expiries = sorted({o["expiry"] for o in instruments})
    weekly_expiries = expiries[:2]
    instruments = [o for o in instruments if o["expiry"] in weekly_expiries]
    if not instruments:
        return None

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
    ce = list((raw or {}).get("CE", []) or [])
    pe = list((raw or {}).get("PE", []) or [])

    locked_ce = {s for s in locked if s.endswith("CE")}
    locked_pe = {s for s in locked if s.endswith("PE")}
    final: List[Dict] = _locked_rows(locked, prev, instruments)
    free_ce = [o for o in ce if o["tradingsymbol"] not in locked_ce]
    free_pe = [o for o in pe if o["tradingsymbol"] not in locked_pe]
    final.extend(free_ce[: max(0, PER_SIDE - len(locked_ce))])
    final.extend(free_pe[: max(0, PER_SIDE - len(locked_pe))])
    if not final:
        return None
    _attach_tokens(final, instruments)
    return final


async def ha_selection_loop(broker_manager, *args, **kwargs):
    '''Entry point spawned by StrategyRuntimeManager.start("HA_V1").'''
    global _LAST_SAVED
    await asyncio.sleep(0)   # yield immediately (Windows asyncio requirement)

    if not license_state.is_usable():
        write_audit_log(
            f"[HA_SELECT] License not usable ({license_state.LICENSE_STATUS}) — loop not started"
        )
        return

    write_audit_log(f"[HA_SELECT] Selection loop started ({STRATEGY_ID}) "
                    f"rule=band→weekly→median-ATM→±{ATM_RANGE}→nearest-{PER_SIDE}/side "
                    f"every {RECHECK_INTERVAL}s (own band, own files, shared WS engine)")

    while True:
        try:
            if not broker_manager.is_ready():
                write_audit_log("[HA_SELECT] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            if not kite_trade:
                write_audit_log("[HA_SELECT] Trade session not ready (needed for kite.ltp)")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg = load_strategy_config(STRATEGY_ID)
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )
            if not instruments:
                write_audit_log("[HA_SELECT][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            locked = _ha_locked_symbols()
            prev = load_selection(STRATEGY_ID)
            final = select_once(cfg, instruments, kite_trade, locked, prev)

            if not final:
                band = cfg.get("option_premium", {}) or {}
                write_audit_log(f"[HA_SELECT] selector returned empty "
                                f"(band {band.get('min')}–{band.get('max')}) — previous selection kept")
            else:
                save_selection(STRATEGY_ID, final)
                syms = [o.get("tradingsymbol") or o.get("symbol") for o in final]
                if syms != _LAST_SAVED:
                    write_audit_log(
                        f"[HA_SELECT] Updated selection ({STRATEGY_ID}): "
                        + ", ".join(f"{s}{'*' if s in locked else ''}" for s in syms)
                        + ("   (* = locked, open trade)" if locked else "")
                    )
                    _LAST_SAVED = syms
                _ensure_subscribed(final)

        except Exception as e:
            write_audit_log(f"[HA_SELECT] ERROR {repr(e)}")

        await asyncio.sleep(_seconds_to_next_boundary(RECHECK_INTERVAL, phase_offset=PHASE_OFFSET))
