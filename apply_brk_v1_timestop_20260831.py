#!/usr/bin/env python3
# apply_brk_v1_timestop_20260831.py
#
# ── BRK_V1_TIMESTOP_20260831 ── D3.3: time stop for dead-on-arrival trades.
#
#   time_stop_min      N minutes after entry (0 = off)
#   time_stop_need_pts X points: at the bar that starts entry+N minutes, if
#                      that bar's CLOSE − entry < X, exit at that bar's close,
#                      reason TIME. Evaluated ONCE (that bar only), AFTER the
#                      SL/TP/trail check on the same bar (a stop or target
#                      touched in that minute still wins — pessimistic).
#
# Touches (both trees): brk runner + sim suite. Frontend: Backtest.jsx BRK
# panel (two fields, buildConfig, deps, LS, describeConfig), paramFormat.js,
# SweepBuilder.jsx (two axes), RunComparison.jsx (TIME exit key).
# Assert-anchored, replace-once, staged py_compile, esbuild gate, .bak-FENCE.
#     python3 apply_brk_v1_timestop_20260831.py --check
#     python3 apply_brk_v1_timestop_20260831.py

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "BRK_V1_TIMESTOP_20260831"
TREES = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
RUNNER = "backtest/brk/backtest_brk_runner.py"
TEST = "backtest/brk/test_brk_runner_sim.py"
BT = Path("frontend/src/pages/Backtest.jsx")
PF = Path("frontend/src/pages/backtest/paramFormat.js")
SB = Path("frontend/src/pages/backtest/SweepBuilder.jsx")
RC = Path("frontend/src/pages/backtest/RunComparison.jsx")

# ── runner ──
R_DEF_OLD = '''    "trail_gap": 0.0,                  # ── BRK_V1_RATCHET_20260831 ── ratchet: stop = max high − gap (0 = off)
'''
R_DEF_NEW = R_DEF_OLD + f'''    "time_stop_min": 0,                # ── {FENCE} ── N minutes after entry (0 = off)
    "time_stop_need_pts": 0.0,         # ── {FENCE} ── exit unless close ≥ entry + X at that minute
'''
R_NORM_OLD = '''    for k in ("select_below", "select_min", "break_above", "sl_pts",
              "tp_pts", "trail_trigger_pts", "trail_lock_pts", "trail_gap"):'''
R_NORM_NEW = f'''    try:   # ── {FENCE} ──
        cfg["time_stop_min"] = max(0, int(cfg.get("time_stop_min") or 0))
    except (TypeError, ValueError):
        cfg["time_stop_min"] = 0
    for k in ("select_below", "select_min", "break_above", "sl_pts",
              "tp_pts", "trail_trigger_pts", "trail_lock_pts", "trail_gap",
              "time_stop_need_pts"):'''
R_DIAG_OLD = '''        "sl_exits": 0, "tp_exits": 0, "trail_exits": 0, "eod_exits": 0,'''
R_DIAG_NEW = '''        "sl_exits": 0, "tp_exits": 0, "trail_exits": 0, "eod_exits": 0,
        "time_exits": 0, "time_pnl_gross": 0.0,   # ── ''' + FENCE + ''' ──'''
R_KEY_OLD = '''        key = {"SL": "sl", "TP": "tp", "TRAIL": "trail", "EOD": "eod"}[reason]'''
R_KEY_NEW = '''        key = {"SL": "sl", "TP": "tp", "TRAIL": "trail", "EOD": "eod",
               "TIME": "time"}[reason]   # ── ''' + FENCE + ''' ──'''
R_LAD_OLD = '''            if ex is not None:
                close_trade(pos, ds + m * 60, ex[1], ex[0])
                closed = True
                break
            # D7: trail arms from the NEXT bar (pessimistic ordering).'''
R_LAD_NEW = f'''            if ex is not None:
                close_trade(pos, ds + m * 60, ex[1], ex[0])
                closed = True
                break
            # ── {FENCE} ── time stop: at entry+N, needs close ≥ entry+X.
            # Checked AFTER the stop/target on the same bar, ONCE.
            if cfg["time_stop_min"] > 0 and m == pos["entry_min"] + cfg["time_stop_min"] \\
                    and float(b.close) - pos["entry_px"] < cfg["time_stop_need_pts"]:
                close_trade(pos, ds + m * 60, float(b.close), "TIME")
                closed = True
                break
            # D7: trail arms from the NEXT bar (pessimistic ordering).'''
