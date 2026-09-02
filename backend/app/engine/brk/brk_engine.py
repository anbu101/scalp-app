# backend/app/engine/brk/brk_engine.py
#
# ── BRK_V1 ENGINE ── clocks, chain snapshots, quotes; decisions in the core.
# ============================================================================
# Fence: BRK_V1_LIVE_20260902 · TSG engine pattern (threaded poll loop, IST
# fixed +05:30, no pytz), decisions ONLY at 1m closes (LD2).
#
# DAILY STATE MACHINE:
#   1. SELECT (per session, at select_time:00–:59): snapshot_weekly_chain
#      (fail closed: (None,[],{}) → session forfeited with an alert, P5 —
#      never a substituted expiry) → core.select() on {side: {sym: ltp}}.
#   2. DECIDE: at each minute boundary (second >= 2 — the completed bar's
#      close is the quote sampled now, the TSG LD2 transport), feed the
#      watched symbols' closes to the core and ask decide(m). ENTER →
#      manager.open_trade with the core's exit levels off the decision LTP
#      (live recomputes off the real fill).
#   3. OPEN BOOK: tick-check core.check_exit every OPEN_POLL_S. In LIVE with
#      a healthy OCO GTT the broker usually exits first; the engine detecting
#      SL/TP on a tick still calls close_trade (cancel-verified handles the
#      race). EOD 15:15 strictly by clock.
#   4. S2 (Config B): same machine at the s2 window, gated by
#      s1_open/s1_result (only_if_flat skips minutes; only_if_loss kills the
#      session on a profitable morning).
#
# RESTART: sessions whose decision window already elapsed are marked done
# (fail closed — no late entries); the open position is resumed from the DB
# by the manager; s1_result comes back from today's closed rows.
#
# PrefixGuard: every observed minute boundary must move strictly forward;
# a rewind (clock jump / rebuilt stream) freezes the day's decisions.
#
# Part-2b note: BRK quotes its two watched contracts directly (kite.quote →
# LTPStore publish, the TSG_LTP_PUBLISH doctrine); it does not read
# ChainStore, so the aligned-ts probe scar does not apply here — the core's
# align_minute/minute_of_day still normalise every wall clock it touches.
# ============================================================================

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config
from app.engine.brk.brk_live_core import (
    BrkCore, ENTER, NO_TRADE, FROZEN, R_EOD, minute_of_day)
from app.engine.brk.brk_manager import BrkManager, STRATEGY_ID

IST = timezone(timedelta(minutes=330))
IDLE_POLL_S = 5
OPEN_POLL_S = 2


def now_ist() -> datetime:
    return datetime.now(IST)


