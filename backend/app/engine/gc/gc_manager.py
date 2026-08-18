# backend/app/engine/gc/gc_manager.py
#
# ── GC_V1 MANAGER ── impure wrapper around gc_live_core (which owns every
# DECISION). This file owns: executor calls (paper + live), paper_trades
# rows (generic table — LD3), cap evaluation with live quotes, persistence
# (~/.scalp-app/state/GC_V1_session.json on every transition — LD11), and
# square-off (manual / EOD backstop / kill adapter).
#
# Execution doctrine (TSG donor, simplified): entries are HEDGE-FIRST,
# all-or-unwind — a failed MAIN leg after a filled hedge unwinds the hedge
# and skips the entry; a naked short can never appear where a hedged one
# was configured (LD8). Exits are MAIN-FIRST (buy back the short before
# selling the hedge — margin-safe ordering). No repeg machinery in v1
# (divergence ledger: single placement + fill-poll window).

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade


# ── ACC2_GCDIAG ── map the engine's diag counters to a plain-English
# reason for a no-entry day. Order matters: earliest gate first.
def _derive_skip_reason(diag: dict):
    if not diag:
        return None
    if diag.get("entries"):
        return None
    if diag.get("no_c1"):
        return "no C1 candle in session scope (no candles reached the engine)"
    if diag.get("c1_range_no_ref"):
        return "C1 volatility gate: prev_close reference missing (fail-closed)"
    if diag.get("c1_range_skip"):
        return (f"C1 volatility gate: C1 range "
                f"{diag.get('c1_range_pts')} pts exceeded the limit")
    if diag.get("cutoff_blocked_entries"):
        return (f"entry cutoff blocked "
                f"{diag['cutoff_blocked_entries']} touch(es)")
    if diag.get("armed_no_retrace"):
        return "armed but no retrace/trigger"
    if diag.get("no_breakout"):
        return "no breakout of C1 range"
    return None

from app.engine.gc.gc_live_core import (
    STRATEGY_ID, LOT_SIZE, norm_live_cfg, engine_cfg_for_day,
    replay_and_diff, stable_history_check, plan_legs, combined_open_mtm,
    cap_cut, cut_halts_day, to_tf_candles, Action, LegPlan)

IST = timezone(timedelta(minutes=330))
STATE_DIR = Path.home() / ".scalp-app" / "state"
STATE_FILE = STATE_DIR / "GC_V1_session.json"
FILL_TIMEOUT_S = 12


def _now_ist() -> datetime:
    return datetime.now(IST)


