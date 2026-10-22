# backend/app/jobs/broker_session_watch.py
# ── BROKER_SESSION_WATCH_20261022 ──
# ============================================================================
# 2026-10-22: Kite was not logged in. Nothing said so until a human looked at
# the header chip at 09:35 — by then IC_V2's 09:16 carry close had already run
# with no prices, and TMA/TMA2/VET were waiting silently for a session. With
# LIVE strategies that is an unmanaged book.
#
# This job is the alarm. Trading days only, every 2 min, 08:45–15:30 IST:
#   * not logged in at/after 08:45 → WARNING  (bell + Telegram)
#   * still not logged in at 09:10 → CRITICAL (bell + Telegram), naming the
#     open positions and the LIVE strategies that depend on the session
#   * session LOST mid-day (was ready, now not) → CRITICAL
#   * logged in but trading DISABLED while a strategy is LIVE → WARNING
#   * session back after any of the above → INFO
# Each alert fires once per transition per day — never a flood.
#
# Signal = the same check that drives the header's "No Broker" chip
# (zerodha_manager.is_trade_ready()), after the accessor's token-rotation
# check so a fresh morning login is seen immediately. Never raises.
# ============================================================================

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

try:
    from app.event_bus.audit_logger import write_audit_log
except Exception:                                    # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_START_MIN = 8 * 60 + 45
ESCALATE_MIN = 9 * 60 + 10
WINDOW_END_MIN = 15 * 60 + 30

_STATE: Dict = {}


def _fresh_state(day: str) -> Dict:
    return {"day": day, "warned": False, "critical": False, "was_ready": False,
            "lost": False, "td_warned": False}


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def evaluate(now_min: int, *, logged_in: bool, trade_ready: bool,
             live_ids: List[str], open_summary: str,
             state: Dict) -> List[Tuple[str, str]]:
    """Pure decision. Returns [(severity, message)] and mutates state."""
    out: List[Tuple[str, str]] = []
    if not (WINDOW_START_MIN <= now_min <= WINDOW_END_MIN):
        return out
    pos = f" Open positions: {open_summary}." if open_summary else ""
    live = (f" LIVE strategies affected: {', '.join(live_ids)}."
            if live_ids else "")

    if trade_ready:
        if state["warned"] or state["critical"] or state["lost"] or state["td_warned"]:
            out.append(("info", f"Kite session ready at {_hm(now_min)} — "
                                f"strategies can price and trade again."))
            state.update(warned=False, critical=False, lost=False, td_warned=False)
        state["was_ready"] = True
        return out

    if logged_in:
        # token valid, trading switched off — only matters when something is LIVE
        if live_ids and not state["td_warned"]:
            state["td_warned"] = True
            out.append(("warning", f"Kite is logged in but TRADING IS DISABLED "
                                   f"(Connections) — LIVE strategies cannot "
                                   f"place orders.{live}{pos}"))
        return out

    if state["was_ready"]:
        if not state["lost"]:
            state["lost"] = True
            out.append(("critical", f"Kite session LOST at {_hm(now_min)} — "
                                    f"strategies have no prices and cannot place "
                                    f"orders. Log in to Kite again now.{live}{pos}"))
        return out

    if now_min >= ESCALATE_MIN:
        if not state["critical"]:
            state["critical"] = state["warned"] = True
            out.append(("critical", f"Kite STILL NOT LOGGED IN at {_hm(now_min)} — "
                                    f"the market opens 09:15. Carried positions "
                                    f"cannot be closed or marked until you log "
                                    f"in.{live}{pos}"))
        return out
    if not state["warned"]:
        state["warned"] = True
        out.append(("warning", f"Kite not logged in yet ({_hm(now_min)}) — log "
                               f"in before 09:15.{live}{pos}"))
    return out


