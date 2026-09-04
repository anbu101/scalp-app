#!/usr/bin/env python3
# apply_orb_gapfix.py — ORB_V1 gap-sweep closure (checklist Part 5 #5).
#
# Fence: ORB_GAPFIX_20260903   PREREQUISITE: apply_orb_live3.py (verified).
#
# The sweep found 22 files naming donor strategies but not ORB_V1. Eleven
# edits here; the rest are CONSCIOUS SKIPS, each with its reason:
#   paper_trades_routes / trade_history_routes / telegram_summary_data —
#     generic paper_trades reads, NO strategy whitelist (verified) — 2.12.
#   paper_trade_squareoff / eod_safety — exemption list only; ORB is
#     deliberately NOT exempt (engine EOD 13:00 < the 15:25 sweep) — 2.10.
#   day_cycle — ORB runs its own BRK-style loop; no day_cycle tags.
#   vet/tma/tsg private files + panels — other strategies' internals.
#   Portfolio.jsx IS edited (color + short code) so ORB backtest runs
#     render in the allocator; allocation membership itself is a separate
#     operator decision.
#
# EDITS: strategy_display (codename "Outrider" — non-admin aliasing),
# lots_whitelist (["lots"] — the only friend-touchable knob),
# account_bindings (executor list + BUY book), license_server/server.py
# (missing id = 400 on override save), displayNames.js,
# LotsOnlySettings.jsx (id + color + lots field), Portfolio.jsx.
#
# USAGE: python3 apply_orb_gapfix.py --check && python3 apply_orb_gapfix.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_GAPFIX_20260903'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {}

EDITS = [('backend/app/config/strategy_display.py', 'after', '    "VET_V1":    ("VET V1",         "Velvet"),\n', '    "ORB_V1":    ("ORB V1",         "Outrider"),   # ── ORB_V1 2026-09-03 ──\n', 1), ('backend/app/config/lots_whitelist.py', 'after', '    "VET_V1":    ["quantity.lots"],\n', '    # ── ORB_V1 2026-09-03 ── sealed strategy: lots is the ONLY sizing\n    # knob a non-admin may touch (target A/B stays admin-side).\n    "ORB_V1":    ["lots"],\n', 1), ('backend/app/config/account_bindings.py', 'after', '    "VET_V1",\n', '    "ORB_V1",   # ── ORB_V1 2026-09-03 ── same executor path\n', 1), ('backend/app/config/account_bindings.py', 'after', '    "VET_V1": "BUY",\n', '    "ORB_V1": "BUY",   # ── ORB_V1 ── long options only, both sides\n', 1), ('license_server/server.py', 'after', '    "VET_V1",   # ── VET_V1 added 2026-08-29 — missing id = 400 on override save\n', '    "ORB_V1",   # ── ORB_V1 added 2026-09-03 — missing id = 400 on override save\n', 1), ('frontend/src/strategies/displayNames.js', 'after', '  VET_V1:    { real: "VET V1",        code: "Velvet",     sub: "NIFTY 5m trend" },\n', '  ORB_V1:    { real: "ORB V1",        code: "Outrider",   sub: "15m ORB breakout" },   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/LotsOnlySettings.jsx', 'after', '  "VET_V1",\n', '  "ORB_V1",   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/LotsOnlySettings.jsx', 'after', '  VET_V1: "#34d399",\n', '  ORB_V1: "#f59e0b",   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/LotsOnlySettings.jsx', 'after', '  VET_V1:    [{ label: "Number of Lots", helper: "One position at a time; the wing (when selling) always matches this size", paths: ["quantity.lots"] }],\n', '  ORB_V1:    [{ label: "Number of Lots", helper: "One position at a time, max 2 trades/day; everything closed by 13:00", paths: ["lots"] }],   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/backtest/Portfolio.jsx', 'after', '  VET_V1: "#f97316",   // ── VET_V1 ── orange-500 (distinct from every accent above)\n', '  ORB_V1: "#f59e0b",   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/backtest/Portfolio.jsx', 'after', '  VET_V1: "VET",   // ── VET_V1 ──\n', '  ORB_V1: "ORB",   // ── ORB_V1 ──\n', 1)]

