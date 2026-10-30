# backend/app/engine/emc/emc_manager.py
#
# ── EMC_V1 MANAGER ── orders + persistence for "Ember". Fence: EMC_V1_LIVE_20261011
#
# Decisions live in EmcDayCore (the backtest's own object, driven by
# emc_engine); this class owns fills, paper rows, notifications and the kill
# path. Cloned from OrbManager (the fleet's newest single-long-option donor,
# day-1 scars included):
#   * fallbacks split per concern; any degraded persistence import REFUSES
#     new entries (never trade from memory);
#   * LIVE exits are reconcile-first (never sell without a positive holdings
#     read; unreadable broker → back off, never guess);
#   * generic paper_trades storage (PAPER / LIVE rows; PAPER_LIVE twins are
#     booked by the shadow book inside paper_trades_repo).
#
# NO GTT LAYER (ORB / VET doctrine): SL, TP and EOD are ENGINE exits taken
# at market while the app runs. Divergence ledger vs the backtest:
#   1. backtest books SL/TP AT the level (open if gapped); paper books at the
#      first polled LTP through the level (realistic — the paper phase exists
#      to MEASURE slippage); LIVE takes the market fill.
#   2. backtest enters at the next minute's open; paper/live enter at the
#      quote/fill taken right after the decision minute closes.
#   3. if the app is down there is no stop at the broker.

from __future__ import annotations

import time
import uuid
from typing import Callable, Optional

try:
    from app.risk import execution_modes as _xm
except Exception:                                          # standalone tests
    class _xm:                                             # type: ignore
        @staticmethod
        def normalize(raw, default="PAPER"):
            m = str(raw or default).strip().upper().replace("+", "_").replace(" ", "_")
            return m if m in ("OFF", "PAPER", "LIVE", "PAPER_LIVE") else default

STRATEGY_ID = "EMC_V1"
FILL_TIMEOUT_S = 20
FILL_POLL_S = 1.0

_DEGRADED = []

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    _DEGRADED.append("audit_logger")
    def write_audit_log(msg):                              # type: ignore
        print(msg)

try:
    from app.event_bus.inapp_events import record_alert
except ImportError:
    _DEGRADED.append("inapp_events")
    def record_alert(*a, **k):                             # type: ignore
        print("ALERT", a, k)

try:
    from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade, get_conn
except ImportError:
    _DEGRADED.append("paper_trades_repo")
    insert_paper_trade = close_paper_trade = get_conn = None  # type: ignore

try:
    from app.config.strategy_loader import load_strategy_config
except ImportError:
    _DEGRADED.append("strategy_loader")
    load_strategy_config = None  # type: ignore

try:
    from app.backtest.charges.charges_model import charges_for_long_trade
except ImportError:
    _DEGRADED.append("charges_model")
    charges_for_long_trade = None  # type: ignore


def levels_for(fill_px: float, sl_pts: float, tp_pts: float):
    """The core's own SL/TP arithmetic (EmcDayCore._try_enter) — one place."""
    sl = round(fill_px - sl_pts, 2) if sl_pts > 0 else None
    tp = round(fill_px + tp_pts, 2) if tp_pts > 0 else None
    return sl, tp


class EmcPosition:
    def __init__(self, **k):
        self.__dict__.update(k)


