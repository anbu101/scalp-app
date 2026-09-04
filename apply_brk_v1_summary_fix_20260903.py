#!/usr/bin/env python3
# apply_brk_v1_summary_fix_20260903.py
#
# ── BRK_V1_LIVE_20260902 · EOD-summary fix ── the daily summary (card path
# telegram_summary_data + text fallback in telegram_api) is LIVE-blind to
# strategies that store LIVE rows in the generic paper_trades table — the
# FOURTH instance of the same leak class (history route, telegram open
# positions, and the two summary paths). Two defects, both fixed here:
#
#   1. LIVE sections read only `trades` + private-table merges. FIX: a
#      generic paper_trades trade_mode='LIVE' union (BRK_V1 today, TSG_V1's
#      live legs equally — no per-strategy edit ever again for this).
#   2. PAPER sections read paper_trades with NO trade_mode filter — so
#      paper-table LIVE rows were silently counted as PAPER (latent fleet
#      bug: TSG live legs have been mis-sectioned since TSG went live).
#      FIX: AND trade_mode='PAPER'. Together with (1) every row lands in
#      exactly one section.
#
# Touches: api/telegram_summary_data.py (_live_rows, _paper_rows) and
# api/telegram_api.py (_query_today_live_summary, _query_today_paper_summary).
# Dual-tree, assert-anchored, staged py_compile, .bak-FENCE, idempotent.
#     python3 apply_brk_v1_summary_fix_20260903.py --check
#     python3 apply_brk_v1_summary_fix_20260903.py

from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

FENCE = "BRK_V1_LIVE_20260902"
TREES = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
DATA = "api/telegram_summary_data.py"
TELE = "api/telegram_api.py"

# ── card: _live_rows gains the generic paper-table LIVE union ──
D_LIVE_OLD = """    for strat, entry, exit_, qty, direction in rows:"""
D_LIVE_NEW = """    # ── paper-table LIVE union (BRK_V1 fence) ── strategies whose LIVE
    # rows live in generic paper_trades (trade_mode='LIVE'): net_pnl is
    # already charges-net there, so it is used directly.
    try:
        prows = conn.execute(
            \"\"\"
            SELECT strategy_name, net_pnl
            FROM paper_trades
            WHERE trade_mode = 'LIVE'
              AND state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl IS NOT NULL
              AND COALESCE(exit_time, entry_time) >= ?
            \"\"\",
            (midnight,),
        ).fetchall()
        for strat, net in prows:
            net = float(net)
            b = out.setdefault(strat, {"trades": 0, "wins": 0,
                                       "losses": 0, "net": 0.0})
            b["trades"] += 1
            b["net"]    += net
            b["gross"]  = b.get("gross", 0.0) + net   # gross unknown; net is charges-net already
            if net >= 0: b["wins"]   += 1
            else:        b["losses"] += 1
    except Exception as e:
        write_audit_log(f"[CARD][LIVE] paper-table LIVE union failed: {e}")

    for strat, entry, exit_, qty, direction in rows:"""

# ── card: _paper_rows excludes LIVE rows ──
D_PAPER_OLD = """            SELECT strategy_name, net_pnl
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl IS NOT NULL
              AND COALESCE(exit_time, entry_time) >= ?
            \"\"\",
            (midnight,),
        ).fetchall()
    except Exception as e:
        write_audit_log(f"[CARD][PAPER] read failed: {e}")"""
D_PAPER_NEW = """            SELECT strategy_name, net_pnl
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND COALESCE(trade_mode, 'PAPER') = 'PAPER'  -- ── BRK_V1 fence ── LIVE rows belong to the LIVE section
              AND exit_price IS NOT NULL
              AND net_pnl IS NOT NULL
              AND COALESCE(exit_time, entry_time) >= ?
            \"\"\",
            (midnight,),
        ).fetchall()
    except Exception as e:
        write_audit_log(f"[CARD][PAPER] read failed: {e}")"""

# ── text fallback: live query union ──
T_LIVE_OLD = """        by_strategy: dict = {}
        total_pnl = 0.0
        wins = losses = 0
        for row in rows:
            strategy_id, entry_price, exit_price, qty = row"""
T_LIVE_NEW = """        # ── paper-table LIVE union (BRK_V1 fence) ──
        try:
            prow = conn.execute(
                \"\"\"
                SELECT strategy_name, entry_price, exit_price, qty
                FROM paper_trades
                WHERE trade_mode = 'LIVE'
                  AND state = 'CLOSED'
                  AND exit_time  IS NOT NULL
                  AND exit_price IS NOT NULL
                  AND entry_time >= ?
                \"\"\",
                (midnight,),
            ).fetchall()
            rows = list(rows) + [tuple(r) for r in prow]
        except Exception as e:
            print(f"[TELEGRAM] paper-table LIVE union failed: {e}")

        by_strategy: dict = {}
        total_pnl = 0.0
        wins = losses = 0
        for row in rows:
            strategy_id, entry_price, exit_price, qty = row"""

# ── text fallback: paper query mode filter ──
T_PAPER_OLD = """            SELECT strategy_name, pnl_value
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?"""
T_PAPER_NEW = """            SELECT strategy_name, pnl_value
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND COALESCE(trade_mode, 'PAPER') = 'PAPER'  -- ── BRK_V1 fence ──
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?"""


class Abort(Exception):
    pass


def rep(t, old, new, what):
    n = t.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor x{n}, expected 1 — file drifted")
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--allow-missing-tree", action="store_true")
    a = ap.parse_args()
    present = [t for t in TREES if t.exists()]
    missing = [t for t in TREES if not t.exists()]
    if missing and not a.allow_missing_tree:
        print(f"ABORTED: dual-tree not satisfiable, absent: "
              f"{[str(m) for m in missing]}", file=sys.stderr)
        return 1
    staged, skipped = {}, []
    try:
        for tree in present:
            p = tree / DATA
            t = p.read_text()
            if "paper-table LIVE union" in t:
                skipped.append(p)
            else:
                t = rep(t, D_LIVE_OLD, D_LIVE_NEW, f"{p}:live union")
                t = rep(t, D_PAPER_OLD, D_PAPER_NEW, f"{p}:paper filter")
                stage_py(p, t)
                staged[p] = t
            p = tree / TELE
            t = p.read_text()
            if "paper-table LIVE union" in t:
                skipped.append(p)
            else:
                t = rep(t, T_LIVE_OLD, T_LIVE_NEW, f"{p}:text live union")
                t = rep(t, T_PAPER_OLD, T_PAPER_NEW, f"{p}:text paper filter")
                stage_py(p, t)
                staged[p] = t
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1
    for p in skipped:
        print(f"  already present — skipped   {p}")
    for p, t in staged.items():
        if a.check:
            print(f"  would patch (clean)         {p}")
        else:
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}-summary"))
            p.write_text(t)
            print(f"  patched                     {p}")
    for t in missing:
        print(f"  SKIPPED (tree absent)       {t}")
    print(f"\n{FENCE} summary fix "
          f"{'check complete' if a.check else 'applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
