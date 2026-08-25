#!/usr/bin/env python3
# apply_scalp_v1_vwap_fix_20260825.py
#
# HOTFIX — fence: SCALP_V1_VWAP_FIX_20260825
# For trees that already applied the FIRST version of SCALP_V1_VWAP_20260825
# (the one that produced ZERO trades). Three defects fixed in place:
#   1. `c` inside update() is the normalized CLOSE FLOAT, not the candle
#      object — timestamps/volume getattr'd on a float returned nothing.
#   2. The `_is_warmup` guard is not reset where assumed; the day-rollover
#      reset alone guarantees session purity (warmup candles are prior days).
#   3. The live Candle carries NO volume (LTP-built) — a true VWAP is not
#      live-computable today. Parity principle: the filter becomes the
#      session average of typical price (equal weight), identical in
#      backtest and live. True volume weighting is a future CandleBuilder
#      item (per-minute volume from tick volume_traded diffs).
# Gate predicate, config keys, UI, diag "vw" — all unchanged.
#
# PREREQ: SCALP_V1_VWAP_20260825 present. Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_VWAP_FIX_20260825"
PREREQ = "SCALP_V1_VWAP_20260825"
ROOT = Path(__file__).resolve().parent
IND_REL = "app/engine/indicator_engine_pine_v1_9.py"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / IND_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


OLD = '''        # ── SCALP_V1_VWAP_20260825 ── session-anchored VWAP accumulation.
        # Skipped during warmup (prior-day candles must not pollute today's
        # session VWAP). Day rollover reset covers long-lived live instances;
        # backtest per-day context rebuilds guarantee it regardless. Typical
        # price = (H+L+C)/3, volume-weighted; zero cumulative volume -> None.
        if not self._is_warmup:
            _cts = getattr(c, "start_ts", None) or getattr(c, "ts", None) \\
                   or getattr(c, "end_ts", None)
            if _cts is not None:
                _cday = int((_cts + 19800) // 86400)   # IST day index
                if self._vwap_day != _cday:
                    self._vwap_day = _cday
                    self._vwap_pv = 0.0
                    self._vwap_v = 0.0
            _vol = float(getattr(c, "volume", 0) or 0)
            if _vol > 0:
                self._vwap_pv += ((c.high + c.low + c.close) / 3.0) * _vol
                self._vwap_v += _vol
        vwap_val = (self._vwap_pv / self._vwap_v) if self._vwap_v > 0 else None'''

NEW = '''        # ── SCALP_V1_VWAP_20260825 (rev SCALP_V1_VWAP_FIX_20260825) ──
        # Session AVERAGE of typical price (H+L+C)/3, equal weight per candle.
        # NOTE: inside update(), o/h/l/c are normalized FLOATS; the candle
        # OBJECT (timestamps) is the `candle` parameter. No warmup flag
        # needed: warmup candles are PRIOR days, so the IST day-rollover
        # reset wipes them the moment today's first candle arrives. The live
        # Candle carries no volume (LTP-built), so true volume weighting is
        # deferred until CandleBuilder aggregates tick volume — parity
        # principle: never validate in backtest what live cannot reproduce.
        _cts = getattr(candle, "start_ts", None) or getattr(candle, "ts", None) \\
               or getattr(candle, "end_ts", None)
        if _cts is not None:
            _cday = int((_cts + 19800) // 86400)   # IST day index
            if self._vwap_day != _cday:
                self._vwap_day = _cday
                self._vwap_pv = 0.0
                self._vwap_v = 0.0
        self._vwap_pv += (h + l + c) / 3.0
        self._vwap_v += 1.0
        vwap_val = (self._vwap_pv / self._vwap_v) if self._vwap_v > 0 else None'''


def main():
    if not (ROOT / "backend" / IND_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        p = tree / IND_REL
        t = p.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {p} — already fixed")
        if PREREQ not in t:
            _die(f"prerequisite fence {PREREQ} MISSING in {p} — nothing to fix")
        n = t.count(OLD)
        if n != 1:
            _die(f"broken block matched {n} times (want 1) in {p} — file state "
                 f"differs from the first-version apply; NOTHING written")
        staged.append((p, t.replace(OLD, NEW, 1)))
    for p, t in staged:
        try:
            compile(t, str(p), "exec")
        except SyntaxError as e:
            _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — {FENCE} applied. Re-run your VWAP-On config: trades will")
    print("now appear. Protocol unchanged: filter-OFF diag run first, then")
    print("the vw-separation analysis, then the threshold sweep if earned.")


if __name__ == "__main__":
    main()
