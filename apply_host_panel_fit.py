#!/usr/bin/env python3
# apply_host_panel_fit.py — stop the closed-positions card drawing over
# "Today's Performance" on Scalp V1/V3/V5, HA, BB V1 and BB V2.
#
# Fence: HOST_PANEL_FIT_20260921
# PREREQUISITE: CLOSED_RECENT_FLEET_20260921 in StrategyHost.jsx (asserted).
#
# ROOT CAUSE (mine — the fleet patch was verified with server-side renders,
# which have no layout): the desktop focus column is a flex item stretched to
# the RAIL's height. Those six panels root themselves at height:"100%", so
# each took the WHOLE column; the card mounted under the panel therefore
# started at the column's bottom edge and overflowed the host by its own
# height, straight over the next dashboard section. Panels without a 100%
# root (IC, TSG, TMA, VET, BRK, ORB) were never affected.
#
# FIX — one file, StrategyHost.jsx, two anchored edits; NO panel is touched
# (BBPanel included), mobile render site unchanged (its column is not
# stretched, nothing overflowed there):
#   * the focus column becomes a flex column;
#   * the panel is wrapped in <PanelBox>:
#       default      content-height box. height:"100%" inside an auto-height
#                    box computes to auto, so Scalp/HA shrink to their slot
#                    cards — the dead space under them is what is trimmed —
#                    and the card sits directly below.
#       BB_V1/BB_V2  the chart sizes itself from its container, so BB gets a
#                    DEFINITE height: the rest of the column after the card,
#                    floor 600 px (the host grows if the rail is shorter).
#                    Done with an absolutely-positioned inner box — definite
#                    in WKWebView and WebView2 alike, no reliance on
#                    percentage heights resolving through nested flex items.
#
# Verified in real Chromium against the real StrategyHost + all 14 real
# panels (mocked backend): before — the six panels overflow the host by
# exactly the card height; after — 0 px overflow on all 14, BB panel box
# = 600 px with the panel filling it.
#
# Frontend only: ./desktop/build-scalp.sh frontend  (no backend restart).
# USAGE: python3 apply_host_panel_fit.py --check && python3 apply_host_panel_fit.py

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tempfile

FENCE = "HOST_PANEL_FIT_20260921"
PARENT_FENCE = "CLOSED_RECENT_FLEET_20260921"
ROOT = os.path.dirname(os.path.abspath(__file__))
HOST = "frontend/src/components/StrategyHost.jsx"

A1 = 'function renderPanel(strategyId, ltpMap) {\n'
BLOCK = '// ── HOST_PANEL_FIT_20260921 BEGIN ── desktop focus column sizing.\n// The focus column is stretched to the RAIL\'s height (alignItems: stretch).\n// Six panels root themselves at height:"100%" (Scalp V1/V3/V5, HA, BB V1/V2),\n// so each claimed the WHOLE column and anything mounted under it — the\n// closed-positions card — overflowed the host and drew over "Today\'s\n// Performance". The column is now a flex column and the panel sits in a box:\n//   * default — box is content-height. Inside an auto-height box a panel\'s\n//     height:"100%" computes to auto, so Scalp/HA shrink to their slot cards\n//     (the dead space under them is what gets trimmed) and the card follows.\n//   * FILL_PANELS — BB\'s chart sizes itself from its container (ResizeObserver\n//     on a flex:1 area), so it needs a DEFINITE height: it takes what is left\n//     of the column after the card, never less than the floor. The inner box\n//     is absolutely positioned because an absolute box\'s height is definite in\n//     every engine (Tauri = WKWebView on macOS, WebView2 on Windows) — no\n//     reliance on percentage-height resolution through nested flex items.\n// No panel file is touched (BBPanel included). Mobile render site unchanged:\n// its column is not stretched, so nothing overflowed there.\nconst FILL_PANELS = { BB_V1: 600, BB_V2: 600 };   // id -> minimum panel height (px)\n\nfunction PanelBox({ id, children }) {\n  const floor = FILL_PANELS[id];\n  if (!floor) return <div style={{ flex: "0 0 auto", minWidth: 0 }}>{children}</div>;\n  return (\n    <div style={{ position: "relative", flex: "1 1 0", minHeight: floor, minWidth: 0 }}>\n      <div style={{ position: "absolute", top: 0, right: 0, bottom: 0, left: 0 }}>{children}</div>\n    </div>\n  );\n}\n// ── HOST_PANEL_FIT_20260921 END ──\n\n'
A2 = '        style={{ flex: 1, minWidth: 0, animation: "hostFocusIn 0.32s cubic-bezier(0.22,1,0.36,1)" }}\n      >\n        <KillSwitch strategyId={effectiveFocus} />\n        {renderPanel(effectiveFocus, ltpMap)}\n'
B2 = '        style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", animation: "hostFocusIn 0.32s cubic-bezier(0.22,1,0.36,1)" }}   /* ── HOST_PANEL_FIT_20260921 ── flex column */\n      >\n        <KillSwitch strategyId={effectiveFocus} />\n        <PanelBox id={effectiveFocus}>{renderPanel(effectiveFocus, ltpMap)}</PanelBox>   {/* ── HOST_PANEL_FIT_20260921 ── */}\n'


