# backend/app/engine/tsg/tsg_manager.py
#
# TSG_V1 — Manager (live + paper) — Phase 1 (LD1–LD10, locked 2026-08-02)
# ============================================================================
# Wrapper around the PURE tsg_live_core.TsgDayCore: owns time, data, orders,
# persistence, notifications. The core owns every DECISION — this file must
# never re-implement a rule the core (and therefore the backtest) already
# owns. Modeled on ic_group_manager.py minus everything TSG doesn't have:
# no GTTs, no MTC, no adjustments, no carry. Every exit is an engine-driven
# basket close at 1m boundaries.
#
#   PAPER (Phase 1 validated path): fills at the evaluated mark; rows via
#     paper_trades_repo — same object graph as live (IC D9 doctrine).
#   LIVE (pre-wired, gated by resolve_execution_mode — Phase 2 flips the
#     config, not the code): market entries buys-first with all-or-unwind,
#     market exits with synchronous confirm; failures alert CRITICAL and
#     retry on the next tick rather than silently dropping.
#
# PERSISTENCE (LD6): every mutation snapshots core.to_state() to
# ~/.scalp-app/state/TSG_V1_session.json (atomic tempfile+fsync+os.replace —
# ic_carry_store pattern). Boot restores a SAME-DAY session only; a stale
# session is archived to audit and cleared (TSG holds nothing overnight).
#
# LD11 / IV13 (2026-08-03): ENTRY-IV FLOOR. min_entry_iv (decimal, 0=off,
# validated 0.10: +5.9% net at unchanged day-DD, walk-forward PASS on both
# halves). Decided from LADDER LTPs at selection time — BEFORE any order —
# using the same parity-spot + OTM-preference solve as the anchors. Mean
# of the shorts' pre-entry IVs below the floor → day SKIPPED (state
# D_SKIPPED + skip_reason, surfaced on the dashboard panel + TSG_SKIP
# alert). Unsolvable → FAIL-OPEN (enter; audit-logged), matching the
# backtest's iv_filter_open_days. Divergence-ledger note: the filter
# solves from LTPs, the breaker anchors from FILLS — seconds and paise
# apart, same machinery.
#
# IV INPUTS (LD2/IV10): once per minute the manager batch-quotes the open
# legs + each short strike's OPPOSITE-type sibling, infers parity spot from
# the shorts' strikes (S ≈ C − P + K, short-tau approximation — documented
# divergence: backtest uses the r-discounted ladder solve), and solves each
# short's strike IV OTM-side-first via ic_synth_wing.implied_vol (a pure
# pricing module — no live/backtest coupling beyond math).
# ============================================================================

import json
import os
import uuid
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone, date as _date
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config
from app.risk.risk_mtm_guard import is_day_blocked
from app.risk.strategy_max_loss_guard import check_strategy_max_loss
from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade
# ── TSG_LIVE_BOOK ── live legs were never written to any table (see
# _book_live_row); IC's D9 doctrine is paper->paper_trades, live->trades.
from app.db.trades_repo import insert_trade, close_trade
from app.backtest.ic import ic_synth_wing as SW

from app.engine.tsg.tsg_live_core import (
    TsgDayCore, TsgLeg, resolve_lots,
    D_IDLE, D_OPEN, D_PARTIAL, D_CLOSED, D_ABORTED, D_SKIPPED,
    L_OPEN,
)

IST = timezone(timedelta(minutes=330))
STRATEGY_ID = "TSG_V1"

STATE_DIR = Path.home() / ".scalp-app" / "state"
SESSION_PATH = STATE_DIR / "TSG_V1_session.json"

DEFAULT_LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "premium_max": 85},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "premium_max": 85},
    {"id": "L3", "action": "BUY", "opt_type": "CE", "premium_max": 5},
    {"id": "L4", "action": "BUY", "opt_type": "PE", "premium_max": 5},
]


def _now_ist() -> datetime:
    return datetime.now(IST)


def _ts() -> int:
    return int(time.time())


