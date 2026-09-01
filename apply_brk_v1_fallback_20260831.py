#!/usr/bin/env python3
# apply_brk_v1_fallback_20260831.py
#
# ── BRK_V1_FALLBACK_20260831 ── D3.4: "trade every day" fallback toggle.
#
#   fallback_enabled   bool (default False)
#   fallback_min_pts   float (default 0): the chosen side must have gained at
#                      least this much since its 09:25 selection print
#
# On a day with NO confirmed break by entry_last, at entry_last: for each
# selected side compare the last completed close (bar entry_last−1) with its
# selection print; take the side with the LARGER gain (moved most toward the
# level). Fill at the entry_last open; same SL/TP/trail/time-stop/EOD. Days
# with a break are untouched; a both_policy=skip day is a break day and stays
# skipped. Condition tag "BRK·FB·<side>·HH:MM" so Entry Conditions separates
# fallback trades from real breaks. Diag: fallback_entries / fallback_ce /
# fallback_pe / days_fallback_skip (no side gained ≥ min).
#
# The trade-open block is factored into a local open_pos() so the breakout
# path and the fallback path build IDENTICAL trades (one copy, not two).
# Touches (both trees): runner + sim. Frontend: Backtest.jsx BRK panel,
# paramFormat.js, SweepBuilder.jsx. Assert-anchored, replace-once, staged
# py_compile, esbuild gate, .bak-FENCE.
#     python3 apply_brk_v1_fallback_20260831.py --check
#     python3 apply_brk_v1_fallback_20260831.py

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "BRK_V1_FALLBACK_20260831"
TREES = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
RUNNER = "backtest/brk/backtest_brk_runner.py"
TEST = "backtest/brk/test_brk_runner_sim.py"
BT = Path("frontend/src/pages/Backtest.jsx")
PF = Path("frontend/src/pages/backtest/paramFormat.js")
SB = Path("frontend/src/pages/backtest/SweepBuilder.jsx")

# ── runner ──
R_DEF_OLD = '''    "time_stop_need_pts": 0.0,         # ── BRK_V1_TIMESTOP_20260831 ── exit unless close ≥ entry + X at that minute
'''
R_DEF_NEW = R_DEF_OLD + f'''    "fallback_enabled": False,         # ── {FENCE} ── no break by entry_last -> buy the side that gained most
    "fallback_min_pts": 0.0,           # ── {FENCE} ── that side must be ≥ this above its 09:25 print
'''
R_NORM_OLD = '''              "time_stop_need_pts"):'''
R_NORM_NEW = f'''              "time_stop_need_pts", "fallback_min_pts"):'''
R_NORM2_OLD = '''    cfg["skip_expiry_day"] = bool(cfg.get("skip_expiry_day", False))'''
R_NORM2_NEW = f'''    cfg["skip_expiry_day"] = bool(cfg.get("skip_expiry_day", False))
    cfg["fallback_enabled"] = bool(cfg.get("fallback_enabled", False))   # ── {FENCE} ──'''
R_DIAG_OLD = '''        "entries": 0, "ce_entries": 0, "pe_entries": 0,'''
R_DIAG_NEW = f'''        "entries": 0, "ce_entries": 0, "pe_entries": 0,
        "fallback_entries": 0, "fallback_ce": 0, "fallback_pe": 0,   # ── {FENCE} ──
        "days_fallback_skip": 0,'''
