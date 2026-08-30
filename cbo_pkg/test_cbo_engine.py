# backend/app/backtest/cbo/test_cbo_engine.py
#
# ── CBO_V1 ENGINE TESTS ── synthetic candles, hand-computed expectations.
# Pure module, so this runs standalone with no DB and no app imports:
#     python3 backend/app/backtest/cbo/test_cbo_engine.py
#
# Every assertion below states WHAT is being pinned and WHY it matters, so a
# future edit that breaks one has to argue with the reason, not just the
# number.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbo_v1_engine import (  # noqa: E402
    CboBar, UP, DOWN, bucket_start, cbo_signals, tf_bars,
)

FAILED = []


def chk(label, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILED.append(label)


# 09:15 IST on an arbitrary day, as an epoch. Absolute value is irrelevant;
# only the offsets matter, so any anchor exercises the same arithmetic.
A = 1_700_000_000
M = 60


def bar(minute, o, h, l, c):
    """1m bar starting `minute` minutes after the anchor."""
    return CboBar(ts=A + minute * M, open=o, high=h, low=l, close=c)


def flat(minute, px, spread=1.0):
    return bar(minute, px, px + spread, px - spread, px)


print("\n── 1. bucket alignment ───────────────────────────────────────────")
chk("first 5m bucket holds minutes 0-4",
    all(bucket_start(A + m * M, A, 300) == A for m in range(5)))
chk("minute 5 opens the second bucket",
    bucket_start(A + 5 * M, A, 300) == A + 300)
chk("pre-anchor prints floor into a NEGATIVE bucket, never merged into 09:15",
    bucket_start(A - 30, A, 300) == A - 300)
chk("3m grid buckets independently",
    bucket_start(A + 4 * M, A, 180) == A + 180)


print("\n── 2. no signal without a completed reference ────────────────────")
# Minutes 0-4 are the first bucket; there is no previous bar to break.
first = [flat(m, 100 + m) for m in range(5)]
chk("first bucket of the day emits nothing (N4: no cross-day reference)",
    cbo_signals(first, anchor_ts=A) == [])


print("\n── 3. the canonical UP breakout ──────────────────────────────────")
# Bucket 1 (min 0-4): high 105, low 95   <- the reference
# Bucket 2 (min 5-9): min 5 high 104 (no), min 6 high 106 (BREAK), rest quiet
ref_bucket = [bar(0, 100, 105, 95, 100)] + [bar(m, 100, 101, 99, 100) for m in (1, 2, 3, 4)]
day = ref_bucket + [
    bar(5, 100, 104.0, 99.0, 103.0),   # touches 104 — below the 105 level
    bar(6, 103, 106.0, 102.0, 105.5),  # 106 >= 105 -> UP fires here
    bar(7, 105, 107.0, 104.0, 106.0),  # already fired; dedupe must hold
    bar(8, 106, 108.0, 105.0, 107.0),
    bar(9, 107, 109.0, 106.0, 108.0),
]
sig = cbo_signals(day, anchor_ts=A)
chk("exactly one UP signal in the bucket (one_signal_per_bucket)",
    len(sig) == 1 and sig[0].direction == UP)
s = sig[0]
chk("trigger is the minute-6 sub-bar, not minute 5 and not the 5m close",
    s.trigger_ts == A + 6 * M)
chk("fill_ts is trigger + 60s (N2: no fill before the deciding bar closed)",
    s.fill_ts == A + 7 * M)
chk("level breached is the reference HIGH (105)", s.level == 105.0)
chk("stop_level is the reference LOW (95) — the spec's spot stop",
    s.stop_level == 95.0)
chk("reference is bucket 1, not the forming bucket",
    s.ref.bucket_ts == A and s.ref.high == 105.0 and s.ref.low == 95.0)
chk("bucket_ts is the FORMING bar the trigger happened inside",
    s.bucket_ts == A + 300)
chk("excursion measures overshoot past the level (106 - 105)",
    s.excursion == 1.0)
chk("spot recorded is the sub-bar close, not its high",
    s.spot == 105.5)


print("\n── 4. touch is inclusive; buffer makes it strict ─────────────────")
# Minute 6 tops out EXACTLY at the 105 level: "touch or crosses" must fire.
touch = ref_bucket + [
    bar(5, 100, 104.0, 99.0, 103.0),
    bar(6, 103, 105.0, 102.0, 104.5),   # exact touch
]
chk("an exact touch of the level fires (N6)",
    len(cbo_signals(touch, anchor_ts=A)) == 1)
chk("breakout_buffer_pts=0.5 rejects that same exact touch",
    cbo_signals(touch, anchor_ts=A, breakout_buffer_pts=0.5) == [])
chk("buffer 0.5 still admits a 106 breach",
    len(cbo_signals(day, anchor_ts=A, breakout_buffer_pts=0.5)) == 1)


print("\n── 5. trigger_source close vs high ───────────────────────────────")
# Minute 6 WICKS through 106 but CLOSES at 104.5, back under the 105 level.
wick = ref_bucket + [
    bar(5, 100, 104.0, 99.0, 103.0),
    bar(6, 103, 106.0, 102.0, 104.5),   # high breaches, close does not
    bar(7, 104, 104.5, 103.0, 104.0),
]
chk("source=high fires on the wick (spec-literal, intrabar)",
    len(cbo_signals(wick, anchor_ts=A, trigger_source="high")) == 1)
chk("source=close does NOT fire on a wick that closes back inside",
    cbo_signals(wick, anchor_ts=A, trigger_source="close") == [])
chk("source=close fires once the sub-bar actually closes through",
    len(cbo_signals(day, anchor_ts=A, trigger_source="close")) == 1)


print("\n── 6. DOWN breakout mirrors exactly ──────────────────────────────")
down = ref_bucket + [
    bar(5, 100, 101.0, 96.0, 97.0),
    bar(6, 97, 98.0, 94.0, 95.0),       # 94 <= 95 -> DOWN
]
ds = cbo_signals(down, anchor_ts=A)
chk("one DOWN signal", len(ds) == 1 and ds[0].direction == DOWN)
chk("DOWN level is the reference LOW", ds[0].level == 95.0)
chk("DOWN stop is the reference HIGH", ds[0].stop_level == 105.0)
chk("DOWN excursion measures downside overshoot (95 - 94)",
    ds[0].excursion == 1.0)


print("\n── 7. outside sub-bar policy (N5) ────────────────────────────────")
# Minute 5 breaches BOTH 105 and 95 in one sub-bar. Order is unknowable.
outside = ref_bucket + [
    bar(5, 100, 106.0, 94.0, 100.0),
    bar(6, 100, 107.0, 93.0, 100.0),    # would re-arm if the policy let it
]
# ── D8 (locked 2026-08-29): the DEFAULT is pessimistic, not abstaining.
loss = cbo_signals(outside, anchor_ts=A)
chk("D8 default policy=loss TAKES the ambiguous bar rather than skipping it",
    len(loss) == 1)
chk("the taken signal is flagged ambiguous so the runner can force a stop",
    loss[0].ambiguous is True)
chk("minute-5 opens at 100, midway between 94 and 106 -> tie resolves UP "
    "deterministically (repeatable runs)",
    loss[0].direction == UP)
# open 104 sits nearer the 105 high than the 95 low, so a monotonic move
# from the open reaches the UP level first.
near_up = ref_bucket + [bar(5, 104, 106.0, 94.0, 100.0)]
chk("open nearer the HIGH picks the UP side",
    cbo_signals(near_up, anchor_ts=A)[0].direction == UP)
near_dn = ref_bucket + [bar(5, 96, 106.0, 94.0, 100.0)]
chk("open nearer the LOW picks the DOWN side",
    cbo_signals(near_dn, anchor_ts=A)[0].direction == DOWN)
chk("an UNambiguous breakout is NOT flagged (no false positives)",
    cbo_signals(day, anchor_ts=A)[0].ambiguous is False)

chk("policy=skip still available and emits nothing for an ambiguous bar",
    cbo_signals(outside, anchor_ts=A, both_side_policy="skip") == [])
chk("policy=skip also blocks the NEXT sub-bar in the same bucket "
    "(the ambiguity is not resolved by waiting)",
    cbo_signals(outside, anchor_ts=A, both_side_policy="skip") == [])
up_only = cbo_signals(outside, anchor_ts=A, both_side_policy="up")
chk("policy=up takes the long side only",
    len(up_only) == 1 and up_only[0].direction == UP)
dn_only = cbo_signals(outside, anchor_ts=A, both_side_policy="down")
chk("policy=down takes the short side only",
    len(dn_only) == 1 and dn_only[0].direction == DOWN)
both = cbo_signals(outside, anchor_ts=A, both_side_policy="both")
chk("policy=both emits two signals at the same trigger_ts",
    len(both) == 2 and both[0].trigger_ts == both[1].trigger_ts)


print("\n── 8. reference rolls forward every bucket ───────────────────────")
# b1 high 105 / low 95. b2 high 110 / low 104 (breaks up at min 5).
# b3 must reference b2's 110/104, NOT b1's 105/95.
roll = ref_bucket + [
    bar(5, 100, 106.0, 104.0, 105.0),   # UP vs b1
    bar(6, 105, 110.0, 105.0, 109.0),
    bar(7, 109, 110.0, 108.0, 109.0),
    bar(8, 109, 110.0, 108.0, 109.0),
    bar(9, 109, 110.0, 108.0, 109.0),
    bar(10, 109, 109.5, 108.0, 109.0),  # below b2 high 110 -> quiet
    bar(11, 109, 111.0, 108.0, 110.5),  # 111 >= 110 -> UP vs b2
]
rs = cbo_signals(roll, anchor_ts=A)
chk("two signals, one per bucket", len(rs) == 2)
chk("second signal references bucket 2's high of 110, not bucket 1's 105",
    rs[1].level == 110.0 and rs[1].ref.bucket_ts == A + 300)
chk("second signal's stop is bucket 2's low of 104",
    rs[1].stop_level == 104.0)
chk("signals are ascending in trigger_ts",
    rs[0].trigger_ts < rs[1].trigger_ts)


print("\n── 9. gaps and partial buckets ───────────────────────────────────")
# Bucket 2 has ONE printed minute (a data hole). Its 1-bar range is
# artificially narrow, which would make bucket 3's levels trivially easy.
gappy = ref_bucket + [
    bar(5, 100, 100.5, 99.5, 100.0),    # lone print in bucket 2
    bar(10, 100, 102.0, 98.0, 101.0),   # bucket 3 vs a 1-bar reference
]
#
# FOUND BY THIS TEST (2026-08-29), kept as the headline assertion: a 1-bar
# reference is only 1.0 wide, so the very next minute (98..102) breaches the
# high AND the low at once. Under the default skip policy that is ambiguous
# and emits NOTHING. So a data gap does not silently produce a spurious
# trade — it produces a SILENT HOLE instead. On a live day the same shape
# appears whenever a 5m bar is dead flat (lunch chop), so the narrow-
# reference case is common, not exotic. min_ref_range_pts exists to make
# that rejection explicit and countable rather than an accident of policy.
chk("a 1-bar reference is so narrow the next minute breaches BOTH sides, "
    "so under D8 it becomes a FLAGGED FORCED LOSS, not a skipped bar",
    len(cbo_signals(gappy, anchor_ts=A)) == 1
    and cbo_signals(gappy, anchor_ts=A)[0].ambiguous is True)
chk("under policy=skip the same gap would instead go SILENT",
    cbo_signals(gappy, anchor_ts=A, both_side_policy="skip") == [])
chk("forcing a side proves the reference itself was live, not discarded",
    len(cbo_signals(gappy, anchor_ts=A, both_side_policy="up")) == 1)
chk("that forced signal uses the 1-bar bucket's high of 100.5 — the level "
    "a gap made trivially reachable",
    cbo_signals(gappy, anchor_ts=A, both_side_policy="up")[0].level == 100.5)
chk("require_full_ref=True refuses the incomplete reference",
    cbo_signals(gappy, anchor_ts=A, require_full_ref=True,
                both_side_policy="both") == [])
chk("min_ref_range_pts=2.0 rejects the 1.0-wide reference outright, so the "
    "forced loss never happens — this is the lever that keeps D8 from "
    "charging real money for a data gap",
    cbo_signals(gappy, anchor_ts=A, min_ref_range_pts=2.0) == [])


print("\n── 10. ordering and idempotence ──────────────────────────────────")
shuffled = list(reversed(day))
chk("input order does not change the output (bars are sorted internally)",
    cbo_signals(shuffled, anchor_ts=A) == cbo_signals(day, anchor_ts=A))
chk("re-running is byte-identical (no hidden state between calls)",
    cbo_signals(day, anchor_ts=A) == cbo_signals(day, anchor_ts=A))
chk("empty input is empty output", cbo_signals([], anchor_ts=A) == [])


print("\n── 11. tf_bars aggregation ───────────────────────────────────────")
tb = tf_bars(day, anchor_ts=A)
chk("two 5m bars from ten 1m bars", len(tb) == 2)
chk("bucket 1 aggregates to O=100 H=105 L=95 C=100",
    (tb[0].open, tb[0].high, tb[0].low, tb[0].close) == (100, 105, 95, 100))
chk("bucket 2 closes at the last sub-bar's close", tb[1].close == 108.0)
chk("sub_bars counts the prints that built each bar",
    tb[0].sub_bars == 5 and tb[1].sub_bars == 5)


print("\n── 12. input validation ──────────────────────────────────────────")
for kwargs, label in (
    ({"trigger_source": "wick"}, "bad trigger_source raises"),
    ({"both_side_policy": "maybe"}, "bad both_side_policy raises"),
    ({"both_side_policy": "skip "}, "whitespace in policy raises (no silent coerce)"),
    ({"tf_minutes": 0}, "tf_minutes=0 raises"),
):
    try:
        cbo_signals(day, anchor_ts=A, **kwargs)
        chk(label, False)
    except ValueError:
        chk(label, True)


print("\n" + "=" * 66)
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CBO ENGINE TESTS PASSED")
