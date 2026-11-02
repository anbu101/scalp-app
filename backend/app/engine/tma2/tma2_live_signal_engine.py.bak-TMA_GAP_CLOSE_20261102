# backend/app/engine/tma2/tma2_live_signal_engine.py
#
# ── TMA_V2 LIVE SIGNAL ENGINE ── (PST/TMA_V1 parity doctrine, verbatim)
# ============================================================================
# THE PARITY DESIGN: this engine contains NO signal logic of its own. At
# every completed 1-minute spot candle it re-runs the REAL backtest signal
# pipeline — app.backtest.tma.tma_v2_engine (warmup_bars + aggregate +
# compute_state_v2 + build_signals_v2) — over the day's growing candle
# prefix with the prior FIVE sessions as warmup, exactly like
# backtest_tma_v2_runner does per day (TMA2_XDAY_WARMUP). Live signals
# therefore equal backtest signals BY CONSTRUCTION, not by careful porting.
#
# WARMUP DEPTH (D5): FIVE prior sessions, not V1's three — EMA144@5m needs
# 144 bars for the SMA seed alone (~2 sessions) plus convergence. The
# warmup fetcher's WARMUP_DAYS must match the backtest runner's; both are 5.
#
# ── FROZEN STUDY PARAMETERS (2026-08-19) ──────────────────────────────────
# The 6.5-year study locked these; they are config keys with locked
# defaults (LD2) and are deliberately ABSENT from the Settings UI:
#   * exit reference EMA55 (XOVER_REF_DEFAULT) — the exit line closest to
#     EMA13 that still costs zero opportunities (the trade-count knee);
#     ref89 lost 2026 outright, ref40/50 collapsed.
#   * EMA144 slope gate ON — rejects stacks assembled by sideways drift.
#   * max_extension_pct 0.8 — the ONLY study knob exposed in Settings,
#     because its optimum is flat across 0.5-1.5 and it is regime-facing.
#   * min_extension_pct is NOT wired live at all: the IS-only sweep showed
#     the floor improves per-trade quality (+32%) but blocks ~49% of
#     entries and loses a third of net — TMA_V2 is volume-constrained.
#   * xover exit always ON, mode always SELL+hedge (live is spread-only).
#
# VALIDITY CONDITION — PREFIX STABILITY (PST runtime guard): a signal
# emitted for a completed 5m bar must never change or disappear as later
# candles append. The E1/E2 transition definition is prefix-stable (it
# reads only bars i-1 and i of COMPLETED bars), but the property is
# GUARDED at runtime anyway: any drift → the engine FREEZES (emits
# nothing, frozen=True) — fail closed, never trade unstable signals.
#
# XOVER SERVICE: the trade manager's crossover-reversal exit is evaluated
# on the SAME bars5/state this engine maintains, via the backtest's own
# xover_exit_ts_v2 with the SAME exit reference — one indicator stream,
# zero drift between signal and exit.
#
# D27 note: only COMPLETED candles enter; every signal is knowable exactly
# at the minute boundary — the manager acts at that boundary, mirroring the
# backtest's fill discipline (signal at 5m completion T, fill at the close
# of the 1m option candle stamped T).
# ============================================================================

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Tuple

try:
    from app.backtest.tma.tma_v2_engine import (
        REF_KEYS, build_signals_v2, compute_state_v2, warmup_bars,
        xover_exit_ts_v2,
    )
    from app.backtest.pst.pst_indicators import aggregate
except ImportError:  # standalone tests
    from tma_v2_engine import (                     # type: ignore
        REF_KEYS, build_signals_v2, compute_state_v2, warmup_bars,
        xover_exit_ts_v2,
    )
    from pst_indicators import aggregate            # type: ignore

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:  # standalone tests / harness
    def write_audit_log(msg: str) -> None:
        print(msg)

TF_MIN = 5
TF_S = TF_MIN * 60

# ── FROZEN STUDY DEFAULTS ── (config-overridable, UI-hidden — LD2)
XOVER_REF_DEFAULT = 55
MAX_EXT_DEFAULT = 0.8
SLOPE_GATE_DEFAULT = True