R_SUM_OLD = '''        for k in ("sl", "tp", "trail", "eod"):'''
R_SUM_NEW = f'''        for k in ("sl", "tp", "trail", "eod", "time"):   # ── {FENCE} ──'''
R_LOG_OLD = '''        f"EOD {diag['eod_exits']}, days noBreak {diag['days_no_break']} / "'''
R_LOG_NEW = f'''        f"EOD {{diag['eod_exits']}} / TIME {{diag['time_exits']}}, days noBreak {{diag['days_no_break']}} / "'''

T_ANCHOR = '''print(f"\\n{'ALL PASS' if not FAILED else f'{len(FAILED)} FAILED: ' + ', '.join(FAILED)}")'''
T_NEW = f'''# ── {FENCE} ── time stop
print("\\n── D. time stop ──────────────────────────────────────────────────")
# 23. Entry 09:30 @181; time stop 5 min needing +8: at 09:35 close 184 (+3) -> TIME @184.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          **{{m: (183, 185, 182, 184) for m in range(M0931, 600)}}}})
        + series(*PE, 170))
r = run(build(rows), {{"time_stop_min": 5, "time_stop_need_pts": 8}})
t = r["trades"][0]
chk("23. not +8 by entry+5 -> TIME exit at that bar's close (184) at 09:35",
    t.exit_reason == "TIME" and t.exit_price == 184 and t.exit_ts == ts(M0935),
    f"reason={{t.exit_reason}} px={{t.exit_price}}")
chk("23. diag time_exits 1", r["summary"]["diag_brk"]["time_exits"] == 1)
# 24. Same tape, needs only +3 -> passes, holds to EOD.
r = run(build(rows), {{"time_stop_min": 5, "time_stop_need_pts": 3}})
chk("24. +3 satisfied at the check minute -> no TIME exit (EOD)",
    r["trades"][0].exit_reason == "EOD")
# 25. Stop hit in the same bar as the time check -> SL wins.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          M0935: (181, 182, 150, 178)}}) + series(*PE, 170))
r = run(build(rows), {{"time_stop_min": 5, "time_stop_need_pts": 8}})
chk("25. SL touched in the time-check bar -> SL (161), not TIME",
    r["trades"][0].exit_reason == "SL" and r["trades"][0].exit_price == 161)
# 26. Check minute has no print -> no time exit (fail-open on exit path), EOD.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181)}},
                skip=(M0935,)) + series(*PE, 170))
r = run(build(rows), {{"time_stop_min": 5, "time_stop_need_pts": 8}})
chk("26. missing bar at the check minute -> checked once, skipped, EOD",
    r["trades"][0].exit_reason == "EOD" and r["summary"]["diag_brk"]["time_exits"] == 0)
# 27. time_stop_min 0 -> off (regression: seal tape still TP).
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          M0931: (182, 225, 181, 220)}}) + series(*PE, 170))
r = run(build(rows), {{"time_stop_min": 0, "time_stop_need_pts": 8}})
chk("27. time stop off -> unchanged TP", r["trades"][0].exit_reason == "TP")

''' + T_ANCHOR

# ── Backtest.jsx ──
B_ST_OLD = '  const [brkTrailGap, setBrkTrailGap] = useState(brkSaved.trailGap ?? 0);           // ── BRK_V1_RATCHET_20260831 ──\n'
B_ST_NEW = B_ST_OLD + f'''  const [brkTsMin, setBrkTsMin] = useState(brkSaved.tsMin ?? 0);       // ── {FENCE} ──
  const [brkTsPts, setBrkTsPts] = useState(brkSaved.tsPts ?? 0);       // ── {FENCE} ──
'''
B_LS_OLD = 'trailMode: brkTrailMode, trailGap: brkTrailGap, eod: brkEod,'
B_LS_NEW = 'trailMode: brkTrailMode, trailGap: brkTrailGap, tsMin: brkTsMin, tsPts: brkTsPts, eod: brkEod,'
B_LSD_OLD = 'brkTrailMode, brkTrailGap, brkEod, brkLots, brkLotSize, brkSkipExpiry]);'
B_LSD_NEW = 'brkTrailMode, brkTrailGap, brkTsMin, brkTsPts, brkEod, brkLots, brkLotSize, brkSkipExpiry]);'
B_BC_OLD = '        trail_gap: Number(brkTrailGap) || 0,           // ── BRK_V1_RATCHET_20260831 ──\n'
B_BC_NEW = B_BC_OLD + f'''        time_stop_min: Number(brkTsMin) || 0,          // ── {FENCE} ──
        time_stop_need_pts: Number(brkTsPts) || 0,     // ── {FENCE} ──
'''
B_DEPS_OLD = 'brkTrailMode, brkTrailGap, brkEod, brkLots, brkLotSize, brkSkipExpiry,\n'
B_DEPS_NEW = 'brkTrailMode, brkTrailGap, brkTsMin, brkTsPts, brkEod, brkLots, brkLotSize, brkSkipExpiry,\n'
B_DESC_OLD = '''    else if (Number(cfg.trail_trigger_pts) > 0) add("Trail", `@+₹${cfg.trail_trigger_pts} → entry${Number(cfg.trail_lock_pts) > 0 ? `+₹${cfg.trail_lock_pts}` : ""}`);
    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);'''
