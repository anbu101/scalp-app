#!/usr/bin/env python3
# apply_scalp_v5_parity_perf_20260825.py
#
# SCALP_V5 — EOD PARITY + BACKTEST PERFORMANCE
# fence: SCALP_V5_PARITY_PERF_20260825          (backtest-only, V5 runner)
#
# ═══ D14.1 — EOD SQUARE-OFF PARITY ════════════════════════════════════════
# FINDING (run 798a88c0): the V5 EOD block closes the leftover position on
# the DAY'S LAST 1m CANDLE, ignoring session end entirely — 223 exits stamp
# 15:30 and 40 stamp 15:31, while the live scalpv5_live_eod cron squares off
# at 15:25 (15:15 in trees carrying the EOD-safety work). That window is not
# cosmetic: 267 EOD exits carry +Rs 98L of gross at +Rs 36,874 each, and
# WITHOUT them V5 is -Rs 79.6L with every single year negative. The entire
# measured edge is realised in minutes live does not trade.
#
#   "eod_squareoff_time": ""          <- LEGACY (default): day's last candle,
#                                        byte-identical to today's results
#   "eod_squareoff_time": "15:15"     <- PARITY: buckets at/after the boundary
#                                        are not processed at all, and the
#                                        leftover position closes on the last
#                                        1m bar CLOSING at or before it.
#
# The default is deliberately LEGACY so the perf work below can be proven
# byte-identical against run 798a88c0. Flip it to the live cron time for the
# parity run; once that is validated, make it the shipped default.
#
# ═══ D14.3 — PERFORMANCE (all three byte-identical by construction) ═══════
# P1  SCOPED PRELOAD. build_selection_timeline is called WITHOUT
#     scope_to_expected_expiry, so each day preloads every expiry alive in
#     the corpus (~168 symbols / 63k candles) when V5 reads exactly one
#     (~42 / 15.75k). The flag is additive, already proven by the HA runner,
#     and cannot change selection: the boundary selector filters candidates
#     to want_expiry regardless. candle_source measured 214.1 -> 13.8 ms/day
#     — on 1,461 sim days that is ~5 minutes of a ~10 minute run.
# P2  CACHE-HITTING UNIVERSE READ. With P1 the day cache is scoped, so the
#     runner's unscoped contracts_active_on_day would MISS it and force a
#     second full-day preload — undoing P1. Scoped to the same expiry it
#     hits. Every watched symbol belongs to that expiry by construction.
# P3  BATCHED WARMUP. warmup_candles_before is always SQL (it reaches into
#     prior days, outside any preloaded day), so the runner issues one query
#     PER CONTRACT PER DAY. Replaced with ONE query per day over the whole
#     watched set, with an EXACT per-symbol fallback (see _batch_warmup).
#
# NOT INCLUDED (deliberately, and gated on the parity answer): V5
# diagnostics (Condition/MAE/MFE are empty in V5 exports) and the buy-side
# filter port — ScalpV5Engine is a separate engine from strategy_engine, so
# those are their own fence.
#
# VERIFY AFTER APPLYING:
#   1. Re-run the SAME config as 798a88c0 with eod_squareoff_time unset.
#      Trades/net/DD must match EXACTLY (this is the regression test) and
#      the run should be materially faster.
#   2. Then the parity run: eod_squareoff_time = your live cron time.
#
# Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V5_PARITY_PERF_20260825"
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/scalpv5/backtest_scalpv5_runner.py"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / RN_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab):
    c = t.count(o)
    if c != 1:
        _die(f"anchor '{lab}' matched {c} times (want 1) — NOTHING written")
    return t.replace(o, n, 1)