# factor the open block into open_pos(); breakout path calls it
R_OPEN_OLD = '''            sym = ce_sym if side == "CE" else pe_sym
            fb = bars(sym).get(ds + m * 60)
            if fb is None or not fb.open:
                # No print at the decision minute: cannot fill at its open.
                # Try the next decision minute (the confirm may still hold).
                diag["days_no_fill"] += 1
                continue
            entry_px = float(fb.open)
            sl_px = round(entry_px - cfg["sl_pts"], 2)
            tp_px = round(entry_px + cfg["tp_pts"], 2) if cfg["tp_pts"] > 0 else None   # ── BRK_V1_RATCHET_20260831 ──
            mc = meta[sym]
            t = BRKTrade(
                tradingsymbol=sym, symbol=sym, instrument_type=side,
                strike=float(mc["strike"]) if mc.get("strike") is not None else None,
                expiry=mc.get("expiry"), direction="BUY",
                entry_ts=ds + m * 60, entry_price=round(entry_px, 2),
                sl=sl_px, tp=tp_px, exit_ts=None, exit_price=None,
                exit_reason=None, qty=qty,
                condition=f"BRK·{side}·{m // 60:02d}:{m % 60:02d}")
            trades.append(t)
            diag["entries"] += 1
            diag["ce_entries" if side == "CE" else "pe_entries"] += 1
            hk = f"{m // 60:02d}:{m % 60:02d}"
            diag["entry_minute_hist"][hk] = diag["entry_minute_hist"].get(hk, 0) + 1
            pos = {"symbol": sym, "trade": t, "entry_px": entry_px,
                   "sl_px": sl_px, "tp_px": tp_px, "qty": qty,
                   "raised": False, "last_mark": entry_px,
                   "mae": 0.0, "mfe": 0.0, "entry_min": m,
                   "hh": entry_px}   # ── BRK_V1_RATCHET_20260831 ── highest high since entry
            break
'''
R_OPEN_NEW = f'''            sym = ce_sym if side == "CE" else pe_sym
            pos = open_pos(sym, side, m, "BRK")   # ── {FENCE} ── shared open path
            if pos is None:
                # No print at the decision minute: cannot fill at its open.
                # Try the next decision minute (the confirm may still hold).
                diag["days_no_fill"] += 1
                continue
            break
'''
R_LOOP_OLD = '''        # ── D2/D3/D4: decision loop ──
        pos: Optional[dict] = None
        saw_confirm = False
'''
R_LOOP_NEW = f'''        # ── {FENCE} ── ONE trade-open path for breakout and fallback entries.
        # Returns the pos dict, or None when the fill bar has no print.
        def open_pos(sym: str, side: str, m: int, tag: str) -> Optional[dict]:
            fb = bars(sym).get(ds + m * 60)
            if fb is None or not fb.open:
                return None
            entry_px = float(fb.open)
            sl_px = round(entry_px - cfg["sl_pts"], 2)
            tp_px = round(entry_px + cfg["tp_pts"], 2) if cfg["tp_pts"] > 0 else None   # ── BRK_V1_RATCHET_20260831 ──
            mc = meta[sym]
            t = BRKTrade(
                tradingsymbol=sym, symbol=sym, instrument_type=side,
                strike=float(mc["strike"]) if mc.get("strike") is not None else None,
                expiry=mc.get("expiry"), direction="BUY",
                entry_ts=ds + m * 60, entry_price=round(entry_px, 2),
                sl=sl_px, tp=tp_px, exit_ts=None, exit_price=None,
                exit_reason=None, qty=qty,
                condition=f"{{tag}}·{{side}}·{{m // 60:02d}}:{{m % 60:02d}}")
            trades.append(t)
            diag["entries"] += 1
            diag["ce_entries" if side == "CE" else "pe_entries"] += 1
            hk = f"{{m // 60:02d}}:{{m % 60:02d}}"
            diag["entry_minute_hist"][hk] = diag["entry_minute_hist"].get(hk, 0) + 1
            return {{"symbol": sym, "trade": t, "entry_px": entry_px,
                    "sl_px": sl_px, "tp_px": tp_px, "qty": qty,
                    "raised": False, "last_mark": entry_px,
                    "mae": 0.0, "mfe": 0.0, "entry_min": m,
                    "hh": entry_px}}   # ── BRK_V1_RATCHET_20260831 ── highest high since entry

        # ── D2/D3/D4: decision loop ──
        pos: Optional[dict] = None
        saw_confirm = False
'''
R_FB_OLD = '''        if pos is None:
            if not saw_confirm:
                diag["days_no_break"] += 1
            continue
'''
R_FB_NEW = f'''        if pos is None and not saw_confirm and cfg["fallback_enabled"]:
            # ── {FENCE} ── no break all window: buy the side that moved
            # most toward the level since its selection print. Decision at
            # entry_last on the last COMPLETED bar; fill at entry_last open.
            pos = None
            best = None   # (gain, side, sym)
            for side, sym in (("CE", ce_sym), ("PE", pe_sym)):
                if not sym:
                    continue
                last = closes[side].get(last_min - 1)
                if last is None:
                    continue
                gain = last - prints[side][sym]
                key = (gain, side)
                if best is None or key > (best[0], best[1]):
                    best = (gain, side, sym)
            if best is not None and best[0] >= cfg["fallback_min_pts"]:
                pos = open_pos(best[2], best[1], last_min, "BRK·FB")
                if pos is not None:
                    diag["fallback_entries"] += 1
                    diag["fallback_ce" if best[1] == "CE" else "fallback_pe"] += 1
                else:
                    diag["days_no_fill"] += 1
            if pos is None:
                diag["days_fallback_skip"] += 1
                diag["days_no_break"] += 1
                continue
        elif pos is None:
            if not saw_confirm:
                diag["days_no_break"] += 1
            continue
'''
R_LOG_OLD = '''        f"EOD {diag['eod_exits']} / TIME {diag['time_exits']}, days noBreak {diag['days_no_break']} / "'''
R_LOG_NEW = f'''        f"EOD {{diag['eod_exits']}} / TIME {{diag['time_exits']}}, fallback {{diag['fallback_entries']}}, "
        f"days noBreak {{diag['days_no_break']}} / "'''

