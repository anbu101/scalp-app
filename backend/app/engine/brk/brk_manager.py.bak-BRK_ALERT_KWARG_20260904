# backend/app/engine/brk/brk_manager.py
#
# ── BRK_V1 TRADE MANAGER ── order placement + paper_trades lifecycle.
# ============================================================================
# Fence: BRK_V1_LIVE_20260902
#
# ONE LOGICAL TRADE = ONE INSTRUMENT (LONG). The engine (brk_engine) owns all
# DECISIONS via brk_live_core; this manager owns orders and DB rows only.
#
# STORAGE (LD3): generic paper_trades for BOTH modes (trade_mode PAPER/LIVE —
# the TSG pattern; everything downstream is free). tag ("BRK"/"BRK·S2") is
# stored in group_id; the live GTT id is persisted in trade_class as
# "GTT:<id>" so a mid-day restart can recover it (paper_trades has no GTT
# column and a private table is not worth the three display unions).
#
# LIVE ENTRY is two-phase (SCALP_V5 pattern):
#   1. place_buy(symbol, token, qty) → bounded fill poll (get_order_fill;
#      found=False is PENDING, never rejected).
#   2. on COMPLETE: insert the row at the REAL fill (divergence ledger #1),
#      then place ONE OCO GTT (LD4): place_gtt_oco(direction="LONG",
#      sl_price, tp_price) — tp None when tp_pts=0. GTT id → trade_class.
#      on DEAD/timeout: cancel, no row, session stays done (no re-entry —
#      the backtest took exactly one shot too).
#
# EXIT (LD4/LD8, fleet GTT-race doctrine): cancel_gtt_verified FIRST, then
# market-sell, then close the row at the sell fill. If the verified cancel
# reveals the GTT already fired (position flat at the broker), the GTT won
# the race: close the row at LTP with the engine's reason — never a second
# sell. PAPER exits are engine ticks priced at LTP.
#
# RESTART: resume_from_db() rebuilds the open position from OPEN BRK_V1
# paper_trades rows (mandatory smoke leg). s1_result for the S2 gate is
# recomputed from today's CLOSED "BRK"-tagged rows.
# ============================================================================

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

# ── IMPORTS ARE LOAD-BEARING (2026-09-03 scar) ─────────────────────────
# v1 imported the NONEXISTENT app.db.database inside a blanket
# try/except-ImportError whose "standalone tests" fallback set the audit
# logger to print() and the repo functions to None. In production that
# meant: no DB rows ever written, no log lines from this module, resume
# permanently blind — while orders and telegram worked. A LIVE position
# became invisible to the app on restart. Rules now:
#   1. The repo import is app.db.sqlite (the fleet's real conn module —
#      what paper_trades_repo itself uses).
#   2. Each import group degrades SEPARATELY, records WHY, and
#   3. a degraded PERSISTENCE layer refuses to trade (fail closed) instead
#      of trading quietly from memory.
IMPORT_DEGRADED = ""
try:
    from app.event_bus.audit_logger import write_audit_log
    from app.event_bus.inapp_events import record_alert
except ImportError as _e:                                  # pure-test only
    IMPORT_DEGRADED += f"audit/events: {_e!r}; "

    def write_audit_log(msg):                              # type: ignore
        print(msg)

    def record_alert(**k):                                 # type: ignore
        print("ALERT", k)
try:
    from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade
    from app.db.sqlite import get_conn
except ImportError as _e:                                  # pure-test only
    IMPORT_DEGRADED += f"persistence: {_e!r}; "
    insert_paper_trade = close_paper_trade = get_conn = None  # type: ignore

STRATEGY_ID = "BRK_V1"
IST = timezone(timedelta(minutes=330))
FILL_POLL_S = 2
FILL_TIMEOUT_S = 45


def _now() -> int:
    return int(time.time())


class BrkPosition:
    __slots__ = ("row_id", "symbol", "token", "side", "tag", "entry_px",
                 "sl_px", "tp_px", "qty", "lots", "mode", "gtt_id")

    def __init__(self, **k):
        for f in self.__slots__:
            setattr(self, f, k.get(f))


