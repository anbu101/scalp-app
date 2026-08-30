# backend/app/engine/vet/vet_live_signal_engine.py
#
# ── VET_V1 LIVE SIGNAL ENGINE ── (PST / TMA_V2 parity doctrine, verbatim)
# ============================================================================
# THE PARITY DESIGN: this engine contains NO signal logic of its own. At every
# completed 5-minute spot candle it re-runs the REAL backtest pipeline —
# app.backtest.gc.gc_v1_engine.resample_spot + app.backtest.vet.vet_v1_engine
# .vet_states — over the day's growing 1m candle prefix with the prior N
# sessions as warmup, exactly as backtest_vet_runner does per day. Live
# signals therefore EQUAL backtest signals by construction, not by careful
# porting. The decision rule on top comes from vet_live_core.plan(), which is
# proven against a transcription of the runner's own decision block.
#
# WARMUP DEPTH (LD7): the backtest's warmup_sessions, verbatim — default 10.
# SMA(40)+ATR(40) on 5m needs ~40 bars (~1 session) to become valid at all,
# but the EMA10/20 chain and the CARRIED condition state both depend on how
# far back the replay started, so "enough for the SMA" is NOT enough for
# parity. The number here must equal the backtest's or signals diverge
# silently at the start of every day. It is asserted, not assumed.
#
# SESSION ANCHORING: resample_spot buckets from session_start + k*tf. Live
# must pass the SAME session_start_epoch (09:15 IST) the backtest uses, or
# every 5m bar is offset and the whole signal chain shifts. This is the single
# most likely source of a silent live/backtest divergence, so the anchor is
# computed in one place here and nowhere else.
#
# VALIDITY CONDITION — PREFIX STABILITY: a signal emitted for a completed 5m
# bar must never change as later candles append. vet_states is prefix-stable
# by construction (state at i reads bars[0..i] only), but the property is
# GUARDED at runtime anyway via vet_live_core.PrefixGuard: on any drift the
# engine FREEZES and emits nothing. Fail closed — never trade a signal stream
# that has already proven unreliable.
#
# ONLY COMPLETED CANDLES ENTER. The manager acts at the 5m boundary, mirroring
# the backtest's fill discipline (decision at 5m completion T, fill against
# the option close of the 1m bar stamped last1m_ts).
# ============================================================================

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

try:
    from app.backtest.gc.gc_v1_engine import resample_spot
    from app.backtest.vet.vet_v1_engine import (
        EMA_FAST1_DEFAULT, EMA_FAST2_DEFAULT, RANGE_LEN_DEFAULT,
        TREND_LEN_DEFAULT, vet_states)
    from app.engine.vet.vet_live_core import HOLD, PrefixGuard, plan
except ImportError:                                        # standalone tests
    from gc_v1_engine import resample_spot                  # type: ignore
    from vet_v1_engine import (                             # type: ignore
        EMA_FAST1_DEFAULT, EMA_FAST2_DEFAULT, RANGE_LEN_DEFAULT,
        TREND_LEN_DEFAULT, vet_states)
    from vet_live_core import HOLD, PrefixGuard, plan       # type: ignore

IST_OFFSET = 19800
SESSION_OPEN_MIN = 9 * 60 + 15          # 09:15 IST


