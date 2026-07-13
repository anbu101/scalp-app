# backend/app/engine/pst/pst_sell_paper_manager.py
#
# ── PST_SELL PAPER MANAGER ── (Phase 1 — PAPER-HARDWIRED, D26)
#
# Event-driven mirror of the backtest's pst_sell_engine per-minute logic,
# fed by the tick engine with COMPLETED, boundary-aligned 1m candles.
# D27 fill discipline (backtest parity):
#   ENTRY   : signal minute's candle CLOSE of the selected contract
#             (selection priced at ts−60 via ic_v1_engine.select_strike —
#             the same function the backtest calls).
#   TP      : premium level entry×(1−sl_pct/100); trigger on option LOW <=
#             level; PAPER FILL AT THE LEVEL (simulated resting limit).
#   SPOT_SL : spot ±spot_tg_points; fill at that minute's option CLOSE.
#   Both in one minute → SPOT_SL wins + ambiguous (D4/D20 pessimism).
#   EOD     : last option close strictly before exit_time.
# Monitoring starts at signal_ts+60. One position at a time; busy until
# last_exit+60. Risk: entry-gate only (Phase 1, D28).
#
# The equivalence of this event-driven form to simulate_position_short is
# PROVEN by tests/validate_pst_paper_parity.py (randomized trials against
# the actual engine), not assumed.
#
# API (called by the tick engine / minute coordinator):
#   on_minute(ts, spot_candle, chain)   chain: PSTChainView (below)
#   on_signal(sig)                      from PSTLiveSignalEngine (same minute,
#                                       AFTER on_minute — coordinator ordering)
#   force_eod(ts)                       scheduler safety net
#
# PSTChainView duck-type: candle(symbol, ts)->dict|None,
#                         symbols(side)->list[str],
#                         meta(symbol)->{"strike","expiry"}

from __future__ import annotations

from typing import Dict, List, Optional

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

TABLE = "pst_sell_trades"