class EmcManager:
    """One instance per process. The engine calls open_trade / close_trade /
    check_exits; state routes read state(); kill_switch calls kill_all()."""

    def __init__(self, executor=None, *, cfg_fn: Optional[Callable] = None, notifier=None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.executor = executor
        self.cfg_fn = cfg_fn
        self.notifier = notifier
        self.clock = clock
        self.sleep = sleep
        self.pos: Optional[EmcPosition] = None
        self.day_lots: Optional[int] = None
        self._allow_degraded = False          # tests only — never set live
        self._close_backoff_n = 0
        self._close_backoff_until = 0.0
        # engine hook: called with (minute_epoch, exit_px, reason) for every
        # close the MANAGER originates (SL/TP/EOD/KILL/BROKER_FLAT) so the
        # day core counts it toward the losing-trade cap.
        self.on_closed: Optional[Callable[[int, float, str], None]] = None
        self.day_stats = {"entries": 0, "exits": {}, "refused": None}
        self.last_closed_qty: Optional[int] = None

    # ── config / mode ──
    def cfg(self) -> dict:
        if self.cfg_fn:
            return self.cfg_fn() or {}
        if load_strategy_config is None:
            return {}
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception:
            return {}

    def mode(self) -> str:
        m = _xm.normalize(self.cfg().get("trade_execution_mode", "PAPER"))
        return "LIVE" if m == "PAPER_LIVE" else m          # twin booked by paper_trades_repo

    def attach_executor(self, executor) -> None:
        self.executor = executor

    def qty(self):
        cfg = self.cfg()
        lots = int(self.day_lots or cfg.get("lots") or 1)
        lot_size = int(cfg.get("lot_size") or 0) or 65
        return lots, lot_size, lots * lot_size

    def net_of(self, entry: float, exit_px: float, qty: Optional[int] = None) -> float:
        q = int(qty if qty is not None else (self.pos.qty if self.pos else self.qty()[2]))
        ch = 0.0
        if charges_for_long_trade is not None:
            ch = charges_for_long_trade(entry_price=float(entry), exit_price=float(exit_px), qty=q).total_charges
        return round((float(exit_px) - float(entry)) * q - ch, 2)

    # ── OFF_QUIET_20261030 ── an OFF strategy with no position has nothing to
    # act on: its alerts stay in the audit log. Position bookkeeping alerts
    # are always raised. Alerts carry the real mode (record_alert defaults
    # to "live", so every EMC alert used to show a LIVE tag).
    _LOUD_WHEN_OFF = frozenset({
        "STALE_ROW", "STALE_FAIL", "SELL_FAIL", "CLOSE_ROW_FAIL", "BROKER_UNREADABLE",
        "BROKER_FLAT", "DOUBLE_ENTRY", "PERSIST_DOWN", "ROW_FAIL",
    })

    def _alert(self, code, msg, severity="warning"):
        write_audit_log(f"[EMC][{code}] {msg}")
        try:
            m = str(self.mode() or "LIVE").upper()
        except Exception:
            m = "LIVE"                                  # unknown → loud
        if m == "OFF" and getattr(self, "pos", None) is None and code not in self._LOUD_WHEN_OFF:
            write_audit_log(f"[EMC][{code}][OFF_QUIET] strategy OFF and flat — kept out of the bell")
            return
        try:
            record_alert(code, msg, severity=severity, strategy_id=STRATEGY_ID,
                         mode="live" if m in ("LIVE", "PAPER_LIVE") else m.lower())
        except Exception:
            pass

    def _notify(self, fn_name, payload):
        target = self.notifier
        if target is None:
            try:
                from app.api import telegram_api as target
            except Exception:
                return
        try:
            getattr(target, fn_name)(payload)
        except Exception as e:
            write_audit_log(f"[EMC][NOTIFY_FAIL] {fn_name}: {e!r}")

    # ── entry ──
    def open_trade(self, *, symbol: str, token: int, side: str, ltp: Optional[float],
                   entry_spot: Optional[float], decision_ts: int, sl_pts: float, tp_pts: float,
                   note: str = "") -> Optional[float]:
        """Buy one position. Returns the fill price, or None when no position
        was opened (every refusal is alerted)."""
        if _DEGRADED and not self._allow_degraded:
            self._alert("PERSIST_DOWN", f"degraded imports {_DEGRADED} — REFUSING to trade "
                        "(never trade from memory)", "critical")
            return None
        if self.pos is not None:
            self._alert("DOUBLE_ENTRY", f"{symbol} refused — a position is already open", "error")
            return None
        mode = self.mode()
        if mode == "OFF":
            write_audit_log(f"[EMC][MODE_OFF] {side} {symbol} entry ignored — strategy OFF")
            return None
        lots, lot_size, qty = self.qty()
        if mode == "LIVE":
            if self.executor is None:
                self._alert("NO_EXECUTOR", "LIVE entry impossible — executor missing; entry forfeited", "error")
                return None
            required = ("place_buy", "place_market_sell", "get_order_fill", "get_open_positions_or_none")
            missing = [m for m in required if not callable(getattr(self.executor, m, None))]
            if missing:
                self._alert("EXEC_CONTRACT", f"LIVE blocked — executor missing {missing}; ZERO orders placed",
                            "error")
                return None
            try:
                order_id, avg, filled = self.executor.place_buy(symbol, int(token or 0), qty)
            except Exception as e:
                self._alert("BUY_FAIL", f"{symbol}: {e!r}", "error")
                return None
            fill_px, ok = float(avg or 0.0), bool(filled == qty and avg)
            t0 = self.clock()
            while not ok and self.clock() - t0 < FILL_TIMEOUT_S:
                self.sleep(FILL_POLL_S)
                try:
                    st = self.executor.get_order_fill(order_id)
                except Exception:
                    continue
                status = (st or {}).get("status")
                if status == "COMPLETE":
                    fill_px, ok = float(st.get("avg_price") or 0.0), True
                elif status in ("REJECTED", "CANCELLED") and (st or {}).get("found"):
                    self._alert("BUY_DEAD", f"{symbol} order {status}", "error")
                    return None
            if not ok or fill_px <= 0:
                self._alert("FILL_TIMEOUT", f"{symbol} unfilled after {FILL_TIMEOUT_S}s — abandoning "
                            "(check the broker: a late fill would be an untracked position)", "critical")
                return None
        else:
            if not ltp or float(ltp) <= 0:
                self._alert("NO_QUOTE", f"{symbol}: no LTP for the paper fill — entry forfeited", "error")
                return None
            fill_px = float(ltp)
        fill_px = round(fill_px, 2)
        sl, tp = levels_for(fill_px, sl_pts, tp_pts)
        entry_ts = int(self.clock())
        pid = None
        if insert_paper_trade is not None:
            try:
                pid = str(uuid.uuid4())
                insert_paper_trade(
                    paper_trade_id=pid, strategy_name=STRATEGY_ID, trade_mode=mode,
                    symbol=symbol, token=int(token or 0), side=side, entry_price=fill_px,
                    candle_ts=int(decision_ts), sl_price=float(sl or 0.0), tp_price=float(tp or 0.0),
                    rr=round(tp_pts / sl_pts, 2) if sl_pts > 0 and tp_pts > 0 else 0.0,
                    lots=lots, lot_size=lot_size, qty=qty, trade_direction="LONG", group_id="EMC",
                    trade_class=None)
            except Exception as e:
                self._alert("ROW_FAIL", f"{symbol}: {e!r}"
                            + (" — POSITION OPEN AT BROKER, row missing" if mode == "LIVE" else ""),
                            "critical" if mode == "LIVE" else "error")
                pid = None
        self.pos = EmcPosition(row_id=pid, symbol=symbol, token=int(token or 0), side=side, entry_px=fill_px,
                               qty=qty, lots=lots, mode=mode, sl=sl, tp=tp, entry_ts=entry_ts,
                               decision_ts=int(decision_ts), entry_spot=entry_spot, mark=fill_px)
        self.day_stats["entries"] += 1
        write_audit_log(f"[EMC][ENTRY][{mode}] {side} {symbol} @ {fill_px} SL={sl} TP={tp} qty={qty} {note}")
        self._notify("notify_trade_entry", {
            "strategy_id": STRATEGY_ID, "mode": mode, "symbol": symbol, "side": side,
            "entry_price": fill_px, "quantity": qty, "sl": sl, "tp": tp,
            "note": f"Ember {side} · NIFTY 5m EMA cross + option EMA aligned · SL −{sl_pts:g} / TP +{tp_pts:g} "
                    f"premium pts · {note}".strip()})
        return fill_px

    # ── intraminute exits ──
    def check_exits(self, ltp: Optional[float]) -> Optional[str]:
        """Called on every poll with the held option's LTP. Stop before target
        (backtest order). Returns the exit reason when the position closed."""
        pos = self.pos
        if pos is None or not ltp or float(ltp) <= 0:
            return None
        ltp = float(ltp)
        pos.mark = ltp
        reason = None
        if pos.sl is not None and ltp <= pos.sl:
            reason = "SL"
        elif pos.tp is not None and ltp >= pos.tp:
            reason = "TP"
        if reason is None:
            return None
        return reason if self.close_trade(reason=reason, ltp=ltp) is not None else None

    # ── exits ──
    def close_trade(self, *, reason: str, ltp: Optional[float] = None, notify_core: bool = True) -> Optional[float]:
        """Close the open position. Returns the exit price, or None when
        nothing was closed (no position, or LIVE backoff — retried later)."""
        pos = self.pos
        if pos is None:
            return None
        px = float(ltp if ltp else (pos.mark or pos.entry_px))
        if pos.mode == "LIVE" and self.executor is not None:
            if self.clock() < self._close_backoff_until:
                return None
            probe = getattr(self.executor, "get_open_positions_or_none", None)
            if callable(probe):
                try:
                    positions = probe()
                except Exception:
                    positions = None
                if positions is None:
                    self._close_backoff_n += 1
                    self._close_backoff_until = self.clock() + min(60.0, 5.0 * self._close_backoff_n)
                    self._alert("BROKER_UNREADABLE", f"{pos.symbol}: holdings read failed — NO order placed, "
                                f"backoff {min(60, 5 * self._close_backoff_n)}s", "error")
                    return None
                holding = any((p.get("tradingsymbol") or p.get("symbol")) == pos.symbol
                              and int(p.get("quantity") or p.get("qty") or 0) > 0 for p in positions)
                if not holding:
                    self._alert("BROKER_FLAT", f"{pos.symbol}: broker shows no holding — closing the ROW only, "
                                "placing NOTHING (reconcile-first)", "warning")
                    return self._finish_close(pos, px, "BROKER_FLAT" if reason != "XOVER" else reason,
                                              notify_core)
            self._close_backoff_n = 0
            try:
                order_id = self.executor.place_market_sell(pos.symbol, pos.qty)
                t0 = self.clock()
                while self.clock() - t0 < FILL_TIMEOUT_S:
                    try:
                        st = self.executor.get_order_fill(order_id)
                    except Exception:
                        self.sleep(FILL_POLL_S)
                        continue
                    if (st or {}).get("status") == "COMPLETE":
                        px = float(st.get("avg_price") or px)
                        break
                    self.sleep(FILL_POLL_S)
            except Exception as e:
                self._alert("SELL_FAIL", f"{pos.symbol}: {e!r} — POSITION MAY BE OPEN AT THE BROKER", "critical")
        return self._finish_close(pos, px, reason, notify_core)

    def _finish_close(self, pos, px: float, reason: str, notify_core: bool) -> float:
        px = round(float(px), 2)
        if close_paper_trade is not None and pos.row_id:
            try:
                close_paper_trade(paper_trade_id=pos.row_id, exit_price=px, exit_reason=reason)
            except Exception as e:
                self._alert("CLOSE_ROW_FAIL", f"{pos.symbol}: {e!r}", "error")
        self.day_stats["exits"][reason] = self.day_stats["exits"].get(reason, 0) + 1
        gross = (px - pos.entry_px) * pos.qty
        write_audit_log(f"[EMC][EXIT][{pos.mode}] {reason} {pos.symbol} @ {px} gross={gross:,.0f}")
        fn = {"SL": "notify_sl_exit", "TP": "notify_tp_exit"}.get(reason, "notify_manual_exit")
        self._notify(fn, {"strategy_id": STRATEGY_ID, "mode": pos.mode, "symbol": pos.symbol, "side": pos.side,
                          "entry_price": pos.entry_px, "exit_price": px, "quantity": pos.qty,
                          "pnl": round(gross, 2), "exit_reason": reason, "reason": reason})
        self.last_closed_qty = pos.qty
        self.pos = None
        if notify_core and self.on_closed is not None:
            try:
                self.on_closed(int(self.clock()), px, reason)
            except Exception as e:
                write_audit_log(f"[EMC][CORE_CLOSE_HOOK_FAIL] {e!r}")
        return px

    # ── restart ──
    def today_rows(self, day_start: int, rows=None) -> list:
        """Today's EMC rows (twins excluded), oldest first. rows= for tests."""
        if rows is None:
            if get_conn is None:
                return []
            cur = get_conn().execute(
                "SELECT paper_trade_id, symbol, token, side, entry_price, exit_price, exit_reason, qty, lots,"
                " trade_mode, sl_price, tp_price, candle_ts FROM paper_trades WHERE strategy_name=?"
                " AND candle_ts >= ? AND paper_trade_id NOT LIKE 'SHD:%' ORDER BY candle_ts",
                (STRATEGY_ID, int(day_start)))
            rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
        return [r for r in rows if not str(r.get("paper_trade_id") or "").startswith("SHD:")]

    def open_rows_any_day(self) -> list:
        if get_conn is None:
            return []
        cur = get_conn().execute(
            "SELECT paper_trade_id, symbol, token, side, entry_price, qty, lots, trade_mode, sl_price, tp_price,"
            " candle_ts FROM paper_trades WHERE strategy_name=? AND exit_price IS NULL"
            " AND paper_trade_id NOT LIKE 'SHD:%' ORDER BY candle_ts", (STRATEGY_ID,))
        return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]

    def resume_row(self, r: dict) -> None:
        self.pos = EmcPosition(
            row_id=r.get("paper_trade_id"), symbol=r["symbol"], token=int(r.get("token") or 0), side=r["side"],
            entry_px=float(r["entry_price"]), qty=int(r["qty"]), lots=int(r.get("lots") or 1),
            mode=str(r.get("trade_mode") or "PAPER").upper(),
            sl=(float(r["sl_price"]) if r.get("sl_price") else None),
            tp=(float(r["tp_price"]) if r.get("tp_price") else None),
            entry_ts=int(r.get("candle_ts") or 0), decision_ts=int(r.get("candle_ts") or 0),
            entry_spot=None, mark=float(r["entry_price"]))
        write_audit_log(f"[EMC][RESUME] open {self.pos.side} {self.pos.symbol} @ {self.pos.entry_px} "
                        f"({self.pos.mode}) SL={self.pos.sl} TP={self.pos.tp}")

    def mark_stale(self, r: dict, why: str) -> None:
        """A prior-day OPEN row (intraday strategy → the app died before its
        own EOD). Closed at its entry price with reason STALE — never adopted,
        never traded. LIVE: the broker's MIS auto-square has normally flattened
        it; the critical alert says to verify."""
        rid = r.get("paper_trade_id")
        if close_paper_trade is not None and rid:
            try:
                close_paper_trade(paper_trade_id=rid, exit_price=float(r["entry_price"]), exit_reason="STALE")
            except Exception as e:
                self._alert("STALE_FAIL", f"{rid}: {e!r}", "error")
        self._alert("STALE_ROW", f"{r.get('symbol')} ({r.get('trade_mode')}) open since a prior day — closed as "
                    f"STALE at entry; {why}. VERIFY THE BROKER BOOK.", "critical")

    def eod_squareoff(self, ltp: Optional[float] = None) -> int:
        return 1 if self.close_trade(reason="EOD", ltp=ltp) is not None else 0

    def kill_all(self) -> int:
        n = 1 if self.close_trade(reason="KILL", ltp=None) is not None else 0
        write_audit_log(f"[EMC][KILL] flattened {n} position(s)")
        return n

    def kill_adapter(self) -> dict:
        """kill_switch contract: {"closed", "remaining", "detail"}."""
        n = self.kill_all()
        return {"closed": n, "remaining": 0 if self.pos is None else 1,
                "detail": [f"EMC_V1 closed {n}"] + ([f"still open: {self.pos.symbol}"] if self.pos else [])}

    def state(self) -> dict:
        p = self.pos
        return {"strategy": STRATEGY_ID, "mode": self.mode(),
                "position": None if p is None else {
                    "symbol": p.symbol, "side": p.side, "mode": p.mode, "entry": p.entry_px, "qty": p.qty,
                    "lots": p.lots, "sl": p.sl, "tp": p.tp, "entry_ts": p.entry_ts, "mark": p.mark,
                    "row_id": p.row_id},
                "day": dict(self.day_stats)}
