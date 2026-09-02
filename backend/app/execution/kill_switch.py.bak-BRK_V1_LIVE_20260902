# backend/app/execution/kill_switch.py
#
# PER-STRATEGY KILL SWITCH (locked with Anbu 2026-07-26)
# ============================================================================
# One button per strategy on the Dashboard: close every live position,
# cancel every GTT, verify FLAT against the strategy's own source of truth,
# and ONLY THEN flip trade_execution_mode → PAPER. Locked rules:
#
#   * KILL OVERRIDES EVERYTHING — IC's first-candle rule, TMA's positional
#     carry no-op, any session-time gating. A human pressing KILL means NOW.
#   * LIVE-ONLY — eligible when trade_execution_mode == "LIVE", plus the
#     IC special case: a live group still riding after the config was
#     already flipped (group mode is captured at entry).
#   * MODE FLIP LAST — PAPER is written only after the strategy verifies
#     flat. A partial kill NEVER leaves live positions under a PAPER label;
#     it reports stuck legs and keeps the mode as-is for a retry.
#   * NO NEW EXIT-REASON STRINGS — every adapter composes each strategy's
#     EXISTING close primitives with their existing reason vocabulary
#     (several tables carry CHECK constraints on exit_reason; "KILL" would
#     trip them). Kill identity lives in the audit log + alerts.
#
# PER-STRATEGY ADAPTERS (surveyed 2026-07-26 against each family's exit/GTT
# topology — a generic loop would close someone's position wrong):
#
#   SCALP_V1  slots in TradeStateManager._REGISTRY; NO GTTs; direction-aware
#             flatten already encoded in jobs/scalp_live_eod
#             _squareoff_live_trades() → reuse it verbatim. Verify: trades
#             table open rows for SCALP_V1.
#   BB_V1     SACRED — ZERO BB file edits. BB_ENGINE_REGISTRY engines
#             (BBOptionsTickEngine, V2 is a separate class, not a subclass);
#             engine.eod_squareoff() → BBTradeManager._exit() which already
#             cancel-verifies GTTs before flattening with FLAT_GUARD.
#             Verify: trades table.
#   BB_V2     same registry filtered on BBOptionsTickEngineV2; same
#             eod_squareoff contract. Verify: trades table.
#   HA_V1     HA_ENGINE_REGISTRY; eod_squareoff(); its exit path
#             cancel-verifies GTTs (falls back to plain cancel). Verify:
#             trades table.
#   SCALP_V3  manager.eod_squareoff() → close_hedge_trade per row (cancel →
#             verify → sell for live incl. the hedge SL-GTT). NOTE: closes
#             paper rows too — full-stop semantics, acceptable for a kill.
#             Verify: scalp_v3_repo open rows.
#   SCALP_V5  manager.eod_squareoff() → close_trade per row (GTT handled
#             inside). Also closes paper rows. Verify: scalpv5_repo.
#   IC_V1 / IC_V2  (IC_SPLIT: per-instance adapter, same doctrine)
#             gm.kill_all(): drops pending MTC/ADJ, sweeps EVERY leg's GTTs
#             cancel-VERIFIED with ABORT-before-flatten on any survivor
#             (never market-out against an armed GTT), then
#             force_square_off_all("MANUAL") — carried legs close too (kill
#             overrides the 09:16 wait); carry snapshot cleared by
#             housekeeping. Eligible also when a LIVE group rides under a
#             non-LIVE config. Verify: gm.has_open_group() + trades table.
#   PST_SELL / PST_HEDGE  managers from pst_selection_loop.get_managers(),
#             filtered on m._sid; NO GTTs; m.force_eod(now) → _close_all →
#             live legs cancel resting TP + market buy-back (the
#             PST_FILL_TIMEOUT machinery). Verify: m.open_legs empty.
#   TMA_V1    manager.kill_close(now) — ADDITIVE method (force_eod
#             deliberately no-ops positional non-expiry carry; kill
#             overrides). Forced exit path cancel-verifies the sell-leg GTT
#             first; unverified → its own retry semantics keep the group
#             open (reported as stuck, mode NOT flipped). Verify:
#             manager.group is None.
#
#   SCALP_V2 / SCALP_V4: NOT in this tree (no backend references in the
#   public repo). Per the never-reconstruct-from-memory rule there is no
#   adapter here — send me their manager files, or add a local adapter via
#   register_adapter() (template at the bottom).
#
# Concurrency: one lock per strategy; a kill already in flight returns
# {"ok": False, "error": "IN_FLIGHT"} instead of double-firing exits.
# ============================================================================

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config, save_strategy_config

