# backend/app/engine/lvb/lvb_manager.py
#
# ── LVB_V1_20261023 ── orders + persistence for "Tripwire".
#
# Cloned from EmcManager (the fleet's newest single-long-option donor) and
# widened from ONE position to MANY (one per condition, keyed by condition
# id). Same day-1 doctrines:
#   * any degraded persistence import REFUSES new entries (never trade from
#     memory);
#   * LIVE exits are reconcile-first: never sell without a positive holdings
#     read; unreadable broker → back off and retry, never guess;
#   * generic paper_trades rows (PAPER / LIVE; PAPER_LIVE twins are booked
#     by the shadow book inside paper_trades_repo).
#   * NO GTT / broker-side stop: SL, TP and EOD are ENGINE exits while the
#     app runs (ORB / VET / EMC doctrine). If the app is down there is no
#     stop at the broker — the LIVE EOD backstop job and MIS do not cover a
#     NRML long either; this is in the Settings/page copy.
#
# Thread safety: the engine thread (polls) and the API threads (manual
# square-off, kill switch) both close positions → one RLock around every
# open/close.

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from app.engine.lvb.lvb_core import (STRATEGY_ID, INSTRUMENTS, exit_reason, hm_to_min, sl_price,
                                     day_start_epoch_of)

try:
    from app.risk import execution_modes as _xm
except Exception:                                          # standalone tests
    class _xm:                                             # type: ignore
        @staticmethod
        def normalize(raw, default="PAPER"):
            m = str(raw or default).strip().upper().replace("+", "_").replace(" ", "_")
            return m if m in ("OFF", "PAPER", "LIVE", "PAPER_LIVE") else default

FILL_TIMEOUT_S = 20
FILL_POLL_S = 1.0
ZERODHA_EXECUTOR = "ZerodhaOrderExecutor"     # the only executor with BFO (SENSEX) routing

_DEGRADED: List[str] = []

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
    from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade
except ImportError:
    _DEGRADED.append("paper_trades_repo")
    insert_paper_trade = close_paper_trade = None          # type: ignore

try:
    from app.config.strategy_loader import load_strategy_config
except ImportError:
    _DEGRADED.append("strategy_loader")
    load_strategy_config = None                            # type: ignore

try:
    from app.backtest.charges.charges_model import charges_for_long_trade
except ImportError:
    charges_for_long_trade = None                          # type: ignore   # net shown gross-only


class LvbPosition:
    def __init__(self, **k):
        self.__dict__.update(k)

    def view(self) -> dict:
        return {k: getattr(self, k, None) for k in (
            "cid", "row_id", "instrument", "symbol", "exchange", "side", "mode", "entry_px", "qty", "lots",
            "sl", "tp_spot", "eod", "entry_ts", "entry_spot", "mark", "mark_spot")}


