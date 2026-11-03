#!/usr/bin/env python3
# tma2_bar_probe.py — READ-ONLY. Timberwolf (TMA_V2): for each signal the
# recorded data produces on a day, how close was that 5m bar to failing?
# Uses exactly the live engine's pieces (prior 5 sessions' 5m bars + the day,
# EMA 13/55/89/144 stack, maxExt gate, EMA144 slope gate) on the RECORDED 1m
# spot candles the parity check replays.
#
# Per signal it prints:
#   * the EMA stack links (points) — the tightest link is the edge
#   * ext% vs the maxExt gate, and the EMA144 slope over the last 6 bars
#   * ROBUSTNESS: by how many points the signal bar's CLOSE could have been
#     lower / higher and still emit this signal (small = borderline, so a
#     tick-built candle a point or two off flips it)
#   * GAP TEST: the same day with that bar's LAST minute removed, run through
#     the OLD engine (bar dropped for the rest of the day) and the NEW one
#     (apply_tma_gap_close: bar closed one minute late) — what a missing
#     minute would have done
#
#   python3 tma2_bar_probe.py                    # 2026-11-02, whole day
#   python3 tma2_bar_probe.py --day 2026-11-02 --from 13:45 --to 14:45
#   (--repo /path/to/scalp-app if not /Users/anbu/dev/scalp-app)
# Writes nothing.

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def hhmm(ts):
    return datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-11-02")
    ap.add_argument("--from", dest="t0", default="14:00")
    ap.add_argument("--to", dest="t1", default="14:45")
    ap.add_argument("--repo", default="/Users/anbu/dev/scalp-app")
    ap.add_argument("--hole", default=None, metavar="HH:MM-HH:MM",
                    help="what-if: the live feed was DOWN for this window (e.g. a Mac asleep / power cut with "
                         "the app left running — no backfill). Shows the day's signals with those 1m candles missing.")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.repo).expanduser() / "backend"))
    from app.parity import parity_core as PC
    from app.backtest.tma.tma_v2_engine import (build_signals_v2, compute_state_v2, warmup_bars,
                                                SLOPE_BARS)
    from app.backtest.pst.pst_indicators import aggregate

    d = date.fromisoformat(a.day)
    cfg, notes = PC.config_for(d, "TMA_V2")
    cfg = cfg or {}
    max_ext = float(cfg.get("max_extension_pct") or 0.8)
    slope = bool(cfg.get("ema144_slope_gate", True))
    print(f"TMA_V2 {a.day} · maxExt {max_ext}% · slope gate {'ON' if slope else 'OFF'}"
          + ("" if cfg else "  (no saved config for the day — live defaults assumed)"))

    pack, _ = PC.build_pack(d)
    try:
        c = sqlite3.connect(pack)
        days = [r[0] for r in c.execute(
            "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') FROM backtest_candles_1m "
            "WHERE underlying='NIFTY' AND instrument_type='SPOT' ORDER BY 1")]
        if a.day not in days:
            sys.exit(f"no recorded spot for {a.day}")
        prior = [x for x in days if x < a.day][-5:]

        def spot(ds):
            s = PC.day_start(date.fromisoformat(ds))
            rows = [dict(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4]) for r in c.execute(
                "SELECT ts,open,high,low,close FROM backtest_candles_1m WHERE underlying='NIFTY' "
                "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts", (s, s + 86400))]
            return rows, s
        warm_sessions = [spot(x) for x in prior]
        today, ds = spot(a.day)
    finally:
        try:
            os.unlink(pack)
        except Exception:
            pass
    if len(prior) < 5:
        print(f"WARNING: only {len(prior)} prior sessions recorded — EMA144 not fully warm")
    w5 = warmup_bars(warm_sessions, 5)
    e0, e1 = ds + (9 * 60 + 15) * 60, ds + 15 * 3600

    def closed(rows, gap_close):
        bars = aggregate(rows, 5, ds)
        out = []
        for i, b in enumerate(bars):
            if b["complete"]:
                out.append(b)
            elif gap_close and i < len(bars) - 1:
                out.append(dict(b, complete=True))
        return out

    def signals(bars_today, ret_state=False):
        bars = w5 + bars_today
        st = compute_state_v2(bars)
        r = build_signals_v2(bars, len(w5), e0, e0, e1, tf_s=300, state=st,
                             max_extension_pct=max_ext, slope_gate=slope)
        return (r["signals"], st, bars) if ret_state else r["signals"]

    sigs, st, bars = signals(closed(today, False), True)
    print(f"{len(sigs)} signal(s) on recorded data: "
          + (", ".join(f"{hhmm(s['ts'])} {s['side']}" for s in sigs) or "none"))
    n_w = len(w5)
    for s in sigs:
        i = next(k for k in range(n_w, len(bars)) if bars[k]["ts"] + 300 == s["ts"])
        b = bars[i]
        e13, e55, e89, e144 = (st[k][i] for k in ("e13", "e55", "e89", "e144"))
        sgn = 1 if s["side"] == "CE" else -1
        links = {"13-55": sgn * (e13 - e55), "55-89": sgn * (e55 - e89), "89-144": sgn * (e89 - e144)}
        tight = min(links, key=links.get)
        ext = abs(e13 - e89) / b["close"] * 100
        sl = e144 - st["e144"][i - SLOPE_BARS] if i - SLOPE_BARS >= 0 else None
        print(f"\n── signal {hhmm(s['ts'])} {s['side']} (bar {hhmm(b['ts'])}–{hhmm(s['ts'])}, close {b['close']:.2f})")
        print("   stack links (pts, + = in order): " + " · ".join(f"{k} {v:+.2f}" for k, v in links.items())
              + f"   → tightest {tight} {links[tight]:+.2f}")
        print(f"   ext {ext:.3f}% vs maxExt {max_ext}%  (headroom {max_ext - ext:+.3f}% = {(max_ext - ext) / 100 * b['close']:+.1f} pts of EMA gap)")
        if slope:
            print(f"   EMA144 slope over {SLOPE_BARS} bars: {sl:+.2f} pts" if sl is not None else "   slope: n/a")
        pv = [k for k in range(max(n_w, i - 12), i) if st["stack_up" if sgn > 0 else "stack_dn"][k] is True]
        print(f"   stack was already in order on {len(pv)} of the previous 12 bars"
              + ("  ← flickering" if pv else ""))

        # robustness to the signal bar's CLOSE
        def survives(delta):
            bt = [dict(x) for x in bars[:i + 1]]
            bt[-1]["close"] = b["close"] + delta
            stt = compute_state_v2(bt)
            r = build_signals_v2(bt, n_w, e0, e0, e1, tf_s=300, state=stt,
                                 max_extension_pct=max_ext, slope_gate=slope)
            return any(x["ts"] == s["ts"] and x["side"] == s["side"] for x in r["signals"])
        lo = hi = None
        for k in range(1, 81):
            if lo is None and not survives(-0.25 * k):
                lo = 0.25 * (k - 1)
            if hi is None and not survives(0.25 * k):
                hi = 0.25 * (k - 1)
            if lo is not None and hi is not None:
                break
        fmt = lambda v: ">20" if v is None else f"{v:.2f}"      # noqa: E731
        print(f"   ROBUSTNESS: signal survives the bar's close being up to {fmt(lo)} pts LOWER or {fmt(hi)} pts HIGHER")

        # missing last minute
        last_min = b["ts"] + 240
        gapped = [r for r in today if r["ts"] != last_min]
        if len(gapped) == len(today):
            print("   GAP TEST: (that minute is not in the recorded data)")
        else:
            def first_at_or_after(sg):
                x = [y for y in sg if y["ts"] >= s["ts"]]
                return f"{hhmm(x[0]['ts'])} {x[0]['side']}" if x else "no further signal today"
            old = first_at_or_after(signals(closed(gapped, False)))
            new = first_at_or_after(signals(closed(gapped, True)))
            print(f"   GAP TEST (minute {hhmm(last_min)} missing): OLD engine → {old}   ·   NEW (gap-close) → {new}")

    if a.hole:
        h0, h1 = a.hole.split("-")
        hlo = ds + int(h0[:2]) * 3600 + int(h0[3:5]) * 60
        hhi = ds + int(h1[:2]) * 3600 + int(h1[3:5]) * 60
        holed = [r for r in today if not (hlo <= r["ts"] < hhi)]
        fmt_s = lambda sg: ", ".join(f"{hhmm(x['ts'])} {x['side']}" for x in sg) or "none"   # noqa: E731
        so, sn = signals(closed(holed, False)), signals(closed(holed, True))
        print(f"\n── WHAT-IF: 1m spot candles {a.hole} missing ({len(today) - len(holed)} minutes), no backfill")
        print(f"   complete day            → {fmt_s(sigs)}")
        print(f"   with the hole (as built) → {fmt_s(so)}")
        print(f"   with the hole + gap-close→ {fmt_s(sn)}")
        bt = [dict(x) for x in (w5 + closed(holed, False))]
        sh = compute_state_v2(bt)
        i_ref = next((k for k in range(len(bt) - 1, n_w - 1, -1) if hhmm(bt[k]["ts"]) == "14:15"), None)
        if i_ref is not None:
            j = next(k for k in range(n_w, len(bars)) if hhmm(bars[k]["ts"]) == "14:15")
            print(f"   EMA13 at the 14:15 bar: {st['e13'][j]:.2f} complete vs {sh['e13'][i_ref]:.2f} with the hole · "
                  f"EMA55 {st['e55'][j]:.2f} vs {sh['e55'][i_ref]:.2f}")

    lo_t = ds + int(a.t0[:2]) * 3600 + int(a.t0[3:]) * 60
    hi_t = ds + int(a.t1[:2]) * 3600 + int(a.t1[3:]) * 60
    print(f"\n{'bar':>5} {'close':>9} {'e13':>9} {'e55':>9} {'e89':>9} {'e144':>9} {'stack':>6} {'ext%':>6}")
    for i in range(n_w, len(bars)):
        b = bars[i]
        if not (lo_t <= b["ts"] <= hi_t):
            continue
        up, dn = st["stack_up"][i], st["stack_dn"][i]
        tag = "UP" if up else "DOWN" if dn else "-"
        ext = abs(st["e13"][i] - st["e89"][i]) / b["close"] * 100
        print(f"{hhmm(b['ts']):>5} {b['close']:9.2f} {st['e13'][i]:9.2f} {st['e55'][i]:9.2f} "
              f"{st['e89'][i]:9.2f} {st['e144'][i]:9.2f} {tag:>6} {ext:6.3f}")
    have = {r["ts"] for r in today}
    miss = sorted(m for m in range(e0, e1, 60) if m not in have)
    print("\nrecorded 1m spot minutes missing today: "
          + (", ".join(hhmm(m) for m in miss[:20]) or "none") + (" …" if len(miss) > 20 else ""))


if __name__ == "__main__":
    main()
