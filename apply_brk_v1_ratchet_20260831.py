#!/usr/bin/env python3
# apply_brk_v1_ratchet_20260831.py
#
# ── BRK_V1_RATCHET_20260831 ── D3.2 exit modes for BRK_V1:
#
#   * tp_pts = 0  → fixed target OFF (the trail / SL / EOD are the only exits)
#   * trail_mode = "lock" (existing one-shot lock) | "ratchet"
#   * ratchet: stop = highest 1m high since entry − trail_gap, moves only
#     UP, re-evaluated every closed bar; arms immediately (trail_trigger_pts
#     = 0, i.e. Zerodha GTT-trailing semantics) or after +trail_trigger_pts.
#     Fires when a bar's LOW touches the stop; fills AT the stop; reason
#     TRAIL. Pessimistic ordering is kept: a bar's high raises the stop for
#     the NEXT bar, never for itself.
#
# Touches (both trees): brk runner + sim suite. Frontend: Backtest.jsx BRK
# panel (Trail mode select + Trail gap field, buildConfig, deps, LS,
# describeConfig), paramFormat.js, SweepBuilder.jsx (brk_gap axis).
# Assert-anchored, replace-once, staged py_compile, esbuild gate, .bak-FENCE.
#     python3 apply_brk_v1_ratchet_20260831.py --check
#     python3 apply_brk_v1_ratchet_20260831.py

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "BRK_V1_RATCHET_20260831"
TREES = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
RUNNER = "backtest/brk/backtest_brk_runner.py"
TEST = "backtest/brk/test_brk_runner_sim.py"
BT = Path("frontend/src/pages/Backtest.jsx")
PF = Path("frontend/src/pages/backtest/paramFormat.js")
SB = Path("frontend/src/pages/backtest/SweepBuilder.jsx")

# ═════════════════════════════════════════════════════════════════════════
#  RUNNER
# ═════════════════════════════════════════════════════════════════════════
R_DEF_OLD = '''    "trail_trigger_pts": 0.0,          # 0 = trail off
    "trail_lock_pts": 0.0,             # stop -> entry + lock once triggered
'''
R_DEF_NEW = f'''    "trail_trigger_pts": 0.0,          # lock: 0 = off | ratchet: arm after +X (0 = from entry)
    "trail_lock_pts": 0.0,             # lock mode: stop -> entry + lock once triggered
    "trail_mode": "lock",              # ── {FENCE} ── lock | ratchet
    "trail_gap": 0.0,                  # ── {FENCE} ── ratchet: stop = max high − gap (0 = off)
'''
R_NORM_OLD = '''    for k in ("select_below", "select_min", "break_above", "sl_pts",
              "tp_pts", "trail_trigger_pts", "trail_lock_pts"):'''
R_NORM_NEW = f'''    _tm = str(cfg.get("trail_mode", "lock")).lower()   # ── {FENCE} ──
    cfg["trail_mode"] = _tm if _tm in ("lock", "ratchet") else "lock"
    for k in ("select_below", "select_min", "break_above", "sl_pts",
              "tp_pts", "trail_trigger_pts", "trail_lock_pts", "trail_gap"):'''
R_VAL_OLD = '''    if cfg["sl_pts"] <= 0 or cfg["tp_pts"] <= 0:
        return _abort(cfg, strategy_id, "sl_pts and tp_pts must both be > 0")'''
R_VAL_NEW = f'''    if cfg["sl_pts"] <= 0:
        return _abort(cfg, strategy_id, "sl_pts must be > 0")
    # ── {FENCE} ── tp_pts 0 = no fixed target (trail/SL/EOD only).
    if cfg["trail_mode"] == "ratchet" and cfg["trail_gap"] <= 0:
        return _abort(cfg, strategy_id, "trail_mode ratchet needs trail_gap > 0")
    if cfg["tp_pts"] <= 0 and not (cfg["trail_mode"] == "ratchet" and cfg["trail_gap"] > 0) \\
            and cfg["trail_trigger_pts"] <= 0:
        # Not an error, but say it: with no target and no trail the only
        # profitable exit is EOD. Allowed (it is the diagnostic run).
        pass'''
