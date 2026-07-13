# backend/app/engine/pst/pst_live_signal_engine.py
#
# ── PST LIVE SIGNAL ENGINE ── (Phase 1, paper — change-set B, D23/D24/D27)
#
# THE PARITY DESIGN: this engine contains NO signal logic of its own. At
# every completed 1-minute spot candle it re-runs the REAL backtest signal
# function — app.backtest.pst.pst_v1_engine.build_signals — over the day's
# growing candle prefix (with the prior session as warmup, exactly like the
# backtest runner does) and emits whatever NEW signals appear. Live signals
# therefore equal backtest signals BY CONSTRUCTION, not by careful porting.
# Compute cost is trivial (~750 candles max, runs in low milliseconds, once
# per minute).
#
# VALIDITY CONDITION — PREFIX STABILITY: a signal emitted for a completed
# 3m bar must never change or disappear as later candles append. This is
# proven offline by tests/validate_pst_live_parity.py and GUARDED at
# runtime: if a previously-emitted signal is missing or differs in any
# replay, the engine FREEZES (emits nothing, flags frozen=True) — fail
# closed, never trade on unstable signals.
#
# TRANSPORT-AGNOSTIC: consumes completed 1m spot candle dicts
# ({ts, open, high, low, close}, ts = bar-START epoch, boundary-aligned).
# The tick/WebSocket layer (next delivery) feeds it; the parity harness
# feeds it corpus candles; both exercise identical code.
#
# D27 note: this engine only ever sees COMPLETED candles, so every signal
# is knowable exactly at the minute boundary — the managers act at that
# boundary, mirroring the backtest's fill discipline.

from __future__ import annotations

import copy
from typing import Dict, List, Optional

try:
    from app.backtest.pst.pst_v1_engine import build_signals
except ImportError:
    from pst_v1_engine import build_signals  # standalone tests

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:  # standalone tests / harness
    def write_audit_log(msg: str) -> None:
        print(msg)


