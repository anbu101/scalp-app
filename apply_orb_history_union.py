#!/usr/bin/env python3
# apply_orb_history_union.py — widen trade_history's paper-table LIVE
# union from BRK-only to GENERIC (trade_mode='LIVE'), closing the last
# item that was deferred to ORB's LIVE promotion. Nothing is now gated
# on going live.
#
# Fence: ORB_HISTORY_UNION_20260904   PREREQUISITE: the BRK day-1 fix (verified).
#
# WHY GENERIC IS SAFE HERE (verified 2026-09-04): this route has NO other
# union reading paper_trades LIVE rows (TSG included), so widening cannot
# double-count; private-table strategies' LIVE rows live in `trades` and
# never match trade_mode='LIVE' in paper_trades. Bonus: TSG's live legs —
# invisible to this endpoint since forever — now surface. The mapper keeps
# its name (_query_brk_live) for grep history; the specific-strategy
# filter moves INSIDE it. BRK rows: byte-identical output (strategy_name
# fills strategy_id; group_id fills slot exactly as before).
#
# This supersedes the "widen at LIVE promotion" TODO recorded in
# apply_orb_day1_scars.py's header. Checklist 2.9/2.12 mode-split rule now
# holds on ALL FOUR live surfaces.
#
# LIVE-PATH FILE — apply outside market hours, then rebuild.
#
# USAGE: python3 apply_orb_history_union.py --check && python3 apply_orb_history_union.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_HISTORY_UNION_20260904'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {}

EDITS = [('backend/app/api/trade_history_routes.py', 'replace', '    # BRK_HISTORY BEGIN ── BRK_V1 LIVE union (isolated — never breaks\n    # history). UNLIKE the private-table strategies above, BRK stores LIVE\n    # rows in the GENERIC paper_trades table (trade_mode=\'LIVE\'), which this\n    # endpoint otherwise never reads — day-1 scar 2026-09-03: the open live\n    # trade was invisible on Analytics.\n    if (not strategy_id) or strategy_id == "all" or strategy_id == "BRK_V1":\n        try:\n            result.extend(_query_brk_live(from_ts, to_ts))\n        except Exception:\n            # table/column drift must never break the whole history feed.\n            pass\n    # BRK_HISTORY END\n', "    # BRK_HISTORY BEGIN ── paper-table LIVE union — GENERIC since\n    # ── ORB_HISTORY_UNION_20260904 ── (was BRK-only on day 1). Every\n    # strategy storing LIVE rows in the generic paper_trades table\n    # (trade_mode='LIVE': BRK_V1, ORB_V1, TSG_V1's live legs, any future\n    # one — NO edit needed) is invisible to the trades+private-table reads\n    # above; this union surfaces them all. A specific strategy filter is\n    # applied INSIDE the mapper; private-table ids simply match nothing.\n    # Checklist 2.9/2.12 mode-split rule.\n    try:\n        result.extend(_query_brk_live(from_ts, to_ts, strategy_id=strategy_id))\n    except Exception:\n        # table/column drift must never break the whole history feed.\n        pass\n    # BRK_HISTORY END\n", 1), ('backend/app/api/trade_history_routes.py', 'replace', "# Maps paper_trades WHERE strategy_name='BRK_V1' AND trade_mode='LIVE' to\n", "# Maps paper_trades WHERE trade_mode='LIVE' — ALL paper-table strategies,\n# generic since ── ORB_HISTORY_UNION_20260904 ── (name kept for grep\n# history) — to\n", 1), ('backend/app/api/trade_history_routes.py', 'replace', 'def _query_brk_live(from_ts, to_ts):\n', 'def _query_brk_live(from_ts, to_ts, strategy_id=None):\n', 1), ('backend/app/api/trade_history_routes.py', 'replace', '        clauses = ["strategy_name = \'BRK_V1\'", "trade_mode = \'LIVE\'"]\n        params = []\n', '        clauses = ["trade_mode = \'LIVE\'"]   # ── ORB_HISTORY_UNION_20260904 ──\n        params = []\n        if strategy_id and strategy_id != "all":\n            # specific filter — private-table ids match nothing, harmlessly\n            clauses.append("strategy_name = ?")\n            params.append(strategy_id)\n', 1), ('backend/app/api/trade_history_routes.py', 'replace', '            "strategy_id": "BRK_V1",\n', '            "strategy_id": d.get("strategy_name") or "BRK_V1",   # ── ORB_HISTORY_UNION_20260904 ──\n', 1), ('backend/app/api/trade_history_routes.py', 'replace', '            "slot": d.get("group_id") or "BRK",\n', '            "slot": d.get("group_id") or (d.get("strategy_name") or "BRK")[:3],\n', 1)]

VERIFY = [('backend/app/api/trade_history_routes.py', 'ORB_HISTORY_UNION_20260904', 4), ('backend/app/api/trade_history_routes.py', "strategy_name = 'BRK_V1'", 0), ('backend/app/api/trade_history_routes.py', 'strategy_id == "BRK_V1"', 0)]



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

    # ── prerequisite + idempotency ──
    probe = os.path.join(ROOT, "backend", "app", "api",
                         "trade_history_routes.py")
    ptext = open(probe, encoding="utf-8").read()
    if "_query_brk_live" not in ptext:
        fail("the BRK day-1 history fix must be present first")
    if FENCE in ptext:
        print(f"  SKIP   history union already generic — "
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
        ok = (got == 0) if mn == 0 else (got >= mn)
        want = "must be ABSENT" if mn == 0 else f"need >= {mn}"
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} ({want})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   LIVE history union is now generic. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD python3 smoke below, then rebuild")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
