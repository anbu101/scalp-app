# backend/app/risk/pnl_watch_loop.py
#
# Background P&L watcher (VISIBILITY ONLY).
# ============================================================================
# Enforcement of Max Loss / Max Profit happens at each strategy's ENTRY gate
# (block-only). This loop does NOT block or close anything — it periodically
# logs when a strategy has crossed a limit, so it's visible in the audit log /
# Telegram even between entry attempts.
#
# FIX: the previous version called check_strategy_max_loss() with NO argument,
# which raised TypeError every tick (silently swallowed) — so this loop did
# nothing. It now iterates the known strategies and logs limit state.
# ============================================================================

import asyncio

from app.risk.strategy_max_loss_guard import (
    evaluate_strategy_risk,
    today_realised_pnl,
    REASON_OK,
)
from app.event_bus.audit_logger import write_audit_log


# Strategies to monitor. Kept in sync with the app's strategy set.
_WATCHED_STRATEGIES = ["SCALP_V1", "BB_V1", "BB_V2", "HA_V1"]

# Avoid log spam: remember the last reason logged per strategy and only log on
# change (OK -> blocked or blocked -> OK, or reason change).
_last_reason = {}


async def pnl_watch_loop(interval_sec: int = 10):
    """Background P&L watcher. Logs limit crossings; never blocks or closes."""
    while True:
        for sid in _WATCHED_STRATEGIES:
            try:
                reason = evaluate_strategy_risk(sid)
                prev   = _last_reason.get(sid, REASON_OK)
                if reason != prev:
                    if reason is not REASON_OK:
                        pnl = today_realised_pnl(sid)
                        pnl_str = f"{pnl:.2f}" if pnl is not None else "n/a"
                        write_audit_log(
                            f"[RISK][LIMIT] STRATEGY={sid} now BLOCKING reason={reason} "
                            f"today_pnl={pnl_str}"
                        )
                    else:
                        write_audit_log(
                            f"[RISK][LIMIT] STRATEGY={sid} cleared — entries allowed again"
                        )
                    _last_reason[sid] = reason
            except Exception:
                # Never break the loop on a single strategy's error.
                pass

        await asyncio.sleep(interval_sec)