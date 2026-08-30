# backend/app/backtest/cbo/test_cbo_tf_close.py
#
# ── CBO_TF_CLOSE_20260830 REGRESSION ── pins the close-confirmed breakout:
# only the COMPLETED tf candle closing through the previous candle's level
# fires; wicks are invisible to this mode. Pure engine test — no DB, no app
# imports:
#     python3 backend/app/backtest/cbo/test_cbo_tf_close.py

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cbo_v1_engine import CboBar, cbo_signals, UP, DOWN  # noqa: E402

FAILED = []


def chk(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          f"{('  ' + extra) if extra else ''}")
    if not cond:
        FAILED.append(label)


A = 1_700_000_000
M = 60


def bar(minute, o, h, l, c):
    return CboBar(ts=A + minute * M, open=o, high=h, low=l, close=c)


def bucket(m0, bars5):
    """Five 1m bars for the bucket starting at minute m0."""
    return [bar(m0 + i, *b) for i, b in enumerate(bars5)]


REF = bucket(0, [(100, 105, 95, 100)] + [(100, 101, 99, 100)] * 4)
# reference: high 105, low 95

print("\n── 1. wicks are invisible to tf_close ────────────────────────────")
# Forming bucket wicks to 108 twice but CLOSES the 5m candle at 104 (<105).
wicky = REF + bucket(5, [(100, 108, 99, 103), (103, 108, 101, 102),
                         (102, 104, 100, 103), (103, 105, 102, 104),
                         (104, 104.8, 103, 104)])
chk("mode=high fires on the first wick (baseline behaviour unchanged)",
    len(cbo_signals(wicky, anchor_ts=A, trigger_source="high")) == 1)
chk("mode=tf_close does NOT fire — the candle closed back at 104",
    cbo_signals(wicky, anchor_ts=A, trigger_source="tf_close") == [])

print("\n── 2. a genuine close-through fires exactly once ─────────────────")
thru = REF + bucket(5, [(100, 103, 99, 102), (102, 104, 101, 103),
                        (103, 105, 102, 104), (104, 106, 103, 105),
                        (105, 107, 104, 106.5)])   # 5m close 106.5 > 105
s = cbo_signals(thru, anchor_ts=A, trigger_source="tf_close")
chk("exactly one UP signal", len(s) == 1 and s[0].direction == UP)
if s:
    chk("trigger is the bucket's LAST sub-bar (minute 9)",
        s[0].trigger_ts == A + 9 * M)
    chk("fill is the FIRST minute of the next bucket (minute 10)",
        s[0].fill_ts == A + 10 * M)
    chk("level is the reference high (105), stop its low (95)",
        s[0].level == 105.0 and s[0].stop_level == 95.0)
    chk("spot recorded is the tf CLOSE (106.5)", s[0].spot == 106.5)
chk("earlier sub-bars breaching intrabar did not fire early "
    "(minute 8 closed at 105 == level, minute 9 confirms)",
    s and s[0].trigger_ts == A + 9 * M)

print("\n── 3. DOWN mirror ────────────────────────────────────────────────")
dn = REF + bucket(5, [(100, 101, 97, 98), (98, 99, 96, 97),
                      (97, 98, 95, 96), (96, 97, 94, 95),
                      (95, 96, 92, 93.5)])          # close 93.5 < 95
d = cbo_signals(dn, anchor_ts=A, trigger_source="tf_close")
chk("one DOWN signal on a close through the low",
    len(d) == 1 and d[0].direction == DOWN and d[0].level == 95.0)

print("\n── 4. ambiguity is structurally impossible ───────────────────────")
# A violent outside candle: wicks through BOTH levels, closes above high.
out = REF + bucket(5, [(100, 107, 93, 106), (106, 108, 94, 106),
                       (106, 107, 105, 106), (106, 107, 105, 106),
                       (106, 107, 105, 106.5)])
b = cbo_signals(out, anchor_ts=A, trigger_source="tf_close",
                both_side_policy="both")
chk("policy=both still yields ONE signal — a close is one price and "
    "cannot breach both levels (D8's forced-loss path unreachable here)",
    len(b) == 1 and b[0].direction == UP)

print("\n── 5. a missing last minute cannot confirm ───────────────────────")
gap = REF + bucket(5, [(100, 103, 99, 102), (102, 104, 101, 103),
                       (103, 105, 102, 104), (104, 106, 103, 106.5)])
# minute 9 (the bucket's last) absent — deterministic no-confirm, no guess
chk("bucket with its last minute missing emits nothing",
    cbo_signals(gap, anchor_ts=A, trigger_source="tf_close") == [])

print("\n── 6. reference still rolls and re-fires next bucket ─────────────")
roll = thru + bucket(10, [(106, 108, 105, 107), (107, 109, 106, 108),
                          (108, 110, 107, 109), (109, 111, 108, 110),
                          (110, 112, 109, 111)])   # closes 111 > new ref 107
r = cbo_signals(roll, anchor_ts=A, trigger_source="tf_close")
chk("two signals across two buckets, second references the rolled bar",
    len(r) == 2 and r[1].ref.bucket_ts == A + 300 and r[1].level == 107.0)

print("\n── 7. validation ─────────────────────────────────────────────────")
try:
    cbo_signals(REF, anchor_ts=A, trigger_source="5m_close")
    chk("unknown trigger_source still raises", False)
except ValueError:
    chk("unknown trigger_source still raises", True)

print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CBO TF-CLOSE REGRESSION CHECKS PASSED")