R_EXIT_OLD = '''    sl_hit = float(bar.low) <= sl_px
    tp_hit = float(bar.high) >= tp_px'''
R_EXIT_NEW = f'''    sl_hit = float(bar.low) <= sl_px
    tp_hit = tp_px is not None and float(bar.high) >= tp_px   # ── {FENCE} ── None = TP off'''
R_TP_OLD = '''            tp_px = round(entry_px + cfg["tp_pts"], 2)'''
R_TP_NEW = f'''            tp_px = round(entry_px + cfg["tp_pts"], 2) if cfg["tp_pts"] > 0 else None   # ── {FENCE} ──'''
R_POS_OLD = '''                   "raised": False, "last_mark": entry_px,
                   "mae": 0.0, "mfe": 0.0, "entry_min": m}'''
R_POS_NEW = f'''                   "raised": False, "last_mark": entry_px,
                   "mae": 0.0, "mfe": 0.0, "entry_min": m,
                   "hh": entry_px}}   # ── {FENCE} ── highest high since entry'''
R_DIAG_OLD = '''        "stale_marks": 0, "trail_armed": 0,'''
R_DIAG_NEW = f'''        "stale_marks": 0, "trail_armed": 0,
        "trail_ratchets": 0,   # ── {FENCE} ── stop raises in ratchet mode'''
R_LADDER_OLD = '''            # D7: trail arms from the NEXT bar (pessimistic ordering).
            if trig > 0 and not pos["raised"] and \\
                    float(b.high) >= pos["entry_px"] + trig:
                new_sl = round(pos["entry_px"] + cfg["trail_lock_pts"], 2)
                if new_sl > pos["sl_px"]:
                    pos["sl_px"] = new_sl
                    pos["raised"] = True
                    pos["trade"].sl = new_sl
                    diag["trail_armed"] += 1'''
R_LADDER_NEW = f'''            # D7: trail arms from the NEXT bar (pessimistic ordering).
            if cfg["trail_mode"] == "ratchet":
                # ── {FENCE} ── Zerodha GTT-trailing semantics on closed 1m
                # bars: stop follows the highest high by trail_gap, only up.
                pos["hh"] = max(pos["hh"], float(b.high))
                if pos["hh"] >= pos["entry_px"] + trig:
                    new_sl = round(pos["hh"] - cfg["trail_gap"], 2)
                    if new_sl > pos["sl_px"]:
                        if not pos["raised"]:
                            diag["trail_armed"] += 1
                        pos["sl_px"] = new_sl
                        pos["raised"] = True
                        pos["trade"].sl = new_sl
                        diag["trail_ratchets"] += 1
            elif trig > 0 and not pos["raised"] and \\
                    float(b.high) >= pos["entry_px"] + trig:
                new_sl = round(pos["entry_px"] + cfg["trail_lock_pts"], 2)
                if new_sl > pos["sl_px"]:
                    pos["sl_px"] = new_sl
                    pos["raised"] = True
                    pos["trade"].sl = new_sl
                    diag["trail_armed"] += 1'''