IST = timezone(timedelta(minutes=330))

KILL_STRATEGIES = [
    "SCALP_V1", "BB_V1", "BB_V2", "HA_V1", "SCALP_V3", "SCALP_V5",
    "IC_V1", "IC_V2", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
    "VET_V1",   # static adapter below (works even if the loop never armed)
    "TSG_V1",   # LD7: adapter registered by tsg_runtime at boot,
]

_LOCKS: Dict[str, threading.Lock] = {sid: threading.Lock() for sid in KILL_STRATEGIES}


# ============================================================================
# helpers
# ============================================================================

def _mode(sid: str) -> str:
    try:
        cfg = load_strategy_config(sid) or {}
        return str(cfg.get("trade_execution_mode") or "OFF").upper()
    except Exception as e:
        write_audit_log(f"[KILL][{sid}][CFG_READ_ERR] {e!r}")
        return "OFF"


def _flip_to_paper(sid: str) -> bool:
    """Atomic mode flip (same tempfile+fsync+replace path Settings uses).
    Called ONLY after the adapter verified flat."""
    try:
        cfg = load_strategy_config(sid) or {}
        prev = cfg.get("trade_execution_mode")
        cfg["trade_execution_mode"] = "PAPER"
        save_strategy_config(sid, cfg)
        write_audit_log(f"[KILL][{sid}] mode {prev} → PAPER")
        return True
    except Exception as e:
        write_audit_log(f"[KILL][{sid}][MODE_FLIP_FAIL] {e!r}")
        return False


def _open_trades_rows(sid: str) -> int:
    """Open LIVE rows in the shared trades table (SCALP_V1/BB/HA/IC)."""
    try:
        from app.db.trades_repo import get_open_trades_for_strategy
        return len(get_open_trades_for_strategy(sid) or [])
    except Exception as e:
        write_audit_log(f"[KILL][{sid}][DB_CHECK_ERR] {e!r}")
        return -1   # unknown → treated as NOT verifiably flat


def _notify_critical(msg: str):
    try:
        from app.api.telegram_api import notify_critical
        notify_critical({"message": msg, "severity": "error"})
    except Exception:
        pass


# ============================================================================
# adapters — each returns {"closed": int, "remaining": int, "detail": [...]}
# "remaining" is the strategy's OWN verified count of still-open live
# exposure; -1 means it could not be verified (treated as stuck).
# ============================================================================

def _kill_scalp_v1() -> dict:
    from app.jobs.scalp_live_eod import _squareoff_live_trades
    closed = _squareoff_live_trades()
    remaining = _open_trades_rows("SCALP_V1")
    return {"closed": closed, "remaining": remaining, "detail": []}


def _bb_registry():
    from app.core.engine_registry import BB_ENGINE_REGISTRY
    return BB_ENGINE_REGISTRY


def _kill_bb(sid: str) -> dict:
    """BB_V1 / BB_V2 — additive orchestration ONLY: existing registry +
    existing eod_squareoff. No BB file is modified by the kill switch."""
    from app.engine.bb_options.bb_tick_engine import BBOptionsTickEngine
    try:
        from app.engine.bb_v2.bb_tick_engine_v2 import BBOptionsTickEngineV2
    except Exception:
        BBOptionsTickEngineV2 = ()   # V2 not deployed → V1-only filter
    want_v2 = (sid == "BB_V2")
    engines = [
        e for e in _bb_registry()
        if (isinstance(e, BBOptionsTickEngineV2) if want_v2
            else isinstance(e, BBOptionsTickEngine))
    ]
    detail = []
    for e in engines:
        try:
            e.eod_squareoff()
            detail.append(f"engine id={id(e)} squareoff ok")
        except Exception as ex:
            write_audit_log(f"[KILL][{sid}][ENGINE_ERR] id={id(e)} {ex!r}")
            detail.append(f"engine id={id(e)} ERROR {ex!r}")
    remaining = _open_trades_rows(sid)
    return {"closed": len(engines), "remaining": remaining, "detail": detail}