# ── H1: the batched-warmup helper ─────────────────────────────────────────
H1_OLD = "def _empty_summary() -> dict:"
H1_NEW = '''def _batch_warmup(conn, day_map: Dict[str, list], limit: int) -> Dict[str, list]:
    """── SCALP_V5_PARITY_PERF_20260825 ── ONE warmup query for the whole day.

    EQUIVALENCE with N x CandleSource.warmup_candles_before: for each symbol
    that method returns the most recent `limit` candles strictly BEFORE that
    symbol's own first candle of the day. Here one query covers a generous
    window across every watched symbol; a symbol whose slice holds >= limit
    candles yields exactly the same tail (the last `limit` of a superset that
    ends at the same cutoff ARE the globally most recent `limit`). A symbol
    whose slice holds FEWER is omitted from the result and refetched
    individually by the caller — the window may have clipped older history,
    and an approximation there would silently change EMA seeds. So: faster
    on the common path, never different.

    Returns {symbol: [{ts, open, high, low, close}, ...]} ascending.
    """
    if not day_map or limit <= 0:
        return {}
    cutoffs = {s: int(dc[0].ts) for s, dc in day_map.items() if dc}
    if not cutoffs:
        return {}
    hi = max(cutoffs.values())
    # A session holds ~375 1m candles; 3x that many sessions plus a week of
    # slack covers holidays and thin contracts without unbounded scanning.
    span_days = int(limit // 375) * 3 + 7
    lo_w = min(cutoffs.values()) - span_days * 86400
    syms = sorted(cutoffs)
    acc: Dict[str, list] = {}
    CHUNK = 400          # SQLite's variable ceiling is 999 — stay well under
    for i in range(0, len(syms), CHUNK):
        part = syms[i:i + CHUNK]
        q = ("SELECT tradingsymbol, ts, open, high, low, close "
             "FROM backtest_candles_1m "
             f"WHERE tradingsymbol IN ({','.join('?' * len(part))}) "
             "AND ts >= ? AND ts < ? "
             "ORDER BY tradingsymbol, ts")
        for r in conn.execute(q, (*part, lo_w, hi)):
            s = r[0]
            cut = cutoffs.get(s)
            ts = int(r[1])
            if cut is None or ts >= cut:
                continue
            acc.setdefault(s, []).append(
                {"ts": ts, "open": float(r[2]), "high": float(r[3]),
                 "low": float(r[4]), "close": float(r[5])})
    return {s: v[-limit:] for s, v in acc.items() if len(v) >= limit}


def _empty_summary() -> dict:'''

# ── A1: config ────────────────────────────────────────────────────────────
A1_OLD = '''    sess_end = sess.get("end", "15:20")'''
A1_NEW = '''    sess_end = sess.get("end", "15:20")
    # ── SCALP_V5_PARITY_PERF_20260825 ── EOD square-off parity (D14.1).
    # "" / absent = LEGACY: square off on the day's LAST candle (stamps
    # 15:30/15:31 — later than live trades). "HH:MM" = PARITY: the day stops
    # at that boundary and the leftover position closes on the last 1m bar
    # closing at or before it. Set this to the live cron time.
    _eod_hm = str(cfg.get("eod_squareoff_time", "") or "").strip()
    eod_sod = None            # seconds from IST midnight, or None = legacy
    if _eod_hm:
        try:
            _eh, _em = _eod_hm.split(":")
            _eh, _em = int(_eh), int(_em)
            if 0 <= _eh <= 23 and 0 <= _em <= 59:
                eod_sod = _eh * 3600 + _em * 60
        except (ValueError, AttributeError):
            eod_sod = None'''

# ── A2: scoped timeline (P1) ──────────────────────────────────────────────
A2_OLD = '''        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=lo,
            cfg=sel_cfg, strategy_id=strategy_id,
        )'''
A2_NEW = '''        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=lo,
            cfg=sel_cfg, strategy_id=strategy_id,
            # ── SCALP_V5_PARITY_PERF_20260825 (P1) ── additive flag, proven by
            # the HA runner: the boundary selector filters candidates to
            # want_expiry regardless, so scoping cannot change WHICH contracts
            # are selected — it only stops materialising rows destined for the
            # discard pile, and flips preload onto idx_bt1m_under_exp_ts.
            scope_to_expected_expiry=True,
        )'''

# ── A3: cache-hitting universe read (P2) ──────────────────────────────────
A3_OLD = '''        meta_map = {
            c["tradingsymbol"]: {"side": c["instrument_type"], "strike": float(c["strike"])}
            for c in src.contracts_active_on_day(underlying, lo)
        }'''
A3_NEW = '''        # ── SCALP_V5_PARITY_PERF_20260825 (P2) ── read the universe SCOPED to
        # the same expiry the timeline just preloaded. Unscoped here would be
        # a cache MISS and would force a second, full-day preload, undoing P1.
        # Every watched symbol belongs to this expiry by construction.
        meta_map = {
            c["tradingsymbol"]: {"side": c["instrument_type"], "strike": float(c["strike"])}
            for c in src.contracts_active_on_day(underlying, lo, expiry=current_expiry)
        }'''

# ── A4: warmup pre-pass + batch (P3) ──────────────────────────────────────
A4_OLD = '''        for sym in sorted(watched):
            day_candles = src.candles_1m_for_symbol_day(sym, lo)
            if not day_candles:
                continue'''