class TMA2LiveSignalEngine:
    """One instance per trading day. Fail-closed everywhere: no warmup → no
    signals; instability → frozen; malformed candles → rejected."""

    def __init__(self, *, xover_exit_ref: int = XOVER_REF_DEFAULT,
                 max_extension_pct: float = MAX_EXT_DEFAULT,
                 slope_gate: bool = SLOPE_GATE_DEFAULT,
                 tf_min: int = TF_MIN):
        self.xover_ref = int(xover_exit_ref or XOVER_REF_DEFAULT)
        self.max_ext = float(max_extension_pct or 0)
        self.slope_gate = bool(slope_gate)
        # ── EXT_BAND ── the floor is intentionally NOT wired live (see the
        # header): rejected by the IS-only sweep as volume-destroying.
        self.tf_min = int(tf_min)
        self.tf_s = self.tf_min * 60
        # ref_period is only needed when the exit reference is not already
        # one of the stack EMAs (55/89 cost nothing extra)
        self._ref_period = (None if self.xover_ref in REF_KEYS
                            else self.xover_ref)
        # warmup (prior sessions, oldest-first) — backtest warmup_sessions shape
        self._warmup_sessions: Optional[List[Tuple[List[dict], int]]] = None
        # current day
        self._day_start: Optional[int] = None
        self._session0: Optional[int] = None
        self._entry_start_ts: Optional[int] = None
        self._entry_end_ts: Optional[int] = None
        self._candles: List[dict] = []
        self._seen_ts: set = set()
        self._emitted: Dict[Tuple[int, str], dict] = {}   # (ts, side) -> frozen sig
        self.frozen = False
        # cached indicator stream for xover lookups (refreshed per candle)
        self._bars5: List[dict] = []
        self._warm_count: int = 0
        self._state: Optional[Dict] = None
        self.diag = {"candles": 0, "rejected_candles": 0, "signals_emitted": 0,
                     "stale_signals": 0, "freeze_reason": None,
                     "warmup_sessions": 0, "blocked_extension": 0,
                     "blocked_slope": 0, "blocked_warmup": 0,
                     "xover_ref": self.xover_ref,
                     "max_extension_pct": self.max_ext,
                     "slope_gate": "ON" if self.slope_gate else "OFF"}

    # ── lifecycle ────────────────────────────────────────────────────
    def seed_warmup(self, warmup_sessions: List[Tuple[List[dict], int]]) -> bool:
        """[(spot_1m, day_start), ...] oldest-first — the exact shape the
        backtest engine's warmup_bars consumes. Empty → stays unseeded."""
        if not warmup_sessions:
            write_audit_log("[TMA2_LIVE] warmup rejected — empty (fail closed)")
            return False
        self._warmup_sessions = [(list(c), int(ds)) for c, ds in warmup_sessions]
        self.diag["warmup_sessions"] = len(self._warmup_sessions)
        # EMA144 honesty: fewer than 5 sessions means the slow line is still
        # converging — the backtest's early-range days behave identically,
        # but say so loudly rather than trading a half-warm indicator quietly.
        if len(self._warmup_sessions) < 5:
            write_audit_log(f"[TMA2_LIVE][WARN] only "
                            f"{len(self._warmup_sessions)} warmup session(s) "
                            f"(<5): EMA144 is still converging — early "
                            f"signals may differ from a full backtest")
        write_audit_log(f"[TMA2_LIVE] warmup seeded: "
                        f"{len(self._warmup_sessions)} prior session(s), "
                        f"exit ref EMA{self.xover_ref}, "
                        f"maxExt {self.max_ext}%, "
                        f"slope {'ON' if self.slope_gate else 'OFF'}")
        return True

    def start_day(self, day_start_epoch: int, *,
                  sess_start_min: int, sess_end_min: int) -> None:
        self._day_start = int(day_start_epoch)
        self._session0 = self._day_start + (9 * 60 + 15) * 60
        self._entry_start_ts = self._day_start + int(sess_start_min) * 60
        self._entry_end_ts = self._day_start + int(sess_end_min) * 60
        self._candles = []
        self._seen_ts = set()
        self._emitted = {}
        self._bars5, self._warm_count, self._state = [], 0, None
        self.frozen = False
        self.diag.update({"candles": 0, "rejected_candles": 0,
                          "signals_emitted": 0, "stale_signals": 0,
                          "freeze_reason": None, "blocked_extension": 0,
                          "blocked_slope": 0, "blocked_warmup": 0})

    @property
    def ready(self) -> bool:
        return (not self.frozen and self._day_start is not None
                and self._warmup_sessions is not None)

    # ── main entry: one completed 1m spot candle in, new signals out ─
    def on_spot_candle(self, c: dict) -> List[dict]:
        """Feed ONE completed, boundary-aligned 1m spot candle. Returns the
        NEW signals (possibly empty). Each carries stale=True when its ts
        predates the just-completed candle's minute (only possible on
        mid-day starts/backfill) — the manager must skip stale ones."""
        if self.frozen or not self.ready:
            return []
        try:
            ts = int(c["ts"])
            _ = (float(c["open"]), float(c["high"]),
                 float(c["low"]), float(c["close"]))
        except Exception:
            self.diag["rejected_candles"] += 1
            return []
        if ts % 60 != 0 or ts < self._day_start or ts in self._seen_ts \
                or (self._candles and ts < int(self._candles[-1]["ts"])):
            self.diag["rejected_candles"] += 1
            return []
        self._seen_ts.add(ts)
        self._candles.append({"ts": ts, "open": float(c["open"]),
                              "high": float(c["high"]), "low": float(c["low"]),
                              "close": float(c["close"])})
        self.diag["candles"] += 1

        # ── the replay: the REAL backtest pipeline on the prefix ──
        warm5 = warmup_bars(self._warmup_sessions, self.tf_min)
        today5 = [b for b in aggregate(self._candles, self.tf_min,
                                       self._day_start) if b["complete"]]
        bars5 = warm5 + today5
        state = compute_state_v2(bars5, ref_period=self._ref_period)
        res = build_signals_v2(bars5, len(warm5), self._session0,
                               self._entry_start_ts, self._entry_end_ts,
                               tf_s=self.tf_s, state=state,
                               max_extension_pct=self.max_ext,
                               slope_gate=self.slope_gate)
        # cache for xover lookups (manager reads via xover_ts_for)
        self._bars5, self._warm_count, self._state = bars5, len(warm5), state
        for k in ("blocked_extension", "blocked_slope", "blocked_warmup"):
            self.diag[k] = res["diag"].get(k, 0)

        current = {(int(s["ts"]), s["side"]): s for s in res["signals"]}

        # ── PREFIX-STABILITY RUNTIME GUARD (fail closed) ──
        for key, frozen_sig in self._emitted.items():
            live_sig = current.get(key)
            if live_sig is None \
                    or live_sig["cond"] != frozen_sig["cond"] \
                    or abs(float(live_sig["spot"])
                           - float(frozen_sig["spot"])) > 1e-9:
                self.frozen = True
                self.diag["freeze_reason"] = f"prefix instability at {key}"
                write_audit_log(f"[TMA2_LIVE][FATAL] prefix instability at "
                                f"signal {key} — engine FROZEN (fail closed). "
                                f"was={frozen_sig} now={live_sig}")
                return []

        new: List[dict] = []
        for key in sorted(current.keys()):
            if key in self._emitted:
                continue
            sig = copy.deepcopy(current[key])
            # stale = the signal's 5m bar completed before this 1m candle's
            # minute — only possible on mid-day starts / backfill replay.
            sig["stale"] = bool(int(sig["ts"]) < ts + 60)
            # a signal stamped exactly at this candle's completion (ts+60
            # boundary) is FRESH: build_signals_v2 stamps ts_end = bar_ts+tf_s,
            # and the 5m bar completing NOW has ts_end == this candle ts+60.
            if int(sig["ts"]) == ts + 60:
                sig["stale"] = False
            self._emitted[key] = copy.deepcopy(current[key])
            self.diag["signals_emitted"] += 1
            if sig["stale"]:
                self.diag["stale_signals"] += 1
            new.append(sig)
        return new

    # ── XOVER service for the trade manager ─────────────────────────
    def xover_ts_for(self, trend_side: str, after_ts: int) -> Optional[int]:
        """First completed 5m bar completion time strictly after after_ts at
        which the crossover-reversal exit holds for the TREND side (the
        signal's side, not the sold side) — the backtest's own
        xover_exit_ts_v2 over the exact bars/state stream the signals came
        from, at the SAME exit reference. The live crossover exit is always
        ON (spec): there is no toggle here by design."""
        if self._state is None or not self._bars5:
            return None
        try:
            return xover_exit_ts_v2(self._bars5, self._state, trend_side,
                                    int(after_ts), tf_s=self.tf_s,
                                    exit_ref=self.xover_ref)
        except Exception as e:
            write_audit_log(f"[TMA2_LIVE] xover lookup failed: {e}")
            return None

    # ── parity-harness hook ─────────────────────────────────────────
    def reference_full_day(self) -> List[dict]:
        """build_signals_v2 over everything fed so far, one shot — what a
        backtest of this exact day would produce. The dry-run harness
        asserts this equals the incrementally emitted stream."""
        if not self.ready or not self._candles:
            return []
        warm5 = warmup_bars(self._warmup_sessions, self.tf_min)
        today5 = [b for b in aggregate(self._candles, self.tf_min,
                                       self._day_start) if b["complete"]]
        res = build_signals_v2(warm5 + today5, len(warm5), self._session0,
                               self._entry_start_ts, self._entry_end_ts,
                               tf_s=self.tf_s,
                               max_extension_pct=self.max_ext,
                               slope_gate=self.slope_gate)
        return res["signals"]