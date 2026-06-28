# backend/app/engine/scalpv5/scalpv5_manager.py
#
# SCALP_V5 — TEST option-BUYING strategy on 3-minute candles.
#
# ONE LOGICAL TRADE = ONE INSTRUMENT (LONG):
#   V5 buys the signalling contract itself. No hedge, no signal/traded split.
#   The bought option carries the P&L; P&L is LONG: (exit - entry) * qty.
#
# This MANAGER owns order placement + DB lifecycle for V5. The ENGINE
# (scalpv5_tick_engine.py) owns signal generation and the tick-driven exit
# decision (SL / TP / TIME / MTM); it calls open_trade() / close_trade() here.
#
# GLOBAL SINGLE-TRADE GATE (DB-backed):
#   At most ONE OPEN V5 trade at a time. The gate is checked against the DB
#   (get_open_v5_trade) so it survives restarts and cannot be held by stale
#   in-memory state.
#
# LIVE ENTRY is two-phase (mirrors SCALP_V1 / V3):
#   1. place_buy(symbol) → provisional row (entry = protected limit).
#   2. background poll get_order_fill → on COMPLETE: confirm the true fill,
#      then place the protective GTT per the SL/TP matrix, link it.
#      on DEAD / timeout: close row, release gate.
#
# GTT MATRIX (LONG) — fork #1 resolved to "no executor change":
#   SL>0 & TP>0 → place_gtt_oco(direction="LONG")          (OCO, both legs)
#   SL>0 & TP=0 → place_gtt_oco(direction="LONG", tp=None) (SL-only single)
#   SL=0 & TP>0 → NO GTT — engine tick-checks TP; time exit backstops
#   SL=0 & TP=0 → NO GTT — pure time-boxed; time exit only
#   In ALL cases the engine ALSO runs tick-driven SL/TP/time checks, so paper
#   mode and the no-GTT live cases are uniformly covered.
#
# EXIT (cancel → verify → sell), reasons: EMA_EXIT|SL|TP|MAX_LOSS|MAX_PROFIT|EOD|MANUAL.
#   Cancel the GTT first (if any), verify the position is still open (the GTT
#   may have already filled), then market-sell. If already flat, the GTT won
#   the race → close the DB row only. EMA_EXIT (candle closes below EMA20_HIGH)
#   is candle-driven from the tick engine; there is NO time-based exit.
#
# SELF-CONTAINED MTM (max_loss / max_profit):
#   V5 writes scalpv5_trades, which the shared risk guards do NOT read, so MTM
#   is computed HERE (realised-today from the repo + unrealised from the open
#   row vs LTP, LONG sign). On breach: force-exit + a V5-local session latch
#   that blocks re-entry until reset_v5_risk_latch() (called by the EOD job).
#   Mirrors risk_mtm_guard's math + fail-open-on-unresolvable-LTP philosophy.
#
# ISOLATION: touches only scalpv5_repo + the execution router + config +
# selection. No edits to TradeStateManager / trades / paper_trades / the shared
# risk guards.

import time
import uuid
import threading
from typing import Optional
from datetime import datetime

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.utils.session_utils import is_within_session
from app.marketdata.ltp_store import LTPStore

# Shared trade notifications (in-app audio/toast + Telegram). V5 calls these
# explicitly because it does not route through TradeStateManager/PaperTradeRecorder.
from app.api.telegram_api import (
    notify_trade_entry,
    notify_tp_exit,
    notify_sl_exit,
    notify_manual_exit,
)

from app.db.scalpv5_repo import (
    insert_v5_trade,
    confirm_v5_fill,
    link_v5_gtt,
    close_v5_trade,
    get_open_v5_trade,
    get_v5_trade_by_id,
    get_total_pnl_v5_today,
)

STRATEGY_ID = "SCALP_V5"

# Live entry fill-confirm tuning (mirrors SCALP_V1 / V3 _ENTRY_FILL_* semantics).
_ENTRY_FILL_CANCEL_S        = 50
_ENTRY_FILL_POLL_INTERVAL_S = 2
_DEAD_ORDER_STATUSES        = {"REJECTED", "CANCELLED", "LAPSED"}