def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry run, write nothing")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="proceed although git shows local edits on StrategyHost.jsx "
                         "(expected if the fleet patch is applied but not yet committed)")
    a = ap.parse_args()

    p = os.path.join(ROOT, HOST)
    if not os.path.exists(p):
        fail(f"{HOST} not found — run from the scalp-app repo root")
    src = open(p, encoding="utf-8").read()
    if FENCE in src:
        print("  SKIP   host panel fit already present — nothing to do")
        return
    if PARENT_FENCE not in src or "<ClosedRecentFor strategyId={effectiveFocus} />" not in src:
        fail(f"{PARENT_FENCE} not found in {HOST} — apply apply_closed_recent_fleet.py first")
    for name, anchor in (("renderPanel", A1), ("desktop focus column", A2)):
        if src.count(anchor) != 1:
            fail(f"{HOST}: anchor [{name}] x{src.count(anchor)}, expected x1 — the host was edited "
                 f"by something else; inspect by hand")

    status = ""
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--", HOST], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        status = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        pass
    if status and not a.allow_dirty:
        fail("StrategyHost.jsx has uncommitted edits:\n         " + status
             + "\n         if that is just the fleet patch you have not committed yet, commit it"
               "\n         first (preferred) or re-run with --allow-dirty")
    print("  OK     parent fence + anchors verified" + (" (dirty tree allowed)" if status else ""))

    new = src.replace(A1, BLOCK + A1).replace(A2, B2)
    # the desktop site must be wrapped, the mobile site must not
    if new.count("<PanelBox id={effectiveFocus}>") != 1 or new.count("{renderPanel(effectiveFocus, ltpMap)}") != 2:
        fail("internal: unexpected render-site counts after edit")

    tmp = os.path.join(tempfile.mkdtemp(prefix="hpf_gate_"), "StrategyHost.jsx")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    esb, npx = shutil.which("esbuild"), shutil.which("npx")
    cmd = ([esb] if esb else [npx, "--yes", "esbuild"] if npx else None)
    if cmd is None:
        print("  WARN   esbuild unavailable — JSX gate skipped")
    else:
        g = subprocess.run(cmd + ["--loader:.jsx=jsx", tmp, "--outfile=" + os.devnull],
                           capture_output=True, text=True, cwd=os.path.join(ROOT, "frontend"))
        if g.returncode != 0:
            fail(f"esbuild gate:\n{g.stderr[-2000:]}")
        print("  OK     esbuild JSX gate passed")

    if a.check:
        print(f"  WOULD  edit   {p}")
        print("  CHECK  dry run complete — no files written")
        return

    shutil.copy2(p, p + f".bak-{FENCE}")
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"  WROTE  {p}")
    got = open(p, encoding="utf-8").read().count(FENCE)
    if got < 4:
        shutil.copy2(p + f".bak-{FENCE}", p)
        fail(f"verification failed (fence x{got}) — file restored")
    print(f"  OK     {HOST}: {FENCE!r} x{got}")
    print()
    print("  DONE   frontend only — ./desktop/build-scalp.sh frontend, reopen the Dashboard.")
    print("         Check: Scalp V1/V3/V5, HA, BB V1/V2 — card sits under the panel,")
    print("         'Today's Performance' starts clear below it.")


if __name__ == "__main__":
    main()