class PSTSellPaperManager:
    def __init__(self, cfg: dict, repo: PSTRepo):
        mode = str(cfg.get("trade_execution_mode", "PAPER")).upper()
        if mode != "PAPER":
            # D26: Phase 1 refuses anything but paper — fail closed.
            write_audit_log("[PST_SELL] trade_execution_mode != PAPER — "
                            "Phase 1 is paper-only; manager DISABLED (fail closed)")
            self.disabled = True
        else:
            self.disabled = False
        self.repo = repo
        self.prem_max = float(cfg.get("premium_max", 150) or 150)
        self.legs_cfg = [l for l in (cfg.get("legs") or []) if int(l.get("lots") or 0) > 0]
        self.side_mode = str(cfg.get("side_mode", "BOTH") or "BOTH")
        self.max_tpd = int(cfg.get("max_trades_per_day", 0) or 0)
        self.exit_min = hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
        self.risk = RiskGate(dml=cfg.get("daily_max_loss"), dmp=cfg.get("daily_max_profit"),
                             mml=cfg.get("monthly_max_loss"), mmp=cfg.get("monthly_max_profit"))
        # position state (mirrors simulate_position_short's `state`)
        self.open_legs: List[dict] = []      # {db_id, leg_id, lots, tp, spot_sl, ...}
        self.symbol: Optional[str] = None
        self.side: Optional[str] = None
        self.entry_price: Optional[float] = None
        self.last_close: Optional[float] = None
        self.last_ts: Optional[int] = None
        self.monitor_from: Optional[int] = None
        self.busy_until: int = -1
        self.taken_today: int = 0
        self._day_key: Optional[int] = None
        self.diag = {"signals_taken": 0, "signals_skipped_busy": 0,
                     "signals_skipped_side": 0, "signals_skipped_select": 0,
                     "signals_skipped_cap": 0, "signals_skipped_risk": 0,
                     "signals_skipped_stale": 0, "ambiguous": 0}

    # ── day roll ─────────────────────────────────────────────────────
    def _roll_day(self, ts: int) -> None:
        dk = ist_day_start(ts)
        if dk != self._day_key:
            self._day_key = dk
            self.taken_today = 0

    def _eod_ts(self, ts: int) -> int:
        return ist_day_start(ts) + self.exit_min * 60

    # ── entries ──────────────────────────────────────────────────────
    def on_signal(self, sig: dict, chain) -> None:
        """Coordinator calls this AFTER on_minute for the same completed
        candle, so chain has candles for sig['ts'] and sig['ts']-60."""
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
        if self.side_mode != "BOTH" and sig["side"] != self.side_mode:
            self.diag["signals_skipped_side"] += 1
            return
        if ts < self.busy_until or self.open_legs:
            self.diag["signals_skipped_busy"] += 1
            return
        if self.max_tpd and self.taken_today >= self.max_tpd:
            self.diag["signals_skipped_cap"] += 1
            return
        if ts >= self._eod_ts(ts):
            return
        # SELECTION at ts−60, ENTRY FILL at ts close — backtest parity.
        cands = []
        for sym in chain.symbols(sig["side"]):
            c = chain.candle(sym, ts - 60)
            if c and float(c["close"]) > 0:
                cands.append((sym, float(c["close"])))
        pick = select_strike(cands, self.prem_max)
        if pick is None:
            self.diag["signals_skipped_select"] += 1
            return
        sym = pick[0]
        fill_c = chain.candle(sym, ts)
        if fill_c is None:
            self.diag["signals_skipped_select"] += 1
            return
        entry = float(fill_c["close"])
        spot_entry = float(sig["spot"])
        is_ce = sig["side"] == "CE"
        meta = chain.meta(sym) or {}
        self.symbol, self.side = sym, sig["side"]
        self.entry_price = entry
        self.last_close, self.last_ts = entry, ts
        self.monitor_from = ts + 60
        self.open_legs = []
        for leg in self.legs_cfg:
            tp = max(0.05, entry * (1 - float(leg["sl_pct"]) / 100.0)) \
                if float(leg.get("sl_pct") or 0) > 0 else None
            pts = float(leg.get("spot_tg_points") or 0)
            spot_sl = (spot_entry + pts if is_ce else spot_entry - pts) if pts > 0 else None
            db_id = self.repo.insert_leg(TABLE, {
                "mode": "PAPER", "leg_id": leg["id"], "tradingsymbol": sym,
                "instrument_type": sig["side"], "strike": meta.get("strike"),
                "expiry": meta.get("expiry"), "direction": "SELL",
                "qty": int(leg["lots"]) * LOT_SIZE,
                "entry_ts": ts + 60,          # fill-candle completion (backtest stamp)
                "entry_price": round(entry, 2),
                "sl": None, "tp": (round(tp, 2) if tp is not None else None),
                "spot_entry": spot_entry, "spot_sl": spot_sl,
                "condition": f"{leg['id']}\u00b7{sig['side']}\u00b7{','.join(sig.get('levels_crossed') or [])}",
            })
            self.open_legs.append({"db_id": db_id, "leg_id": leg["id"],
                                   "lots": int(leg["lots"]), "tp": tp,
                                   "spot_sl": spot_sl})
        self.taken_today += 1
        self.diag["signals_taken"] += 1
        write_audit_log(f"[PST_SELL][PAPER] ENTER SHORT {sym} @{entry:.2f} "
                        f"({len(self.open_legs)} legs) sig_ts={ts}")

    # ── per-minute monitoring (mirrors simulate_position_short loop) ──
    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        if self.disabled:
            return
        self._roll_day(ts)
        if not self.open_legs:
            return
        eod = self._eod_ts(ts)
        if ts >= eod:
            self._close_all(self.last_ts, self.last_close, "EOD", False)
            return
        if self.monitor_from is None or ts < self.monitor_from:
            return
        oc = chain.candle(self.symbol, ts)
        if oc is None:
            return                              # engine iterates option candles only
        self.last_close, self.last_ts = float(oc["close"]), ts
        is_ce = self.side == "CE"
        still = []
        for st in self.open_legs:
            hit_tp = st["tp"] is not None and float(oc["low"]) <= st["tp"]
            hit_sl = False
            if st["spot_sl"] is not None and spot_candle is not None:
                hit_sl = (float(spot_candle["high"]) >= st["spot_sl"]) if is_ce \
                    else (float(spot_candle["low"]) <= st["spot_sl"])
            if hit_sl:
                self._close_leg(st, ts, float(oc["close"]), "SPOT_SL", hit_tp)
                if hit_tp:
                    self.diag["ambiguous"] += 1
            elif hit_tp:
                self._close_leg(st, ts, st["tp"], "TP", False)
            else:
                still.append(st)
        self.open_legs = still
        if not self.open_legs:
            self._flat(ts)

    def force_eod(self, ts: int) -> None:
        if not self.disabled and self.open_legs:
            self._close_all(self.last_ts, self.last_close, "EOD", False)

    # ── close paths ──────────────────────────────────────────────────
    def _close_leg(self, st: dict, ts: int, px: float, reason: str, amb: bool) -> None:
        gross, charges, net = leg_net("SELL", self.entry_price, px, st["lots"])
        if st["db_id"] is not None:
            self.repo.close_leg(TABLE, st["db_id"], exit_ts=ts, exit_price=px,
                                exit_reason=reason, ambiguous=amb,
                                pnl=gross, charges=charges, net_pnl=net)
        self.risk.on_close(net, ts)
        write_audit_log(f"[PST_SELL][PAPER] EXIT {st['leg_id']} {self.symbol} "
                        f"@{px:.2f} {reason}{' AMB' if amb else ''} net={net:.0f}")

    def _close_all(self, ts: int, px: float, reason: str, amb: bool) -> None:
        for st in self.open_legs:
            self._close_leg(st, ts, px, reason, amb)
        self.open_legs = []
        self._flat(ts)

    def _flat(self, last_exit_ts: int) -> None:
        self.busy_until = int(last_exit_ts) + 60     # run_day parity
        self.symbol = self.side = None
        self.entry_price = self.monitor_from = None