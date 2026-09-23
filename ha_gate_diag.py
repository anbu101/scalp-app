#!/usr/bin/env python3
# ha_gate_diag.py — why did HA_V1 not enter today?  Read-only.
#
# Reads ~/.scalp-app/logs/<date>.log (the app's audit log) and prints the HA_V1
# entry FUNNEL, stage by stage, in the exact order ha_tick_engine._on_candle_close
# evaluates it. The first stage that drops to zero is the blocker. No code path
# is touched; this only parses audit lines the engine already writes.
#
# USAGE:
#   python3 ha_gate_diag.py                 # today
#   python3 ha_gate_diag.py 2026-09-19      # a past trading day
#   python3 ha_gate_diag.py 2026-09-15 2026-09-19   # a range (one block per day)
#   python3 ha_gate_diag.py --log /path/to/file.log
#
# Funnel (each stage only counts lines from the stage above it):
#   1 engine     ENGINE_READY / SUB_RETRY snapshots — universe size, selection
#   2 selection  SELECTION Updated lines; minutes with CE=None PE=None
#   3 candles    [HA][CANDLE] on SELECTED symbols (the only ones evaluated)
#   4 gates      [HA][GATE] reasons: OFF, MTM day-block, EMA warming, outside
#                session, trade_on, side_mode, CE/PE in-trade / cap
#   5 evaluator  [HA][NO_ENTRY] rejections + [HA][REJECT] resets (gap/dup/order)
#   6 skips      [HA][SKIP]: no red candle, LTP unavailable, SL>=LTP,
#                MIN SL (with the distribution of the distances it rejected),
#                condition not enabled
#   7 signals    [HA][SIGNAL_FIRED] by condition
#   8 arbitration ARB_ARM / JOIN / DROP(reasons) / ELECT / CANCEL / ENTRY_FAILED
#   9 faults     OPEN_CHECK_ERR, DEGRADED_HOLD, ENTRY_DEAD, *_ERR lines

from __future__ import annotations
import sys, os, re, argparse, collections
from datetime import datetime, timedelta

LOG_DIR = os.path.join(os.path.expanduser("~"), ".scalp-app", "logs")
LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s+(.*)$")


def load(path):
    if not os.path.exists(path):
        return None
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            ts, msg = m.group(1), m.group(2)
            if "[HA" in msg:          # [HA], [HA][...], [HA-RUNTIME], [HA_SIGNAL]
                out.append((ts, msg))
    return out


def bucket(lines, tag):
    return [(t, m) for t, m in lines if f"[HA][{tag}]" in m]


def counter(items, keyfn):
    c = collections.Counter()
    for t, m in items:
        c[keyfn(m)] += 1
    return c


def print_counter(title, c, indent="      ", limit=12):
    if not c:
        print(f"{indent}{title}: none")
        return
    print(f"{indent}{title}:")
    for k, v in c.most_common(limit):
        print(f"{indent}  {v:6d}  {k}")


def gate_reason(m):
    for k in ("mode=OFF", "MTM day-block", "EMA not ready", "outside session",
              "trade_on=FALSE", "side_mode", "ALREADY_IN_TRADE", "MAX_TRADES_REACHED"):
        if k in m:
            return k
    return m.split("—")[-1].strip()[:60]


def skip_reason(m):
    if "MIN SL" in m:
        return "MIN SL floor"
    for k in ("no red candle", "LTP unavailable", ">= LTP", "not in enabled"):
        if k in m:
            return k
    return m.split("—")[-1].strip()[:60]


def no_entry_reason(m):
    r = m.split("reject=", 1)[-1]
    kinds = []
    for k in ("N_RED", "NO_EMA_TOUCH", "NO_PATTERN_MATCH", "WARMING_UP", "EMA_NOT_READY",
              "DUPLICATE_CANDLE", "OUT_OF_ORDER_CANDLE", "CANDLE_GAP_RESET"):
        if k in r:
            kinds.append(k)
    return " + ".join(kinds) if kinds else r[:40]


def arb_drop_reason(m):
    for k in ("already open (global gate)", "election pending", "gate became occupied",
              "lost election", "open-check error"):
        if k in m:
            return k
    return m.split("—")[-1].strip()[:60]