T_ANCHOR = '''print(f"\\n{'ALL PASS' if not FAILED else f'{len(FAILED)} FAILED: ' + ', '.join(FAILED)}")'''
T_NEW = f'''# ── {FENCE} ── fallback entry
print("\\n── E. fallback (trade every day) ──────────────────────────────────")
# 28. No break by 09:30 (entry_last). CE 175 -> 178 (+3), PE 170 -> 168 (−2): fallback buys CE at 09:30 open.
rows = (series(*CE, 175, {{M0929: (176, 179, 175, 178), M0930: (178, 178, 178, 178),
                          M0931: (178, 230, 178, 225)}})
        + series(*PE, 170, {{M0929: (170, 170, 167, 168)}}))
r = run(build(rows), {{"fallback_enabled": True, "entry_last": "09:30"}})
t = r["trades"][0] if r["trades"] else None
chk("28. fallback: no break -> buys the side that gained most (CE +3) at entry_last open",
    t is not None and t.instrument_type == "CE" and t.entry_price == 178 and t.entry_ts == ts(M0930)
    and t.condition.startswith("BRK·FB·CE"), f"cond={{t.condition if t else None}}")
chk("28. diag: fallback_entries 1, fallback_ce 1, days_no_break 0",
    r["summary"]["diag_brk"]["fallback_entries"] == 1 and r["summary"]["diag_brk"]["fallback_ce"] == 1
    and r["summary"]["diag_brk"]["days_no_break"] == 0)
chk("28. fallback trade uses the same exits (TP at +40)",
    t is not None and t.exit_reason == "TP" and t.exit_price == 218)
# 29. Same tape, toggle off -> no trade (regression).
r = run(build(rows), {{"fallback_enabled": False, "entry_last": "09:30"}})
chk("29. fallback off -> no trade, days_no_break 1",
    not r["trades"] and r["summary"]["diag_brk"]["days_no_break"] == 1)
# 30. Both sides fell since 09:25 -> min 0 requires gain >= 0 -> skipped.
rows = (series(*CE, 175, {{M0929: (175, 175, 172, 173)}}) + series(*PE, 170, {{M0929: (170, 170, 167, 168)}}))
r = run(build(rows), {{"fallback_enabled": True, "entry_last": "09:30"}})
chk("30. both sides fell -> no fallback trade, days_fallback_skip 1",
    not r["trades"] and r["summary"]["diag_brk"]["days_fallback_skip"] == 1)
# 31. fallback_min_pts 5 blocks a +3 gain.
rows = (series(*CE, 175, {{M0929: (176, 179, 175, 178)}}) + series(*PE, 170))
r = run(build(rows), {{"fallback_enabled": True, "fallback_min_pts": 5, "entry_last": "09:30"}})
chk("31. min_pts 5 blocks a +3 mover", not r["trades"])
r = run(build(rows), {{"fallback_enabled": True, "fallback_min_pts": 3, "entry_last": "09:30"}})
chk("31. min_pts 3 admits a +3 mover", len(r["trades"]) == 1)
# 32. A real break still takes precedence (CE confirmed) and is tagged BRK not BRK·FB.
rows = (series(*CE, 175, {{M0929: (176, 182, 175, 181), M0930: (181, 181, 181, 181)}}) + series(*PE, 170))
r = run(build(rows), {{"fallback_enabled": True}})
chk("32. break day unchanged with fallback on (tag BRK·CE, fallback_entries 0)",
    r["trades"][0].condition.startswith("BRK·CE") and r["summary"]["diag_brk"]["fallback_entries"] == 0)
# 33. Fallback fires at entry_last when the window is 09:30–09:35 (decision on the 09:34 close, fill 09:35 open).
rows = (series(*CE, 175, {{574: (176, 179, 175, 179), M0935: (179, 179, 179, 179)}}) + series(*PE, 170))
r = run(build(rows), {{"fallback_enabled": True, "entry_last": "09:35"}})
t = r["trades"][0] if r["trades"] else None
chk("33. window 09:30–09:35: fallback decides on the 09:34 close, fills 09:35 open (179)",
    t is not None and t.entry_ts == ts(M0935) and t.entry_price == 179 and t.condition.endswith("09:35"))

''' + T_ANCHOR

