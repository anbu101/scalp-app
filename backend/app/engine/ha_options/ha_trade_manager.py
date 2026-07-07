# backend/app/engine/ha_options/ha_trade_manager.py
"""
HA Trade Manager
================
Handles all trade lifecycle for HA_V1.

EXIT DESIGN (CRITICAL):
  TP  → checked on EVERY TICK (live price).  As soon as ltp >= tp_price the
        trade is closed immediately, regardless of candle boundary.

  SL  → checked on CANDLE CLOSE only.  Wicks that cross SL mid-candle are
        ignored; only a candle that CLOSES at or below SL triggers an exit.

  This applies identically to PAPER and LIVE modes.

  DEGRADED-READ SAFETY (2026-07-06 fd-exhaustion incident):
    An OPEN LIVE trade is ALWAYS live-managed, no matter what the config
    read resolves to this instant. Previously check_tp_on_tick /
    check_sl_on_close branched on _mode() FIRST; during a degraded config
    read _mode() resolved to the default (PAPER) and the PAPER branch never
    looked at self._live — so a live position went UNMONITORED for the
    duration of the fault. Both monitors now check self._live before
    resolving mode. Additionally _mode() itself now HOLDS the last-known
    effective mode on a degraded read (instead of adopting the default),
    which also stops the PAPER→LIVE transition-alert spam a flapping read
    produced (41 pairs on 2026-07-06).

ENTRY FILL RESOLUTION (FIX):
  HA entry runs on the WS tick thread (via _on_candle_close), and HA's whole
  exit design is "TP on every tick".  The previous _enter_live() blocked that
  thread up to 120s polling for the fill — freezing TP/SL monitoring for the
  ENTIRE HA universe while one entry waited.  It also could not tell a slow
  fill apart from a rejected order, so a dead order would time out and then
  proceed to record a phantom position and monitor it.

  NEW (Option 1 — background confirm):
    enter() places the BUY, records the trade in self._live using the LIMIT
    price as the provisional entry, sets up monitoring, and returns True
    IMMEDIATELY — the tick thread is never blocked.  A background thread then
    confirms the true fill:
      COMPLETE → patch entry_price (in-memory + DB) for accurate P&L
      DEAD     → remove the phantom from self._live, close the DB row, and call
                 the engine's on_entry_dead(symbol, side) hook to roll back
                 monitoring + signal-engine state
      timeout  → leave the provisional entry, log RECONCILE_NEEDED (120s cap)

  SL/TP are FILL-INDEPENDENT:
    SL = the signal's red-candle low (fixed).
    TP = computed from the SIGNAL entry_ltp (NOT the fill) so it never moves
         when the fill lands.  Only the recorded entry_price is patched.

EXECUTION MODES:
  LIVE  → real broker orders.
  PAPER → simulated orders via PaperTradeRecorder.
  OFF   → strategy still collects ticks/candles/EMA, but NO NEW ENTRIES.
          Exits for an already-open trade still run.

LIVE ORDER DESIGN:
  Entry  → protected limit BUY at LTP × 1.03 (3% buffer).
  TP     → NO GTT.  Tick-level monitor handles TP via limit SELL.
  SL     → NO GTT.  Candle-close monitor places a limit SELL when close <= sl.

PAPER EOD/MTM SQUARE-OFF (FIX):
  eod_squareoff() in PAPER mode previously only cleared signal-engine flags and
  returned WITHOUT closing the open paper trade in the DB. The MTM guard calls
  eod_squareoff() on a breach (and re-calls it every ~3s while day-blocked), so
  the position stayed OPEN forever while the flags were cleared on a loop. Now
  the PAPER path force-exits every open paper trade (via PaperTradeRecorder,
  the same call used by the TP/SL paths) before clearing flags — so a paper
  position is actually closed on EOD / MTM square-off.
"""

import time
import uuid
import threading
from datetime import datetime
from typing import Optional, Dict

from app.risk.strategy_max_loss_guard import evaluate_strategy_risk
from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore
from app.config.strategy_loader import (
    load_strategy_config,
    load_strategy_config_ex,
)
from app.config.global_loader import load_global_config
from app.db.trades_repo import insert_trade, close_trade, update_gtt
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.db.paper_trades_repo import (
    get_open_paper_trades_by_side,
)
from app.engine.ha_options.ha_signal_engine import HASignalEngine
from app.event_bus.inapp_events import record_alert
from app.api.telegram_api import (
    notify_trade_entry,
    notify_sl_exit,
    notify_tp_exit,
    notify_manual_exit,
)


# Background entry-fill confirmation (LIVE entries).
# Never on the critical path — monitoring is already live by the time this
# runs; it only patches the recorded entry_price or rolls back a dead order.
_ENTRY_FILL_CONFIRM_CAP_S   = 120
_ENTRY_FILL_POLL_INTERVAL_S = 2

# Terminal "dead" Kite order statuses — order never opened a position.
_DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}

# ── EXIT_SELL_BACKOFF constants ───────────────────────────────────
# Applies to the app-driven SELL path (SL / EOD / MANUAL). After a failed
# exit sell, suppress re-entry for _EXIT_RETRY_COOLDOWN_S; after
# _EXIT_MAX_ATTEMPTS consecutive fails, HALT that side (no more sells),
# alert Telegram-critical, and require manual/reconcile intervention.
# Rationale: a naked-long exit that the broker structurally rejects (relay
# margin math / non-whitelisted IP) must NEVER spin at tick rate.
_EXIT_RETRY_COOLDOWN_S = 15
_EXIT_MAX_ATTEMPTS     = 5


# ──────────────────────────────────────────────────────────────────
# Live trade record
# ──────────────────────────────────────────────────────────────────

class _LiveTrade:
    __slots__ = (
        "trade_id", "symbol", "side", "qty",
        "entry_price", "sl_price", "tp_price",
        "entry_order_id", "fill_confirmed",
        "tp_gtt_id",
    )

    def __init__(self, trade_id, symbol, side, qty,
                 entry_price, sl_price, tp_price,
                 entry_order_id=None, fill_confirmed=False,
                 tp_gtt_id=None):
        self.trade_id       = trade_id
        self.symbol         = symbol
        self.side           = side
        self.qty            = qty
        self.entry_price    = entry_price
        self.sl_price       = sl_price
        self.tp_price       = tp_price
        self.entry_order_id = entry_order_id
        self.fill_confirmed = fill_confirmed
        self.tp_gtt_id      = tp_gtt_id     # broker-side TP backstop GTT id


# ──────────────────────────────────────────────────────────────────
# Trade manager
# ──────────────────────────────────────────────────────────────────

