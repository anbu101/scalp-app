# backend/app/engine/scalp_v2/scalp_v2_group_manager.py
#
# SCALP_V2 — Group Manager (v2.0 — "V1 clone + 3-leg split")
# ============================================================================
# REDESIGN (replaces the Class A/B/C / master-election model):
#   SCALP_V2 is now SCALP_V1 upstream (single premium range, same option
#   selection, same per-contract signal generation). It diverges from V1 ONLY
#   at order placement: a single V1 SELL signal is split into THREE legs:
#       Leg 1 (L1) = the SIGNAL strike      — signal's EXACT tp/sl
#       Leg 2 (L2) = +1 strike (+50)        — pct-derived tp/sl off its own entry
#       Leg 3 (L3) = -1 strike (-50)        — pct-derived tp/sl off its own entry
#   All three legs are the SAME side as the signal (CE signal -> 3 CE).
#
# EXIT = L1-MASTER (changed from all-or-nothing):
#   Each leg keeps its OWN GTT (SL/TP).
#     • L2 or L3 crosses its OWN TP/SL  → ONLY that leg exits; L1 and the
#       other sibling keep running; the group stays OPEN.
#     • L1 (the signal strike) crosses its OWN TP/SL → L1 exits AND every
#       remaining open leg is force-closed (L1 is the master trigger).
#   Group finalizes (V2 free to re-elect) only when ALL legs are closed.
#   MTM risk square-off and EOD square-off remain ALL-OR-NOTHING.
#
# SAME-CANDLE SIGNAL ARBITRATION (uniformity across machines):
#   When >1 selected contract fires a SELL on the SAME candle, the SIGNAL strike
#   that becomes L1 (master_instrument — it sets the signal tp/sl AND drives the
#   L1-master cascade exit) must be IDENTICAL on every friend's machine.
#   Previously whichever try_enter thread won the _mutex race claimed the group
#   and became L1 — nondeterministic across machines. Fix: buffer gate-passing
#   same-candle signals, wait a short window, then elect the HIGHEST signal
#   premium (entry_price, the closed-candle premium — identical everywhere) with
#   the symbol string as a stable tie-break. ONLY the elected winner claims the
#   group and fans out. Every existing gate (incl. the single-group claim) is
#   preserved; the arbiter only decides WHICH surviving candidate reaches the
#   claim.
#
# ----------------------------------------------------------------------------
# FILL-RESOLUTION FIX (recorded entry price != broker fill):
#   place_sell_entry returns (order_id, limit_price, qty) — the protected LIMIT
#   price, NOT the fill. Previously _place_leg recorded that limit as
#   leg.entry_price and derived sl/tp from it, then placed the GTT — so the
#   recorded short entry (and its (entry-exit) P&L) was biased by the
#   entry-price error, fanned across 3 legs. It also placed a BUY-back GTT +
#   DB row even when the SELL was rejected (phantom short with a stray BUY GTT).
#
#   NEW per-leg flow:
#     1. SELL entry → (order_id, limit_price, qty).
#     2. Record leg.entry_price = limit_price IMMEDIATELY.
#        SL/TP are computed from the leg's INTENDED entry (L1: signal sl/tp;
#        L2/L3: pct off the sibling premium) — NOT from the fill — exactly as
#        before. They are fill-independent.
#     3. Place the SHORT GTT OCO immediately (protection on within ~1s; no
#        unprotected window, no GTT churn).
#     4. Insert the live DB row, then spawn a background thread that polls the
#        order book for the true fill and UPDATEs that leg's entry_price for
#        accurate P&L; on a dead order it cancels the GTT + closes the row +
#        drops the leg from the group.
#
#   The tick thread is NEVER blocked — fan-out stays synchronous and fast, and
#   fill confirmation happens off-thread. Exit detection keeps running on every
#   tick while fills confirm in the background.
# ----------------------------------------------------------------------------
#
# GATING = one group at a time (a live group blocks any new signal until all
#   its legs close). Mirrors V1's gates: trade_on, daily max-loss/profit,
#   session, dedup, selection-filter.
#
# ISOLATION: TradeStateManager._REGISTRY is NEVER touched. SCALP_V1 / BB / HA
#   are completely unaffected. This manager owns all SCALP_V2 leg state.
#
# Compatibility note: LegState keeps a field named `trade_class` (it now holds
# the leg ROLE "L1"/"L2"/"L3", not a class), so scalp_v2_gtt_monitor.py and
# scalp_v2_live_eod.py need NO changes.
# ============================================================================

import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Set

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.utils.session_utils import is_within_session
from app.risk.strategy_max_loss_guard import check_strategy_max_loss
from app.marketdata.ltp_store import LTPStore
from app.event_bus.inapp_events import record_alert
from app.risk.risk_mtm_guard import mtm_breach_for_group, is_day_blocked

STRATEGY_ID = "SCALP_V2"

# Leg roles (replaces classes A/B/C). Stored in LegState.trade_class so the
# GTT monitor / EOD job keep working unchanged.
LEG_SIGNAL = "L1"   # the signal strike
LEG_UP     = "L2"   # +1 strike
LEG_DOWN   = "L3"   # -1 strike

# Strike step for NIFTY weekly options.
STRIKE_STEP = 50

# A cached LTPStore tick older than this is treated as stale (used for sibling
# pricing primary path; candle-close fallback handles the stale case).
LTP_STALENESS_SEC = 30

# Background per-leg entry-fill confirmation (Option A). Place the leg's SELL,
# then in the background: on COMPLETE place that leg's GTT and record the true
# fill; on DEAD drop the leg; if unfilled at the cancel cap, cancel the order
# (a SCALP signal is valid ~one candle). The GTT is never placed before a
# confirmed fill, so it can't open an unintended position. The fill→GTT window
# is covered by the tick exit.
_ENTRY_FILL_CANCEL_S        = 50    # cancel unfilled leg SELL after 50s
_ENTRY_FILL_POLL_INTERVAL_S = 2

# Terminal "dead" Kite order statuses — order never opened a position.
_DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}

# Same-candle signal arbitration tuning (see header). 0.4s collection window
# from the first same-candle candidate; fired-set bounded over a session.
_SIG_ARB_WINDOW_S  = 0.4
_SIG_ARB_FIRED_MAX = 512

# Group lifecycle states
PENDING = "PENDING"
OPEN    = "OPEN"
CLOSED  = "CLOSED"


# ============================================================================
# Leg state
# ============================================================================

