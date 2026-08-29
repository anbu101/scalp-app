# edit bodies for stock_backfill.py, fence STOCK_FRAME_GUARD_20260828
OLD_WRITE = '''                        expiries_seen: set) -> int:
    """Split by IST day, stamp the front MONTHLY expiry, synthesize the
    monthly symbol, delete-then-insert (overlap-safe, dhan_backfill rule)."""
    written = 0
    for i in range(len(series)):
        ts = series.timestamp[i]
        if i >= len(series.strike) or i >= len(series.close):
            continue
        strike = series.strike[i]
        if not strike:
            continue
        d = _ist_day(ts)
'''

NEW_WRITE = '''                        expiries_seen: set,
                        spot_by_day: Optional[Dict[str, float]] = None,
                        reject: Optional[Dict[str, int]] = None) -> int:
    """Split by IST day, stamp the front MONTHLY expiry, synthesize the
    monthly symbol, delete-then-insert (overlap-safe, dhan_backfill rule).

    ── STOCK_FRAME_GUARD_20260828 ──
    `strike` is whatever Dhan returned and the tradingsymbol is COMPOSED from
    it. After a split or bonus Dhan can serve the same (day, expiry) in two
    price frames; the composed symbols then differ, the surrogate tokens
    differ, and delete-then-insert on (tradingsymbol, ts) cannot collapse
    them. Both ladders land in the corpus and an ATM selector keyed on spot
    silently picks from whichever one sits near the (back-adjusted) spot.
    HDFCBANK carried 3.3M such rows across 55 months before anyone noticed.

    So: every row is validated against that IST day's spot before it is
    written. Outside [FRAME_LO, FRAME_HI] x spot it is REJECTED and counted,
    never written. Fail-closed — a row we cannot place in a frame is worth
    less than nothing, because it looks exactly like a real one later.
    """
    written = 0
    for i in range(len(series)):
        ts = series.timestamp[i]
        if i >= len(series.strike) or i >= len(series.close):
            continue
        strike = series.strike[i]
        if not strike:
            continue
        d = _ist_day(ts)
        if spot_by_day is not None:                  # ── STOCK_FRAME_GUARD ──
            sp = spot_by_day.get(d.isoformat())
            if not sp:
                if reject is not None:
                    reject["no_spot_ref"] = reject.get("no_spot_ref", 0) + 1
                continue
            ratio = float(strike) / sp
            if not (_FRAME_LO <= ratio <= _FRAME_HI):
                if reject is not None:
                    reject["out_of_frame"] = reject.get("out_of_frame", 0) + 1
                    lo = reject.setdefault("_ratios", [])
                    if len(lo) < 20:
                        lo.append(round(ratio, 3))
                continue
'''

OLD_SIG = '''def _write_stock_series(cur, series: RollingSeries, *, underlying: str,
                        type_code: str, days_seen: set,
'''
NEW_SIG = OLD_SIG   # unchanged; the tail of the signature carries the new args

OLD_CALL = '''                rows += _write_stock_series(
                    cur, series, underlying=underlying, type_code=type_code,
                    days_seen=days_seen, expiries_seen=expiries_seen)'''
NEW_CALL = '''                rows += _write_stock_series(
                    cur, series, underlying=underlying, type_code=type_code,
                    days_seen=days_seen, expiries_seen=expiries_seen,
                    spot_by_day=spot_by_day, reject=reject)   # STOCK_FRAME_GUARD'''

OLD_SETUP = '''    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_candles_schema(conn)
    cur = conn.cursor()
    for (cfrom, cto) in chunk_list:'''
NEW_SETUP = '''    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_candles_schema(conn)
    cur = conn.cursor()
    # ── STOCK_FRAME_GUARD_20260828 ── day -> spot, the reference every option
    # row is placed against. Built once; the corpus is single-underlying so
    # this is small (a few thousand entries for a 6-year window).
    spot_by_day: Dict[str, float] = {}
    for _d, _sp in cur.execute(
            "SELECT date(ts,'unixepoch','+330 minutes') d, avg(close) "
            "FROM backtest_candles_1m WHERE underlying=? "
            "AND instrument_type='SPOT' GROUP BY d", (underlying,)):
        if _sp:
            spot_by_day[_d] = float(_sp)
    reject: Dict[str, int] = {}
    if not spot_by_day:
        # options-only run into an empty corpus: nothing to validate against.
        # Degrade loudly rather than rejecting every row.
        spot_by_day = None          # type: ignore[assignment]
        write_audit_log(
            f"[BACKTEST][STOCK_OPT][{underlying}] FRAME GUARD DISABLED — no "
            f"SPOT rows in the corpus. Backfill spot FIRST, or re-run the "
            f"options pass afterwards, then scan with corpus_health.")
    for (cfrom, cto) in chunk_list:'''

OLD_REPORT = '''    report = {"requests": calls, "rows_upserted": rows,
              "days_covered": len(days_seen),
              "expiries": sorted(expiries_seen),
              "empty_windows": empty_windows, "errors": errors}'''
NEW_REPORT = '''    # ── STOCK_FRAME_GUARD_20260828 ──
    ratios = reject.pop("_ratios", [])
    rejected = reject.get("out_of_frame", 0) + reject.get("no_spot_ref", 0)
    considered = rows + rejected
    reject_share = (rejected / considered) if considered else 0.0
    report = {"requests": calls, "rows_upserted": rows,
              "days_covered": len(days_seen),
              "expiries": sorted(expiries_seen),
              "empty_windows": empty_windows, "errors": errors,
              "frame_guard": ("off" if spot_by_day is None else "on"),
              "rejected_out_of_frame": reject.get("out_of_frame", 0),
              "rejected_no_spot_ref": reject.get("no_spot_ref", 0),
              "reject_share": round(reject_share, 4),
              "sample_reject_ratios": ratios}
    if reject_share > _FRAME_REJECT_ALARM:
        msg = (f"{underlying}: frame guard rejected {reject_share:.1%} of "
               f"option rows ({rejected}/{considered}); sample strike/spot "
               f"ratios {ratios}. A ratio clustered near a whole number means "
               f"a split/bonus frame split — check corpus_health --scan.")
        report["frame_alarm"] = msg
        write_audit_log(f"[BACKTEST][STOCK_OPT][{underlying}] ALARM: {msg}")'''

OLD_CONST = '''SESSION_OPEN_MIN'''  # unused sentinel

OLD_IMPORTS_ANCHOR = '''def _offsets(n: int) -> List[str]:'''
NEW_IMPORTS_ANCHOR = '''# ── STOCK_FRAME_GUARD_20260828 ── sane band for ONE price frame, as a
# multiple of that day's spot. Shared with corpus_health so the write-time
# guard and the after-the-fact scan can never disagree about what "in frame"
# means. Import is lazy-safe: a bare copy keeps the CLI usable standalone.
try:
    from app.backtest.util.corpus_health import FRAME_LO as _FRAME_LO, \\
        FRAME_HI as _FRAME_HI
except Exception:                                    # standalone harness
    _FRAME_LO, _FRAME_HI = 0.60, 1.45
_FRAME_REJECT_ALARM = 0.05      # >5% rejected = something structural, not noise


def _offsets(n: int) -> List[str]:'''
