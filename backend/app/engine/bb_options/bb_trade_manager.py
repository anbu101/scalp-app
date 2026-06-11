# backend/app/engine/bb_options/bb_trade_manager.py
#
# CHANGES vs previous version:
#
# *** CRITICAL FIX (live entry UnboundLocalError) ***
#   The multiple-targets refactor renamed the entry price math to percentages
#   (tp1_pct / tp2_pct) but the LIVE path's GTT placement, DB insert, and state
#   registration still referenced sl_price / tp1_price / tp2_price — three
#   variables that were NEVER assigned in the live branch (only the PAPER branch
#   computed a local sl_price, and it returns before the live code). The result
#   was `UnboundLocalError: local variable 'sl_price' referenced before
#   assignment` thrown the instant a buy fill confirmed — BEFORE the GTT was
#   placed, BEFORE the DB row was inserted, BEFORE state.register_trade(), and
#   BEFORE the Telegram entry notify. That left a live, UNPROTECTED position with
#   no recorded trade (so SuperTrend/EOD exits found no active_trade and aborted)
#   and no entry alert. This is the production incident on 2026-06-05.
#
#   FIX: compute sl_price / tp1_price / tp2_price in the LIVE path from the
#   ACTUAL fill price (avg_price) — not the pre-fill premium — right before the
#   GTT/DB/state steps. SL/TP for a long option are now anchored to what was
#   really paid. tp2_price is 0.0 in single-target mode (unused).
#
# ENTRY FILL RESOLUTION (FIX: recorded entry price != broker fill):
#   - _resolve_fill_price() REPLACED by _resolve_fill().
#   - Old code returned a float and bailed after a 5s poll; on BANKNIFTY limit
#     orders that fill in 40-60s it always timed out and recorded a stale
#     LTPStore value as the "fill". It also placed GTTs + DB rows even when the
#     broker REJECTED/CANCELLED/LAPSED the order (phantom trades).
#   - _resolve_fill() now polls the order book on STATUS (not a race against a
#     non-zero price) for up to _ENTRY_FILL_TIMEOUT_S, and returns
#     (outcome, price):
#         "FILLED"  -> COMPLETE, price is the true broker avg fill
#         "DEAD"    -> REJECTED/CANCELLED/LAPSED -> caller aborts (no GTT, no DB)
#         "UNKNOWN" -> never confirmed within timeout -> best-effort fallback
#                      price, position may be live+unprotected; reconcile fixes
#                      it on next startup.
#   - Uses executor.get_order_fill(order_id) -> {"status","avg_price","found"}
#     so the order book is read once per poll cycle.
#
# PARTIAL PROFIT BOOKING (multiple_targets mode):
#   - _enter() places ONE buy order for total qty, then splits into two DB
#     rows (slot CE_L1 / CE_L2 or PE_L1 / PE_L2) and places two GTTs.
#   - _exit() inspects which legs are still open and only sells remaining qty,
#     cancelling any live GTTs first.
#   - New live-config helpers: _live_lots(), _live_tp1_pct(), _live_tp2_pct(),
#     _live_trailing_sl(), _live_multiple_targets().
#
# SLOT NAMING:
#   single-target:   "CE" / "PE"            (unchanged - backward compat)
#   multiple-targets: "CE_L1" / "CE_L2"     (satisfies DB unique constraint)
#                    "PE_L1" / "PE_L2"

from datetime import datetime
import threading
import uuid
import time

from app.risk.strategy_max_loss_guard import evaluate_strategy_risk
from app.engine.bb_options.option_selector import OptionSelector
from app.engine.bb_options.confluence_signal_engine import TradeSignal
from app.event_bus.audit_logger import write_audit_log
from app.db.trades_repo import insert_trade, close_trade
from app.marketdata.ltp_store import LTPStore
from app.trading.paper_trade_recorder import PaperTradeRecorder
from app.config.strategy_loader import load_strategy_config
from app.db.paper_trades_repo import (
    get_open_paper_trades_by_side,
    get_paper_trade_by_id,
)
from app.event_bus.inapp_events import record_alert
from app.api.telegram_api import (
    notify_trade_entry,
    notify_manual_exit,
)

_FILL_POLL_TIMEOUT_S  = 120
_FILL_POLL_INTERVAL_S = 3

# Entry-fill polling (buy confirmation). Generous because BANKNIFTY limit
# orders can take 40-60s to fill, and the Kite order book can itself lag.
_ENTRY_FILL_TIMEOUT_S  = 120
_ENTRY_FILL_INTERVAL_S = 2

# Terminal "dead" order statuses per Kite Connect: an order in any of these
# never resulted in a position, so we must NOT place a GTT or insert a DB row.
_DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}