class PSTLiveSignalEngine:
    """One instance per trading day serves BOTH PST_SELL and PST_HEDGE
    (identical signal stream — D23). Fail-closed everywhere: no warmup →
    no signals; instability → frozen; malformed candles → rejected."""

    def __init__(self, *, signal_tf: int = 3, sma_period: int = 9,
                 sma_tf: int = 5, st_period: int = 10, st_mult: float = 2.0,
                 entry_cutoff_min: int = 15 * 60):
        self.signal_tf = int(signal_tf)
        self.sma_period = int(sma_period)
        self.sma_tf = int(sma_tf)
        self.st_period = int(st_period)
        self.st_mult = float(st_mult)
        self.entry_cutoff_min = int(entry_cutoff_min)
        # warmup (prior session)
        self._warm_spot: Optional[List[dict]] = None
        self._warm_day_start: Optional[int] = None
        self._prev_hlc: Optional[dict] = None
        # current day
        self._day_start: Optional[int] = None
        self._candles: List[dict] = []
        self._seen_ts: set = set()
        self._emitted: Dict[int, dict] = {}   # signal_ts -> frozen signal dict
        self.frozen = False
        self.diag = {"candles": 0, "rejected_candles": 0, "signals_emitted": 0,
                     "stale_signals": 0, "freeze_reason": None}

    # ── lifecycle ────────────────────────────────────────────────────
    def seed_warmup(self, prev_spot_1m: List[dict], prev_day_start: int,
                    prev_hlc: dict) -> bool:
        """Prior session's 1m spot + its day_start epoch + its H/L/C.
        Returns False (and stays unseeded) on obviously bad input."""
        if not prev_spot_1m or not prev_hlc or prev_day_start is None:
            write_audit_log("[PST_LIVE] warmup rejected — empty input (fail closed)")
            return False
        need = {"high", "low", "close"}
        if not need.issubset(set(prev_hlc.keys())):
            write_audit_log("[PST_LIVE] warmup rejected — prev_hlc incomplete")
            return False
        self._warm_spot = [dict(c) for c in prev_spot_1m]
        self._warm_day_start = int(prev_day_start)
        self._prev_hlc = dict(prev_hlc)
        write_audit_log(f"[PST_LIVE] warmup seeded: {len(prev_spot_1m)} prior-session "
                        f"candles, prev H/L/C {prev_hlc['high']}/{prev_hlc['low']}/{prev_hlc['close']}")
        return True

    def start_day(self, day_start_epoch: int) -> None:
        self._day_start = int(day_start_epoch)
        self._candles = []
        self._seen_ts = set()
        self._emitted = {}
        self.frozen = False
        self.diag = {"candles": 0, "rejected_candles": 0, "signals_emitted": 0,
                     "stale_signals": 0, "freeze_reason": None}

    @property
    def ready(self) -> bool:
        return (not self.frozen and self._day_start is not None
                and self._warm_spot is not None and self._prev_hlc is not None)

    # ── main entry: one completed 1m candle in, new signals out ─────
    def on_spot_candle(self, c: dict) -> List[dict]:
        """Feed ONE completed, boundary-aligned 1m spot candle. Returns the
        NEW signals (possibly empty). Each returned signal carries
        stale=True when its ts predates the just-completed candle's bar
        (only possible on mid-day starts) — managers must skip stale ones."""
        if self.frozen or not self.ready:
            return []
        try:
            ts = int(c["ts"])
            _ = (float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
        except Exception:
            self.diag["rejected_candles"] += 1
            return []
        if ts % 60 != 0 or ts < self._day_start or ts in self._seen_ts \
                or (self._candles and ts < int(self._candles[-1]["ts"])):
            self.diag["rejected_candles"] += 1
            return []
        self._seen_ts.add(ts)
        self._candles.append({"ts": ts, "open": float(c["open"]), "high": float(c["high"]),
                              "low": float(c["low"]), "close": float(c["close"])})
        self.diag["candles"] += 1

        # ── the replay: the REAL backtest signal function on the prefix ──
        res = build_signals(
            self._candles, self._day_start, self._prev_hlc,
            signal_tf=self.signal_tf,
            sma_period=self.sma_period, sma_tf=self.sma_tf,
            st_period=self.st_period, st_mult=self.st_mult,
            entry_cutoff_min=self.entry_cutoff_min,
            warmup_sessions=[(self._warm_spot, self._warm_day_start)])
        current = {int(s["ts"]): s for s in res["signals"]}

        # ── PREFIX-STABILITY RUNTIME GUARD (fail closed) ──
        for sts, frozen_sig in self._emitted.items():
            live_sig = current.get(sts)
            if live_sig is None or int(live_sig["ts"]) != int(frozen_sig["ts"]) \
                    or live_sig["side"] != frozen_sig["side"] \
                    or abs(float(live_sig["spot"]) - float(frozen_sig["spot"])) > 1e-9:
                self.frozen = True
                self.diag["freeze_reason"] = f"prefix instability at signal ts={sts}"
                write_audit_log(f"[PST_LIVE][FATAL] prefix instability at signal "
                                f"ts={sts} — engine FROZEN (fail closed). "
                                f"was={frozen_sig} now={live_sig}")
                return []

        new: List[dict] = []
        for sts in sorted(current.keys()):
            if sts in self._emitted:
                continue
            sig = copy.deepcopy(current[sts])
            # stale = the signal's 3m bar completed before this 1m candle's
            # bar — only possible when the engine starts mid-day.
            sig["stale"] = bool(sts < ts)
            self._emitted[sts] = copy.deepcopy(current[sts])
            self.diag["signals_emitted"] += 1
            if sig["stale"]:
                self.diag["stale_signals"] += 1
            new.append(sig)
        return new

    # ── parity-harness hook: the full-day reference set ─────────────
    def reference_full_day(self) -> List[dict]:
        """build_signals over everything fed so far, in one shot — what a
        backtest of this exact day would produce. The harness asserts this
        equals the incrementally emitted stream."""
        if not self.ready:
            return []
        res = build_signals(
            self._candles, self._day_start, self._prev_hlc,
            signal_tf=self.signal_tf,
            sma_period=self.sma_period, sma_tf=self.sma_tf,
            st_period=self.st_period, st_mult=self.st_mult,
            entry_cutoff_min=self.entry_cutoff_min,
            warmup_sessions=[(self._warm_spot, self._warm_day_start)])
        return res["signals"]