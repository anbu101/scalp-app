# backend/app/engine/brk/test_brk_live_core.py
#
# ── BRK_V1 LIVE CORE TESTS ── pure, no app imports. Fence BRK_V1_LIVE_20260902.
# Section 2 is the PARITY-BY-CONSTRUCTION block (VET doctrine): the live core
# is driven bar by bar and every decision is asserted equal to what the
# sealed backtest helpers say for the same prefix.
#
# Run from repo root:
#     python3 backend/app/engine/brk/test_brk_live_core.py .

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO / "backend" / "app" / "backtest" / "brk"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from brk_live_core import (  # noqa: E402
    BrkCore, PrefixGuard, WAIT, ENTER, NO_TRADE, FROZEN,
    R_SL, R_TP, R_EOD, hhmm_to_min, minute_of_day, align_minute, IST_OFFSET)

FAILED = []


def chk(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{('  ' + extra) if extra else ''}")
    if not cond:
        FAILED.append(label)


SEAL = {"select_time": "09:25", "select_below": 180, "select_min": 0,
        "break_above": 180, "sustain_candles": 1,
        "entry_first": "09:30", "entry_last": "09:30", "both_policy": "first",
        "sl_pts": 16, "tp_pts": 46, "eod_square_off": "15:15"}
SEAL_B = dict(SEAL, s2_enabled=True, s2_select_time="10:25",
              s2_entry_first="10:30", s2_entry_last="10:30",
              s2_only_if_flat=True, s2_only_if_loss=True)

print("── 1. clocks & alignment ─────────────────────────────────────────")
chk("hhmm 09:30 -> 570", hhmm_to_min("09:30", 0) == 570)
chk("hhmm malformed -> fallback", hhmm_to_min("junk", 915) == 915)
# 2026-03-12 09:30:01 IST — unaligned wall clock (the VET scar)
import datetime  # noqa: E402
d = datetime.datetime(2026, 3, 12, 9, 30, 1)
epoch = int((d - datetime.datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET
chk("minute_of_day tolerates unaligned ts (09:30:01 -> 570)",
    minute_of_day(epoch) == 570)
chk("align_minute strips seconds", align_minute(epoch) == epoch - 1)

print("── 2. PARITY BY CONSTRUCTION vs sealed backtest helpers ──────────")
from backtest_brk_runner import pick_candidate, confirmed_at, choose_side  # noqa: E402

core = BrkCore(SEAL)
ltps = {"CE": {"C1": 150.0, "C2": 175.0, "C3": 185.0},
        "PE": {"P1": 162.5, "P2": 190.0}}
ce, pe = core.select(core.s1, ltps)
chk("selection == sealed pick_candidate (both sides)",
    ce == pick_candidate(ltps["CE"], below=180, floor=0)
    and pe == pick_candidate(ltps["PE"], below=180, floor=0)
    and ce == "C2" and pe == "P1")
chk("selection prints recorded", core.s1.sel_prints == {"CE": 175.0, "PE": 162.5})

# drive closes minute by minute; at each decision minute assert the core's
# answer equals a fresh evaluation of the sealed helpers on the same prefix.
tape = {"CE": {565: 176, 566: 177, 567: 178, 568: 179, 569: 181},
        "PE": {565: 162, 566: 161, 567: 160, 568: 159, 569: 158}}
for m in range(565, 570):
    core.on_close(core.s1, "CE", m, tape["CE"][m])
    core.on_close(core.s1, "PE", m, tape["PE"][m])
dec, pay = core.decide(core.s1, 570)
chk("decision at 09:30 == backtest confirm (CE close 181 >= 180 -> ENTER CE)",
    dec == ENTER and pay["side"] == "CE" and pay["symbol"] == "C2"
    and pay["tag"] == "BRK",
    f"dec={dec} pay={pay}")
chk("parity: confirmed_at agrees on the same prefix",
    confirmed_at(tape["CE"], 570, level=180, sustain=1) is True
    and confirmed_at(tape["PE"], 570, level=180, sustain=1) is False)
chk("session terminal after entry", core.s1.done and core.s1.entered)
chk("further minutes are WAIT (one trade a session)",
    core.decide(core.s1, 571)[0] == WAIT)

core = BrkCore(SEAL)
core.select(core.s1, ltps)
core.on_close(core.s1, "CE", 569, 179.5)   # wick day: close below
dec, _ = core.decide(core.s1, 570)
chk("no close >= 180 at the only decision minute -> NO_TRADE (window closed)",
    dec == NO_TRADE and core.s1.done)

# both confirmed, policy first, tie -> dearer (delegates to sealed choose_side)
core = BrkCore(dict(SEAL, entry_first="09:30", entry_last="09:31"))
core.select(core.s1, ltps)
for m, (c, p) in {569: (181, 181), 570: (182, 186)}.items():
    core.on_close(core.s1, "CE", m, c)
    core.on_close(core.s1, "PE", m, p)
dec, pay = core.decide(core.s1, 570)
want = choose_side(ce_ok=True, pe_ok=True, policy="first", ce_first=569,
                   pe_first=569, ce_px=181, pe_px=181)
chk("both-break resolution delegates to sealed choose_side (tie -> dearer PE... "
    "same-price tie -> PE by >=)", dec == ENTER and pay["side"] == want)

print("── 3. exits ──────────────────────────────────────────────────────")
core = BrkCore(SEAL)
sl, tp = core.exit_levels(181.0)
chk("exit levels: entry-16 / entry+46", sl == 165.0 and tp == 227.0)
chk("tp_pts 0 -> no target", BrkCore(dict(SEAL, tp_pts=0)).exit_levels(181.0)[1] is None)
chk("tick SL", core.check_exit(ltp=164.9, sl_px=sl, tp_px=tp, m=600) == R_SL)
chk("tick TP", core.check_exit(ltp=227.0, sl_px=sl, tp_px=tp, m=600) == R_TP)
chk("same-tick collision impossible by construction; SL tested first",
    core.check_exit(ltp=100.0, sl_px=165, tp_px=90.0, m=600) == R_SL)
chk("quiet tick holds", core.check_exit(ltp=200.0, sl_px=sl, tp_px=tp, m=600) is None)
chk("EOD strictly by clock even on a winning tick",
    core.check_exit(ltp=300.0, sl_px=sl, tp_px=tp, m=915) == R_EOD)

print("── 4. PrefixGuard (fail closed) ──────────────────────────────────")
g = PrefixGuard()
chk("forward bars accepted", g.observe(100) and g.observe(160))
chk("restated bar freezes", not g.observe(160) and g.frozen and "restated" in g.reason)
chk("frozen stays frozen", not g.observe(220))
core = BrkCore(SEAL)
core.guard.frozen = True
chk("frozen core answers FROZEN, never trades",
    core.decide(core.s1, 570)[0] == FROZEN)

print("── 5. session-2 gates (Config B) ─────────────────────────────────")
b = BrkCore(SEAL_B)
chk("s2 window parsed (10:25/10:30)", b.s2 is not None
    and b.s2.spec.sel_min == 625 and b.s2.spec.first_min == 630)
b.select(b.s2, {"CE": {"X": 175.0}, "PE": {"Y": 170.0}})
b.on_close(b.s2, "CE", 629, 181)
chk("only_if_flat: s1 open at 10:30 -> WAIT (minute skipped, window alive)",
    b.decide(b.s2, 630, s1_open=True, s1_result=None)[0] == WAIT)
chk("only_if_loss: s1 closed PROFITABLE -> NO_TRADE (session dead)",
    b.decide(b.s2, 630, s1_open=False, s1_result=+500.0)[0] == NO_TRADE and b.s2.done)
b2 = BrkCore(SEAL_B)
b2.select(b2.s2, {"CE": {"X": 175.0}, "PE": {"Y": 170.0}})
b2.on_close(b2.s2, "CE", 629, 181)
dec, pay = b2.decide(b2.s2, 630, s1_open=False, s1_result=-500.0)
chk("losing morning + flat -> s2 ENTER, tag BRK·S2",
    dec == ENTER and pay["tag"] == "BRK·S2" and pay["symbol"] == "X")
b3 = BrkCore(SEAL_B)
b3.select(b3.s2, {"CE": {"X": 175.0}, "PE": {"Y": 170.0}})
b3.on_close(b3.s2, "CE", 629, 181)
dec, pay = b3.decide(b3.s2, 630, s1_open=False, s1_result=None)
chk("no-trade morning -> s2 allowed (idle capital)", dec == ENTER)
chk("Config A: s2 disabled -> no s2 session object", BrkCore(SEAL).s2 is None)

print("── 6. window-elapse with only_if_flat blocking every minute ──────")
b4 = BrkCore(SEAL_B)
b4.select(b4.s2, {"CE": {"X": 175.0}, "PE": {"Y": 170.0}})
b4.on_close(b4.s2, "CE", 629, 181)
chk("blocked at 10:30 (only minute) -> WAIT; past window -> NO_TRADE",
    b4.decide(b4.s2, 630, s1_open=True)[0] == WAIT
    and b4.decide(b4.s2, 631, s1_open=False)[0] == NO_TRADE and b4.s2.done)

print(f"\n{'ALL PASS' if not FAILED else f'{len(FAILED)} FAILED: ' + ', '.join(FAILED)}")
sys.exit(1 if FAILED else 0)