class BrkEngine:
    def __init__(self, manager: BrkManager, broker_manager):
        self.gm = manager
        self.broker = broker_manager
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # per-day state
        self._day: Optional[str] = None
        self.core: Optional[BrkCore] = None
        self._selected: Dict[str, bool] = {}      # tag -> selection done
        self._last_eval_min = -1
        self.status: Dict[str, object] = {}

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="brk-engine")
        self._thread.start()

    def stop(self):
        self._running = False

    # ── broker adapters (TSG pattern, verbatim doctrine) ───────────────
    def _chain(self):
        try:
            from app.engine.ic.ic_selection import snapshot_weekly_chain
            kite = self.broker.get_data_kite()
            if kite is None:
                write_audit_log("[BRK][CHAIN_FAIL] data kite unavailable "
                                "(broker not connected / not logged in)")
                return (None, [], {})
            api_key = getattr(kite, "api_key", None)
            access_token = getattr(kite, "access_token", None)
            return snapshot_weekly_chain(kite, api_key, access_token)
        except Exception as e:
            write_audit_log(f"[BRK][CHAIN_FAIL] {e!r}")
            return (None, [], {})

    def _quote_many(self, symbols):
        try:
            kite = self.broker.get_data_kite()
            if kite is None or not symbols:
                return {}
            q = kite.quote([f"NFO:{s}" for s in symbols]) or {}
            out = {}
            for k, row in q.items():
                sym = k.split(":", 1)[-1]
                v = float((row or {}).get("last_price") or 0)
                if v > 0:
                    out[sym] = v
            try:                                   # TSG_LTP_PUBLISH doctrine
                from app.marketdata.ltp_store import LTPStore
                for sym, v in out.items():
                    LTPStore.update(sym, v)
            except Exception:
                pass
            return out
        except Exception as e:
            write_audit_log(f"[BRK][QUOTE_FAIL] {e!r}")
            return {}

    # ── day setup ──────────────────────────────────────────────────────
    def _roll_day(self, now: datetime):
        day = now.strftime("%Y-%m-%d")
        if day == self._day:
            return
        self._day = day
        cfg = load_strategy_config(STRATEGY_ID) or {}
        self.core = BrkCore(cfg)
        self._selected = {}
        self._last_eval_min = -1
        self.gm.day_results = {}
        self.gm.resume_from_db()
        m = minute_of_day(int(now.timestamp()))
        # RESTART FAIL-CLOSED: windows already elapsed take no late entries.
        for sess in filter(None, (self.core.s1, self.core.s2)):
            if m > sess.spec.last_min and not (
                    self.gm.pos is not None
                    and self.gm.pos.tag == sess.spec.tag):
                sess.done = True
                self._selected[sess.spec.tag] = True
        write_audit_log(f"[BRK][DAY] {day} armed (minute {m}); "
                        f"resumed_pos={bool(self.gm.pos)}")

    # ── selection ──────────────────────────────────────────────────────
    def _try_select(self, sess, now_min: int):
        tag = sess.spec.tag
        if self._selected.get(tag) or now_min < sess.spec.sel_min:
            return
        if now_min > sess.spec.sel_min:
            self._selected[tag] = True
            sess.done = True
            self._alert("SELECT_MISSED",
                        f"{tag} selection minute passed while down — "
                        f"session forfeited (fail closed)")
            return
        expiry, contracts, ltps = self._chain()
        if not contracts:
            self._selected[tag] = True
            sess.done = True
            self._alert("NO_CHAIN", f"{tag} chain snapshot failed — "
                        f"session forfeited (fail closed, P5)")
            return
        by_side: Dict[str, Dict[str, float]] = {"CE": {}, "PE": {}}
        self._tokens = getattr(self, "_tokens", {})
        for c in contracts:
            it = c.get("instrument_type")
            sym = c.get("tradingsymbol")
            if it in ("CE", "PE") and sym in ltps:
                by_side[it][sym] = float(ltps[sym])
                self._tokens[sym] = int(c.get("instrument_token") or 0)
        ce, pe = self.core.select(sess, by_side)
        self._selected[tag] = True
        if ce is None and pe is None:
            sess.done = True
            self._alert("NO_CANDIDATE", f"{tag} no contract below "
                        f"₹{self.core.cfg['select_below']} — no trade")
            return
        write_audit_log(f"[BRK][SELECT] {tag} CE={ce} "
                        f"({sess.sel_prints.get('CE')}) PE={pe} "
                        f"({sess.sel_prints.get('PE')})")

    # ── minute evaluation ──────────────────────────────────────────────
    def _eval_minute(self, m: int):
        """Called once per boundary at second>=2: the quotes sampled now are
        the completed bar (m-1)'s closes."""
        core = self.core
        if not core.guard.observe(m):
            self._alert("PREFIX_FROZEN", core.guard.reason, "error")
            return
        for sess in filter(None, (core.s1, core.s2)):
            if sess.done or not self._selected.get(sess.spec.tag):
                continue
            watched = {s: sym for s, sym in
                       (("CE", sess.ce_sym), ("PE", sess.pe_sym)) if sym}
            quotes = self._quote_many(list(watched.values()))
            for side, sym in watched.items():
                if sym in quotes:
                    core.on_close(sess, side, m - 1, quotes[sym])
            dec, pay = core.decide(
                sess, m, s1_open=self.gm.s1_open(),
                s1_result=self.gm.s1_result())
            if dec == ENTER:
                sym = pay["symbol"]
                ltp = quotes.get(sym) or 0.0
                if ltp <= 0:
                    self._alert("NO_ENTRY_QUOTE", f"{sym} unquotable at the "
                                f"decision minute — session forfeited",
                                "error")
                    sess.done = True
                    continue
                sl, tp = core.exit_levels(ltp)
                self.gm.open_trade(symbol=sym, token=self._tokens.get(sym, 0),
                                   side=pay["side"], tag=pay["tag"], ltp=ltp,
                                   sl_px=sl, tp_px=tp)
            elif dec == NO_TRADE:
                write_audit_log(f"[BRK][NO_TRADE] {sess.spec.tag} window "
                                f"closed without a confirmed break")
            elif dec == FROZEN:
                return

    # ── open-position tick check ───────────────────────────────────────
    def _tick_exits(self, now: datetime):
        pos = self.gm.pos
        if pos is None or self.core is None:
            return
        m = minute_of_day(int(now.timestamp()))
        q = self._quote_many([pos.symbol])
        ltp = q.get(pos.symbol)
        if ltp is None:
            if m >= self.core.eod_min:
                self.gm.close_trade(reason=R_EOD)   # LTP fallback inside
            return
        reason = self.core.check_exit(ltp=ltp, sl_px=pos.sl_px,
                                      tp_px=pos.tp_px, m=m)
        if reason is not None:
            self.gm.close_trade(reason=reason, ltp=ltp)

    # ── loop ───────────────────────────────────────────────────────────
    def _alert(self, code, msg, severity="warning"):
        write_audit_log(f"[BRK][{code}] {msg}")
        try:
            record_alert(source=STRATEGY_ID, code=code, message=msg,
                         severity=severity)
        except Exception:
            pass

    def _loop(self):
        write_audit_log("[BRK][ENGINE] loop up")
        while self._running:
            try:
                now = now_ist()
                self._roll_day(now)
                m = minute_of_day(int(now.timestamp()))
                for sess in filter(None, (self.core.s1, self.core.s2)):
                    self._try_select(sess, m)
                if now.second >= 2 and m != self._last_eval_min:
                    self._last_eval_min = m
                    self._eval_minute(m)
                self._tick_exits(now)
                self.status = {
                    "day": self._day, "minute": m,
                    "frozen": self.core.guard.frozen,
                    "pos": (self.gm.pos.symbol if self.gm.pos else None),
                }
                time.sleep(OPEN_POLL_S if self.gm.pos else IDLE_POLL_S)
            except Exception as e:
                write_audit_log(f"[BRK][ENGINE][ERR] {e!r}")
                time.sleep(IDLE_POLL_S)