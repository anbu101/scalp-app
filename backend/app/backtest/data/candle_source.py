# backend/app/backtest/data/candle_source.py
#
# The historical feed. Reads 1-minute candles from backtest.db and exposes the
# 1-second adjudication lookup used by the fill model.
#
# DESIGN (settled):
#   * The engine ALWAYS runs on 1-minute candles for signals + indicators —
#     identical to live (timeframe_sec=60). We never feed raw 1s to the engine;
#     mixing bar widths would corrupt the EMA/RSI series.
#   * 1-second data is used ONLY to adjudicate an ambiguous 1m fill (both SL and
#     TP inside one minute's [low,high]). When 1s exists for that contract+minute
#     we replay the seconds to learn which level was touched FIRST; otherwise the
#     fill model applies the pessimistic "SL first" rule and flags the trade.
#
# This module is READ-ONLY and opens its own connection to the separate
# backtest.db (isolation: never contends with the live trading DB).
#
# PERFORMANCE (per-day preload cache):
#   Previously every lookup opened a NEW sqlite connection and ran a query. With
#   a dense ATM±10 universe that meant ~thousands of connect+query cycles PER DAY
#   (option_premium_at is called per-contract per-selection-boundary), which made
#   multi-week/À multi-month runs very slow. Now:
#     * ONE persistent connection is reused (no per-call connect()).
#     * preload_day() pulls the WHOLE day's 1m candles for ALL contracts in a
#       SINGLE query into in-memory dicts. The hot-path readers
#       (option_premium_at, candles_1m_for_symbol_day, contracts_active_on_day)
#       serve from memory when the day is preloaded.
#   This is purely a read-access optimization: identical rows, identical values,
#   identical ordering. Methods that legitimately cross day boundaries
#   (warmup_candles_before, candles_1m_for_symbol_range) and the 1s adjudication
#   path still query SQL directly, so nothing about results changes.

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.utils.app_paths import APP_HOME


IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60   # fixed, app-wide


def _backtest_db_path() -> Path:
    return APP_HOME / "backtest" / "backtest.db"


@dataclass
class BTCandle:
    ts: int            # epoch seconds, candle START (IST grid)
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int


@dataclass
class BTSecond:
    ts: int
    open: float
    high: float
    low: float
    close: float


def _day_bounds_epoch(day_epoch_start: int) -> tuple[int, int]:
    """Given the epoch of 00:00 IST for a day, return [start, end) covering the
    full trading day in epoch seconds."""
    return day_epoch_start, day_epoch_start + 86400


