# backend/app/engine/scalp_v2/scalp_v2_group_manager.py
#
# SCALP_V2 — Group Manager (Model B)
# ============================================================================
# Owns ALL leg state for SCALP_V2. TradeStateManager._REGISTRY is NEVER
# touched (so SCALP_V1 gates are byte-identical). Mirrors the BB/HA pattern
# of self-owned trade state.
#
# Responsibilities:
#   1. Dynamic master election (first valid SELL signal wins, atomic).
#   2. Group free-gate: a new group can only be elected when no legs are open.
#   3. Slave resolution: per non-master class, pick the highest in-band
#      same-direction contract (LTPStore first, REST fallback). Skip if none.
#   4. Fan-out placement: master + 0..2 slaves. SHORT GTT OCO per leg.
#      SL/TP propagated by percentage from the master.
#   5. Tick-driven exit: first leg to cross TP/SL starts a single global
#      15s window. Stragglers force-exit at expiry (cancel GTT -> buy back).
#   6. P&L rollup on group close, then release the group so V2 can re-elect.
#
# Signal generation is NOT here — the tick engine (Step 4) owns the per-contract
# StrategyEngine instances and hands finished signals to try_elect_and_enter().
#
# DB write seam:
#   PAPER -> PaperTradeRecorder (known-good path)
#   LIVE  -> trades_repo adapter (_live_record_entry / _live_record_exit)
#            wired to the real schema once trades_repo.py is confirmed.
# ============================================================================

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.marketdata.ltp_store import LTPStore


STRATEGY_ID = "SCALP_V2"

CLASSES = ("A", "B", "C")

# A cached LTPStore tick older than this is treated as stale and ignored,
# so we never act on (or display) a price left over from a previous session
# or a feed gap. During market hours ticks are sub-second fresh, so this
# never trips in normal live operation.
LTP_STALENESS_SEC = 30

# Group lifecycle states
PENDING  = "PENDING"
OPEN     = "OPEN"
EXITING  = "EXITING"
CLOSED   = "CLOSED"


# ============================================================================
# Leg state
# ============================================================================