# ── sim additions (appended before the final print) ──
T_ANCHOR = '''print(f"\\n{'ALL PASS' if not FAILED else f'{len(FAILED)} FAILED: ' + ', '.join(FAILED)}")'''
T_NEW = f'''# ── {FENCE} ── ratchet trail + TP off
print("\\n── C. ratchet trail / TP off ─────────────────────────────────────")
# 16. Ratchet gap 20 from entry, no TP: run to +60 then fade -> exit at 60-20.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          M0931: (181, 211, 181, 210),      # hh 211 -> stop 191 (next bar)
                          M0932: (210, 241, 210, 240),      # hh 241 -> stop 221
                          M0933: (240, 240, 230, 232),      # low 230 > 221: holds
                          574:   (232, 232, 200, 205)}})    # low 200 <= 221: TRAIL @221
        + series(*PE, 170))
r = run(build(rows), {{"tp_pts": 0, "trail_mode": "ratchet", "trail_gap": 20}})
t = r["trades"][0]
chk("16. ratchet: stop follows hh−gap, fires on low, fills AT 221 (TRAIL)",
    t.exit_reason == "TRAIL" and t.exit_price == 221 and t.exit_ts == ts(574) and t.tp is None,
    f"reason={{t.exit_reason}} px={{t.exit_price}} tp={{t.tp}}")
chk("16. diag counts ratchets (2 raises) and one arm",
    r["summary"]["diag_brk"]["trail_ratchets"] == 2 and r["summary"]["diag_brk"]["trail_armed"] == 1)
# 17. Same tape with TP 46 still on: TP wins at 227 on the 09:32 bar.
r = run(build(rows), {{"tp_pts": 46, "trail_mode": "ratchet", "trail_gap": 20}})
chk("17. ratchet + TP: fixed target still caps the trade",
    r["trades"][0].exit_reason == "TP" and r["trades"][0].exit_price == 227)
# 18. Gap wider than the run: ratchet stop never exceeds the original SL -> SL.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          M0931: (181, 190, 181, 189), M0932: (189, 189, 150, 155)}})
        + series(*PE, 170))
r = run(build(rows), {{"tp_pts": 0, "trail_mode": "ratchet", "trail_gap": 40}})
chk("18. gap 40 on a 9-pt run: original SL (entry−20 = 161) still governs",
    r["trades"][0].exit_reason == "SL" and r["trades"][0].exit_price == 161)
# 19. trail_trigger 30 arms the ratchet only after +30.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          M0931: (181, 201, 181, 200),      # +20: NOT armed (stop stays 165)
                          M0932: (200, 200, 170, 172),      # low 170 > 165 holds; would be TRAIL@181 if armed
                          M0933: (172, 215, 172, 214),      # +34 hh 215 -> armed, stop 195
                          574:   (214, 214, 190, 192)}})    # low 190 <= 195: TRAIL @195
        + series(*PE, 170))
r = run(build(rows), {{"tp_pts": 0, "trail_mode": "ratchet", "trail_gap": 20, "trail_trigger_pts": 30}})
t = r["trades"][0]
chk("19. ratchet arms only after +trigger; earlier dip survives",
    t.exit_reason == "TRAIL" and t.exit_price == 195 and t.exit_ts == ts(574),
    f"reason={{t.exit_reason}} px={{t.exit_price}}")
# 20. TP off, no trail: holds to EOD.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          **{{m: (250, 251, 249, 250) for m in range(M0931, 917)}}}})
        + series(*PE, 170))
r = run(build(rows), {{"tp_pts": 0}})
chk("20. tp_pts 0 = no target: +69 run holds to EOD",
    r["trades"][0].exit_reason == "EOD" and r["trades"][0].exit_price == 250)
# 21. ratchet without a gap aborts.
r = run(build(rows), {{"trail_mode": "ratchet", "trail_gap": 0}})
chk("21. ratchet with gap 0 -> aborted", r.get("aborted") and "trail_gap" in r["reason"])
# 22. lock mode is unchanged (regression of case 8 semantics).
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181),
                          M0931: (181, 202, 181, 200), M0932: (200, 201, 199, 200),
                          M0933: (200, 200, 170, 172)}}) + series(*PE, 170))
r = run(build(rows), {{"trail_mode": "lock", "trail_trigger_pts": 20, "trail_lock_pts": 0}})
chk("22. lock mode unchanged: one-shot to entry, TRAIL @181",
    r["trades"][0].exit_reason == "TRAIL" and r["trades"][0].exit_price == 181)

''' + T_ANCHOR

# ═════════════════════════════════════════════════════════════════════════
#  Backtest.jsx
# ═════════════════════════════════════════════════════════════════════════
B_STATE_OLD = '  const [brkTrailLock, setBrkTrailLock] = useState(brkSaved.trailLock ?? 0);\n'
B_STATE_NEW = B_STATE_OLD + f'''  const [brkTrailMode, setBrkTrailMode] = useState(brkSaved.trailMode ?? "lock");   // ── {FENCE} ──
  const [brkTrailGap, setBrkTrailGap] = useState(brkSaved.trailGap ?? 0);           // ── {FENCE} ──
'''
B_LS_OLD = 'trailTrig: brkTrailTrig, trailLock: brkTrailLock, eod: brkEod,'
B_LS_NEW = 'trailTrig: brkTrailTrig, trailLock: brkTrailLock, trailMode: brkTrailMode, trailGap: brkTrailGap, eod: brkEod,'
B_LSDEP_OLD = '  }, [brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkTrailTrig, brkTrailLock, brkEod, brkLots, brkLotSize, brkSkipExpiry]);'
B_LSDEP_NEW = '  }, [brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkTrailTrig, brkTrailLock, brkTrailMode, brkTrailGap, brkEod, brkLots, brkLotSize, brkSkipExpiry]);'
B_BC_OLD = '''        trail_trigger_pts: Number(brkTrailTrig) || 0,
        trail_lock_pts: Number(brkTrailLock) || 0,
        eod_square_off: brkEod,'''