B_DESC_NEW = f'''    else if (Number(cfg.trail_trigger_pts) > 0) add("Trail", `@+₹${{cfg.trail_trigger_pts}} → entry${{Number(cfg.trail_lock_pts) > 0 ? `+₹${{cfg.trail_lock_pts}}` : ""}}`);
    if (Number(cfg.time_stop_min) > 0) add("Time stop", `+₹${{cfg.time_stop_need_pts}} by ${{cfg.time_stop_min}}m`);   // ── {FENCE} ──
    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);'''
B_PAN_OLD = '''                <Field label="EOD square-off"><input type="text" style={inputStyle} value={brkEod} onChange={(e) => setBrkEod(e.target.value)} title="Open position is closed at this minute's 1m close. Same clock as the live cron would use." /></Field>'''
B_PAN_NEW = f'''                {{/* ── {FENCE} ── time stop */}}
                <Field label="Time stop (min, 0=off)"><input type="number" style={{inputStyle}} value={{brkTsMin}} onChange={{(e) => setBrkTsMin(Number(e.target.value))}} title="N minutes after entry the trade must be at least +X (close vs entry) or it exits at that bar's close, reason TIME. Checked once, after the stop/target on the same bar." /></Field>
                <Field label="Time stop needs +₹"><input type="number" style={{inputStyle}} value={{brkTsPts}} onChange={{(e) => setBrkTsPts(Number(e.target.value))}} disabled={{!(Number(brkTsMin) > 0)}} title="The minimum unrealised gain (premium points, close − entry) required at the check minute. 0 = must merely be non-negative." /></Field>
''' + B_PAN_OLD

# ── paramFormat.js ──
P_OLD = '''  else if (Number(cfg.trail_trigger_pts) > 0) p.push(`trail${cfg.trail_trigger_pts}/${Number(cfg.trail_lock_pts) || 0}`);
  if (cfg.eod_square_off) p.push(`eod ${cfg.eod_square_off}`);'''
P_NEW = f'''  else if (Number(cfg.trail_trigger_pts) > 0) p.push(`trail${{cfg.trail_trigger_pts}}/${{Number(cfg.trail_lock_pts) || 0}}`);
  if (Number(cfg.time_stop_min) > 0) p.push(`ts${{cfg.time_stop_min}}m/${{Number(cfg.time_stop_need_pts) || 0}}`);   // ── {FENCE} ──
  if (cfg.eod_square_off) p.push(`eod ${{cfg.eod_square_off}}`);'''

# ── SweepBuilder.jsx ──
S_OLD = '''  { key: "brk_eod", label: "BRK EOD square-off", strategies: [BRK],'''
S_NEW = f'''  // ── {FENCE} ── time stop
  {{ key: "brk_ts_min", label: "BRK time stop (min, 0=off)", strategies: [BRK],
    hint: "0, 10, 15, 20, 30", parse: _num,
    apply: (c, v) => {{ c.time_stop_min = Math.max(0, Math.round(v)); }}, fmt: (v) => (v > 0 ? `ts${{Math.round(v)}}m` : "no ts") }},
  {{ key: "brk_ts_pts", label: "BRK time stop needs +₹", strategies: [BRK],
    hint: "0, 5, 8, 12", parse: _num,
    apply: (c, v) => {{ c.time_stop_need_pts = Math.abs(v); }}, fmt: (v) => `need+${{Math.abs(v)}}` }},
''' + S_OLD

# ── RunComparison.jsx ──
C_OLD = '''  // ── BRK_V1_UI_20260830 ── BRK_V1 raised-stop exit
  "TRAIL"];'''
C_NEW = f'''  // ── BRK_V1_UI_20260830 ── BRK_V1 raised-stop exit
  "TRAIL",
  // ── {FENCE} ── BRK_V1 time-stop exit
  "TIME"];'''


class Abort(Exception):
    pass


def rep(t, old, new, what):
    n = t.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n} times, expected 1 — file drifted")
    return t.replace(old, new)


