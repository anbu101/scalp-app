# backend/app/risk/risk_mtm_guard.py
#
# LIVE MARK-TO-MARKET risk guard — Max Loss / Max Profit ENFORCED MID-TRADE.
# ============================================================================
# This module is ADDITIVE. It does NOT replace strategy_max_loss_guard.py:
#   - strategy_max_loss_guard.evaluate_strategy_risk()  → ENTRY gate
#       (blocks NEW entries once today's REALISED P&L crosses the limit).
#   - risk_mtm_guard.mtm_breach()                        → LIVE EXIT trigger
#       (closes the CURRENTLY OPEN position the instant
#        realised + unrealised MTM crosses the limit).
#
# WHY A SEPARATE COMPUTATION:
#   The entry gate only sums CLOSED trades. To "close as soon as max_loss is
#   hit" we must include the UNREALISED mark-to-market of every open position,
#   because the position that pushes you over the limit hasn't been realised
#   yet. MTM = today's realised (mode-aware, from strategy_max_loss_guard) +
#   sum of open-position MTM.
#
# SIGN CONVENTION (identical to the rest of the codebase):
#   SHORT:  pnl = (entry - ltp) * qty
#   LONG:   pnl = (ltp   - entry) * qty
#
# DAY-BLOCK SEMANTICS (REVISED — live-evaluated, not a sticky latch):
#   The original design set riskblock:<id> on the first breach and treated it
#   as set-once-until-EOD. That caused two problems:
#     (a) Setting max_loss/max_profit to 0 (disable) mid-day, or RAISING the
#         limit after a hit, did NOT un-block — the latch stayed set and the
#         carrier square-off loop spammed every 3s forever.
#     (b) Once positions were flat, the loop kept running on the latch alone.
#
#   NEW MODEL (Decision A = raising/zeroing the limit un-blocks immediately):
#     - is_day_blocked() RE-VALIDATES the latch against the CURRENT limits and
#       today's realised P&L every time it's called. The latch is necessary but
#       NOT sufficient: if limits are now disabled (both 0), or realised P&L is
#       no longer beyond the current limit, the latch is STALE → it is cleared
#       and is_day_blocked() returns False. So changing the limit takes effect
#       on the very next check.
#     - The square-off latches (maxloss_sq / maxprofit_sq) are also cleared when
#       the block clears, so a later genuine breach can re-fire its alert.
#     - clear_day_block() is exposed for callers that want to force-clear.
#
#   The carrier loops additionally only KEEP squaring off while positions are
#   actually open (they pass their live book), so a flat, blocked strategy does
#   nothing — no spam. See each engine's _maybe_mtm_squareoff.
#
# FAIL-SAFE PHILOSOPHY (opposite of the entry gate, ON PURPOSE):
#   The entry gate FAILS CLOSED (blocks on uncertainty). The MTM exit FAILS
#   OPEN (does NOT trigger on uncertainty) — force-closing a live position on a
#   STALE / PHANTOM price is worse than waiting one more cycle.
# ============================================================================

from typing import Optional, List, Tuple

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.marketdata.ltp_store import LTPStore
from app.event_bus.inapp_events import (
    record_alert_once,
    is_alert_active,
    clear_alert_once,
)
from app.risk.strategy_max_loss_guard import (
    today_realised_pnl,
    _strategy_mode,
    REASON_OK,
    REASON_MAX_LOSS,
    REASON_MAX_PROFIT,
)


# Result reasons (same vocabulary as the entry gate).
REASON_MTM_OK         = None
REASON_MTM_MAX_LOSS   = REASON_MAX_LOSS      # "MAX_LOSS_HIT"
REASON_MTM_MAX_PROFIT = REASON_MAX_PROFIT    # "MAX_PROFIT_HIT"


# ---------------------------------------------------------------------------
# Latch keys
# ---------------------------------------------------------------------------

def _sq_loss_key(strategy_id: str) -> str:
    return f"maxloss_sq:{strategy_id}"


def _sq_profit_key(strategy_id: str) -> str:
    return f"maxprofit_sq:{strategy_id}"


def _day_block_key(strategy_id: str) -> str:
    return f"riskblock:{strategy_id}"


def clear_day_block(strategy_id: str) -> None:
    """
    Force-clear the day-block AND the square-off latches for a strategy, so a
    later genuine breach can fire fresh. Used internally when a stale block is
    detected (limit disabled / raised), and callable externally if needed.
    Never raises.
    """
    try:
        clear_alert_once(_day_block_key(strategy_id))
        clear_alert_once(_sq_loss_key(strategy_id))
        clear_alert_once(_sq_profit_key(strategy_id))
    except Exception:
        pass


