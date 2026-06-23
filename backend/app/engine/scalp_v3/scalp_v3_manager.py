# backend/app/engine/scalp_v3/scalp_v3_manager.py
#
# SCALP_V3 — TEST option-BUYING hedge strategy (derived from SCALP_V1).
#
# ONE LOGICAL TRADE = TWO INSTRUMENTS:
#   signal_*  — the contract that fired the signal (e.g. 24500CE). TRACKED for
#               its own SL/TP; NEVER traded. Drives WHEN to exit.
#   hedge_*   — the contract actually BOUGHT (e.g. 24450PE). LONG. Protected by
#               an SL-only GTT at (hedge_fill - max_sl_points). This is the
#               position that carries P&L.
#
# This MANAGER owns order placement + DB lifecycle for V3. The ENGINE
# (scalp_v3_engine.py) owns signal generation, pairing, and the tick-driven
# exit decision; it calls open_hedge_trade() / close_hedge_trade() here.
#
# NOTIFICATIONS:
#   V3 does NOT use TradeStateManager / PaperTradeRecorder, so it does not get
#   in-app/Telegram trade notifications "for free" the way V1/V2/BB/HA do. We
#   therefore call the shared notify_* functions explicitly at entry and exit.
#   Those functions record an in-app event (audio+toast) BEFORE the Telegram
#   filter and then send Telegram. We report the HEDGE symbol (the instrument
#   that actually carries P&L) and compute P&L as LONG: (exit - entry) * qty.
#   Operational alerts (GTT fail / dead entry / partial fill) continue to use
#   record_alert as before. All notify_* / record_* calls are best-effort and
#   wrapped so they can never break the trading path.
#
# GLOBAL SINGLE-TRADE GATE (DB-backed):
#   At most ONE OPEN V3 trade at a time, across CE and PE. The gate is checked
#   against the DB (get_open_v3_trade) so it survives restarts and cannot be
#   held by stale in-memory state.
#
# LIVE ENTRY is two-phase (mirrors SCALP_V1 on_sell_signal → fill-confirm):
#   1. place_buy(PE) → provisional row (entry = protected limit).
#   2. background poll get_order_fill → on COMPLETE: confirm_hedge_fill
#      (recompute hedge_sl = fill - max_sl), then place SL-only GTT, link it.
#      on DEAD / timeout: close row, release gate.
#   The SL-only GTT is the broker-side protection for the hedge's OWN stop.
#
# EXIT (cancel → verify → sell):
#   On a signal-driven exit (SIG_SL/SIG_TP) or EOD, cancel the hedge GTT first,
#   verify the PE is still open (the GTT may have already filled), then sell.
#   If already flat, the GTT won the race → just close the DB row as HEDGE_SL.
#
# ISOLATION: imports SCALP_V1's StrategyEngine/selection ONLY via the engine;
# this file touches only scalp_v3_repo + the execution router + config. No edits
# to TradeStateManager / trades / paper_trades / GTTMonitor.

import time
import uuid
import threading
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.utils.session_utils import is_within_session

# Shared trade notifications (in-app audio/toast + Telegram). V3 calls these
# explicitly because it does not route through TradeStateManager/PaperTradeRecorder.
from app.api.telegram_api import (
    notify_trade_entry,
    notify_tp_exit,
    notify_sl_exit,
    notify_manual_exit,
)

from app.db.scalp_v3_repo import (
    insert_v3_trade,
    confirm_hedge_fill,
    link_hedge_gtt,
    close_v3_trade,
    get_open_v3_trade,
    get_v3_trade_by_id,
    get_all_open_v3_trades,
)

STRATEGY_ID = "SCALP_V3"

# Live entry fill-confirm tuning (mirrors SCALP_V1 _ENTRY_FILL_* semantics).
_ENTRY_FILL_CANCEL_S        = 50
_ENTRY_FILL_POLL_INTERVAL_S = 2
_DEAD_ORDER_STATUSES        = {"REJECTED", "CANCELLED", "LAPSED"}


@dataclass
class V3Trade:
    v3_trade_id:        str
    paper:              bool
    # signal (tracked, never traded)
    signal_symbol:      str
    signal_token:       int
    signal_side:        str
    signal_entry_price: float
    signal_sl:          float
    signal_tp:          float
    signal_candle_ts:   int
    # hedge (bought, protected)
    hedge_symbol:       str
    hedge_token:        int
    hedge_side:         str
    hedge_qty:          int
    hedge_entry_price:  float
    hedge_sl:           float
    hedge_order_id:     Optional[str] = None
    hedge_gtt_id:       Optional[str] = None
    state:              str = "OPEN"


