#!/usr/bin/env python3
# apply_vet_hedge_field_fix_20260827.py
#
# ── TWO DEFECTS IN THE HEDGE FIELD ───────────────────────────────────────
#
# 1. LITERAL "\n" ON SCREEN. A previous patch escaped a newline as "\\n"
#    inside a Python string, so a backslash-n pair was written into the JSX
#    as a TEXT NODE and React rendered it verbatim between the hedge field
#    and Strike selection. Replaced with a real newline.
#
# 2. THE FIELD LOOKED DEAD. It displayed `vetHedge ? vetHedgeMax : 0`, and
#    the boolean defaulted to false, so the box read 0 even though the
#    underlying default was 5 — indistinguishable from "not wired up".
#
#    The boolean is now REMOVED entirely. The number IS the switch: 0 means
#    naked, anything above 0 means hedged at that budget. One control, one
#    meaning, nothing to get out of sync. hedge_enabled is derived in
#    buildConfig, so the runner contract is unchanged.
#
#    Consequence worth knowing: with the box defaulting to 5, a SELL run is
#    now HEDGED by default instead of naked. That is the safer default for a
#    short option and matches how the knob was asked for, but it does change
#    what a fresh SELL run does — set the box to 0 to sell bare.
#
# Idempotent, assert-anchored, dual-tree, staged esbuild check.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_hedge_field_fix_20260827.py --dry-run
#   python3 apply_vet_hedge_field_fix_20260827.py

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.getcwd()
TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
         (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"), "desktop-fe")]
BT = os.path.join("pages", "Backtest.jsx")

# 1. the literal backslash-n text node
BAD_NL = '/></Field>\\n                <Field label="Strike selection">'
GOOD_NL = '/></Field>\n                <Field label="Strike selection">'

# 2. drop the redundant boolean
ST_OLD = '  const [vetHedge, setVetHedge] = useState(vetSaved.hedge ?? false);\n'
PS_OLD = 'hedge: vetHedge, hedgeMax: vetHedgeMax,'
PS_NEW = 'hedgeMax: vetHedgeMax,'
DEP_OLD = 'vetLots, vetLegAction, vetHedge, vetHedgeMax,'
DEP_NEW = 'vetLots, vetLegAction, vetHedgeMax,'
CFG_OLD = '        hedge_enabled: !!vetHedge,\n'
CFG_NEW = ('        // ── the number IS the switch: 0 = naked, > 0 = hedged.\n'
           '        hedge_enabled: Math.abs(Number(vetHedgeMax) || 0) > 0,\n')
VAL_OLD = 'value={vetHedge ? vetHedgeMax : 0}'
VAL_NEW = 'value={vetHedgeMax}'
ON_OLD = ('onChange={(e) => { const v = Math.abs(Number(e.target.value) || 0); '
          'setVetHedgeMax(v); setVetHedge(v > 0); }}')
ON_NEW = 'onChange={(e) => setVetHedgeMax(Math.abs(Number(e.target.value) || 0))}'


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def edit(t):
    n = 0
    if BAD_NL in t:
        if t.count(BAD_NL) != 1:
            die(f"literal newline artifact appears {t.count(BAD_NL)} times")
        t = t.replace(BAD_NL, GOOD_NL, 1)
        n += 1
    if "vetHedge " in t or "setVetHedge(" in t or VAL_OLD in t:
        for needle, lbl in ((ST_OLD, "state"), (PS_OLD, "persist"),
                            (CFG_OLD, "buildConfig"), (VAL_OLD, "value"),
                            (ON_OLD, "onChange")):
            if t.count(needle) != 1:
                die(f"anchor count {t.count(needle)}, expected 1 [{lbl}]")
        if t.count(DEP_OLD) != 2:
            die(f"dep arrays: found {t.count(DEP_OLD)}, expected 2")
        t = t.replace(ST_OLD, "", 1)
        t = t.replace(PS_OLD, PS_NEW, 1)
        t = t.replace(DEP_OLD, DEP_NEW)
        t = t.replace(CFG_OLD, CFG_NEW, 1)
        t = t.replace(VAL_OLD, VAL_NEW, 1)
        t = t.replace(ON_OLD, ON_NEW, 1)
        n += 6
    return t, n


def find_esbuild(canary):
    cands = []
    loc = os.path.join(REPO, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.isfile(loc) and os.access(loc, os.X_OK):
        cands.append(([loc], "node_modules"))
    p = shutil.which("esbuild")
    if p:
        cands.append(([p], "PATH"))
    npx = shutil.which("npx")
    if npx:
        cands += [([npx, "--no", "esbuild"], "npx"),
                  ([npx, "--no-install", "esbuild"], "npx7")]
    for c, w in cands:
        try:
            if subprocess.run(c + ["--log-level=silent", canary], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=90).returncode == 0:
                return c, w
        except Exception:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        path = os.path.join(root, BT)
        if not os.path.isfile(path):
            die(f"[{label}] missing {path}")
        src = open(path).read()
        if "vetHedgeMax" not in src:
            die(f"[{label}] hedge field not present — apply "
                f"apply_vet_hedge_leg_20260827.py first")
        out, n = edit(src)
        if n == 0:
            notes.append(f"[{label}] SKIP (already correct): {BT}")
        else:
            writes[path] = out
            notes.append(f"[{label}] EDIT ({n}): {BT}")
    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── JSX SYNTAX CHECK ─────────────────────────────────────────")
    if a.skip_jsx_check:
        print("  skipped by request")
    else:
        tmp = tempfile.mkdtemp(prefix="vet_hfix_")
        try:
            can = os.path.join(tmp, "c.jsx")
            open(can, "w").write("const A = () => <div>{1}</div>;\n")
            cmd, where = find_esbuild(can)
            if cmd is None:
                print("  !! no working esbuild — check SKIPPED (not an error)")
            else:
                print(f"  esbuild via {where}")
                for i, (dest, body) in enumerate(writes.items()):
                    st = os.path.join(tmp, f"s{i}.jsx")
                    open(st, "w").write(body)
                    r = subprocess.run(cmd + ["--log-level=warning", st],
                                       capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=120)
                    if r.returncode != 0:
                        die(f"esbuild FAILED for {dest}:\n{r.stderr[:1500]}")
                print(f"  {len(writes)} file(s) parse clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. Rebuild the frontend.")
    print("  the stray \\n between the hedge field and Strike selection is gone")
    print("  the hedge box now shows its real value (default 5), 0 = naked")


if __name__ == "__main__":
    main()