def is_day_blocked(strategy_id: str) -> bool:
    """
    True if this strategy is CURRENTLY blocked from re-entry due to an MTM
    breach today — RE-VALIDATED against the current limits and realised P&L.

    The latch (riskblock:<id>) is necessary but not sufficient:
      - If the latch isn't set → not blocked.
      - If the latch IS set but the limits are now disabled (both 0), OR
        realised P&L is no longer beyond the current limit, the latch is STALE.
        We clear it (and the square-off latches) and return False. This is what
        makes "set limit to 0" or "raise the limit" un-block immediately
        (Decision A), and stops the carrier square-off loop from spinning.

    Note: this re-validates on REALISED P&L (closed trades), which is the right
    basis for the re-entry decision — after a square-off the breaching position
    is closed, so realised P&L is the durable figure. If realised is still
    beyond the current limit, the block legitimately stands.
    """
    if not is_alert_active(_day_block_key(strategy_id)):
        return False

    # Latch is set — re-validate it against the CURRENT config + realised P&L.
    max_loss, max_profit = _limits(strategy_id)

    # Config read error → be conservative and keep the block (do not un-block
    # on a transient read failure).
    if max_loss is None:
        return True

    # Limits disabled entirely → block is stale, clear it.
    if max_loss <= 0 and max_profit <= 0:
        write_audit_log(
            f"[MTM][DAY_BLOCK_CLEARED] {strategy_id} — limits disabled (0); "
            f"re-entry unblocked"
        )
        clear_day_block(strategy_id)
        return False

    realised = today_realised_pnl(strategy_id)
    if realised is None:
        # Can't determine P&L → keep the block (conservative).
        return True

    # Still beyond a currently-enabled limit? Block legitimately stands.
    if max_loss > 0 and realised <= -max_loss:
        return True
    if max_profit > 0 and realised >= max_profit:
        return True

    # Latch set, but realised P&L is back inside the (possibly raised) limits →
    # stale latch. Clear and un-block.
    write_audit_log(
        f"[MTM][DAY_BLOCK_CLEARED] {strategy_id} — realised P&L ₹{realised:,.0f} "
        f"now within limits (loss −₹{max_loss:,.0f} / profit ₹{max_profit:,.0f}); "
        f"re-entry unblocked"
    )
    clear_day_block(strategy_id)
    return False


# ---------------------------------------------------------------------------
# Limits (positive rupee magnitudes; 0 = disabled). Mirrors the entry guard.
# ---------------------------------------------------------------------------