class TsgManager:

    def __init__(self, executor=None, quote_fn: Optional[Callable] = None):
        """executor: ZerodhaOrderExecutor (None in pure-paper startup —
        attach_executor() later; house rule: pre-built by the runtime so a
        mid-session PAPER→LIVE flip needs no restart).
        quote_fn(symbols: List[str]) -> {symbol: ltp} — injectable for
        tests; the runtime wires a batched data-kite quote."""
        self.executor = executor
        self._quote_fn = quote_fn
        self._lock = threading.RLock()
        self._core: Optional[TsgDayCore] = None
        self._paper = True
        self._entry_date: Optional[str] = None     # once-per-day latch
        self._expiry_iso: str = ""
        self._token_by_sym: Dict[str, dict] = {}   # symbol → chain row meta
        self._sibling: Dict[str, str] = {}         # short leg_id → opp symbol
        self._paper_row_ids: Dict[str, str] = {}   # leg_id → paper_trade_id
        # ── TSG_LIVE_BOOK ── leg_id → trades.trade_id (LIVE mode). Persisted
        # in the session file: a mid-day restart that loses these leaves the
        # rows OPEN forever, and tomorrow's entry then trips
        # uniq_open_trade_per_slot on the same slot.
        self._live_row_ids: Dict[str, str] = {}
        self._group_id: Optional[str] = None
        self._lot_size = 65
        self._restore_session()
        self._sweep_stale_live_rows()   # ── TSG_STALE_SWEEP ──

    # ── wiring ──────────────────────────────────────────────────────────
    def attach_executor(self, executor):
        self.executor = executor

    def attach_quote_fn(self, fn: Callable):
        self._quote_fn = fn

    # ── config ──────────────────────────────────────────────────────────
    def _cfg(self) -> dict:
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception:
            return {}

    @staticmethod
    def _legs_cfg(cfg: dict) -> List[dict]:
        legs = cfg.get("legs")
        return legs if isinstance(legs, list) and legs else DEFAULT_LEGS

    # ── panel state ─────────────────────────────────────────────────────
    def snapshot(self) -> Optional[dict]:
        with self._lock:
            if self._core is None:
                return None
            d = self._core.to_state()
            d["paper"] = self._paper
            d["expiry"] = self._expiry_iso
            marks = {i: (self._core.legs[i].last_mark)
                     for i in self._core.legs}
            d["day_mtm"] = self._core.day_mtm(
                {i: m for i, m in marks.items() if m is not None}) \
                if self._core.state in (D_OPEN, D_PARTIAL) else \
                self._core.realized
            return d

    def has_open_day(self) -> bool:
        with self._lock:
            return (self._core is not None
                    and self._core.state in (D_OPEN, D_PARTIAL))

    def latched_today(self) -> bool:
        return self._entry_date == _now_ist().date().isoformat()

    # ── ENTRY (LD3/LD5) ─────────────────────────────────────────────────
    def enter_day(self, chain, *, mode: str) -> bool:
        """chain = (expiry_date, rows, ltp_by_symbol) from
        snapshot_weekly_chain (fail-closed: (None, [], {}) → no entry)."""
        with self._lock:
            return self._enter_day_impl(chain, mode=mode)

    def _enter_day_impl(self, chain, *, mode: str) -> bool:
        today = _now_ist().date().isoformat()
        if self.latched_today():
            return False
        if self.has_open_day():
            write_audit_log("[TSG][ENTRY] open book — entry blocked")
            return False
        try:
            if is_day_blocked(STRATEGY_ID):
                write_audit_log("[TSG][ENTRY] day blocked — NO ENTRY")
                self._entry_date = today
                return False
        except Exception:
            pass
        try:
            if check_strategy_max_loss(STRATEGY_ID):
                self._alert("TSG_MAX_LOSS",
                            "TSG_V1 max-loss guard active — NO ENTRY today",
                            severity="warning", mode=mode.lower())
                self._entry_date = today
                return False
        except Exception:
            pass

        expiry, rows, ltp = chain if chain else (None, [], {})
        if not expiry or not rows:
            self._alert("TSG_NO_CHAIN",
                        "TSG_V1: chain snapshot unavailable — NO ENTRY "
                        "today (fail closed)", severity="warning",
                        mode=mode.lower())
            self._entry_date = today
            return False

        cfg = self._cfg()
        lot_size = int(cfg.get("lot_size", 65) or 65)
        self._lot_size = lot_size
        is_expiry = (str(expiry) == today)
        base_lots = int(cfg.get("lots", 1) or 1)
        lots = resolve_lots(base_lots,
                            int(cfg.get("expiry_lots", 0) or 0), is_expiry)
        # LD5a (2026-08-03): MTM SL/target scale with the day's lots so the
        # PER-LOT risk geometry stays exactly what the backtest validated
        # (₹35k @ 10 lots ≡ ₹3.5k/lot). The expiry lot-scaling analysis was
        # linear P&L scaling — which scales the SL implicitly — so this
        # rule matches the evidence, not just intuition. IV knobs are vol-
        # points (lot-independent) and deliberately do NOT scale.
        risk_scale = (lots / base_lots) if base_lots > 0 else 1.0

        ladder: Dict[str, list] = {"CE": [], "PE": []}
        meta: Dict[str, dict] = {}
        for r in rows:
            sym = r.get("tradingsymbol")
            it = r.get("instrument_type")
            px = ltp.get(sym)
            if sym and it in ("CE", "PE") and px and px > 0:
                ladder[it].append((sym, float(px)))
                meta[sym] = r
        self._token_by_sym = meta
        self._expiry_iso = str(expiry)

        core = TsgDayCore(
            mtm_sl=abs(float(cfg.get("mtm_sl", 35000) or 0)) * risk_scale,
            mtm_target=abs(float(cfg.get("mtm_target", 0) or 0)) * risk_scale,
            iv_sl_pct=abs(float(cfg.get("iv_sl_pct", 0) or 0)),
            iv_sl_delta_pts=abs(float(cfg.get("iv_sl_delta_pts", 4) or 0)),
            entry_date=today,
        )
        planned = core.plan_entry(ladder, self._legs_cfg(cfg),
                                  lot_size, lots, meta)
        self._core = core
        self._entry_date = today
        if planned is None:
            self._alert("TSG_SKIP",
                        f"TSG_V1 skipped today: {core.skip_reason}",
                        severity="warning", mode=mode.lower())
            self._persist()
            return False

        # ── LD11 / IV13 ── entry-IV floor from ladder LTPs (pre-order)
        floor = abs(float(cfg.get("min_entry_iv", 0) or 0))
        if floor > 0:
            _spot = self._parity_spot(ltp)
            _eivs = []
            for l in planned:
                if not l.is_short:
                    continue
                _sib = self._sibling_symbol(l)
                _iv = self._solve_strike_iv(
                    l, dict(ladder[l.opt_type]).get(l.symbol),
                    ltp.get(_sib) if _sib else None, _spot)
                if _iv is not None:
                    _eivs.append(_iv)
            if _eivs:
                _mean = sum(_eivs) / len(_eivs)
                if _mean < floor:
                    core.state = D_SKIPPED
                    core.skip_reason = (f"entry IV {_mean:.3f} below floor "
                                        f"{floor:g} — dead low-vol day "
                                        f"(IV13)")
                    self._alert("TSG_SKIP",
                                f"TSG_V1 NO ENTRY: {core.skip_reason}",
                                severity="warning", mode=mode.lower())
                    write_audit_log(f"[TSG][IV_FLOOR] {core.skip_reason}")
                    self._persist()
                    return False
                write_audit_log(f"[TSG][IV_FLOOR] entry IV {_mean:.3f} >= "
                                f"{floor:g} — clear to enter")
            else:
                write_audit_log("[TSG][IV_FLOOR] entry IVs unsolvable — "
                                "fail-open (entering)")

        self._paper = (mode != "LIVE")
        entry_px = {l.leg_id: dict(ladder[l.opt_type]).get(l.symbol)
                    for l in planned}
        ok = (self._enter_paper(planned, entry_px) if self._paper
              else self._enter_live(planned, entry_px))
        if ok:
            self._anchor_entry_ivs(ltp)
            self._persist()
            _rs = (f" risk×{risk_scale:.2f} (SL ₹{core.mtm_sl:,.0f})"
                   if abs(risk_scale - 1.0) > 1e-9 else "")
            self._notify(f"TSG_V1 ENTERED ({'paper' if self._paper else 'LIVE'})"
                         f" lots={lots}{' [EXPIRY-DAY]' if is_expiry else ''}{_rs}: "
                         + ", ".join(f"{l.leg_id} {l.action} {l.symbol}"
                                     for l in planned))
            # ── GROUP_ENTRY ── Telegram basket message (was: never sent —
            # _notify only reaches the in-app bell, so TSG entries were
            # silent on Telegram since launch).
            self._notify_group_entry(lots, is_expiry)
        return ok

    def _enter_paper(self, planned: List[TsgLeg], entry_px: Dict) -> bool:
        for l in planned:
            px = entry_px.get(l.leg_id)
            self._core.leg_filled(l.leg_id, px, order_id="PAPER")
            try:
                pid = str(uuid.uuid4())
                tok = int((self._token_by_sym.get(l.symbol) or {})
                          .get("instrument_token") or 0)
                insert_paper_trade(
                    paper_trade_id=pid, strategy_name=STRATEGY_ID,
                    trade_mode="PAPER", symbol=l.symbol, token=tok,
                    side=l.opt_type, entry_price=float(px),
                    candle_ts=_ts(), sl_price=0.0, tp_price=0.0, rr=0.0,
                    lots=l.qty // max(1, self._lot_size),
                    lot_size=self._lot_size, qty=l.qty,
                    trade_direction="SHORT" if l.is_short else "LONG",
                    group_id=None, trade_class=l.leg_id)
                self._paper_row_ids[l.leg_id] = pid
            except Exception as e:
                write_audit_log(f"[TSG][PAPER][ROW_FAIL] {l.leg_id}: {e!r}")
        write_audit_log("[TSG][ENTRY][PAPER] day OPEN")
        return True

    def _enter_live(self, planned: List[TsgLeg], entry_px: Dict) -> bool:
        """LD3: buys before sells; all-or-unwind (IC D6)."""
        if self.executor is None:
            self._alert("TSG_NO_EXECUTOR",
                        "TSG_V1 LIVE entry impossible — executor missing",
                        severity="error", mode="live")
            self._core.state = D_ABORTED
            return False
        order = ["L3", "L4", "L1", "L2"]
        for lid in order:
            leg = self._core.legs.get(lid)
            if leg is None:
                continue
            res = self._place_and_confirm(leg)
            if not res["ok"]:
                # ── TSG_ENTRY_REPEG ── D5: a cancel that lands AFTER a
                # partial fill leaves a live residual position that the
                # in-memory core never knew about. Flatten it FIRST, then
                # run the normal all-or-unwind of already-open legs —
                # otherwise the abort path unwinds the hedges and walks
                # away from a naked partial short.
                if res["filled_qty"] > 0:
                    self._flatten_entry_residual(leg, res["filled_qty"])
                for open_id in self._core.leg_entry_dead(lid):
                    self._market_close(self._core.legs[open_id], "UNWIND")
                self._alert("TSG_ENTRY_UNWIND",
                            f"TSG_V1 LIVE entry failed at {lid} — unwound",
                            severity="error", mode="live")
                return False
            self._core.leg_filled(lid, res["avg"])
            self._book_live_row(leg)          # ── TSG_LIVE_BOOK ──
        write_audit_log("[TSG][ENTRY][LIVE] day OPEN")
        return True

    # ── TSG_ENTRY_REPEG BEGIN ──────────────────────────────────────────
    # D1–D5 (2026-08-10 L1 incident: SELL 24650CE limit 75.00 vs falling
    # bid; single-shot order sat OPEN 22s, timeout-cancelled, whole day
    # aborted −₹26 on the hedge round-trip).
    #
    #   D1  re-peg loop: on an unfilled slice, MODIFY the working order
    #       (same order_id — no cancel/re-place orphan window) to a fresh
    #       touch-referenced price; cancel+abort only after the last slice.
    #       Old behaviour == entry_repeg_max = 0.
    #   D2  fresh prices come from executor.fresh_{sell,buy}_entry_limit
    #       (best bid / best ask, LTP fallback).
    #   D3  tiered buffer lives in the executor (_entry_limit_price).
    #   D4  [TSG][ENTRY_WAIT] heartbeat every poll — the incident log was
    #       silent for the entire 22s window.
    #   D5  post-cancel fill state (filled_qty / avg) is read back and
    #       returned so the abort path can flatten a partial residual.
    #
    # Config (TSG_V1 strategy config; defaults preserve the old ~20s
    # total envelope): entry_fill_timeout_s = seconds per slice (5),
    # entry_repeg_max = number of MODIFY attempts after the initial
    # placement (3). Worst case ≈ (1 + max) × timeout.
    def _place_and_confirm(self, leg: TsgLeg) -> dict:
        """Returns {"ok": bool, "avg": float|None, "filled_qty": int}."""
        fail = {"ok": False, "avg": None, "filled_qty": 0}
        try:
            tok = (self._token_by_sym.get(leg.symbol) or {}).get(
                "instrument_token")
            if leg.is_short:
                oid, limit_px, _ = self.executor.place_sell_entry(
                    symbol=leg.symbol, token=tok, qty=leg.qty)
            else:
                out = self.executor.place_buy(leg.symbol, tok, leg.qty)
                oid, limit_px = (out[0], out[1]) if isinstance(
                    out, (tuple, list)) else (out, None)
            res = self._confirm_entry_with_repeg(leg, oid, limit_px)
            if res["ok"]:
                leg.entry_order_id = oid
            return res
        except Exception as e:
            write_audit_log(f"[TSG][ENTRY][{leg.leg_id}][PLACE_FAIL] {e!r}")
            return fail

    def _confirm_entry_with_repeg(self, leg: TsgLeg, oid,
                                  limit_px) -> dict:
        cfg = self._cfg()
        slice_s = max(2, int(cfg.get("entry_fill_timeout_s", 5) or 5))
        max_repegs = max(0, int(cfg.get("entry_repeg_max", 3) or 3))
        t0 = time.time()
        attempt = 0                     # 0 = initial placement
        cur_limit = limit_px
        while attempt <= max_repegs:
            slice_t0 = time.time()
            while time.time() - slice_t0 < slice_s:
                st = {}
                try:
                    st = self.executor.get_order_fill(oid) or {}
                except Exception:
                    pass
                status = (st.get("status") or "").upper()
                filled = int(st.get("filled_qty") or 0)
                write_audit_log(
                    f"[TSG][ENTRY_WAIT] leg={leg.leg_id} order_id={oid} "
                    f"status={status or 'PENDING'} filled={filled}/{leg.qty} "
                    f"attempt={attempt}/{max_repegs} "
                    f"limit={cur_limit} elapsed={time.time() - t0:.0f}s")
                if status == "COMPLETE":
                    avg = float(st.get("avg_price") or 0.0)
                    return {"ok": True,
                            "avg": avg if avg > 0 else cur_limit,
                            "filled_qty": filled or leg.qty}
                if status in ("REJECTED", "CANCELLED", "LAPSED"):
                    write_audit_log(
                        f"[TSG][ENTRY][{leg.leg_id}] order {status} "
                        f"broker-side — no re-peg possible")
                    return {"ok": False, "avg": None, "filled_qty": filled}
                time.sleep(1.0)
            attempt += 1
            if attempt > max_repegs:
                break
            fresh = (self.executor.fresh_sell_entry_limit(leg.symbol)
                     if leg.is_short
                     else self.executor.fresh_buy_entry_limit(leg.symbol))
            if fresh is None:
                write_audit_log(
                    f"[TSG][ENTRY_REPEG] leg={leg.leg_id} attempt={attempt}"
                    f" — no fresh quote, keeping limit {cur_limit}")
                continue
            new_limit, ref, src = fresh
            if cur_limit is not None and abs(new_limit - cur_limit) < 0.049:
                write_audit_log(
                    f"[TSG][ENTRY_REPEG] leg={leg.leg_id} attempt={attempt}"
                    f" — price unchanged ({new_limit} ~ {cur_limit}), "
                    f"waiting another slice")
                continue
            ok = self.executor.modify_order(oid, price=new_limit,
                                            symbol=leg.symbol)
            if ok is None:
                write_audit_log(
                    f"[TSG][ENTRY_REPEG] leg={leg.leg_id} attempt={attempt}"
                    f" — MODIFY failed, keeping limit {cur_limit}")
                continue
            write_audit_log(
                f"[TSG][ENTRY_REPEG] leg={leg.leg_id} attempt={attempt} "
                f"order_id={oid} {cur_limit} -> {new_limit} "
                f"(ref={ref} src={src})")
            cur_limit = new_limit
        # exhausted → cancel, then read back post-cancel fill state (D5).
        try:
            self.executor.cancel_order(oid, symbol=leg.symbol)
        except Exception as e:
            write_audit_log(f"[TSG][ENTRY][{leg.leg_id}][CANCEL_FAIL] {e!r}")
        filled, avg = 0, 0.0
        cancel_t0 = time.time()
        while time.time() - cancel_t0 < 5:
            try:
                st = self.executor.get_order_fill(oid) or {}
                status = (st.get("status") or "").upper()
                filled = int(st.get("filled_qty") or 0)
                avg = float(st.get("avg_price") or 0.0)
                if status == "COMPLETE":
                    # cancel raced a full fill — the leg is actually ours
                    write_audit_log(
                        f"[TSG][ENTRY][{leg.leg_id}] cancel raced a "
                        f"COMPLETE fill — accepting leg")
                    return {"ok": True,
                            "avg": avg if avg > 0 else cur_limit,
                            "filled_qty": filled or leg.qty}
                if status in ("CANCELLED", "REJECTED", "LAPSED"):
                    break
            except Exception:
                pass
            time.sleep(1.0)
        write_audit_log(
            f"[TSG][ENTRY][{leg.leg_id}] entry timed out — cancelled "
            f"(filled={filled}/{leg.qty} avg={avg})")
        return {"ok": False,
                "avg": avg if filled > 0 else None,
                "filled_qty": filled}

    def _flatten_entry_residual(self, leg: TsgLeg, filled_qty: int) -> None:
        """D5: flatten the partially-filled residual of an aborted entry.
        NFO fills arrive in lot multiples, so filled_qty always satisfies
        the executor's lot-size validation."""
        try:
            write_audit_log(
                f"[TSG][ENTRY_PARTIAL] leg={leg.leg_id} {leg.symbol} "
                f"residual={filled_qty} — flattening before unwind")
            if leg.is_short:
                oid = self.executor.place_buy_exit(
                    leg.symbol, filled_qty, "ENTRY_PARTIAL_UNWIND")
            else:
                oid = self.executor.place_market_sell(leg.symbol,
                                                      filled_qty)
            avg = self._confirm_fill(oid)
            if avg is None:
                self._alert(
                    "TSG_PARTIAL_STUCK",
                    f"TSG_V1: partial entry residual on {leg.leg_id} "
                    f"({leg.symbol} qty={filled_qty}) could not be "
                    f"confirmed flat — CHECK KITE POSITIONS NOW",
                    severity="error", mode="live")
            else:
                write_audit_log(
                    f"[TSG][ENTRY_PARTIAL] {leg.leg_id} residual flat "
                    f"@ {avg}")
        except Exception as e:
            self._alert(
                "TSG_PARTIAL_STUCK",
                f"TSG_V1: flatten of partial entry residual FAILED for "
                f"{leg.leg_id} ({leg.symbol} qty={filled_qty}): {e!r} — "
                f"CHECK KITE POSITIONS NOW",
                severity="error", mode="live")
    # ── TSG_ENTRY_REPEG END ────────────────────────────────────────────

    def _confirm_fill(self, oid, timeout_s: int = 20) -> Optional[float]:
        """Synchronous fill confirm — the PST_FILL_TIMEOUT lesson: poll the
        order, never assume."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                st = self.executor.get_order_fill(oid) or {}
                status = (st.get("status") or "").upper()
                if status == "COMPLETE":
                    avg = st.get("average_price") or st.get("avg_price")
                    return float(avg) if avg else None
                if status in ("REJECTED", "CANCELLED"):
                    return None
            except Exception:
                pass
            time.sleep(1.0)
        return None

    # ── entry IV anchoring (IV11 via IV10 preference) ───────────────────
    def _expiry_ts(self) -> int:
        try:
            y, m, d = (int(x) for x in self._expiry_iso.split("-"))
            dt = datetime(y, m, d, 15, 30, tzinfo=IST)
            return int(dt.timestamp())
        except Exception:
            return _ts() + 24 * 3600

    def _parity_spot(self, px: Dict[str, float]) -> Optional[float]:
        """S ≈ C − P + K averaged over the shorts' strikes (short-tau
        approximation; divergence-ledger item)."""
        vals = []
        for lid in ("L1", "L2"):
            leg = (self._core.legs or {}).get(lid)
            if leg is None:
                continue
            k = leg.strike
            ce = pe = None
            for sym, m in self._token_by_sym.items():
                if float(m.get("strike") or 0) == k:
                    if m.get("instrument_type") == "CE":
                        ce = px.get(sym)
                    elif m.get("instrument_type") == "PE":
                        pe = px.get(sym)
            if ce and pe:
                vals.append(ce - pe + k)
        return (sum(vals) / len(vals)) if vals else None

    def _sibling_symbol(self, leg: TsgLeg) -> Optional[str]:
        want = "PE" if leg.opt_type == "CE" else "CE"
        for sym, m in self._token_by_sym.items():
            if (float(m.get("strike") or 0) == leg.strike
                    and m.get("instrument_type") == want):
                return sym
        return None

    def _solve_strike_iv(self, leg: TsgLeg, own_px: Optional[float],
                         opp_px: Optional[float], spot: Optional[float]
                         ) -> Optional[float]:
        if not spot or spot <= 0:
            return None
        tau = SW.tau_years(_ts(), self._expiry_ts())
        is_call = leg.opt_type == "CE"
        own = (own_px, is_call) if own_px else None
        opp = (opp_px, not is_call) if opp_px else None
        own_otm = (leg.strike > spot) if is_call else (leg.strike < spot)
        order = ([own, opp] if own_otm or opp is None else [opp, own])
        for cand in order:
            if cand is None:
                continue
            iv = SW.implied_vol(cand[0], cand[1], spot, leg.strike, tau)
            if iv is not None:
                return iv
        return None

    def _anchor_entry_ivs(self, ltp: Dict[str, float]) -> None:
        spot = self._parity_spot(ltp)
        for lid in ("L1", "L2"):
            leg = self._core.legs.get(lid)
            if leg is None:
                continue
            sib = self._sibling_symbol(leg)
            self._sibling[lid] = sib or ""
            iv = self._solve_strike_iv(
                leg, leg.entry_price, ltp.get(sib) if sib else None, spot)
            self._core.set_entry_iv(lid, iv)
            if iv is None and leg.is_short:
                write_audit_log(f"[TSG][IV] entry IV unsolvable for {lid} — "
                                f"unmonitored today (parity: "
                                f"iv_entry_solve_fail)")

    # ── PER-MINUTE EVALUATION (LD2/LD4) ─────────────────────────────────
    def on_minute(self, now: datetime) -> None:
        with self._lock:
            if self._core is None or self._core.state not in (D_OPEN,
                                                              D_PARTIAL):
                return
            syms = [self._core.legs[i].symbol for i in self._core.open_ids()]
            syms += [s for s in self._sibling.values() if s]
            px = self._quotes(list(dict.fromkeys(syms)))
            if not px:
                return
            marks = {i: px.get(self._core.legs[i].symbol)
                     for i in self._core.open_ids()}
            spot = self._parity_spot(px)
            ivs: Dict[str, Optional[float]] = {}
            for lid in ("L1", "L2"):
                leg = self._core.legs.get(lid)
                if leg is None or leg.state != L_OPEN or not leg.is_short:
                    continue
                ivs[lid] = self._solve_strike_iv(
                    leg, marks.get(lid),
                    px.get(self._sibling.get(lid) or ""), spot)
                if ivs[lid] is not None:
                    leg.last_iv = ivs[lid]
            decision = self._core.evaluate_minute(marks, ivs)
            self._persist()
            if decision is None:
                return
            reason, ids = decision
            self._execute_exits(ids, reason)

    def refresh_display(self) -> None:
        """DISPLAY-ONLY refresh (~4s cadence from the engine): update
        leg.last_mark + leg.last_iv for the panel. Deliberately performs
        NO exit evaluation and NO persistence — decisions remain exclusively
        the property of on_minute at 1m closes (LD2 backtest parity). Side
        benefit: the D11 carry-forward fallback inside evaluate_minute now
        falls back to a seconds-old price instead of a minutes-old one."""
        with self._lock:
            if self._core is None or self._core.state not in (D_OPEN,
                                                              D_PARTIAL):
                return
            syms = [self._core.legs[i].symbol for i in self._core.open_ids()]
            syms += [s for s in self._sibling.values() if s]
            px = self._quotes(list(dict.fromkeys(syms)))
            if not px:
                return
            spot = self._parity_spot(px)
            for i in self._core.open_ids():
                leg = self._core.legs[i]
                mk = px.get(leg.symbol)
                if mk is not None:
                    leg.last_mark = mk
                if leg.is_short and leg.iv_threshold is not None:
                    iv = self._solve_strike_iv(
                        leg, mk, px.get(self._sibling.get(i) or ""), spot)
                    if iv is not None:
                        leg.last_iv = iv

    def _quotes(self, symbols: List[str]) -> Dict[str, float]:
        try:
            if self._quote_fn is None or not symbols:
                return {}
            return {s: float(v) for s, v in
                    (self._quote_fn(symbols) or {}).items() if v}
        except Exception as e:
            write_audit_log(f"[TSG][QUOTES_FAIL] {e!r}")
            return {}

    # ── EXIT EXECUTION ──────────────────────────────────────────────────
    def _execute_exits(self, ids: List[str], reason: str) -> None:
        self._core.begin_close() if reason in ("MTM_SL", "MTM_TARGET",
                                               "EOD", "KILL", "MANUAL") \
            else None
        # ── GROUP_EXIT ── remember what this batch was ASKED to close; a
        # live _market_close can leave a leg OPEN (unconfirmed fill retries
        # next tick), so the message is built from what ACTUALLY closed.
        asked = [lid for lid in ids
                 if (self._core.legs.get(lid) is not None
                     and self._core.legs[lid].state == L_OPEN)]
        for n, lid in enumerate(ids):
            leg = self._core.legs[lid]
            r = reason
            if reason == "IV_SL" and not leg.is_short:
                r = "IV_SL_HEDGE"
            if self._paper:
                px = leg.last_mark if leg.last_mark else leg.entry_price
                self._book_exit(leg, px, r)
            else:
                self._market_close(leg, r)
        self._persist()
        self._notify_group_exit(asked, reason)

    def _book_exit(self, leg: TsgLeg, px: float, reason: str,
                   exit_order_id: Optional[str] = None) -> None:
        self._core.leg_exited(leg.leg_id, px, reason, ts=_ts())
        try:
            pid = self._paper_row_ids.get(leg.leg_id)
            if self._paper and pid is not None:
                close_paper_trade(
                    paper_trade_id=pid, exit_price=float(px),
                    exit_reason=reason,
                    trade_direction="SHORT" if leg.is_short else "LONG")
        except Exception as e:
            write_audit_log(f"[TSG][PAPER][CLOSE_FAIL] {leg.leg_id}: {e!r}")
        # ── TSG_LIVE_BOOK ── close the trades row so the day's realized P&L
        # survives the session file, which is deleted on the next day's boot.
        try:
            tid = self._live_row_ids.get(leg.leg_id)
            if not self._paper and tid is not None:
                close_trade(trade_id=tid, exit_price=float(px),
                            exit_order_id=exit_order_id, exit_reason=reason)
        except Exception as e:
            write_audit_log(f"[TSG][LIVE][CLOSE_FAIL] {leg.leg_id}: {e!r}")
        pnl = leg.pnl()
        self._notify(f"TSG_V1 EXIT {leg.leg_id} {leg.symbol} {reason} "
                     f"@ {px} (pnl {pnl:+.0f})" if pnl is not None else
                     f"TSG_V1 EXIT {leg.leg_id} {reason} @ {px}")
        if self._core.state == D_CLOSED:
            self._notify(f"TSG_V1 DAY CLOSED — realized "
                         f"{self._core.realized:+.0f}")

    def _market_close(self, leg: TsgLeg, reason: str) -> None:
        try:
            if leg.is_short:
                oid = self.executor.place_buy_exit(leg.symbol, leg.qty,
                                                   reason)
            else:
                oid = self.executor.place_market_sell(leg.symbol, leg.qty)
            avg = self._confirm_fill(oid)
            if avg is None:
                self._alert("TSG_EXIT_STUCK",
                            f"TSG_V1 exit unconfirmed for {leg.leg_id} "
                            f"({reason}) — will retry next tick",
                            severity="error", mode="live")
                return                       # leg stays OPEN → next tick retries
            self._book_exit(leg, avg, reason, exit_order_id=oid)
        except Exception as e:
            write_audit_log(f"[TSG][EXIT][{leg.leg_id}][FAIL] {e!r}")

    # ── EOD / MANUAL / KILL ─────────────────────────────────────────────
    def square_off_all(self, reason: str = "EOD") -> int:
        with self._lock:
            if self._core is None:
                return 0
            ids = self._core.open_ids()
            if not ids:
                return 0
            self._execute_exits(ids, reason)
            return len(ids)

    def kill_all(self) -> dict:
        """LD7 adapter for app.execution.kill_switch. Flatten everything,
        report; the kill framework owns verified-flat + mode flip."""
        with self._lock:
            if self._core is None or not self._core.open_ids():
                return {"ok": True, "flat": True, "closed": []}
            ids = list(self._core.open_ids())
            self._execute_exits(ids, "KILL")
            flat = not self._core.open_ids()
            return {"ok": flat, "flat": flat,
                    "closed": [i for i in ids
                               if self._core.legs[i].state != L_OPEN]}

    # ── TSG_LIVE_BOOK BEGIN ────────────────────────────────────────────
    def _book_live_row(self, leg: TsgLeg) -> None:
        """Write a filled LIVE leg into `trades`, mirroring IC's D9 doctrine
        (paper -> paper_trades, live -> trades).

        WAS: nothing. _enter_live placed orders and recorded them ONLY in the
        in-memory core and the session JSON — which _restore_session deletes
        on the next day's boot ("TSG holds nothing overnight"). Live P&L was
        therefore absent from the EOD card, Analytics, the CSV export and all
        history, and was permanently gone the following morning.

        SLOT NAMESPACING: uniq_open_trade_per_slot is UNIQUE(slot) WHERE
        exit_time IS NULL — on slot ALONE, not (strategy_id, slot). IC books
        its legs as bare "L1".."L4", so a TSG leg using the same value would
        collide with an open IC live leg and one of the two inserts would be
        rejected. Prefixing with the strategy id sidesteps that without a
        migration. (The IC_V1-vs-IC_V2 collision on that index is the same
        latent bug and is NOT fixed here — it needs a schema change.)
        """
        try:
            if self._group_id is None:
                self._group_id = f"TSG_V1_{self._entry_date or _now_ist().date().isoformat()}"
            tid = str(uuid.uuid4())
            tok = int((self._token_by_sym.get(leg.symbol) or {})
                      .get("instrument_token") or 0)
            insert_trade(
                trade_id=tid, strategy_id=STRATEGY_ID,
                slot=f"{STRATEGY_ID}_{leg.leg_id}",
                symbol=leg.symbol, token=tok,
                entry_price=float(leg.entry_price or 0.0), qty=leg.qty,
                buy_order_id=leg.entry_order_id or "TSG",
                # TSG carries no per-leg SL/TP — risk is the GROUP MTM stop,
                # so these are 0.0 and tp_mode is MANUAL (no GTT is placed).
                sl_price=0.0, tp_price=0.0, tp_mode="MANUAL",
                state="PROTECTED",
                trade_direction="SHORT" if leg.is_short else "LONG",
                group_id=self._group_id, trade_class=leg.leg_id,
            )
            self._live_row_ids[leg.leg_id] = tid
        except Exception as e:
            write_audit_log(f"[TSG][LIVE][ROW_FAIL] {leg.leg_id}: {e!r}")
    # ── TSG_LIVE_BOOK END ──────────────────────────────────────────────

    # ── persistence (LD6, ic_carry_store pattern) ───────────────────────
    def _persist(self) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "entry_date": self._entry_date,
                       "paper": self._paper, "expiry": self._expiry_iso,
                       "sibling": self._sibling,
                       "paper_rows": self._paper_row_ids,
                       "live_rows": self._live_row_ids,   # ── TSG_LIVE_BOOK ──
                       "group_id": self._group_id,
                       # slim chain meta — _parity_spot() needs strike/type
                       # pairs AFTER a restart (LD6 gap caught by the
                       # restart-path integration smoke 2026-08-02)
                       "meta": {s: {"strike": m.get("strike"),
                                    "instrument_type":
                                        m.get("instrument_type"),
                                    "instrument_token":
                                        m.get("instrument_token")}
                                for s, m in self._token_by_sym.items()},
                       "core": self._core.to_state() if self._core else None}
            fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR))
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SESSION_PATH)
        except Exception as e:
            write_audit_log(f"[TSG][PERSIST_FAIL] {e!r}")

    def _restore_session(self) -> None:
        try:
            if not SESSION_PATH.exists():
                return
            payload = json.loads(SESSION_PATH.read_text())
            today = _now_ist().date().isoformat()
            if payload.get("entry_date") != today:
                write_audit_log("[TSG][RESTORE] stale session "
                                f"({payload.get('entry_date')}) — cleared "
                                "(TSG holds nothing overnight)")
                SESSION_PATH.unlink(missing_ok=True)
                return
            core = payload.get("core")
            self._core = TsgDayCore.from_state(core) if core else None
            self._entry_date = payload.get("entry_date")
            self._paper = bool(payload.get("paper", True))
            self._expiry_iso = payload.get("expiry") or ""
            self._sibling = payload.get("sibling") or {}
            self._paper_row_ids = {k: v for k, v in
                                   (payload.get("paper_rows") or {}).items()}
            self._live_row_ids = {k: v for k, v in
                                  (payload.get("live_rows") or {}).items()}
            self._group_id = payload.get("group_id")
            self._token_by_sym = payload.get("meta") or {}
            write_audit_log(f"[TSG][RESTORE] same-day session restored "
                            f"(state={self._core.state if self._core else '-'})")
        except Exception as e:
            write_audit_log(f"[TSG][RESTORE_FAIL] {e!r} — starting clean")

    # ── TSG_STALE_SWEEP BEGIN ──────────────────────────────────────────
    def _sweep_stale_live_rows(self) -> None:
        """Boot-time close of PREVIOUS-DAY TSG rows stuck OPEN in `trades`.

        WHY: a mid-day restart that loses the session file drops
        _live_row_ids, so _book_exit can no longer close those rows.
        They stay OPEN forever and tomorrow's entry then trips
        uniq_open_trade_per_slot on the same TSG_V1_L* slots — every
        insert fails and the day goes invisible again (the exact
        pre-v10.0.9 Analytics blackout, resurrected).

        SCOPE — deliberately conservative:
          * strategy_id = 'TSG_V1' AND exit_time IS NULL AND
            entry_time < today's IST midnight ONLY. TSG holds nothing
            overnight, so any open TSG row from a previous day is
            definitionally stale bookkeeping, never a live position.
          * SAME-DAY orphans are left alone: after a same-day restart
            the broker position may still be live, and closing the row
            would misrepresent it. Broker reconciliation is a separate
            deferred item (same class as IC's).

        Rows are closed via close_trade() (audit trail + double-close
        trigger semantics) with exit_price=None and reason STALE_SWEEP —
        pnl_value renders as None in history; the alert tells the admin
        to reconcile the day's P&L from the Kite orderbook. Fail-open:
        boot must never break on a sweep error.
        """
        try:
            from app.db.sqlite import get_conn   # deferred: keep boot path lean
            midnight = int(_now_ist().replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp())
            conn = get_conn()
            rows = conn.execute(
                """
                SELECT trade_id, slot, symbol, entry_time
                FROM trades
                WHERE strategy_id = ?
                  AND exit_time IS NULL
                  AND entry_time < ?
                """,
                (STRATEGY_ID, midnight),
            ).fetchall()
            if not rows:
                return
            for r in rows:
                tid = r[0]
                close_trade(trade_id=tid, exit_price=None,
                            exit_order_id=None, exit_reason="STALE_SWEEP")
                write_audit_log(
                    f"[TSG][STALE_SWEEP] closed stuck row trade_id={tid} "
                    f"slot={r[1]} sym={r[2]} entry_ts={r[3]}")
            record_alert(
                "TSG_STALE_SWEEP",
                f"TSG_V1: closed {len(rows)} stale open row(s) from a "
                f"previous day (lost session). P&L for those legs is NOT "
                f"recorded — reconcile from the Kite orderbook.",
                severity="warning", strategy_id=STRATEGY_ID, mode="live")
        except Exception as e:
            write_audit_log(f"[TSG][STALE_SWEEP_FAIL] {e!r}")
    # ── TSG_STALE_SWEEP END ────────────────────────────────────────────

    # ── GROUP_ENTRY BEGIN ──────────────────────────────────────────────
    def _notify_group_entry(self, lots: int, is_expiry: bool) -> None:
        """ONE composite Telegram message for the day's 4-leg entry.

        Deferred import: keeps app.api off TSG's boot path (no cycle risk),
        matching the ha_trade_manager / gtt_monitor pattern. Fully
        best-effort — a notification must never break the entry path.
        """
        try:
            from app.api.telegram_api import notify_group_entry
            legs = [{
                "leg_id":      l.leg_id,
                "action":      l.action,
                "opt_type":    l.opt_type,
                "symbol":      l.symbol,
                "strike":      l.strike,
                "qty":         l.qty,
                "entry_price": l.entry_price,
            } for l in self._core.legs.values() if l.state == L_OPEN]
            if not legs:
                return
            risk = []
            if self._core.mtm_sl:
                risk.append(["Group SL", f"-₹{self._core.mtm_sl:,.0f}"])
            if self._core.mtm_target:
                risk.append(["Target", f"+₹{self._core.mtm_target:,.0f}"])
            notify_group_entry({
                "strategy_id":    STRATEGY_ID,   # label = codename (UI_MASK)
                "mode":           "paper" if self._paper else "live",
                "expiry":         self._expiry_iso,
                "lots":           lots,
                "lot_size":       self._lot_size,
                "risk":           risk,
                "note":           "expiry-day sizing" if is_expiry else "",
                "legs":           legs,
            })
        except Exception as e:
            write_audit_log(f"[TSG][TG_ENTRY_FAIL] {e!r}")
    # ── GROUP_ENTRY END ────────────────────────────────────────────────

    # ── GROUP_EXIT BEGIN ───────────────────────────────────────────────
    def _notify_group_exit(self, asked_ids, reason: str) -> None:
        """ONE composite Telegram message per exit batch (was: nothing —
        _notify only reaches the in-app bell, so TSG exits, including
        MTM_SL stop-outs, were Telegram-silent).

        Reports only legs that actually reached CLOSED with a fill, so a
        partial live close reports the truth and flags the remainder.
        """
        try:
            if self._core is None or not asked_ids:
                return
            from app.api.telegram_api import notify_group_exit
            legs, stuck = [], []
            for lid in asked_ids:
                l = self._core.legs.get(lid)
                if l is None:
                    continue
                if l.state == L_OPEN or l.exit_price is None:
                    stuck.append(lid)
                    continue
                legs.append({
                    "leg_id":      l.leg_id,
                    "action":      l.action,
                    "opt_type":    l.opt_type,
                    "symbol":      l.symbol,
                    "strike":      l.strike,
                    "qty":         l.qty,
                    "entry_price": l.entry_price,
                    "exit_price":  l.exit_price,
                    "pnl":         l.pnl(),
                })
            if not legs:
                return
            _r = self._core.realized
            totals = [["Day realized",
                       f"{'-' if _r < 0 else '+'}₹{abs(_r):,.0f}"]]
            still_open = len(self._core.open_ids())
            totals.append(["Book", "FLAT" if still_open == 0
                           else f"{still_open} leg(s) still open"])
            note = ""
            if stuck:
                note = (f"{len(stuck)} leg(s) unconfirmed ({', '.join(stuck)})"
                        f" — retrying next tick")
            notify_group_exit({
                "strategy_id": STRATEGY_ID,   # label = codename (UI_MASK)
                "mode":        "paper" if self._paper else "live",
                "expiry":      self._expiry_iso,
                "reason":      reason,
                "legs":        legs,
                "totals":      totals,
                "note":        note,
                "footer":      "gross, before charges",
            })
        except Exception as e:
            write_audit_log(f"[TSG][TG_EXIT_FAIL] {e!r}")
    # ── GROUP_EXIT END ─────────────────────────────────────────────────

    # ── TG_ALERTS BEGIN ────────────────────────────────────────────────
    def _alert(self, code: str, msg: str, *, severity: str = "warning",
               mode: Optional[str] = None) -> None:
        """record_alert (in-app bell) + a Telegram CRITICAL mirror.

        record_alert is in-app ONLY, so every TSG operational alert —
        including TSG_EXIT_STUCK, an unconfirmed LIVE exit — never reached
        the phone. IC already mirrors its equivalents via notify_critical;
        this brings TSG to parity. Best-effort: the mirror can never break
        the caller.
        """
        m = mode or ("paper" if self._paper else "live")
        record_alert(code, msg, severity=severity,
                     strategy_id=STRATEGY_ID, mode=m)
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"severity": severity, "message": msg,
                             "strategy_id": STRATEGY_ID})
        except Exception as e:
            write_audit_log(f"[TSG][TG_ALERT_FAIL] {code}: {e!r}")
    # ── TG_ALERTS END ──────────────────────────────────────────────────

    # ── notifications (best-effort) ─────────────────────────────────────
    def _notify(self, msg: str) -> None:
        write_audit_log(f"[TSG] {msg}")
        try:
            record_alert("TSG_EVENT", msg, severity="info",
                         strategy_id=STRATEGY_ID,
                         mode="paper" if self._paper else "live")
        except Exception:
            pass