class ScalpV3Manager:
    """
    One instance per process for SCALP_V3 (single-trade-global). Constructed by
    the engine, which passes the shared execution router (same factory the rest
    of the app uses, so relay / SEBI-IP routing is identical).
    """

    def __init__(self, executor):
        self.executor = executor
        self._entry_lock = threading.Lock()
        self._reserved   = False   # in-flight entry guard (pre-DB-row)

    # ==================================================
    # NOTIFICATION HELPERS (best-effort; never break trading)
    # Report the HEDGE symbol; P&L is LONG: (exit - entry) * qty.
    # ==================================================

    def _notify_entry(self, *, paper: bool, hedge_symbol: str, hedge_side: str,
                      hedge_entry: float, hedge_sl: float, qty: int):
        try:
            notify_trade_entry({
                "strategy_id": STRATEGY_ID,
                "mode":        "paper" if paper else "live",
                "symbol":      hedge_symbol,
                "side":        hedge_side,
                "entry_price": hedge_entry,
                "quantity":    qty,
                "sl":          hedge_sl,
                "tp":          None,   # hedge has no TP leg (SL-only GTT)
                "trade_direction": "LONG",
            })
        except Exception as e:
            write_audit_log(f"[V3][NOTIFY][ENTRY_ERROR] {hedge_symbol} ERR={e}")

    def _notify_exit(self, *, paper: bool, hedge_symbol: str,
                     hedge_entry: Optional[float], qty: Optional[int],
                     exit_price: Optional[float], exit_reason: str):
        try:
            pnl = None
            if exit_price is not None and hedge_entry is not None and qty:
                pnl = (float(exit_price) - float(hedge_entry)) * int(qty)

            payload = {
                "strategy_id": STRATEGY_ID,
                "mode":        "paper" if paper else "live",
                "symbol":      hedge_symbol,
                "entry_price": hedge_entry,
                "exit_price":  exit_price,
                "pnl":         pnl,
                "trade_direction": "LONG",
            }

            # Map V3 exit reasons → the three exit notifiers:
            #   SIG_TP            → target hit (happy tone)
            #   SIG_SL / HEDGE_SL → stop hit  (loss tone)
            #   EOD / MANUAL / *  → generic close (P&L-sign tone via Option A)
            if exit_reason == "SIG_TP":
                notify_tp_exit(payload)
            elif exit_reason in ("SIG_SL", "HEDGE_SL"):
                notify_sl_exit(payload)
            else:
                payload["exit_reason"] = exit_reason
                notify_manual_exit(payload)
        except Exception as e:
            write_audit_log(f"[V3][NOTIFY][EXIT_ERROR] {hedge_symbol} ERR={e}")

    # ==================================================
    # GATES
    # ==================================================

    def _gate_open(self) -> Optional[dict]:
        """Return the open V3 trade row (DB-backed) if one exists, else None."""
        return get_open_v3_trade()

    def _cfg(self) -> dict:
        return load_strategy_config(STRATEGY_ID)

    def _mode(self) -> str:
        return self._cfg().get("trade_execution_mode", "PAPER").upper()

    def _max_sl_points(self) -> float:
        return float(self._cfg().get("max_sl_points", 20) or 20)
 
    # ── SCALP_V3_HEDGE_SL BEGIN ──
    def _hedge_sl_points(self) -> float:
        """
        Hedge SL-only GTT distance (points below the hedge fill).
 
        DECOUPLED from the signal max_sl_points. Option-A fallback: if the
        dedicated hedge_sl_points key is absent (old config files), fall back
        to max_sl_points so existing behaviour is preserved until the user
        sets a hedge value in the UI. Final fallback 20.
        """
        cfg = self._cfg()
        return float(
            cfg.get("hedge_sl_points",
                    cfg.get("max_sl_points", 20))
            or 20
        )
    # ── SCALP_V3_HEDGE_SL END ──


    def _within_session(self) -> bool:
        cfg = self._cfg()
        primary = (cfg.get("session") or {}).get("primary") or {}
        return is_within_session(
            datetime.now(), primary.get("start"), primary.get("end")
        )

    # ==================================================
    # ENTRY — called by the engine when a signal fires + hedge is paired
    # ==================================================

    def open_hedge_trade(
        self,
        *,
        signal_symbol: str,
        signal_token: int,
        signal_side: str,           # "CE" | "PE"
        signal_entry_price: float,
        signal_sl: float,           # CE/PE signal levels (premium space of SIGNAL)
        signal_tp: float,
        signal_candle_ts: int,
        hedge_symbol: str,
        hedge_token: int,
        hedge_side: str,            # opposite of signal_side
    ):
        # ── pre-DB gates ──────────────────────────────
        if not load_global_config().get("trade_on", False):
            self._skip("GLOBAL_TRADE_OFF", signal_symbol, hedge_symbol)
            return
        if not self._within_session():
            self._skip("OUTSIDE_SESSION", signal_symbol, hedge_symbol)
            return

        with self._entry_lock:
            if self._reserved:
                self._skip("ENTRY_IN_PROGRESS", signal_symbol, hedge_symbol)
                return
            # DB-backed global single-trade gate.
            existing = self._gate_open()
            if existing is not None:
                write_audit_log(
                    f"[V3][SINGLE_TRADE_GATE] DROP signal={signal_symbol} "
                    f"hedge={hedge_symbol} — already OPEN id={existing.get('v3_trade_id')} "
                    f"({existing.get('signal_symbol')}→{existing.get('hedge_symbol')})"
                )
                return
            self._reserved = True

        try:
            cfg       = self._cfg()
            lots      = int(cfg.get("quantity", {}).get("lots", 1))
            lot_size  = int(cfg.get("quantity", {}).get("lot_size", 65))
            qty       = lots * lot_size
            max_sl    = self._hedge_sl_points()   # SCALP_V3_HEDGE_SL: hedge GTT distance (decoupled from signal max_sl)
            paper     = (self._mode() == "PAPER")
            v3_id     = str(uuid.uuid4())

            # Hedge LTP now (REST primary). Provisional entry for both modes.
            hedge_ltp = self.executor.resolve_ltp(hedge_symbol)
            if not hedge_ltp or hedge_ltp <= 0:
                write_audit_log(
                    f"[V3][ENTRY_ABORT] hedge LTP unavailable for {hedge_symbol} "
                    f"— skipping entry"
                )
                return

            if paper:
                self._open_paper(
                    v3_id=v3_id, qty=qty, max_sl=max_sl, hedge_ltp=hedge_ltp,
                    signal_symbol=signal_symbol, signal_token=signal_token,
                    signal_side=signal_side, signal_entry_price=signal_entry_price,
                    signal_sl=signal_sl, signal_tp=signal_tp,
                    signal_candle_ts=signal_candle_ts,
                    hedge_symbol=hedge_symbol, hedge_token=hedge_token,
                    hedge_side=hedge_side,
                )
            else:
                self._open_live(
                    v3_id=v3_id, qty=qty, max_sl=max_sl, hedge_ltp=hedge_ltp,
                    signal_symbol=signal_symbol, signal_token=signal_token,
                    signal_side=signal_side, signal_entry_price=signal_entry_price,
                    signal_sl=signal_sl, signal_tp=signal_tp,
                    signal_candle_ts=signal_candle_ts,
                    hedge_symbol=hedge_symbol, hedge_token=hedge_token,
                    hedge_side=hedge_side,
                )
        finally:
            # Reservation is released here for PAPER (fully done) and for LIVE
            # the DB row now exists so the gate is held by the row, not _reserved.
            with self._entry_lock:
                self._reserved = False

    # --------------------------------------------------
    # PAPER entry
    # --------------------------------------------------

    def _open_paper(self, *, v3_id, qty, max_sl, hedge_ltp,
                    signal_symbol, signal_token, signal_side, signal_entry_price,
                    signal_sl, signal_tp, signal_candle_ts,
                    hedge_symbol, hedge_token, hedge_side):
        hedge_entry = round(hedge_ltp, 2)
        hedge_sl    = round(hedge_entry - max_sl, 2)

        insert_v3_trade(
            v3_trade_id=v3_id, paper=True,
            signal_symbol=signal_symbol, signal_token=signal_token,
            signal_side=signal_side, signal_entry_price=signal_entry_price,
            signal_sl=signal_sl, signal_tp=signal_tp, signal_candle_ts=signal_candle_ts,
            hedge_symbol=hedge_symbol, hedge_token=hedge_token, hedge_side=hedge_side,
            hedge_qty=qty, hedge_entry_price=hedge_entry, hedge_sl=hedge_sl,
            hedge_order_id=None,
        )
        write_audit_log(
            f"[V3][PAPER][ENTRY] id={v3_id} signal={signal_symbol} "
            f"(sl={signal_sl} tp={signal_tp}) → BUY {hedge_symbol} "
            f"entry={hedge_entry} hedge_sl={hedge_sl} qty={qty}"
        )

        # In-app + Telegram entry notification (hedge is the real position).
        self._notify_entry(
            paper=True, hedge_symbol=hedge_symbol, hedge_side=hedge_side,
            hedge_entry=hedge_entry, hedge_sl=hedge_sl, qty=qty,
        )

    # --------------------------------------------------
    # LIVE entry (two-phase)
    # --------------------------------------------------

    def _open_live(self, *, v3_id, qty, max_sl, hedge_ltp,
                   signal_symbol, signal_token, signal_side, signal_entry_price,
                   signal_sl, signal_tp, signal_candle_ts,
                   hedge_symbol, hedge_token, hedge_side):
        broker_symbol = self.executor.resolve_symbol(hedge_symbol)

        # Phase 1: BUY the hedge (protected-limit inside executor).
        try:
            order_id, _, filled_qty = self.executor.place_buy(
                broker_symbol, hedge_token, qty
            )
        except Exception as e:
            write_audit_log(f"[V3][LIVE][BUY_FAIL] {hedge_symbol} ERR={e}")
            return

        if filled_qty <= 0:
            write_audit_log(f"[V3][LIVE][BUY_ZERO_QTY] {hedge_symbol} — aborting")
            return

        provisional_entry = round(hedge_ltp, 2)
        provisional_sl    = round(provisional_entry - max_sl, 2)

        insert_v3_trade(
            v3_trade_id=v3_id, paper=False,
            signal_symbol=signal_symbol, signal_token=signal_token,
            signal_side=signal_side, signal_entry_price=signal_entry_price,
            signal_sl=signal_sl, signal_tp=signal_tp, signal_candle_ts=signal_candle_ts,
            hedge_symbol=hedge_symbol, hedge_token=hedge_token, hedge_side=hedge_side,
            hedge_qty=filled_qty, hedge_entry_price=provisional_entry,
            hedge_sl=provisional_sl, hedge_order_id=str(order_id),
        )
        write_audit_log(
            f"[V3][LIVE][ENTRY_PROVISIONAL] id={v3_id} signal={signal_symbol} "
            f"→ BUY {hedge_symbol} order={order_id} prov_entry={provisional_entry} "
            f"prov_sl={provisional_sl} qty={filled_qty} (fill+GTT pending)"
        )

        # In-app + Telegram entry notification at provisional entry. The order
        # is placed and the position is being established; the confirmed fill
        # price/SL are finalised in the background worker, but the user should
        # hear/see the entry now. (Provisional entry ≈ protected limit.)
        self._notify_entry(
            paper=False, hedge_symbol=hedge_symbol, hedge_side=hedge_side,
            hedge_entry=provisional_entry, hedge_sl=provisional_sl, qty=filled_qty,
        )

        # Phase 2: confirm fill → place SL-only GTT.
        threading.Thread(
            target=self._confirm_fill_worker,
            args=(v3_id, str(order_id), hedge_symbol, filled_qty, max_sl),
            daemon=True,
            name=f"scalp-v3-fill-{v3_id[:8]}",
        ).start()

    def _confirm_fill_worker(self, v3_id, order_id, hedge_symbol, qty, max_sl):
        start = time.time()
        while time.time() - start < _ENTRY_FILL_CANCEL_S:
            try:
                info = self.executor.get_order_fill(order_id)
            except Exception as e:
                write_audit_log(
                    f"[V3][LIVE][FILL_POLL_ERR] {hedge_symbol} order={order_id} ERR={e}"
                )
                time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)
                continue

            status = (info.get("status") or "").upper()
            avg    = info.get("avg_price") or 0.0

            if status == "COMPLETE" and avg > 0:
                self._on_hedge_filled(v3_id, hedge_symbol, float(avg), qty, max_sl)
                return
            if status in _DEAD_ORDER_STATUSES:
                self._on_hedge_dead(v3_id, hedge_symbol, status)
                return
            time.sleep(_ENTRY_FILL_POLL_INTERVAL_S)

        # Timeout → cancel unfilled entry.
        self._cancel_unfilled(v3_id, order_id, hedge_symbol)

    def _on_hedge_filled(self, v3_id, hedge_symbol, fill_price, qty, max_sl):
        row = get_v3_trade_by_id(v3_id)
        if not row or row.get("state") != "OPEN":
            write_audit_log(
                f"[V3][LIVE][FILL_STALE] id={v3_id} {hedge_symbol} "
                f"fill={fill_price} — row not OPEN, skipping"
            )
            return

        # Recompute SL from the TRUE fill (fill-relative per spec) + persist.
        new_sl = confirm_hedge_fill(
            v3_trade_id=v3_id, fill_price=fill_price, max_sl_points=max_sl
        )
        write_audit_log(
            f"[V3][LIVE][FILL_CONFIRMED] id={v3_id} {hedge_symbol} "
            f"entry={fill_price} sl={new_sl} — placing SL-only GTT"
        )

        # Place the SL-only GTT (LONG, tp_price=None → GTT_TYPE_SINGLE SELL@SL).
        # Fresh LTP as last_price to avoid stale-LTP rejection.
        try:
            fresh_ltp = self.executor.resolve_ltp(hedge_symbol)
            gtt_id = self.executor.place_gtt_oco(
                symbol=hedge_symbol,
                qty=qty,
                sl_price=new_sl,
                tp_price=None,            # SL-only — no TP leg on the hedge
                last_price=fresh_ltp,
                direction="LONG",
            )
        except Exception as e:
            write_audit_log(
                f"[V3][LIVE][GTT_FAIL] {hedge_symbol} ERR={e} — hedge UNPROTECTED; "
                f"signal-watcher / EOD will close it."
            )
            record_alert(
                code="V3_GTT_FAIL",
                message=f"{hedge_symbol}: SL-only GTT failed — hedge UNPROTECTED, relies on watcher/EOD.",
                severity="error", strategy_id=STRATEGY_ID, symbol=hedge_symbol, mode="live",
            )
            return

        # Re-check still OPEN (could have been closed by the watcher during the
        # GTT round-trip); if not, cancel the GTT we just placed.
        row = get_v3_trade_by_id(v3_id)
        if not row or row.get("state") != "OPEN":
            write_audit_log(
                f"[V3][LIVE][GTT_RACE] id={v3_id} closed during GTT placement "
                f"— cancelling gtt_id={gtt_id}"
            )
            try:
                self.executor.cancel_gtt(gtt_id)
            except Exception as e:
                write_audit_log(f"[V3][LIVE][GTT_RACE_CANCEL_WARN] {hedge_symbol} ERR={e}")
            return

        link_hedge_gtt(v3_trade_id=v3_id, gtt_id=str(gtt_id))
        write_audit_log(
            f"[V3][LIVE][ENTRY_CONFIRMED] id={v3_id} {hedge_symbol} "
            f"entry={fill_price} hedge_sl={new_sl} gtt={gtt_id}"
        )

    def _on_hedge_dead(self, v3_id, hedge_symbol, status):
        row = get_v3_trade_by_id(v3_id)
        if not row or row.get("state") != "OPEN":
            return
        write_audit_log(
            f"[V3][LIVE][DEAD_ENTRY] id={v3_id} {hedge_symbol} status={status} "
            f"— no position opened, closing row"
        )
        close_v3_trade(
            v3_trade_id=v3_id, exit_price=None,
            exit_order_id=None, exit_reason="BROKER_EXIT",
        )
        record_alert(
            code="V3_DEAD_ENTRY",
            message=f"{hedge_symbol}: entry {status} — no position opened.",
            severity="error", strategy_id=STRATEGY_ID, symbol=hedge_symbol, mode="live",
        )

    def _cancel_unfilled(self, v3_id, order_id, hedge_symbol):
        row = get_v3_trade_by_id(v3_id)
        if not row or row.get("state") != "OPEN":
            return
        write_audit_log(
            f"[V3][LIVE][ENTRY_TIMEOUT] id={v3_id} {hedge_symbol} order={order_id} "
            f"unfilled after {_ENTRY_FILL_CANCEL_S}s — cancelling"
        )
        try:
            self.executor.cancel_order(order_id)
        except Exception as e:
            write_audit_log(f"[V3][LIVE][CANCEL_WARN] {hedge_symbol} ERR={e}")

        time.sleep(1.0)
        try:
            info = self.executor.get_order_fill(order_id)
        except Exception:
            info = {"status": None, "avg_price": 0.0, "filled_qty": 0}

        status     = (info.get("status") or "").upper()
        avg        = info.get("avg_price") or 0.0
        filled_qty = int(info.get("filled_qty") or 0)

        row = get_v3_trade_by_id(v3_id)
        if not row or row.get("state") != "OPEN":
            return
        qty    = int(row["hedge_qty"])
        max_sl = self._hedge_sl_points()   # SCALP_V3_HEDGE_SL: hedge GTT distance (decoupled from signal max_sl)

        # Filled at the wire before cancel landed → protect it.
        if status == "COMPLETE" and filled_qty >= qty and avg > 0:
            write_audit_log(
                f"[V3][LIVE][CANCEL_RACE_FILLED] {hedge_symbol} fill={avg} "
                f"— protecting position"
            )
            self._on_hedge_filled(v3_id, hedge_symbol, float(avg), qty, max_sl)
            return

        # Partial fill → leave for manual; do NOT auto-act.
        if 0 < filled_qty < qty:
            write_audit_log(
                f"[V3][LIVE][PARTIAL_FILL][MANUAL] {hedge_symbol} "
                f"filled={filled_qty}/{qty} avg={avg} — LEFT FOR MANUAL. "
                f"Partial LONG hedge WITHOUT a GTT."
            )
            record_alert(
                code="V3_PARTIAL_FILL",
                message=f"{hedge_symbol}: partial fill {filled_qty}/{qty}, NO GTT — handle manually.",
                severity="error", strategy_id=STRATEGY_ID, symbol=hedge_symbol, mode="live",
            )
            return

        # Clean cancel → release gate.
        write_audit_log(
            f"[V3][LIVE][ENTRY_CANCELLED] {hedge_symbol} clean cancel — closing row"
        )
        close_v3_trade(
            v3_trade_id=v3_id, exit_price=None,
            exit_order_id=None, exit_reason="ENTRY_TIMEOUT",
        )

    # ==================================================
    # EXIT — called by the engine (signal SL/TP) or EOD
    # ==================================================

    def close_hedge_trade(self, *, v3_trade_id: str, exit_reason: str):
        """
        exit_reason: SIG_SL | SIG_TP | HEDGE_SL | EOD | MANUAL
        PAPER  → close at hedge LTP.
        LIVE   → cancel GTT → verify position → sell; resolve exit price.
        """
        row = get_v3_trade_by_id(v3_trade_id)
        if not row or row.get("state") != "OPEN":
            write_audit_log(
                f"[V3][CLOSE_SKIP] id={v3_trade_id} reason={exit_reason} — not OPEN"
            )
            return

        paper        = bool(row.get("paper"))
        hedge_symbol = row["hedge_symbol"]
        hedge_qty    = int(row["hedge_qty"])
        hedge_gtt_id = row.get("hedge_gtt_id")
        # Capture entry now (for P&L in the notification) — close_v3_trade does
        # not return it, and the row is no longer OPEN after the close.
        hedge_entry  = row.get("hedge_entry_price")

        # Best exit price (REST primary, LTPStore fallback inside resolve_ltp).
        exit_price = self.executor.resolve_ltp(hedge_symbol)

        if paper:
            close_v3_trade(
                v3_trade_id=v3_trade_id,
                exit_price=float(exit_price) if exit_price else None,
                exit_order_id=None, exit_reason=exit_reason,
            )
            write_audit_log(
                f"[V3][PAPER][EXIT] id={v3_trade_id} {hedge_symbol} "
                f"reason={exit_reason} exit={exit_price}"
            )
            self._notify_exit(
                paper=True, hedge_symbol=hedge_symbol, hedge_entry=hedge_entry,
                qty=hedge_qty, exit_price=exit_price, exit_reason=exit_reason,
            )
            return

        # ── LIVE: cancel GTT → verify position → sell ──────────────────
        # HARD RULE: nothing in this cancel step may ever prevent the
        # position flatten below. Everything is wrapped; any failure here
        # degrades to "proceed to verify+sell", never to "abort exit".
        if hedge_gtt_id:
            gone = True
            try:
                if hasattr(self.executor, "cancel_gtt_verified"):
                    gone = self.executor.cancel_gtt_verified(hedge_gtt_id)
                else:
                    # Executor is a router/wrapper without the verified method —
                    # fall back to the plain cancel (original behaviour).
                    self.executor.cancel_gtt(hedge_gtt_id)
            except Exception as e:
                write_audit_log(
                    f"[V3][LIVE][GTT_CANCEL_WARN] id={v3_trade_id} "
                    f"gtt={hedge_gtt_id} ERR={e} — proceeding to verify"
                )
            if not gone:
                write_audit_log(
                    f"[V3][LIVE][GTT_ORPHAN] id={v3_trade_id} gtt={hedge_gtt_id} "
                    f"STILL ARMED after cancel — alerting; still flattening below"
                )
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({
                        "message": (
                            f"SCALP_V3 GTT {hedge_gtt_id} for {hedge_symbol} could NOT be "
                            f"cancelled (still armed at broker). Closing the position now, but "
                            f"DELETE THIS GTT MANUALLY in Kite to avoid an unintended order."
                        ),
                        "severity": "error",
                    })
                except Exception:
                    pass

        # Verify the hedge is still open (GTT may have already filled).
        still_open = False
        try:
            for p in self.executor.get_open_positions():
                if p.get("tradingsymbol") == hedge_symbol and p.get("quantity", 0) != 0:
                    still_open = True
                    break
        except Exception as e:
            write_audit_log(
                f"[V3][LIVE][POS_CHECK_ERR] {hedge_symbol} ERR={e} — assuming open"
            )
            still_open = True

        exit_order_id = None
        if still_open:
            try:
                exit_order_id = self.executor.place_market_sell(
                    symbol=hedge_symbol, qty=hedge_qty
                )
                time.sleep(1.5)
                avg = self.executor.get_last_avg_price(exit_order_id)
                if avg and avg > 0:
                    exit_price = avg
            except Exception as e:
                write_audit_log(
                    f"[V3][LIVE][EXIT_SELL_FAIL] {hedge_symbol} ERR={e} "
                    f"— closing DB row anyway"
                )
        else:
            # GTT won the race → position already flat. Tag HEDGE_SL unless the
            # caller already specified a signal reason.
            if exit_reason in ("EOD", "MANUAL"):
                pass
            else:
                exit_reason = "HEDGE_SL"
            write_audit_log(
                f"[V3][LIVE][ALREADY_FLAT] {hedge_symbol} — GTT filled; "
                f"closing row reason={exit_reason}"
            )

        close_v3_trade(
            v3_trade_id=v3_trade_id,
            exit_price=float(exit_price) if exit_price else None,
            exit_order_id=str(exit_order_id) if exit_order_id else None,
            exit_reason=exit_reason,
        )
        write_audit_log(
            f"[V3][LIVE][EXIT] id={v3_trade_id} {hedge_symbol} "
            f"reason={exit_reason} exit={exit_price} order={exit_order_id}"
        )

        # In-app + Telegram exit notification. exit_reason here is the FINAL
        # reason (HEDGE_SL if the GTT won the race), so the tone matches reality.
        self._notify_exit(
            paper=False, hedge_symbol=hedge_symbol, hedge_entry=hedge_entry,
            qty=hedge_qty, exit_price=exit_price, exit_reason=exit_reason,
        )

    # ==================================================
    # EOD SQUARE-OFF — called by scalp_v3_live_eod_job
    # ==================================================

    def eod_squareoff(self) -> int:
        """
        Close every OPEN V3 trade (paper + live) at EOD. Uses close_hedge_trade
        with reason EOD, which handles both modes and the cancel→verify→sell
        path for live. Returns count closed.
        """
        rows = get_all_open_v3_trades()
        if not rows:
            write_audit_log("[V3][EOD] No open trades")
            return 0
        write_audit_log(f"[V3][EOD] Squaring off {len(rows)} trade(s)")
        closed = 0
        for r in rows:
            try:
                self.close_hedge_trade(v3_trade_id=r["v3_trade_id"], exit_reason="EOD")
                closed += 1
            except Exception as e:
                write_audit_log(
                    f"[V3][EOD][ERROR] id={r.get('v3_trade_id')} ERR={e}"
                )
        return closed

    # ==================================================
    # HELPERS
    # ==================================================

    def _skip(self, reason: str, signal_symbol: str, hedge_symbol: str):
        write_audit_log(
            f"[V3][SKIP] reason={reason} signal={signal_symbol} hedge={hedge_symbol}"
        )