def session_start_epoch(any_ts_in_day: int) -> int:
    """09:15 IST of the calendar day containing `any_ts_in_day`.

    resample_spot anchors its buckets here; the backtest uses the same
    anchor. Getting this wrong shifts every 5m bar and silently changes
    every signal, so it lives in exactly one place.
    """
    ts = int(any_ts_in_day)
    day_start_utc = ((ts + IST_OFFSET) // 86400) * 86400 - IST_OFFSET
    return day_start_utc + SESSION_OPEN_MIN * 60


class VetLiveSignalEngine:
    """Rebuilds VET signals from 1m spot candles, at 5m boundaries.

    Usage per session:
        eng = VetLiveSignalEngine(cfg, warmup_1m=<prior N sessions of 1m>)
        eng.on_minute(candle)          # every completed 1m spot candle
        sig = eng.latest_signal()      # None until a 5m bar completes
    """

    def __init__(self, cfg: Optional[Dict] = None,
                 warmup_1m: Optional[Sequence[Dict]] = None) -> None:
        c = dict(cfg or {})
        self.trend_len = int(c.get("trend_len") or TREND_LEN_DEFAULT)
        self.range_len = float(c.get("range_len") or RANGE_LEN_DEFAULT)
        self.ema_fast1 = int(c.get("ema_fast1") or EMA_FAST1_DEFAULT)
        self.ema_fast2 = int(c.get("ema_fast2") or EMA_FAST2_DEFAULT)
        self.tf_min = int(c.get("signal_tf") or 5)
        self.leg_action = str(c.get("leg_action") or "BUY").upper()
        self.warmup_sessions = int(c.get("warmup_sessions") or 10)

        # warmup 1m candles from PRIOR sessions, oldest first
        self.warmup_1m: List[Dict] = [dict(r) for r in (warmup_1m or [])]
        self.today_1m: List[Dict] = []
        self.guard = PrefixGuard()
        self._last_bar_ts: Optional[int] = None
        self._last_signal: Optional[Dict] = None
        self._states_cache: List = []

    # ── warmup accounting ────────────────────────────────────────────────
    def warmup_session_count(self) -> int:
        days = {((int(r["ts"]) + IST_OFFSET) // 86400) for r in self.warmup_1m}
        return len(days)

    def warmup_ok(self) -> bool:
        """The engine refuses to trade on short warmup rather than emitting
        signals that quietly differ from the backtest's."""
        return self.warmup_session_count() >= self.warmup_sessions

    # ── ingest ───────────────────────────────────────────────────────────
    def on_minute(self, candle: Dict) -> Optional[Dict]:
        """Fold one COMPLETED 1m spot candle. Returns a signal dict when this
        minute closed a 5m bucket AND the decision changed, else None."""
        if self.guard.frozen:
            return None
        r = {"ts": int(candle["ts"]), "open": float(candle["open"]),
             "high": float(candle["high"]), "low": float(candle["low"]),
             "close": float(candle["close"])}
        if self.today_1m and r["ts"] <= self.today_1m[-1]["ts"]:
            return None                       # duplicate / out-of-order tick
        self.today_1m.append(r)
        return self._recompute()

    def _recompute(self) -> Optional[Dict]:
        if not self.today_1m:
            return None
        anchor = session_start_epoch(self.today_1m[0]["ts"])
        # Warmup is resampled on ITS OWN session anchors, then concatenated —
        # the same shape the backtest builds when it walks prior days.
        bars: List = []
        by_day: Dict[int, List[Dict]] = {}
        for r in self.warmup_1m:
            d = (int(r["ts"]) + IST_OFFSET) // 86400
            by_day.setdefault(d, []).append(r)
        for d in sorted(by_day):
            rows = sorted(by_day[d], key=lambda x: int(x["ts"]))
            bars += resample_spot(rows, self.tf_min,
                                  session_start_epoch(rows[0]["ts"]))
        today_bars = resample_spot(self.today_1m, self.tf_min, anchor)
        bars += today_bars
        if not today_bars:
            return None

        # ── only act when a 5m bucket has actually COMPLETED ──
        # The final bucket is still forming unless this minute is its last.
        last = today_bars[-1]
        bucket_end = last.ts + self.tf_min * 60 - 60
        if self.today_1m[-1]["ts"] < bucket_end:
            return None
        if self._last_bar_ts is not None and last.ts <= self._last_bar_ts:
            return None

        states = vet_states(bars, ema_fast1=self.ema_fast1,
                            ema_fast2=self.ema_fast2,
                            trend_len=self.trend_len,
                            range_len=self.range_len)
        self._states_cache = states
        if not self.guard.check([b.ts for b in bars],
                                [s.condition for s in states]):
            return None                        # frozen — fail closed

        self._last_bar_ts = last.ts
        st = states[-1]
        if not st.valid:
            return None
        self._last_signal = {
            "bar_ts": last.ts, "last1m_ts": last.last1m_ts,
            "condition": int(st.condition), "spot": float(last.close),
            "in_range": bool(st.in_range), "dir_trend": int(st.dir_trend),
            "warmup_ok": self.warmup_ok(),
        }
        return self._last_signal

    # ── decision ─────────────────────────────────────────────────────────
    def decide(self, pos_side: Optional[str]) -> Dict:
        """Apply the (proven) live-core rule to the latest completed bar.

        Returns {"action": HOLD|ENTER|EXIT|FLIP, ...}. Always HOLD while the
        guard is frozen or warmup is short — both are refusals, not opinions.
        """
        sig = self._last_signal
        if sig is None or self.guard.frozen:
            return {"action": HOLD, "side": None, "reason": None,
                    "blocked": "frozen" if self.guard.frozen else "no_signal"}
        if not sig["warmup_ok"]:
            return {"action": HOLD, "side": None, "reason": None,
                    "blocked": (f"warmup {self.warmup_session_count()}"
                                f"/{self.warmup_sessions} sessions")}
        action, side, reason = plan(pos_side, sig["condition"],
                                    self.leg_action)
        return {"action": action, "side": side, "reason": reason,
                "bar_ts": sig["bar_ts"], "last1m_ts": sig["last1m_ts"],
                "spot": sig["spot"], "condition": sig["condition"],
                "blocked": None}

    def latest_signal(self) -> Optional[Dict]:
        return dict(self._last_signal) if self._last_signal else None

    def status(self) -> Dict:
        return {"frozen": self.guard.frozen, "freeze_reason": self.guard.reason,
                "warmup_sessions": self.warmup_session_count(),
                "warmup_required": self.warmup_sessions,
                "warmup_ok": self.warmup_ok(),
                "bars_today": len(self.today_1m),
                "last_bar_ts": self._last_bar_ts,
                "leg_action": self.leg_action}