def _limits(strategy_id: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        cfg = load_strategy_config(strategy_id)
        ml = float(cfg.get("max_loss", 0) or 0)
        mp = float(cfg.get("max_profit", 0) or 0)
        return abs(ml), abs(mp)
    except Exception as e:
        write_audit_log(f"[MTM][ERROR] limit fetch failed STRATEGY={strategy_id} ERR={e}")
        return None, None


# ---------------------------------------------------------------------------
# LTP resolution: LTPStore first, REST fallback. Returns None if unresolvable.
# An unresolvable LTP makes the whole MTM INDETERMINATE (fail-open).
# ---------------------------------------------------------------------------

def _resolve_ltp(symbol: str, executor=None) -> Optional[float]:
    ltp = LTPStore.get(symbol)
    if ltp and ltp > 0:
        return float(ltp)

    if executor is not None:
        try:
            data_kite = executor.broker_manager.get_data_kite()
            if data_kite:
                quote = data_kite.ltp(f"NFO:{symbol}")
                rest = quote.get(f"NFO:{symbol}", {}).get("last_price")
                if rest and rest > 0:
                    LTPStore.update(symbol, rest)
                    return float(rest)
        except Exception as e:
            write_audit_log(f"[MTM][LTP_REST_FAIL] {symbol} ERR={e}")

    return None


def _pos_pnl(entry: float, ltp: float, qty: int, direction: str) -> float:
    if (direction or "LONG").upper() == "SHORT":
        return (float(entry) - float(ltp)) * int(qty)
    return (float(ltp) - float(entry)) * int(qty)


# ---------------------------------------------------------------------------
# Open-position collectors. Each returns a list of (symbol, entry, qty, dir)
# or None to signal "cannot enumerate" (treated as indeterminate -> fail-open).
# ---------------------------------------------------------------------------

def _open_positions_paper(strategy_id: str):
    """Open paper positions from the DB (used by SCALP_V1/BB/HA in PAPER)."""
    try:
        from app.db.paper_trades_repo import get_all_open_paper_trades
        rows = get_all_open_paper_trades(strategy_id)
    except Exception as e:
        write_audit_log(f"[MTM][PAPER_ENUM_FAIL] {strategy_id} ERR={e}")
        return None

    out = []
    for r in rows:
        sym = r.get("symbol")
        entry = r.get("entry_price")
        qty = r.get("qty")
        direction = r.get("trade_direction") or "LONG"
        if not sym or entry is None or not qty:
            continue
        out.append((sym, float(entry), int(qty), direction))
    return out


def _open_positions_scalp_v1_live():
    """SCALP_V1 live slots from TradeStateManager registry."""
    try:
        from app.trading.trade_state_manager import TradeStateManager
        slots = TradeStateManager._REGISTRY.get("SCALP_V1", {})
    except Exception as e:
        write_audit_log(f"[MTM][SCALP_V1_ENUM_FAIL] ERR={e}")
        return None

    out = []
    for slot_name, mgr in slots.items():
        # An entry that is locked but not yet recorded (fill-confirm in flight)
        # has selection_locked=True and active_trade=None. Skip it — its MTM is
        # unknowable until the fill lands, and force-closing now would race the
        # fill-confirm thread.
        trade = getattr(mgr, "active_trade", None)
        if trade is None:
            continue
        out.append((
            trade.symbol,
            float(trade.buy_price),
            int(trade.qty),
            getattr(trade, "trade_direction", "LONG") or "LONG",
        ))
    return out


def _open_positions_bb_live(ce_state, pe_state):
    """BB live positions from the two state managers (LONG). Includes leg2."""
    out = []
    for state in (ce_state, pe_state):
        if not state:
            continue
        for attr in ("active_trade", "active_trade_leg2"):
            t = getattr(state, attr, None)
            if t and getattr(t, "symbol", None) and getattr(t, "qty", 0):
                out.append((t.symbol, float(t.entry_price), int(t.qty), "LONG"))
    return out


def _open_positions_ha_live(trade_manager):
    """HA live positions from HATradeManager._live (LONG)."""
    out = []
    try:
        live = dict(getattr(trade_manager, "_live", {}) or {})
    except Exception:
        return None
    for side, t in live.items():
        if t and getattr(t, "symbol", None) and getattr(t, "qty", 0):
            out.append((t.symbol, float(t.entry_price), int(t.qty), "LONG"))
    return out


# ---------------------------------------------------------------------------
# PUBLIC: breach check for the generic strategies (SCALP_V1 / BB / HA).
# ---------------------------------------------------------------------------

def _evaluate(strategy_id: str, positions, executor=None) -> str:
    """
    Shared breach evaluation. Returns REASON_MTM_MAX_LOSS /
    REASON_MTM_MAX_PROFIT (and latches + day-blocks on the first crossing),
    else REASON_MTM_OK. Never raises a square-off on indeterminate data.
    """
    max_loss, max_profit = _limits(strategy_id)
    if max_loss is None:                      # config read error
        return REASON_MTM_OK                  # fail OPEN on the exit path
    if max_loss <= 0 and max_profit <= 0:
        return REASON_MTM_OK                  # nothing to enforce

    # Already squared off today AND still legitimately blocked → nothing to do.
    # is_day_blocked() now self-clears a stale latch (limit disabled/raised), so
    # this no longer pins the strategy forever.
    if is_day_blocked(strategy_id):
        return REASON_MTM_OK

    realised = today_realised_pnl(strategy_id)
    if realised is None:
        # Realised P&L unavailable → indeterminate → fail open (do not close).
        write_audit_log(f"[MTM][INDETERMINATE] {strategy_id} realised P&L unavailable")
        return REASON_MTM_OK

    unrealised, indeterminate = _unrealised_mtm(positions, executor=executor)
    if indeterminate:
        return REASON_MTM_OK

    mtm = realised + unrealised

    if max_loss > 0 and mtm <= -max_loss:
        fired = record_alert_once(
            _sq_loss_key(strategy_id),
            "MAX_LOSS_SQUAREOFF",
            f"{strategy_id} hit daily MAX-LOSS on live MTM "
            f"(MTM ₹{mtm:,.0f}, realised ₹{realised:,.0f}, "
            f"open ₹{unrealised:,.0f}, limit −₹{max_loss:,.0f}) — "
            f"squaring off open position(s) now. New entries blocked for the session.",
            severity="warning",
            strategy_id=strategy_id,
        )
        # Set the hard re-entry day-block regardless of whether the alert was
        # the transition (idempotent: is_alert_active gate makes repeats no-ops).
        record_alert_once(_day_block_key(strategy_id), "RISK_DAY_BLOCK",
                          f"{strategy_id} re-entry blocked for the session (MTM max-loss).",
                          severity="info", strategy_id=strategy_id)
        if fired:
            write_audit_log(
                f"[MTM][BREACH][MAX_LOSS] {strategy_id} mtm={mtm:.2f} "
                f"realised={realised:.2f} unrealised={unrealised:.2f} "
                f"limit=-{max_loss:.2f}"
            )
        return REASON_MTM_MAX_LOSS

    if max_profit > 0 and mtm >= max_profit:
        fired = record_alert_once(
            _sq_profit_key(strategy_id),
            "MAX_PROFIT_SQUAREOFF",
            f"{strategy_id} hit daily MAX-PROFIT on live MTM "
            f"(MTM ₹{mtm:,.0f}, realised ₹{realised:,.0f}, "
            f"open ₹{unrealised:,.0f}, target ₹{max_profit:,.0f}) — "
            f"squaring off open position(s) now. New entries blocked for the session.",
            severity="info",
            strategy_id=strategy_id,
        )
        record_alert_once(_day_block_key(strategy_id), "RISK_DAY_BLOCK",
                          f"{strategy_id} re-entry blocked for the session (MTM max-profit).",
                          severity="info", strategy_id=strategy_id)
        if fired:
            write_audit_log(
                f"[MTM][BREACH][MAX_PROFIT] {strategy_id} mtm={mtm:.2f} "
                f"realised={realised:.2f} unrealised={unrealised:.2f} "
                f"target={max_profit:.2f}"
            )
        return REASON_MTM_MAX_PROFIT

    return REASON_MTM_OK


# ---------------------------------------------------------------------------
# Strategy-specific entry points (called from each carrier loop).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OPEN-BOOK helpers for carrier loops (Decision B: stop the loop when flat).
#
# A day-blocked strategy with NO open positions has nothing to square off, so
# its 3s carrier loop should do NOTHING (no work, no log) rather than calling
# the EOD path on an empty book every cycle. These return True only if there is
# at least one genuinely open position to act on. They reuse the SAME
# enumerators _evaluate uses, so "is there anything open?" is answered exactly
# the way the breach math counts positions. A None (cannot-enumerate) result is
# treated as "assume open" so we never silently skip a real square-off on a
# transient enumeration failure.
# ---------------------------------------------------------------------------

def _has_open(positions) -> bool:
    # None => could not enumerate => be safe and assume there may be something
    # open (let the loop run; _evaluate itself fails open on indeterminate).
    if positions is None:
        return True
    return len(positions) > 0


def has_open_positions_scalp_v1() -> bool:
    sid = "SCALP_V1"
    if _strategy_mode(sid) == "PAPER":
        return _has_open(_open_positions_paper(sid))
    return _has_open(_open_positions_scalp_v1_live())


def has_open_positions_ha(trade_mode: str, trade_manager) -> bool:
    sid = "HA_V1"
    if trade_mode == "PAPER":
        return _has_open(_open_positions_paper(sid))
    elif trade_mode == "LIVE":
        return _has_open(_open_positions_ha_live(trade_manager))
    else:  # OFF — whatever is actually open, prefer the live dict then paper
        live = _open_positions_ha_live(trade_manager)
        if live is None:
            return _has_open(_open_positions_paper(sid))
        if live:
            return True
        return _has_open(_open_positions_paper(sid))


# ---------------------------------------------------------------------------

def mtm_breach_scalp_v1(executor=None) -> str:
    """SCALP_V1, mode-aware: live slots OR open paper rows."""
    sid = "SCALP_V1"
    if _strategy_mode(sid) == "PAPER":
        positions = _open_positions_paper(sid)
    else:
        positions = _open_positions_scalp_v1_live()
    return _evaluate(sid, positions, executor=executor)


def mtm_breach_bb(strategy_id: str, trade_mode: str, ce_state, pe_state,
                  executor=None) -> str:
    """
    BB_V1 / BB_V2. strategy_id distinguishes them; state managers and the
    paper DB rows are already strategy-scoped, so BB_V1 and BB_V2 never see
    each other's positions.
    """
    if trade_mode == "PAPER":
        positions = _open_positions_paper(strategy_id)
    else:
        positions = _open_positions_bb_live(ce_state, pe_state)
    return _evaluate(strategy_id, positions, executor=executor)


def mtm_breach_ha(trade_mode: str, trade_manager, executor=None) -> str:
    sid = "HA_V1"
    if trade_mode == "PAPER":
        positions = _open_positions_paper(sid)
    elif trade_mode == "LIVE":
        positions = _open_positions_ha_live(trade_manager)
    else:  # OFF — manage whatever is actually open, prefer live dict
        positions = _open_positions_ha_live(trade_manager) or _open_positions_paper(sid)
    return _evaluate(sid, positions, executor=executor)