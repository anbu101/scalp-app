# backend/app/engine/orb/orb_live_core.py
#
# ── ORB_V1 LIVE CORE ── pure decision core for paper/live "Outrider".
#
# Fence: ORB_LIVE_20260903
#
# DOCTRINE (docs/strategy_checklist.md, VET_V1 donor notes): parity by
# construction — the live core does not re-implement the strategy, it
# RE-RUNS the backtest's own primitives (resample_1m, compute_orb,
# orb_signals, spot_breached, prem_levels) over the growing 1-minute day
# prefix, and a PrefixGuard freezes the day on any restated bar or any
# mutation of the already-emitted signal stream. No app imports beyond the
# backtest engine module (single source of truth); no DB, no clock, no
# config singletons. The manager owns fills, orders and persistence.
#
# ── LIVE PARITY CONTRACT (LD-sheet, 2026-09-03) ──────────────────────────
#   LD1  Decisions ONLY at completed 1m bars. process() accepts a bar whose
#        ts is the MINUTE-START of the just-completed minute and must be
#        60s-aligned — unaligned ts is a caller bug and raises (the VET
#        "gross 0" scar: ChainStore probes step in exact 60s increments).
#   LD2  Entry: a signal emitted at bar ts fills at the NEXT 1m open. The
#        manager samples the option chain at the signal bar's close (the
#        bar ENDING at the fill minute is the selection instant) and buys
#        at the next open — byte-matching backtest R1/R2.
#   LD3  Stop: spot_sl_trigger=close — evaluated ONCE per completed spot
#        bar via the backtest's own spot_breached(trigger="close"); a
#        closing breach means market-sell at the next 1m open. No tick
#        monitor, no GTT: a spot-close-conditional stop cannot be a broker
#        order, and the GTT-race scar (duplicate exits) forbids dual
#        executors anyway.
#   LD4  Target: resting LIMIT SELL at entry_premium x (1 + target/100),
#        placed at entry, cancelled (abort-before-flatten) before any
#        engine exit. Backtest books AT the level on a touch; a resting
#        limit is its live twin. Divergence ledger: a gap THROUGH the
#        level fills the limit at-or-better vs backtest's at-the-open —
#        live can only be >= backtest here.
#   LD5  EOD 13:00 engine square-off (own job as backstop). 13:00 < the
#        generic 15:25 sweep, so NO squareoff exemption — the generic
#        sweep remains a harmless catastrophe backstop (checklist 2.10).
#   LD6  Budgets in-core: max 2/day, 1/side, one position at a time;
#        signals while a position is open are dropped and counted
#        (runner's sig_dropped_open).
#   LD7  Day gates: expected weekly expiry only (uncovered day skipped by
#        the manager); ORB window with ANY missing bucket refuses the day
#        (fail-closed, backtest-identical); NSE calendar gating at cron
#        AND engine (exits fail open, entries fail closed).
#   LD8  Trade storage: generic paper_trades (TSG-style) — checklist 2.9,
#        2.12 become no-ops by construction.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from app.backtest.orb.orb_v1_engine import (
        OrbBar, resample_1m, compute_orb, orb_signals, spot_breached,
        prem_levels, spot_sl_level, SESSION_OPEN_MIN)
except ImportError:                                        # standalone tests
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "backtest", "orb"))
    from orb_v1_engine import (  # type: ignore
        OrbBar, resample_1m, compute_orb, orb_signals, spot_breached,
        prem_levels, spot_sl_level, SESSION_OPEN_MIN)


class PrefixGuard:
    """Fail-closed stability guard (VET doctrine, adapted to ORB).

    Two invariants, both prefix-stable by construction and GUARDED anyway:
      1. A completed 1m spot bar, once seen, must never be restated with
         different OHLC (a re-delivered identical bar is idempotent).
      2. The signal stream produced by re-running orb_signals over the
         prefix must only ever APPEND — an earlier signal changing ts,
         side or flags means the inputs are unreliable.
    On violation the guard FREEZES: the caller must stop trading the day.
    """

    def __init__(self) -> None:
        self.bars: Dict[int, Tuple[float, float, float, float]] = {}
        self.sig_seen: List[Tuple[int, str, bool, bool]] = []
        self.frozen: bool = False
        self.reason: Optional[str] = None

    def _freeze(self, why: str) -> bool:
        self.frozen, self.reason = True, why
        return False

    def check_bar(self, b: OrbBar) -> bool:
        if self.frozen:
            return False
        key = (b.open, b.high, b.low, b.close)
        prev = self.bars.get(b.ts)
        if prev is not None and prev != key:
            return self._freeze(f"bar {b.ts} restated {prev} -> {key}")
        self.bars[b.ts] = key
        return True

    def check_signals(self, sigs) -> bool:
        if self.frozen:
            return False
        now = [(s.ts, s.side, s.ambiguous, s.rearm_entry) for s in sigs]
        if now[:len(self.sig_seen)] != self.sig_seen:
            return self._freeze("signal stream mutated (not append-only)")
        self.sig_seen = now
        return True