def _kill_ha() -> dict:
    from app.core.ha_engine_registry import HA_ENGINE_REGISTRY
    detail = []
    for e in HA_ENGINE_REGISTRY:
        try:
            e.eod_squareoff()
            detail.append(f"engine id={id(e)} squareoff ok")
        except Exception as ex:
            write_audit_log(f"[KILL][HA_V1][ENGINE_ERR] id={id(e)} {ex!r}")
            detail.append(f"engine id={id(e)} ERROR {ex!r}")
    remaining = _open_trades_rows("HA_V1")
    return {"closed": len(HA_ENGINE_REGISTRY), "remaining": remaining, "detail": detail}


def _kill_scalp_v3() -> dict:
    from app.engine.scalp_v3.scalp_v3_selection_loop import get_manager
    from app.db.scalp_v3_repo import get_all_open_v3_trades
    m = get_manager()
    if m is None:
        # engine not running: nothing this process can close — verify via DB
        remaining = len(get_all_open_v3_trades() or [])
        return {"closed": 0, "remaining": remaining,
                "detail": ["manager not running"]}
    closed = m.eod_squareoff()   # closes paper rows too — full stop
    remaining = len(get_all_open_v3_trades() or [])
    return {"closed": closed, "remaining": remaining, "detail": []}


def _kill_scalp_v5() -> dict:
    from app.engine.scalpv5.scalpv5_selection_loop import get_manager
    from app.db.scalpv5_repo import get_all_open_v5_trades
    m = get_manager()
    if m is None:
        remaining = len(get_all_open_v5_trades() or [])
        return {"closed": 0, "remaining": remaining,
                "detail": ["manager not running"]}
    closed = m.eod_squareoff()
    remaining = len(get_all_open_v5_trades() or [])
    return {"closed": closed, "remaining": remaining, "detail": []}


def _kill_ic(sid: str) -> dict:
    # ── IC_SPLIT ── per-instance kill: sid scopes the manager lookup AND
    # the trades-table secondary verification. Doctrine unchanged.
    from app.engine.ic.ic_runtime import get_ic_manager
    gm = get_ic_manager(sid)
    if gm is None:
        remaining = _open_trades_rows(sid)
        return {"closed": 0, "remaining": remaining,
                "detail": ["manager not running"]}
    res = gm.kill_all()
    detail = [f"GTT {s['gtt_id']} on {s['symbol']} ({s['leg_id']}) STILL "
              f"ARMED — delete in Kite, then retry"
              for s in (res.get("stuck_gtts") or [])]
    remaining = res.get("remaining", -1)
    # secondary verification against the live trades table
    db_open = _open_trades_rows(sid)
    if db_open > 0:
        remaining = max(remaining, db_open)
        detail.append(f"{db_open} open IC row(s) still in trades DB")
    return {"closed": res.get("closed", 0), "remaining": remaining,
            "detail": detail}


def _kill_pst(sid: str) -> dict:
    from app.engine.pst.pst_selection_loop import get_managers
    now = int(time.time())
    mine = [m for m in get_managers() if getattr(m, "_sid", None) == sid]
    detail = []
    for m in mine:
        try:
            m.force_eod(now)
            detail.append(f"{sid} manager force_eod ok")
        except Exception as ex:
            write_audit_log(f"[KILL][{sid}][MGR_ERR] {ex!r}")
            detail.append(f"manager ERROR {ex!r}")
    remaining = 0
    for m in mine:
        try:
            remaining += len(getattr(m, "open_legs", []) or [])
        except Exception:
            remaining = -1
            break
    if not mine:
        detail.append("no managers running")
    return {"closed": len(mine), "remaining": remaining, "detail": detail}