class BrkManager:
    """Injected collaborators keep this testable without a broker:
    executor  — place_buy/place_market_sell/get_order_fill/place_gtt_oco/
                cancel_gtt_verified (router-wrapped or raw)
    quote_fn(symbol) -> float|None
    """

    def __init__(self, executor=None, *, cfg_fn: Optional[Callable] = None,
                 mode_fn: Optional[Callable] = None,
                 quote_fn: Optional[Callable] = None):
        self.executor = executor
        self._cfg_fn = cfg_fn
        self._mode_fn = mode_fn
        self.quote_fn = quote_fn or (lambda s: None)
        self.pos: Optional[BrkPosition] = None
        self.day_results: Dict[str, float] = {}   # tag -> closed net (today)
        self._close_fail_n = 0        # consecutive close failures (backoff)
        self._close_next_ts = 0.0     # earliest next close attempt

    # ── config / mode ──────────────────────────────────────────────────
    def cfg(self) -> dict:
        if self._cfg_fn:
            return self._cfg_fn() or {}
        from app.config.strategy_loader import load_strategy_config
        return load_strategy_config(STRATEGY_ID) or {}

    def mode(self) -> str:
        """LIVE only when positively confirmed (degraded reads drop to
        PAPER with an alert) — resolve_execution_mode doctrine."""
        if self._mode_fn:
            return self._mode_fn()
        from app.risk.strategy_max_loss_guard import resolve_execution_mode
        m, _degraded = resolve_execution_mode(STRATEGY_ID)
        return m

    def attach_executor(self, executor) -> None:
        self.executor = executor

    def _qty(self):
        q = (self.cfg().get("quantity") or {})
        lots = int(q.get("lots") or 1)
        lot_size = int(q.get("lot_size") or 65)
        return lots, lot_size, lots * lot_size

    def _alert(self, code, msg, severity="warning"):
        write_audit_log(f"[BRK][{code}] {msg}")
        try:
            record_alert(source=STRATEGY_ID, code=code, message=msg,
                         severity=severity)
        except Exception:
            pass

    def _notify(self, fn_name, payload):
        try:
            from app.api import telegram_api
            getattr(telegram_api, fn_name)(payload)
        except Exception:
            pass

    # ── entry ──────────────────────────────────────────────────────────
    def open_trade(self, *, symbol: str, token: int, side: str, tag: str,
                   ltp: float, sl_px: float, tp_px: Optional[float]) -> bool:
        """Called by the engine on an ENTER decision. True = position open."""
        if self.pos is not None:
            self._alert("DOUBLE_ENTRY", f"entry for {symbol} refused — "
                        f"{self.pos.symbol} already open", "error")
            return False
        if insert_paper_trade is None or get_conn is None:
            # 2026-09-03 scar: a manager that cannot persist must refuse to
            # trade — an unrecorded LIVE position is invisible after any
            # restart and escapes EOD management.
            self._alert("PERSISTENCE_DOWN",
                        f"entry for {symbol} REFUSED — DB layer unavailable "
                        f"({IMPORT_DEGRADED or 'unknown'}); ZERO orders "
                        f"placed", "critical")
            return False
        lots, lot_size, qty = self._qty()
        mode = self.mode()
        if mode == "LIVE":
            return self._open_live(symbol=symbol, token=token, side=side,
                                   tag=tag, ltp=ltp, sl_px=sl_px, tp_px=tp_px,
                                   lots=lots, lot_size=lot_size, qty=qty)
        return self._open_paper(symbol=symbol, token=token, side=side,
                                tag=tag, ltp=ltp, sl_px=sl_px, tp_px=tp_px,
                                lots=lots, lot_size=lot_size, qty=qty)

    def _insert_row(self, *, mode, symbol, token, side, tag, entry_px,
                    sl_px, tp_px, lots, lot_size, qty, trade_class=None) -> str:
        pid = str(uuid.uuid4())
        rr = (round((tp_px - entry_px) / max(0.01, entry_px - sl_px), 2)
              if tp_px else 0.0)
        insert_paper_trade(
            paper_trade_id=pid, strategy_name=STRATEGY_ID, trade_mode=mode,
            symbol=symbol, token=int(token or 0), side=side,
            entry_price=float(entry_px), candle_ts=_now() - _now() % 60,
            sl_price=float(sl_px), tp_price=float(tp_px or 0.0), rr=rr,
            lots=lots, lot_size=lot_size, qty=qty,
            trade_direction="LONG", group_id=tag, trade_class=trade_class)
        return pid

    def _open_paper(self, *, symbol, token, side, tag, ltp, sl_px, tp_px,
                    lots, lot_size, qty) -> bool:
        try:
            pid = self._insert_row(mode="PAPER", symbol=symbol, token=token,
                                   side=side, tag=tag, entry_px=ltp,
                                   sl_px=sl_px, tp_px=tp_px, lots=lots,
                                   lot_size=lot_size, qty=qty)
        except Exception as e:
            self._alert("PAPER_ROW_FAIL", f"{symbol}: {e!r}", "error")
            return False
        self.pos = BrkPosition(row_id=pid, symbol=symbol, token=token,
                               side=side, tag=tag, entry_px=float(ltp),
                               sl_px=sl_px, tp_px=tp_px, qty=qty, lots=lots,
                               mode="PAPER", gtt_id=None)
        write_audit_log(f"[BRK][ENTRY][PAPER] {tag} {symbol} @ {ltp} "
                        f"sl={sl_px} tp={tp_px} qty={qty}")
        self._notify("notify_trade_entry", {
            "strategy_id": STRATEGY_ID, "mode": "PAPER", "symbol": symbol,
            "side": side, "entry_price": ltp, "quantity": qty,
            "sl": sl_px, "tp": tp_px})
        return True

    def _open_live(self, *, symbol, token, side, tag, ltp, sl_px, tp_px,
                   lots, lot_size, qty) -> bool:
        if self.executor is None:
            self._alert("NO_EXECUTOR", "LIVE entry impossible — executor "
                        "missing; day forfeited (no re-entry by design)",
                        "error")
            return False
        # fail-closed preflight (TSG_EXEC_CONTRACT doctrine).
        # 2026-09-03 scar: read the executor's BODY, not an imagined
        # signature — get_open_positions_or_none / get_gtt_status are the
        # real reconcile primitives (get_positions never existed).
        required = ("place_buy", "place_market_sell", "get_order_fill",
                    "place_gtt_oco", "cancel_gtt_verified",
                    "get_gtt_status", "get_open_positions_or_none")
        missing = [m for m in required
                   if not callable(getattr(self.executor, m, None))]
        if missing:
            self._alert("EXEC_CONTRACT", f"LIVE entry blocked — executor "
                        f"missing {missing}; ZERO orders placed", "error")
            return False
        try:
            order_id, avg, filled = self.executor.place_buy(symbol, token, qty)
        except Exception as e:
            self._alert("BUY_FAIL", f"{symbol}: {e!r}", "error")
            return False
        # bounded synchronous fill poll — a 09:30 scalp cannot wait minutes
        fill_px, ok = float(avg or 0.0), filled == qty and avg
        t0 = time.time()
        while not ok and time.time() - t0 < FILL_TIMEOUT_S:
            time.sleep(FILL_POLL_S)
            try:
                st = self.executor.get_order_fill(order_id)
            except Exception:
                continue
            status = (st or {}).get("status")
            if status == "COMPLETE":
                fill_px, ok = float(st.get("avg_price") or 0.0), True
            elif status in ("REJECTED", "CANCELLED") and (st or {}).get("found"):
                self._alert("BUY_DEAD", f"{symbol} order {status}", "error")
                return False
            # found=False → PENDING, never rejected (executor contract)
        if not ok or fill_px <= 0:
            self._alert("FILL_TIMEOUT", f"{symbol} unfilled after "
                        f"{FILL_TIMEOUT_S}s — abandoning entry", "error")
            return False
        # recompute exits off the REAL fill (ledger #1): same distances
        cfg = self.cfg()
        sl_real = round(fill_px - float(cfg.get("sl_pts") or 16), 2)
        tp_pts = float(cfg.get("tp_pts") or 0)
        tp_real = round(fill_px + tp_pts, 2) if tp_pts > 0 else None
        gtt_id = None
        try:
            gtt_id = self.executor.place_gtt_oco(
                symbol, qty, sl_real, tp_real, last_price=fill_px,
                direction="LONG")
        except Exception as e:
            self._alert("GTT_FAIL", f"{symbol}: OCO GTT failed ({e!r}) — "
                        f"engine tick exits are the only protection", "error")
        try:
            pid = self._insert_row(
                mode="LIVE", symbol=symbol, token=token, side=side, tag=tag,
                entry_px=fill_px, sl_px=sl_real, tp_px=tp_real, lots=lots,
                lot_size=lot_size, qty=qty,
                trade_class=f"GTT:{gtt_id}" if gtt_id else None)
        except Exception as e:
            self._alert("LIVE_ROW_FAIL", f"{symbol}: {e!r} — POSITION IS "
                        f"OPEN AT THE BROKER, row missing", "critical")
            pid = None
        self.pos = BrkPosition(row_id=pid, symbol=symbol, token=token,
                               side=side, tag=tag, entry_px=fill_px,
                               sl_px=sl_real, tp_px=tp_real, qty=qty,
                               lots=lots, mode="LIVE", gtt_id=gtt_id)
        write_audit_log(f"[BRK][ENTRY][LIVE] {tag} {symbol} filled @ "
                        f"{fill_px} sl={sl_real} tp={tp_real} gtt={gtt_id}")
        self._notify("notify_trade_entry", {
            "strategy_id": STRATEGY_ID, "mode": "LIVE", "symbol": symbol,
            "side": side, "entry_price": fill_px, "quantity": qty,
            "sl": sl_real, "tp": tp_real})
        return True

    # ── exit ───────────────────────────────────────────────────────────
    def close_trade(self, *, reason: str, ltp: Optional[float] = None) -> bool:
        """Engine-side exit (EOD/KILL, or tick SL/TP in PAPER / GTT-less
        LIVE).

        LIVE ORDER OF OPERATIONS (2026-09-03 incident, 464 blocked sells
        into a flat book — rules written in its blood):
          1. RECONCILE FIRST: get_gtt_status(gtt_id). "triggered" means the
             broker already exited — close the row, place NOTHING.
          2. Cancel-verified only an ARMED GTT.
          3. NEVER sell without a positive broker-side confirmation the
             position still exists (get_open_positions_or_none; None =
             couldn't read = fail closed, NO sell).
          4. Failures back off (5s·n, cap 60s) instead of hammering every
             engine tick; alert once, then every 10th.
        """
        pos = self.pos
        if pos is None:
            return False
        now = time.time()
        if now < self._close_next_ts:
            return False                       # backoff window — quiet no-op
        px = float(ltp if ltp is not None else
                   (self.quote_fn(pos.symbol) or pos.entry_px))
        if pos.mode == "LIVE" and self.executor is not None:
            # ── 1. reconcile the GTT before touching anything ──
            if pos.gtt_id:
                status = None
                try:
                    status = self.executor.get_gtt_status(pos.gtt_id)
                except Exception as e:
                    write_audit_log(f"[BRK][EXIT] gtt status read raised {e!r}")
                if status == "triggered":
                    write_audit_log(f"[BRK][EXIT] GTT {pos.gtt_id} already "
                                    f"FIRED at the broker — closing row "
                                    f"only, no orders")
                    self._close_row(pos, px, reason)
                    self._close_ok()
                    return True
                if status is None:
                    # Broker state unreadable — selling blind risks a naked
                    # short (or double exit). Fail closed, retry later.
                    self._close_failed("GTT_STATUS_UNREADABLE",
                                       f"{pos.symbol}: GTT {pos.gtt_id} "
                                       f"state unknown — NO orders placed, "
                                       f"will retry")
                    return False
                try:
                    cancelled = self.executor.cancel_gtt_verified(pos.gtt_id)
                except Exception as e:
                    cancelled = False
                    write_audit_log(f"[BRK][EXIT] cancel_gtt raised {e!r}")
                if not cancelled and self._broker_flat(pos.symbol):
                    write_audit_log(f"[BRK][EXIT] GTT won the race on "
                                    f"{pos.symbol} — closing row only")
                    self._close_row(pos, px, reason)
                    self._close_ok()
                    return True
                if not cancelled:
                    self._alert("GTT_CANCEL_FAIL",
                                f"{pos.symbol}: GTT {pos.gtt_id} not "
                                f"verifiably cancelled AND position not "
                                f"confirmed flat — selling (orphan-GTT "
                                f"risk, check broker)", "critical")
            # ── 3. UNCONDITIONAL flat gate before any sell ──
            if self._broker_flat(pos.symbol):
                write_audit_log(f"[BRK][EXIT] broker already flat on "
                                f"{pos.symbol} — closing row only, no sell")
                self._close_row(pos, px, reason)
                self._close_ok()
                return True
            try:
                sell_id = self.executor.place_market_sell(pos.symbol, pos.qty)
                t0 = time.time()
                while time.time() - t0 < FILL_TIMEOUT_S:
                    st = {}
                    try:
                        st = self.executor.get_order_fill(sell_id) or {}
                    except Exception:
                        pass
                    if st.get("status") == "COMPLETE":
                        px = float(st.get("avg_price") or px)
                        break
                    time.sleep(FILL_POLL_S)
            except Exception as e:
                self._close_failed("SELL_FAIL", f"{pos.symbol}: {e!r} — "
                                   f"POSITION MAY STILL BE OPEN")
                return False
        self._close_row(pos, px, reason)
        self._close_ok()
        return True

    def _close_ok(self) -> None:
        self._close_fail_n = 0
        self._close_next_ts = 0.0

    def _close_failed(self, code: str, msg: str) -> None:
        self._close_fail_n += 1
        self._close_next_ts = time.time() + min(60, 5 * self._close_fail_n)
        if self._close_fail_n == 1 or self._close_fail_n % 10 == 0:
            self._alert(code, f"{msg} (attempt {self._close_fail_n}, "
                        f"backing off)", "critical")
        else:
            write_audit_log(f"[BRK][{code}] attempt {self._close_fail_n} "
                            f"(suppressed alert)")

    def _broker_flat(self, symbol: str) -> bool:
        """True ONLY on a positive broker read showing no holding.
        get_open_positions_or_none is the STRICT primitive: None means the
        read failed → NOT flat (fail closed, never sell blind)."""
        try:
            positions = self.executor.get_open_positions_or_none()
        except Exception:
            return False
        if positions is None:
            return False   # unreadable → fail closed
        for p in positions:
            if p.get("tradingsymbol") == symbol                     and int(p.get("quantity") or 0) != 0:
                return False
        return True

    def _close_row(self, pos: BrkPosition, px: float, reason: str) -> None:
        if pos.row_id:
            try:
                close_paper_trade(paper_trade_id=pos.row_id,
                                  exit_price=float(px), exit_reason=reason,
                                  trade_direction="LONG")
            except Exception as e:
                self._alert("CLOSE_ROW_FAIL", f"{pos.row_id}: {e!r}", "error")
        net = (px - pos.entry_px) * pos.qty
        self.day_results[pos.tag] = self.day_results.get(pos.tag, 0.0) + net
        write_audit_log(f"[BRK][EXIT][{pos.mode}] {pos.tag} {pos.symbol} "
                        f"@ {px} reason={reason} gross={net:.0f}")
        fn = {"SL": "notify_sl_exit", "TP": "notify_tp_exit"}.get(
            reason, "notify_manual_exit")
        self._notify(fn, {"strategy_id": STRATEGY_ID, "mode": pos.mode,
                          "symbol": pos.symbol, "entry_price": pos.entry_px,
                          "exit_price": px, "quantity": pos.qty,
                          "pnl": round(net, 2), "exit_reason": reason})
        self.pos = None

    # ── S2 gate inputs ─────────────────────────────────────────────────
    def s1_open(self) -> bool:
        return self.pos is not None and self.pos.tag == "BRK"

    def s1_result(self) -> Optional[float]:
        """Closed net of today's morning session, None if it never traded."""
        if "BRK" in self.day_results:
            return self.day_results["BRK"]
        return None

    # ── restart / EOD / kill ───────────────────────────────────────────
    def resume_from_db(self) -> None:
        """Rebuild the open position (and today's closed results for the S2
        gate) from paper_trades. Mandatory restart-smoke leg."""
        if get_conn is None:
            return
        day0 = int(datetime.now(IST).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
        try:
            conn = get_conn()
            rows = conn.execute(
                "SELECT paper_trade_id, symbol, token, side, trade_mode, "
                "entry_price, sl_price, tp_price, qty, lots, group_id, "
                "trade_class, state, exit_price, candle_ts "
                "FROM paper_trades WHERE strategy_name = ? AND candle_ts >= ?",
                (STRATEGY_ID, day0)).fetchall()
        except Exception as e:
            write_audit_log(f"[BRK][RESUME] read failed: {e!r}")
            return
        for r in rows:
            d = dict(r)
            tag = d.get("group_id") or "BRK"
            if d["state"] == "OPEN":
                if self.pos is not None:
                    self._alert("RESUME_DOUBLE", "two OPEN BRK rows — "
                                "resuming the first, check the second",
                                "error")
                    continue
                gtt = None
                tc = d.get("trade_class") or ""
                if tc.startswith("GTT:"):
                    gtt = tc[4:] or None
                self.pos = BrkPosition(
                    row_id=d["paper_trade_id"], symbol=d["symbol"],
                    token=d["token"], side=d["side"], tag=tag,
                    entry_px=float(d["entry_price"]),
                    sl_px=float(d["sl_price"]),
                    tp_px=float(d["tp_price"]) or None,
                    qty=int(d["qty"]), lots=int(d["lots"]),
                    mode=d["trade_mode"], gtt_id=gtt)
                write_audit_log(f"[BRK][RESUME] open {tag} {d['symbol']} "
                                f"@ {d['entry_price']} mode={d['trade_mode']}"
                                f" gtt={gtt}")
            elif d["state"] == "CLOSED" and d.get("exit_price") is not None:
                net = (float(d["exit_price"]) - float(d["entry_price"])) \
                    * int(d["qty"])
                self.day_results[tag] = self.day_results.get(tag, 0.0) + net

    def verify_exit_contract(self) -> None:
        """2026-09-03: a resumed LIVE position can hold a contract the
        current executor can't honor. Alert-only (position already exists)."""
        if self.pos is None or self.pos.mode != "LIVE":
            return
        if self.executor is None:
            self._alert("EXEC_CONTRACT", "resumed LIVE position with NO "
                        "executor — engine-side exits impossible until "
                        "re-attach; GTT remains the only protection",
                        "critical")
            return
        required = ("place_market_sell", "get_order_fill",
                    "cancel_gtt_verified", "get_gtt_status",
                    "get_open_positions_or_none")
        missing = [m for m in required
                   if not callable(getattr(self.executor, m, None))]
        if missing:
            self._alert("EXEC_CONTRACT", f"resumed LIVE position but "
                        f"executor lacks {missing} — engine-side exits "
                        f"will fail; manage manually", "critical")

    def eod_squareoff(self) -> int:
        if self.pos is None:
            return 0
        return 1 if self.close_trade(reason="EOD") else 0

    def kill_all(self) -> int:
        """LD8: cancel GTT verified → flatten → verified-flat is the close
        path itself; a failed close stays open and screams."""
        if self.pos is None:
            return 0
        return 1 if self.close_trade(reason="KILL") else 0