@dataclass
class OrbPosition:
    side: str
    symbol: str
    entry_px: float
    entry_spot: float
    entry_ts: int
    sl_spot: float
    tp_prem: float


@dataclass
class OrbLiveDay:
    """One trading day of ORB_V1 decisions. The manager feeds completed 1m
    SPOT bars in order; the core answers with a list of action tuples:

      ("DAY_REFUSED", reason)        fail-closed; nothing will trade today
      ("LEVELS", high, low)          ORB window complete, levels locked
      ("SIGNAL", side, sig_ts)       buy `side` at the NEXT 1m open (LD2)
      ("STOP_CLOSE_BREACH", ts)      cancel TP limit, market-sell (LD3)
      ("EOD_SQUARE_OFF", ts)         cancel TP limit, market-sell (LD5)
      ("FROZEN", reason)             PrefixGuard tripped — flatten & stop

    Fills flow back via on_entry_fill / on_position_closed. The core never
    talks to brokers, DBs or clocks."""
    day_start_epoch: int
    cfg: dict
    prefix: List[OrbBar] = field(default_factory=list)
    guard: PrefixGuard = field(default_factory=PrefixGuard)
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    refused: Optional[str] = None
    consumed_sigs: int = 0
    day_trades: int = 0
    side_trades: Dict[str, int] = field(default_factory=lambda: {"CE": 0, "PE": 0})
    dropped_open: int = 0
    dropped_budget: int = 0
    dropped_block: int = 0
    pending_side: Optional[str] = None      # SIGNAL emitted, fill not confirmed
    position: Optional[OrbPosition] = None
    eod_emitted: bool = False
    frozen_reported: bool = False

    # ── derived once ──
    def _m(self, ts: int) -> int:
        return (ts - self.day_start_epoch) // 60

    @property
    def _orb_end_min(self) -> int:
        return SESSION_OPEN_MIN + int(self.cfg["orb_minutes"])

    @property
    def _block_min(self) -> int:
        h, m = str(self.cfg["entry_block_time"]).split(":")
        return int(h) * 60 + int(m)

    @property
    def _eod_min(self) -> int:
        h, m = str(self.cfg["eod_square_off"]).split(":")
        return int(h) * 60 + int(m)

    def process(self, bar: OrbBar) -> List[tuple]:
        """Feed ONE completed 1m spot bar (ts = minute START, aligned)."""
        if bar.ts % 60 != 0:
            raise ValueError(f"unaligned bar ts {bar.ts} — completed-minute "
                             "START epochs only (LD1)")
        out: List[tuple] = []
        if self.refused:
            return out
        if not self.guard.check_bar(bar):
            if not self.frozen_reported:
                self.frozen_reported = True
                out.append(("FROZEN", self.guard.reason))
            return out
        if self.prefix and bar.ts == self.prefix[-1].ts:
            return out                                    # idempotent redeliver
        if self.prefix and bar.ts < self.prefix[-1].ts:
            self.guard._freeze(f"bar ts regression {bar.ts}")
            if not self.frozen_reported:
                self.frozen_reported = True
                out.append(("FROZEN", self.guard.reason))
            return out
        self.prefix.append(bar)
        mod = self._m(bar.ts)

        # ── ORB window: lock levels at the first bar AT/after orb end ──
        if self.orb_high is None and mod >= self._orb_end_min:
            tf = int(self.cfg["timeframe_minutes"])
            bars_tf = resample_1m(self.prefix, day_start_epoch=self.day_start_epoch,
                                  tf_minutes=tf)
            orb = compute_orb(bars_tf, day_start_epoch=self.day_start_epoch,
                              orb_minutes=int(self.cfg["orb_minutes"]),
                              tf_minutes=tf)
            if orb is None:
                self.refused = "ORB window incomplete — day refused (fail-closed)"
                out.append(("DAY_REFUSED", self.refused))
                return out
            self.orb_high, self.orb_low = orb
            out.append(("LEVELS", self.orb_high, self.orb_low))

        # ── position exits BEFORE new signals (runner ladder order) ──
        if self.position is not None and not self.eod_emitted:
            if mod >= self._eod_min:
                self.eod_emitted = True
                out.append(("EOD_SQUARE_OFF", bar.ts))
            elif spot_breached(side=self.position.side,
                               sl_level=self.position.sl_spot, spot_bar=bar,
                               trigger=str(self.cfg.get("spot_sl_trigger",
                                                        "close"))):
                out.append(("STOP_CLOSE_BREACH", bar.ts))

        # ── signal stream: re-run the backtest's own detector on the prefix ──
        if self.orb_high is not None:
            sigs = orb_signals(
                self.prefix, day_start_epoch=self.day_start_epoch,
                orb_high=self.orb_high, orb_low=self.orb_low,
                orb_minutes=int(self.cfg["orb_minutes"]),
                tf_minutes=int(self.cfg["timeframe_minutes"]),
                trigger_source=str(self.cfg.get("trigger_source", "high")),
                breakout_buffer_pts=float(self.cfg.get("breakout_buffer_pts", 0)),
                direction=str(self.cfg.get("direction", "BOTH")),
                both_side_policy=str(self.cfg.get("both_side_policy",
                                                  "pessimistic")))
            if not self.guard.check_signals(sigs):
                if not self.frozen_reported:
                    self.frozen_reported = True
                    out.append(("FROZEN", self.guard.reason))
                return out
            for s in sigs[self.consumed_sigs:]:
                self.consumed_sigs += 1
                entry_min = self._m(s.ts) + 1                       # LD2
                if self.position is not None or self.pending_side is not None:
                    self.dropped_open += 1
                    continue
                if self.day_trades >= int(self.cfg["max_trades_per_day"]):
                    self.dropped_budget += 1
                    continue
                if self.side_trades[s.side] >= int(self.cfg["max_trades_per_side"]):
                    self.dropped_budget += 1
                    continue
                if entry_min >= self._block_min or entry_min >= self._eod_min:
                    self.dropped_block += 1
                    continue
                self.pending_side = s.side
                out.append(("SIGNAL", s.side, s.ts))
        return out

    # ── manager callbacks ──
    def on_entry_fill(self, *, side: str, symbol: str, entry_px: float,
                      entry_spot: float, entry_ts: int) -> OrbPosition:
        """Called after the option buy fills at the next 1m open (LD2).
        Computes the sealed stop/target levels with the backtest's own
        arithmetic and arms the position."""
        assert self.pending_side == side, "fill without a pending signal"
        v = float(self.cfg["sl_points"])
        eff = (entry_spot * v / 100.0
               if str(self.cfg.get("sl_dist_mode", "pts")) == "pct" else v)
        sl_spot = spot_sl_level(side=side,
                                mode=str(self.cfg.get("spot_sl_mode", "points")),
                                orb_high=self.orb_high, orb_low=self.orb_low,
                                entry_spot=entry_spot, sl_points=eff)
        tp, _ = prem_levels(entry_px=entry_px,
                            target_mode=str(self.cfg.get("target_mode", "pct")),
                            target_value=float(self.cfg["target_value"]),
                            sl_prem_mode="off", sl_prem_value=0.0)
        self.position = OrbPosition(side=side, symbol=symbol, entry_px=entry_px,
                                    entry_spot=entry_spot, entry_ts=entry_ts,
                                    sl_spot=sl_spot, tp_prem=tp)
        self.pending_side = None
        self.day_trades += 1
        self.side_trades[side] += 1
        return self.position

    def on_entry_abandoned(self) -> None:
        """No candidate in band / no fill — release the pending slot
        WITHOUT consuming budget (runner's sig_no_candidate path)."""
        self.pending_side = None

    def on_position_closed(self) -> None:
        self.position = None