def _kill_tma() -> dict:
    from app.engine.tma.tma_selection_loop import get_manager
    m = get_manager()
    if m is None:
        return {"closed": 0, "remaining": 0, "detail": ["manager not running"]}
    had = 1 if getattr(m, "group", None) else 0
    try:
        m.kill_close(int(time.time()))
    except Exception as ex:
        write_audit_log(f"[KILL][TMA_V1][MGR_ERR] {ex!r}")
        return {"closed": 0, "remaining": had,
                "detail": [f"kill_close ERROR {ex!r}"]}
    still = 1 if getattr(m, "group", None) else 0
    detail = []
    if still:
        detail.append("group still open — forced exit is retrying its GTT "
                      "cancel (armed-GTT double-fire guard); retry KILL")
    return {"closed": had - still, "remaining": still, "detail": detail}


def _kill_tma2() -> dict:
    # ── TMA_V2 ── same single-group contract as TMA_V1 (SELL leg + hedge)
    from app.engine.tma2.tma2_selection_loop import get_manager
    m = get_manager()
    if m is None:
        return {"closed": 0, "remaining": 0, "detail": ["manager not running"]}
    had = 1 if getattr(m, "group", None) else 0
    try:
        m.kill_close(int(time.time()))
    except Exception as ex:
        write_audit_log(f"[KILL][TMA_V2][MGR_ERR] {ex!r}")
        return {"closed": 0, "remaining": had,
                "detail": [f"kill_close ERROR {ex!r}"]}
    still = 1 if getattr(m, "group", None) else 0
    detail = []
    if still:
        detail.append("group still open — forced exit is retrying its GTT "
                      "cancel (armed-GTT double-fire guard); retry KILL")
    return {"closed": had - still, "remaining": still, "detail": detail}


def _kill_vet() -> dict:
    # ── VET_V1 ── one position, up to two legs (short + wing). manager.kill
    # closes SHORT FIRST then the wing (never leaves a naked short), and
    # FREEZES the manager against reopening. There is no GTT layer to race —
    # sl/tp are 0 by design — so a single flatten is the whole contract.
    from app.engine.vet.vet_selection_loop import get_manager
    m = get_manager()
    if m is None:
        return {"closed": 0, "remaining": 0, "detail": ["manager not running"]}
    had = 1 if getattr(m, "pos", None) else 0
    try:
        m.kill(int(time.time()))
    except Exception as ex:
        write_audit_log(f"[KILL][VET_V1][MGR_ERR] {ex!r}")
        return {"closed": 0, "remaining": had,
                "detail": [f"kill ERROR {ex!r}"]}
    still = 1 if getattr(m, "pos", None) else 0
    detail = ["manager frozen against re-entry"] if had else []
    return {"closed": had - still, "remaining": still, "detail": detail}


_ADAPTERS: Dict[str, Callable[[], dict]] = {
    "SCALP_V1":  _kill_scalp_v1,
    "BB_V1":     lambda: _kill_bb("BB_V1"),
    "BB_V2":     lambda: _kill_bb("BB_V2"),
    "HA_V1":     _kill_ha,
    "SCALP_V3":  _kill_scalp_v3,
    "SCALP_V5":  _kill_scalp_v5,
    "IC_V1":     lambda: _kill_ic("IC_V1"),
    "IC_V2":     lambda: _kill_ic("IC_V2"),
    "PST_SELL":  lambda: _kill_pst("PST_SELL"),
    "PST_HEDGE": lambda: _kill_pst("PST_HEDGE"),
    "TMA_V1":    _kill_tma,
    "TMA_V2":    _kill_tma2,
    "VET_V1":    _kill_vet,
}


def register_adapter(strategy_id: str, fn: Callable[[], dict]):
    """Local-tree extension point (SCALP_V2 / SCALP_V4): fn must return
    {"closed": int, "remaining": int, "detail": [str, ...]} with "remaining"
    verified against the strategy's own source of truth."""
    _ADAPTERS[strategy_id] = fn
    if strategy_id not in KILL_STRATEGIES:
        KILL_STRATEGIES.append(strategy_id)
        _LOCKS[strategy_id] = threading.Lock()


# ============================================================================
# eligibility + orchestration
# ============================================================================