@dataclass
class LegState:
    trade_class:  str
    symbol:       str
    token:        int
    qty:          int
    entry_price:  float
    sl:           float          # ABOVE entry — premium rising = loss (short)
    tp:           float          # BELOW entry — premium falling = profit (short)
    gtt_id:       Optional[str] = None
    db_trade_id:  Optional[str] = None    # trades.trade_id (live) or paper_trade_id
    paper:        bool = False
    is_master:    bool = False
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
    master_class:      str
    master_instrument: str
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
    """

    DEFAULT_STAGGER_SEC = 15

    def __init__(self, executor, selection_provider, candidate_provider):
        """
        executor            : ZerodhaOrderExecutor (place_sell_entry,
                              place_gtt_oco(direction="SHORT"), cancel_gtt,
                              place_buy_exit)
        selection_provider  : callable(trade_class, side) -> List[symbol]
                              returns the user-selected contracts for a class.
        candidate_provider  : callable(symbol) -> token (resolve token + meta)
        """
        self.executor           = executor
        self.selection_provider = selection_provider
        self.candidate_provider = candidate_provider

        self._group:  Optional[GroupState] = None
        self._mutex   = threading.Lock()        # guards election + group lifecycle
        self._timer:  Optional[threading.Timer] = None

    # --------------------------------------------------------------------
    # Config
    # --------------------------------------------------------------------

    def _cfg(self) -> dict:
        return load_strategy_config(STRATEGY_ID)

    def _is_paper(self) -> bool:
        return self._cfg().get("trade_execution_mode", "LIVE") == "PAPER"

    def _stagger_sec(self) -> int:
        return int(self._cfg().get("exit_stagger_seconds", self.DEFAULT_STAGGER_SEC))

    def _class_band(self, trade_class: str) -> tuple:
        c = self._cfg().get("classes", {}).get(trade_class, {})
        band = c.get("premium", {})
        return float(band.get("min", 0)), float(band.get("max", 0))

    def _class_lots(self, trade_class: str) -> int:
        c = self._cfg().get("classes", {}).get(trade_class, {})
        return int(c.get("lots", 0))

    def _lot_size(self) -> int:
        return int(self._cfg().get("quantity", {}).get("lot_size", 65))

    def _class_of_symbol(self, symbol: str) -> Optional[str]:
        """Return which class's selection list contains this symbol."""
        side = "CE" if symbol.endswith("CE") else "PE"
        for c in CLASSES:
            if symbol in set(self.selection_provider(c, side)):
                return c
        return None

    # --------------------------------------------------------------------
    # Premium read: LTPStore first, REST fallback
    # --------------------------------------------------------------------

    def _live_premium(self, symbol: str) -> Optional[float]:
        """
        Premium from a FRESH LTPStore tick only.

        LTPStore is WebSocket-only by design (no REST fallback exists), so the
        premium is available only while live ticks are flowing. A stale tick
        (previous session / feed gap, older than LTP_STALENESS_SEC) is ignored
        and returns None, so we never act on or display a price left over from
        a closed market. During market hours ticks are sub-second fresh, so
        valid prices are always returned as before.

        Returns None when there is no fresh tick (e.g. market closed) — callers
        already handle None: surveillance shows "—" and marks the contract not
        in-band; slave-selection skips a class with no price.
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
        return None

    # --------------------------------------------------------------------
    # ELECTION + ENTRY  (called by the tick engine on a valid SELL signal)
    # --------------------------------------------------------------------

    def try_elect_and_enter(
        self,
        *,
        symbol: str,
        token: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        candle_ts: int,
    ):
        """
        Atomic master election. The first valid SELL signal that finds the
        group free becomes master and triggers fan-out. Competing same-tick
        signals are dropped because the mutex + group-not-None gate.
        """
        side = "CE" if symbol.endswith("CE") else "PE"

        master_class = self._class_of_symbol(symbol)
        if master_class is None:
            write_audit_log(f"[V2][ELECT] {symbol} not in any class selection — drop")
            return

        with self._mutex:
            # GATE: group must be fully free (no open legs).
            if self._group is not None and self._group.status != CLOSED:
                write_audit_log(
                    f"[V2][ELECT] Group busy (status={self._group.status}) "
                    f"— dropping {symbol}"
                )
                return

            if entry_price <= 0:
                write_audit_log(f"[V2][ELECT] Invalid entry {entry_price} — drop")
                return

            # Master-derived percentages.
            sl_pct = (sl_price - entry_price) / entry_price
            tp_pct = (entry_price - tp_price) / entry_price

            group_id = f"V2-{int(time.time()*1000)}-{symbol}"
            group = GroupState(
                group_id=group_id,
                direction=side,
                master_class=master_class,
                master_instrument=symbol,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                paper=self._is_paper(),
                status=PENDING,
                entry_signal_ts=candle_ts,
            )
            self._group = group   # LOCK: group now claimed; same-tick rivals drop

        write_audit_log(
            f"[V2][ELECT] MASTER={symbol} class={master_class} side={side} "
            f"entry={entry_price} sl={sl_price} tp={tp_price} "
            f"sl_pct={sl_pct:.4f} tp_pct={tp_pct:.4f} group={group_id}"
        )

        # Fan-out runs outside the mutex (network I/O); group is already locked.
        try:
            self._fan_out(group, side, master_class, symbol, token, entry_price)
        except Exception as e:
            write_audit_log(f"[V2][FANOUT][FATAL] {e} — aborting group {group_id}")
            self._abort_group(group)
            return

        # If at least the master placed, group is OPEN; else aborted.
        if group.open_legs():
            self._persist_group(group, status=OPEN)
            group.status = OPEN
            write_audit_log(
                f"[V2][OPEN] group={group_id} legs="
                f"{[lg.trade_class for lg in group.open_legs()]}"
            )
        else:
            write_audit_log(f"[V2][ABORT] No legs placed for group={group_id}")
            self._abort_group(group)

    # --------------------------------------------------------------------
    # FAN-OUT
    # --------------------------------------------------------------------

    def _fan_out(self, group, side, master_class, master_symbol, master_token, master_entry):
        # 1) Master leg first.
        self._place_leg(
            group=group,
            trade_class=master_class,
            symbol=master_symbol,
            token=master_token,
            entry_hint=master_entry,
            is_master=True,
        )

        # 2) Slave classes.
        for c in CLASSES:
            if c == master_class:
                continue
            chosen = self._resolve_slave_contract(c, side)
            if chosen is None:
                write_audit_log(f"[V2][SLAVE] class={c} no in-band contract — skip")
                continue
            sym, tok, prem = chosen
            self._place_leg(
                group=group,
                trade_class=c,
                symbol=sym,
                token=tok,
                entry_hint=prem,
                is_master=False,
            )

    def _resolve_slave_contract(self, trade_class, side):
        """Highest in-band same-direction contract for this class, or None."""
        lo, hi = self._class_band(trade_class)
        candidates = self.selection_provider(trade_class, side)
        best = None  # (symbol, token, premium)
        for sym in candidates:
            prem = self._live_premium(sym)
            if prem is None:
                continue
            if lo <= prem <= hi:
                if best is None or prem > best[2]:
                    tok = self.candidate_provider(sym)
                    best = (sym, tok, prem)
        if best:
            write_audit_log(
                f"[V2][SLAVE] class={trade_class} chosen={best[0]} "
                f"prem={best[2]} band={lo}-{hi}"
            )
        return best

    def _place_leg(self, *, group, trade_class, symbol, token, entry_hint, is_master):
        lots     = self._class_lots(trade_class)
        lot_size = self._lot_size()
        qty      = lots * lot_size
        if qty <= 0:
            write_audit_log(f"[V2][LEG] class={trade_class} qty=0 — skip")
            return

        # Per-leg SL/TP from master percentages applied to THIS leg's entry.
        entry = entry_hint
        sl    = round(entry * (1 + group.sl_pct), 2)
        tp    = round(entry * (1 - group.tp_pct), 2)

        leg = LegState(
            trade_class=trade_class, symbol=symbol, token=token, qty=qty,
            entry_price=entry, sl=sl, tp=tp, paper=group.paper, is_master=is_master,
        )

        if group.paper:
            self._paper_record_entry(group, leg)
            group.legs[trade_class] = leg
            write_audit_log(
                f"[V2][LEG][PAPER] class={trade_class} {symbol} qty={qty} "
                f"entry={entry} sl={sl} tp={tp} master={is_master}"
            )
            return

        # LIVE: SELL entry, then SHORT GTT OCO (place-then-confirm).
        try:
            order_id, fill_limit, _ = self.executor.place_sell_entry(symbol, token, qty)
            leg.entry_price = fill_limit or entry
            # recompute levels off actual entry
            leg.sl = round(leg.entry_price * (1 + group.sl_pct), 2)
            leg.tp = round(leg.entry_price * (1 - group.tp_pct), 2)
        except Exception as e:
            write_audit_log(f"[V2][LEG][SELL_FAIL] class={trade_class} {symbol} ERR={e}")
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
                f"[V2][LEG][GTT_FAIL] class={trade_class} {symbol} ERR={e} "
                f"— position OPEN without GTT; tick-exit + backstop will protect"
            )

        self._live_record_entry(group, leg, order_id)
        group.legs[trade_class] = leg
        write_audit_log(
            f"[V2][LEG][LIVE] class={trade_class} {symbol} qty={qty} "
            f"entry={leg.entry_price} sl={leg.sl} tp={leg.tp} "
            f"gtt={leg.gtt_id} master={is_master}"
        )

    # --------------------------------------------------------------------
    # TICK-DRIVEN EXIT  (called by tick engine for every relevant tick)
    # --------------------------------------------------------------------

    def on_tick(self, token: int, ltp: float):
        group = self._group
        if group is None or group.status not in (OPEN, EXITING):
            return
        if ltp is None or ltp <= 0:
            return

        for leg in group.open_legs():
            if leg.token != token:
                continue

            # SHORT cross detection.
            hit_sl = ltp >= leg.sl
            hit_tp = ltp <= leg.tp
            if not (hit_sl or hit_tp):
                continue

            reason = "SL" if hit_sl else "TP"
            self._close_leg(group, leg, reason=reason, ltp_hint=ltp)
            self._after_leg_closed(group, leg, reason)
            return

    def _after_leg_closed(self, group, leg, reason):
        """
        Shared FSM transition after ANY leg closes (tick path OR backstop):
          - first leg to close → group EXITING + start global stagger timer
          - last leg to close  → finalize group
        Lives in ONE place so tick + backstop can never produce divergent state.
        """
        # First leg to close starts the global window.
        if group.status == OPEN:
            with self._mutex:
                if group.status == OPEN:   # double-check under lock
                    group.status = EXITING
                    group.exit_trigger_ts = int(time.time())
                    group.exit_reason = reason
                    self._persist_group(group, status=EXITING)
                    self._start_stagger_timer(group)
                    write_audit_log(
                        f"[V2][EXITING] first leg {leg.trade_class} hit {reason} "
                        f"— {self._stagger_sec()}s window started"
                    )

        if group.all_closed():
            self._finalize_group(group)

    # --------------------------------------------------------------------
    # BACKSTOP MONITOR HANDOFF
    # Called by ScalpV2GTTMonitor when it confirms a leg exited at the broker
    # but the tick path missed it. Re-checks leg.open under lock so a race
    # with the tick path can never double-close.
    # --------------------------------------------------------------------

    def current_group(self):
        return self._group

    def on_backstop_leg_exit(self, *, group_id, trade_class, exit_price, reason):
        group = self._group
        if group is None or group.group_id != group_id:
            return
        leg = group.legs.get(trade_class)
        if leg is None:
            return

        with self._mutex:
            if not leg.open:
                return   # tick path already closed it — backstop is a no-op
            leg.exit_price  = exit_price
            leg.exit_reason = reason
            leg.open        = False

        # Record outside the lock (DB I/O); leg already marked closed atomically.
        self._record_leg_exit(group, leg)
        write_audit_log(
            f"[V2][BACKSTOP_CLOSE] class={leg.trade_class} {leg.symbol} "
            f"reason={reason} exit={exit_price} pnl={leg.realized_pnl()}"
        )
        self._after_leg_closed(group, leg, reason)

    # --------------------------------------------------------------------
    # EOD SQUARE-OFF (called by the scalp_v2 EOD job at 15:25)
    # Force-exits every open leg immediately (no stagger wait) and finalizes.
    # Reuses _force_exit_leg so the check-then-cancel / buy-back path is
    # identical to the stagger-expiry behaviour.
    # --------------------------------------------------------------------

    def force_square_off_all(self, reason: str = "EOD_SQUAREOFF"):
        group = self._group
        if group is None or group.status == CLOSED:
            write_audit_log("[V2][EOD] No open group to square off")
            return

        # Cancel any pending stagger timer — EOD overrides the window.
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        open_now = group.open_legs()
        write_audit_log(
            f"[V2][EOD] Squaring off group={group.group_id} "
            f"open_legs={[lg.trade_class for lg in open_now]} reason={reason}"
        )

        for leg in list(open_now):
            self._force_exit_leg(group, leg, reason=reason)

        if group.all_closed():
            self._finalize_group(group)

    def _start_stagger_timer(self, group):
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(
            self._stagger_sec(), self._on_stagger_expiry, args=(group.group_id,)
        )
        self._timer.daemon = True
        self._timer.start()

    def _on_stagger_expiry(self, group_id):
        group = self._group
        if group is None or group.group_id != group_id:
            return
        if group.status != EXITING:
            return

        write_audit_log(f"[V2][STAGGER_EXPIRY] force-exiting stragglers group={group_id}")

        for leg in group.open_legs():
            self._force_exit_leg(group, leg)

        if group.all_closed():
            self._finalize_group(group)

    # --------------------------------------------------------------------
    # LEG CLOSE (natural) + FORCE EXIT (straggler)
    # --------------------------------------------------------------------

    def _close_leg(self, group, leg, *, reason, ltp_hint=None):
        """Natural close: GTT is firing/fired. Resolve exit price + record."""
        # Atomic claim of this leg — same guard the backstop path uses, so a
        # simultaneous tick + backstop close can never both proceed.
        with self._mutex:
            if not leg.open:
                return
            leg.open = False
        exit_price = self._resolve_exit_price(leg.symbol, ltp_hint)
        leg.exit_price  = exit_price
        leg.exit_reason = reason
        self._record_leg_exit(group, leg)
        write_audit_log(
            f"[V2][LEG_CLOSE] class={leg.trade_class} {leg.symbol} "
            f"reason={reason} exit={exit_price} pnl={leg.realized_pnl()}"
        )

    def _force_exit_leg(self, group, leg, reason: str = "GROUP_FORCE_EXIT"):
        """
        Straggler at window expiry (or EOD square-off). Check-then-cancel: if
        GTT already triggered, let it win (resolve naturally). Else cancel GTT
        and buy back. `reason` labels the leg exit (GROUP_FORCE_EXIT for the
        stagger path, EOD_SQUAREOFF when called from the EOD job).
        """
        # Atomically claim the leg first. If the backstop monitor or a late
        # tick already closed it during the window, do nothing.
        with self._mutex:
            if not leg.open:
                return
            leg.open = False

        if leg.paper:
            # Paper: just close at current LTP.
            exit_price = self._resolve_exit_price(leg.symbol, None)
            leg.exit_price  = exit_price
            leg.exit_reason = reason
            self._record_leg_exit(group, leg)
            write_audit_log(
                f"[V2][FORCE_EXIT][PAPER] class={leg.trade_class} {leg.symbol} "
                f"exit={exit_price} reason={reason}"
            )
            return

        # LIVE: check GTT status first (trigger wins).
        already_triggered = self._gtt_already_triggered(leg.gtt_id)
        if already_triggered:
            write_audit_log(
                f"[V2][FORCE_EXIT] class={leg.trade_class} GTT already triggered "
                f"— letting it resolve naturally"
            )
            exit_price = self._resolve_exit_price(leg.symbol, None)
            leg.exit_price  = exit_price
            leg.exit_reason = "GTT_TRIGGERED"
            self._record_leg_exit(group, leg)
            return

        # Cancel GTT, then buy back to close the short.
        if leg.gtt_id:
            try:
                self.executor.cancel_gtt(leg.gtt_id)
            except Exception as e:
                write_audit_log(f"[V2][FORCE_EXIT] cancel_gtt failed {leg.gtt_id}: {e}")

        try:
            self.executor.place_buy_exit(
                symbol=leg.symbol, qty=leg.qty, reason=reason
            )
        except Exception as e:
            write_audit_log(
                f"[V2][FORCE_EXIT][FATAL] buy_exit failed {leg.symbol}: {e}"
            )

        exit_price = self._resolve_exit_price(leg.symbol, None)
        leg.exit_price  = exit_price
        leg.exit_reason = reason
        self._record_leg_exit(group, leg)
        write_audit_log(
            f"[V2][FORCE_EXIT][LIVE] class={leg.trade_class} {leg.symbol} "
            f"exit={exit_price} reason={reason} pnl={leg.realized_pnl()}"
        )

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
        """LTPStore/REST resolution (mirrors gtt_monitor chain, lightweight)."""
        if ltp_hint and ltp_hint > 0:
            return float(ltp_hint)
        prem = self._live_premium(symbol)
        return prem

    # --------------------------------------------------------------------
    # FINALIZE
    # --------------------------------------------------------------------

    def _finalize_group(self, group):
        with self._mutex:
            if group.status == CLOSED:
                return
            total = sum(
                (lg.realized_pnl() or 0.0) for lg in group.legs.values()
            )
            group.status = CLOSED
            self._persist_group(group, status=CLOSED, realized_pnl=total)
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            write_audit_log(
                f"[V2][CLOSED] group={group.group_id} realized_pnl={total:.2f} "
                f"legs={len(group.legs)} — V2 free to re-elect"
            )
            self._group = None   # release: V2 can elect again

    def _abort_group(self, group):
        with self._mutex:
            group.status = CLOSED
            self._persist_group(group, status=CLOSED, realized_pnl=0.0)
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._group = None

    # ====================================================================
    # DB SEAM — paper (known-good) + live (wired to trades_repo)
    # ====================================================================

    def _paper_record_entry(self, group, leg):
        """
        V2 bypasses PaperTradeRecorder.record_entry because that path enforces
        one-open-trade-per-(strategy+side) — which would silently drop V2's
        slave legs (all same side) — and recomputes qty from single-lot cfg
        rather than per-class lots. Instead V2 inserts directly via
        insert_paper_trade with this leg's own qty + group columns.

        NOTE: insert_paper_trade must accept group_id/trade_class kwargs
        (additive, nullable) — paired edit in paper_trades_repo.py.
        """
        import uuid
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
                rr=0.0,                      # V2 uses pct-derived levels, not rr here
                lots=lots,
                lot_size=lot_size,
                qty=leg.qty,
                trade_direction="SHORT",
                group_id=group.group_id,     # additive kwargs (paired repo edit)
                trade_class=leg.trade_class,
            )
            write_audit_log(
                f"[V2][PAPER_ENTRY] paper_trade inserted pid={pid} "
                f"class={leg.trade_class} {leg.symbol} qty={leg.qty} "
                f"group={group.group_id}"
            )
        except TypeError as e:
            # insert_paper_trade doesn't yet accept group kwargs — log clearly.
            write_audit_log(
                f"[V2][PAPER_ENTRY_SEAM] insert_paper_trade group kwargs not "
                f"yet wired: {e}"
            )
        except Exception as e:
            write_audit_log(f"[V2][PAPER_ENTRY_FAIL] {leg.symbol} ERR={e}")

    def _paper_record_exit(self, group, leg):
        """
        Direct close via close_paper_trade (proven signature from recorder):
        it computes signed net_pnl + charges itself, direction-aware.
        """
        try:
            from app.db.paper_trades_repo import close_paper_trade
            if leg.db_trade_id is not None:
                close_paper_trade(
                    paper_trade_id=leg.db_trade_id,
                    exit_price=leg.exit_price,
                    exit_reason=leg.exit_reason or "GROUP_FORCE_EXIT",
                    trade_direction="SHORT",
                )
                write_audit_log(
                    f"[V2][PAPER_EXIT] pid={leg.db_trade_id} "
                    f"class={leg.trade_class} reason={leg.exit_reason} "
                    f"exit={leg.exit_price}"
                )
        except Exception as e:
            write_audit_log(f"[V2][PAPER_EXIT_FAIL] {leg.symbol} ERR={e}")

    # --- LIVE seam: wired to trades_repo once schema is confirmed ----------
    # These call signatures are intentionally thin; the actual trades_repo
    # insert/close function names + columns get filled in when trades_repo.py
    # is supplied. close path reuses trades_repo.close_trade() which
    # gtt_monitor already uses.

    def _live_record_entry(self, group, leg, order_id):
        """
        V2 does its OWN direct insert into `trades` so it can populate
        group_id + trade_class (the new SCALP_V2 columns) WITHOUT touching
        trades_repo.insert_trade — which has no group params and is shared
        by SCALP_V1. Column list mirrors insert_trade exactly, plus the two
        additive nullable columns.
        """
        import uuid
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
                    trade_id,
                    STRATEGY_ID,
                    f"V2_{leg.trade_class}",
                    leg.symbol,
                    leg.token,
                    int(time.time()),
                    leg.entry_price,
                    leg.qty,
                    order_id,                 # buy_order_id col holds the entry order id
                    leg.sl,
                    leg.gtt_id,               # sl_order_id col holds the GTT id (mirrors SCALP_V1)
                    leg.tp,
                    "PCT",                    # tp_mode — V2 uses percentage-derived levels
                    "PROTECTED" if leg.gtt_id else "SELL_PLACED",
                    "SHORT",
                    group.group_id,
                    leg.trade_class,
                ),
            )
            conn.commit()
            write_audit_log(
                f"[V2][LIVE_ENTRY] trades row inserted trade_id={trade_id} "
                f"class={leg.trade_class} {leg.symbol} group={group.group_id}"
            )
        except Exception as e:
            write_audit_log(
                f"[V2][LIVE_ENTRY_FAIL] {leg.symbol} ERR={e}"
            )

    def _live_record_exit(self, group, leg):
        try:
            from app.db.trades_repo import close_trade
            if leg.db_trade_id is not None:
                close_trade(
                    trade_id=leg.db_trade_id,
                    exit_price=leg.exit_price,
                    exit_order_id=None,
                    exit_reason=leg.exit_reason or "GROUP_FORCE_EXIT",
                )
        except Exception as e:
            write_audit_log(f"[V2][LIVE_EXIT_FAIL] {leg.symbol} ERR={e}")

    def _record_leg_exit(self, group, leg):
        if leg.paper:
            self._paper_record_exit(group, leg)
        else:
            self._live_record_exit(group, leg)

    # ====================================================================
    # GROUP PERSISTENCE (scalp_v2_groups)
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