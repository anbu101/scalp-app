# backend/app/risk/strategy_max_loss_guard.py
#
# Per-strategy DAILY risk limits — Max Loss and Max Profit.
# ============================================================================
# User-configurable per strategy via Settings (config keys max_loss /
# max_profit, in rupees).
#
# TWO ENFORCEMENT LAYERS (this file = ENTRY gate; risk_mtm_guard.py = EXIT):
#   - ENTRY gate  (this file): blocks NEW entries once today's REALISED P&L
#     crosses the limit. Open positions are NOT force-closed here.
#   - EXIT trigger (risk_mtm_guard.py): closes the open position the instant
#     realised + UNREALISED MTM crosses the limit, and sets a hard re-entry
#     day-block (riskblock:<id>) that THIS gate now also honors.
#
# CORRECTNESS (why this replaces the old guard):
#   - DAILY scope: only today's CLOSED trades count (old version summed ALL
#     history → strategy would be permanently locked after a bad stretch).
#   - MODE-aware: PAPER strategies measure paper_trades.net_pnl; LIVE
#     strategies measure realised P&L from the trades table.
#   - Direction-aware for live SHORT trades; paper net_pnl is already signed.
#
# Fail-safe: if P&L cannot be determined, block (fail closed).
#
# MODE RESOLUTION SAFETY (the 2026-06-15 paper→live flip postmortem):
#   `_strategy_mode` previously returned "LIVE" on ANY config-read exception.
#   Combined with strategy_loader's old clobber-on-failure, that armed live
#   routing on a transient I/O fault. Both are now fixed:
#     - `_strategy_mode` falls back to "PAPER" on error (never LIVE).
#     - `resolve_execution_mode()` is the AUTHORITATIVE resolver for any code
#       that decides whether to place a LIVE order. It returns LIVE *only* on a
#       clean read whose value is explicitly "LIVE"; every other state (PAPER,
#       OFF, missing key, None, unknown string, exception) resolves to PAPER,
#       and reports whether that PAPER result was a DEGRADED fallback so the
#       caller can alert. Paper-instead-of-anything costs nothing; live-
#       instead-of-anything costs real money — so PAPER is the only safe
#       default.
#
# MID-DAY LIMIT CHANGES (Decision A — un-block immediately):
#   The day-block is no longer a sticky latch. _mtm_day_blocked() routes
#   through risk_mtm_guard.is_day_blocked(), which RE-VALIDATES the latch
#   against the CURRENT limits and realised P&L on every call and self-clears a
#   stale latch. So raising the limit, or setting it to 0 (disable), mid-day
#   un-blocks new entries on the very next gate check — without waiting for EOD.
#
# IN-APP ALERTS (edge-triggered, per strategy):
#   When a strategy first crosses its max-loss or max-profit limit, ONE bell
#   alert fires (record_alert_once keyed "maxloss:<id>" / "maxprofit:<id>").
#   Call reset_strategy_risk_alerts() once at start-of-day / EOD so each
#   strategy can alert again the next session (wired into the EOD jobs). That
#   reset now ALSO clears the MTM square-off latches and the re-entry day-block
#   set by risk_mtm_guard.py.
# ============================================================================

from datetime import datetime, date
from typing import Optional, Tuple

from app.db.sqlite import get_conn
from app.config.strategy_loader import load_strategy_config
from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import (
    record_alert_once,
    reset_alert_keys,
    is_alert_active,
)


# Result reasons
REASON_OK         = None
REASON_MAX_LOSS   = "MAX_LOSS_HIT"
REASON_MAX_PROFIT = "MAX_PROFIT_HIT"
REASON_ERROR      = "RISK_CHECK_ERROR"

# The single string that means "place real orders". Anything else → paper.
_LIVE_MODE = "LIVE"


def _today_midnight_ts() -> int:
    today = date.today()
    return int(datetime(today.year, today.month, today.day, 0, 0, 0).timestamp())


# ---------------------------------------------------------------------------
# Today's realised P&L, mode-aware
# ---------------------------------------------------------------------------

def _today_live_pnl(strategy_id: str) -> Optional[float]:
    """Realised P&L from today's CLOSED live trades (direction-aware)."""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT entry_price, exit_price, qty,
                   COALESCE(trade_direction, 'LONG') AS dir
            FROM trades
            WHERE strategy_id = ?
              AND state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (strategy_id, _today_midnight_ts()),
        ).fetchall()
        total = 0.0
        for entry_price, exit_price, qty, direction in rows:
            if entry_price is None or exit_price is None or qty is None:
                continue
            if direction == "SHORT":
                total += (entry_price - exit_price) * qty
            else:
                total += (exit_price - entry_price) * qty
        return float(total)
    except Exception as e:
        write_audit_log(f"[RISK][ERROR] live pnl fetch failed STRATEGY={strategy_id} ERR={e}")
        return None