class GcManager:
    """One instance per process; runtime feeds candles, panel reads
    snapshot(). All mutation under one lock."""

    def __init__(self, executor=None, quote_fn: Optional[Callable] = None):
        self.last_diag = {}   # ── ACC2_GCDIAG ── always present
        self._lock = threading.RLock()
        self.executor = executor
        self.quote_fn = quote_fn            # (symbols)->{sym: ltp}
        self.day_date: Optional[str] = None
        self.prev_tail: list = []
        self.prev_close: Optional[float] = None
        self.chain_rows: List[dict] = []    # [{symbol, opt_type, token}]
        self.executed: List[dict] = []      # core parity ledger
        self.entries = 0
        self.exits = 0
        self.position: List[dict] = []      # open legs (see _leg_row)
        self.day_realized = 0.0             # gross ₹ (backtest-parity marks)
        self.halted = False
        self.halt_reason: Optional[str] = None
        self.eod_done = False
        self.skip_reason: Optional[str] = None
        self.last_minute_ts: Optional[int] = None
        self.trades_log: List[dict] = []    # closed pairs (panel)
        self._restore()

    # ── config / mode ────────────────────────────────────────────────────
    def cfg(self) -> dict:
        try:
            return norm_live_cfg(load_strategy_config(STRATEGY_ID) or {})
        except Exception:
            return norm_live_cfg({})

    def mode(self) -> str:
        try:
            raw = (load_strategy_config(STRATEGY_ID) or {})
            return str(raw.get("trade_execution_mode", "OFF")).upper()
        except Exception:
            return "OFF"

    # ── day lifecycle (runtime calls) ────────────────────────────────────
    def arm_day(self, day_iso: str, prev_tail, prev_close,
                chain_rows: List[dict]):
        with self._lock:
            if self.day_date != day_iso:
                self.day_date = day_iso
                self.executed = []
                self.entries = self.exits = 0
                self.position = []
                self.day_realized = 0.0
                self.halted = False
                self.halt_reason = None
                self.eod_done = False
                self.skip_reason = None
                self.last_diag = {}   # ── ACC2_GCDIAG ──
                self.trades_log = []
            self.prev_tail = prev_tail
            self.prev_close = prev_close
            self.chain_rows = chain_rows or []
            self._persist()
            write_audit_log(f"[GC][ARM] {day_iso} prev_close={prev_close} "
                            f"chain={len(self.chain_rows)} mode={self.mode()}")

    def teardown_day(self):
        with self._lock:
            if self.position:
                self.square_off_all("EOD")
            self.eod_done = True
            self._persist()

    # ── the 1m tick (runtime calls at each closed minute) ────────────────
    def on_minute(self, candle_rows: List[dict], day_start_epoch: int):
        mode = self.mode()
        if mode not in ("PAPER", "LIVE"):
            return
        with self._lock:
            if self.halted and not self.position:
                return
            if self.eod_done:
                return
            cfg = self.cfg()
            candles = to_tf_candles(candle_rows)
            if not candles:
                # ── ACC2_GCDIAG ── silent return before; if the data path
                # yields nothing the engine can never signal, and the day
                # looked identical to "no setup". Log once per day.
                if not getattr(self, "_no_candle_logged", False):
                    self._no_candle_logged = True
                    write_audit_log(f"[GC][NO_CANDLES] rows_in="
                                    f"{len(candle_rows or [])} — engine idle")
                return
            self._no_candle_logged = False
            if self.last_minute_ts == candles[-1].ts and not self.position:
                return
            self.last_minute_ts = candles[-1].ts
            ecfg = engine_cfg_for_day(cfg, day_start_epoch, self.prev_close)

            sim, acts = replay_and_diff(candles, self.prev_tail, ecfg,
                                        self.entries, self.exits)
            # ── ACC2_GCDIAG 20260818 ────────────────────────────────────
            # simulate_gc_day() NEVER returns a "skip_reason" key (all four
            # of its return sites emit {"trades", "diag"}), so the previous
            # `sim.get("skip_reason")` branch was dead and state always
            # persisted skip_reason=null — a skipped day looked identical to
            # a broken engine. The engine's own `diag` carries the truth;
            # capture it, and derive a human skip reason from it.
            self.last_diag = sim.get("diag") or {}
            derived = _derive_skip_reason(self.last_diag)
            if derived and derived != self.skip_reason:
                self.skip_reason = derived
                write_audit_log(f"[GC][SKIP] {derived} diag={self.last_diag}")
                self._persist()
            if sim.get("skip_reason") and not self.skip_reason:
                self.skip_reason = sim["skip_reason"]
                self._persist()
            err = stable_history_check(sim["trades"], self.executed)
            if err:
                # vendor rewrote the candle history under us — the brain
                # diverged; flatten and halt the day (LD6 tripwire).
                write_audit_log(f"[GC][HISTORY_DIVERGED] {err} — halting day")
                self.square_off_all("HISTORY_DIVERGED")
                self.halted = True
                self.halt_reason = "HISTORY_DIVERGED"
                self._persist()
                return

            for a in acts:
                if a.kind == "ENTER" and not self.halted:
                    self._do_enter(a, cfg, mode)
                elif a.kind == "EXIT" and self.position:
                    self._do_exit(a.exit_reason or "SL", mode)

            # cap book at this close (only while a position is open)
            if self.position:
                marks = self._marks()
                mtm = combined_open_mtm(self.position, marks) \
                    if marks else None
                if mtm is not None:
                    cut = cap_cut(day_realized=self.day_realized,
                                  open_mtm=mtm, cfg=cfg)
                    if cut:
                        self._do_exit(cut, mode)
                        if cut_halts_day(cut):
                            self.halted = True
                            self.halt_reason = cut
            self._persist()

    # ── entry / exit ─────────────────────────────────────────────────────
    def _chain_for_core(self):
        ltps = self._marks([r["symbol"] for r in self.chain_rows])
        return [(r["symbol"], r["opt_type"], float(ltps.get(r["symbol"]) or 0))
                for r in self.chain_rows]

    def _marks(self, symbols: Optional[List[str]] = None) -> Dict[str, float]:
        syms = symbols if symbols is not None \
            else [l["symbol"] for l in self.position]
        if not syms or self.quote_fn is None:
            return {}
        try:
            return self.quote_fn(syms) or {}
        except Exception as e:
            write_audit_log(f"[GC][QUOTE_FAIL] {e!r}")
            return {}

    def _do_enter(self, a: Action, cfg: dict, mode: str):
        legs, reason = plan_legs(signal_side=a.signal_side, cfg=cfg,
                                 chain=self._chain_for_core())
        if not legs:
            write_audit_log(f"[GC][NO_ENTRY] seq={a.trade_seq} {reason}")
            # the core replay believes this trade exists; record it so the
            # parity ledger stays aligned (an unfillable entry is executed-
            # as-skip: counted, position empty).
            self.entries += 1
            self.exits += 1                 # nothing to exit later
            self.executed.append({"entry_ts": a.ts,
                                  "signal_side": a.signal_side,
                                  "skipped": reason})
            return
        tok = {r["symbol"]: r.get("token") or 0 for r in self.chain_rows}
        tag = "GC" if a.flip_seq == 0 else f"GC·FLIP{a.flip_seq}"
        opened: List[dict] = []
        for lp in legs:                     # HEDGE FIRST (core ordering)
            row = self._open_leg(lp, tok.get(lp.symbol, 0), a, tag, mode)
            if row is None:
                for r in opened:            # all-or-unwind
                    self._close_leg(r, r["entry_price"], "UNWIND", mode)
                write_audit_log(f"[GC][ENTRY_UNWIND] {tag} at {lp.symbol}")
                self.entries += 1
                self.exits += 1
                self.executed.append({"entry_ts": a.ts,
                                      "signal_side": a.signal_side,
                                      "skipped": "ENTRY_UNWIND"})
                return
            opened.append(row)
        self.position = opened
        self.entries += 1
        self.executed.append({"entry_ts": a.ts,
                              "signal_side": a.signal_side, "tag": tag})
        write_audit_log(f"[GC][ENTRY][{mode}] {tag} "
                        + " + ".join(f"{l['action']} {l['symbol']}"
                                     f"@{l['entry_price']}" for l in opened))

    def _open_leg(self, lp: LegPlan, token: int, a: Action, tag: str,
                  mode: str) -> Optional[dict]:
        entry_px = lp.ltp
        if mode == "LIVE":
            fill = self._live_place(lp)
            if fill is None:
                return None
            entry_px = fill
        pid = f"GC-{uuid.uuid4().hex[:10]}"
        try:
            insert_paper_trade(
                paper_trade_id=pid, strategy_name=STRATEGY_ID,
                trade_mode=mode, symbol=lp.symbol, token=int(token or 0),
                side=("CE" if lp.symbol.endswith("CE") else "PE"),
                entry_price=float(entry_px), candle_ts=int(a.ts),
                sl_price=float(a.sl_level), tp_price=0.0, rr=0.0,
                lots=int(lp.qty // LOT_SIZE), lot_size=LOT_SIZE,
                qty=int(lp.qty),
                trade_direction=("LONG" if lp.action == "BUY" else "SHORT"))
        except Exception as e:
            write_audit_log(f"[GC][BOOK_FAIL] {lp.symbol}: {e!r}")
            if mode == "LIVE":
                # position exists at the broker but not in the book —
                # flatten immediately rather than run an unbooked leg.
                self._live_flatten(lp.action, lp.symbol, lp.qty)
            return None
        return {"paper_trade_id": pid, "symbol": lp.symbol,
                "token": int(token or 0), "action": lp.action,
                "role": lp.role, "entry_price": float(entry_px),
                "qty": int(lp.qty), "tag": tag, "entry_ts": int(a.ts)}

    def _live_place(self, lp: LegPlan) -> Optional[float]:
        if self.executor is None:
            write_audit_log("[GC][NO_EXECUTOR] LIVE entry impossible")
            return None
        try:
            if lp.action == "SELL":
                out = self.executor.place_sell_entry(
                    symbol=lp.symbol, token=None, qty=lp.qty)
                oid = out[0] if isinstance(out, (tuple, list)) else out
            else:
                out = self.executor.place_buy(lp.symbol, None, lp.qty)
                oid = out[0] if isinstance(out, (tuple, list)) else out
            t0 = time.time()
            while time.time() - t0 < FILL_TIMEOUT_S:
                st = {}
                try:
                    st = self.executor.get_order_fill(oid) or {}
                except Exception:
                    pass
                status = (st.get("status") or "").upper()
                if status == "COMPLETE":
                    return float(st.get("avg") or st.get("average_price")
                                 or lp.ltp)
                if status in ("REJECTED", "CANCELLED"):
                    write_audit_log(f"[GC][ORDER_{status}] {lp.symbol}")
                    return None
                time.sleep(1.0)
            write_audit_log(f"[GC][ORDER_TIMEOUT] {lp.symbol} oid={oid}")
            return None
        except Exception as e:
            write_audit_log(f"[GC][PLACE_FAIL] {lp.symbol}: {e!r}")
            return None

    def _live_flatten(self, entry_action: str, symbol: str, qty: int):
        try:
            if entry_action == "SELL":
                self.executor.place_buy_exit(symbol, qty, "GC_FLATTEN")
            else:
                self.executor.place_market_sell(symbol, qty)
        except Exception as e:
            write_audit_log(f"[GC][FLATTEN_FAIL] {symbol}: {e!r}")

    def _do_exit(self, reason: str, mode: str):
        marks = self._marks()
        # MAIN first (buy back the short before selling the hedge)
        ordered = sorted(self.position,
                         key=lambda l: 0 if l["role"] == "MAIN" else 1)
        for leg in ordered:
            px = float(marks.get(leg["symbol"]) or leg["entry_price"])
            self._close_leg(leg, px, reason, mode)
        pair_pnl = sum(
            ((l["_exit_px"] - l["entry_price"]) if l["action"] == "BUY"
             else (l["entry_price"] - l["_exit_px"])) * l["qty"]
            for l in self.position)
        self.day_realized += pair_pnl
        self.trades_log.append({"tag": self.position[0]["tag"],
                                "reason": reason,
                                "pnl": round(pair_pnl, 2),
                                "legs": [{k: l[k] for k in
                                          ("symbol", "action", "entry_price",
                                           "_exit_px", "qty")}
                                         for l in self.position]})
        self.position = []
        self.exits += 1
        write_audit_log(f"[GC][EXIT][{mode}] {reason} "
                        f"pnl≈{pair_pnl:+.0f} dayGross≈"
                        f"{self.day_realized:+.0f}")

    def _close_leg(self, leg: dict, px: float, reason: str, mode: str):
        if mode == "LIVE" and self.executor is not None:
            self._live_flatten(leg["action"], leg["symbol"], leg["qty"])
        try:
            close_paper_trade(paper_trade_id=leg["paper_trade_id"],
                              exit_price=float(px), exit_reason=reason)
        except Exception as e:
            write_audit_log(f"[GC][CLOSE_BOOK_FAIL] {leg['symbol']}: {e!r}")
        leg["_exit_px"] = float(px)

    # ── square-off (manual / EOD backstop / kill) ────────────────────────
    def square_off_all(self, reason: str) -> int:
        with self._lock:
            if not self.position:
                return 0
            n = len(self.position)
            self._do_exit(reason, self.mode())
            self._persist()
            return n

    def kill_adapter(self) -> dict:
        n = self.square_off_all("KILL")
        with self._lock:
            self.halted = True
            self.halt_reason = "KILL"
            self._persist()
        return {"ok": True, "flattened_legs": n}

    # ── persistence (LD11) ───────────────────────────────────────────────
    def _persist(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            snap = {"day_date": self.day_date, "executed": self.executed,
                    "entries": self.entries, "exits": self.exits,
                    "position": [{k: v for k, v in l.items()}
                                 for l in self.position],
                    "day_realized": self.day_realized,
                    "halted": self.halted, "halt_reason": self.halt_reason,
                    "eod_done": self.eod_done,
                    "skip_reason": self.skip_reason,
                    "last_diag": getattr(self, "last_diag", {}),
                    "prev_close": self.prev_close,
                    "trades_log": self.trades_log[-40:]}
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap))
            tmp.replace(STATE_FILE)
        except Exception as e:
            write_audit_log(f"[GC][PERSIST_FAIL] {e!r}")

    def _restore(self):
        try:
            if not STATE_FILE.exists():
                return
            snap = json.loads(STATE_FILE.read_text())
            if snap.get("day_date") != _now_ist().date().isoformat():
                return                       # stale day — fresh start
            for k in ("day_date", "executed", "entries", "exits", "position",
                      "day_realized", "halted", "halt_reason", "eod_done",
                      "skip_reason", "prev_close", "trades_log"):
                if k in snap:
                    setattr(self, k, snap[k])
            write_audit_log(f"[GC][RESTORE] {self.day_date} "
                            f"entries={self.entries} exits={self.exits} "
                            f"open={len(self.position)}")
        except Exception as e:
            write_audit_log(f"[GC][RESTORE_FAIL] {e!r}")

    # ── panel snapshot ───────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            marks = self._marks() if self.position else {}
            mtm = combined_open_mtm(self.position, marks) \
                if self.position else 0.0
            return {"day_date": self.day_date,
                    "entries": self.entries, "exits": self.exits,
                    "open_legs": [
                        {"symbol": l["symbol"], "action": l["action"],
                         "role": l["role"], "entry_price": l["entry_price"],
                         "qty": l["qty"], "tag": l["tag"],
                         "last_mark": marks.get(l["symbol"])}
                        for l in self.position],
                    "open_mtm": mtm, "day_realized": self.day_realized,
                    "halted": self.halted, "halt_reason": self.halt_reason,
                    "eod_done": self.eod_done,
                    "skip_reason": self.skip_reason,
                    "trades": self.trades_log[-12:]}