class HATradeManager:

    # Limit-order buffer above LTP for BUY entries.
    ENTRY_LIMIT_BUFFER = 1.03   # LTP × 1.03

    def __init__(
        self,
        strategy_id: str,
        trade_mode: str,
        executor,
        signal_engine: HASignalEngine,
        config: dict,
        engine=None,            # back-reference to HAOptionsTickEngine for hooks
    ):
        self.strategy_id   = strategy_id
        self._startup_mode = trade_mode
        self.executor      = executor
        self.signal_engine = signal_engine
        self.config        = config
        self._engine       = engine    # may be set later via attach_engine()

        # Fallbacks (constructor-time values)
        self._rr_fallback       = float(config.get("risk_reward_ratio", 2.0))
        self._lot_fallback      = int(config.get("quantity", {}).get("lots", 1))
        self._lot_size_fallback = int(config.get("quantity", {}).get("lot_size", 65))

        # Live trade state — keyed by side ("CE" / "PE")
        self._live: Dict[str, _LiveTrade] = {}

        # Guard: prevents double-exit when TP fires from two rapid ticks
        # before the first exit finishes placing the sell order.
        self._tp_exit_in_progress: set = set()   # set of sides currently exiting

        # ── EXIT_SELL_BACKOFF BEGIN ───────────────────────────────
        # Per-side failure tracking for the app-driven SELL path (SL / EOD /
        # MANUAL only — TP is GTT-exclusive as of 2026-07-07). A failed live
        # exit used to return without clearing _live and with no backoff, so
        # check_*_on_* re-entered _exit_live on every subsequent tick/candle —
        # producing 1000+ rejected resells in one incident (naked-long exit
        # rejected by relay margin + non-whitelisted IP). These bound retries.
        self._exit_fail_count: Dict[str, int]   = {}   # side -> consecutive fails
        self._exit_fail_at:    Dict[str, float] = {}   # side -> last-fail epoch
        self._exit_halted:     set              = set()  # sides given up on (alerted)
        # Throttle for the tick-path TP reconcile probe (≤1/sec/side).
        self._tp_recon_probe_at: Dict[str, float] = {}
        # ── EXIT_SELL_BACKOFF END ─────────────────────────────────

        # Tracks the last EFFECTIVE mode so _mode() can fire an edge-triggered
        # notice the instant the effective mode changes (e.g. PAPER→LIVE). None
        # until the first _mode() call seeds it.
        self._last_effective_mode: Optional[str] = None

        # ── DEGRADED_HOLD BEGIN ───────────────────────────────────
        # True when the most recent _mode() call hit a degraded config read
        # (file present but unreadable this instant — e.g. fd exhaustion).
        # enter() refuses new entries while this is set: a degraded read also
        # means _rr()/_qty() would silently use DEFAULTS instead of the user's
        # tuned values, so entering under a degraded read risks a wrong-size
        # live order. Exits are unaffected (they use the SL/TP stored on the
        # trade at entry time).
        self._cfg_read_degraded: bool = False
        # ── DEGRADED_HOLD END ─────────────────────────────────────

        # ── GLOBAL_ARB_GATE BEGIN ─────────────────────────────────
        # Single global "HA is occupied" authority for the one-trade-at-a-time
        # arbitration window (applies to BOTH paper and live). "Occupied" means
        # EITHER an arbitration election is pending OR a trade is open. The
        # open-trade half is computed from real state (self._live / open paper
        # rows) so it can never desync; only the pending half is a flag, with
        # exactly two transitions (arm_pending / clear_pending).
        #
        # The window itself lives in the tick engine; the manager owns the gate
        # because it is the authority on open/closed truth (it owns self._live
        # and the exit paths). Every exit path calls clear_pending() defensively
        # so the gate releases the instant a trade closes.
        self._arb_pending: bool = False
        self._arb_lock = threading.Lock()
        # ── GLOBAL_ARB_GATE END ───────────────────────────────────

    def attach_engine(self, engine):
        """Wire the tick engine back-reference (for on_entry_dead rollback)."""
        self._engine = engine

    # ── GLOBAL_ARB_GATE BEGIN ─────────────────────────────────────
    # Public gate API used by the tick engine's arbitration window.

    def _has_open_trade(self) -> bool:
        """True if ANY HA position is currently open (live OR paper). Computed
        from real state so it can never drift from a stale flag."""
        if self._live:
            return True
        try:
            for side in ("CE", "PE"):
                if get_open_paper_trades_by_side(
                    strategy_name=self.strategy_id, side=side,
                ):
                    return True
        except Exception as e:
            # Fail SAFE: if we can't tell, treat as occupied so we never open a
            # second concurrent trade. A transient False-positive only delays an
            # entry; a False-negative would breach the one-trade invariant.
            write_audit_log(f"[HA][ARB][OPEN_CHECK_ERR] {e} — assuming occupied")
            return True
        return False

    def ha_is_occupied(self) -> bool:
        """The global gate. Occupied = an election is pending OR a trade is
        open. The tick engine checks this before arming a new window and before
        entering an elected winner."""
        with self._arb_lock:
            if self._arb_pending:
                return True
        return self._has_open_trade()

    def arm_pending(self) -> bool:
        """Mark the gate occupied because an arbitration window has been armed.
        Returns True if newly armed, False if already occupied (caller must NOT
        arm a second window). Atomic under the lock."""
        with self._arb_lock:
            if self._arb_pending:
                return False
            # Also refuse if a trade is already open (belt-and-suspenders; the
            # caller checks ha_is_occupied first, but state can change between).
            if self._has_open_trade():
                return False
            self._arb_pending = True
            return True

    def clear_pending(self) -> None:
        """Release the pending half of the gate. Called by the tick engine after
        an election resolves (win or cancel), and defensively by every exit path
        so the gate can never stay stuck occupied after a trade closes. Idempotent."""
        with self._arb_lock:
            self._arb_pending = False
    # ── GLOBAL_ARB_GATE END ───────────────────────────────────────

    # ── Live config readers ───────────────────────────────────────

    def _mode(self) -> str:
        """
        Returns the current EFFECTIVE trade mode from live config, honoring a
        runtime switch (Decision: honor immediately). Valid values: "LIVE",
        "PAPER", "OFF".

        Unlike BB (which pins PAPER-until-restart), HA honors a mid-session
        PAPER→LIVE switch: the next entry goes live. _enter_live fails SAFE if
        the executor / trade session isn't ready (refuse + DEAD_ENTRY alert), so
        honoring the switch can never crash or silently paper-fill a live entry.

        An edge-triggered notice (log + in-app alert) fires the instant the
        effective mode CHANGES, so the switch is never silent. This method is
        called on every tick/candle, so the notice is gated on a real change to
        avoid spam.

        ── DEGRADED_HOLD ──
        A DEGRADED config read (file present but unreadable this instant — fd
        exhaustion, transient I/O) previously returned the loader's in-memory
        DEFAULT, whose execution mode is PAPER. With HA configured LIVE, every
        degraded/clean read pair produced a spurious PAPER→LIVE "transition"
        (41 alert-pairs on 2026-07-06) — and worse, mis-routed tick/candle
        processing down the paper path while a live position was open.

        NOW: on a degraded read we HOLD the last-known effective mode (or the
        startup mode if none was ever observed), fire NO transition notice, and
        set self._cfg_read_degraded so enter() refuses new entries this cycle.
        A degraded read is an I/O fault, not a user decision — it must never
        masquerade as a mode switch.
        """
        # ── DEGRADED_HOLD BEGIN ───────────────────────────────────
        cfg, degraded = load_strategy_config_ex(self.strategy_id)
        self._cfg_read_degraded = degraded

        if degraded:
            held = self._last_effective_mode or self._startup_mode
            # Loud in the audit log (the loader already logged READ_DEGRADED
            # with the underlying error), but NO transition alert — nothing
            # actually changed.
            write_audit_log(
                f"[HA][TRADE_MODE][DEGRADED_HOLD] config unreadable this call — "
                f"holding effective mode {held}; new entries refused this cycle."
            )
            return held

        m = cfg.get("trade_execution_mode", self._startup_mode)
        if m not in ("LIVE", "PAPER", "OFF"):
            m = self._startup_mode
        # ── DEGRADED_HOLD END ─────────────────────────────────────

        self._note_mode_transition(m)
        return m

    def _note_mode_transition(self, effective_mode: str) -> None:
        """
        Edge-triggered: fire a log + in-app alert ONLY when the effective mode
        actually changes. Seeds silently on the first observation so startup
        doesn't fire a spurious transition. Never raises.

        NOTE (DEGRADED_HOLD): this is only ever called from _mode() on a CLEAN
        config read — a degraded read holds the previous mode and never reaches
        here, so a flapping read can no longer fire transition alerts.
        """
        prev = self._last_effective_mode
        if prev == effective_mode:
            return

        # First observation — seed without alerting.
        if prev is None:
            self._last_effective_mode = effective_mode
            return

        self._last_effective_mode = effective_mode

        try:
            write_audit_log(
                f"[HA][TRADE_MODE] Effective mode changed {prev} → {effective_mode} "
                f"(startup={self._startup_mode}) — takes effect on the next entry/exit"
            )
        except Exception:
            pass

        # A PAPER→LIVE switch is the safety-critical one: prompt the user to
        # verify the trade session is authenticated, since the next entry will
        # attempt a real broker order (and will fail SAFE with a DEAD_ENTRY
        # alert if the session isn't ready).
        try:
            if effective_mode == "LIVE":
                record_alert(
                    code="RECONCILE_NEEDED",
                    message=(
                        f"HA_V1 switched to LIVE mid-session — the next entry will "
                        f"place a REAL broker order. Verify the trade session is "
                        f"connected. (If it isn't, the entry is refused safely with "
                        f"an alert; no silent paper fill.)"
                    ),
                    severity="warning",
                    strategy_id=self.strategy_id,
                    mode="live",
                )
            else:
                record_alert(
                    code="RECONCILE_NEEDED",
                    message=f"HA_V1 effective mode is now {effective_mode}.",
                    severity="info",
                    strategy_id=self.strategy_id,
                    mode=effective_mode.lower(),
                )
        except Exception:
            pass

    def is_off(self) -> bool:
        """True when the strategy is in OFF mode — new entries suppressed."""
        return self._mode() == "OFF"

    def _rr(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "risk_reward_ratio", self._rr_fallback
                ) or self._rr_fallback
            )
        except Exception:
            return self._rr_fallback

    def _qty(self) -> int:
        try:
            cfg = load_strategy_config(self.strategy_id)
            lots = int(cfg.get("quantity", {}).get("lots", self._lot_fallback) or self._lot_fallback)
            size = int(cfg.get("quantity", {}).get("lot_size", self._lot_size_fallback) or self._lot_size_fallback)
            return lots * size
        except Exception:
            return self._lot_fallback * self._lot_size_fallback

    def _apply_target_override(self, entry_price: float, rr_tp: float) -> float:
        """
        Returns the effective TP price.
        If target_override.enabled and points > 0, returns entry_price + points;
        otherwise returns the R:R computed value unchanged.
        """
        try:
            override = load_strategy_config(self.strategy_id).get(
                "target_override", {}
            )
            if override.get("enabled") and float(override.get("points", 0)) > 0:
                fixed_tp = entry_price + float(override["points"])
                write_audit_log(
                    f"[HA] target_override active: "
                    f"fixed_tp={fixed_tp:.2f} "
                    f"(entry={entry_price:.2f} + {override['points']} pts)"
                )
                return fixed_tp
        except Exception:
            pass
        return rr_tp

    # ── ENTRY ─────────────────────────────────────────────────────

    def enter(self, symbol: str, side: str, entry_ltp: float, sl_price: float) -> bool:
        """
        Place entry order.  Returns True once the order is PLACED and the trade
        is registered for monitoring (Option 1 — the true fill is confirmed in
        the background; True no longer means "filled", it means "placed and
        being monitored").

        TP calculation (FILL-INDEPENDENT — uses the SIGNAL entry_ltp):
          1. Fixed target override (target_override.enabled = True)
             TP = entry_ltp + override.points
          2. R:R ratio (default)
             TP = entry_ltp + (entry_ltp - sl_price) × RR
        """
        mode = self._mode()

        # ── DEGRADED_HOLD BEGIN ── entry refusal on a degraded config read ──
        # If the config could not be read cleanly this instant, _rr()/_qty()/
        # _apply_target_override below would silently use the DEFAULT config
        # (e.g. lots=1) instead of the user's tuned values — a live order at
        # the wrong size. Refuse the entry outright; the signal is consumed
        # (matching every other entry-refusal path) and the fault is loud.
        if self._cfg_read_degraded:
            write_audit_log(
                f"[HA][ENTRY_REFUSED_DEGRADED] {symbol} side={side} — config "
                f"read degraded this cycle; entry refused (params would fall "
                f"back to defaults). Fix the machine's I/O/fd issue."
            )
            record_alert(
                code="RECONCILE_NEEDED",
                message=(
                    f"{symbol} ({side}): HA_V1 entry refused — strategy config "
                    f"could not be read cleanly (I/O fault). No order placed."
                ),
                severity="warning",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode=mode.lower(),
            )
            return False
        # ── DEGRADED_HOLD END ─────────────────────────────────────

        if mode == "OFF":
            write_audit_log(
                f"[HA][OFF][ENTRY_SUPPRESSED] {symbol} side={side} "
                f"— strategy is OFF, no entry taken"
            )
            return False

        rr   = self._rr()
        qty  = self._qty()

        risk_block = evaluate_strategy_risk(self.strategy_id)
        if risk_block:
            write_audit_log(
                f"[HA][RISK_BLOCK] {symbol} side={side} reason={risk_block}"
            )
            return False

        risk = entry_ltp - sl_price
        if risk <= 0:
            write_audit_log(
                f"[HA][ENTRY_ABORT] {symbol} invalid risk "
                f"entry={entry_ltp:.2f} sl={sl_price:.2f}"
            )
            return False

        # TP from SIGNAL entry (fill-independent), then optional override.
        rr_tp    = entry_ltp + risk * rr
        tp_price = self._apply_target_override(entry_ltp, rr_tp)

        write_audit_log(
            f"[HA][ENTRY] {symbol} side={side} mode={mode} "
            f"entry={entry_ltp:.2f} sl={sl_price:.2f} tp={tp_price:.2f} "
            f"rr={rr} qty={qty}"
        )

        if mode == "PAPER":
            return self._enter_paper(symbol, side, entry_ltp, sl_price, tp_price)
        else:
            return self._enter_live(symbol, side, entry_ltp, sl_price, tp_price, qty)

    # ── PAPER ENTRY ───────────────────────────────────────────────

    def _enter_paper(
        self, symbol: str, side: str,
        entry_ltp: float, sl_price: float, tp_price: float,
    ) -> bool:
        paper_id = PaperTradeRecorder.record_entry(
            strategy_id=self.strategy_id,
            symbol=symbol,
            token=0,
            entry_price=entry_ltp,
            sl_price=sl_price,
            tp_price=tp_price,
            candle_ts=int(datetime.now().timestamp()),
        )
        if not paper_id:
            write_audit_log(
                f"[HA][PAPER][BLOCKED] {symbol} already has open {side} trade"
            )
            return False
        write_audit_log(f"[HA][PAPER][OK] {symbol} id={paper_id}")
        return True

    # ── LIVE ENTRY (Option 1 — non-blocking) ──────────────────────

    def _enter_live(
        self, symbol: str, side: str,
        entry_ltp: float, sl_price: float, tp_price: float, qty: int,
    ) -> bool:
        if side in self._live:
            write_audit_log(f"[HA][LIVE][SKIP] {side} already has open trade")
            return False

        # ── Guard: no executor → fail-safe (clean alert, no crash) ──────
        # The trade manager can resolve to LIVE at runtime even if the engine
        # was started before a trade session existed. Rather than crash with
        # 'NoneType has no attribute broker_manager', refuse the entry cleanly
        # and alert so the user knows the broker/trade session isn't wired.
        if self.executor is None or getattr(self.executor, "broker_manager", None) is None:
            write_audit_log(
                f"[HA][LIVE][NO_EXECUTOR] {symbol} side={side} — executor/trade "
                f"session not ready; live entry refused (fail-safe)."
            )
            record_alert(
                code="DEAD_ENTRY",
                message=(
                    f"{symbol} ({side}): live entry refused — broker/trade session "
                    f"not ready. Reconnect the trade session (Connections) and retry. "
                    f"No order was placed."
                ),
                severity="error",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode="live",
            )
            return False

        # Place protected limit BUY (3% above LTP)
        limit_price = round(round(entry_ltp * self.ENTRY_LIMIT_BUFFER / 0.05) * 0.05, 2)

        write_audit_log(
            f"[HA][LIVE][BUY] {symbol} ltp={entry_ltp:.2f} limit={limit_price:.2f}"
        )

        try:
            kite = self.executor.broker_manager.get_trade_kite()
            if not kite:
                raise RuntimeError("Broker not ready")

            order_params = dict(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_BUY,
                quantity=qty,
                order_type=kite.ORDER_TYPE_LIMIT,
                price=limit_price,
                product=kite.PRODUCT_NRML,
            )
            order_id = self.executor._relay_call(
                relay_fn=lambda r: r.place_order(**order_params),
                direct_fn=lambda: kite.place_order(**order_params),
                op_name="HA_BUY",
                symbol=symbol,
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][BUY_FAIL] {symbol} ERR={repr(e)}")
            record_alert(
                code="DEAD_ENTRY",
                message=f"{symbol} ({side}): buy order placement failed ({e}). No position opened.",
                severity="error",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode="live",
            )
            return False

        # ── Record trade IMMEDIATELY with provisional (limit) entry ──
        # SL/TP are fill-independent (SL=red low, TP from signal entry), so
        # monitoring is correct from this instant. The background thread will
        # patch entry_price to the true fill for P&L accuracy.
        trade_id = str(uuid.uuid4())
        try:
            insert_trade(
                trade_id=trade_id,
                strategy_id=self.strategy_id,
                slot=side,
                symbol=symbol,
                token=0,
                entry_price=limit_price,
                qty=qty,
                buy_order_id=order_id,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_mode="GTT",
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_FAIL] {e}")

        self._live[side] = _LiveTrade(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=limit_price,     # provisional
            sl_price=sl_price,
            tp_price=tp_price,
            entry_order_id=order_id,
            fill_confirmed=False,
        )

        write_audit_log(
            f"[HA][LIVE][ENTRY_PROVISIONAL] {symbol} side={side} "
            f"entry≈{limit_price:.2f} (limit; fill pending) "
            f"sl={sl_price:.2f} tp={tp_price:.2f} — monitoring live"
        )

        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "side": side,
                "entry_price": limit_price,
                "quantity": qty,
                "sl": sl_price,
                "tp": tp_price,
            })
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][ENTRY_FAIL] {e}")

        # ── Confirm fill in the background (never blocks the tick thread) ──
        self._spawn_fill_confirm(side, trade_id, order_id, symbol)

        return True

    # ── BACKGROUND FILL CONFIRMATION ──────────────────────────────

    def _spawn_fill_confirm(self, side, trade_id, order_id, symbol):
        threading.Thread(
            target=self._confirm_fill_worker,
            args=(side, trade_id, order_id, symbol),
            daemon=True,
            name=f"ha-fill-{side}-{trade_id[:8]}",
        ).start()

    def _confirm_fill_worker(self, side, trade_id, order_id, symbol):
        """
        Poll the order book for the true fill (≤120s).

        COMPLETE → patch entry_price (in-memory + DB) for accurate P&L.
        DEAD     → remove the phantom from self._live, close the DB row, and
                   roll back engine monitoring + signal state via on_entry_dead.
        timeout  → leave the provisional entry; log RECONCILE_NEEDED.

        Uses get_order_fill if available (distinguishes DEAD from pending);
        falls back to get_last_avg_price if the executor lacks it.
        """
        start = time.time()

        while time.time() - start < _ENTRY_FILL_CONFIRM_CAP_S:
            status = None
            avg    = 0.0
            try:
                if hasattr(self.executor, "get_order_fill"):
                    info   = self.executor.get_order_fill(order_id)
                    status = (info.get("status") or "").upper()
                    avg    = info.get("avg_price") or 0.0
                else:
                    avg = self.executor.get_last_avg_price(order_id) or 0.0
            except Exception as e:
                write_audit_log(f"[HA][FILL_POLL_ERR] {symbol} order_id={order_id} ERR={e}")
                time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)
                continue

            if status == "COMPLETE" or (status is None and avg > 0):
                if avg > 0:
                    self._apply_confirmed_fill(side, trade_id, symbol, float(avg))
                    return
                # COMPLETE but price not yet populated — wait one more cycle.

            elif status in _DEAD_ORDER_STATUSES:
                self._handle_dead_entry(side, trade_id, symbol, status)
                return

            time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)

        write_audit_log(
            f"[HA][FILL_TIMEOUT][RECONCILE_NEEDED] {symbol} side={side} "
            f"order_id={order_id} — true fill not confirmed in "
            f"{_ENTRY_FILL_CONFIRM_CAP_S}s; recorded entry remains the limit estimate."
        )
        record_alert(
            code="FILL_TIMEOUT",
            message=f"{symbol} ({side}): fill not confirmed in {_ENTRY_FILL_CONFIRM_CAP_S}s; recorded entry is the limit estimate and will be corrected on reconcile.",
            severity="info",
            strategy_id=self.strategy_id,
            symbol=symbol,
            mode="live",
        )

    def _apply_confirmed_fill(self, side, trade_id, symbol, fill_price: float):
        """Patch entry_price to the true fill — only if still the active trade."""
        trade = self._live.get(side)
        if trade is None or trade.trade_id != trade_id:
            write_audit_log(
                f"[HA][FILL_STALE] {symbol} side={side} trade_id={trade_id} "
                f"fill={fill_price:.2f} — trade no longer active, skipping"
            )
            return

        trade.entry_price    = fill_price
        trade.fill_confirmed = True

        # ── ENTRY CORRECTION (limit → true fill) ──────────────────────
        # Allowed by migration 009 ONLY while state='BUY_PLACED'. This MUST run
        # BEFORE the GTT link below flips the row to PROTECTED (which re-locks
        # entry_price). If this UPDATE fails, DO NOT place a TP GTT — the row is
        # not in a known-good state and the position may already be exiting.
        entry_patched = False
        try:
            from app.db.sqlite import get_conn
            conn = get_conn()
            cur = conn.execute(
                "UPDATE trades SET entry_price = ? "
                "WHERE trade_id = ? AND exit_time IS NULL AND state = 'BUY_PLACED'",
                (fill_price, trade_id),
            )
            conn.commit()
            entry_patched = (cur.rowcount > 0)
        except Exception as e:
            write_audit_log(f"[HA][FILL_DB_UPDATE_FAIL] {symbol} trade_id={trade_id} ERR={e}")

        write_audit_log(
            f"[HA][LIVE][FILL_CONFIRMED] {symbol} side={side} "
            f"entry updated → {fill_price:.2f}"
        )

        # ── HA_TP_GTT BEGIN ── broker-side TP backstop ───────────────
        # Place a TP-ONLY GTT so the broker exits at TP even if the app goes
        # blind (lost tick sub / crash / kill) — the exact failure that left a
        # position naked past TP. TP-only (not OCO) because HA's SL is candle-
        # close-evaluated and a broker SL-GTT would fire on intra-candle wicks.
        # Best-effort: a GTT failure alerts but does NOT break fill-confirm; the
        # app-side tick monitor still guards TP (degrades to prior behaviour, not
        # worse). Linked via update_gtt(), which also flips state → PROTECTED and
        # thereby RE-LOCKS entry_price (must run AFTER the entry correction above).
        if entry_patched:
            try:
                fresh_ltp = LTPStore.get(symbol) or fill_price
                gtt_id = self.executor.place_gtt_tp_only_long(
                    symbol=symbol,
                    qty=trade.qty,
                    tp_price=trade.tp_price,
                    last_price=fresh_ltp,
                )
                trade.tp_gtt_id = str(gtt_id)
                try:
                    update_gtt(trade_id=trade_id, gtt_id=str(gtt_id))
                except Exception as e:
                    write_audit_log(f"[HA][TP_GTT_LINK_FAIL] {symbol} gtt={gtt_id} ERR={e}")
                write_audit_log(
                    f"[HA][LIVE][TP_GTT_PLACED] {symbol} side={side} "
                    f"tp={trade.tp_price:.2f} gtt_id={gtt_id} — broker backstop armed"
                )
            except Exception as e:
                write_audit_log(
                    f"[HA][LIVE][TP_GTT_FAIL] {symbol} side={side} tp={trade.tp_price:.2f} "
                    f"ERR={e} — app-side tick monitor still guards TP (no broker backstop)"
                )
                record_alert(
                    code="RECONCILE_NEEDED",
                    message=(
                        f"{symbol} ({side}): TP backstop GTT could not be placed "
                        f"({e}). App-side TP monitoring is active, but there is no "
                        f"broker-side exit if the app goes offline. Consider a manual "
                        f"GTT or watch the position."
                    ),
                    severity="warning",
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    mode="live",
                )
        # ── HA_TP_GTT END ──

    def _handle_dead_entry(self, side, trade_id, symbol, status):
        """
        Entry order never opened a position. Remove the phantom from in-memory
        state, close the DB row, and roll back engine monitoring + signal state.
        No-op if the trade is no longer the active one.
        """
        trade = self._live.get(side)
        if trade is None or trade.trade_id != trade_id:
            write_audit_log(
                f"[HA][DEAD_ENTRY_STALE] {symbol} side={side} trade_id={trade_id} "
                f"status={status} — trade no longer active"
            )
            return

        write_audit_log(
            f"[HA][DEAD_ENTRY] {symbol} side={side} trade_id={trade_id} "
            f"status={status} — removing phantom, rolling back monitoring"
        )

        # Remove in-memory trade (stops TP/SL checks immediately).
        self._live.pop(side, None)

        # Close the DB row as a no-fill abort.
        try:
            close_trade(
                trade_id=trade_id,
                exit_price=None,
                exit_order_id=None,
                exit_reason="ENTRY_REJECTED",
            )
        except Exception as e:
            write_audit_log(f"[HA][DEAD_ENTRY][DB_CLOSE_FAIL] {symbol} trade_id={trade_id} ERR={e}")

        # Roll back signal-engine state directly.
        try:
            self.signal_engine.notify_exit(side)
        except Exception:
            pass

        # ── GLOBAL_ARB_GATE BEGIN ── release pending half: a dead elected entry
        # must not leave the gate stuck occupied.
        self.clear_pending()
        # ── GLOBAL_ARB_GATE END ──

        # Roll back engine monitoring (remove from _active_trade_symbols).
        if self._engine is not None:
            try:
                self._engine.on_entry_dead(symbol, side)
            except Exception as e:
                write_audit_log(f"[HA][DEAD_ENTRY][ENGINE_HOOK_FAIL] {symbol} ERR={e}")

        # Alert.
        record_alert(
            code="DEAD_ENTRY",
            message=f"{symbol} ({side}): buy order {status.lower()} — phantom removed, monitoring rolled back. No position opened.",
            severity="error",
            strategy_id=self.strategy_id,
            symbol=symbol,
            mode="live",
        )
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({
                "message": (
                    f"HA_V1 entry order {status} for {symbol} ({side})\n"
                    f"trade_id={trade_id} — no position opened.\n"
                    f"Phantom removed, monitoring rolled back."
                ),
                "severity": "warning",
            })
        except Exception:
            pass


    # ══════════════════════════════════════════════════════════════
    # TP-GTT FILL RECONCILIATION
    # Detect a broker TP-only GTT that fired while the app was blind
    # (lost ticks / crashed / restarted) and close the DB row as GTT_TP.
    # DB-driven so it works even after a restart (in-memory _live is empty).
    # ══════════════════════════════════════════════════════════════

    def reconcile_gtt_exits(self):
        """
        For every OPEN live HA trade in the DB, check whether its TP-only GTT has
        fired at the broker. If the GTT is triggered/gone AND the broker position
        is flat, close the DB row as GTT_TP with a resolved exit price, and — if
        the app is still running and holds the trade in memory — clean up _live,
        release the arbitration gate, and notify.

        Called from the tick engine's 30s subscription-retry loop (no new thread).
        Only meaningful in LIVE; paper trades have no broker GTT. Every broker
        read is guarded so a transient failure can NEVER close a trade.
        """
        # Only live mode has broker GTTs to reconcile.
        if self._mode() not in ("LIVE", "OFF"):
            return

        try:
            from app.db.trades_repo import get_open_trades_for_strategy
            open_rows = get_open_trades_for_strategy(self.strategy_id)
        except Exception as e:
            write_audit_log(f"[HA][GTT_RECON][DB_ERR] {e}")
            return

        if not open_rows:
            return

        # Fetch broker GTT list ONCE. None/failure → never close this cycle.
        try:
            gtts = self.executor.get_gtts()
        except Exception as e:
            write_audit_log(f"[HA][GTT_RECON][GTT_FETCH_FAIL] {e} — no close this cycle")
            return
        if gtts is None:
            return

        # Fetch positions ONCE for flat-confirmation. Failure → skip closes.
        try:
            positions = self.executor.get_open_positions()
        except Exception as e:
            write_audit_log(f"[HA][GTT_RECON][POS_FETCH_FAIL] {e} — no close this cycle")
            return

        for row in open_rows:
            try:
                self._reconcile_one_gtt(row, gtts, positions)
            except Exception as e:
                write_audit_log(
                    f"[HA][GTT_RECON][ROW_ERR] trade_id={row.get('trade_id')} ERR={e}"
                )

    def _reconcile_one_gtt(self, row, gtts, positions):
        trade_id = row.get("trade_id")
        symbol   = row.get("symbol")
        gtt_id   = row.get("sl_order_id")   # HA links the TP GTT here via update_gtt
        tp_price = row.get("tp_price")

        # No GTT recorded → not a GTT-protected trade (or GTT placement failed at
        # entry). Leave it to the app-side monitor / EOD; nothing to reconcile.
        if not gtt_id:
            return

        # Is the GTT still ACTIVE at the broker? If so, the trade is genuinely
        # open — do nothing.
        g = next((x for x in gtts if str(x.get("id")) == str(gtt_id)), None)
        if g is not None and g.get("status") == "active":
            return

        # GTT is triggered OR gone from the list → it may have fired. CONFIRM the
        # position is actually flat before closing (BB safety rule 2).
        qty_open = sum(
            abs(p.get("quantity", 0))
            for p in positions
            if p.get("tradingsymbol") == symbol and p.get("quantity", 0) != 0
        )
        if qty_open > 0:
            # GTT gone but position still open — NOT a completed TP fill. Could be
            # a mid-cancel or a broker lag. Do not close.
            write_audit_log(
                f"[HA][GTT_RECON][POS_STILL_OPEN] {symbol} gtt={gtt_id} "
                f"qty_open={qty_open} — not closing"
            )
            return

        # Confirmed: GTT fired and the position is flat → this was a TP exit the
        # app missed. Resolve a REAL exit price (never entry_price).
        exit_price = self._resolve_gtt_exit_price(symbol, gtt=g, tp_price=tp_price)

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=str(gtt_id),
                exit_reason="GTT_TP",
            )
        except Exception as e:
            write_audit_log(f"[HA][GTT_RECON][CLOSE_FAIL] trade_id={trade_id} ERR={e}")
            return

        write_audit_log(
            f"[HA][GTT_RECON][CLOSED_GTT_TP] {symbol} trade_id={trade_id} "
            f"gtt={gtt_id} exit={exit_price} — broker TP fired while app was blind"
        )

        # Clean up in-memory + gate + signal state IF the app is still running and
        # still holds this trade (Scenario A). After a restart (Scenario B) _live
        # is empty and there's nothing to clean — the DB close above is enough.
        side = "CE" if (symbol or "").endswith("CE") else "PE"
        trade = self._live.get(side)
        if trade is not None and trade.trade_id == trade_id:
            entry_price = trade.entry_price
            qty         = trade.qty
            self._live.pop(side, None)
            # ── EXIT_SELL_BACKOFF (reconcile reset) ── GTT closed it; clear any
            # app-side exit-failure/halt state so a later trade on this side is
            # not blocked by a stale halt.
            self._exit_fail_count.pop(side, None)
            self._exit_fail_at.pop(side, None)
            self._exit_halted.discard(side)
            self._tp_recon_probe_at.pop(side, None)
            try:
                self.signal_engine.notify_exit(side)
            except Exception:
                pass
            self.clear_pending()
            try:
                pnl = (exit_price - entry_price) * qty if exit_price is not None else None
                notify_tp_exit({
                    "strategy_id": self.strategy_id,
                    "mode": "live",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                })
            except Exception as e:
                write_audit_log(f"[HA][GTT_RECON][NOTIFY_FAIL] {symbol} ERR={e}")

    def _resolve_gtt_exit_price(self, symbol, gtt=None, tp_price=None):
        """
        Best real exit price for a GTT that fired, in order:
          1. broker completed SELL for this symbol (kite.orders average_price)
          2. the GTT's own orders[].result.average_price (if populated)
          3. REST LTP (fresh)
          4. tp_price (the trigger — we know it fired at/around this)
        NEVER entry_price (that silently yields P&L = 0).
        """
        # 1. Completed SELL from the order book.
        try:
            for o in self.executor.get_orders():
                if (o.get("tradingsymbol") == symbol
                        and o.get("transaction_type") == "SELL"
                        and o.get("status") == "COMPLETE"):
                    avg = o.get("average_price")
                    if avg and float(avg) > 0:
                        return float(avg)
        except Exception as e:
            write_audit_log(f"[HA][GTT_RECON][ORDERS_FAIL] {symbol} ERR={e}")

        # 2. GTT's own result payload.
        try:
            if gtt:
                for o in (gtt.get("orders") or []):
                    res = o.get("result") or {}
                    ap = res.get("average_price")
                    if ap and float(ap) > 0:
                        return float(ap)
        except Exception:
            pass

        # 3. REST LTP (fresh).
        try:
            data_kite = self.executor.broker_manager.get_data_kite()
            if data_kite:
                q = data_kite.ltp(f"NFO:{symbol}")
                rest = q.get(f"NFO:{symbol}", {}).get("last_price")
                if rest and rest > 0:
                    return float(rest)
        except Exception:
            pass

        # 4. The TP trigger — we know it fired at/above this.
        if tp_price and float(tp_price) > 0:
            return float(tp_price)
        return None
    
    # ══════════════════════════════════════════════════════════════
    # TP  —  checked on EVERY TICK (live price)
    # ══════════════════════════════════════════════════════════════

    def check_tp_on_tick(self, symbol: str, ltp: float):
        """
        Called by ha_tick_engine on EVERY incoming tick for this symbol.
        Exits the trade immediately when ltp >= tp_price.
        """
        LTPStore.update(symbol, ltp)

        side = "CE" if symbol.endswith("CE") else "PE"

        # ── LIVE_FIRST_MONITOR BEGIN ──────────────────────────────
        # An OPEN LIVE trade is ALWAYS live-managed, regardless of what mode
        # resolves to this instant. Previously the mode branch ran FIRST, and
        # a degraded config read (resolving PAPER) routed the tick down the
        # paper path — which never inspects self._live — leaving a live
        # position unmonitored for the duration of the fault (2026-07-06).
        # Bonus: while a live trade is open, the per-tick config read in
        # _mode() is skipped entirely on this hot path.
        if side in self._live:
            self._check_live_tp_tick(symbol, side, ltp)
            return

        mode = self._mode()

        if mode in ("PAPER", "OFF"):
            # No live trade open (checked above) — monitor any open paper
            # trade. (OFF: manage whatever is actually open, as before.)
            self._check_paper_tp_tick(symbol, side, ltp)
        # LIVE with no open live trade: nothing to monitor on this tick.
        # ── LIVE_FIRST_MONITOR END ────────────────────────────────

    def _check_paper_tp_tick(self, symbol: str, side: str, ltp: float):
        open_trades = get_open_paper_trades_by_side(
            strategy_name=self.strategy_id,
            side=side,
        )
        for t in open_trades:
            if t["symbol"] != symbol:
                continue
            tp = t.get("tp_price") or 0
            if tp <= 0:
                continue
            if HASignalEngine.tp_hit(ltp, tp):
                write_audit_log(
                    f"[HA][PAPER][TP_TICK] {symbol} ltp={ltp:.2f} tp={tp:.2f}"
                )
                PaperTradeRecorder.force_exit(
                    paper_trade_id=t["paper_trade_id"],
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="TP",
                )
                self.signal_engine.notify_exit(side)
                # ── GLOBAL_ARB_GATE BEGIN ── release on paper close
                self.clear_pending()
                # ── GLOBAL_ARB_GATE END ──
                break

    def _check_live_tp_tick(self, symbol: str, side: str, ltp: float):
        # ── HA_TP_GTT_ONLY BEGIN ──────────────────────────────────
        # TP is executed EXCLUSIVELY by the broker-side TP-only GTT placed at
        # entry. The app NEVER places a TP sell (previously it did: it cancelled
        # the GTT and sold itself — which, on a non-whitelisted-IP / relay-margin
        # machine, could not sell and looped 1000+ rejected resells, 2026-07-07).
        #
        # On a tick that reaches/exceeds TP we do exactly ONE thing: ask the
        # broker whether the GTT has already fired and the position is flat. If
        # so, reconcile_gtt_exits closes the DB row as GTT_TP and cleans up
        # _live. If the GTT hasn't fired yet, we do NOTHING and let the next
        # tick (or the 30s subscription-retry sweep) catch it. No app-side sell,
        # no GTT cancel, no retry loop.
        trade = self._live.get(side)
        if not trade or trade.symbol != symbol:
            return
        if side in self._tp_exit_in_progress:
            return
        if not HASignalEngine.tp_hit(ltp, trade.tp_price):
            return

        # Throttle broker reconcile probes to at most once/sec/side on the hot
        # tick path (the 30s sweep is the unconditional backstop).
        now = time.time()
        last = self._tp_recon_probe_at.get(side, 0.0)
        if now - last < 1.0:
            return
        self._tp_recon_probe_at[side] = now

        write_audit_log(
            f"[HA][LIVE][TP_TICK] {symbol} ltp={ltp:.2f} tp={trade.tp_price:.2f} "
            f"— GTT-owned; probing broker for fill (no app sell)"
        )
        self._tp_exit_in_progress.add(side)
        try:
            # Reuse the exact DB-driven reconcile the 30s sweep uses. It is
            # fully broker-guarded: a transient read failure closes nothing.
            self.reconcile_gtt_exits()
        except Exception as e:
            write_audit_log(f"[HA][LIVE][TP_RECON_TICK_ERR] {symbol} ERR={e}")
        finally:
            self._tp_exit_in_progress.discard(side)
        # ── HA_TP_GTT_ONLY END ────────────────────────────────────

    # ══════════════════════════════════════════════════════════════
    # SL  —  checked on CANDLE CLOSE only
    # ══════════════════════════════════════════════════════════════

    def check_sl_on_close(self, symbol: str, candle_close: float):
        """
        Called by ha_tick_engine after EVERY completed 1-minute candle.
        Exits when candle_close <= sl_price.
        """
        LTPStore.update(symbol, candle_close)

        side = "CE" if symbol.endswith("CE") else "PE"

        # ── LIVE_FIRST_MONITOR BEGIN ──────────────────────────────
        # Same rule as check_tp_on_tick: an open LIVE trade is always
        # live-managed, no matter what the config read resolves to.
        if side in self._live:
            self._check_live_sl_close(symbol, side, candle_close)
            return

        mode = self._mode()

        if mode in ("PAPER", "OFF"):
            self._check_paper_sl_close(symbol, side, candle_close)
        # LIVE with no open live trade: nothing to monitor on this close.
        # ── LIVE_FIRST_MONITOR END ────────────────────────────────

    def _check_paper_sl_close(self, symbol: str, side: str, candle_close: float):
        open_trades = get_open_paper_trades_by_side(
            strategy_name=self.strategy_id,
            side=side,
        )
        for t in open_trades:
            if t["symbol"] != symbol:
                continue
            sl = t.get("sl_price") or 0
            if sl <= 0:
                continue
            if HASignalEngine.sl_hit(candle_close, sl):
                write_audit_log(
                    f"[HA][PAPER][SL_CLOSE] {symbol} "
                    f"close={candle_close:.2f} sl={sl:.2f}"
                )
                PaperTradeRecorder.force_exit(
                    paper_trade_id=t["paper_trade_id"],
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    reason="SL",
                )
                self.signal_engine.notify_exit(side)
                # ── GLOBAL_ARB_GATE BEGIN ── release on paper close
                self.clear_pending()
                # ── GLOBAL_ARB_GATE END ──
                break

    def _check_live_sl_close(self, symbol: str, side: str, candle_close: float):
        trade = self._live.get(side)
        if not trade or trade.symbol != symbol:
            return
        if side in self._tp_exit_in_progress:
            return
        if HASignalEngine.sl_hit(candle_close, trade.sl_price):
            write_audit_log(
                f"[HA][LIVE][SL_CLOSE] {symbol} "
                f"close={candle_close:.2f} sl={trade.sl_price:.2f}"
            )
            self._exit_live(side, "SL", exit_price_hint=candle_close)

    # ══════════════════════════════════════════════════════════════
    # Legacy shim
    # ══════════════════════════════════════════════════════════════

    def check_sl_tp_on_close(self, symbol: str, candle_close: float):
        """DEPRECATED shim. Prefer check_sl_on_close() + check_tp_on_tick()."""
        self.check_sl_on_close(symbol, candle_close)

    # ══════════════════════════════════════════════════════════════
    # Shared live exit
    # ══════════════════════════════════════════════════════════════

    def _exit_live(
        self,
        side: str,
        reason: str,
        exit_price_hint: Optional[float] = None,
    ):
        trade = self._live.get(side)
        if not trade:
            return

        symbol   = trade.symbol
        qty      = trade.qty
        trade_id = trade.trade_id

        # ── HA_TP_GTT_ONLY GUARD BEGIN ────────────────────────────
        # TP is GTT-exclusive: the app must NEVER place a TP sell. If some future
        # caller passes reason="TP" it's a regression — route it to the GTT
        # reconcile instead of selling, and log loudly.
        if reason == "TP":
            write_audit_log(
                f"[HA][LIVE][TP_VIA_EXIT_LIVE_BLOCKED] {symbol} — TP is GTT-only; "
                f"redirecting to reconcile_gtt_exits (no app sell)"
            )
            try:
                self.reconcile_gtt_exits()
            except Exception as e:
                write_audit_log(f"[HA][LIVE][TP_RECON_REDIRECT_ERR] {symbol} ERR={e}")
            return
        # ── HA_TP_GTT_ONLY GUARD END ──────────────────────────────

        # ── EXIT_SELL_BACKOFF (pre-sell gate) BEGIN ───────────────
        # Bound the app-driven SELL path (SL / EOD / MANUAL) so a broker-rejected
        # exit can never spin at tick/candle rate.
        if side in self._exit_halted:
            # Already gave up on this side and alerted — do not resell. Only a
            # restart, a manual close, or GTT reconcile clears it.
            return

        _now = time.time()
        _fails = self._exit_fail_count.get(side, 0)
        if _fails > 0:
            # Within cooldown window → skip this attempt silently.
            if _now - self._exit_fail_at.get(side, 0.0) < _EXIT_RETRY_COOLDOWN_S:
                return
            # A retry (attempt > 0) is only allowed while trading is enabled; a
            # user hitting Trading-Disable must be able to stop a failing resell.
            # The FIRST attempt (_fails == 0) is never gated — you must always be
            # able to flatten a genuinely open live position.
            try:
                if not load_global_config().get("trade_on", False):
                    write_audit_log(
                        f"[HA][LIVE][EXIT_RETRY_SUPPRESSED_DISABLED] {symbol} "
                        f"side={side} — trade_on=FALSE; not resending exit sell"
                    )
                    return
            except Exception:
                # Config unreadable → be conservative, allow the retry (flatten
                # bias) but it remains bounded by _EXIT_MAX_ATTEMPTS below.
                pass
        # ── EXIT_SELL_BACKOFF (pre-sell gate) END ─────────────────

        # ── HA_TP_GTT BEGIN ── cancel the broker TP backstop before we sell ──
        # This exit is app-driven (SL on candle close / EOD / a TP tick the app
        # itself saw). The broker-side TP GTT must be cancelled FIRST so it can't
        # later fire a SELL on a position we're already closing (orphan-GTT).
        # cancel_gtt_verified re-checks the broker so we know it's truly gone;
        # fall back to plain cancel_gtt if the executor lacks the verified form.
        # A cancel failure NEVER blocks the flatten below — we sell regardless
        # and alert, exactly like SCALP_V3's cancel→verify→sell ordering.
        if getattr(trade, "tp_gtt_id", None):
            _gtt = trade.tp_gtt_id
            try:
                if hasattr(self.executor, "cancel_gtt_verified"):
                    gone = self.executor.cancel_gtt_verified(_gtt)
                else:
                    self.executor.cancel_gtt(_gtt)
                    gone = True
                if not gone:
                    write_audit_log(
                        f"[HA][LIVE][TP_GTT_ORPHAN] {symbol} gtt={_gtt} STILL ARMED "
                        f"after cancel — flattening anyway; DELETE THIS GTT IN KITE."
                    )
                    try:
                        from app.api.telegram_api import notify_critical
                        notify_critical({
                            "message": (
                                f"HA_V1 TP GTT {_gtt} for {symbol} could NOT be cancelled "
                                f"(still armed at broker). Closing the position now, but "
                                f"DELETE THIS GTT MANUALLY in Kite to avoid an unintended sell."
                            ),
                            "severity": "error",
                        })
                    except Exception:
                        pass
                else:
                    write_audit_log(f"[HA][LIVE][TP_GTT_CANCELLED] {symbol} gtt={_gtt}")
            except Exception as e:
                write_audit_log(
                    f"[HA][LIVE][TP_GTT_CANCEL_WARN] {symbol} gtt={_gtt} ERR={e} "
                    f"— proceeding to flatten"
                )
            trade.tp_gtt_id = None
        # ── HA_TP_GTT END ──

        # Fetch fresh LTP via REST for the limit sell price.
        ltp = None
        try:
            data_kite = self.executor.broker_manager.get_data_kite()
            if data_kite:
                q = data_kite.ltp(f"NFO:{symbol}")
                rest_ltp = q.get(f"NFO:{symbol}", {}).get("last_price")
                if rest_ltp and rest_ltp > 0:
                    ltp = rest_ltp
        except Exception as e:
            write_audit_log(f"[HA][LIVE][EXIT_LTP_REST_FAIL] {e}")

        if not ltp or ltp <= 0:
            ltp = exit_price_hint or LTPStore.get(symbol) or trade.entry_price

        # App-driven exits (SL / EOD / MANUAL) flatten with a protective limit
        # 3% through the LTP to cross the spread. (TP never reaches here — it is
        # GTT-exclusive; see HA_TP_GTT_ONLY GUARD above.)
        limit_price = round(round(ltp * 0.97 / 0.05) * 0.05, 2)

        exit_order_id = None
        try:
            kite = self.executor.broker_manager.get_trade_kite()
            order_params = dict(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=qty,
                order_type=kite.ORDER_TYPE_LIMIT,
                price=limit_price,
                product=kite.PRODUCT_NRML,
            )
            exit_order_id = self.executor._relay_call(
                relay_fn=lambda r: r.place_order(**order_params),
                direct_fn=lambda: kite.place_order(**order_params),
                op_name=f"HA_SELL_{reason}",
                symbol=symbol,
            )
            write_audit_log(
                f"[HA][LIVE][SELL_PLACED] {symbol} "
                f"reason={reason} limit={limit_price:.2f} order={exit_order_id}"
            )
        except Exception as e:
            # ── EXIT_SELL_BACKOFF (failure) BEGIN ─────────────────
            self._exit_fail_count[side] = self._exit_fail_count.get(side, 0) + 1
            self._exit_fail_at[side]    = time.time()
            attempts = self._exit_fail_count[side]
            write_audit_log(
                f"[HA][LIVE][EXIT_SELL_FAIL] {symbol} reason={reason} "
                f"attempt={attempts}/{_EXIT_MAX_ATTEMPTS} ERR={repr(e)}"
            )
            if attempts >= _EXIT_MAX_ATTEMPTS:
                # Give up on the app-side sell for this side. Do NOT clear _live
                # (the position may truly be open and still needs GTT/manual
                # exit) but STOP reselling. Alert loudly. GTT reconcile or a
                # restart clears the halt.
                self._exit_halted.add(side)
                write_audit_log(
                    f"[HA][LIVE][EXIT_SELL_HALTED] {symbol} side={side} — "
                    f"{attempts} consecutive failures; app-side exit DISABLED for "
                    f"this side. Check broker/relay/IP; GTT or manual exit required."
                )
                record_alert(
                    code="RECONCILE_NEEDED",
                    message=(
                        f"{symbol} ({side}): HA_V1 {reason} exit sell failed "
                        f"{attempts}x and is now HALTED. Last error: {e}. If the "
                        f"position is still open, exit via Kite manually — the app "
                        f"will not keep retrying."
                    ),
                    severity="error",
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    mode="live",
                )
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({
                        "message": (
                            f"HA_V1 {reason} exit for {symbol} FAILED {attempts}x "
                            f"and is HALTED (no more auto-resells). "
                            f"Last error: {e}. Check the position in Kite."
                        ),
                        "severity": "error",
                    })
                except Exception:
                    pass
            # Don't clear state — bounded retry on next tick/candle after cooldown.
            return
            # ── EXIT_SELL_BACKOFF (failure) END ───────────────────

        # Get actual fill price (wait up to 60s)
        exit_price = ltp
        deadline = time.time() + 60
        while time.time() < deadline:
            ap = self.executor.get_last_avg_price(exit_order_id)
            if ap and ap > 0:
                exit_price = ap
                break
            time.sleep(1)

        try:
            close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_order_id=exit_order_id,
                exit_reason=reason,
            )
        except Exception as e:
            write_audit_log(f"[HA][LIVE][DB_CLOSE_FAIL] {e}")

        del self._live[side]
        # ── EXIT_SELL_BACKOFF (success reset) ──
        self._exit_fail_count.pop(side, None)
        self._exit_fail_at.pop(side, None)
        self._exit_halted.discard(side)
        self._tp_recon_probe_at.pop(side, None)
        self.signal_engine.notify_exit(side)
        # ── GLOBAL_ARB_GATE BEGIN ── release pending half on close
        self.clear_pending()
        # ── GLOBAL_ARB_GATE END ──

        write_audit_log(
            f"[HA][LIVE][EXIT_OK] {symbol} side={side} "
            f"reason={reason} fill={exit_price:.2f}"
        )

        try:
            pnl = (exit_price - trade.entry_price) * qty
            payload = {
                "strategy_id": self.strategy_id,
                "mode": "live",
                "symbol": symbol,
                "entry_price": trade.entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
            }
            if reason == "SL":
                notify_sl_exit(payload)
            elif reason in ("TP", "EOD_SQUARE_OFF"):
                notify_tp_exit(payload)
            else:
                payload["exit_reason"] = reason
                notify_manual_exit(payload)
        except Exception as e:
            write_audit_log(f"[HA][TELEGRAM][EXIT_FAIL] {e}")

    # ── PAPER force-exit (used by EOD / MTM square-off) ───────────

    def _squareoff_paper(self, reason: str = "EOD_SQUARE_OFF") -> int:
        """
        Force-close every OPEN paper trade for this strategy (both sides), then
        clear signal-engine flags. Returns the count closed.

        This is what makes EOD / MTM square-off actually CLOSE a paper position
        instead of only clearing in-memory flags (which left the row OPEN and
        made the MTM guard re-fire every cycle).
        """
        closed = 0
        for side in ("CE", "PE"):
            try:
                open_trades = get_open_paper_trades_by_side(
                    strategy_name=self.strategy_id,
                    side=side,
                )
            except Exception as e:
                write_audit_log(f"[HA][PAPER][EOD_FETCH_ERR] side={side} ERR={e}")
                open_trades = []

            for t in open_trades:
                try:
                    PaperTradeRecorder.force_exit(
                        paper_trade_id=t["paper_trade_id"],
                        strategy_id=self.strategy_id,
                        symbol=t["symbol"],
                        reason=reason,
                    )
                    closed += 1
                    write_audit_log(
                        f"[HA][PAPER][EOD_CLOSE] {t['symbol']} side={side} "
                        f"id={t['paper_trade_id']} reason={reason}"
                    )
                except Exception as e:
                    write_audit_log(
                        f"[HA][PAPER][EOD_CLOSE_ERR] id={t.get('paper_trade_id')} ERR={e}"
                    )

            # Clear the signal-engine in-trade flag for the side regardless.
            try:
                self.signal_engine.notify_exit(side)
            except Exception:
                pass

        # ── GLOBAL_ARB_GATE BEGIN ── EOD/MTM closed paper positions; release.
        self.clear_pending()
        # ── GLOBAL_ARB_GATE END ──
        return closed

    # ── EOD square-off ────────────────────────────────────────────

    def eod_squareoff(self):
        """
        End-of-day / MTM square-off. Routes by what is actually open.

        PAPER (or OFF with no live trade): force-CLOSE open paper trades, then
        clear flags. (Previously this only cleared flags and left the paper
        position OPEN — so the MTM guard re-fired every ~3s forever.)

        LIVE (or OFF with a live trade): exit each live side via _exit_live.
        """
        mode = self._mode()

        if mode == "PAPER" or (mode == "OFF" and not self._live):
            closed = self._squareoff_paper(reason="EOD_SQUARE_OFF")
            write_audit_log(
                f"[HA][{mode}][EOD] Paper square-off complete "
                f"— {closed} trade(s) closed, signal flags cleared"
            )
            return

        for side in list(self._live.keys()):
            write_audit_log(f"[HA][LIVE][EOD] Closing {side}")
            try:
                self._exit_live(side, "EOD_SQUARE_OFF")
            except Exception as e:
                write_audit_log(
                    f"[HA][LIVE][EOD_FAIL] side={side} ERR={repr(e)}"
                )