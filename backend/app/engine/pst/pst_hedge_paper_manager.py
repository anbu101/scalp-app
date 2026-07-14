# backend/app/engine/pst/pst_hedge_paper_manager.py
#
# ── PST_HEDGE PAPER MANAGER ── (Phase 1 — PAPER-HARDWIRED, D26)
#
# Event-driven mirror of pst_hedge_engine v2 (signal-tracked, D17-amended):
#   * dual-side SELECTION at ts−60 (select_strike on BOTH sides — the same
#     function/prices the backtest uses); either side missing → skip signal.
#   * SIGNAL contract: never traded; virtual entry = its close at ts;
#     SIG_TP level = sig_entry×(1−sl_pct/100), trigger on its 1m LOW.
#   * HELD contract (opposite side): BOUGHT at its close at ts; every exit
#     fills at the HELD close of the event minute (or last known held close
#     when the held contract printed no candle that minute — engine gap rule).
#   * SPOT_SL: spot ±spot_tg_points WITH the signal; same-minute collision →
#     SPOT_SL wins + ambiguous. EOD at last held close. Monitoring from
#     ts+60 over the UNION of held/sig candle minutes.
#   * side_mode filters the SIGNAL side (D21). One position at a time; busy
#     until last_exit+60. Risk entry-gate only (Phase 1, D28).
#
# Equivalence to simulate_position_hedge is PROVEN by
# tests/validate_pst_paper_parity.py, not assumed.

from __future__ import annotations

from typing import List, Optional

try:
    from app.engine.pst.pst_common import (LOT_SIZE, PSTRepo, RiskGate,
                                           hm_to_min, ist_day_start, leg_net)
except ImportError:  # standalone tests
    from pst_common import (LOT_SIZE, PSTRepo, RiskGate,
                                       hm_to_min, ist_day_start, leg_net)

try:
    from app.backtest.ic.ic_v1_engine import select_strike
except ImportError:
    from ic_v1_engine import select_strike  # type: ignore

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)

TABLE = "pst_hedge_trades"


def _other(side: str) -> str:
    return "PE" if side == "CE" else "CE"