class LvbManager:
    def __init__(self, repo, executor=None, *, cfg_fn: Optional[Callable] = None, notifier=None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.repo = repo
        self.executor = executor
        self.cfg_fn = cfg_fn
        self.notifier = notifier
        self.clock = clock
        self.sleep = sleep
        self.pos: Dict[int, LvbPosition] = {}
        self.lock = threading.RLock()
        self._allow_degraded = False              # tests only — never set live
        self._backoff: Dict[int, tuple] = {}      # cid -> (n, until)

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

    def raw_mode(self) -> str:
        return _xm.normalize(self.cfg().get("trade_execution_mode", "PAPER"))

    def mode(self) -> str:
        m = self.raw_mode()
        return "LIVE" if m == "PAPER_LIVE" else m          # twin booked by paper_trades_repo

    def attach_executor(self, executor) -> None:
        self.executor = executor

    # ── helpers ──
    def net_of(self, entry: float, exit_px: float, qty: int) -> float:
        ch = 0.0
        if charges_for_long_trade is not None:
            try:
                ch = charges_for_long_trade(entry_price=float(entry), exit_price=float(exit_px),
                                            qty=int(qty)).total_charges
            except Exception:
                ch = 0.0
        return round((float(exit_px) - float(entry)) * int(qty) - ch, 2)

    def _alert(self, code, msg, severity="warning", mode=None):
        write_audit_log(f"[LVB][{code}] {msg}")
        try:
            record_alert(code, msg, severity=severity, strategy_id=STRATEGY_ID,
                         mode=str(mode or self.mode() or "paper").lower())
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
            write_audit_log(f"[LVB][NOTIFY_FAIL] {fn_name}: {e!r}")

    # ── entry ──
    def open_position(self, cond: dict, pick: dict, *, side: str, trigger_bar_ts: int, trigger_close: float,
                      tp_spot: Optional[float], entry_spot: Optional[float]) -> Optional[float]:
        """Buy the picked contract for condition `cond`. Returns the fill
        price, or None when nothing was opened (every refusal is alerted and
        noted on the condition; it stays ARMED)."""
        cid = int(cond["id"])
        with self.lock:
            if _DEGRADED and not self._allow_degraded:
                self._alert("PERSIST_DOWN", f"degraded imports {_DEGRADED} — REFUSING to trade", "critical")
                return None
            if cid in self.pos:
                self._alert("DOUBLE_ENTRY", f"#{cid} refused — it already holds a position", "error")
                return None
            mode = self.mode()
            if mode == "OFF":
                self.repo.note(cid, f"{side} breakout at {trigger_close:g} ignored — strategy is OFF")
                write_audit_log(f"[LVB][MODE_OFF] #{cid} {side} {pick['sym']} entry ignored")
                return None
            lot_size = int(pick.get("lot_size") or 0)
            if lot_size <= 0:
                self._refuse(cid, "NO_LOT_SIZE", f"{pick['sym']}: lot size unknown — entry forfeited")
                return None
            lots = int(cond["lots"])
            qty = lots * lot_size
            sym, token, exch = pick["sym"], int(pick.get("token") or 0), pick.get("exchange") or "NFO"
            if mode == "LIVE":
                fill_px = self._live_buy(cid, sym, token, qty, exch)
                if fill_px is None:
                    return None
            else:
                ltp = float(pick.get("ltp") or 0)
                if ltp <= 0:
                    self._refuse(cid, "NO_QUOTE", f"{sym}: no LTP for the paper fill — entry forfeited")
                    return None
                fill_px = ltp
            fill_px = round(float(fill_px), 2)
            sl = sl_price(fill_px, float(cond["sl_pct"]))
            entry_ts = int(self.clock())
            pid = None
            if insert_paper_trade is not None:
                try:
                    pid = str(uuid.uuid4())
                    insert_paper_trade(
                        paper_trade_id=pid, strategy_name=STRATEGY_ID, trade_mode=mode, symbol=sym, token=token,
                        side=side, entry_price=fill_px, candle_ts=int(trigger_bar_ts), sl_price=float(sl),
                        tp_price=0.0,        # the target is on SPOT (lvb_conditions.tp_spot) — never an option price
                        rr=0.0, lots=lots, lot_size=lot_size, qty=qty, trade_direction="LONG",
                        group_id=f"LVB:{cid}", trade_class=None)
                except Exception as e:
                    self._alert("ROW_FAIL", f"#{cid} {sym}: {e!r}"
                                + (" — POSITION OPEN AT BROKER, paper_trades row missing" if mode == "LIVE" else ""),
                                "critical" if mode == "LIVE" else "error")
                    pid = None
            note = f"{side} on the {_hm(trigger_bar_ts + 300)} close {trigger_close:g}"
            ok = self.repo.mark_triggered(cid, {
                "side": side, "trigger_bar_ts": int(trigger_bar_ts), "trigger_close": float(trigger_close),
                "mode": mode, "symbol": sym, "token": token, "exchange": exch, "expiry": pick.get("expiry"),
                "strike": pick.get("strike"), "lot_size": lot_size, "qty": qty, "entry_ts": entry_ts,
                "entry_px": fill_px, "entry_spot": entry_spot, "sl_px": sl, "tp_spot": tp_spot,
                "paper_trade_id": pid, "note": note})
            if not ok:
                self._alert("COND_ROW_FAIL", f"#{cid} could not be marked TRIGGERED (no longer ARMED?) — "
                            f"{sym} position is tracked in memory only; square it off manually",
                            "critical" if mode == "LIVE" else "error")
            self.pos[cid] = LvbPosition(
                cid=cid, row_id=pid, instrument=cond["instrument"], symbol=sym, token=token, exchange=exch,
                side=side, mode=mode, entry_px=fill_px, qty=qty, lots=lots, sl=sl, tp_spot=tp_spot,
                eod=cond["eod"], eod_min=hm_to_min(cond["eod"]), entry_ts=entry_ts, entry_spot=entry_spot,
                mark=fill_px, mark_spot=entry_spot)
            write_audit_log(f"[LVB][ENTRY][{mode}] #{cid} {side} {sym} @ {fill_px} qty={qty} SL={sl} "
                            f"TPspot={tp_spot} {note}")
            self._notify("notify_trade_entry", {
                "strategy_id": STRATEGY_ID, "mode": mode, "symbol": sym, "side": side, "entry_price": fill_px,
                "quantity": qty, "sl": sl, "tp": None,
                "note": (f"Tripwire #{cid} · {cond['instrument']} 5m close {trigger_close:g} "
                         f"{'above' if side == 'CE' else 'below'} {cond['upper'] if side == 'CE' else cond['lower']:g}"
                         f" · SL {cond['sl_pct']:g}% (₹{sl:g})"
                         + (f" · TP spot {tp_spot:g}" if tp_spot is not None else " · no target")
                         + f" · EOD {cond['eod']}")})
            return fill_px

    def _refuse(self, cid: int, code: str, msg: str, severity: str = "error"):
        self._alert(code, f"#{cid} {msg}", severity)
        try:
            self.repo.note(cid, f"{code}: {msg} — still armed")
        except Exception:
            pass

    def _live_buy(self, cid: int, sym: str, token: int, qty: int, exch: str) -> Optional[float]:
        ex = self.executor
        if ex is None:
            self._refuse(cid, "NO_EXECUTOR", "LIVE entry impossible — executor missing; entry forfeited")
            return None
        required = ("place_buy", "place_market_sell", "get_order_fill", "get_open_positions_or_none")
        missing = [m for m in required if not callable(getattr(ex, m, None))]
        if missing:
            self._refuse(cid, "EXEC_CONTRACT", f"LIVE blocked — executor missing {missing}; ZERO orders placed")
            return None
        if exch != "NFO" and type(ex).__name__ != ZERODHA_EXECUTOR:
            self._refuse(cid, "EXCHANGE_UNSUPPORTED", f"{sym} is a {exch} contract; the bound executor "
                         f"({type(ex).__name__}) routes NFO only — ZERO orders placed")
            return None
        try:
            order_id, avg, filled = ex.place_buy(sym, int(token or 0), int(qty))
        except Exception as e:
            self._refuse(cid, "BUY_FAIL", f"{sym}: {e!r}")
            return None
        fill_px, ok = float(avg or 0.0), bool(filled == qty and avg)
        t0 = self.clock()
        while not ok and self.clock() - t0 < FILL_TIMEOUT_S:
            self.sleep(FILL_POLL_S)
            try:
                st = ex.get_order_fill(order_id)
            except Exception:
                continue
            status = (st or {}).get("status")
            if status == "COMPLETE":
                fill_px, ok = float(st.get("avg_price") or 0.0), True
            elif status in ("REJECTED", "CANCELLED") and (st or {}).get("found"):
                self._refuse(cid, "BUY_DEAD", f"{sym} order {status} {(st or {}).get('status_message') or ''}".strip())
                return None
        if not ok or fill_px <= 0:
            self._alert("FILL_TIMEOUT", f"#{cid} {sym} unfilled after {FILL_TIMEOUT_S}s — abandoning (CHECK THE "
                        "BROKER: a late fill would be an untracked position)", "critical", mode="LIVE")
            try:
                self.repo.note(cid, f"FILL_TIMEOUT {sym} — check the broker; condition still armed")
            except Exception:
                pass
            return None
        return fill_px

    # ── exits ──
    def check(self, cid: int, opt_ltp: Optional[float], spot: Optional[float]) -> Optional[str]:
        """One poll for one position: update marks, take SL/TP. Returns the
        exit reason when it closed."""
        with self.lock:
            p = self.pos.get(cid)
            if p is None:
                return None
            if opt_ltp and opt_ltp > 0:
                p.mark = float(opt_ltp)
            if spot and spot > 0:
                p.mark_spot = float(spot)
            reason = exit_reason(p.side, opt_ltp, spot, p.sl, p.tp_spot)
            if reason is None:
                return None
            return reason if self.close(cid, reason, ltp=opt_ltp, spot=spot) is not None else None

    def close(self, cid: int, reason: str, ltp: Optional[float] = None, spot: Optional[float] = None) -> Optional[float]:
        """Close one position. Returns the exit price, or None when nothing
        was closed (no position, or LIVE backoff — retried by the engine)."""
        with self.lock:
            p = self.pos.get(cid)
            if p is None:
                return None
            px = float(ltp if ltp else (p.mark or p.entry_px))
            if p.mode == "LIVE" and self.executor is not None:
                n, until = self._backoff.get(cid, (0, 0.0))
                if self.clock() < until:
                    return None
                probe = getattr(self.executor, "get_open_positions_or_none", None)
                if callable(probe):
                    try:
                        positions = probe()
                    except Exception:
                        positions = None
                    if positions is None:
                        n += 1
                        self._backoff[cid] = (n, self.clock() + min(60.0, 5.0 * n))
                        self._alert("BROKER_UNREADABLE", f"#{cid} {p.symbol}: holdings read failed — NO order placed, "
                                    f"backoff {min(60, 5 * n)}s", "error", mode="LIVE")
                        return None
                    holding = any((q.get("tradingsymbol") or q.get("symbol")) == p.symbol
                                  and int(q.get("quantity") or q.get("qty") or 0) > 0 for q in positions)
                    if not holding:
                        self._alert("BROKER_FLAT", f"#{cid} {p.symbol}: broker shows no holding — closing the ROW "
                                    "only, placing NOTHING (reconcile-first)", "warning", mode="LIVE")
                        return self._finish(p, px, "BROKER_FLAT", spot)
                self._backoff.pop(cid, None)
                try:
                    order_id = self.executor.place_market_sell(p.symbol, p.qty)
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
                    n += 1
                    self._backoff[cid] = (n, self.clock() + min(60.0, 5.0 * n))
                    self._alert("SELL_FAIL", f"#{cid} {p.symbol}: {e!r} — POSITION STILL OPEN AT THE BROKER, "
                                f"retrying in {min(60, 5 * n)}s", "critical", mode="LIVE")
                    return None
            return self._finish(p, px, reason, spot)

    def _finish(self, p: LvbPosition, px: float, reason: str, spot: Optional[float]) -> float:
        px = round(float(px), 2)
        if close_paper_trade is not None and p.row_id:
            try:
                close_paper_trade(paper_trade_id=p.row_id, exit_price=px, exit_reason=reason)
            except Exception as e:
                self._alert("CLOSE_ROW_FAIL", f"#{p.cid} {p.symbol}: {e!r}", "error")
        try:
            self.repo.mark_exit(p.cid, px, reason, spot if spot else p.mark_spot)
        except Exception as e:
            self._alert("COND_EXIT_FAIL", f"#{p.cid}: {e!r}", "error")
        gross = (px - p.entry_px) * p.qty
        write_audit_log(f"[LVB][EXIT][{p.mode}] #{p.cid} {reason} {p.symbol} @ {px} gross={gross:,.0f}")
        fn = {"SL": "notify_sl_exit", "TP": "notify_tp_exit"}.get(reason, "notify_manual_exit")
        self._notify(fn, {"strategy_id": STRATEGY_ID, "mode": p.mode, "symbol": p.symbol, "side": p.side,
                          "entry_price": p.entry_px, "exit_price": px, "quantity": p.qty, "pnl": round(gross, 2),
                          "exit_reason": reason, "reason": reason})
        self.pos.pop(p.cid, None)
        self._backoff.pop(p.cid, None)
        return px

    def close_all(self, reason: str, ltps: Optional[Dict[int, float]] = None) -> int:
        n = 0
        for cid in list(self.pos):
            if self.close(cid, reason, ltp=(ltps or {}).get(cid)) is not None:
                n += 1
        return n

    # ── restart ──
    def resume(self, now: int) -> int:
        """Adopt today's open trades from lvb_conditions; a prior day's open
        trade is closed STALE at entry (intraday strategy — the app died
        before its EOD) with a critical alert to verify the broker."""
        today0 = day_start_epoch_of(now)
        n = 0
        for r in self.repo.open_trades():
            cid = int(r["id"])
            if cid in self.pos:
                continue
            if day_start_epoch_of(int(r.get("entry_ts") or 0)) != today0:
                if close_paper_trade is not None and r.get("paper_trade_id"):
                    try:
                        close_paper_trade(paper_trade_id=r["paper_trade_id"], exit_price=float(r["entry_px"]),
                                          exit_reason="STALE")
                    except Exception as e:
                        self._alert("STALE_FAIL", f"#{cid}: {e!r}", "error")
                self.repo.mark_exit(cid, float(r["entry_px"]), "STALE", None)
                self._alert("STALE_ROW", f"#{cid} {r.get('symbol')} ({r.get('mode')}) open since {r.get('day')} — "
                            "closed as STALE at entry. VERIFY THE BROKER BOOK.", "critical", mode=r.get("mode"))
                continue
            self.pos[cid] = LvbPosition(
                cid=cid, row_id=r.get("paper_trade_id"), instrument=r["instrument"], symbol=r["symbol"],
                token=int(r.get("token") or 0), exchange=r.get("exchange") or INSTRUMENTS[r["instrument"]]["exchange"],
                side=r["side"], mode=str(r.get("mode") or "PAPER").upper(), entry_px=float(r["entry_px"]),
                qty=int(r["qty"]), lots=int(r.get("lots") or 1), sl=r.get("sl_px"), tp_spot=r.get("tp_spot"),
                eod=r["eod"], eod_min=hm_to_min(r["eod"]), entry_ts=int(r["entry_ts"]), entry_spot=r.get("entry_spot"),
                mark=r.get("mark") or float(r["entry_px"]), mark_spot=r.get("mark_spot"))
            n += 1
            write_audit_log(f"[LVB][RESUME] #{cid} {r['side']} {r['symbol']} @ {r['entry_px']} ({r.get('mode')}) "
                            f"SL={r.get('sl_px')} TPspot={r.get('tp_spot')} EOD {r['eod']}")
        return n

    # ── kill switch ──
    def kill_all(self) -> int:
        n = self.close_all("KILL")
        write_audit_log(f"[LVB][KILL] flattened {n} position(s)")
        return n

    def kill_adapter(self) -> dict:
        """kill_switch contract: {"closed", "remaining", "detail"}."""
        n = self.kill_all()
        left = [p.symbol for p in self.pos.values()]
        return {"closed": n, "remaining": len(left),
                "detail": [f"LVB_V1 closed {n}"] + ([f"still open: {', '.join(left)}"] if left else [])}

    def positions(self) -> List[dict]:
        with self.lock:
            return [p.view() for p in self.pos.values()]


def _hm(ts: int) -> str:
    m = ((int(ts) + 19800) % 86400) // 60
    return f"{m // 60:02d}:{m % 60:02d}"