class BBTradeManager:

    def __init__(
        self,
        strategy_id:  str,
        trade_mode:   str,
        executor,
        symbol_fut:   str,
        lot_size:     int,
        lot_count:    int,
        sl_percent:   float,
        tp_percent:   float,
        max_premium:  float,
        scan_strikes: int,
        config:       dict,
    ):
        self.strategy_id           = strategy_id
        self._startup_trade_mode   = trade_mode
        self.executor              = executor
        self.symbol_fut            = symbol_fut
        self.config                = config

        self.lot_size              = lot_size
        self.default_lot_count     = lot_count

        self._sl_percent_fallback  = sl_percent  or 0
        self._tp_percent_fallback  = tp_percent  or 0
        self._max_premium_fallback = max_premium or 300

        self.selector = OptionSelector(
            max_premium=max_premium,
            scan_strikes=scan_strikes,
        )

        self.ce_state      = None
        self.pe_state      = None
        self.signal_engine = None
 
        # Back-reference to the tick engine (set by the engine right after
        # construction). Used to arm the live path on a mid-session flip.
        self._engine = None
 
        # Tracks the last effective mode so _live_trade_mode() can fire an
        # edge-triggered transition notice (no spam).
        self._last_effective_mode = None

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self._startup_trade_mode}] "
            f"TradeManager INIT fut_symbol={self.symbol_fut}"
        )

    # ==================================================
    # STATE MANAGERS
    # ==================================================

    def attach_state_managers(self, ce_state, pe_state, signal_engine=None):
        self.ce_state      = ce_state
        self.pe_state      = pe_state
        self.signal_engine = signal_engine

    # ==================================================
    # LIVE CONFIG HELPERS
    # ==================================================


    def _live_trade_mode(self) -> str:
        """
        Effective trade mode from live config, honoring a mid-session flip.
 
        The previous PAPER->LIVE pin is REMOVED: a flip to LIVE now takes effect
        from the next trade. Safety is enforced at the entry point — _enter()
        calls self._engine.ensure_live_armed() before placing any live order and
        refuses cleanly if the live path can't be armed (trade session not
        ready). So honoring the flip can never place an unmanaged order.
 
        An edge-triggered notice fires the instant the effective mode changes
        (this method runs on every candle/exit, so the notice is gated on a real
        change to avoid spam).
        """
        try:
            cfg  = load_strategy_config(self.strategy_id)
            mode = cfg.get("trade_execution_mode", self._startup_trade_mode)
            if mode not in ("LIVE", "PAPER"):
                mode = self._startup_trade_mode
        except Exception:
            mode = self._startup_trade_mode
 
        self._note_mode_transition(mode)
        return mode
 
    def _note_mode_transition(self, effective_mode: str) -> None:
        """Edge-triggered log + alert when the effective mode changes. Never raises."""
        prev = self._last_effective_mode
        if prev == effective_mode:
            return
        if prev is None:
            self._last_effective_mode = effective_mode
            return
        self._last_effective_mode = effective_mode
 
        try:
            write_audit_log(
                f"[BB][TRADE_MODE] Effective mode changed {prev} -> {effective_mode} "
                f"(startup={self._startup_trade_mode}) — takes effect on the next entry"
            )
        except Exception:
            pass
 
        try:
            if effective_mode == "LIVE":
                record_alert(
                    code="RECONCILE_NEEDED",
                    message=(
                        f"{self.strategy_id} switched to LIVE mid-session — the next "
                        f"entry will place a REAL broker order. Verify the trade "
                        f"session is connected. (If it isn't, the entry is refused "
                        f"safely with an alert; no order is placed.)"
                    ),
                    severity="warning",
                    strategy_id=self.strategy_id,
                    mode="live",
                )
            else:
                record_alert(
                    code="RECONCILE_NEEDED",
                    message=f"{self.strategy_id} effective mode is now {effective_mode}.",
                    severity="info",
                    strategy_id=self.strategy_id,
                    mode=effective_mode.lower(),
                )
        except Exception:
            pass

    def _live_sl_pct(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "sl_pct", self._sl_percent_fallback
                ) or 0
            )
        except Exception:
            return self._sl_percent_fallback

    def _live_tp_pct(self) -> float:
        """Single-target TP %. Also used as TP1 fallback in multiple-targets mode."""
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "tp_pct", self._tp_percent_fallback
                ) or 0
            )
        except Exception:
            return self._tp_percent_fallback

    def _live_tp1_pct(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "tp1_pct", self._live_tp_pct()
                ) or 0
            )
        except Exception:
            return self._live_tp_pct()

    def _live_tp2_pct(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "tp2_pct", self._live_tp_pct()
                ) or 0
            )
        except Exception:
            return self._live_tp_pct()

    def _live_max_premium(self) -> float:
        try:
            return float(
                load_strategy_config(self.strategy_id).get(
                    "max_premium", self._max_premium_fallback
                ) or self._max_premium_fallback
            )
        except Exception:
            return self._max_premium_fallback

    def _live_lots(self) -> int:
        """Total lots (replaces ce_lots / pe_lots)."""
        try:
            cfg = load_strategy_config(self.strategy_id)
            # Prefer new unified "lots"; fall back to ce_lots for old configs
            v = cfg.get("lots") or cfg.get("ce_lots") or self.default_lot_count
            return int(v)
        except Exception:
            return self.default_lot_count

    def _live_multiple_targets(self) -> bool:
        try:
            return bool(
                load_strategy_config(self.strategy_id).get("multiple_targets", False)
            )
        except Exception:
            return False

    def _live_trailing_sl(self) -> bool:
        try:
            return bool(
                load_strategy_config(self.strategy_id).get("trailing_sl", False)
            )
        except Exception:
            return False

    def _live_leg_lots(self, leg: int) -> int:
        """
        Returns lots for leg 1 or leg 2 in multiple-targets mode.
        Falls back to half the total lots if not explicitly set.
        """
        try:
            cfg   = load_strategy_config(self.strategy_id)
            total = self._live_lots()
            key   = "lots_leg1" if leg == 1 else "lots_leg2"
            val   = cfg.get(key)
            if val is not None:
                return int(val)
            # Even split, bias leg 1 on odd totals
            return (total + 1) // 2 if leg == 1 else total // 2
        except Exception:
            return 1

    # ==================================================
    # HANDLE SIGNAL
    # ==================================================

    def handle_signal(self, signal: TradeSignal):
        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{self._startup_trade_mode}] "
            f"[SIGNAL_RECEIVED] action={signal.action} "
            f"reason={signal.reason} rejection={signal.rejection_reason}"
        )

        if not signal or not signal.action:
            return

        effective_mode = self._live_trade_mode()

        now           = datetime.now().strftime("%H:%M")
        live_cfg      = load_strategy_config(self.strategy_id)
        session_start = live_cfg.get("session_start", "09:15")
        session_end   = live_cfg.get("session_end",   "15:15")

        if now < session_start or now >= session_end:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][{effective_mode}][BLOCKED] "
                f"Outside session window ({session_start}-{session_end})"
            )
            return

        if signal.action == "EXIT_CE":
            self._exit("CE", effective_mode=effective_mode)
            return

        if signal.action == "EXIT_PE":
            self._exit("PE", effective_mode=effective_mode)
            return

        if signal.action == "ENTER_CE":
            if effective_mode == "LIVE" and self.ce_state and self.ce_state.in_trade:
                write_audit_log("[BB][SKIP] CE already in trade")
                return
            self.signal_engine.confirm_entry("CE")
            if not self._enter("CE", effective_mode=effective_mode):
                self.signal_engine.notify_exit("CE")

        elif signal.action == "ENTER_PE":
            if effective_mode == "LIVE" and self.pe_state and self.pe_state.in_trade:
                write_audit_log("[BB][SKIP] PE already in trade")
                return
            self.signal_engine.confirm_entry("PE")
            if not self._enter("PE", effective_mode=effective_mode):
                self.signal_engine.notify_exit("PE")

    # ==================================================
    # ENTRY
    # ==================================================

    def _enter(self, side: str, effective_mode: str) -> bool:
        """
        Returns True if entry was confirmed, False on any abort.

        In multiple-targets mode:
          - Places ONE buy order for total qty
          - Inserts TWO DB rows (slot CE_L1 + CE_L2 or PE_L1 + PE_L2)
          - Places TWO GTTs (leg1: sl/tp1, leg2: sl/tp2)
          - Registers both legs in state manager
        """
        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{effective_mode}] "
            f"[ENTRY_ATTEMPT] side={side}"
        )

        risk = evaluate_strategy_risk(self.strategy_id)
        if risk:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][{effective_mode}][RISK_BLOCK] "
                    f"side={side} reason={risk}"
                )
                return False

        fut_price = LTPStore.get(self.symbol_fut)
        if not fut_price:
            write_audit_log("[BB][ENTRY_ABORT] No FUT LTP")
            return False

        live_max_premium   = self._live_max_premium()
        multiple_targets   = self._live_multiple_targets()
        total_lots         = self._live_lots()
        live_sl_pct        = self._live_sl_pct()

        # Resolve TP percentages
        if multiple_targets:
            tp1_pct    = self._live_tp1_pct()
            tp2_pct    = self._live_tp2_pct()
            leg1_lots  = self._live_leg_lots(1)
            leg2_lots  = self._live_leg_lots(2)
            # Safety guard
            if leg1_lots + leg2_lots != total_lots:
                leg1_lots = (total_lots + 1) // 2
                leg2_lots = total_lots // 2
        else:
            tp1_pct   = self._live_tp_pct()
            tp2_pct   = 0.0
            leg1_lots = total_lots
            leg2_lots = 0

        total_qty = self.lot_size * total_lots
        leg1_qty  = self.lot_size * leg1_lots
        leg2_qty  = self.lot_size * leg2_lots if multiple_targets else 0

        write_audit_log(
            f"[BB][LIVE_CONFIG] side={side} mode={effective_mode} "
            f"multiple_targets={multiple_targets} "
            f"sl_pct={live_sl_pct} tp1_pct={tp1_pct} tp2_pct={tp2_pct} "
            f"total_lots={total_lots} leg1_lots={leg1_lots} leg2_lots={leg2_lots}"
        )

        # --------------------------------------------------
        # OPTION SELECTION
        # --------------------------------------------------
        selected = self.selector.select(
            futures_price=fut_price,
            direction=side,
            max_premium_override=live_max_premium,
        )
        if not selected:
            write_audit_log("[BB][ENTRY_ABORT] No suitable option found")
            return False

        symbol, premium = selected
        if symbol == self.symbol_fut:
            write_audit_log("[BB_FATAL] OptionSelector returned FUT symbol. ABORTING.")
            return False

        write_audit_log(f"[BB][OPTION_SELECTED] symbol={symbol} premium={premium}")

        # ==========================
        # PAPER MODE
        # ==========================
        if effective_mode == "PAPER":
            sl_price = premium * (1 - live_sl_pct / 100) if live_sl_pct > 0 else 0
            tp_price = premium * (1 + tp1_pct / 100)     if tp1_pct > 0    else 0

            paper_trade_id = PaperTradeRecorder.record_entry(
                strategy_id=self.strategy_id,
                symbol=symbol,
                token=0,
                entry_price=premium,
                sl_price=sl_price,
                tp_price=tp_price,
                candle_ts=int(datetime.now().timestamp()),
            )
            if not paper_trade_id:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_BLOCKED] "
                    f"record_entry returned None for side={side}"
                )
                return False

            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][ENTRY_CONFIRMED] "
                f"{symbol} side={side} trade_id={paper_trade_id}"
            )
            return True

        # ==========================
        # LIVE MODE
        # ==========================

        # ── PRE-FLIGHT: arm the live path if this engine started in PAPER ──
        # On a mid-session PAPER->LIVE flip, ce_state/pe_state/executor may not
        # exist yet (they're built only on a LIVE start, or by ensure_live_armed).
        # Arm now or REFUSE — never place an order that can't be state-managed.
        if self.ce_state is None or self.pe_state is None or self.executor is None:
            armed = False
            if self._engine is not None:
                try:
                    armed = self._engine.ensure_live_armed()
                except Exception as e:
                    write_audit_log(f"[BB][LIVE][ARM_ERR] side={side} {repr(e)}")
                    armed = False
            if not armed:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][LIVE][ENTRY_REFUSED] side={side} "
                    f"— live path not armed (trade session not ready). No order placed."
                )
                record_alert(
                    code="DEAD_ENTRY",
                    message=(
                        f"{self.strategy_id} ({side}): live entry refused — live path "
                        f"not armed (trade session not ready). Reconnect the trade "
                        f"session and retry. No order was placed."
                    ),
                    severity="error",
                    strategy_id=self.strategy_id,
                    symbol="?",
                    mode="live",
                )
                return False
            # Re-read the now-armed live state/executor from the engine.
            self.ce_state  = self._engine.ce_state
            self.pe_state  = self._engine.pe_state
            self.executor  = self._engine.executor
 
        # Step 1: Place one buy order for total qty
        try:
            order_id, avg_price, filled_qty = self.executor.place_buy(
                symbol=symbol,
                token=0,
                qty=total_qty,
            )
        except Exception as e:
            write_audit_log(f"[BB][LIVE][ENTRY_FAILED] side={side} BUY_ERROR={repr(e)}")
            return False

        if filled_qty <= 0:
            write_audit_log("[BB][LIVE][ENTRY_ABORT] No fill")
            return False

        # Step 2: Resolve actual fill - blocks until COMPLETE / DEAD / timeout.
        # NOTE: avg_price returned by place_buy is intentionally ignored here
        # (Zerodha executor returns 0.0); the broker order book is authoritative.
        outcome, avg_price = self._resolve_fill(
            order_id=order_id,
            symbol=symbol,
            premium=premium,
        )

        if outcome == "DEAD":
            # Order rejected/cancelled/lapsed at broker - no position exists.
            # Do NOT place GTTs or insert DB rows (would create a phantom trade).
            write_audit_log(
                f"[BB][LIVE][ENTRY_ABORT] {symbol} side={side} "
                f"order_id={order_id} dead at broker - no GTT, no DB row"
            )
            record_alert(
                code="DEAD_ENTRY",
                message=f"{symbol} ({side}): buy order rejected/cancelled/lapsed — no position taken.",
                severity="error",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode="live",
            )
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({
                    "message": (
                        f"Entry order DEAD for {symbol} ({side})\n"
                        f"order_id={order_id} - rejected/cancelled/lapsed.\n"
                        f"No position taken."
                    ),
                    "severity": "warning",
                })
            except Exception:
                pass
            return False

        if outcome == "UNKNOWN":
            # Order never confirmed COMPLETE within timeout. It may still be live
            # (the order book can lag minutes). We proceed to protect a possible
            # position with GTT + DB using a best-effort price, and log loudly.
            # _reconcile() on next startup corrects price/state vs the broker.
            write_audit_log(
                f"[BB][LIVE][ENTRY_UNCONFIRMED] {symbol} side={side} "
                f"order_id={order_id} - using best-effort price {avg_price:.2f}; "
                f"placing protective GTT/DB. VERIFY position manually."
            )
            record_alert(
                code="FILL_TIMEOUT",
                message=f"{symbol} ({side}): fill not confirmed in 120s. Position may be live and is protected at a best-effort price — verify; reconcile will correct it.",
                severity="warning",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode="live",
            )

        # --------------------------------------------------
        # *** CRITICAL FIX: compute the SL / TP prices ***
        #
        # These three names (sl_price, tp1_price, tp2_price) are consumed by the
        # GTT placement, DB insert, state registration, and Telegram notify
        # below. They were NEVER assigned in the live path before this fix,
        # which raised UnboundLocalError the instant a fill confirmed — skipping
        # the GTT, the DB row, the active_trade registration, and the entry
        # alert, and leaving a live UNPROTECTED position with no recorded trade.
        #
        # Anchored to avg_price (the ACTUAL fill), not premium, so SL/TP reflect
        # what was really paid. For a LONG option:
        #   SL  below fill: avg_price * (1 - sl_pct/100)
        #   TP  above fill: avg_price * (1 + tp_pct/100)
        # A pct of 0 disables that leg's trigger (price = 0); _place_gtt_safe
        # skips the GTT when both SL and TP are 0.
        # --------------------------------------------------
        sl_price  = avg_price * (1 - live_sl_pct / 100) if live_sl_pct > 0 else 0.0
        tp1_price = avg_price * (1 + tp1_pct / 100)     if tp1_pct     > 0 else 0.0
        tp2_price = (
            avg_price * (1 + tp2_pct / 100)
            if (multiple_targets and tp2_pct > 0)
            else 0.0
        )

        write_audit_log(
            f"[BB][LIVE][PRICES] {symbol} side={side} fill={avg_price:.2f} "
            f"sl={sl_price:.2f} tp1={tp1_price:.2f} tp2={tp2_price:.2f} "
            f"(sl_pct={live_sl_pct} tp1_pct={tp1_pct} tp2_pct={tp2_pct})"
        )

        # Step 4: Slot names
        if multiple_targets:
            slot1 = f"{side}_L1"
            slot2 = f"{side}_L2"
        else:
            slot1 = side
            slot2 = None

        # Step 5: Place GTT(s) and insert DB rows
        trade_id1 = str(uuid.uuid4())
        trade_id2 = str(uuid.uuid4()) if multiple_targets else None

        gtt_id1 = self._place_gtt_safe(
            symbol=symbol,
            qty=leg1_qty,
            sl_price=sl_price,
            tp_price=tp1_price,
            last_price=avg_price,
            label="GTT_LEG1",
            side=side,
        )

        gtt_id2 = None
        if multiple_targets:
            gtt_id2 = self._place_gtt_safe(
                symbol=symbol,
                qty=leg2_qty,
                sl_price=sl_price,
                tp_price=tp2_price,
                last_price=avg_price,
                label="GTT_LEG2",
                side=side,
            )

        # Step 6: Insert DB rows
        _db_insert_safe(
            trade_id=trade_id1, strategy_id=self.strategy_id,
            slot=slot1, symbol=symbol, token=0,
            entry_price=avg_price, qty=leg1_qty,
            buy_order_id=order_id,
            sl_price=sl_price, tp_price=tp1_price, tp_mode="GTT",
        )

        if multiple_targets:
            _db_insert_safe(
                trade_id=trade_id2, strategy_id=self.strategy_id,
                slot=slot2, symbol=symbol, token=0,
                entry_price=avg_price, qty=leg2_qty,
                buy_order_id=order_id,
                sl_price=sl_price, tp_price=tp2_price, tp_mode="GTT",
            )

        # Step 7: Register in state manager
        state = self.ce_state if side == "CE" else self.pe_state
        if state:
            state.register_trade(
                trade_id=trade_id1,
                symbol=symbol,
                qty=leg1_qty,
                sl_price=sl_price,
                tp_price=tp1_price,
                gtt_id=gtt_id1,
                entry_price=avg_price,
                leg_number=1,
            )
            if multiple_targets:
                state.register_trade_leg2(
                    trade_id=trade_id2,
                    symbol=symbol,
                    qty=leg2_qty,
                    sl_price=sl_price,
                    tp_price=tp2_price,
                    gtt_id=gtt_id2,
                    entry_price=avg_price,
                )

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][LIVE][ENTRY_CONFIRMED] "
            f"{symbol} side={side} entry={avg_price:.2f} "
            f"sl={sl_price:.2f} tp1={tp1_price:.2f} tp2={tp2_price:.2f} "
            f"gtt1={'placed' if gtt_id1 else 'NONE'} "
            f"gtt2={'placed' if gtt_id2 else 'NONE'}"
        )

        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode":        "live",
                "symbol":      symbol,
                "side":        side,
                "entry_price": avg_price,
                "quantity":    total_qty,
                "sl":          sl_price,
                "tp":          tp1_price,
                "tp2":         tp2_price if multiple_targets else None,
                "multiple_targets": multiple_targets,
            })
        except Exception as e:
            write_audit_log(f"[TELEGRAM][ENTRY_NOTIFY_ERROR] {e}")

        return True

    # ==================================================
    # EXIT
    # Handles:
    #   A) Single-target: cancel 1 GTT, sell total qty
    #   B) Multiple-targets, both legs open: cancel 2 GTTs, sell total qty
    #   C) Multiple-targets, leg1 already closed (TP1 hit):
    #      cancel GTT2 only, sell leg2 qty
    # ==================================================

    def _exit(self, side: str, effective_mode: str, exit_reason: str = "SuperTrend"):
        write_audit_log(
            f"[STRATEGY={self.strategy_id}][{effective_mode}] "
            f"[EXIT_ATTEMPT] side={side}"
        )

        # ==========================
        # PAPER MODE
        # ==========================
        # ── REVERSE-FLIP GUARD: exits follow the POSITION's mode, not config ──
        # If this side has an OPEN LIVE position in state, exit it LIVE even if
        # the config was flipped to PAPER mid-position. Otherwise a SuperTrend
        # exit after a LIVE->PAPER flip would take the paper branch, find no
        # paper row, and leave the real position riding on GTT/EOD only.
        _live_state = self.ce_state if side == "CE" else self.pe_state
        if effective_mode == "PAPER" and _live_state is not None and _live_state.in_trade:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][EXIT] side={side} config=PAPER but "
                f"an OPEN LIVE position exists — exiting LIVE (exits follow the "
                f"position's mode)."
            )
            effective_mode = "LIVE"
 
        # ==========================
        # PAPER MODE
        # ==========================
        if effective_mode == "PAPER":
            open_trades = get_open_paper_trades_by_side(
                strategy_name=self.strategy_id,
                side=side,
            )
            if not open_trades:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EXIT_ABORT] "
                    f"No open {side} trade found in DB"
                )
                if self.signal_engine:
                    self.signal_engine.notify_exit(side) 
                return

            for trade_row in open_trades:
                paper_trade_id = trade_row["paper_trade_id"]
                symbol         = trade_row["symbol"]
                entry_price    = trade_row["entry_price"]
                exit_price     = LTPStore.get(symbol)

                try:
                    PaperTradeRecorder.force_exit(
                        paper_trade_id=paper_trade_id,
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        reason="SuperTrend",
                    )
                except Exception as e:
                    write_audit_log(f"[BB][PAPER][EXIT_FAILED] trade_id={paper_trade_id} ERR={e}")
                    continue

                try:
                    safe_exit = exit_price if exit_price is not None else entry_price
                    pnl = (
                        (safe_exit - entry_price) * trade_row["qty"]
                        if safe_exit is not None and entry_price is not None
                        else None
                    )
                    notify_manual_exit({
                        "strategy_id": self.strategy_id,
                        "mode":        "paper",
                        "symbol":      symbol,
                        "entry_price": entry_price,
                        "exit_price":  safe_exit,
                        "exit_reason": "SuperTrend",
                        "pnl":         pnl,
                    })
                except Exception as e:
                    write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")
            
            if self.signal_engine:
                self.signal_engine.notify_exit(side) 
            return

        # ==========================
        # LIVE MODE
        # ==========================
        state = self.ce_state if side == "CE" else self.pe_state

        if not state or not state.in_trade:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][LIVE][EXIT_ABORT] "
                f"No active trade for side={side}"
            )
            if self.signal_engine:
                self.signal_engine.notify_exit(side)
            return

        leg1  = state.active_trade
        leg2  = state.active_trade_leg2

        # Determine what's still open
        symbol      = (leg1 or leg2).symbol
        legs_to_close = [t for t in [leg1, leg2] if t is not None]
        total_sell_qty = sum(t.qty for t in legs_to_close)

        if total_sell_qty <= 0:
            write_audit_log(f"[BB][LIVE][EXIT_ABORT] qty=0 for side={side}")
            if self.signal_engine:
                self.signal_engine.notify_exit(side)
            return

        # Step 1: Cancel all live GTTs for this side (verified where available —
        # an orphan GTT on a flat long would fire an unintended SELL → short).
        orphaned_gtts = []
        for leg in legs_to_close:
            if leg.gtt_id:
                gone = True
                try:
                    if hasattr(self.executor, "cancel_gtt_verified"):
                        gone = self.executor.cancel_gtt_verified(leg.gtt_id)
                    else:
                        self.executor.cancel_gtt(leg.gtt_id)
                    write_audit_log(
                        f"[BB][LIVE][GTT_CANCEL] leg{leg.leg_number} gtt_id={leg.gtt_id}"
                    )
                except Exception as e:
                    write_audit_log(
                        f"[BB][LIVE][GTT_CANCEL_WARN] gtt_id={leg.gtt_id} "
                        f"ERR={e} - continuing with market sell"
                    )
                if not gone:
                    orphaned_gtts.append(leg.gtt_id)
                    write_audit_log(
                        f"[BB][LIVE][GTT_ORPHAN] leg{leg.leg_number} gtt_id={leg.gtt_id} "
                        f"STILL ARMED - continuing with market sell, alerting"
                    )
        if orphaned_gtts:
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({
                    "message": (
                        f"{self.strategy_id} ({side}): GTT(s) {orphaned_gtts} could NOT be "
                        f"cancelled (still armed) for {symbol}. Market-selling to flatten now, "
                        f"but DELETE THESE GTTs MANUALLY in Kite."
                    ),
                    "severity": "error",
                })
            except Exception:
                pass

        # Step 2: Capture REST LTP before sell (price reference)
        rest_ltp_at_exit = _fetch_rest_ltp(self.executor, symbol)

        # Step 3: Place market sell for remaining qty
        try:
            exit_order_id = self.executor.place_market_sell(
                symbol=symbol,
                qty=total_sell_qty,
            )
        except Exception as e:
            write_audit_log(f"[BB][LIVE][EXIT_FAILED] side={side} ERR={repr(e)}")
            return

        # Step 4: Clear in-memory state IMMEDIATELY
        trade_ids = [t.trade_id for t in legs_to_close]
        state.clear_trade()
        if self.signal_engine:
            self.signal_engine.notify_exit(side)

        write_audit_log(
            f"[BB][LIVE][STATE_CLEARED] {symbol} side={side} "
            f"qty={total_sell_qty} legs={len(legs_to_close)} "
            f"- fill confirmation running in background"
        )

        # Step 5: Confirm fill and close DB in background
        _executor        = self.executor
        _strategy_id     = self.strategy_id
        _exit_reason     = exit_reason

        def _confirm_fill_and_close():
            exit_price = _poll_for_fill(
                executor=_executor,
                order_id=exit_order_id,
                symbol=symbol,
                rest_ltp_at_exit=rest_ltp_at_exit,
            )

            for trade_id in trade_ids:
                entry_price = None
                try:
                    from app.db.trades_repo import get_trade_by_id
                    db_trade = get_trade_by_id(trade_id)
                    if db_trade:
                        entry_price = db_trade.get("entry_price")
                except Exception:
                    pass

                try:
                    close_trade(
                        trade_id=trade_id,
                        exit_price=exit_price,
                        exit_order_id=exit_order_id,
                        exit_reason=_exit_reason,
                    )
                except Exception as e:
                    write_audit_log(f"[BB][LIVE][DB_CLOSE_FAIL] trade_id={trade_id} ERR={e}")

                write_audit_log(
                    f"[STRATEGY={_strategy_id}][LIVE][EXIT_CONFIRMED] "
                    f"{symbol} side={side} exit={exit_price} trade_id={trade_id}"
                )

                try:
                    pnl = (
                        (exit_price - entry_price) * total_sell_qty
                        if exit_price is not None and entry_price is not None
                        else None
                    )
                    notify_manual_exit({
                        "strategy_id": _strategy_id,
                        "mode":        "live",
                        "symbol":      symbol,
                        "entry_price": entry_price,
                        "exit_price":  exit_price,
                        "exit_reason": _exit_reason,
                        "pnl":         pnl,
                    })
                except Exception as e:
                    write_audit_log(f"[TELEGRAM][EXIT_NOTIFY_ERROR] {e}")

        threading.Thread(
            target=_confirm_fill_and_close,
            daemon=True,
            name=f"fill-confirm-{side}-{trade_ids[0][:8]}",
        ).start()

    # ==================================================
    # EOD SQUARE-OFF
    # ==================================================

    def eod_squareoff(self):
        effective_mode = self._live_trade_mode()

        if effective_mode == "PAPER":
            if self.signal_engine:
                for side in ("CE", "PE"):
                    self.signal_engine.notify_exit(side)
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EOD] "
                    f"Signal engine flags cleared"
                )
            return

        write_audit_log(
            f"[STRATEGY={self.strategy_id}][LIVE][EOD] Square-off triggered"
        )

        for side in ("CE", "PE"):
            state = self.ce_state if side == "CE" else self.pe_state
            if state and state.in_trade:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][LIVE][EOD] "
                    f"Closing open {side} trade(s)"
                )
                try:
                    self._exit(side, effective_mode="LIVE", exit_reason="EOD_SQUARE_OFF")
                except Exception as e:
                    write_audit_log(
                        f"[STRATEGY={self.strategy_id}][LIVE][EOD][FAIL] "
                        f"side={side} ERR={repr(e)}"
                    )
            else:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][LIVE][EOD] "
                    f"No open {side} trade - skipping"
                )

    # ==================================================
    # INTERNAL HELPERS
    # ==================================================

    def _resolve_fill(
        self,
        order_id: str,
        symbol:   str,
        premium:  float,
    ):
        """
        Poll the broker order book until the buy reaches a terminal state.

        Returns (outcome, price):
          ("FILLED",  avg_price)  -> order COMPLETE, avg_price is the true fill
          ("DEAD",    0.0)        -> order REJECTED/CANCELLED/LAPSED;
                                     caller MUST abort (no GTT, no DB row)
          ("UNKNOWN", price)      -> never confirmed within timeout; price is a
                                     best-effort fallback (REST LTP -> premium).
                                     Position may be live and unprotected.

        This intentionally BLOCKS up to _ENTRY_FILL_TIMEOUT_S. BB makes
        infrequent entries on BANKNIFTY monthly options where limit fills can
        take 40-60s, so blocking the side's signal processing for one fill is
        acceptable and far safer than recording a stale LTPStore estimate.
        """
        start    = time.time()
        last_avg = 0.0

        write_audit_log(
            f"[BB][LIVE][ENTRY_FILL_POLL_START] {symbol} order_id={order_id} "
            f"timeout={_ENTRY_FILL_TIMEOUT_S}s"
        )

        while time.time() - start < _ENTRY_FILL_TIMEOUT_S:
            try:
                info = self.executor.get_order_fill(order_id)
            except Exception as e:
                # Treat a transient read error as "keep polling"
                write_audit_log(f"[BB][LIVE][ENTRY_FILL_POLL_ERR] {symbol} ERR={e}")
                time.sleep(_ENTRY_FILL_INTERVAL_S)
                continue

            status = (info.get("status") or "").upper()
            avg    = info.get("avg_price") or 0.0

            if status == "COMPLETE":
                # Kite can flip to COMPLETE a beat before average_price populates.
                if avg > 0:
                    write_audit_log(
                        f"[BB][LIVE][ENTRY_FILL_CONFIRMED] {symbol} "
                        f"fill={avg:.2f} elapsed={time.time() - start:.1f}s"
                    )
                    return "FILLED", float(avg)
                # COMPLETE but price not yet there - wait one more cycle.
                last_avg = avg

            elif status in _DEAD_ORDER_STATUSES:
                write_audit_log(
                    f"[BB][LIVE][ENTRY_ORDER_DEAD] {symbol} order_id={order_id} "
                    f"status={status} - aborting entry"
                )
                return "DEAD", 0.0

            # OPEN / TRIGGER PENDING / not-yet-in-book (found=False) -> keep polling
            time.sleep(_ENTRY_FILL_INTERVAL_S)

        # ── Timed out without a COMPLETE / DEAD verdict ──
        write_audit_log(
            f"[BB][LIVE][ENTRY_FILL_TIMEOUT] {symbol} order_id={order_id} "
            f"- order never confirmed in {_ENTRY_FILL_TIMEOUT_S}s. "
            f"Falling back to best-effort price (position may be UNPROTECTED)."
        )

        fallback = last_avg if last_avg > 0 else 0.0
        if fallback <= 0:
            rest = _fetch_rest_ltp(self.executor, symbol)
            if rest > 0:
                fallback = rest
                write_audit_log(
                    f"[BB][LIVE][ENTRY_PRICE_REST_FALLBACK] {symbol} ltp={fallback:.2f}"
                )
        if fallback <= 0:
            fallback = premium
            write_audit_log(
                f"[BB][LIVE][ENTRY_PRICE_PREMIUM_FALLBACK] {symbol} premium={premium}"
            )

        return "UNKNOWN", float(fallback)

    def _place_gtt_safe(
        self,
        symbol:     str,
        qty:        int,
        sl_price:   float,
        tp_price:   float,
        last_price: float,
        label:      str,
        side:       str,
    ) -> str:
        """Place a GTT and return gtt_id. Logs but does not abort on failure."""
        if sl_price <= 0 and tp_price <= 0:
            write_audit_log(f"[BB][{label}] Both SL and TP are 0 - skipping GTT")
            return None

        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=qty,
                sl_price=sl_price,
                tp_price=tp_price,
                last_price=last_price,
            )
            write_audit_log(
                f"[BB][{label}] GTT placed gtt_id={gtt_id} "
                f"symbol={symbol} qty={qty} "
                f"sl={sl_price:.2f} tp={tp_price:.2f}"
            )
            return gtt_id
        except Exception as gtt_err:
            write_audit_log(
                f"[BB][LIVE][CRITICAL] {label} GTT FAILED - position UNPROTECTED. "
                f"side={side} symbol={symbol} ERR={repr(gtt_err)}"
            )
            record_alert(
                code="GTT_FAIL",
                message=f"{symbol} ({side}): {label} GTT FAILED — position UNPROTECTED, SuperTrend/EOD exit only. Investigate now.",
                severity="error",
                strategy_id=self.strategy_id,
                symbol=symbol,
                mode="live",
            )
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({
                    "message": (
                        f"{label} GTT FAILED for {symbol} ({side})\n"
                        f"SL: Rs{sl_price:.2f} | TP: Rs{tp_price:.2f}\n"
                        f"ERR: {gtt_err}\n"
                        f"Position UNPROTECTED - SuperTrend/EOD exit only."
                    ),
                    "severity": "error",
                })
            except Exception:
                pass
            return None


