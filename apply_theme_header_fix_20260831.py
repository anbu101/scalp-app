#!/usr/bin/env python3
# apply_theme_header_fix_20260831.py
#
# ── HEADER NAV DRIFT · FIX 2 ── left-anchor the nav (supersedes the 2a grid)
# ============================================================================
# Requires phase 2a (THEME_PHASE2A_20260831) on App.jsx.
#
# WHY THE 2a GRID DID NOT HOLD
#   `minmax(max-content,1fr) auto minmax(max-content,1fr)` only centres the
#   nav when BOTH side clusters fit in an equal half-share. At 2420 px with
#   two account chips the right cluster is ~600 px and the half-share is
#   ~555 px, so the grid drops into its concession regime on every render —
#   and there the nav moves by the FULL pill delta (screenshots: "Dashboard"
#   at x≈598 for a 1-digit P&L, x≈584 for 2 digits, identical in all three
#   themes). A truly centred nav (~1230 px) would overlap the pill by ~30 px
#   at this width, so centring is not achievable here at all.
#
# FIX
#   1. Left-anchor: [brand][fixed gap][nav] … [right cluster, marginLeft:auto].
#      The nav's x is now a function of the brand width only. Nothing that
#      renders on the right — P&L digits, balance digits, chip count, bell
#      badge — can move it.
#   2. Reserve width on the P&L number (inline-block, minWidth 7ch, right-
#      aligned, tabular digits) so the pill itself stops jittering its
#      neighbours when the digit count changes.
#
# Idempotent (fence THEME_HEADER_FIX_20260831), esbuild gate before write,
# .bak backup. Frontend-only; Tauri rebuild.
#
# USAGE
#   cd <repo root>
#   python3 apply_theme_header_fix_20260831.py --dry-run
#   python3 apply_theme_header_fix_20260831.py

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

FENCE = "THEME_HEADER_FIX_20260831"
PHASE2A = "THEME_PHASE2A_20260831"
REPO = os.getcwd()
FE_TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
            (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"),
             "desktop-fe")]


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:100]}")


# 1 ── wrapper: grid → flex, left-anchored
WRAP_A = '''      {/* ── THEME_PHASE2A_20260831 ── 3-track grid: the nav sits in the middle `auto` track and
          the two outer tracks are equal whenever there is room, so a wider
          P&L pill (more digits) or extra account chips never push the nav
          sideways; max-content floors mean nothing is ever clipped. The old
          [flex:1][nav][flex:1] layout centred the nav between the brand and
          the right cluster, which moved by half of every width change. */}
      <div style={{ padding: compact ? "0 14px" : "0 24px",
        display: "grid", gridTemplateColumns: "minmax(max-content,1fr) auto minmax(max-content,1fr)",
        alignItems: "center", height: 54, gap: compact ? 8 : 16 }}>'''
WRAP_N = f'''      {{/* ── {FENCE} ── LEFT-ANCHORED header. The nav sits right after the
          brand at a fixed gap; the right cluster is pushed to the edge with
          marginLeft:auto. The nav's x therefore depends on the brand width
          ONLY — P&L digits, balance digits, chip count or bell badge can
          never move it. (Centring was tried twice and cannot work at this
          width: 7 nav items + two account chips leave no equal half-share,
          so any centring scheme degrades to "nav follows the right cluster".) */}}
      <div style={{{{ padding: compact ? "0 14px" : "0 24px",
        display: "flex", alignItems: "center", height: 54, gap: compact ? 8 : 16 }}}}>'''

# 2 ── brand: drop grid-only justifySelf
BRAND_A = 'display: "flex", alignItems: "center", gap: 8, flexShrink: 0, justifySelf: "start" }}>'
BRAND_N = 'display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>'

# 3 ── nav: fixed lead-in gap so it does not hug the brand
NAV_A = '''        <div style={{ display: "flex", gap: 2, flexShrink: 0 }}>
          {navItems.map((item) => {'''
NAV_N = f'''        <div style={{{{ display: "flex", gap: 2, flexShrink: 0, marginLeft: compact ? 8 : 40 }}}}>   {{/* ── {FENCE} ── */}}
          {{navItems.map((item) => {{'''

# 4 ── right cluster: justifySelf → marginLeft:auto
RIGHT_A = 'gap: compact ? 8 : 14, flexShrink: 0, justifySelf: "end" }}>'
RIGHT_N = 'gap: compact ? 8 : 14, flexShrink: 0, marginLeft: "auto" }}>'

# 5 ── pill number: reserved width so digit-count changes don't jitter
PILL_A = '''      <span style={{ fontSize: 15, fontWeight: 800, fontFamily: "'JetBrains Mono','Fira Code',monospace", color }}>
        {total >= 0 ? "+" : "−"}₹{Math.round(Math.abs(total)).toLocaleString("en-IN")}
      </span>'''
PILL_N = f'''      {{/* ── {FENCE} ── reserved width (7ch fits "−₹12,345"); grows beyond
          that only for lakh-scale days. Right-aligned so the sign/₹ move,
          not the pill's edges. */}}
      <span style={{{{ fontSize: 15, fontWeight: 800, fontFamily: "'JetBrains Mono','Fira Code',monospace", color,
        display: "inline-block", minWidth: "7ch", textAlign: "right", fontVariantNumeric: "tabular-nums" }}}}>
        {{total >= 0 ? "+" : "−"}}₹{{Math.round(Math.abs(total)).toLocaleString("en-IN")}}
      </span>'''


def edit_app(t):
    if FENCE in t:
        return t, 0
    if PHASE2A not in t:
        die("App.jsx lacks phase 2a — run apply_theme_phase2a_20260831.py first")
    for a, lbl in ((WRAP_A, "wrap"), (BRAND_A, "brand"), (NAV_A, "nav"),
                   (RIGHT_A, "right"), (PILL_A, "pill")):
        one(t, a, lbl)
    for a, n in ((WRAP_A, WRAP_N), (BRAND_A, BRAND_N), (NAV_A, NAV_N),
                 (RIGHT_A, RIGHT_N), (PILL_A, PILL_N)):
        t = t.replace(a, n, 1)
    if "gridTemplateColumns" in t or "justifySelf" in t:
        die("grid remnants left in App.jsx")
    return t, 5


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
            if subprocess.run(c + ["--log-level=silent", canary],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=90).returncode == 0:
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
    for root, label in FE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        path = os.path.join(root, "App.jsx")
        if not os.path.isfile(path):
            die(f"[{label}] missing {path}")
        out, n = edit_app(open(path).read())
        if n == 0:
            notes.append(f"[{label}] SKIP (fenced): App.jsx")
        else:
            writes[path] = out
            notes.append(f"[{label}] EDIT ({n}): App.jsx")
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
        tmp = tempfile.mkdtemp(prefix="hdr_fix_")
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
        shutil.copy2(dest, dest + f".bak-{FENCE}")
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print(f"\nDONE. Header nav left-anchored (fence {FENCE}). Tauri rebuild.")


if __name__ == "__main__":
    main()