VERIFY = [('backend/app/config/strategy_display.py', '"ORB_V1"', 1), ('backend/app/config/lots_whitelist.py', '"ORB_V1"', 1), ('backend/app/config/account_bindings.py', '"ORB_V1"', 2), ('license_server/server.py', '"ORB_V1"', 1), ('frontend/src/strategies/displayNames.js', 'ORB_V1', 1), ('frontend/src/pages/LotsOnlySettings.jsx', 'ORB_V1', 3), ('frontend/src/pages/backtest/Portfolio.jsx', 'ORB_V1', 2)]



def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    """A backend-relative path lands in both trees; frontend in one."""
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def stage_edit(text, kind, anchor, payload, count, path):
    n = text.count(anchor)
    if kind == "replaceall":
        if n != count:
            fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
        return text.replace(anchor, payload)
    if n != count:
        fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
    if kind == "replace":
        return text.replace(anchor, payload)
    if kind == "before":
        return text.replace(anchor, payload + anchor)
    if kind == "after":
        return text.replace(anchor, anchor + payload)
    fail(f"unknown edit kind {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--single-tree", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard "
             "requirement locally; pass --single-tree only on a CI checkout")

    # ── prerequisite ──
    probe = os.path.join(ROOT, "frontend", "src", "strategies", "orb",
                         "ORBPanel.jsx")
    if not os.path.exists(probe):
        fail("apply_orb_live3.py must be applied first")
    probe_text = open(os.path.join(ROOT, "backend", "app", "config",
                                   "strategy_display.py"),
                      encoding="utf-8").read()
    if FENCE in probe_text or '"ORB_V1"' in probe_text:
        print(f"  SKIP   gap edits already present — "
              f"nothing to do")
        return

    # ── stage every write in memory first ──
    staged = {}   # abs path -> new text
    for rel, body in PAYLOADS.items():
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                fail(f"{p} already exists (half-applied tree?)")
            staged[p] = body
    per_file = {}
    for rel, kind, anchor, payload, count in EDITS:
        per_file.setdefault(rel, []).append((kind, anchor, payload, count))
    for rel, ops in per_file.items():
        src_path = os.path.join(ROOT, rel)
        if not os.path.exists(src_path):
            fail(f"{src_path} not found")
        text = open(src_path, encoding="utf-8").read()
        if FENCE in text:
            fail(f"{rel} already carries the fence — mixed state, resolve by hand")
        for kind, anchor, payload, count in ops:
            text = stage_edit(text, kind, anchor, payload, count, rel)
        for p in both_trees(rel, a.single_tree):
            if p != src_path and not os.path.exists(p):
                fail(f"dual-tree copy missing: {p}")
            staged[p] = text

    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── staged compile gates ──
    tmp = tempfile.mkdtemp(prefix="orv_gate_")
    jsx_targets = []
    for p, body in staged.items():
        t = os.path.join(tmp, os.path.basename(p))
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        elif p.endswith((".jsx", ".js")):
            jsx_targets.append((p, t))
    print(f"  OK     py_compile gate passed")
    esb = shutil.which("esbuild")
    npx = shutil.which("npx")
    for p, t in jsx_targets:
        cmd = None
        if esb:
            cmd = [esb, "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        elif npx:
            cmd = [npx, "--yes", "esbuild", "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        if cmd is None:
            print(f"  WARN   esbuild unavailable — JSX gate skipped for {p}")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "frontend"))
        if r.returncode != 0:
            fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
    if jsx_targets and (esb or npx):
        print(f"  OK     esbuild JSX gate passed ({len(jsx_targets)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  write {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write, with backups for edited files ──
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    # ── grep-count verification ──
    bad = 0
    for rel, needle, mn in VERIFY:
        got = open(os.path.join(ROOT, rel), encoding="utf-8").read().count(needle)
        ok = got >= mn
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} (need >= {mn})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   gap sweep closed. ORB_V1 integration COMPLETE. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD npm run tauri build && re-run the Part-5 sweep (expects only documented skips)")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