# ============================================================================
# V5-LOCAL MTM DAY-BLOCK LATCH (process-local; reset by the EOD job)
# Self-contained — does NOT use the shared risk_mtm_guard latches, keeping V5
# fully isolated. A simple in-process flag is sufficient because V5 runs as a
# single standalone engine per process (like V3).
# ============================================================================

_V5_DAY_BLOCKED = False
_V5_BLOCK_LOCK  = threading.Lock()


def is_v5_day_blocked() -> bool:
    with _V5_BLOCK_LOCK:
        return _V5_DAY_BLOCKED


def _set_v5_day_blocked():
    global _V5_DAY_BLOCKED
    with _V5_BLOCK_LOCK:
        _V5_DAY_BLOCKED = True


def reset_v5_risk_latch() -> None:
    """Clear the V5 MTM re-entry block (call at EOD / start-of-day). Never raises."""
    global _V5_DAY_BLOCKED
    try:
        with _V5_BLOCK_LOCK:
            _V5_DAY_BLOCKED = False
        write_audit_log("[V5][RISK] Daily MTM re-entry latch reset")
    except Exception:
        pass


class ScalpV5Manager:
    """
    One instance per process for SCALP_V5 (single-trade-global). Constructed by
    the engine, which passes the shared execution router (same factory the rest
    of the app uses, so relay / SEBI-IP routing is identical).
    """

    def __init__(self, executor):
        self.executor = executor
        self._entry_lock = threading.Lock()
        self._reserved   = False   # in-flight entry guard (pre-DB-row)

    # ==================================================
    # CONFIG HELPERS
    # ==================================================

    def _cfg(self) -> dict:
        return load_strategy_config(STRATEGY_ID)

    def _mode(self) -> str:
        return self._cfg().get("trade_execution_mode", "PAPER").upper()

    def _limits(self):
        """(max_loss, max_profit) as positive magnitudes; 0 = disabled."""
        cfg = self._cfg()
        try:
            ml = abs(float(cfg.get("max_loss", 0) or 0))
            mp = abs(float(cfg.get("max_profit", 0) or 0))
            return ml, mp
        except Exception:
            return 0.0, 0.0

    def _within_session(self) -> bool:
        cfg = self._cfg()
        primary = (cfg.get("session") or {}).get("primary") or {}
        return is_within_session(
            datetime.now(), primary.get("start"), primary.get("end")
        )

    # ==================================================
    # NOTIFICATION HELPERS (best-effort; never break trading)
    # P&L is LONG: (exit - entry) * qty.
    # ==================================================

    def _notify_entry(self, *, paper, symbol, side, entry, sl, tp, qty):
        try:
            notify_trade_entry({
                "strategy_id": STRATEGY_ID,
                "mode":        "paper" if paper else "live",
                "symbol":      symbol,
                "side":        side,
                "entry_price": entry,
                "quantity":    qty,
                "sl":          sl,
                "tp":          tp,
                "trade_direction": "LONG",
            })
        except Exception as e:
            write_audit_log(f"[V5][NOTIFY][ENTRY_ERROR] {symbol} ERR={e}")

    def _notify_exit(self, *, paper, symbol, entry, qty, exit_price, exit_reason):
        try:
            pnl = None
            if exit_price is not None and entry is not None and qty:
                pnl = (float(exit_price) - float(entry)) * int(qty)

            payload = {
                "strategy_id": STRATEGY_ID,
                "mode":        "paper" if paper else "live",
                "symbol":      symbol,
                "entry_price": entry,
                "exit_price":  exit_price,
                "pnl":         pnl,
                "trade_direction": "LONG",
            }

            # TP → target tone; SL → stop tone; everything else → generic close.
            if exit_reason == "TP":
                notify_tp_exit(payload)
            elif exit_reason == "SL":
                notify_sl_exit(payload)
            else:
                payload["exit_reason"] = exit_reason
                notify_manual_exit(payload)
        except Exception as e:
            write_audit_log(f"[V5][NOTIFY][EXIT_ERROR] {symbol} ERR={e}")

    # ==================================================
    # ENTRY — called by the engine when a BUY signal fires
    # ==================================================

    def open_trade(
        self,
        *,
        symbol: str,
        token: int,
        side: str,                 # "CE" | "PE"
        entry_price: float,        # signal close (provisional pre-fill)
        sl_price: Optional[float], # entry - sl_points (None if disabled)
        tp_price: Optional[float], # entry + tp_points (None if disabled)
        entry_candle_ts: int,
    ):
        # ── pre-DB gates ──────────────────────────────
        if not load_global_config().get("trade_on", False):
            self._skip("GLOBAL_TRADE_OFF", symbol)
            return
        if not self._within_session():
            self._skip("OUTSIDE_SESSION", symbol)
            return
        if is_v5_day_blocked():
            self._skip("MTM_DAY_BLOCKED", symbol)
            return

        with self._entry_lock:
            if self._reserved:
                self._skip("ENTRY_IN_PROGRESS", symbol)
                return
            existing = get_open_v5_trade()
            if existing is not None:
                write_audit_log(
                    f"[V5][SINGLE_TRADE_GATE] DROP {symbol} — already OPEN "
                    f"id={existing.get('v5_trade_id')} ({existing.get('symbol')})"
                )
                return
            self._reserved = True

        try:
            cfg          = self._cfg()
            lots         = int(cfg.get("quantity", {}).get("lots", 1))
            lot_size     = int(cfg.get("quantity", {}).get("lot_size", 65))
            qty          = lots * lot_size
            paper        = (self._mode() == "PAPER")
            v5_id        = str(uuid.uuid4())

            if paper:
                self._open_paper(
                    v5_id=v5_id, qty=qty, symbol=symbol, token=token, side=side,
                    entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
                    entry_candle_ts=entry_candle_ts,
                )
            else:
                self._open_live(
                    v5_id=v5_id, qty=qty, symbol=symbol, token=token, side=side,
                    entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
                    entry_candle_ts=entry_candle_ts,
                )
        finally:
            # PAPER: fully done. LIVE: the DB row now holds the gate, not _reserved.
            with self._entry_lock:
                self._reserved = False

    # --------------------------------------------------
    # PAPER entry
    # --------------------------------------------------

    def _open_paper(self, *, v5_id, qty, symbol, token, side,
                    entry_price, sl_price, tp_price, entry_candle_ts):
        # Use a fresh LTP as the provisional paper entry if available, else the
        # signal close. (Paper records the entry-equivalent; no broker fill.)
        ltp = self.executor.resolve_ltp(symbol)
        entry = round(float(ltp), 2) if (ltp and ltp > 0) else round(float(entry_price), 2)

        insert_v5_trade(
            v5_trade_id=v5_id, paper=True,
            symbol=symbol, token=token, side=side, qty=qty,
            entry_price=entry, sl_price=sl_price, tp_price=tp_price,
            entry_candle_ts=entry_candle_ts,
            order_id=None,
        )
        write_audit_log(
            f"[V5][PAPER][ENTRY] id={v5_id} {symbol} entry={entry} "
            f"sl={sl_price} tp={tp_price} qty={qty}"
        )
        self._notify_entry(
            paper=True, symbol=symbol, side=side, entry=entry,
            sl=sl_price, tp=tp_price, qty=qty,
        )

    # --------------------------------------------------
    # LIVE entry (two-phase)
    # --------------------------------------------------

    def _open_live(self, *, v5_id, qty, symbol, token, side,
                   entry_price, sl_price, tp_price, entry_candle_ts):
        broker_symbol = self.executor.resolve_symbol(symbol)

        # Phase 1: BUY (protected-limit inside executor).
        try:
            order_id, _, filled_qty = self.executor.place_buy(
                broker_symbol, token, qty
            )
        except Exception as e:
            write_audit_log(f"[V5][LIVE][BUY_FAIL] {symbol} ERR={e}")
            return

        if filled_qty <= 0:
            write_audit_log(f"[V5][LIVE][BUY_ZERO_QTY] {symbol} — aborting")
            return

        ltp = self.executor.resolve_ltp(symbol)
        provisional_entry = round(float(ltp), 2) if (ltp and ltp > 0) else round(float(entry_price), 2)

        insert_v5_trade(
            v5_trade_id=v5_id, paper=False,
            symbol=symbol, token=token, side=side, qty=filled_qty,
            entry_price=provisional_entry, sl_price=sl_price, tp_price=tp_price,
            entry_candle_ts=entry_candle_ts,
            order_id=str(order_id),
        )
        write_audit_log(
            f"[V5][LIVE][ENTRY_PROVISIONAL] id={v5_id} {symbol} order={order_id} "
            f"prov_entry={provisional_entry} sl={sl_price} tp={tp_price} "
            f"qty={filled_qty} (fill+GTT pending)"
        )
        self._notify_entry(
            paper=False, symbol=symbol, side=side, entry=provisional_entry,
            sl=sl_price, tp=tp_price, qty=filled_qty,
        )

        # Phase 2: confirm fill → place GTT per the SL/TP matrix.
        threading.Thread(
            target=self._confirm_fill_worker,
            args=(v5_id, str(order_id), symbol, filled_qty, sl_price, tp_price),
            daemon=True,
            name=f"scalpv5-fill-{v5_id[:8]}",
        ).start()

    def _confirm_fill_worker(self, v5_id, order_id, symbol, qty, sl_price, tp_price):
        start = time.time()
        while time.time() - start < _ENTRY_FILL_CANCEL_S:
            try:
                info = self.executor.get_order_fill(order_id)
            except Exception as e:
                write_audit_log(
                    f"[V5][LIVE][FILL_POLL_ERR] {symbol} order={order_id} ERR={e}"
                )
                time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)
                continue

            status = (info.get("status") or "").upper()
            avg    = info.get("avg_price") or 0.0

            if status == "COMPLETE" and avg > 0:
                self._on_filled(v5_id, symbol, float(avg), qty, sl_price, tp_price)
                return
            if status in _DEAD_ORDER_STATUSES:
                self._on_dead(v5_id, symbol, status)
                return
            time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)

        self._cancel_unfilled(v5_id, order_id, symbol, sl_price, tp_price)

    def _on_filled(self, v5_id, symbol, fill_price, qty, sl_price, tp_price):
        row = get_v5_trade_by_id(v5_id)
        if not row or row.get("state") != "OPEN":
            write_audit_log(
                f"[V5][LIVE][FILL_STALE] id={v5_id} {symbol} fill={fill_price} "
                f"— row not OPEN, skipping"
            )
            return

        # Record the TRUE fill. SL/TP are config-absolute → NOT recomputed.
        confirm_v5_fill(v5_trade_id=v5_id, fill_price=fill_price)
        write_audit_log(
            f"[V5][LIVE][FILL_CONFIRMED] id={v5_id} {symbol} entry={fill_price} "
            f"sl={sl_price} tp={tp_price} — placing protective GTT (per matrix)"
        )

        # ── GTT MATRIX ───────────────────────────────────────
        # SL>0 & TP>0 → OCO ; SL>0 & TP=0 → SL-only ; else → NO GTT.
        has_sl = sl_price is not None and sl_price > 0
        has_tp = tp_price is not None and tp_price > 0

        if not has_sl:
            # SL disabled → no protective GTT regardless of TP (executor's LONG
            # branch has no TP-only single-leg path; engine tick + time exit
            # cover TP). Fully covered by the tick watcher.
            write_audit_log(
                f"[V5][LIVE][NO_GTT] {symbol} sl disabled — relying on tick/time exit "
                f"(has_tp={has_tp})"
            )
            return

        try:
            fresh_ltp = self.executor.resolve_ltp(symbol)
            gtt_id = self.executor.place_gtt_oco(
                symbol=symbol,
                qty=qty,
                sl_price=sl_price,
                tp_price=tp_price if has_tp else None,   # None → SL-only single
                last_price=fresh_ltp,
                direction="LONG",
            )
        except Exception as e:
            write_audit_log(
                f"[V5][LIVE][GTT_FAIL] {symbol} ERR={e} — position UNPROTECTED; "
                f"tick-watcher / time / EOD will close it."
            )
            record_alert(
                code="V5_GTT_FAIL",
                message=f"{symbol}: protective GTT failed — position UNPROTECTED, relies on tick/time/EOD.",
                severity="error", strategy_id=STRATEGY_ID, symbol=symbol, mode="live",
            )
            return

        # Re-check still OPEN (tick exit may have closed it during the GTT
        # round-trip); if not, cancel the GTT we just placed.
        row = get_v5_trade_by_id(v5_id)
        if not row or row.get("state") != "OPEN":
            write_audit_log(
                f"[V5][LIVE][GTT_RACE] id={v5_id} closed during GTT placement "
                f"— cancelling gtt_id={gtt_id}"
            )
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(f"[V5][LIVE][GTT_RACE_CANCEL_WARN] {symbol} ERR={e}")
            return

        link_v5_gtt(v5_trade_id=v5_id, gtt_id=str(gtt_id))
        write_audit_log(
            f"[V5][LIVE][ENTRY_CONFIRMED] id={v5_id} {symbol} entry={fill_price} "
            f"gtt={gtt_id} (sl={sl_price} tp={tp_price if has_tp else None})"
        )

    def _on_dead(self, v5_id, symbol, status):
        row = get_v5_trade_by_id(v5_id)
        if not row or row.get("state") != "OPEN":
            return
        write_audit_log(
            f"[V5][LIVE][DEAD_ENTRY] id={v5_id} {symbol} status={status} "
            f"— no position opened, closing row"
        )
        close_v5_trade(
            v5_trade_id=v5_id, exit_price=None,
            exit_order_id=None, exit_reason="BROKER_EXIT",
        )
        record_alert(
            code="V5_DEAD_ENTRY",
            message=f"{symbol}: entry {status} — no position opened.",
            severity="error", strategy_id=STRATEGY_ID, symbol=symbol, mode="live",
        )

    def _cancel_unfilled(self, v5_id, order_id, symbol, sl_price, tp_price):
        row = get_v5_trade_by_id(v5_id)
        if not row or row.get("state") != "OPEN":
            return
        write_audit_log(
            f"[V5][LIVE][ENTRY_TIMEOUT] id={v5_id} {symbol} order={order_id} "
            f"unfilled after {_ENTRY_FILL_CANCEL_S}s — cancelling"
        )
        try:
            self.executor.cancel_order(order_id)
        except Exception as e:
            write_audit_log(f"[V5][LIVE][CANCEL_WARN] {symbol} ERR={e}")

        time.sleep(1.0)
        try:
            info = self.executor.get_order_fill(order_id)
        except Exception:
            info = {"status": None, "avg_price": 0.0, "filled_qty": 0}

        status     = (info.get("status") or "").upper()
        avg        = info.get("avg_price") or 0.0
        filled_qty = int(info.get("filled_qty") or 0)

        row = get_v5_trade_by_id(v5_id)
        if not row or row.get("state") != "OPEN":
            return
        qty = int(row["qty"])

        # Filled at the wire before cancel landed → protect it.
        if status == "COMPLETE" and filled_qty >= qty and avg > 0:
            write_audit_log(
                f"[V5][LIVE][CANCEL_RACE_FILLED] {symbol} fill={avg} — protecting"
            )
            self._on_filled(v5_id, symbol, float(avg), qty, sl_price, tp_price)
            return

        # Partial fill → leave for manual; do NOT auto-act.
        if 0 < filled_qty < qty:
            write_audit_log(
                f"[V5][LIVE][PARTIAL_FILL][MANUAL] {symbol} filled={filled_qty}/{qty} "
                f"avg={avg} — LEFT FOR MANUAL. Partial LONG WITHOUT a GTT."
            )
            record_alert(
                code="V5_PARTIAL_FILL",
                message=f"{symbol}: partial fill {filled_qty}/{qty}, NO GTT — handle manually.",
                severity="error", strategy_id=STRATEGY_ID, symbol=symbol, mode="live",
            )
            return

        # Clean cancel → release gate.
        write_audit_log(
            f"[V5][LIVE][ENTRY_CANCELLED] {symbol} clean cancel — closing row"
        )
        close_v5_trade(
            v5_trade_id=v5_id, exit_price=None,
            exit_order_id=None, exit_reason="ENTRY_TIMEOUT",
        )

    # ==================================================
    # EXIT — called by the engine (SL/TP/TIME/MTM) or EOD
    # ==================================================

    def close_trade(self, *, v5_trade_id: str, exit_reason: str):
        """
        exit_reason: EMA_EXIT | SL | TP | MAX_LOSS | MAX_PROFIT | EOD | MANUAL
        PAPER → close at LTP.
        LIVE  → cancel GTT → verify position → market-sell; resolve exit price.
        """
        row = get_v5_trade_by_id(v5_trade_id)
        if not row or row.get("state") != "OPEN":
            write_audit_log(
                f"[V5][CLOSE_SKIP] id={v5_trade_id} reason={exit_reason} — not OPEN"
            )
            return

        paper   = bool(row.get("paper"))
        symbol  = row["symbol"]
        qty     = int(row["qty"])
        gtt_id  = row.get("gtt_id")
        entry   = row.get("entry_price")

        exit_price = self.executor.resolve_ltp(symbol)

        if paper:
            close_v5_trade(
                v5_trade_id=v5_trade_id,
                exit_price=float(exit_price) if exit_price else None,
                exit_order_id=None, exit_reason=exit_reason,
            )
            write_audit_log(
                f"[V5][PAPER][EXIT] id={v5_trade_id} {symbol} "
                f"reason={exit_reason} exit={exit_price}"
            )
            self._notify_exit(
                paper=True, symbol=symbol, entry=entry, qty=qty,
                exit_price=exit_price, exit_reason=exit_reason,
            )
            return

        # ── LIVE: cancel GTT → verify position → sell ──────────────────
        # HARD RULE: nothing in the cancel step may prevent the flatten below.
        if gtt_id:
            gone = True
            try:
                if hasattr(self.executor, "cancel_gtt_verified"):
                    gone = self.executor.cancel_gtt_verified(gtt_id)
                else:
                    self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(
                    f"[V5][LIVE][GTT_CANCEL_WARN] id={v5_trade_id} gtt={gtt_id} "
                    f"ERR={e} — proceeding to verify"
                )
            if not gone:
                write_audit_log(
                    f"[V5][LIVE][GTT_ORPHAN] id={v5_trade_id} gtt={gtt_id} "
                    f"STILL ARMED after cancel — alerting; still flattening"
                )
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({
                        "message": (
                            f"SCALP_V5 GTT {gtt_id} for {symbol} could NOT be cancelled "
                            f"(still armed). Closing the position now, but DELETE THIS GTT "
                            f"MANUALLY in Kite to avoid an unintended order."
                        ),
                        "severity": "error",
                    })
                except Exception:
                    pass

        # Verify the position is still open (GTT may have already filled).
        still_open = False
        try:
            for p in self.executor.get_open_positions():
                if p.get("tradingsymbol") == symbol and p.get("quantity", 0) != 0:
                    still_open = True
                    break
        except Exception as e:
            write_audit_log(
                f"[V5][LIVE][POS_CHECK_ERR] {symbol} ERR={e} — assuming open"
            )
            still_open = True

        exit_order_id = None
        if still_open:
            try:
                exit_order_id = self.executor.place_market_sell(
                    symbol=symbol, qty=qty
                )
                time.sleep(1.5)
                avg = self.executor.get_last_avg_price(exit_order_id)
                if avg and avg > 0:
                    exit_price = avg
            except Exception as e:
                write_audit_log(
                    f"[V5][LIVE][EXIT_SELL_FAIL] {symbol} ERR={e} — closing DB row anyway"
                )
        else:
            # GTT won the race → position already flat. The GTT that fired was
            # the SL leg (OCO) or the SL-only leg, so tag SL unless caller gave
            # an explicit non-tick reason.
            if exit_reason in ("EOD", "MANUAL", "MAX_LOSS", "MAX_PROFIT", "EMA_EXIT"):
                pass
            else:
                exit_reason = "SL"
            write_audit_log(
                f"[V5][LIVE][ALREADY_FLAT] {symbol} — GTT filled; "
                f"closing row reason={exit_reason}"
            )

        close_v5_trade(
            v5_trade_id=v5_trade_id,
            exit_price=float(exit_price) if exit_price else None,
            exit_order_id=str(exit_order_id) if exit_order_id else None,
            exit_reason=exit_reason,
        )
        write_audit_log(
            f"[V5][LIVE][EXIT] id={v5_trade_id} {symbol} reason={exit_reason} "
            f"exit={exit_price} order={exit_order_id}"
        )
        self._notify_exit(
            paper=False, symbol=symbol, entry=entry, qty=qty,
            exit_price=exit_price, exit_reason=exit_reason,
        )

    # ==================================================
    # SELF-CONTAINED MTM CHECK (called by the engine on ticks)
    # Returns True if a breach fired (and the trade was force-closed).
    # ==================================================

    def mtm_check(self, open_row: dict) -> bool:
        """
        Compute realised-today + unrealised(open) for V5 and force-exit on a
        max_loss / max_profit breach. Fail-OPEN on an unresolvable LTP (do not
        close on stale/phantom prices). Sets the V5-local day-block latch on a
        breach so re-entry is blocked for the session.
        """
        max_loss, max_profit = self._limits()
        if max_loss <= 0 and max_profit <= 0:
            return False
        if is_v5_day_blocked():
            return False
        if not open_row or open_row.get("state") != "OPEN":
            return False

        paper  = bool(open_row.get("paper"))
        symbol = open_row.get("symbol")
        entry  = open_row.get("entry_price")
        qty    = open_row.get("qty")

        # Unrealised: LONG (ltp - entry) * qty. Fail-open if LTP unresolvable.
        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
            ltp = self.executor.resolve_ltp(symbol)
        if not ltp or ltp <= 0 or entry is None or not qty:
            return False  # indeterminate → no action

        unrealised = (float(ltp) - float(entry)) * int(qty)
        realised   = get_total_pnl_v5_today(paper=paper)
        mtm        = realised + unrealised

        breach_reason = None
        if max_loss > 0 and mtm <= -max_loss:
            breach_reason = "MAX_LOSS"
        elif max_profit > 0 and mtm >= max_profit:
            breach_reason = "MAX_PROFIT"

        if not breach_reason:
            return False

        write_audit_log(
            f"[V5][MTM][BREACH] {breach_reason} mtm=₹{mtm:,.0f} "
            f"(realised ₹{realised:,.0f} + open ₹{unrealised:,.0f}) "
            f"limit_loss=−₹{max_loss:,.0f} limit_profit=₹{max_profit:,.0f} "
            f"— squaring off {symbol}"
        )
        record_alert(
            code=f"V5_{breach_reason}",
            message=(
                f"SCALP_V5 hit daily {breach_reason} on live MTM "
                f"(MTM ₹{mtm:,.0f}) — squaring off. New entries blocked for the session."
            ),
            severity=("warning" if breach_reason == "MAX_LOSS" else "info"),
            strategy_id=STRATEGY_ID, symbol=symbol,
            mode=("paper" if paper else "live"),
        )
        _set_v5_day_blocked()
        try:
            self.close_trade(v5_trade_id=open_row["v5_trade_id"], exit_reason=breach_reason)
        except Exception as e:
            write_audit_log(f"[V5][MTM][CLOSE_ERR] {symbol} ERR={e}")
        return True

    # ==================================================
    # EOD SQUARE-OFF — called by scalpv5_live_eod_job
    # ==================================================

    def eod_squareoff(self) -> int:
        from app.db.scalpv5_repo import get_all_open_v5_trades
        rows = get_all_open_v5_trades()
        if not rows:
            write_audit_log("[V5][EOD] No open trades")
            return 0
        write_audit_log(f"[V5][EOD] Squaring off {len(rows)} trade(s)")
        closed = 0
        for r in rows:
            try:
                self.close_trade(v5_trade_id=r["v5_trade_id"], exit_reason="EOD")
                closed += 1
            except Exception as e:
                write_audit_log(f"[V5][EOD][ERROR] id={r.get('v5_trade_id')} ERR={e}")
        return closed

    # ==================================================
    # HELPERS
    # ==================================================

    def _skip(self, reason: str, symbol: str):
        write_audit_log(f"[V5][SKIP] reason={reason} symbol={symbol}")