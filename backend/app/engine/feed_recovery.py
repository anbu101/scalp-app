# backend/app/engine/feed_recovery.py
#
# ── FEED_RECOVERY_20261103 ── repair a spot-candle HOLE from Kite while the app runs.
#
# 2026-11-02: a friend's Mac slept 09:27–10:23 with the app left running. The
# WebSocket died, ~56 spot minutes were never built, and — because only a
# RESTART backfills — Timberwolf / TMA_V1 / Velvet kept computing their EMAs on
# a series missing ~11 five-minute bars for the rest of the day. EMA55 ended
# 17 points off, his 14:20 stack check came out differently, and the signal the
# backtest and the other machine both saw never fired — with no log line.
#
# FeedRecovery watches the candle stream each engine already folds. When a
# completed spot candle arrives ≥ STALL_MISSING_MIN minutes after the previous
# one (1–2 missing minutes are the gap-close case), it re-fetches today's 1m
# spot candles from Kite and asks the engine to REBUILD today's prefix from
# them — exactly what the restart backfill does, signals from the replay
# DISCARDED (they belong to the past; nothing is entered late).
#
# Fail closed: until a rebuild succeeds the caller must NOT act on signals from
# the (still distorted) stream — before_candle() returns True ("suppress").
# It retries every minute (RETRY_MAX attempts), alerting on the first failure
# and when it gives up.
#
# An engine adapter needs two methods:
#     last_candle_ts() -> Optional[int]     ts of the last 1m candle folded today
#     rebuild_today(candles) -> int         replace today's prefix, replay, discard
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

try:
    from app.event_bus.audit_logger import write_audit_log
except Exception:                                   # standalone harness
    def write_audit_log(msg: str) -> None:
        print(msg)

FENCE = "FEED_RECOVERY_20261103"
IST = timezone(timedelta(hours=5, minutes=30))
STALL_MISSING_MIN = 3        # ≥ this many consecutive missing 1m spot minutes = a stall
RETRY_MAX = 20               # fetch attempts (one per minute) before giving up
HOLE_SLACK_S = 120           # Kite's series must reach within this of the current candle


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def _day(ts: int) -> int:
    return int(ts + 19800) // 86400


class FeedRecovery:
    def __init__(self, *, tag: str, engine, fetch_fn: Callable[[], Optional[List[dict]]],
                 notify: Optional[Callable] = None, missing_min: int = STALL_MISSING_MIN,
                 retry_max: int = RETRY_MAX):
        self.tag = tag
        self.engine = engine
        self.fetch_fn = fetch_fn
        self.notify = notify
        self.missing_min = int(missing_min)
        self.retry_max = int(retry_max)
        self.pending = False
        self.attempts = 0
        self.recoveries = 0
        self.failures = 0
        self._gap = None            # (last candle before the hole, candle that ended it)

    # ── helpers ─────────────────────────────────────────────────────
    def _say(self, msg: str, sev: str = "info") -> None:
        write_audit_log(f"[{self.tag}][RECOVER] {msg}")
        if not self.notify:
            return
        try:
            try:
                self.notify(f"{self.tag}: {msg}", sev)
            except TypeError:
                self.notify(f"{self.tag}: {msg}")
        except Exception:
            pass

    # ── the one entry point ─────────────────────────────────────────
    def before_candle(self, ts: int) -> bool:
        """Call BEFORE folding a completed spot candle `ts` into the engine.
        Returns True when signals from this candle must be suppressed (a
        rebuild is needed and has not succeeded yet)."""
        ts = int(ts)
        if self.pending and self._gap and _day(ts) != _day(self._gap[0]):
            self.pending = False                    # a new session: the engine restarts clean
        last = self.engine.last_candle_ts()
        if last is None:
            return False
        if not self.pending:
            missing = (ts - int(last)) // 60 - 1
            if missing < self.missing_min or _day(ts) != _day(last):
                return False
            self.pending, self.attempts, self._gap = True, 0, (int(last), ts)
            write_audit_log(f"[{self.tag}][RECOVER] spot feed gap {_hhmm(last)}→{_hhmm(ts)} "
                            f"({missing} minutes missing) — rebuilding indicators from Kite")
        return not self._try(ts)

    def _try(self, ts: int) -> bool:
        if self.attempts >= self.retry_max:
            return False
        self.attempts += 1
        err = None
        try:
            rows = self.fetch_fn() or []
        except Exception as e:                      # noqa: BLE001
            rows, err = [], e
        have = sorted((c for c in rows if int(c["ts"]) < ts), key=lambda c: int(c["ts"]))
        gap_from = self._gap[0]
        beyond = [c for c in have if int(c["ts"]) > gap_from]
        if not beyond or int(beyond[-1]["ts"]) < ts - HOLE_SLACK_S:
            self.failures += 1
            why = repr(err) if err else (f"Kite returned {len(rows)} candles, none covering "
                                         f"{_hhmm(gap_from)}→{_hhmm(ts)}")
            if self.attempts == 1:
                self._say(f"rebuild failed ({why}) — signals suppressed, retrying every minute",
                          "warning")
            elif self.attempts >= self.retry_max:
                self._say(f"rebuild failed {self.attempts}× ({why}) — giving up; signals stay "
                          f"suppressed today, restart the app", "error")
            else:
                write_audit_log(f"[{self.tag}][RECOVER] attempt {self.attempts} failed ({why})")
            return False
        n = int(self.engine.rebuild_today(have) or 0)
        self.pending = False
        self.recoveries += 1
        self._say(f"spot feed gap {_hhmm(gap_from)}→{_hhmm(ts)} repaired — indicators rebuilt "
                  f"from {len(have)} Kite candles ({n} replayed, replay signals discarded)", "info")
        return True