@dataclass
class LegState:
    trade_class:  str            # leg ROLE: "L1" | "L2" | "L3" (name kept for monitor compat)
    symbol:       str
    token:        int
    qty:          int
    entry_price:  float
    sl:           float          # ABOVE entry — premium rising = loss (short)
    tp:           float          # BELOW entry — premium falling = profit (short)
    gtt_id:       Optional[str] = None
    db_trade_id:  Optional[str] = None
    paper:        bool = False
    is_master:    bool = False   # L1 (signal leg) flagged True for display continuity
    open:         bool = True
    exit_price:   Optional[float] = None
    exit_reason:  Optional[str]   = None
    entry_order_id: Optional[str] = None   # SELL order id (for background fill confirm)

    def realized_pnl(self) -> Optional[float]:
        """Direction-aware SHORT P&L: (entry - exit) * qty."""
        if self.exit_price is None:
            return None
        return (self.entry_price - self.exit_price) * self.qty


# ============================================================================
# Group state
# ============================================================================

@dataclass
class GroupState:
    group_id:          str
    direction:         str                 # "CE" or "PE"
    master_class:      str                 # always LEG_SIGNAL ("L1") — kept for persistence/UI
    master_instrument: str                 # the signal symbol
    sl_pct:            float
    tp_pct:            float
    paper:             bool
    status:            str = PENDING
    entry_signal_ts:   Optional[int] = None
    exit_trigger_ts:   Optional[int] = None
    exit_reason:       Optional[str] = None
    legs:              Dict[str, LegState] = field(default_factory=dict)

    def open_legs(self) -> List[LegState]:
        return [lg for lg in self.legs.values() if lg.open]

    def all_closed(self) -> bool:
        return all(not lg.open for lg in self.legs.values())


# ============================================================================
# Group Manager
# ============================================================================