class CandleSource:
    """Read-only access to the historical corpus in backtest.db.

    A single connection is held for the lifetime of the instance. Use
    preload_day() at the start of each backtest day to load that day's candles
    into memory; the per-symbol / per-minute readers then serve from the cache.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._path = str(db_path or _backtest_db_path())
        # ── CORPUS_SANITIZER BEGIN ── self-healing data-quality gate. Runs
        # before the read connection opens; version+watermark stamped, so
        # after the first heal it costs ONE SELECT per CandleSource init.
        # Quarantines (never destroys) flat-candle spike prints and
        # interleaved symbol-days — see corpus_sanitizer.py for the 2022-12-08
        # / 2024-06-06 forensics that motivated it. MUST fail open: a broken
        # sanitizer must never block a backtest.
        try:
            from app.backtest.tools.corpus_sanitizer import ensure_corpus_sane
            ensure_corpus_sane(self._path)
        except Exception as _san_exc:
            # Fail-open, but NEVER silently: a missing/broken sanitizer once
            # went unnoticed because this except swallowed the ImportError
            # (module absent from a frozen bundle is exactly the failure the
            # build gates exist for). One audit line makes it visible.
            try:
                from app.event_bus.audit_logger import write_audit_log
                write_audit_log("[CORPUS_SANITIZER] unavailable — corpus "
                                f"NOT checked (fail-open): {_san_exc!r}")
            except Exception:
                pass
        # ── CORPUS_SANITIZER END ──
        # One persistent read-only-style connection (we never write here).
        self._c = sqlite3.connect(self._path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        # Read tuning: bigger cache, memory temp store. Harmless for reads.
        try:
            self._c.execute("PRAGMA cache_size=-65536;")   # ~64 MB page cache
            self._c.execute("PRAGMA temp_store=MEMORY;")
            self._c.execute("PRAGMA mmap_size=268435456;") # 256 MB mmap if supported
        except Exception:
            pass

        # ── per-day preload cache ──
        # _cache_day_start: the day_start_epoch currently loaded (or None).
        # _by_sym_min[sym][minute_ts] = BTCandle   (point lookups)
        # _by_sym_list[sym] = [BTCandle ascending] (day slices)
        # _contracts: list[dict] for contracts_active_on_day
        self._cache_day_start: Optional[int] = None
        self._cache_underlying: Optional[str] = None
        # ── PRELOAD_SCOPED ── None = whole day cached; a string = ONLY that
        # expiry is cached, so readers must fall back to SQL for anything else.
        self._cache_expiry: Optional[str] = None
        self._by_sym_min: Dict[str, Dict[int, BTCandle]] = {}
        self._by_sym_list: Dict[str, List[BTCandle]] = {}
        self._contracts: List[dict] = []

    # Back-compat: some call sites may use `with self._conn() as c`. Keep a
    # method that returns the shared connection (callers must NOT close it).
    def _conn(self) -> sqlite3.Connection:
        return self._c

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass

    # ---------------------------------------------------------------
    # PER-DAY PRELOAD  (the speed fix)
    # ---------------------------------------------------------------
    def preload_day(self, underlying: str, day_start_epoch: int,
                    expiry: Optional[str] = None) -> int:
        """Load ALL 1m candles for `underlying` on this day into memory with a
        SINGLE query. Returns the number of contracts loaded. Idempotent: if the
        same (underlying, day, expiry) is already cached, does nothing.

        After this, option_premium_at / candles_1m_for_symbol_day /
        contracts_active_on_day serve from memory for THIS day.

        ── PRELOAD_SCOPED (2026-08-05) ── `expiry` restricts the load to ONE
        expiry's contracts. Default None = load everything, byte-identical to
        the previous behaviour for every existing caller.

        WHY IT MATTERS: the corpus holds several weeklies alive at once
        (measured on a 4-expiry corpus: 168 symbols/day, 63k candles), while a
        single-expiry strategy such as IC reads exactly one of them (42
        symbols, 15.75k candles). Scoping does not merely skip 3/4 of the row
        materialisation — it changes the PLAN. With `expiry` in the predicate
        SQLite uses idx_bt1m_under_exp_ts (underlying, expiry, ts), which
        covers the whole WHERE *and* yields ts order for free, eliminating the
        TEMP B-TREE the unscoped query needs. Measured 214.1 → 13.8 ms/day.

        SAFETY: a scoped cache is a PARTIAL view of the day, so the readers
        below must never treat "absent from cache" as "no candles". They key
        off `_cache_expiry` and fall back to SQL for out-of-scope symbols —
        slower for those, never wrong. This is the one invariant to preserve
        if this method is ever touched again."""
        if (self._cache_day_start == day_start_epoch
                and self._cache_underlying == underlying
                and self._cache_expiry == expiry):
            return len(self._by_sym_list)

        lo, hi = _day_bounds_epoch(day_start_epoch)

        # ── PRELOAD_FAST BEGIN (2026-08-05) ── preload_day was 81% of an IC
        # run's wall clock (cProfile, 40-day synthetic corpus: 4.57s of
        # 5.63s). Three changes, none of which alter what is cached:
        #
        #  1. POSITIONAL TUPLES, not sqlite3.Row. The row_factory is set on
        #     the shared connection for every other caller's benefit, but
        #     THIS loop reads 11 columns from every row in the day — at
        #     ~15k candles/day that is ~170k Row key lookups (each a string
        #     hash into the description tuple) where tuple unpacking is one
        #     opcode. The factory is restored in a finally: block so no
        #     other CandleSource method ever sees it missing.
        #  2. STREAM the cursor instead of .fetchall(). fetchall builds a
        #     throwaway list of every row in the day before the first
        #     BTCandle is constructed; iterating the cursor overlaps
        #     materialisation with the loop and halves peak memory.
        #  3. ORDER BY ts ASC, not (tradingsymbol, ts). The two-key sort
        #     forced a TEMP B-TREE (confirmed by EXPLAIN QUERY PLAN) over
        #     the whole day. Global ts-ascending IMPLIES per-symbol
        #     ts-ascending, which is the ONLY ordering the append below
        #     relies on — the symbol grouping is done by dict, not by the
        #     sort. by_sym_list therefore comes out identical.
        #
        # Measured on the synthetic corpus: 95.1 → 60.9 ms/day (-36%).
        # Behaviour-identical: same keys, same BTCandle values, same order.
        prev_factory = self._c.row_factory
        try:
            self._c.row_factory = None
            # ── PRELOAD_SCOPED ── expiry FIRST in the predicate so the
            # (underlying, expiry, ts) index is a clean prefix match.
            if expiry:
                cur = self._c.execute(
                    """
                    SELECT ts, open, high, low, close, volume, oi,
                           tradingsymbol, strike, instrument_type, expiry
                    FROM backtest_candles_1m
                    WHERE underlying = ? AND expiry = ?
                      AND instrument_type IN ('CE','PE')
                      AND ts >= ? AND ts < ?
                    ORDER BY ts ASC
                    """,
                    (underlying, expiry, lo, hi),
                )
            else:
                cur = self._c.execute(
                    """
                    SELECT ts, open, high, low, close, volume, oi,
                           tradingsymbol, strike, instrument_type, expiry
                    FROM backtest_candles_1m
                    WHERE underlying = ? AND instrument_type IN ('CE','PE')
                      AND ts >= ? AND ts < ?
                    ORDER BY ts ASC
                    """,
                    (underlying, lo, hi),
                )

            by_sym_min: Dict[str, Dict[int, BTCandle]] = {}
            by_sym_list: Dict[str, List[BTCandle]] = {}
            contracts_seen: Dict[str, dict] = {}

            for (ts, o, h, lw, cl, vol, oi,
                 sym, strike, itype, expiry) in cur:
                cdl = BTCandle(ts, o, h, lw, cl, vol, oi)
                d = by_sym_min.get(sym)
                if d is None:
                    d = {}
                    by_sym_min[sym] = d
                    by_sym_list[sym] = []
                    contracts_seen[sym] = {
                        "tradingsymbol": sym,
                        "strike": strike,
                        "instrument_type": itype,
                        "expiry": expiry,
                    }
                d[ts] = cdl
                by_sym_list[sym].append(cdl)   # ts ASC globally ⇒ ts ASC per symbol
        finally:
            self._c.row_factory = prev_factory
        # ── PRELOAD_FAST END ──

        self._cache_day_start = day_start_epoch
        self._cache_underlying = underlying
        self._cache_expiry = expiry          # ── PRELOAD_SCOPED ──
        self._by_sym_min = by_sym_min
        self._by_sym_list = by_sym_list
        self._contracts = list(contracts_seen.values())
        return len(by_sym_list)

    def _day_is_cached(self, day_start_epoch: int) -> bool:
        return self._cache_day_start == day_start_epoch

    # ---------------------------------------------------------------
    # 1-MINUTE FEED  (the signal/indicator path)
    # ---------------------------------------------------------------
    def candles_1m_for_symbol_day(
        self, tradingsymbol: str, day_start_epoch: int
    ) -> List[BTCandle]:
        """All 1m candles for one contract on one day, ordered by ts ascending."""
        # ── PRELOAD_SCOPED ── a scoped cache is a PARTIAL view of the day.
        # Serving `[]` for a symbol outside the scope would be SILENTLY WRONG
        # (an IC_V2 leg carried from last week's expiry would book as having
        # no candles). Absent-from-a-scoped-cache therefore falls through to
        # SQL below — slower for those few symbols, never wrong.
        if self._day_is_cached(day_start_epoch) and (
                self._cache_expiry is None
                or tradingsymbol in self._by_sym_list):
            # Serve from cache (already ascending). Return a shallow copy so
            # callers can't mutate the cache list.
            return list(self._by_sym_list.get(tradingsymbol, ()))

        lo, hi = _day_bounds_epoch(day_start_epoch)
        rows = self._c.execute(
            """
            SELECT ts, open, high, low, close, volume, oi
            FROM backtest_candles_1m
            WHERE tradingsymbol = ? AND ts >= ? AND ts < ?
            ORDER BY ts ASC
            """,
            (tradingsymbol, lo, hi),
        ).fetchall()
        return [BTCandle(r["ts"], r["open"], r["high"], r["low"],
                         r["close"], r["volume"], r["oi"]) for r in rows]

    def candles_1m_for_symbol_range(
        self, tradingsymbol: str, from_epoch: int, to_epoch: int
    ) -> List[BTCandle]:
        """Used for warmup: pull the N candles BEFORE the test window so the
        indicator engine is ready exactly as live warmup makes it. Always SQL
        (may span days outside any single preloaded day)."""
        rows = self._c.execute(
            """
            SELECT ts, open, high, low, close, volume, oi
            FROM backtest_candles_1m
            WHERE tradingsymbol = ? AND ts >= ? AND ts < ?
            ORDER BY ts ASC
            """,
            (tradingsymbol, from_epoch, to_epoch),
        ).fetchall()
        return [BTCandle(r["ts"], r["open"], r["high"], r["low"],
                         r["close"], r["volume"], r["oi"]) for r in rows]

    def warmup_candles_before(
        self, tradingsymbol: str, before_epoch: int, limit: int
    ) -> List[BTCandle]:
        """The most recent `limit` candles strictly BEFORE before_epoch,
        returned ascending. Mirrors timeline_repo.fetch_recent_candles_for_warmup.
        Always SQL: warmup deliberately reaches into PRIOR days, outside the
        current preloaded day."""
        rows = self._c.execute(
            """
            SELECT ts, open, high, low, close, volume, oi
            FROM backtest_candles_1m
            WHERE tradingsymbol = ? AND ts < ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (tradingsymbol, before_epoch, limit),
        ).fetchall()
        rows = list(reversed(rows))
        return [BTCandle(r["ts"], r["open"], r["high"], r["low"],
                         r["close"], r["volume"], r["oi"]) for r in rows]

    # ---------------------------------------------------------------
    # SELECTION SUPPORT
    # ---------------------------------------------------------------
    def spot_at(self, underlying: str, ts: int) -> Optional[float]:
        """Underlying spot close at-or-before ts, from SPOT rows (the Dhan
        index backfill). NO-LOOKAHEAD: a bar stamped T covers [T, T+60) — at
        time ts the bar stamped ts is IN PROGRESS, so the freshest legal
        close is the bar stamped ts-60. Bounded to the same session (6h
        lookback) so a pre-open ts can't return yesterday's close silently.
        Returns None when no spot data covers ts; callers keep their
        fallbacks (parity inference / strike-median ATM)."""
        row = self._c.execute(
            """
            SELECT close FROM backtest_candles_1m
            WHERE underlying = ? AND instrument_type = 'SPOT'
              AND ts <= ? AND ts >= ?
            ORDER BY ts DESC LIMIT 1
            """,
            (underlying, int(ts) - 60, int(ts) - 6 * 3600),
        ).fetchone()
        return float(row["close"]) if row else None

    def option_premium_at(self, tradingsymbol: str, ts: int) -> Optional[float]:
        """The contract's price at the minute containing ts (the 1m candle close
        of that minute). This is the backtest analogue of the live selector's
        kite.ltp() sample at the 120s grid instant."""
        minute_start = (ts // 60) * 60
        # Fast path: serve from the preloaded day if this minute belongs to it.
        # ── PRELOAD_SCOPED ── same rule: only trust a MISS as authoritative
        # when the whole day is cached. Under a scoped cache an unknown symbol
        # means "not in scope", not "no print".
        if self._cache_day_start is not None \
                and self._cache_day_start <= minute_start < self._cache_day_start + 86400:
            d = self._by_sym_min.get(tradingsymbol)
            if d is not None:
                cdl = d.get(minute_start)
                return float(cdl.close) if cdl is not None else None
            if self._cache_expiry is None:
                return None

        # Fallback: out-of-cache minute (rare) → single query on shared conn.
        row = self._c.execute(
            """
            SELECT close FROM backtest_candles_1m
            WHERE tradingsymbol = ? AND ts = ?
            """,
            (tradingsymbol, minute_start),
        ).fetchone()
        return float(row["close"]) if row else None

    def contracts_active_on_day(
        self, underlying: str, day_start_epoch: int,
        expiry: Optional[str] = None
    ) -> List[dict]:
        """Distinct (tradingsymbol, strike, instrument_type, expiry) that have
        ANY candle on this day — the historical instrument universe for that day,
        rebuilt from the corpus (NOT from today's instruments.csv)."""
        # Ensure the day is loaded, then serve the contract list from cache.
        # ── PRELOAD_SCOPED ── `expiry` scopes BOTH the preload and the
        # returned universe. A caller that already filters to one expiry (IC,
        # TSG) should pass it: same result list, a fraction of the work. A
        # caller passing None still gets the whole day, and a cache scoped to
        # some OTHER expiry is correctly treated as a miss and reloaded.
        if not (self._cache_day_start == day_start_epoch
                and self._cache_underlying == underlying
                and self._cache_expiry == expiry):
            self.preload_day(underlying, day_start_epoch, expiry=expiry)
        # Return copies so callers can't mutate cached dicts.
        return [dict(x) for x in self._contracts]

    # ---------------------------------------------------------------
    # 1-SECOND ADJUDICATION  (ambiguous-fill resolution only)
    # ---------------------------------------------------------------
    def has_1s_for_minute(self, tradingsymbol: str, minute_start_epoch: int) -> bool:
        row = self._c.execute(
            """
            SELECT 1 FROM backtest_candles_1s
            WHERE tradingsymbol = ? AND ts >= ? AND ts < ?
            LIMIT 1
            """,
            (tradingsymbol, minute_start_epoch, minute_start_epoch + 60),
        ).fetchone()
        return row is not None

    def seconds_for_minute(
        self, tradingsymbol: str, minute_start_epoch: int
    ) -> List[BTSecond]:
        """The 1s bars within one minute, ascending. Empty if none recorded."""
        rows = self._c.execute(
            """
            SELECT ts, open, high, low, close
            FROM backtest_candles_1s
            WHERE tradingsymbol = ? AND ts >= ? AND ts < ?
            ORDER BY ts ASC
            """,
            (tradingsymbol, minute_start_epoch, minute_start_epoch + 60),
        ).fetchall()
        return [BTSecond(r["ts"], r["open"], r["high"], r["low"], r["close"])
                for r in rows]