def _ic_live_group_open(sid: str) -> bool:
    """IC special: group mode is captured at entry — a LIVE group can ride
    under a non-LIVE config, and killing it must stay possible.
    IC_SPLIT: checked per instance."""
    try:
        from app.engine.ic.ic_runtime import get_ic_manager
        gm = get_ic_manager(sid)
        return bool(gm and gm.has_open_group() and not gm.is_paper())
    except Exception:
        return False


def eligibility() -> Dict[str, dict]:
    out = {}
    for sid in KILL_STRATEGIES:
        mode = _mode(sid)
        eligible = (mode == "LIVE")
        reason = "LIVE mode" if eligible else ""
        if sid in ("IC_V1", "IC_V2") and not eligible \
                and _ic_live_group_open(sid):
            eligible = True
            reason = f"LIVE group open (mode {mode})"
        out[sid] = {"eligible": eligible, "mode": mode, "reason": reason,
                    "in_flight": _LOCKS[sid].locked()}
    return out


def kill(strategy_id: str) -> dict:
    """Orchestrate one strategy's kill. Returns the report dict for the UI."""
    sid = (strategy_id or "").upper()
    if sid not in _ADAPTERS:
        return {"ok": False, "error": "UNKNOWN_STRATEGY", "strategy_id": sid}

    elig = eligibility().get(sid) or {}
    if not elig.get("eligible"):
        return {"ok": False, "error": "NOT_LIVE", "strategy_id": sid,
                "mode": elig.get("mode")}

    lock = _LOCKS[sid]
    if not lock.acquire(blocking=False):
        return {"ok": False, "error": "IN_FLIGHT", "strategy_id": sid}
    try:
        write_audit_log(f"[KILL][{sid}] ═══ KILL SWITCH PRESSED ═══")
        record_alert("KILL_SWITCH", f"{sid}: KILL pressed — closing all live "
                     f"exposure and switching to PAPER.",
                     severity="error", strategy_id=sid, mode="live")
        try:
            res = _ADAPTERS[sid]()
        except Exception as e:
            write_audit_log(f"[KILL][{sid}][ADAPTER_ERR] {e!r}")
            _notify_critical(f"{sid} KILL FAILED with {e!r} — verify "
                             f"positions in Kite NOW.")
            return {"ok": False, "error": f"ADAPTER_ERROR: {e!r}",
                    "strategy_id": sid}

        closed = int(res.get("closed") or 0)
        remaining = int(res.get("remaining") if res.get("remaining") is not None else -1)
        detail = list(res.get("detail") or [])

        if remaining == 0:
            flipped = _flip_to_paper(sid)
            if not flipped:
                detail.append("positions flat but MODE FLIP FAILED — "
                              "flip to PAPER manually in Settings")
                _notify_critical(f"{sid} KILL: flat, but the PAPER mode "
                                 f"flip failed — set it manually.")
            write_audit_log(f"[KILL][{sid}] complete closed={closed} "
                            f"mode_flipped={flipped}")
            record_alert("KILL_SWITCH",
                         f"{sid}: KILL complete — {closed} closed, flat"
                         + (", mode → PAPER" if flipped else
                            ", MODE FLIP FAILED"),
                         severity="info" if flipped else "error",
                         strategy_id=sid)
            return {"ok": True, "strategy_id": sid, "closed": closed,
                    "remaining": 0, "mode_flipped": flipped, "detail": detail}

        # stuck (or unverifiable): NO mode flip — never label live as PAPER
        write_audit_log(f"[KILL][{sid}][STUCK] closed={closed} "
                        f"remaining={remaining} detail={detail}")
        _notify_critical(f"{sid} KILL INCOMPLETE — "
                         f"{'unverified' if remaining < 0 else remaining} "
                         f"position(s) still open. Mode NOT flipped. "
                         f"Check Kite, then retry KILL.")
        record_alert("KILL_SWITCH",
                     f"{sid}: KILL INCOMPLETE — "
                     f"{'unverified' if remaining < 0 else remaining} still "
                     f"open; mode NOT flipped. Retry after checking Kite.",
                     severity="error", strategy_id=sid)
        return {"ok": False, "strategy_id": sid, "closed": closed,
                "remaining": remaining, "mode_flipped": False,
                "detail": detail}
    finally:
        lock.release()