def stage_py(path, text):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise Abort(f"{path}: staged compile FAILED — {e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def esbuild_ok(path, text):
    tmp = path.parent / f"_ts_stage{path.suffix}"
    tmp.write_text(text)
    try:
        r = subprocess.run(["npx", "--yes", "esbuild", str(tmp),
                            f"--loader:{path.suffix}={'jsx' if path.suffix == '.jsx' else 'js'}",
                            "--outfile=/dev/null"], capture_output=True, text=True)
        if r.returncode != 0:
            raise Abort(f"esbuild rejected patched {path}:\n{r.stderr[:2000]}")
    except FileNotFoundError:
        print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true")
    ap.add_argument("--allow-missing-tree", action="store_true")
    a = ap.parse_args()
    present = [t for t in TREES if t.exists()]
    missing = [t for t in TREES if not t.exists()]
    if missing and not a.allow_missing_tree:
        print(f"ABORTED: dual-tree not satisfiable, absent: {[str(m) for m in missing]}", file=sys.stderr)
        return 1
    staged, skipped = {}, []
    try:
        for tree in present:
            p = tree / RUNNER
            t = p.read_text()
            if "BRK_V1_RATCHET_20260831" not in t:
                raise Abort(f"{p}: apply_brk_v1_ratchet_20260831.py must be applied first")
            if FENCE in t:
                skipped.append(p)
            else:
                for old, new, what in ((R_DEF_OLD, R_DEF_NEW, "defaults"), (R_NORM_OLD, R_NORM_NEW, "normalise"),
                                       (R_DIAG_OLD, R_DIAG_NEW, "diag"), (R_KEY_OLD, R_KEY_NEW, "key map"),
                                       (R_LAD_OLD, R_LAD_NEW, "ladder"), (R_SUM_OLD, R_SUM_NEW, "summary"),
                                       (R_LOG_OLD, R_LOG_NEW, "audit line")):
                    t = rep(t, old, new, f"{p}:{what}")
                stage_py(p, t)
                staged[p] = t
            p = tree / TEST
            t = p.read_text()
            if FENCE in t:
                skipped.append(p)
            else:
                t = rep(t, T_ANCHOR, T_NEW, f"{p}:sim cases")
                stage_py(p, t)
                staged[p] = t
        for path, edits in ((BT, ((B_ST_OLD, B_ST_NEW, "state"), (B_LS_OLD, B_LS_NEW, "LS"), (B_LSD_OLD, B_LSD_NEW, "LS deps"),
                                  (B_BC_OLD, B_BC_NEW, "buildConfig"), (B_DEPS_OLD, B_DEPS_NEW, "deps"),
                                  (B_DESC_OLD, B_DESC_NEW, "describeConfig"), (B_PAN_OLD, B_PAN_NEW, "panel"))),
                            (PF, ((P_OLD, P_NEW, "paramFormat"),)),
                            (SB, ((S_OLD, S_NEW, "axes"),)),
                            (RC, ((C_OLD, C_NEW, "exit keys"),))):
            t = path.read_text()
            if FENCE in t:
                skipped.append(path)
                continue
            for old, new, what in edits:
                t = rep(t, old, new, f"{path.name}:{what}")
            if path == BT:
                arm = t.split('if (sid === "BRK_V1") {', 1)[1].split('if (sid === "CBO_V1") {', 1)[0]
                # deps = the full brk* line in the buildConfig dep array (after the STALE-CLOSURE marker)
                dep_line = t.split("STALE-CLOSURE RULE: buildConfig reads every one of these.", 1)[1].split("\n", 2)[1]
                deps = {d.strip() for d in dep_line.strip().rstrip(",").split(",")}
                reads = set(re.findall(r"\bbrk[A-Z]\w*", arm))
                if reads - deps:
                    raise Abort(f"stale-closure: buildConfig reads {sorted(reads - deps)} not in deps")
            staged[path] = t
        if not a.no_esbuild:
            for p, t in staged.items():
                if p.suffix in (".jsx", ".js"):
                    esbuild_ok(p, t)
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1
    for p in skipped:
        print(f"  already fenced — skipped     {p}")
    for p, t in staged.items():
        if a.check:
            print(f"  would patch (clean)          {p}")
        else:
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
            p.write_text(t)
            print(f"  patched                      {p}")
    for t in missing:
        print(f"  SKIPPED (tree absent)        {t}")
    print(f"\n{FENCE} {'check complete' if a.check else 'applied'}.")
    if not a.check and staged:
        print("\nNext:")
        print("  1. python3 backend/app/backtest/brk/test_brk_runner_sim.py .   (59 checks)")
        print("  2. restart backend; sweep brk_ts_min 10/15/20/30 × brk_ts_pts 0/5/8 on the 16/46 seal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