class PSTHedgePaperManager:
    def __init__(self, cfg: dict, repo: PSTRepo, executor=None):
        mode = str(cfg.get("trade_execution_mode", "PAPER")).upper()
        if mode not in ("PAPER", "LIVE"):
            write_audit_log(f"[PST_HEDGE] unknown trade_execution_mode {mode} — "
                            f"manager DISABLED (fail closed)")
            self.disabled = True
        else:
            self.disabled = False
        self.mode = mode
        if executor is None:
            try:
                from app.engine.pst.pst_order_executor import PaperExecutor
            except ImportError:  # standalone tests
                from pst_order_executor import PaperExecutor
            executor = PaperExecutor()
        self.exec = executor
        if mode == "LIVE" and getattr(executor, "is_paper", True):
            write_audit_log("[PST_HEDGE] LIVE mode without a LiveExecutor — "
                            "manager DISABLED (fail closed)")
            self.disabled = True
        self.repo = repo
        self.prem_max = float(cfg.get("premium_max", 150) or 150)
        self.legs_cfg = [l for l in (cfg.get("legs") or []) if int(l.get("lots") or 0) > 0]
        self.side_mode = str(cfg.get("side_mode", "BOTH") or "BOTH")
        self.max_tpd = int(cfg.get("max_trades_per_day", 0) or 0)
        self.exit_min = hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
        self.risk = RiskGate(dml=cfg.get("daily_max_loss"), dmp=cfg.get("daily_max_profit"),
                             mml=cfg.get("monthly_max_loss"), mmp=cfg.get("monthly_max_profit"))
        # position state (mirrors simulate_position_hedge's `state`)
        self.open_legs: List[dict] = []
        self.sig_side: Optional[str] = None
        self.held_symbol: Optional[str] = None
        self.sig_symbol: Optional[str] = None
        self.held_entry: Optional[float] = None
        self.last_close: Optional[float] = None    # last known HELD close
        self.last_ts: Optional[int] = None
        self.monitor_from: Optional[int] = None
        self.pending: Optional[dict] = None      # staged entry awaiting fill candles
        self.busy_until: int = -1
        self.taken_today: int = 0
        self._day_key: Optional[int] = None
        self.diag = {"signals_taken": 0, "signals_skipped_busy": 0,
                     "signals_skipped_side": 0, "signals_skipped_select": 0,
                     "signals_skipped_cap": 0, "signals_skipped_risk": 0,
                     "signals_skipped_stale": 0, "ambiguous": 0}

    def _roll_day(self, ts: int) -> None:
        dk = ist_day_start(ts)
        if dk != self._day_key:
            self._day_key = dk
            self.taken_today = 0

    def _eod_ts(self, ts: int) -> int:
        return ist_day_start(ts) + self.exit_min * 60

    # ── entries ──────────────────────────────────────────────────────
    def on_signal(self, sig: dict, chain) -> None:
        if self.disabled or not self.legs_cfg:
            return
        ts = int(sig["ts"])
        self._roll_day(ts)
        if sig.get("stale"):
            self.diag["signals_skipped_stale"] += 1
            return
        if self.risk.blocked(ts):
            self.diag["signals_skipped_risk"] += 1
            return
        if self.side_mode != "BOTH" and sig["side"] != self.side_mode:   # D21: SIGNAL side
            self.diag["signals_skipped_side"] += 1
            return
        if ts < self.busy_until or self.open_legs or self.pending:
            self.diag["signals_skipped_busy"] += 1
            return
        if self.max_tpd and self.taken_today >= self.max_tpd:
            self.diag["signals_skipped_cap"] += 1
            return
        if ts >= self._eod_ts(ts):
            return

        def pick(side: str):
            cands = []
            for sym in chain.symbols(side):
                c = chain.candle(sym, ts - 60)
                if c and float(c["close"]) > 0:
                    cands.append((sym, float(c["close"])))
            p = select_strike(cands, self.prem_max)
            return p[0] if p is not None else None

        sig_sym = pick(sig["side"])
        held_sym = pick(_other(sig["side"])) if sig_sym else None
        if sig_sym is None or held_sym is None:         # fail closed per signal
            self.diag["signals_skipped_select"] += 1
            return
        # ── TWO-PHASE ENTRY (backtest timeline) ── fill candles for minute
        # ts do not exist yet; stage now, fill when minute ts completes.
        self.pending = {"sig": dict(sig), "sig_symbol": sig_sym,
                        "held_symbol": held_sym, "fill_ts": ts}
        return

    def _complete_pending(self, chain) -> None:
        pend = self.pending
        self.pending = None
        sig = pend["sig"]
        ts = int(sig["ts"])
        sig_sym, held_sym = pend["sig_symbol"], pend["held_symbol"]
        sfc = chain.candle(sig_sym, ts)
        hfc = chain.candle(held_sym, ts)
        if sfc is None or hfc is None:         # backtest: fill None → skip
            self.diag["signals_skipped_select"] += 1
            return
        sig_entry = float(sfc["close"])
        if self.exec.is_paper:
            held_entry = float(hfc["close"])   # model fill — backtest parity
        else:
            total_qty = sum(int(l["lots"]) for l in self.legs_cfg) * LOT_SIZE
            held_entry, _oid = self.exec.market(held_sym, "BUY", total_qty,
                                                model_price=float(hfc["close"]))
            if held_entry is None:
                self.diag["signals_skipped_select"] += 1
                return
        spot_entry = float(sig["spot"])
        is_ce_sig = sig["side"] == "CE"
        meta = chain.meta(held_sym) or {}
        self.sig_side, self.sig_symbol = sig["side"], sig_sym
        self.held_symbol, self.held_entry = held_sym, held_entry
        self.last_close, self.last_ts = held_entry, ts
        self.monitor_from = ts + 60
        self.open_legs = []
        for leg in self.legs_cfg:
            tp_level = max(0.05, sig_entry * (1 - float(leg["sl_pct"]) / 100.0)) \
                if float(leg.get("sl_pct") or 0) > 0 else None
            pts = float(leg.get("spot_tg_points") or 0)
            spot_sl = (spot_entry + pts if is_ce_sig else spot_entry - pts) if pts > 0 else None
            db_id = self.repo.insert_leg(TABLE, {
                "mode": "PAPER", "leg_id": leg["id"], "tradingsymbol": held_sym,
                "instrument_type": _other(sig["side"]), "strike": meta.get("strike"),
                "expiry": meta.get("expiry"), "direction": "BUY",
                "qty": int(leg["lots"]) * LOT_SIZE,
                "entry_ts": ts + 60,
                "entry_price": round(held_entry, 2),
                "sl": None,
                "tp": (round(tp_level, 2) if tp_level is not None else None),
                "sig_symbol": sig_sym, "sig_entry": round(sig_entry, 2),
                "spot_entry": spot_entry, "spot_sl": spot_sl,
                "condition": f"{leg['id']}\u00b7{sig['side']}\u00b7{','.join(sig.get('levels_crossed') or [])}",
            })
            self.open_legs.append({"db_id": db_id, "leg_id": leg["id"],
                                   "lots": int(leg["lots"]), "sig_tp": tp_level,
                                   "spot_sl": spot_sl})
        self.taken_today += 1
        self.diag["signals_taken"] += 1
        write_audit_log(f"[PST_HEDGE][PAPER] ENTER LONG {held_sym} @{held_entry:.2f} "
                        f"tracking {sig_sym} (sig_entry {sig_entry:.2f}) sig_ts={ts}")

    # ── per-minute monitoring (mirrors simulate_position_hedge loop) ──
    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        if self.disabled:
            return
        self._roll_day(ts)
        eod = self._eod_ts(ts)
        if self.pending is not None:
            if ts >= eod:
                self.pending = None            # never fill at/after EOD
            elif ts >= self.pending["fill_ts"]:
                self._complete_pending(chain)
        if not self.open_legs:
            return
        if ts >= eod:
            self._close_all(self.last_ts, self.last_close, "EOD", False)
            return
        if self.monitor_from is None or ts < self.monitor_from:
            return
        hc = chain.candle(self.held_symbol, ts)
        sg = chain.candle(self.sig_symbol, ts)
        if hc is None and sg is None:
            return                       # engine iterates the held∪sig union
        if hc is not None:
            self.last_close, self.last_ts = float(hc["close"]), ts
        fill = float(hc["close"]) if hc is not None else self.last_close
        is_ce_sig = self.sig_side == "CE"
        still = []
        for st in self.open_legs:
            hit_tp = (st["sig_tp"] is not None and sg is not None
                      and float(sg["low"]) <= st["sig_tp"])
            hit_sl = False
            if st["spot_sl"] is not None and spot_candle is not None:
                hit_sl = (float(spot_candle["high"]) >= st["spot_sl"]) if is_ce_sig \
                    else (float(spot_candle["low"]) <= st["spot_sl"])
            if hit_sl:
                self._close_leg(st, ts, fill, "SPOT_SL", hit_tp)
                if hit_tp:
                    self.diag["ambiguous"] += 1
            elif hit_tp:
                self._close_leg(st, ts, fill, "SIG_TP", False)
            else:
                still.append(st)
        self.open_legs = still
        if not self.open_legs:
            self._flat(ts)


    # ── restart adoption (same-day OPEN rows) ─────────────────────────
    def adopt_rows(self, rows) -> None:
        if not rows:
            return
        r0 = rows[0]
        self.held_symbol = r0["tradingsymbol"]
        self.sig_symbol = r0["sig_symbol"]
        self.sig_side = "PE" if r0["instrument_type"] == "CE" else "CE"
        self.held_entry = float(r0["entry_price"])
        self.last_close = float(r0["entry_price"])
        self.last_ts = int(r0["entry_ts"]) - 60
        self.monitor_from = int(r0["entry_ts"])
        self.open_legs = [{"db_id": r["id"], "leg_id": r["leg_id"],
                           "lots": int(r["qty"]) // LOT_SIZE,
                           "sig_tp": r["tp"], "spot_sl": r["spot_sl"]}
                          for r in rows]
        write_audit_log(f"[PST_HEDGE] adopted {len(rows)} OPEN leg(s) on "
                        f"{self.held_symbol} (tracking {self.sig_symbol}) after restart")
    def force_eod(self, ts: int) -> None:
        if not self.disabled and self.open_legs:
            self._close_all(self.last_ts, self.last_close, "EOD", False)

    # ── close paths ──────────────────────────────────────────────────
    def _close_leg(self, st: dict, ts: int, px: float, reason: str, amb: bool) -> None:
        if not self.exec.is_paper:
            fill, _oid = self.exec.market(self.held_symbol, "SELL",
                                          int(st["lots"]) * LOT_SIZE,
                                          model_price=px)
            if fill is None:
                st["_still_open"] = True
                return                         # alerted; retry next minute
            px = fill
        st["_still_open"] = False
        gross, charges, net = leg_net("BUY", self.held_entry, px, st["lots"])
        if st["db_id"] is not None:
            self.repo.close_leg(TABLE, st["db_id"], exit_ts=ts, exit_price=px,
                                exit_reason=reason, ambiguous=amb,
                                pnl=gross, charges=charges, net_pnl=net)
        self.risk.on_close(net, ts)
        write_audit_log(f"[PST_HEDGE][PAPER] EXIT {st['leg_id']} {self.held_symbol} "
                        f"@{px:.2f} {reason}{' AMB' if amb else ''} net={net:.0f}")

    def _close_all(self, ts: int, px: float, reason: str, amb: bool) -> None:
        before = list(self.open_legs)
        self.open_legs = []
        for st in before:
            self._close_leg(st, ts, px, reason, amb)
            if st.get("_still_open"):
                self.open_legs.append(st)
        if not self.open_legs:
            self._flat(ts)

    def _flat(self, last_exit_ts: int) -> None:
        self.busy_until = int(last_exit_ts) + 60
        self.sig_side = self.held_symbol = self.sig_symbol = None
        self.held_entry = self.monitor_from = None