B_BC_NEW = f'''        trail_trigger_pts: Number(brkTrailTrig) || 0,
        trail_lock_pts: Number(brkTrailLock) || 0,
        trail_mode: brkTrailMode,                      // ── {FENCE} ──
        trail_gap: Number(brkTrailGap) || 0,           // ── {FENCE} ──
        eod_square_off: brkEod,'''
B_DEPS_OLD = '    brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkTrailTrig, brkTrailLock, brkEod, brkLots, brkLotSize, brkSkipExpiry,\n'
B_DEPS_NEW = '    brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkTrailTrig, brkTrailLock, brkTrailMode, brkTrailGap, brkEod, brkLots, brkLotSize, brkSkipExpiry,\n'
B_DESC_OLD = '''    add("SL / TP", `−₹${cfg.sl_pts} / +₹${cfg.tp_pts}`);
    if (Number(cfg.trail_trigger_pts) > 0) add("Trail", `@+₹${cfg.trail_trigger_pts} → entry${Number(cfg.trail_lock_pts) > 0 ? `+₹${cfg.trail_lock_pts}` : ""}`);'''
B_DESC_NEW = f'''    add("SL / TP", `−₹${{cfg.sl_pts}} / ${{Number(cfg.tp_pts) > 0 ? `+₹${{cfg.tp_pts}}` : "no target"}}`);   // ── {FENCE} ──
    if (cfg.trail_mode === "ratchet" && Number(cfg.trail_gap) > 0) add("Trail", `ratchet −₹${{cfg.trail_gap}}${{Number(cfg.trail_trigger_pts) > 0 ? ` after +₹${{cfg.trail_trigger_pts}}` : ""}}`);
    else if (Number(cfg.trail_trigger_pts) > 0) add("Trail", `@+₹${{cfg.trail_trigger_pts}} → entry${{Number(cfg.trail_lock_pts) > 0 ? `+₹${{cfg.trail_lock_pts}}` : ""}}`);'''
B_PANEL_OLD = '''                <Field label="Target ₹ (premium pts)"><input type="number" style={inputStyle} value={brkTp} onChange={(e) => setBrkTp(Number(e.target.value))} title="Target = entry + this. Triggers on the bar HIGH touching it; fills AT the level. SL and TP in one bar → SL." /></Field>
                <Field label="Trail trigger ₹ (0=off)"><input type="number" style={inputStyle} value={brkTrailTrig} onChange={(e) => setBrkTrailTrig(Number(e.target.value))} title="Once a bar's high reaches entry + this, the stop is raised (from the NEXT bar) to entry + lock. Exit on the raised stop is booked as TRAIL." /></Field>
                <Field label="Trail lock ₹"><input type="number" style={inputStyle} value={brkTrailLock} onChange={(e) => setBrkTrailLock(Number(e.target.value))} title="0 = breakeven. 10 = lock +10." /></Field>'''
