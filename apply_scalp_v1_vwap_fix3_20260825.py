#!/usr/bin/env python3
# apply_scalp_v1_vwap_fix3_20260825.py
#
# HOTFIX v3 — fence: SCALP_V1_VWAP_FIX_20260825
# For the INTERMEDIATE applied state (candle-object getattrs already present,
# but still VOLUME-weighted). Live candles carry no volume, so the weighted
# average never accumulates -> vwap None -> fail-closed blocks every entry
# (the observed zero-trade run). This replaces the span with the final
# equal-weight session average of typical price.
#
# Landmark-based (whitespace-immune):
#   START: line containing "SCALP_V1_VWAP_20260825" and "session-anch"
#   END:   line containing "vwap_val = (self._vwap_pv / self._vwap_v)"
# GUARDS: landmarks unique, span < 40 lines, span must contain '"volume"'
# (the defect being fixed) and must NOT contain 'self._vwap_v += 1.0'
# (already corrected). Staged file compiled before write. Idempotent.
# Run from the repo root.

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

START_A, START_B = "SCALP_V1_VWAP_20260825", "session-anch"
END_MARK = "vwap_val = (self._vwap_pv / self._vwap_v)"
BROKEN_SIG = '"volume"'
FIXED_SIG = "self._vwap_v += 1.0"

NEW_BLOCK = '''        # ── SCALP_V1_VWAP_20260825 (rev SCALP_V1_VWAP_FIX_20260825) ──
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
        vwap_val = (self._vwap_pv / self._vwap_v) if self._vwap_v > 0 else None
'''


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def main():
    if not (ROOT / "backend" / IND_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        p = tree / IND_REL
        text = p.read_text()
        if PREREQ not in text:
            _die(f"prerequisite fence {PREREQ} MISSING in {p}")
        lines = text.splitlines(True)
        starts = [i for i, l in enumerate(lines) if START_A in l and START_B in l]
        ends = [i for i, l in enumerate(lines) if END_MARK in l]
        if len(starts) != 1 or len(ends) != 1:
            _die(f"landmarks matched start={len(starts)} end={len(ends)} "
                 f"(want 1/1) in {p} — NOTHING written")
        i0, i1 = starts[0], ends[0]
        if not (i0 < i1 and (i1 - i0) < 40):
            _die(f"landmark span {i0}..{i1} implausible in {p} — NOTHING written")
        span = "".join(lines[i0:i1 + 1])
        if FIXED_SIG in span:
            _die(f"{p} already carries the corrected equal-weight block — "
                 f"NOTHING written (this is the desired end state)")
        if BROKEN_SIG not in span:
            _die(f"span in {p} lacks the '{BROKEN_SIG}' defect signature — "
                 f"unrecognized state; paste the span to Claude. NOTHING written")
        new_text = "".join(lines[:i0]) + NEW_BLOCK + "".join(lines[i1 + 1:])
        try:
            compile(new_text, str(p), "exec")
        except SyntaxError as e:
            _die(f"staged content for {p} does not compile: {e} — NOTHING written")
        staged.append((p, new_text, i1 - i0 + 1))
    for p, t, n in staged:
        p.write_text(t)
        print(f"PATCHED: {p} (replaced {n}-line span)")
    print(f"\nDONE — {FENCE} applied. Restart backend, re-run VWAP-On on a short")
    print("range: trades will now appear. Then: sealed config, filter OFF, full")
    print("range, upload export for the vw-separation analysis.")


if __name__ == "__main__":
    main()