# ── real-world signal readers (each fail-soft) ──────────────────────────────
def _live_strategy_ids() -> List[str]:
    try:
        from app.strategy.strategy_registry import STRATEGIES
        from app.risk import execution_modes as xm
        from app.license import license_state
    except Exception:
        return []
    out = []
    for sid in STRATEGIES:
        try:
            if (license_state.license_allows_strategy(sid)
                    and xm.wants_live(xm.strategy_mode(sid))):
                out.append(sid)
        except Exception:
            continue
    return out


_OPEN_QUERIES = [
    ("SELECT strategy_name, COUNT(*) FROM paper_trades WHERE state='OPEN' "
     "GROUP BY strategy_name", None),
    ("SELECT strategy_id, COUNT(*) FROM trades WHERE exit_time IS NULL "
     "GROUP BY strategy_id", None),
    ("SELECT COUNT(*) FROM tma_trades WHERE status='OPEN'", "TMA_V1"),
    ("SELECT COUNT(*) FROM tma2_trades WHERE status='OPEN'", "TMA_V2"),
    ("SELECT COUNT(*) FROM vet_trades WHERE status='OPEN'", "VET_V1"),
]


def _open_summary(db_path: Optional[str] = None) -> str:
    try:
        if db_path is None:
            from app.utils.app_paths import DB_PATH
            db_path = str(DB_PATH)
        c = sqlite3.connect(db_path, timeout=5)
    except Exception:
        return ""
    counts: Dict[str, int] = {}
    try:
        for q, sid in _OPEN_QUERIES:
            try:
                rows = c.execute(q).fetchall()
            except Exception:
                continue
            for r in rows:
                k, n = (sid, r[0]) if sid else (r[0], r[1])
                if k and n:
                    counts[str(k)] = max(counts.get(str(k), 0), int(n))
    finally:
        c.close()
    return ", ".join(f"{k} {v} leg{'s' if v != 1 else ''}"
                     for k, v in sorted(counts.items()))


def _emit(severity: str, msg: str) -> None:
    write_audit_log(f"[BROKER_WATCH][{severity.upper()}] {msg}")
    if severity == "critical":
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"message": msg, "severity": "error"})   # mirrors to bell
        except Exception as e:
            write_audit_log(f"[BROKER_WATCH] critical notify failed: {e!r}")
        return
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert("BROKER_SESSION", msg, severity=severity)
    except Exception:
        pass
    try:
        from app.api.telegram_api import notify_system_alert
        notify_system_alert({"message": msg, "severity": severity})
    except Exception as e:
        write_audit_log(f"[BROKER_WATCH] notify failed: {e!r}")


def broker_session_watch_job(manager=None) -> None:
    try:
        from app.utils.market_hours import is_trading_day
        if not is_trading_day():
            return
        now = datetime.now(IST)
        now_min = now.hour * 60 + now.minute
        if not (WINDOW_START_MIN <= now_min <= WINDOW_END_MIN):
            return
        day = now.date().isoformat()
        if _STATE.get("day") != day:
            _STATE.clear()
            _STATE.update(_fresh_state(day))

        trade_ready = False
        if manager is not None:
            try:
                manager.get_kite()               # TOKEN_ROTATE: see a fresh login now
            except Exception:
                pass
            try:
                trade_ready = bool(manager.is_trade_ready())
            except Exception:
                trade_ready = False
        logged_in = trade_ready
        if not trade_ready:
            try:
                from app.brokers.zerodha_auth import is_token_valid
                logged_in = bool(is_token_valid())
            except Exception:
                logged_in = False

        needs_context = not trade_ready
        alerts = evaluate(now_min, logged_in=logged_in, trade_ready=trade_ready,
                          live_ids=_live_strategy_ids() if needs_context else [],
                          open_summary=_open_summary() if needs_context else "",
                          state=_STATE)
        for sev, msg in alerts:
            _emit(sev, msg)
    except Exception as e:
        try:
            write_audit_log(f"[BROKER_WATCH][ERROR] {e!r}")
        except Exception:
            pass