A4_NEW = '''        # ── SCALP_V5_PARITY_PERF_20260825 (P3) ── pre-pass over the watched
        # set: day candles are cache-served (free after the preload above),
        # then ONE batched warmup query replaces one query per contract.
        _warm_limit = WARMUP_CANDLES * tf_minutes
        _day_map: Dict[str, list] = {}
        for _s in sorted(watched):
            _dc = src.candles_1m_for_symbol_day(_s, lo)
            if _dc:
                _day_map[_s] = _dc
        _warm_batch = _batch_warmup(conn, _day_map, _warm_limit)

        for sym in sorted(watched):
            day_candles = _day_map.get(sym)
            if not day_candles:
                continue'''

A5_OLD = '''            warm = src.warmup_candles_before(sym, day_candles[0].ts, WARMUP_CANDLES * tf_minutes)
            if warm:
                w1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in warm]
                w3m = _aggregate_1m_to_tf(w1m, tf_minutes)'''
A5_NEW = '''            # ── SCALP_V5_PARITY_PERF_20260825 (P3) ── batched warmup, with an
            # EXACT per-symbol fallback whenever the batch could not guarantee
            # the full depth (see _batch_warmup).
            w1m = _warm_batch.get(sym)
            if w1m is None:
                _wc = src.warmup_candles_before(sym, day_candles[0].ts, _warm_limit)
                w1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in _wc]
            if w1m:
                w3m = _aggregate_1m_to_tf(w1m, tf_minutes)'''

# ── A6: stop the day at the parity boundary ───────────────────────────────
A6_OLD = '''        for bucket_start in ordered_buckets:
            if cancel_cb and cancel_cb():
                break'''
A6_NEW = '''        for bucket_start in ordered_buckets:
            # ── SCALP_V5_PARITY_PERF_20260825 (D14.1) ── with a square-off
            # time configured the day STOPS there: no entry and no exit is
            # evaluated on candles the live engine would never trade.
            if eod_sod is not None and bucket_start >= lo + eod_sod:
                break
            if cancel_cb and cancel_cb():
                break'''

# ── A7: square off at the boundary bar ────────────────────────────────────
A7_OLD = '''            if day_bars:
                last = day_bars[-1]'''
A7_NEW = '''            # ── SCALP_V5_PARITY_PERF_20260825 (D14.1) ── close on the last 1m
            # bar CLOSING at or before the boundary (bar ts + 60 <= cutoff).
            # Legacy (eod_sod None) keeps the day's last bar — 15:30/15:31.
            if day_bars and eod_sod is not None:
                _cut = lo + eod_sod
                day_bars = [b for b in day_bars if int(b.ts) + 60 <= _cut]
            if day_bars:
                last = day_bars[-1]'''


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        p = tree / RN_REL
        t = p.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {p} — already applied")
        for lab, o, n in [("H1", H1_OLD, H1_NEW), ("A1", A1_OLD, A1_NEW),
                          ("A2", A2_OLD, A2_NEW), ("A3", A3_OLD, A3_NEW),
                          ("A4", A4_OLD, A4_NEW), ("A5", A5_OLD, A5_NEW),
                          ("A6", A6_OLD, A6_NEW), ("A7", A7_OLD, A7_NEW)]:
            t = _ro(t, o, n, f"{tree.name}:{lab}")
        staged.append((p, t))
    for p, t in staged:
        try:
            compile(t, str(p), "exec")
        except SyntaxError as e:
            _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied.")
    print()
    print("STEP 1 (REGRESSION, do this first): re-run the SAME config as run")
    print("  798a88c0 with eod_squareoff_time UNSET. Trades / net / max DD must")
    print("  match EXACTLY — that proves the three perf changes are behaviour-")
    print("  neutral. Note the wall clock: the scoped preload alone should take")
    print("  the biggest bite out of the ~10 minutes.")
    print()
    print("STEP 2 (THE REAL TEST): set eod_squareoff_time to your live cron")
    print("  time — check it with:")
    print("     grep -n 'scalpv5_live_eod' -A3 backend/app/api_server.py")
    print("  — and re-run. Upload the export: 265 of 267 EOD winners currently")
    print("  hold past 15:15, and they carry 100% of V5's P&L, so this run is")
    print("  the one that says whether V5's edge is real or a stamping artifact.")


if __name__ == "__main__":
    main()
