#!/usr/bin/env python3
"""
apply_stock_lot_auto_20260828.py
────────────────────────────────────────────────────────────────────────────
Fence: STOCK_LOT_AUTO_20260828

Makes stock lot sizes RESOLVE THEMSELVES instead of living in a hand-edited
dict. After this, adding a new stock corpus needs no code change and no
release: the runner reads the current lot straight off the Dhan scrip master
(cached, refreshed weekly, stale-tolerant).

WRITES
  backend/app/backtest/util/lot_sizes.py        (new — the resolver)
  backend/app/backtest/util/test_lot_sizes.py   (new — 20 behavioural tests)

EDITS (assert-anchored, replace-once, aborts before writing on any miss)
  backend/app/backtest/gc/backtest_gc_runner.py    import + ladder + diag
  backend/app/backtest/vet/backtest_vet_runner.py  import + ladder + diag
  backend/app/api/backtest_routes.py               stamp corpus_meta on backfill

NOT TOUCHED, DELIBERATELY
  LOT_SIZE (index constant, 65). Every sealed strategy has results locked
  against it. Sourcing it live would silently restate SCALP V1/V5, PST Sell
  and BB the next time NSE moves it.
  STOCK_LOT_SIZES stays where it is and is still consulted LAST, so the DIXON
  entry and any run that depended on it behave identically.

IDEMPOTENT — re-running is a no-op (fence markers are the guard).

Run from the repo root:  python3 apply_stock_lot_auto_20260828.py [--dry-run]
"""
from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

FENCE = "STOCK_LOT_AUTO_20260828"

ROOTS = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]

RESOLVER_SRC = Path("_stock_lot_auto_payload/lot_sizes.py")
TEST_SRC = Path("_stock_lot_auto_payload/test_lot_sizes.py")


# ── replace-once helper ────────────────────────────────────────────────────

def _ro(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{label}]: anchor matched {n} times, need "
                         f"exactly 1. Nothing written.\n--- anchor ---\n{old}")
    return text.replace(old, new, 1)


# ── edit bodies ────────────────────────────────────────────────────────────

IMPORT_BLOCK = f'''
# ── {FENCE} ── auto lot resolution. Stock lots come from the Dhan scrip-master
# cache (weekly refresh, stale-tolerant, offline-safe); indexes keep LOT_SIZE.
try:
    from app.backtest.util.lot_sizes import resolve_lot, unresolved_reason
except ImportError:  # standalone test harness
    from lot_sizes import resolve_lot, unresolved_reason   # type: ignore

'''


def _ladder(which: str) -> str:
    """which is 'GC' or 'VET' — only the abort payload differs."""
    return f'''    # ── {FENCE} ── lot: explicit config > index constant > live scrip
    # master > corpus stamp > stale cache > legacy static map > fail-closed.
    # A wrong qty is silent P&L corruption; no guessing, ever.
    lot_size, lot_source = resolve_lot(
        underlying=underlying, is_stock=is_stock, cfg_lot=cfg["lot_size"],
        index_lot=LOT_SIZE, db_path=db_path, static_map=STOCK_LOT_SIZES)
    if lot_size is None:
        return {{"run_id": None, "aborted": True,
                "reason": unresolved_reason(underlying),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}}
'''


GC_OLD_LADDER = '''    # ── GC_STOCK_MODE ── lot: explicit config > index constant > stock map >
    # fail-closed abort. A wrong qty is silent P&L corruption; no guessing.
    if cfg["lot_size"] > 0:
        lot_size = cfg["lot_size"]
    elif not is_stock:
        lot_size = LOT_SIZE
    elif underlying in STOCK_LOT_SIZES:
        lot_size = STOCK_LOT_SIZES[underlying]
    else:
        return {"run_id": None, "aborted": True,
                "reason": f"{underlying}: lot size unknown — set lot_size in "
                          f"the GC config (or add it to STOCK_LOT_SIZES)",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
'''

VET_OLD_LADDER = '''    if cfg["lot_size"] > 0:
        lot_size = cfg["lot_size"]
    elif not is_stock:
        lot_size = LOT_SIZE
    elif underlying in STOCK_LOT_SIZES:
        lot_size = STOCK_LOT_SIZES[underlying]
    else:
        return {"run_id": None, "aborted": True,
                "reason": f"{underlying}: lot size unknown — set lot_size in "
                          f"the VET config (or add it to STOCK_LOT_SIZES)",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
'''

GC_ANCHOR_SESSION = "SESSION_OPEN_MIN = 9 * 60 + 15          # 09:15 IST — C1 anchor"
VET_ANCHOR_SESSION = "SESSION_OPEN_MIN = 9 * 60 + 15   # 09:15 IST"

GC_DIAG_OLD = '"lot_size": lot_size, "min_entry_volume": min_vol,'
GC_DIAG_NEW = ('"lot_size": lot_size, "lot_source": lot_source,   # '
               + FENCE + '\n        "min_entry_volume": min_vol,')

