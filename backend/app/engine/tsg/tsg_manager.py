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
        self._lot_size = 65
        self._restore_session()

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
                record_alert("TSG_MAX_LOSS",
                             "TSG_V1 max-loss guard active — NO ENTRY today",
                             severity="warning", strategy_id=STRATEGY_ID,
                             mode=mode.lower())
                self._entry_date = today
                return False
        except Exception:
            pass

        expiry, rows, ltp = chain if chain else (None, [], {})
        if not expiry or not rows:
            record_alert("TSG_NO_CHAIN",
                         "TSG_V1: chain snapshot unavailable — NO ENTRY "
                         "today (fail closed)", severity="warning",
                         strategy_id=STRATEGY_ID, mode=mode.lower())
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
            record_alert("TSG_SKIP",
                         f"TSG_V1 skipped today: {core.skip_reason}",
                         severity="warning", strategy_id=STRATEGY_ID,
                         mode=mode.lower())
            self._persist()
            return False

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
            record_alert("TSG_NO_EXECUTOR",
                         "TSG_V1 LIVE entry impossible — executor missing",
                         severity="error", strategy_id=STRATEGY_ID,
                         mode="live")
            self._core.state = D_ABORTED
            return False
        order = ["L3", "L4", "L1", "L2"]
        for lid in order:
            leg = self._core.legs.get(lid)
            if leg is None:
                continue
            avg = self._place_and_confirm(leg)
            if avg is None:
                for open_id in self._core.leg_entry_dead(lid):
                    self._market_close(self._core.legs[open_id], "UNWIND")
                record_alert("TSG_ENTRY_UNWIND",
                             f"TSG_V1 LIVE entry failed at {lid} — unwound",
                             severity="error", strategy_id=STRATEGY_ID,
                             mode="live")
                return False
            self._core.leg_filled(lid, avg)
        write_audit_log("[TSG][ENTRY][LIVE] day OPEN")
        return True

    def _place_and_confirm(self, leg: TsgLeg) -> Optional[float]:
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
            avg = self._confirm_fill(oid)
            if avg is None:
                try:
                    self.executor.cancel_order(oid)
                except Exception:
                    pass
                return None
            leg.entry_order_id = oid
            return avg if avg > 0 else limit_px
        except Exception as e:
            write_audit_log(f"[TSG][ENTRY][{leg.leg_id}][PLACE_FAIL] {e!r}")
            return None

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
            decision = self._core.evaluate_minute(marks, ivs)
            self._persist()
            if decision is None:
                return
            reason, ids = decision
            self._execute_exits(ids, reason)

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

    def _book_exit(self, leg: TsgLeg, px: float, reason: str) -> None:
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
                record_alert("TSG_EXIT_STUCK",
                             f"TSG_V1 exit unconfirmed for {leg.leg_id} "
                             f"({reason}) — will retry next tick",
                             severity="error", strategy_id=STRATEGY_ID,
                             mode="live")
                return                       # leg stays OPEN → next tick retries
            self._book_exit(leg, avg, reason)
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

    # ── persistence (LD6, ic_carry_store pattern) ───────────────────────
    def _persist(self) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "entry_date": self._entry_date,
                       "paper": self._paper, "expiry": self._expiry_iso,
                       "sibling": self._sibling,
                       "paper_rows": self._paper_row_ids,
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
            self._token_by_sym = payload.get("meta") or {}
            write_audit_log(f"[TSG][RESTORE] same-day session restored "
                            f"(state={self._core.state if self._core else '-'})")
        except Exception as e:
            write_audit_log(f"[TSG][RESTORE_FAIL] {e!r} — starting clean")

    # ── notifications (best-effort) ─────────────────────────────────────
    def _notify(self, msg: str) -> None:
        write_audit_log(f"[TSG] {msg}")
        try:
            record_alert("TSG_EVENT", msg, severity="info",
                         strategy_id=STRATEGY_ID,
                         mode="paper" if self._paper else "live")
        except Exception:
            pass