# ==================================================
# MODULE-LEVEL HELPERS
# ==================================================

def _fetch_rest_ltp(executor, symbol: str) -> float:
    """Fetch fresh REST LTP as a price reference at exit time."""
    try:
        data_kite = executor.broker_manager.get_data_kite()
        if data_kite:
            quote = data_kite.ltp(f"NFO:{symbol}")
            rlt   = quote.get(f"NFO:{symbol}", {}).get("last_price")
            if rlt and rlt > 0:
                write_audit_log(
                    f"[BB][LIVE][EXIT_REST_LTP_CAPTURE] {symbol} ltp={rlt}"
                )
                return float(rlt)
    except Exception as e:
        write_audit_log(f"[BB][LIVE][EXIT_REST_LTP_CAPTURE_FAIL] {e}")
    return 0.0


def _poll_for_fill(executor, order_id: str, symbol: str, rest_ltp_at_exit: float) -> float:
    """
    Poll kite.orders() for actual fill price.
    Fallback chain: fresh REST LTP -> captured REST LTP -> LTPStore.
    """
    exit_price = None
    poll_start = time.time()

    write_audit_log(
        f"[BB][LIVE][FILL_POLL_START] {symbol} order_id={order_id} "
        f"timeout={_FILL_POLL_TIMEOUT_S}s"
    )

    while time.time() - poll_start < _FILL_POLL_TIMEOUT_S:
        try:
            avg = executor.get_last_avg_price(order_id)
            if avg and avg > 0:
                exit_price = float(avg)
                write_audit_log(
                    f"[BB][LIVE][FILL_CONFIRMED] {symbol} fill={exit_price:.2f} "
                    f"elapsed={time.time() - poll_start:.1f}s"
                )
                break
        except Exception as poll_err:
            write_audit_log(f"[BB][LIVE][FILL_POLL_ERR] {symbol} ERR={poll_err}")
        time.sleep(_FILL_POLL_INTERVAL_S)

    if not exit_price:
        write_audit_log(
            f"[BB][LIVE][FILL_POLL_TIMEOUT] {symbol} order_id={order_id} "
            f"- using fallback prices"
        )
        try:
            data_kite = executor.broker_manager.get_data_kite()
            if data_kite:
                q   = data_kite.ltp(f"NFO:{symbol}")
                rlt = q.get(f"NFO:{symbol}", {}).get("last_price")
                if rlt and rlt > 0:
                    exit_price = float(rlt)
                    write_audit_log(
                        f"[BB][LIVE][EXIT_PRICE_REST_FRESH] {symbol} ltp={exit_price:.2f}"
                    )
        except Exception as e:
            write_audit_log(f"[BB][LIVE][EXIT_PRICE_REST_FRESH_FAIL] {symbol} ERR={e}")

        if not exit_price and rest_ltp_at_exit:
            exit_price = rest_ltp_at_exit
            write_audit_log(
                f"[BB][LIVE][EXIT_PRICE_REST_CAPTURE] {symbol} ltp={exit_price:.2f}"
            )

        if not exit_price:
            ltp_store_val = LTPStore.get(symbol)
            if ltp_store_val and ltp_store_val > 0:
                exit_price = float(ltp_store_val)
                write_audit_log(
                    f"[BB][LIVE][EXIT_PRICE_LTPSTORE_LASTRESORT] {symbol} "
                    f"ltp={exit_price:.2f} (may be stale)"
                )

    return exit_price


def _db_insert_safe(trade_id, strategy_id, slot, symbol, token,
                    entry_price, qty, buy_order_id, sl_price, tp_price, tp_mode):
    """Insert a trade row, logging but not raising on failure."""
    try:
        insert_trade(
            trade_id=trade_id,
            strategy_id=strategy_id,
            slot=slot,
            symbol=symbol,
            token=token,
            entry_price=entry_price,
            qty=qty,
            buy_order_id=buy_order_id,
            sl_price=sl_price,
            tp_price=tp_price,
            tp_mode=tp_mode,
        )
    except Exception as db_err:
        write_audit_log(
            f"[BB][LIVE][CRITICAL] DB INSERT FAILED "
            f"slot={slot} symbol={symbol} order_id={buy_order_id} ERR={repr(db_err)}"
        )