B_PANEL_NEW = f'''                <Field label="Target ₹ (0 = none)"><input type="number" style={{inputStyle}} value={{brkTp}} onChange={{(e) => setBrkTp(Number(e.target.value))}} title="Target = entry + this. Triggers on the bar HIGH touching it; fills AT the level. SL and TP in one bar → SL. 0 = no fixed target (trail / SL / EOD only)." /></Field>
                {{/* ── {FENCE} ── trail mode + gap */}}
                <Field label="Trail mode">
                  <select style={{inputStyle}} value={{brkTrailMode}} onChange={{(e) => setBrkTrailMode(e.target.value)}}
                    title="lock: one-shot — after +trigger the stop moves once to entry+lock. ratchet: Zerodha GTT-trailing style — stop = highest 1m high since entry − gap, only moves up, re-checked every bar.">
                    <option value="lock">lock (one-shot)</option>
                    <option value="ratchet">ratchet (GTT trailing)</option>
                  </select>
                </Field>
                <Field label={{brkTrailMode === "ratchet" ? "Trail gap ₹" : "Trail gap ₹ (ratchet only)"}}><input type="number" style={{inputStyle}} value={{brkTrailGap}} onChange={{(e) => setBrkTrailGap(Number(e.target.value))}} disabled={{brkTrailMode !== "ratchet"}} title="Ratchet: the stop sits this far below the highest high since entry. Fires when a bar's low touches it; fills at the stop." /></Field>
                <Field label={{brkTrailMode === "ratchet" ? "Trail arm after +₹ (0=entry)" : "Trail trigger ₹ (0=off)"}}><input type="number" style={{inputStyle}} value={{brkTrailTrig}} onChange={{(e) => setBrkTrailTrig(Number(e.target.value))}} title={{brkTrailMode === "ratchet" ? "0 = the ratchet trails from the entry bar (exactly like a GTT trailing SL placed at entry). X = start trailing only once the trade is +X." : "Once a bar's high reaches entry + this, the stop is raised (from the NEXT bar) to entry + lock. Exit on the raised stop is booked as TRAIL."}} /></Field>
                <Field label="Trail lock ₹ (lock mode)"><input type="number" style={{inputStyle}} value={{brkTrailLock}} onChange={{(e) => setBrkTrailLock(Number(e.target.value))}} disabled={{brkTrailMode === "ratchet"}} title="Lock mode only. 0 = breakeven. 10 = lock +10." /></Field>'''
B_BLURB_OLD = '''Stop −₹{brkSl}, target +₹{brkTp} on the bought premium; both inside one minute → the STOP wins.'''
B_BLURB_NEW = f'''Stop −₹{{brkSl}}, {{Number(brkTp) > 0 ? `target +₹${{brkTp}}` : "no fixed target"}} on the bought premium; both inside one minute → the STOP wins.{{brkTrailMode === "ratchet" && Number(brkTrailGap) > 0 ? ` Ratchet trail: stop follows the highest high by ₹${{brkTrailGap}}${{Number(brkTrailTrig) > 0 ? ` once +₹${{brkTrailTrig}}` : " from entry"}}.` : ""}}'''

# ═════════════════════════════════════════════════════════════════════════
#  paramFormat.js
# ═════════════════════════════════════════════════════════════════════════
P_OLD = '''  p.push(`SL${cfg.sl_pts} TP${cfg.tp_pts}`);
  if (Number(cfg.trail_trigger_pts) > 0) p.push(`trail${cfg.trail_trigger_pts}/${Number(cfg.trail_lock_pts) || 0}`);'''
P_NEW = f'''  p.push(`SL${{cfg.sl_pts}} ${{Number(cfg.tp_pts) > 0 ? `TP${{cfg.tp_pts}}` : "noTP"}}`);   // ── {FENCE} ──
  if (cfg.trail_mode === "ratchet" && Number(cfg.trail_gap) > 0) p.push(`ratchet${{cfg.trail_gap}}${{Number(cfg.trail_trigger_pts) > 0 ? `@${{cfg.trail_trigger_pts}}` : ""}}`);
  else if (Number(cfg.trail_trigger_pts) > 0) p.push(`trail${{cfg.trail_trigger_pts}}/${{Number(cfg.trail_lock_pts) || 0}}`);'''

