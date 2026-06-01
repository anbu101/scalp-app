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
# EXIT = ALL-OR-NOTHING: the moment ANY open leg crosses its own TP or SL (on
#   tick OR via the backstop monitor), ALL open legs are closed immediately.
#   No stagger window.
#
# GATING = one group at a time (a live group blocks any new signal until all
#   its legs close). Mirrors V1's gates: trade_on, daily max-loss/profit,
#   session, dedup, selection-filter.
#
# ISOLATION: TradeStateManager._REGISTRY is NEVER touched. SCALP_V1 / BB / HA
#   are completely unaffected. This manager owns all SCALP_V2 leg state.
#
# DB write seam (unchanged from prior V2):
#   PAPER -> paper_trades_repo.insert_paper_trade / close_paper_trade
#   LIVE  -> direct trades insert (group_id + trade_class cols) + close_trade
#
# Compatibility note: LegState keeps a field named `trade_class` (it now holds
#   the leg ROLE "L1"/"L2"/"L3", not a class), so scalp_v2_gtt_monitor.py and
#   scalp_v2_live_eod.py need NO changes — they reference leg.trade_class and
#   the same group-manager API (current_group, force_square_off_all,
#   on_backstop_leg_exit, _live_premium).
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
                            get_gtts, get_open_positions, get_orders).
      candidate_provider  : callable(symbol) -> token (resolve token for a symbol)
      instrument_provider : callable(strike:int, opt_type:str, expiry) -> dict|None
                            returns {"tradingsymbol","instrument_token","strike",
                                     "type","expiry"} for the adjacent strike, or
                            None if that strike doesn't exist.
      selected_provider   : callable() -> (set[str] ce, set[str] pe)
                            the currently-selected SCALP_V2 symbols (selection
                            filter, mirrors V1's router behaviour).
      candle_provider     : callable(symbol) -> float|None  (last candle close,
                            E1 fallback when no fresh LTPStore tick).
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
        None only if neither is available (caller skips that leg / uses hint).
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

        # Fallback: last candle close (decision-aligned, always present if traded).
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

    # --------------------------------------------------------------------
    # ENTRY — 3-leg split (called by the V2 tick engine on a valid SELL)
    # --------------------------------------------------------------------

    def try_enter(
        self,
        *,
        symbol: str,
        token: int,
        entry_price: float,
        sl_price: float,    # ABOVE entry (short loss)
        tp_price: float,    # BELOW entry (short profit)
        candle_ts: int,
    ):
        """
        Single-group gate + 3-leg fan-out. The signal contract is L1 (exact
        signal tp/sl); the ±1 strikes are L2/L3 (pct-derived). All-or-nothing
        exit is handled in on_tick.
        """
        if entry_price <= 0:
            write_audit_log(f"[V2][ENTRY] invalid entry {entry_price} — drop")
            return

        side = "CE" if symbol.endswith("CE") else "PE"

        # Gates first (cheap, no lock contention with exit path).
        if not self._gates_pass(symbol, candle_ts):
            return

        # Master-derived percentages (from the SIGNAL leg).
        sl_pct = (sl_price - entry_price) / entry_price
        tp_pct = (entry_price - tp_price) / entry_price

        with self._mutex:
            # GATE: one group at a time.
            if self._group is not None and self._group.status != CLOSED:
                write_audit_log(
                    f"[V2][ENTRY] group busy (status={self._group.status}) — drop {symbol}"
                )
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
            self._group = group   # claim: same-tick rivals drop on the gate above

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
        # ----- L1: the SIGNAL strike, EXACT signal tp/sl -----
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

            # Pct-derived levels off the sibling's OWN entry.
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
            # instrument_provider may be (strike, type, expiry) style only.
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

        # LIVE: SELL entry, then SHORT GTT OCO.
        try:
            order_id, fill_limit, _ = self.executor.place_sell_entry(symbol, token, qty)
            leg.entry_price = fill_limit or entry
            leg.sl = round(leg.entry_price * (1 + group.sl_pct), 2)
            leg.tp = round(leg.entry_price * (1 - group.tp_pct), 2)
        except Exception as e:
            write_audit_log(f"[V2][LEG][SELL_FAIL] role={role} {symbol} ERR={e}")
            return

        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol, qty=qty,
                sl_price=leg.sl, tp_price=leg.tp,
                last_price=leg.entry_price, direction="SHORT",
            )
            leg.gtt_id = gtt_id
        except Exception as e:
            write_audit_log(
                f"[V2][LEG][GTT_FAIL] role={role} {symbol} ERR={e} "
                f"— OPEN without GTT; tick-exit + backstop will protect"
            )

        self._live_record_entry(group, leg, order_id)
        group.legs[role] = leg
        write_audit_log(
            f"[V2][LEG][LIVE] role={role} {symbol} qty={qty} entry={leg.entry_price} "
            f"sl={leg.sl} tp={leg.tp} gtt={leg.gtt_id} master={is_master}"
        )

    # --------------------------------------------------------------------
    # TICK-DRIVEN EXIT — ALL-OR-NOTHING
    # --------------------------------------------------------------------

    def on_tick(self, token: int, ltp: float):
        group = self._group
        if group is None or group.status != OPEN:
            return
        if ltp is None or ltp <= 0:
            return

        # Find a matching open leg and test its own SHORT cross.
        for leg in group.open_legs():
            if leg.token != token:
                continue
            hit_sl = ltp >= leg.sl
            hit_tp = ltp <= leg.tp
            if hit_sl or hit_tp:
                reason = "SL" if hit_sl else "TP"
                write_audit_log(
                    f"[V2][TRIGGER] role={leg.trade_class} {leg.symbol} hit {reason} "
                    f"@ltp={ltp} → closing ALL legs (all-or-nothing)"
                )
                self._close_all(group, trigger_leg=leg, trigger_reason=reason, trigger_ltp=ltp)
            return

    def _close_all(self, group, *, trigger_leg, trigger_reason, trigger_ltp):
        """All-or-nothing: close every open leg now. Trigger leg keeps its real
        reason; the others are GROUP_EXIT. Single transition, then finalize."""
        # Claim the group transition under lock so tick + backstop can't both run it.
        with self._mutex:
            if group.status != OPEN:
                return
            # Sentinel status blocks re-entry of on_tick / backstop mid-close.
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
    # BACKSTOP MONITOR HANDOFF — now also triggers all-or-nothing
    # --------------------------------------------------------------------

    def on_backstop_leg_exit(self, *, group_id, trade_class, exit_price, reason):
        group = self._group
        if group is None or group.group_id != group_id:
            return
        leg = group.legs.get(trade_class)
        if leg is None:
            return

        # Close the confirmed leg under lock, then close the rest (all-or-nothing).
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

        # The broker exited one leg → close all remaining legs immediately.
        remaining = [lg for lg in group.legs.values() if lg.open]
        if remaining:
            with self._mutex:
                if group.status == OPEN:
                    group.status = "CLOSING"
                    group.exit_trigger_ts = int(time.time())
                    group.exit_reason = reason
            for lg in remaining:
                if lg.open:
                    self._close_leg(group, lg, reason="GROUP_EXIT", ltp_hint=None)

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
        # Paper closes at LTP; live closes the short (cancel GTT + buy back).
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
        # If GTT already triggered, let it resolve naturally.
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
            try:
                self.executor.cancel_gtt(leg.gtt_id)
            except Exception as e:
                write_audit_log(f"[V2][LEG_CLOSE] cancel_gtt failed {leg.gtt_id}: {e}")

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
        # EOD path reuses the same close machinery.
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