class ScalpV2GroupManager:
    """
    One instance per running SCALP_V2 strategy. Holds the single active group
    (V2 trades one group at a time, gated by all-legs-free).

    Providers (injected by the selection loop):
      executor            : order executor (place_sell_entry, place_gtt_oco
                            (direction="SHORT"), cancel_gtt, place_buy_exit,
                            get_gtts, get_open_positions, get_orders,
                            get_order_fill).
      candidate_provider  : callable(symbol) -> token
      instrument_provider : callable(...) -> instrument dict | None
      selected_provider   : callable() -> (set[str] ce, set[str] pe)
      candle_provider     : callable(symbol) -> float | None  (E1 fallback)
    """

    def __init__(
        self,
        executor,
        candidate_provider,
        instrument_provider,
        selected_provider,
        candle_provider=None,
    ):
        self.executor            = executor
        self.candidate_provider  = candidate_provider
        self.instrument_provider = instrument_provider
        self.selected_provider   = selected_provider
        self.candle_provider     = candle_provider

        self._group: Optional[GroupState] = None
        self._mutex = threading.Lock()        # guards entry + group lifecycle

        # Dedup of (symbol, candle_ts) like V1's SignalRouter._last_routed.
        self._last_routed: Set[Tuple[str, int]] = set()

        # ── same-candle signal arbitration state ──
        self._sig_arb_lock      = threading.Lock()
        self._sig_arb_candle_ts = None
        self._sig_arb_buffer: List[dict] = []
        self._sig_arb_fired: Set[int] = set()

    # --------------------------------------------------------------------
    # Config
    # --------------------------------------------------------------------

    def _cfg(self) -> dict:
        return load_strategy_config(STRATEGY_ID)

    def _is_paper(self) -> bool:
        return self._cfg().get("trade_execution_mode", "LIVE") == "PAPER"

    def _lot_size(self) -> int:
        return int(self._cfg().get("quantity", {}).get("lot_size", 65))

    def _leg_lots(self, role: str) -> int:
        q = self._cfg().get("quantity", {})
        key = {LEG_SIGNAL: "leg1_lots", LEG_UP: "leg2_lots", LEG_DOWN: "leg3_lots"}[role]
        # Fall back to a generic "lots" if per-leg not set, else 0.
        return int(q.get(key, q.get("lots", 0)))

    def current_group(self):
        return self._group

    # --------------------------------------------------------------------
    # Premium read: FRESH LTPStore tick, else candle-close fallback (E1)
    # --------------------------------------------------------------------

    def _live_premium(self, symbol: str) -> Optional[float]:
        """
        Sibling/exit price resolution: a FRESH LTPStore tick (<30s) is primary;
        if absent, fall back to the last candle close (E1 decision). Returns
        None only if neither is available.
        """
        try:
            result = LTPStore.get_with_timestamp(symbol)
            if result is not None:
                ltp, ts = result
                if ltp and ltp > 0 and ts is not None:
                    if (time.time() - ts) <= LTP_STALENESS_SEC:
                        return float(ltp)
        except Exception as e:
            write_audit_log(f"[V2][PREMIUM] LTPStore read failed {symbol} ERR={e}")

        if self.candle_provider is not None:
            try:
                c = self.candle_provider(symbol)
                if c and c > 0:
                    return float(c)
            except Exception as e:
                write_audit_log(f"[V2][PREMIUM] candle fallback failed {symbol} ERR={e}")
        return None

    # --------------------------------------------------------------------
    # ENTRY GATES (ported from V1 SignalRouter._common_gates + on_sell_signal)
    # --------------------------------------------------------------------

    def _gates_pass(self, symbol: str, candle_ts: int) -> bool:
        cfg = self._cfg()

        # MTM day-block: if we squared off on MTM today, no new group all day.
        if is_day_blocked(STRATEGY_ID):
            write_audit_log("[V2][ENTRY] MTM_DAY_BLOCK → drop")
            return False
    
        # 1. global trade_on
        try:
            if not load_global_config().get("trade_on", False):
                write_audit_log("[V2][ENTRY] trade_on=FALSE → drop")
                return False
        except Exception:
            return False

        # 2. daily max-loss / max-profit guard (mode-aware, today-scoped)
        try:
            if check_strategy_max_loss(STRATEGY_ID):
                write_audit_log("[V2][ENTRY] RISK_LIMIT_HIT → drop")
                return False
        except Exception:
            return False  # fail closed

        # 3. session window
        session_cfg = cfg.get("session", {}).get("primary", {})
        if session_cfg:
            if not is_within_session(
                datetime.now(), session_cfg.get("start"), session_cfg.get("end")
            ):
                write_audit_log("[V2][ENTRY] OUTSIDE_SESSION → drop")
                return False

        # 4. dedup (symbol, candle_ts)
        key = (symbol, candle_ts)
        if key in self._last_routed:
            write_audit_log("[V2][ENTRY] DUPLICATE_SIGNAL → drop")
            return False

        # 5. selection filter — signal symbol must be in SCALP_V2's selection
        try:
            ce_sel, pe_sel = self.selected_provider()
            is_ce = symbol.endswith("CE")
            if (ce_sel or pe_sel):
                if is_ce and symbol not in ce_sel:
                    write_audit_log("[V2][ENTRY] CE_NOT_SELECTED → drop")
                    return False
                if (not is_ce) and symbol not in pe_sel:
                    write_audit_log("[V2][ENTRY] PE_NOT_SELECTED → drop")
                    return False
        except Exception as e:
            write_audit_log(f"[V2][ENTRY] selection check failed ERR={e} → drop")
            return False

        # Passed — record dedup key.
        self._last_routed.add(key)
        return True

    def _release_dedup_key(self, symbol: str, candle_ts: int):
        """Drop a (symbol, candle_ts) dedup key so a later distinct candle can
        re-route the symbol. Used for arbitration losers / aborted winners."""
        self._last_routed.discard((symbol, candle_ts))

    # --------------------------------------------------------------------
    # ENTRY — gates + same-candle arbitration (called by the V2 tick engine)
    # --------------------------------------------------------------------

    def try_enter(
        self,
        *,
        symbol: str,
        token: int,
        entry_price: float,
        sl_price: float,    # ABOVE entry (short loss) — already max_sl-capped upstream
        tp_price: float,    # BELOW entry (short profit)
        candle_ts: int,
    ):
        """
        Per-candidate gates run synchronously (UNCHANGED). A surviving signal is
        BUFFERED for its candle_ts; after a short window the highest-premium
        signal is elected (see header) and ONLY that one claims the group and
        fans out, via _enter_winner(). The single-group claim and 3-leg fan-out
        are byte-for-byte the prior try_enter body, moved into _enter_winner.
        """
        if entry_price <= 0:
            write_audit_log(f"[V2][ENTRY] invalid entry {entry_price} — drop")
            return

        # Gates first (cheap, no lock contention with exit path). UNCHANGED.
        if not self._gates_pass(symbol, candle_ts):
            return

        # ── BUFFER for same-candle arbitration ──
        self._register_signal_candidate(
            symbol=symbol, token=token, candle_ts=candle_ts,
            entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
        )

    def _register_signal_candidate(self, *, symbol, token, candle_ts,
                                   entry_price, sl_price, tp_price):
        """
        Collect gate-passing same-candle signals; the first registrant for a
        candle_ts arms a single arbitration timer. Determinism: ranking key is
        (entry_price, symbol), both identical on every machine for the same
        closed candle. Losers' dedup keys are released so a later distinct
        candle can re-route them.
        """
        late = False
        arm = False
        with self._sig_arb_lock:
            # Already elected for this candle → this signal missed the window.
            # DO NOT drop it: route it through to _enter_winner (outside the
            # lock). The single-group gate there decides whether it enters —
            # never miss a trade for the sake of uniformity. (In practice it
            # usually hits "group busy" and releases its key, matching today's
            # single-group behaviour; it enters only if the elected group has
            # already finalized.)
            if candle_ts in self._sig_arb_fired:
                late = True

            if not late:
                if self._sig_arb_candle_ts != candle_ts:
                    # New candle → release any keys still buffered for the previous
                    # candle (they never elected and must be re-routable).
                    if self._sig_arb_buffer:
                        for c in self._sig_arb_buffer:
                            self._release_dedup_key(c["symbol"], c["candle_ts"])
                    self._sig_arb_candle_ts = candle_ts
                    self._sig_arb_buffer = []

                self._sig_arb_buffer.append({
                    "symbol": symbol, "token": token, "candle_ts": candle_ts,
                    "entry_price": float(entry_price),
                    "sl_price": sl_price, "tp_price": tp_price,
                })
                if len(self._sig_arb_buffer) == 1:
                    arm = True

        if late:
            write_audit_log(
                f"[V2][SIG_ARB_LATE] {symbol} ts={candle_ts} missed window — "
                f"routing through (entering on the single-group gate)"
            )
            self._enter_winner(
                symbol=symbol, token=token, entry_price=entry_price,
                sl_price=sl_price, tp_price=tp_price, candle_ts=candle_ts,
            )
            return

        if arm:
            threading.Thread(
                target=self._arbitrate_after_window,
                args=(candle_ts,),
                daemon=True,
                name=f"scalp-v2-sigarb-{candle_ts}",
            ).start()

    def _arbitrate_after_window(self, candle_ts: int):
        """Wait the collection window, elect the highest-premium signal, enter it."""
        time.sleep(_SIG_ARB_WINDOW_S)

        with self._sig_arb_lock:
            if self._sig_arb_candle_ts != candle_ts:
                return
            if candle_ts in self._sig_arb_fired:
                return
            candidates = list(self._sig_arb_buffer)
            if not candidates:
                return
            self._sig_arb_fired.add(candle_ts)
            if len(self._sig_arb_fired) > _SIG_ARB_FIRED_MAX:
                for old in sorted(self._sig_arb_fired)[:-(_SIG_ARB_FIRED_MAX // 2)]:
                    self._sig_arb_fired.discard(old)
            self._sig_arb_buffer = []

        winner = max(candidates, key=lambda c: (c["entry_price"], c["symbol"]))

        # Release the dedup keys of the losers so they can re-route on a later,
        # distinct candle. The winner KEEPS its key (it is being routed now).
        for c in candidates:
            if c is not winner:
                self._release_dedup_key(c["symbol"], c["candle_ts"])

        if len(candidates) > 1:
            losers = ", ".join(
                f"{c['symbol']}@{c['entry_price']}" for c in candidates if c is not winner
            )
            write_audit_log(
                f"[V2][SIG_ARB] ts={candle_ts} {len(candidates)} signals "
                f"→ elected {winner['symbol']}@{winner['entry_price']} (dropped: {losers})"
            )

        self._enter_winner(
            symbol=winner["symbol"], token=winner["token"],
            entry_price=winner["entry_price"], sl_price=winner["sl_price"],
            tp_price=winner["tp_price"], candle_ts=candle_ts,
        )

    def _enter_winner(self, *, symbol, token, entry_price, sl_price, tp_price, candle_ts):
        """
        Post-election entry — the prior try_enter body (single-group claim +
        3-leg fan-out), moved here verbatim. Runs for ONE elected winner.
        """
        side = "CE" if symbol.endswith("CE") else "PE"

        # Master-derived percentages (from the SIGNAL leg).
        sl_pct = (sl_price - entry_price) / entry_price
        tp_pct = (entry_price - tp_price) / entry_price

        with self._mutex:
            # GATE: one group at a time.
            if self._group is not None and self._group.status != CLOSED:
                write_audit_log(
                    f"[V2][ENTRY] group busy (status={self._group.status}) — drop {symbol}"
                )
                # Winner could not claim — release its dedup key so a later
                # distinct candle can re-route it.
                self._release_dedup_key(symbol, candle_ts)
                return

            group_id = f"V2-{int(time.time()*1000)}-{symbol}"
            group = GroupState(
                group_id=group_id,
                direction=side,
                master_class=LEG_SIGNAL,
                master_instrument=symbol,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                paper=self._is_paper(),
                status=PENDING,
                entry_signal_ts=candle_ts,
            )
            self._group = group   # claim: arbitration already elected one winner

        write_audit_log(
            f"[V2][ENTRY] SIGNAL={symbol} side={side} entry={entry_price} "
            f"sl={sl_price} tp={tp_price} sl_pct={sl_pct:.4f} tp_pct={tp_pct:.4f} "
            f"group={group_id}"
        )

        # Fan-out (network I/O) outside the mutex; group already claimed.
        try:
            self._fan_out(group, side, symbol, token, entry_price, sl_price, tp_price)
        except Exception as e:
            write_audit_log(f"[V2][FANOUT][FATAL] {e} — aborting group {group_id}")
            self._abort_group(group)
            return

        if group.open_legs():
            self._persist_group(group, status=OPEN)
            group.status = OPEN
            write_audit_log(
                f"[V2][OPEN] group={group_id} legs="
                f"{[lg.trade_class for lg in group.open_legs()]}"
            )
        else:
            write_audit_log(f"[V2][ABORT] no legs placed group={group_id}")
            self._abort_group(group)

    # --------------------------------------------------------------------
    # FAN-OUT — L1 (signal) + L2 (+1) + L3 (-1)
    # --------------------------------------------------------------------

    def _fan_out(self, group, side, signal_symbol, signal_token, signal_entry, signal_sl, signal_tp):
        # ----- L1: the SIGNAL strike, EXACT signal tp/sl (already capped) -----
        self._place_leg(
            group=group, role=LEG_SIGNAL, symbol=signal_symbol, token=signal_token,
            entry=signal_entry, sl=signal_sl, tp=signal_tp, is_master=True,
        )

        # Resolve adjacent strikes from the instrument universe (NOT string surgery).
        sig_instr = self._resolve_instrument(signal_symbol)
        if sig_instr is None:
            write_audit_log(
                f"[V2][SIBLING] signal instrument {signal_symbol} not resolvable "
                f"— placing L1 only"
            )
            return

        base_strike = int(sig_instr["strike"])
        expiry      = sig_instr.get("expiry")

        for role, offset in ((LEG_UP, +1), (LEG_DOWN, -1)):
            target_strike = base_strike + offset * STRIKE_STEP
            sib = self._resolve_sibling(target_strike, side, expiry)
            if sib is None:
                write_audit_log(
                    f"[V2][SIBLING] {role} strike={target_strike} {side} not found — skip"
                )
                continue

            sib_symbol = sib["tradingsymbol"]
            sib_token  = int(sib["instrument_token"])

            # Sibling entry = fresh LTP, else candle-close fallback.
            sib_entry = self._live_premium(sib_symbol)
            if sib_entry is None or sib_entry <= 0:
                write_audit_log(
                    f"[V2][SIBLING] {role} {sib_symbol} no price — skip"
                )
                continue

            # Pct-derived levels off the sibling's OWN entry (UNCHANGED).
            # NOTE: max_sl is intentionally NOT applied to siblings.
            sib_sl = round(sib_entry * (1 + group.sl_pct), 2)
            sib_tp = round(sib_entry * (1 - group.tp_pct), 2)

            self._place_leg(
                group=group, role=role, symbol=sib_symbol, token=sib_token,
                entry=sib_entry, sl=sib_sl, tp=sib_tp, is_master=False,
            )

    def _resolve_instrument(self, symbol: str) -> Optional[dict]:
        """Resolve the full instrument record for a symbol (for strike/expiry)."""
        try:
            return self.instrument_provider(symbol=symbol)
        except TypeError:
            return None
        except Exception as e:
            write_audit_log(f"[V2][SIBLING] resolve instrument {symbol} ERR={e}")
            return None

    def _resolve_sibling(self, strike: int, opt_type: str, expiry) -> Optional[dict]:
        try:
            return self.instrument_provider(strike=strike, opt_type=opt_type, expiry=expiry)
        except Exception as e:
            write_audit_log(
                f"[V2][SIBLING] resolve sibling strike={strike} {opt_type} ERR={e}"
            )
            return None

    def _place_leg(self, *, group, role, symbol, token, entry, sl, tp, is_master):
        """
        Place one leg.

        LIVE: record the LIMIT price as the provisional entry, place the SHORT
        GTT immediately from the leg's INTENDED sl/tp (fill-independent), insert
        the DB row, then confirm the true fill in the background.

        The sl/tp passed in are already final for each leg:
          L1     → signal sl/tp (max_sl-capped upstream)
          L2/L3  → pct-derived off the sibling premium
        We DO NOT recompute them from the fill.
        """
        lots     = self._leg_lots(role)
        lot_size = self._lot_size()
        qty      = lots * lot_size
        if qty <= 0:
            write_audit_log(f"[V2][LEG] role={role} qty=0 (lots={lots}) — skip")
            return

        leg = LegState(
            trade_class=role, symbol=symbol, token=token, qty=qty,
            entry_price=entry, sl=sl, tp=tp, paper=group.paper, is_master=is_master,
        )

        if group.paper:
            self._paper_record_entry(group, leg)
            group.legs[role] = leg
            write_audit_log(
                f"[V2][LEG][PAPER] role={role} {symbol} qty={qty} "
                f"entry={entry} sl={sl} tp={tp} master={is_master}"
            )
            return

        # ── LIVE: SELL entry ──────────────────────────────────────────
        # place_sell_entry → (order_id, limit_price, qty). limit_price is the
        # protected limit (ltp*0.99); record it as the provisional entry. The
        # true fill is patched in by the background confirm thread, which also
        # places the GTT once the fill is confirmed.
        try:
            order_id, limit_price, _ = self.executor.place_sell_entry(symbol, token, qty)
        except Exception as e:
            write_audit_log(f"[V2][LEG][SELL_FAIL] role={role} {symbol} ERR={e}")
            return

        leg.entry_order_id = order_id
        if limit_price and limit_price > 0:
            leg.entry_price = float(limit_price)
        # leg.sl / leg.tp keep the INTENDED levels passed in (NOT recomputed).

        # ── NO GTT YET (Option A) ─────────────────────────────────────
        # The leg's GTT is placed ONLY after the SELL fill is confirmed. If we
        # placed it now and the SELL never filled, a triggered GTT would open an
        # unintended LONG on this strike. The fill→GTT window is covered by the
        # tick exit, which watches every leg's price live.
        self._live_record_entry(group, leg, order_id)
        group.legs[role] = leg
        write_audit_log(
            f"[V2][LEG][LIVE] role={role} {symbol} qty={qty} entry≈{leg.entry_price} "
            f"(limit; fill + GTT pending) sl={leg.sl} tp={leg.tp} master={is_master}"
        )

        # ── Background: confirm fill → place GTT; cancel if unfilled ──
        self._spawn_leg_fill_confirm(group, leg)

    # --------------------------------------------------------------------
    # BACKGROUND PER-LEG FILL CONFIRMATION — Option A
    # --------------------------------------------------------------------

    def _spawn_leg_fill_confirm(self, group, leg):
        t = threading.Thread(
            target=self._leg_fill_worker,
            args=(group.group_id, leg.trade_class, leg.symbol, leg.entry_order_id),
            daemon=True,
            name=f"v2-fill-{leg.trade_class}-{leg.symbol}",
        )
        t.start()

    def _leg_fill_worker(self, group_id, role, symbol, order_id):
        """
        Poll the order book for this leg's true fill.

        COMPLETE → place the leg's GTT now (intended sl/tp), record the true
                   fill. Only if the leg is still live in the live group.
        DEAD     → never opened a position (no GTT was placed); close DB row,
                   drop the leg.
        unfilled at 50s cap → cancel, then re-check:
                   post-cancel COMPLETE → treat as fill (place GTT)
                   post-cancel partial  → log + alert, leave for manual
                   clean cancel         → close DB row, drop the leg
        """
        if not order_id:
            return

        start = time.time()
        while time.time() - start < _ENTRY_FILL_CANCEL_S:
            try:
                info = self.executor.get_order_fill(order_id)
            except Exception as e:
                write_audit_log(f"[V2][FILL_POLL_ERR] {symbol} order_id={order_id} ERR={e}")
                time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)
                continue

            status = (info.get("status") or "").upper()
            avg    = info.get("avg_price") or 0.0

            if status == "COMPLETE":
                if avg > 0:
                    self._on_leg_filled(group_id, role, symbol, float(avg))
                    return
                # COMPLETE but avg not populated — wait one more cycle.

            elif status in _DEAD_ORDER_STATUSES:
                self._on_leg_dead(group_id, role, symbol, status)
                return

            time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)

        # Unfilled at cap → cancel + resolve race.
        self._cancel_unfilled_leg(group_id, role, symbol, order_id)

    def _live_leg(self, group_id, role) -> Optional[LegState]:
        """Return the leg iff it belongs to the still-live group and is open."""
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        leg = group.legs.get(role)
        if leg is None or not leg.open:
            return None
        return leg

    def _on_leg_filled(self, group_id, role, symbol, fill_price: float):
        """Record the true fill, then place this leg's protective GTT."""
        leg = self._live_leg(group_id, role)
        if leg is None:
            write_audit_log(
                f"[V2][FILL_STALE] {symbol} role={role} fill={fill_price:.2f} "
                f"— leg not live, skipping"
            )
            return

        # 1) Record true fill for accurate (entry-exit) P&L.
        leg.entry_price = fill_price
        if leg.db_trade_id:
            try:
                from app.db.sqlite import get_conn
                conn = get_conn()
                conn.execute(
                    "UPDATE trades SET entry_price = ? WHERE trade_id = ? AND exit_time IS NULL",
                    (fill_price, leg.db_trade_id),
                )
                conn.commit()
            except Exception as e:
                write_audit_log(f"[V2][FILL_DB_UPDATE_FAIL] {symbol} role={role} ERR={e}")

        write_audit_log(
            f"[V2][FILL_CONFIRMED] role={role} {symbol} entry={fill_price:.2f} "
            f"— placing GTT"
        )

        # 2) Place the leg's GTT NOW (intended sl/tp; fill-independent).
        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol, qty=leg.qty,
                sl_price=leg.sl, tp_price=leg.tp,
                last_price=leg.entry_price, direction="SHORT",
            )
        except Exception as e:
            write_audit_log(
                f"[V2][LEG][GTT_FAIL] role={role} {symbol} ERR={e} "
                f"— OPEN without GTT; tick-exit + backstop will protect"
            )
            record_alert(
                code="GTT_FAIL",
                message=f"{symbol} ({role}): leg GTT failed — open without broker protection; tick/backstop exit applies.",
                severity="error",
                strategy_id=STRATEGY_ID,
                symbol=symbol,
                mode="live",
            )
            return

        # Re-check the leg is still live (could have exited via tick exit
        # during the GTT round-trip); if not, cancel the just-placed GTT.
        leg2 = self._live_leg(group_id, role)
        if leg2 is None:
            write_audit_log(
                f"[V2][GTT_RACE] {symbol} role={role} leg closed during GTT "
                f"placement — cancelling just-placed gtt_id={gtt_id}"
            )
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(f"[V2][GTT_RACE_CANCEL_WARN] {symbol} ERR={e}")
            return

        leg2.gtt_id = gtt_id
        # Reflect the GTT id on the live DB row (sl_order_id column).
        if leg2.db_trade_id:
            try:
                from app.db.sqlite import get_conn
                conn = get_conn()
                conn.execute(
                    "UPDATE trades SET sl_order_id = ?, state = 'PROTECTED' "
                    "WHERE trade_id = ? AND exit_time IS NULL",
                    (gtt_id, leg2.db_trade_id),
                )
                conn.commit()
            except Exception as e:
                write_audit_log(f"[V2][GTT_LINK_FAIL] {symbol} role={role} ERR={e}")

        write_audit_log(
            f"[V2][LEG_PROTECTED] role={role} {symbol} entry={fill_price:.2f} "
            f"sl={leg2.sl} tp={leg2.tp} gtt={gtt_id}"
        )

    def _on_leg_dead(self, group_id, role, symbol, status):
        """SELL never opened a position. No GTT was placed. Drop the leg."""
        leg = self._live_leg(group_id, role)
        if leg is None:
            write_audit_log(
                f"[V2][DEAD_LEG_STALE] {symbol} role={role} status={status} "
                f"— leg not live, no cleanup needed"
            )
            return

        write_audit_log(
            f"[V2][DEAD_LEG] role={role} {symbol} status={status} "
            f"— no position opened, dropping leg"
        )

        leg.open        = False
        leg.exit_price  = None
        leg.exit_reason = "ENTRY_REJECTED"
        self._live_record_exit(self._group, leg)

        group = self._group
        if group is not None and group.group_id == group_id:
            group.legs.pop(role, None)
            if not group.open_legs() and group.all_closed():
                self._finalize_group(group)

        self._alert_leg_aborted(symbol, role, group_id, status, "no position opened")

    def _cancel_unfilled_leg(self, group_id, role, symbol, order_id):
        """
        Leg SELL unfilled at the 50s cap. Cancel, then re-check to resolve a
        fill that raced the cancel.
        """
        leg = self._live_leg(group_id, role)
        if leg is None:
            return

        write_audit_log(
            f"[V2][LEG_TIMEOUT] role={role} {symbol} order_id={order_id} "
            f"unfilled after {_ENTRY_FILL_CANCEL_S}s — cancelling"
        )

        try:
            self.executor.cancel_order(order_id)
        except Exception as e:
            write_audit_log(f"[V2][LEG_CANCEL_WARN] {symbol} order_id={order_id} ERR={e}")

        time.sleep(1.0)
        try:
            info = self.executor.get_order_fill(order_id)
        except Exception:
            info = {"status": None, "avg_price": 0.0, "filled_qty": 0}

        status     = (info.get("status") or "").upper()
        avg        = info.get("avg_price") or 0.0
        filled_qty = int(info.get("filled_qty") or 0)

        leg = self._live_leg(group_id, role)
        if leg is None:
            return

        # Case 1: filled before cancel landed → protect it.
        if status == "COMPLETE" and filled_qty >= leg.qty and avg > 0:
            write_audit_log(
                f"[V2][CANCEL_RACE_FILLED] {symbol} role={role} filled before "
                f"cancel (fill={avg:.2f}) — protecting"
            )
            self._on_leg_filled(group_id, role, symbol, float(avg))
            return

        # Case 2: partial fill → manual. Do NOT auto-handle.
        if 0 < filled_qty < leg.qty:
            write_audit_log(
                f"[V2][PARTIAL_FILL][MANUAL] {symbol} role={role} "
                f"filled_qty={filled_qty}/{leg.qty} avg={avg:.2f} status={status} "
                f"— LEFT FOR MANUAL INTERVENTION. Partial short WITHOUT a GTT. "
                f"Leg left in place to avoid auto-actions."
            )
            self._alert_leg_aborted(
                symbol, role, group_id, "PARTIAL_FILL",
                f"filled {filled_qty}/{leg.qty} @~{avg:.2f}, NO GTT — handle manually"
            )
            return

        # Case 3: clean cancel → drop the leg.
        write_audit_log(
            f"[V2][LEG_CANCELLED] {symbol} role={role} clean cancel "
            f"(filled_qty={filled_qty}) — dropping leg"
        )
        record_alert(
            code="LEG_TIMEOUT",
            message=f"{symbol} ({role}): sell not filled in 50s — leg cancelled.",
            severity="warning",
            strategy_id=STRATEGY_ID,
            symbol=symbol,
            mode="live",
        )

    def _alert_leg_aborted(self, symbol, role, group_id, status, detail):
        sev = "error" if status in ("PARTIAL_FILL", "REJECTED", "CANCELLED", "LAPSED") else "warning"
        record_alert(
            code=("PARTIAL_FILL" if status == "PARTIAL_FILL" else "DEAD_LEG"),
            message=f"{symbol} ({role}): {status} — {detail}",
            severity=sev,
            strategy_id=STRATEGY_ID,
            symbol=symbol,
            mode="live",
        )
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({
                "message": (
                    f"SCALP_V2 leg {role} entry {status} for {symbol}\n"
                    f"group={group_id} — {detail}"
                ),
                "severity": "warning",
            })
        except Exception:
            pass

    # --------------------------------------------------------------------
    # TICK-DRIVEN EXIT — L1-MASTER (cascade) / L2-L3 (independent)
    # --------------------------------------------------------------------

    def on_tick(self, token: int, ltp: float):
        group = self._group
        if group is None or group.status != OPEN:
            return
        if ltp is None or ltp <= 0:
            return

        # ── MTM RISK SQUARE-OFF (combined group MTM vs SCALP_V2 limit) ──
        # Checked here because every leg's price flows through on_tick. On a
        # breach we close the ENTIRE group (all-or-nothing) using the existing
        # EOD-grade path. Latched in risk_mtm_guard so this fires once.
        try:
            if mtm_breach_for_group(group, executor=self.executor):
                write_audit_log(
                    f"[V2][MTM_SQUAREOFF] group={group.group_id} "
                    f"breached daily limit — squaring off all legs"
                )
                self.force_square_off_all(reason="MAX_LOSS")
                return
        except Exception as e:
            write_audit_log(f"[V2][MTM_CHECK_ERROR] {e}")

        for leg in group.open_legs():
            if leg.token != token:
                continue
            hit_sl = ltp >= leg.sl
            hit_tp = ltp <= leg.tp
            if not (hit_sl or hit_tp):
                return
            reason = "SL" if hit_sl else "TP"

            if leg.trade_class == LEG_SIGNAL:
                # L1 = MASTER: its exit cascades to ALL remaining open legs.
                write_audit_log(
                    f"[V2][TRIGGER][L1] role={leg.trade_class} {leg.symbol} hit {reason} "
                    f"@ltp={ltp} → closing ALL legs (L1 master exit)"
                )
                self._close_all(group, trigger_leg=leg, trigger_reason=reason, trigger_ltp=ltp)
            else:
                # L2 / L3 = INDEPENDENT: only this leg exits; group stays OPEN.
                write_audit_log(
                    f"[V2][TRIGGER][{leg.trade_class}] {leg.symbol} hit {reason} "
                    f"@ltp={ltp} → closing this leg only (independent)"
                )
                self._close_single_leg(group, leg, reason=reason, ltp_hint=ltp)
            return

    def _close_single_leg(self, group, leg, *, reason, ltp_hint):
        """
        Close ONE leg (an independent L2/L3 self-exit). The group stays OPEN;
        the remaining legs (including L1) keep running. Finalize only if this
        turns out to be the last open leg (defensive — normally L1 is still
        open here).
        """
        self._close_leg(group, leg, reason=reason, ltp_hint=ltp_hint)
        write_audit_log(
            f"[V2][LEG_INDEP_CLOSE] role={leg.trade_class} {leg.symbol} "
            f"reason={reason} pnl={leg.realized_pnl()} — group stays OPEN, "
            f"remaining={[lg.trade_class for lg in group.open_legs()]}"
        )
        if group.all_closed():
            self._finalize_group(group)

    def _close_all(self, group, *, trigger_leg, trigger_reason, trigger_ltp):
        """
        L1-master cascade (also the shared path for MTM/EOD all-or-nothing):
        close every open leg now. Already-closed legs (e.g. an L2/L3 that
        self-exited earlier) are skipped, so they are never double-exited.
        """
        with self._mutex:
            if group.status != OPEN:
                return
            group.status = "CLOSING"
            group.exit_trigger_ts = int(time.time())
            group.exit_reason = trigger_reason

        for leg in list(group.legs.values()):
            if not leg.open:
                continue
            if leg is trigger_leg:
                self._close_leg(group, leg, reason=trigger_reason, ltp_hint=trigger_ltp)
            else:
                self._close_leg(group, leg, reason="GROUP_EXIT", ltp_hint=None)

        self._finalize_group(group)

    # --------------------------------------------------------------------
    # BACKSTOP MONITOR HANDOFF — L1-master cascade / L2-L3 independent
    # --------------------------------------------------------------------

    def on_backstop_leg_exit(self, *, group_id, trade_class, exit_price, reason):
        """
        The GTT monitor confirmed a broker exit for one leg. Record it, then:
          • if the exited leg is L1 → cascade-close all remaining open legs.
          • if it's L2/L3          → close ONLY that leg; the group stays open.
        Finalize when all legs are closed.
        """
        group = self._group
        if group is None or group.group_id != group_id:
            return
        leg = group.legs.get(trade_class)
        if leg is None:
            return

        with self._mutex:
            if not leg.open:
                return
            leg.exit_price  = exit_price
            leg.exit_reason = reason
            leg.open        = False

        self._record_leg_exit(group, leg)
        write_audit_log(
            f"[V2][BACKSTOP_CLOSE] role={leg.trade_class} {leg.symbol} "
            f"reason={reason} exit={exit_price} pnl={leg.realized_pnl()}"
        )

        if leg.trade_class == LEG_SIGNAL:
            # L1 exited at broker → cascade the remaining legs.
            remaining = [lg for lg in group.legs.values() if lg.open]
            if remaining:
                with self._mutex:
                    if group.status == OPEN:
                        group.status = "CLOSING"
                        group.exit_trigger_ts = int(time.time())
                        group.exit_reason = reason
                write_audit_log(
                    f"[V2][BACKSTOP_L1_CASCADE] group={group.group_id} "
                    f"L1 exited → closing remaining {[lg.trade_class for lg in remaining]}"
                )
                for lg in remaining:
                    if lg.open:
                        self._close_leg(group, lg, reason="GROUP_EXIT", ltp_hint=None)
        else:
            # L2 / L3 exited independently — leave the rest running.
            write_audit_log(
                f"[V2][BACKSTOP_INDEP] role={leg.trade_class} closed independently "
                f"— group stays open, remaining="
                f"{[lg.trade_class for lg in group.open_legs()]}"
            )

        if group.all_closed():
            self._finalize_group(group)

    # --------------------------------------------------------------------
    # EOD SQUARE-OFF (called by scalp_v2_live_eod at 15:25) — unchanged API
    # --------------------------------------------------------------------

    def force_square_off_all(self, reason: str = "EOD_SQUAREOFF"):
        group = self._group
        if group is None or group.status == CLOSED:
            write_audit_log("[V2][EOD] No open group to square off")
            return

        open_now = group.open_legs()
        write_audit_log(
            f"[V2][EOD] Squaring off group={group.group_id} "
            f"open_legs={[lg.trade_class for lg in open_now]} reason={reason}"
        )
        with self._mutex:
            if group.status in (OPEN, "CLOSING"):
                group.status = "CLOSING"
        for leg in list(open_now):
            self._force_exit_leg(group, leg, reason=reason)

        if group.all_closed():
            self._finalize_group(group)

    # --------------------------------------------------------------------
    # LEG CLOSE (natural) + FORCE EXIT (EOD)
    # --------------------------------------------------------------------

    def _close_leg(self, group, leg, *, reason, ltp_hint=None):
        with self._mutex:
            if not leg.open:
                return
            leg.open = False
        if leg.paper:
            exit_price = self._resolve_exit_price(leg.symbol, ltp_hint)
            leg.exit_price  = exit_price
            leg.exit_reason = reason
            self._record_leg_exit(group, leg)
            write_audit_log(
                f"[V2][LEG_CLOSE][PAPER] role={leg.trade_class} {leg.symbol} "
                f"reason={reason} exit={exit_price} pnl={leg.realized_pnl()}"
            )
            return
        self._live_close_short(group, leg, reason, ltp_hint)

    def _live_close_short(self, group, leg, reason, ltp_hint):
        if self._gtt_already_triggered(leg.gtt_id):
            write_audit_log(
                f"[V2][LEG_CLOSE] role={leg.trade_class} GTT already triggered "
                f"— resolving naturally"
            )
            exit_price = self._resolve_exit_price(leg.symbol, ltp_hint)
            leg.exit_price  = exit_price
            leg.exit_reason = "GTT_TRIGGERED"
            self._record_leg_exit(group, leg)
            return

        if leg.gtt_id:
            gone = True
            try:
                if hasattr(self.executor, "cancel_gtt_verified"):
                    gone = self.executor.cancel_gtt_verified(leg.gtt_id)
                else:
                    self.executor.cancel_gtt(leg.gtt_id)
            except Exception as e:
                write_audit_log(f"[V2][LEG_CLOSE] cancel_gtt failed {leg.gtt_id}: {e}")
            if not gone:
                write_audit_log(
                    f"[V2][LEG_CLOSE][GTT_ORPHAN] role={leg.trade_class} {leg.symbol} "
                    f"gtt={leg.gtt_id} STILL ARMED after cancel — alerting; still flattening"
                )
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({
                        "message": (
                            f"SCALP_V2 GTT {leg.gtt_id} for {leg.symbol} ({leg.trade_class}) "
                            f"could NOT be cancelled (still armed). Selling to flatten now, but "
                            f"DELETE THIS GTT MANUALLY in Kite."
                        ),
                        "severity": "error",
                    })
                except Exception:
                    pass

        try:
            self.executor.place_buy_exit(symbol=leg.symbol, qty=leg.qty, reason=reason)
        except Exception as e:
            write_audit_log(f"[V2][LEG_CLOSE][FATAL] buy_exit failed {leg.symbol}: {e}")

        exit_price = self._resolve_exit_price(leg.symbol, ltp_hint)
        leg.exit_price  = exit_price
        leg.exit_reason = reason
        self._record_leg_exit(group, leg)
        write_audit_log(
            f"[V2][LEG_CLOSE][LIVE] role={leg.trade_class} {leg.symbol} "
            f"reason={reason} exit={exit_price} pnl={leg.realized_pnl()}"
        )

    def _force_exit_leg(self, group, leg, reason: str = "EOD_SQUAREOFF"):
        self._close_leg(group, leg, reason=reason, ltp_hint=None)

    def _gtt_already_triggered(self, gtt_id) -> bool:
        if not gtt_id:
            return False
        try:
            gtts = self.executor.get_gtts()
            g = next((x for x in gtts if str(x.get("id")) == str(gtt_id)), None)
            return bool(g and g.get("status") in ("triggered", "disabled"))
        except Exception:
            return False

    def _resolve_exit_price(self, symbol, ltp_hint):
        if ltp_hint and ltp_hint > 0:
            return float(ltp_hint)
        return self._live_premium(symbol)

    # --------------------------------------------------------------------
    # FINALIZE
    # --------------------------------------------------------------------

    def _finalize_group(self, group):
        with self._mutex:
            if group.status == CLOSED:
                return
            total = sum((lg.realized_pnl() or 0.0) for lg in group.legs.values())
            group.status = CLOSED
            self._persist_group(group, status=CLOSED, realized_pnl=total)
            write_audit_log(
                f"[V2][CLOSED] group={group.group_id} realized_pnl={total:.2f} "
                f"legs={len(group.legs)} — V2 free to re-elect"
            )
            self._group = None

    def _abort_group(self, group):
        with self._mutex:
            group.status = CLOSED
            self._persist_group(group, status=CLOSED, realized_pnl=0.0)
            self._group = None

    # ====================================================================
    # DB SEAM — paper + live (unchanged from prior V2)
    # ====================================================================

    def _paper_record_entry(self, group, leg):
        pid = str(uuid.uuid4())
        leg.db_trade_id = pid
        side = "CE" if leg.symbol.endswith("CE") else "PE"
        lot_size = self._lot_size()
        lots = leg.qty // lot_size if lot_size else leg.qty
        try:
            from app.db.paper_trades_repo import insert_paper_trade
            insert_paper_trade(
                paper_trade_id=pid,
                strategy_name=STRATEGY_ID,
                trade_mode="PAPER",
                symbol=leg.symbol,
                token=leg.token,
                side=side,
                entry_price=leg.entry_price,
                candle_ts=group.entry_signal_ts or int(time.time()),
                sl_price=leg.sl,
                tp_price=leg.tp,
                rr=0.0,
                lots=lots,
                lot_size=lot_size,
                qty=leg.qty,
                trade_direction="SHORT",
                group_id=group.group_id,
                trade_class=leg.trade_class,
            )
            write_audit_log(
                f"[V2][PAPER_ENTRY] pid={pid} role={leg.trade_class} "
                f"{leg.symbol} qty={leg.qty} group={group.group_id}"
            )
        except TypeError as e:
            write_audit_log(
                f"[V2][PAPER_ENTRY_SEAM] insert_paper_trade group kwargs not wired: {e}"
            )
        except Exception as e:
            write_audit_log(f"[V2][PAPER_ENTRY_FAIL] {leg.symbol} ERR={e}")

    def _paper_record_exit(self, group, leg):
        try:
            from app.db.paper_trades_repo import close_paper_trade
            if leg.db_trade_id is not None:
                close_paper_trade(
                    paper_trade_id=leg.db_trade_id,
                    exit_price=leg.exit_price,
                    exit_reason=leg.exit_reason or "GROUP_EXIT",
                    trade_direction="SHORT",
                )
                write_audit_log(
                    f"[V2][PAPER_EXIT] pid={leg.db_trade_id} role={leg.trade_class} "
                    f"reason={leg.exit_reason} exit={leg.exit_price}"
                )
        except Exception as e:
            write_audit_log(f"[V2][PAPER_EXIT_FAIL] {leg.symbol} ERR={e}")

    def _live_record_entry(self, group, leg, order_id):
        trade_id = str(uuid.uuid4())
        leg.db_trade_id = trade_id
        try:
            from app.db.sqlite import get_conn
            conn = get_conn()
            conn.execute(
                """
                INSERT INTO trades (
                    trade_id, strategy_id, slot, symbol, token,
                    entry_time, entry_price, qty, buy_order_id,
                    sl_price, sl_order_id, tp_price, tp_mode,
                    state, trade_direction, group_id, trade_class
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id, STRATEGY_ID, f"V2_{leg.trade_class}", leg.symbol, leg.token,
                    int(time.time()), leg.entry_price, leg.qty, order_id,
                    leg.sl, leg.gtt_id, leg.tp, "PCT",
                    "PROTECTED" if leg.gtt_id else "SELL_PLACED",
                    "SHORT", group.group_id, leg.trade_class,
                ),
            )
            conn.commit()
            write_audit_log(
                f"[V2][LIVE_ENTRY] trade_id={trade_id} role={leg.trade_class} "
                f"{leg.symbol} group={group.group_id}"
            )
        except Exception as e:
            write_audit_log(f"[V2][LIVE_ENTRY_FAIL] {leg.symbol} ERR={e}")

    def _live_record_exit(self, group, leg):
        try:
            from app.db.trades_repo import close_trade
            if leg.db_trade_id is not None:
                close_trade(
                    trade_id=leg.db_trade_id,
                    exit_price=leg.exit_price,
                    exit_order_id=None,
                    exit_reason=leg.exit_reason or "GROUP_EXIT",
                )
        except Exception as e:
            write_audit_log(f"[V2][LIVE_EXIT_FAIL] {leg.symbol} ERR={e}")

    def _record_leg_exit(self, group, leg):
        if leg.paper:
            self._paper_record_exit(group, leg)
        else:
            self._live_record_exit(group, leg)

    # ====================================================================
    # GROUP PERSISTENCE (scalp_v2_groups) — unchanged schema
    # ====================================================================

    def _persist_group(self, group, *, status=None, realized_pnl=None):
        try:
            from app.db.sqlite import get_conn
            conn = get_conn()
            if status:
                group.status = status
            conn.execute(
                """
                INSERT INTO scalp_v2_groups
                    (group_id, session_date, paper, direction, master_class,
                     master_instrument, status, sl_pct, tp_pct, entry_signal_ts,
                     exit_trigger_ts, exit_reason, realized_pnl, updated_at)
                VALUES (?, date('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        strftime('%s','now'))
                ON CONFLICT(group_id) DO UPDATE SET
                    status          = excluded.status,
                    exit_trigger_ts = COALESCE(excluded.exit_trigger_ts, scalp_v2_groups.exit_trigger_ts),
                    exit_reason     = COALESCE(excluded.exit_reason, scalp_v2_groups.exit_reason),
                    realized_pnl    = excluded.realized_pnl,
                    updated_at      = strftime('%s','now')
                """,
                (
                    group.group_id,
                    1 if group.paper else 0,
                    group.direction,
                    group.master_class,
                    group.master_instrument,
                    group.status,
                    group.sl_pct,
                    group.tp_pct,
                    group.entry_signal_ts,
                    group.exit_trigger_ts,
                    group.exit_reason,
                    realized_pnl if realized_pnl is not None else 0.0,
                ),
            )
            conn.commit()
        except Exception as e:
            write_audit_log(f"[V2][PERSIST_FAIL] group={group.group_id} ERR={e}")