# ═════════════════════════════════════════════════════════════════════════
#  SweepBuilder.jsx
# ═════════════════════════════════════════════════════════════════════════
S_OLD = '''  { key: "brk_trail", label: "BRK trail trigger ₹ (0=off)", strategies: [BRK],'''
S_NEW = f'''  // ── {FENCE} ── ratchet gap (sets trail_mode=ratchet; 0 = lock mode / off)
  {{ key: "brk_gap", label: "BRK ratchet gap ₹ (0=off)", strategies: [BRK],
    hint: "0, 10, 15, 20, 30", parse: _num,
    apply: (c, v) => {{ c.trail_gap = Math.abs(v); c.trail_mode = v > 0 ? "ratchet" : "lock"; }},
    fmt: (v) => (v > 0 ? `ratchet${{Math.abs(v)}}` : "no ratchet") }},
  {{ key: "brk_tp", label: "BRK target ₹ (0=none)", strategies: [BRK],
    hint: "0, 30, 46, 60, 80", parse: _num,
    apply: (c, v) => {{ c.tp_pts = Math.abs(v); }}, fmt: (v) => (v > 0 ? `TP${{Math.abs(v)}}` : "noTP") }},
''' + S_OLD
S_OLDTP = '''  { key: "brk_tp", label: "BRK target ₹", strategies: [BRK],
    hint: "30, 40, 60, 80", parse: _num,
    apply: (c, v) => { c.tp_pts = Math.abs(v); }, fmt: (v) => `TP${Math.abs(v)}` },
'''


class Abort(Exception):
    pass


def rep(t, old, new, what, n=1):
    got = t.count(old)
    if got != n:
        raise Abort(f"{what}: anchor found {got} times, expected {n} — file drifted")
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
    tmp = path.parent / f"_ratchet_stage{path.suffix}"
    tmp.write_text(text)
    try:
        r = subprocess.run(["npx", "--yes", "esbuild", str(tmp), f"--loader:{path.suffix}={'jsx' if path.suffix == '.jsx' else 'js'}",
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
            if FENCE in t:
                skipped.append(p)
            else:
                for old, new, what in ((R_DEF_OLD, R_DEF_NEW, "defaults"), (R_NORM_OLD, R_NORM_NEW, "normalise"),
                                       (R_VAL_OLD, R_VAL_NEW, "validation"), (R_EXIT_OLD, R_EXIT_NEW, "resolve_exit"),
                                       (R_TP_OLD, R_TP_NEW, "tp_px"), (R_POS_OLD, R_POS_NEW, "pos hh"),
                                       (R_DIAG_OLD, R_DIAG_NEW, "diag"), (R_LADDER_OLD, R_LADDER_NEW, "ladder")):
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

        t = BT.read_text()
        if FENCE in t:
            skipped.append(BT)
        else:
            for old, new, what in ((B_STATE_OLD, B_STATE_NEW, "state"), (B_LS_OLD, B_LS_NEW, "LS payload"),
                                   (B_LSDEP_OLD, B_LSDEP_NEW, "LS deps"), (B_BC_OLD, B_BC_NEW, "buildConfig"),
                                   (B_DEPS_OLD, B_DEPS_NEW, "buildConfig deps"), (B_DESC_OLD, B_DESC_NEW, "describeConfig"),
                                   (B_PANEL_OLD, B_PANEL_NEW, "panel"), (B_BLURB_OLD, B_BLURB_NEW, "blurb")):
                t = rep(t, old, new, f"Backtest:{what}")
            # stale-closure: every brk* read inside the BRK buildConfig arm is in its deps
            arm = t.split('if (sid === "BRK_V1") {', 1)[1].split('if (sid === "CBO_V1") {', 1)[0]
            deps_line = B_DEPS_NEW.strip().rstrip(",")
            deps = {d.strip() for d in deps_line.split(",")}
            reads = set(re.findall(r"\bbrk[A-Z]\w*", arm))
            if reads - deps:
                raise Abort(f"stale-closure: buildConfig reads {sorted(reads - deps)} not in deps")
            staged[BT] = t

        t = PF.read_text()
        if FENCE in t:
            skipped.append(PF)
        else:
            staged[PF] = rep(t, P_OLD, P_NEW, "paramFormat")

        t = SB.read_text()
        if FENCE in t:
            skipped.append(SB)
        else:
            t = rep(t, S_OLDTP, "", "SweepBuilder old brk_tp axis")
            t = rep(t, S_OLD, S_NEW, "SweepBuilder axes")
            staged[SB] = t

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
        print("  1. python3 backend/app/backtest/brk/test_brk_runner_sim.py .   (53 checks)")
        print("  2. restart backend; npm start → BRK V1: Trail mode = ratchet, Target = 0, gap 20")
        print("  3. Sweep: brk_gap 10/15/20/30 × brk_tp 0/46 against the 16/46 seal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