# ── Backtest.jsx ──
B_ST_OLD = '  const [brkTsPts, setBrkTsPts] = useState(brkSaved.tsPts ?? 0);       // ── BRK_V1_TIMESTOP_20260831 ──\n'
B_ST_NEW = B_ST_OLD + f'''  const [brkFb, setBrkFb] = useState(brkSaved.fb ?? false);            // ── {FENCE} ──
  const [brkFbMin, setBrkFbMin] = useState(brkSaved.fbMin ?? 0);       // ── {FENCE} ──
'''
B_LS_OLD = 'tsMin: brkTsMin, tsPts: brkTsPts, eod: brkEod,'
B_LS_NEW = 'tsMin: brkTsMin, tsPts: brkTsPts, fb: brkFb, fbMin: brkFbMin, eod: brkEod,'
B_LSD_OLD = 'brkTsMin, brkTsPts, brkEod, brkLots, brkLotSize, brkSkipExpiry]);'
B_LSD_NEW = 'brkTsMin, brkTsPts, brkFb, brkFbMin, brkEod, brkLots, brkLotSize, brkSkipExpiry]);'
B_BC_OLD = '        time_stop_need_pts: Number(brkTsPts) || 0,     // ── BRK_V1_TIMESTOP_20260831 ──\n'
B_BC_NEW = B_BC_OLD + f'''        fallback_enabled: !!brkFb,                     // ── {FENCE} ──
        fallback_min_pts: Number(brkFbMin) || 0,       // ── {FENCE} ──
'''
B_DEPS_OLD = 'brkTsMin, brkTsPts, brkEod, brkLots, brkLotSize, brkSkipExpiry,\n'
B_DEPS_NEW = 'brkTsMin, brkTsPts, brkFb, brkFbMin, brkEod, brkLots, brkLotSize, brkSkipExpiry,\n'
B_DESC_OLD = '''    if (Number(cfg.time_stop_min) > 0) add("Time stop", `+₹${cfg.time_stop_need_pts} by ${cfg.time_stop_min}m`);   // ── BRK_V1_TIMESTOP_20260831 ──'''
B_DESC_NEW = B_DESC_OLD + f'''
    if (cfg.fallback_enabled) add("Fallback", `best mover${{Number(cfg.fallback_min_pts) > 0 ? ` ≥+₹${{cfg.fallback_min_pts}}` : ""}}`);   // ── {FENCE} ──'''
