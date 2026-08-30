#!/usr/bin/env python3
# apply_vet_v1_display_unions_20260829.py
#
# ── VET_V1 LIVE WIRING, PART 3 ── the display unions (checklist "unions in
# N files" cost of a private table)
# ============================================================================
# vet_trades rows must surface everywhere tma2_trades rows do, or trades
# exist but nobody — including the friends' UIs — can see them:
#
#   paper_trades_routes.py    → PaperTrades page (open/closed lists)
#   telegram_summary_data.py  → daily Telegram card (per-GROUP aggregation:
#                               a short+wing pair is ONE trade, not two)
#   trade_history_routes.py   → Analytics history (LIVE rows, per-leg with
#                               group_id so the page can pair legs)
#
# REUSE VIA WIDENING, NOT FORKING. The three TMA mappers are one column and
# one string away from table-generic:
#   * _load_tma_paper selects sl/tp explicitly — vet_trades has neither (no
#     GTT layer by design), so the SELECT becomes SELECT * and the mapper's
#     .get() calls return None for absent keys. TMA rows see identical
#     values (they always selected a superset via named columns).
#   * both direction checks read "SELL" only; vet_trades stores LONG/SHORT.
#     Widened to ("SELL", "SHORT") — strictly additive, TMA rows unaffected.
# Display-only files; no live-money path is touched.
#
# Idempotent, assert-anchored, staged compile, dual-tree aware.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_display_unions_20260829.py --dry-run
#   python3 apply_vet_v1_display_unions_20260829.py

import argparse
import os
import py_compile
import shutil
import sys
import tempfile

REPO = os.getcwd()
BE_TREES = [(os.path.join(REPO, "backend"), "backend"),
            (os.path.join(REPO, "desktop", "src-tauri", "backend"),
             "desktop-be")]

PPR = os.path.join("app", "api", "paper_trades_routes.py")
TSD = os.path.join("app", "api", "telegram_summary_data.py")
THR = os.path.join("app", "api", "trade_history_routes.py")


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:90]}")


# ── paper_trades_routes.py ──────────────────────────────────────────────
P_SEL_OLD = '''        SELECT id, group_id, direction, tradingsymbol, instrument_type, qty,
               entry_ts, entry_price, sl, tp, exit_ts, exit_price,
               exit_reason, pnl, charges, net_pnl, status
        FROM {table}'''
P_SEL_NEW = '''        SELECT *
        FROM {table}'''
# widened mapper note replaces the docstring's last line
P_DIR_OLD = '\n        is_sell = (row.get("direction") == "SELL")'
P_DIR_NEW = ('\n        # SELL = tma legs · SHORT = vet_trades main leg '
             '(widened 2026-08-29)\n'
             '        is_sell = (row.get("direction") in ("SELL", "SHORT"))')
P_CALL_OLD = "    # TMA2_PAPER END"
P_CALL_NEW = '''    # TMA2_PAPER END

    # VET_PAPER BEGIN — vet_trades PAPER rows through the SAME widened
    # mapper: SELECT * makes the absent sl/tp read None (VET has no GTT
    # layer by design), and the direction check accepts SHORT.
    try:
        _vo, _vc = _load_tma_paper(conn, table="vet_trades",
                                   strategy_name="VET_V1")
        open_trades.extend(_vo)
        closed_trades.extend(_vc)
    except Exception as e:
        write_audit_log(f"[API][PAPER_TRADES][VET_V1][SKIP] {repr(e)}")
    # VET_PAPER END'''


def edit_ppr(t):
    if "VET_PAPER BEGIN" in t:
        return t, 0
    one(t, P_SEL_OLD, "paper:named SELECT")
    one(t, P_DIR_OLD, "paper:direction check")
    one(t, P_CALL_OLD, "paper:TMA2 call site")
    t = t.replace(P_SEL_OLD, P_SEL_NEW, 1)
    t = t.replace(P_DIR_OLD, P_DIR_NEW, 1)
    t = t.replace(P_CALL_OLD, P_CALL_NEW, 1)
    return t, 3


# ── telegram_summary_data.py ── group-aggregated, column-compatible as-is
T_LIVE_OLD = ('    _merge_tma(out, paper=False, table="tma2_trades", '
              'sid="TMA_V2")   # ── TMA_V2 ──')
T_LIVE_NEW = (T_LIVE_OLD + '\n    _merge_tma(out, paper=False, '
              'table="vet_trades", sid="VET_V1")    # ── VET_V1 ──')
T_PAP_OLD = ('    _merge_tma(out, paper=True, table="tma2_trades", '
             'sid="TMA_V2")    # ── TMA_V2 ──')
T_PAP_NEW = (T_PAP_OLD + '\n    _merge_tma(out, paper=True, '
             'table="vet_trades", sid="VET_V1")     # ── VET_V1 ──')


def edit_tsd(t):
    if '"vet_trades"' in t:
        return t, 0
    one(t, T_LIVE_OLD, "telegram:live merge")
    one(t, T_PAP_OLD, "telegram:paper merge")
    t = t.replace(T_LIVE_OLD, T_LIVE_NEW, 1)
    t = t.replace(T_PAP_OLD, T_PAP_NEW, 1)
    return t, 2


# ── trade_history_routes.py ─────────────────────────────────────────────
H_DIR_OLD = '        direction = "SHORT" if d.get("direction") == "SELL" else "LONG"'
H_DIR_NEW = ('        # SELL = tma legs · vet_trades already stores '
             'LONG/SHORT (2026-08-29)\n'
             '        direction = ("SHORT" if d.get("direction") in '
             '("SELL", "SHORT") else "LONG")')
H_CALL_OLD = "    # TMA2_HISTORY END"
H_CALL_NEW = '''    # TMA2_HISTORY END

    # VET_HISTORY BEGIN — same shape, own private table; per-leg rows with
    # group_id so Analytics can pair a short with its wing.
    if (not strategy_id) or strategy_id == "all" or strategy_id == "VET_V1":
        try:
            result.extend(_query_tma_live(from_ts, to_ts,
                                          table="vet_trades",
                                          strategy_id="VET_V1"))
        except Exception:
            pass
    # VET_HISTORY END'''


def edit_thr(t):
    if "VET_HISTORY BEGIN" in t:
        return t, 0
    one(t, H_DIR_OLD, "history:direction check")
    one(t, H_CALL_OLD, "history:TMA2 call site")
    t = t.replace(H_DIR_OLD, H_DIR_NEW, 1)
    t = t.replace(H_CALL_OLD, H_CALL_NEW, 1)
    return t, 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in BE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped (rsync target)")
            continue
        for rel, fn in ((PPR, edit_ppr), (TSD, edit_tsd), (THR, edit_thr)):
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")
    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── STAGED COMPILE ───────────────────────────────────────────")
    tmp = tempfile.mkdtemp(prefix="vet_du_")
    try:
        for i, (dest, body) in enumerate(writes.items()):
            stage = os.path.join(tmp, f"s{i}.py")
            open(stage, "w").write(body)
            try:
                py_compile.compile(stage, doraise=True)
            except py_compile.PyCompileError as e:
                die(f"compile FAILED for {dest}:\n{e}")
        print(f"  {len(writes)} file(s) compile clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. vet_trades now surfaces on PaperTrades, the Telegram "
          "daily card and Analytics history.")


if __name__ == "__main__":
    main()