VET_DIAG_OLD = '"lot_size": lot_size, "corpus_db": db_path.rsplit("/", 1)[-1],'
VET_DIAG_NEW = ('"lot_size": lot_size, "lot_source": lot_source,   # '
                + FENCE + '\n        "corpus_db": db_path.rsplit("/", 1)[-1],')

ROUTE_OLD = '''            report = {"underlying": und, "lot_size": ids["lot_size"],
                      "db": str(db)}
'''

ROUTE_NEW = f'''            report = {{"underlying": und, "lot_size": ids["lot_size"],
                      "db": str(db)}}
            # ── {FENCE} ── stamp the lot onto the corpus so the DB is
            # self-describing: a runner can then size correctly with no
            # network and no code entry for this symbol. Best-effort.
            try:
                from app.backtest.util.lot_sizes import write_corpus_meta
                from datetime import date as _d
                write_corpus_meta(
                    str(db), underlying=und, lot_size=ids["lot_size"],
                    lot_size_asof=_d.today().isoformat(),
                    eq_security_id=ids["eq_security_id"],
                    underlying_security_id=ids["underlying_security_id"])
            except Exception:
                pass
'''


# ── driver ─────────────────────────────────────────────────────────────────

def patch_tree(root: Path, staged: dict) -> None:
    gc_p = root / "backtest/gc/backtest_gc_runner.py"
    vet_p = root / "backtest/vet/backtest_vet_runner.py"
    route_p = root / "api/backtest_routes.py"

    for p in (gc_p, vet_p, route_p):
        if not p.exists():
            raise SystemExit(f"ABORT: missing {p}")

    # GC
    t = gc_p.read_text()
    if FENCE not in t:
        t = _ro(t, GC_ANCHOR_SESSION, IMPORT_BLOCK.lstrip("\n") + GC_ANCHOR_SESSION,
                "gc-import")
        t = _ro(t, GC_OLD_LADDER, _ladder("GC"), "gc-ladder")
        t = _ro(t, GC_DIAG_OLD, GC_DIAG_NEW, "gc-diag")
        staged[gc_p] = t

    # VET
    t = vet_p.read_text()
    if FENCE not in t:
        t = _ro(t, VET_ANCHOR_SESSION, IMPORT_BLOCK.lstrip("\n") + VET_ANCHOR_SESSION,
                "vet-import")
        t = _ro(t, VET_OLD_LADDER, _ladder("VET"), "vet-ladder")
        t = _ro(t, VET_DIAG_OLD, VET_DIAG_NEW, "vet-diag")
        staged[vet_p] = t

    # route
    t = route_p.read_text()
    if FENCE not in t:
        t = _ro(t, ROUTE_OLD, ROUTE_NEW, "route-stamp")
        staged[route_p] = t

    # new files — skip when byte-identical so a re-run is a true no-op
    for dst, src in ((root / "backtest/util/lot_sizes.py", RESOLVER_SRC),
                     (root / "backtest/util/test_lot_sizes.py", TEST_SRC)):
        body = src.read_text()
        if not dst.exists() or dst.read_text() != body:
            staged[dst] = body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not RESOLVER_SRC.exists() or not TEST_SRC.exists():
        raise SystemExit(f"ABORT: put lot_sizes.py and test_lot_sizes.py in "
                         f"./{RESOLVER_SRC.parent}/ next to this script")

    trees = [r for r in ROOTS if r.exists()]
    if not trees:
        raise SystemExit("ABORT: run me from the repo root (no backend/app found)")
    print(f"trees: {', '.join(str(t) for t in trees)}")

    staged: dict = {}
    for r in trees:
        patch_tree(r, staged)

    if not staged:
        print(f"already fenced ({FENCE}) in every tree — nothing to do")
        return

    # STAGED COMPILE — validate every payload before a single byte is written
    with tempfile.TemporaryDirectory() as td:
        for path, text in staged.items():
            probe = Path(td) / path.name
            probe.write_text(text)
            try:
                py_compile.compile(str(probe), doraise=True)
            except py_compile.PyCompileError as e:
                raise SystemExit(f"ABORT: staged compile failed for {path}\n{e}")
    print(f"staged compile OK ({len(staged)} files)")

    if a.dry_run:
        for p in sorted(staged):
            print(f"  would write {p}")
        return

    for path, text in staged.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{FENCE}"))
        path.write_text(text)
        print(f"  wrote {path}")

    print("\ndone. next:")
    print("  cd backend && python3 -m pyflakes app/backtest/util/lot_sizes.py "
          "app/backtest/gc/backtest_gc_runner.py "
          "app/backtest/vet/backtest_vet_runner.py app/api/backtest_routes.py")
    print("  python3 app/backtest/util/test_lot_sizes.py")
    print("  python3 -m app.backtest.util.lot_sizes --refresh")
    print("  python3 -m app.backtest.util.lot_sizes --show HDFCBANK")
    print("  python3 -m app.backtest.util.lot_sizes --gap-scan HDFCBANK")


if __name__ == "__main__":
    sys.exit(main())