B_PAN_OLD = '''                <Field label="EOD square-off"><input type="text" style={inputStyle} value={brkEod} onChange={(e) => setBrkEod(e.target.value)} title="Open position is closed at this minute's 1m close. Same clock as the live cron would use." /></Field>'''
B_PAN_NEW = f'''                {{/* ── {FENCE} ── fallback toggle */}}
                <label style={{{{ fontSize: 12, display: "flex", alignItems: "center", gap: 6, alignSelf: "flex-end", paddingBottom: 6 }}}}
                  title="On a day with NO confirmed break by entry-last, buy the selected side that gained the most since its 09:25 print, at the entry-last open. Same SL/TP/trail/EOD. Tagged BRK·FB in Entry Conditions.">
                  <input type="checkbox" checked={{brkFb}} onChange={{(e) => setBrkFb(e.target.checked)}} /> fallback: trade every day
                </label>
                <Field label="Fallback needs +₹"><input type="number" style={{inputStyle}} value={{brkFbMin}} onChange={{(e) => setBrkFbMin(Number(e.target.value))}} disabled={{!brkFb}} title="The best mover must be at least this far above its 09:25 print. 0 = any non-negative move; a day where both sides fell is skipped." /></Field>
''' + B_PAN_OLD

# ── paramFormat.js ──
P_OLD = '''  if (Number(cfg.time_stop_min) > 0) p.push(`ts${cfg.time_stop_min}m/${Number(cfg.time_stop_need_pts) || 0}`);   // ── BRK_V1_TIMESTOP_20260831 ──'''
P_NEW = P_OLD + f'''
  if (cfg.fallback_enabled) p.push(`FB${{Number(cfg.fallback_min_pts) > 0 ? `≥${{cfg.fallback_min_pts}}` : ""}}`);   // ── {FENCE} ──'''

# ── SweepBuilder.jsx ──
S_OLD = '''  { key: "brk_eod", label: "BRK EOD square-off", strategies: [BRK],'''
S_NEW = f'''  // ── {FENCE} ── fallback on/off (0 = off, >0 = on with that min gain; use 0.001 for "on, any move")
  {{ key: "brk_fb", label: "BRK fallback min +₹ (−1 = off)", strategies: [BRK],
    hint: "-1, 0, 3, 5", parse: _num,
    apply: (c, v) => {{ c.fallback_enabled = v >= 0; c.fallback_min_pts = v >= 0 ? v : 0; }},
    fmt: (v) => (v >= 0 ? `FB≥${{v}}` : "no FB") }},
''' + S_OLD


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
    tmp = path.parent / f"_fb_stage{path.suffix}"
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
            if "BRK_V1_TIMESTOP_20260831" not in t:
                raise Abort(f"{p}: apply_brk_v1_timestop_20260831.py must be applied first")
            if FENCE in t:
                skipped.append(p)
            else:
                for old, new, what in ((R_DEF_OLD, R_DEF_NEW, "defaults"), (R_NORM_OLD, R_NORM_NEW, "normalise floats"),
                                       (R_NORM2_OLD, R_NORM2_NEW, "normalise bool"), (R_DIAG_OLD, R_DIAG_NEW, "diag"),
                                       (R_OPEN_OLD, R_OPEN_NEW, "open block -> call"), (R_LOOP_OLD, R_LOOP_NEW, "open_pos def"),
                                       (R_FB_OLD, R_FB_NEW, "fallback branch"), (R_LOG_OLD, R_LOG_NEW, "audit line")):
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
                            (SB, ((S_OLD, S_NEW, "axes"),))):
            t = path.read_text()
            if FENCE in t:
                skipped.append(path)
                continue
            for old, new, what in edits:
                t = rep(t, old, new, f"{path.name}:{what}")
            if path == BT:
                arm = t.split('if (sid === "BRK_V1") {', 1)[1].split('if (sid === "CBO_V1") {', 1)[0]
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
        print("  1. python3 backend/app/backtest/brk/test_brk_runner_sim.py .   (68 checks)")
        print("  2. restart backend; BRK V1 panel: tick 'fallback: trade every day' on the 16/46 seal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
