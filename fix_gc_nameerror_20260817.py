#!/usr/bin/env python3
# fix_gc_nameerror_20260817.py
#
# ── GC_NAMEERROR_20260817 ────────────────────────────────────────────────
# ONE-LINE MECHANICAL FIX. api_server.py:624 passes `broker_manager`, a name
# that exists only as gc_v1_runtime()'s PARAMETER — never in api_server's
# module scope (the singleton is `zerodha_manager`, line 389).
#
# The launch block is unguarded, so the NameError propagated to the outer
# except in _run_heavy_startup() and ABORTED STARTUP AT LINE 624. Everything
# after it never ran: GC_V1, TMA_V1, BrokerReconciliationJob, the ENTIRE
# scheduler block (all 13 EOD/morning crons), telegram scheduler, relay
# monitor, disk guard.
#
# Fired on 2026-08-16 and 2026-08-17 boots. One trading session lost.
#
# gc_v1_runtime() uses the arg as a ZerodhaManager (.get_data_kite(), and
# passes it to ZerodhaOrderExecutor), so `zerodha_manager` is correct.
#
# DUAL-TREE: lands in backend/ AND desktop/src-tauri/backend/.
# Assert-anchored: writes NOTHING unless every anchor passes in every tree.
# ─────────────────────────────────────────────────────────────────────────

import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
REL = Path("app/api_server.py")
TREES = [REPO / "backend", REPO / "desktop/src-tauri/backend"]

OLD = "            asyncio.create_task(gc_v1_runtime(broker_manager))"
NEW = "            asyncio.create_task(gc_v1_runtime(zerodha_manager))"

# Anchors that must hold BEFORE the edit (proves we are editing the file we
# think we are, at the version we think it is).
PRE_ANCHORS = [
    ("gc launch line", OLD, 1),
    ("singleton exists", "zerodha_manager = ZerodhaManager()", 1),
    ("no stray broker_manager", "broker_manager", 1),  # only the bad call
    ("gc gate intact", 'license_state.license_allows_strategy("GC_V1")', 1),
    ("tma launch follows", "asyncio.create_task(tma_selection_loop(zerodha_manager))", 1),
]

# Anchors that must hold AFTER the edit.
POST_ANCHORS = [
    ("fixed line", NEW, 1),
    ("broker_manager gone", "broker_manager", 0),
    ("tma launch untouched", "asyncio.create_task(tma_selection_loop(zerodha_manager))", 1),
]


def check(label, text, needle, want):
    got = text.count(needle)
    ok = got == want
    print(f"  [{'ok  ' if ok else 'MISS'}] {label}: found {got}, want {want}")
    return ok


def undefined_names(path: Path):
    """pyflakes F821 scan. py_compile CANNOT catch NameError — this can.
    Returns list of offending lines, or None if pyflakes is unavailable."""
    try:
        r = subprocess.run([sys.executable, "-m", "pyflakes", str(path)],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    return [ln for ln in r.stdout.splitlines() if "undefined name" in ln]


def main():
    targets = []
    all_ok = True

    for tree in TREES:
        path = tree / REL
        print(f"\n=== {path} ===")
        if not path.exists():
            print("  [SKIP] tree not present on this machine")
            continue
        text = path.read_text(encoding="utf-8")
        for label, needle, want in PRE_ANCHORS:
            all_ok &= check(label, text, needle, want)
        targets.append((path, text))

    if not targets:
        print("\nABORT: no api_server.py found in either tree.")
        return 1
    if not all_ok:
        print("\nABORT: pre-anchor failure — NOTHING written.")
        return 1

    # Dry-run every edit in a temp file before touching anything on disk.
    print("\n=== dry run ===")
    staged = []
    for path, text in targets:
        new_text = text.replace(OLD, NEW)
        ok = True
        for label, needle, want in POST_ANCHORS:
            ok &= check(f"{path.parts[-4]}/{label}", new_text, needle, want)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(new_text)
            tmp = Path(tf.name)
        try:
            py_compile.compile(str(tmp), doraise=True)
            print(f"  [ok  ] py_compile: {path.parts[-4]}")
        except py_compile.PyCompileError as e:
            print(f"  [MISS] py_compile: {e}")
            ok = False
        und = undefined_names(tmp)
        if und is None:
            print("  [WARN] pyflakes unavailable — install it "
                  "(pip install pyflakes --break-system-packages)")
        elif und:
            print("  [MISS] pyflakes undefined names remain:")
            for ln in und:
                print(f"         {ln}")
            ok = False
        else:
            print(f"  [ok  ] pyflakes: no undefined names")
        tmp.unlink(missing_ok=True)
        all_ok &= ok
        staged.append((path, new_text))

    if not all_ok:
        print("\nABORT: dry-run failure — NOTHING written.")
        return 1

    print("\n=== writing ===")
    for path, new_text in staged:
        path.write_text(new_text, encoding="utf-8")
        print(f"  written: {path}")

    print("\nDONE. Next:")
    print("  1. Rebuild (non-trading evening, market closed).")
    print("  2. Boot and confirm BOTH lines appear in today's log:")
    print("       grep -n 'GC_V1 standalone runtime launched' ~/.scalp-app/logs/$(date +%F).log")
    print("       grep -n 'TMA_V1 standalone selection loop launched' ~/.scalp-app/logs/$(date +%F).log")
    print("       grep -n 'All EOD schedulers started' ~/.scalp-app/logs/$(date +%F).log")
    print("       grep -n 'Background startup complete' ~/.scalp-app/logs/$(date +%F).log")
    print("  3. Confirm NO '[SYSTEM][ERROR] Background startup failed' line.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