def report(day, lines):
    print("=" * 78)
    print(f"HA_V1 entry funnel — {day}   ({len(lines)} HA audit lines)")
    print("=" * 78)
    if not lines:
        print("  no [HA] lines at all — HA_V1 never launched, or the log path is wrong")
        return

    # 1 engine
    ready = [m for t, m in lines if "[HA][ENGINE_READY]" in m or "[HA-RUNTIME]" in m]
    retry = bucket(lines, "SUB_RETRY")
    print("  1 ENGINE")
    for m in ready[:6]:
        print("      " + m[:120])
    if retry:
        print("      last SUB_RETRY: " + retry[-1][1][:140])
    univ = [m for t, m in lines if "[HA][UNIVERSE]" in m]
    if univ:
        print("      last UNIVERSE : " + univ[-1][:120])

    # 2 selection — HA_OWN_SELECT_20260923: HA's own loop logs [HA_SELECT];
    # the engine logs [HA][SELECTION] Updated → CE=[...] PE=[...] (lists) —
    # older logs have CE=<sym> PE=<sym> (single slot). Both are handled.
    own = [(t, m) for t, m in lines if "[HA_SELECT]" in m]
    if own:
        own_upd = [(t, m) for t, m in own if "Updated selection" in m]
        own_err = [(t, m) for t, m in own if "ERROR" in m or "not ready" in m or "empty" in m]
        print("  2a OWN SELECTION LOOP ([HA_SELECT])")
        print(f"      loop lines: {len(own)}  saves-with-change: {len(own_upd)}  problems: {len(own_err)}")
        for t, m in (own_upd[:2] + own_upd[-1:] if len(own_upd) > 3 else own_upd):
            print(f"      {t}  {m[:120]}")
        if own_err:
            print_counter("loop problems", counter(own_err, lambda m: re.sub(r"\d+", "#", m)[:90]))
    sel = [(t, m) for t, m in lines if "[HA][SELECTION]" in m]
    upd = [(t, m) for t, m in sel if "Updated" in m]
    none_both = [(t, m) for t, m in upd if "CE=None PE=None" in m or "CE=[] PE=[]" in m]
    print("  2 SELECTION (engine view)")
    print(f"      selection updates: {len(upd)}   (both empty: {len(none_both)})")
    shown = upd if len(upd) <= 6 else upd[:3] + [("...", "...")] + upd[-3:]
    for t, m in shown:
        print(f"      {t}  {m[:110]}")
    errs = [(t, m) for t, m in sel if "ERROR" in m or "not resolvable" in m]
    if errs:
        print_counter("selection errors", counter(errs, lambda m: m[:90]))

    # 3 candles on selected symbols
    cand = bucket(lines, "CANDLE")
    print("  3 CANDLES on selected symbols")
    c_side = counter(cand, lambda m: re.search(r"side=(CE|PE)", m).group(1) if re.search(r"side=(CE|PE)", m) else "?")
    warm = sum(1 for t, m in cand if "WARMING_UP" in m)
    print(f"      {len(cand)} candles  CE={c_side.get('CE',0)} PE={c_side.get('PE',0)}  EMA warming={warm}")
    if cand:
        print(f"      first {cand[0][0]}  last {cand[-1][0]}")

    # 4 gates
    gates = bucket(lines, "GATE")
    print("  4 GATES (returned before the evaluator ran)")
    print_counter(f"{len(gates)} gate returns", counter(gates, gate_reason))

    # 5 evaluator
    ne = bucket(lines, "NO_ENTRY")
    rj = bucket(lines, "REJECT")
    print("  5 EVALUATOR")
    print_counter(f"{len(ne)} NO_ENTRY", counter(ne, no_entry_reason))
    print_counter(f"{len(rj)} REJECT (state resets)", counter(rj, lambda m: m.split("]")[-1].strip().split(" ")[0]))

    # 6 skips
    sk = bucket(lines, "SKIP")
    print("  6 SKIPS (signal fired in the evaluator, dropped before arbitration)")
    print_counter(f"{len(sk)} skips", counter(sk, skip_reason))
    dists = []
    for t, m in sk:
        mm = re.search(r"SL distance ([\d.]+) < MIN SL ([\d.]+)", m)
        if mm:
            dists.append((float(mm.group(1)), float(mm.group(2))))
    if dists:
        floor = dists[0][1]
        ds = sorted(d for d, _ in dists)
        med = ds[len(ds) // 2]
        print(f"      MIN SL floor={floor:g}: {len(ds)} signals rejected; distances "
              f"min={ds[0]:.2f} median={med:.2f} max={ds[-1]:.2f}")
        hist = collections.Counter(min(int(d // 3) * 3, 30) for d in ds)
        print("      distance histogram (pts): " + "  ".join(
            f"{k}-{k+3}:{v}" if k < 30 else f"30+:{v}" for k, v in sorted(hist.items())))
        print(f"      → with the floor at 0, {len(ds)} more signals would have reached arbitration today")

    # 7 signals
    sf = bucket(lines, "SIGNAL_FIRED")
    print("  7 SIGNALS reaching arbitration")
    print_counter(f"{len(sf)} SIGNAL_FIRED", counter(sf, lambda m: (re.search(r"cond=(COND\d)", m) or [None, "?"])[1]))
    for t, m in sf[:8]:
        print(f"      {t}  {m[:110]}")

    # 8 arbitration
    print("  8 ARBITRATION")
    for tag in ("ARB_ARM", "ARB_JOIN", "ARB_ELECT", "ARB_CANCEL", "ARB_CANCEL_EOD", "ENTRY_FAILED", "ACTIVE_TRADE"):
        b = bucket(lines, tag)
        if b:
            print(f"      {len(b):6d}  {tag}" + (f"   e.g. {b[0][1][:90]}" if tag in ("ARB_ELECT", "ENTRY_FAILED") else ""))
    drops = bucket(lines, "ARB_DROP")
    print_counter(f"{len(drops)} ARB_DROP", counter(drops, arb_drop_reason))

    # 9 faults
    print("  9 FAULTS")
    faults = [(t, m) for t, m in lines if any(k in m for k in
              ("OPEN_CHECK_ERR", "DEGRADED_HOLD", "ENTRY_DEAD", "_ERR]", "_ERROR]", "ERROR", "READ_DEGRADED"))]
    print_counter(f"{len(faults)} fault lines", counter(faults, lambda m: re.sub(r"\d+", "#", m)[:90]))

    # verdict — first stage in the funnel that is empty
    print("  VERDICT")
    if not cand:
        if not upd or none_both == upd:
            print("      No selected symbols all day → nothing was ever evaluated. Check stage 2a:")
            print("      HA's own selection loop must be saving (broker/trade session ready, band not empty).")
        else:
            print("      Selection existed but no [HA][CANDLE] on it → ticks for the selected symbols never")
            print("      reached HA (token not in universe / WS). See stage 1 universe size.")
    elif not ne and not sf and not sk:
        top = counter(gates, gate_reason).most_common(1)
        print(f"      Every candle returned at a GATE before the evaluator ran — top reason: {top[0][0] if top else '?'}")
    elif not sf and not sk:
        print("      Evaluator ran but never fired (no SKIP, no SIGNAL_FIRED). Either the pattern")
        print("      genuinely did not occur, or it kept resetting (see REJECT counts / selection churn).")
    elif not sf:
        top = counter(sk, skip_reason).most_common(1)[0][0]
        print(f"      Signals fired but ALL were skipped — top skip reason: {top}")
        if top == "MIN SL floor":
            print("      → Min SL is the choke today. Set it to 0 (or lower) to let entries through.")
        elif top == "not in enabled":
            print("      → The disabled condition(s) are the only ones firing; widen Entry Conditions.")
    elif not bucket(lines, "ARB_ELECT"):
        top = counter(drops, arb_drop_reason).most_common(1)
        print(f"      Signals reached arbitration but nothing was elected — top drop: {top[0][0] if top else 'see ARB_CANCEL'}")
        if top and top[0][0] in ("gate became occupied", "already open (global gate)", "open-check error"):
            print("      → global gate stuck occupied (stale open row / pending flag / repo error). Restart the")
            print("        backend and check the Trades page for a phantom open HA_V1 row.")
    elif bucket(lines, "ENTRY_FAILED"):
        print("      Elected but enter() failed — see the ENTRY_FAILED / DEAD lines above.")
    else:
        print("      Entries happened today; nothing blocked.")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="*", help="YYYY-MM-DD [YYYY-MM-DD]; default today")
    ap.add_argument("--log", help="explicit log file instead of ~/.scalp-app/logs/<date>.log")
    a = ap.parse_args()
    if a.log:
        lines = load(a.log)
        if lines is None:
            sys.exit(f"not found: {a.log}")
        report(os.path.basename(a.log), lines)
        return
    if not a.days:
        days = [datetime.now().strftime("%Y-%m-%d")]
    elif len(a.days) == 1:
        days = a.days
    else:
        d0, d1 = (datetime.strptime(x, "%Y-%m-%d") for x in a.days[:2])
        days = []
        while d0 <= d1:
            if d0.weekday() < 5:
                days.append(d0.strftime("%Y-%m-%d"))
            d0 += timedelta(days=1)
    for d in days:
        p = os.path.join(LOG_DIR, f"{d}.log")
        lines = load(p)
        if lines is None:
            print(f"{d}: no log file at {p}")
            continue
        report(d, lines)


if __name__ == "__main__":
    main()