def _today_paper_pnl(strategy_id: str) -> Optional[float]:
    """Realised P&L from today's CLOSED paper trades (net_pnl is already signed
    + charge-deducted by close_paper_trade)."""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT net_pnl
            FROM paper_trades
            WHERE strategy_name = ?
              AND state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl    IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') = date('now', 'localtime')
            """,
            (strategy_id,),
        ).fetchall()
        total = 0.0
        for (net_pnl,) in rows:
            total += float(net_pnl or 0.0)
        return float(total)
    except Exception as e:
        write_audit_log(f"[RISK][ERROR] paper pnl fetch failed STRATEGY={strategy_id} ERR={e}")
        return None


# ---------------------------------------------------------------------------
# MODE RESOLUTION
# ---------------------------------------------------------------------------

def _strategy_mode(strategy_id: str) -> str:
    """
    Raw mode string from config, used by risk/P&L code that compares against
    "PAPER" (and, for HA elsewhere, "OFF"). On a config-read error this now
    returns "PAPER" — NEVER "LIVE". Reading the wrong P&L book in paper is
    harmless; defaulting to LIVE on uncertainty was the original sin.

    NOTE: this is NOT the gate for placing live orders. Code that decides
    whether to punch a real order MUST use resolve_execution_mode() below,
    which fails closed to PAPER and reports degraded reads.
    """
    try:
        return load_strategy_config(strategy_id).get("trade_execution_mode", "PAPER")
    except Exception as e:
        write_audit_log(
            f"[RISK][MODE_READ_DEGRADED] {strategy_id}: could not read execution "
            f"mode ({e!r}) — treating as PAPER"
        )
        return "PAPER"


def resolve_execution_mode(strategy_id: str) -> Tuple[str, bool]:
    """
    AUTHORITATIVE resolver for "should this strategy place LIVE orders right now?"

    Returns (mode, degraded) where:
      mode == "LIVE"   ONLY if the config was read cleanly AND
                       trade_execution_mode is exactly the string "LIVE".
      mode == "PAPER"  for EVERY other case — explicit PAPER, OFF, a missing
                       key, None, an unknown string, or a read exception.
      degraded == True ONLY when the config was CONFIGURED for LIVE but we could
                       not confirm it cleanly (read error / unreadable), so we
                       defensively dropped to PAPER. This is the dangerous-glitch
                       case the caller should ALERT on. A clean PAPER/OFF config
                       returns degraded == False (normal, silent).

    Rationale: paper-instead-of-anything costs nothing (a DB row, no broker
    call); live-instead-of-anything costs real money. So LIVE must be an
    explicit, positively-confirmed assertion — never a fallback.
    """
    try:
        cfg = load_strategy_config(strategy_id)
    except Exception as e:
        # Could not read config at all. We cannot confirm LIVE → PAPER.
        # We don't know the user's intent, but the safe assumption when a
        # strategy MIGHT be live is to flag it. Treat as degraded so the caller
        # surfaces it loudly rather than silently missing a live session.
        write_audit_log(
            f"[RISK][MODE_RESOLVE_DEGRADED] {strategy_id}: config unreadable "
            f"({e!r}) — forcing PAPER this call"
        )
        return "PAPER", True

    raw = cfg.get("trade_execution_mode", "PAPER")
    mode = (raw or "PAPER").strip().upper()

    if mode == _LIVE_MODE:
        return "LIVE", False

    # Any non-LIVE clean value (PAPER, OFF, unknown) → PAPER, not degraded.
    return "PAPER", False


def today_realised_pnl(strategy_id: str) -> Optional[float]:
    """Mode-aware today's realised P&L. None on error (caller fails closed).

    Uses the raw _strategy_mode: OFF and PAPER both mean "no live money", so
    the paper book is the correct realised-P&L source for both. Only an
    explicit LIVE reads the live trades table.
    """
    mode = _strategy_mode(strategy_id)
    if mode == "LIVE":
        return _today_live_pnl(strategy_id)
    return _today_paper_pnl(strategy_id)


# ---------------------------------------------------------------------------
# Limits from config
# ---------------------------------------------------------------------------

def _limits(strategy_id: str):
    """Returns (max_loss, max_profit) as positive rupee magnitudes; 0 = disabled."""
    try:
        cfg = load_strategy_config(strategy_id)
        ml = float(cfg.get("max_loss", 0) or 0)
        mp = float(cfg.get("max_profit", 0) or 0)
        return abs(ml), abs(mp)
    except Exception as e:
        write_audit_log(f"[RISK][ERROR] limit fetch failed STRATEGY={strategy_id} ERR={e}")
        # Fail closed: signal error via sentinel (None, None)
        return None, None


# ---------------------------------------------------------------------------
# MTM re-entry day-block bridge (set by risk_mtm_guard.py on an MTM breach)
# ---------------------------------------------------------------------------

def _mtm_day_blocked(strategy_id: str) -> bool:
    """
    True if risk_mtm_guard squared this strategy off on live MTM today AND that
    block is STILL valid against the current limits.

    We delegate to risk_mtm_guard.is_day_blocked(), which re-validates the latch
    every call and self-clears it when the limit has been raised or disabled
    (Decision A). The import is LAZY (inside the function) to avoid a module-
    level import cycle: risk_mtm_guard imports this module at module level, so
    importing it back at module level here would deadlock the import.

    Fallback: if that import/call fails for any reason, fall back to reading the
    raw latch key directly (the pre-existing behaviour) so the entry gate still
    errs on the safe side.
    """
    try:
        from app.risk.risk_mtm_guard import is_day_blocked
        return is_day_blocked(strategy_id)
    except Exception:
        try:
            return is_alert_active(f"riskblock:{strategy_id}")
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Daily reset of the edge-triggered risk alert keys
# ---------------------------------------------------------------------------

def reset_strategy_risk_alerts() -> None:
    """
    Clear the per-strategy risk alert keys so each strategy can act again next
    session. Called from the EOD jobs (and safe to call at start-of-day).
    Clears:
      maxloss:    / maxprofit:     — entry-gate bell alerts
      maxloss_sq: / maxprofit_sq:  — MTM square-off latches (risk_mtm_guard)
      riskblock:                   — MTM re-entry day-block  (risk_mtm_guard)
    Never raises.
    """
    try:
        reset_alert_keys("maxloss:")
        reset_alert_keys("maxprofit:")
        reset_alert_keys("maxloss_sq:")
        reset_alert_keys("maxprofit_sq:")
        reset_alert_keys("riskblock:")
        write_audit_log("[RISK] Daily risk-alert keys reset (entry + MTM + day-block)")
    except Exception as e:
        write_audit_log(f"[RISK][WARN] risk-alert reset failed ERR={e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_strategy_risk(strategy_id: str) -> str:
    """
    Returns a reason string if NEW ENTRIES should be blocked, else None.
      REASON_MAX_LOSS / REASON_MAX_PROFIT / REASON_ERROR  -> block
      None (REASON_OK)                                    -> allowed
    Fail-safe: on any inability to determine P&L or limits, blocks (fail closed).

    NEW: if risk_mtm_guard squared this strategy off on live MTM today, the
    re-entry day-block is honored here (Decision A) — BUT that block now self-
    clears the moment the limit is raised or disabled, so a mid-day limit change
    re-opens entries on the next check.

    Fires a ONE-TIME in-app bell alert per strategy on the first crossing of
    each limit (edge-triggered; reset daily via reset_strategy_risk_alerts()).
    """
    max_loss, max_profit = _limits(strategy_id)
    if max_loss is None:           # error reading config
        return REASON_ERROR

    # Both disabled → nothing to enforce, allow. Checked BEFORE the day-block so
    # that disabling the limits (setting both to 0) un-blocks immediately even
    # if a latch is somehow still present.
    if max_loss <= 0 and max_profit <= 0:
        return REASON_OK

    # Hard re-entry block after an MTM square-off (Decision A). is_day_blocked()
    # re-validates against the current limits and realised P&L and self-clears a
    # stale latch, so this only holds while the block is genuinely live.
    if _mtm_day_blocked(strategy_id):
        return REASON_MAX_LOSS   # any non-OK reason blocks; MAX_LOSS is the safe label

    pnl = today_realised_pnl(strategy_id)
    if pnl is None:                # P&L unavailable → fail closed
        write_audit_log(f"[RISK][WARN] pnl unavailable STRATEGY={strategy_id} — blocking")
        return REASON_ERROR

    if max_loss > 0 and pnl <= -max_loss:
        write_audit_log(f"[RISK][BLOCK] MAX_LOSS STRATEGY={strategy_id} pnl={pnl:.2f} limit=-{max_loss:.2f}")
        record_alert_once(
            f"maxloss:{strategy_id}",
            "MAX_LOSS",
            f"{strategy_id} hit its daily max-loss "
            f"(P&L ₹{pnl:,.0f}, limit −₹{max_loss:,.0f}) — new entries paused for "
            f"the rest of the session. Open positions are still managed to exit.",
            severity="warning",
            strategy_id=strategy_id,
        )
        return REASON_MAX_LOSS

    if max_profit > 0 and pnl >= max_profit:
        write_audit_log(f"[RISK][BLOCK] MAX_PROFIT STRATEGY={strategy_id} pnl={pnl:.2f} limit={max_profit:.2f}")
        record_alert_once(
            f"maxprofit:{strategy_id}",
            "MAX_PROFIT",
            f"{strategy_id} hit its daily max-profit target "
            f"(P&L ₹{pnl:,.0f}, target ₹{max_profit:,.0f}) — new entries paused for "
            f"the rest of the session. Open positions are still managed to exit.",
            severity="info",
            strategy_id=strategy_id,
        )
        return REASON_MAX_PROFIT

    return REASON_OK


# ---------------------------------------------------------------------------
# Back-compat shim
# ---------------------------------------------------------------------------

def check_strategy_max_loss(strategy_id: str) -> bool:
    """
    Backward-compatible boolean: True = block (limit hit or error).
    Now also covers Max Profit, is daily + mode-aware, and honors the MTM
    re-entry day-block. Existing callers (SCALP_V1 on_buy_signal/on_sell_signal)
    keep working.
    """
    return evaluate_strategy_risk(strategy_id) is not REASON_OK