#!/usr/bin/env python3
# repair_fill_truth_20261102.py — ── FILL_TRUTH_REPAIR_20261102 ──
#
# One-off DATA repair for Monday 2026-11-02 (IC_V2 "Icarus", LIVE, 1 lot):
# L1·ADJ  NIFTY26N0323350CE  BUY 65 @ 80.15 (09:46:29)  →  SELL 65 @ 55.85
# (11:48:07, its SL GTT). The app booked the exit at 80.15 (its own entry,
# the bug fixed by FILL_TRUTH_20261102), so the day showed −₹3,545.75 gross
# instead of Kite's −₹5,125.25.
#
# What it does (DRY RUN unless --apply):
#   1. finds exactly ONE trades row: IC_V2 · that symbol · exited on
#      2026-11-02 IST · exit 80.15 — else stops and lists what it saw
#   2. finds any PAPER_LIVE shadow twin of it in paper_trades (same symbol,
#      entry 80.15, exit 80.15, exited within 5 min of the live row)
#   3. --apply: copies app.db to ~/.scalp-app/patch_backups/FILL_TRUTH_REPAIR_<ts>/,
#      sets the live exit to 55.85, re-prices the twin's P&L and charges with
#      the app's own charges calculator, then re-runs and stores the day's
#      parity for IC_V2 so the Telegram/Parity views pick up the truth.
# Idempotent: a second run finds the row already at 55.85 and says so.
# Safe with the app open (WAL + busy timeout), but run it after market hours.
#
#   python3 repair_fill_truth_20261102.py                 # look only
#   python3 repair_fill_truth_20261102.py --apply         # write
#   (--repo /path/to/scalp-app if not /Users/anbu/dev/scalp-app)

import argparse
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FENCE = "FILL_TRUTH_REPAIR_20261102"
SID = "IC_V2"
SYMBOL = "NIFTY26N0323350CE"
DAY = date(2026, 11, 2)
WRONG, RIGHT = 80.15, 55.85
IST = timezone(timedelta(hours=5, minutes=30))
EXPECTED_DAY_GROSS = -5125.25


def day_bounds(d):
    a = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return a, a + 86400


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--repo", default="/Users/anbu/dev/scalp-app")
    a = ap.parse_args()

    backend = Path(a.repo).expanduser() / "backend"
    if not (backend / "app" / "db" / "sqlite.py").exists():
        sys.exit(f"[{FENCE}] backend not found under {backend} — pass --repo")
    sys.path.insert(0, str(backend))
    from app.utils.app_paths import DATA_DIR
    db = Path(DATA_DIR) / "app.db"
    if not db.exists():
        sys.exit(f"[{FENCE}] no app.db at {db}")
    print(f"[{FENCE}] app.db = {db}")

    lo, hi = day_bounds(DAY)
    c = sqlite3.connect(str(db), timeout=30)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT trade_id, slot, symbol, qty, entry_price, exit_price, exit_reason, "
                     "exit_time, trade_direction, exit_order_id FROM trades "
                     "WHERE strategy_id=? AND symbol=? AND exit_time>=? AND exit_time<?",
                     (SID, SYMBOL, lo, hi)).fetchall()
    for r in rows:
        print(f"  live row {r['trade_id'][:12]} {r['slot']} {r['trade_direction']} qty={r['qty']} "
              f"entry={r['entry_price']} exit={r['exit_price']} reason={r['exit_reason']}")
    wrong = [r for r in rows if r["exit_price"] is not None and abs(r["exit_price"] - WRONG) < 1e-6
             and abs(r["entry_price"] - WRONG) < 1e-6 and (r["trade_direction"] or "LONG") == "LONG"]
    done = [r for r in rows if r["exit_price"] is not None and abs(r["exit_price"] - RIGHT) < 1e-6]
    if not wrong and done:
        print(f"[{FENCE}] already repaired (exit {RIGHT}). Nothing to do.")
        return
    if len(wrong) != 1:
        sys.exit(f"[{FENCE}] expected exactly one LONG row exited at {WRONG}; found {len(wrong)} — stopping.")
    live = wrong[0]
    qty = int(live["qty"])
    print(f"  → live exit {WRONG} → {RIGHT}: leg P&L ₹0.00 → ₹{(RIGHT - WRONG) * qty:,.2f}")

    twins = c.execute("SELECT paper_trade_id, qty, entry_price, exit_price, exit_time, trade_direction "
                      "FROM paper_trades WHERE strategy_name=? AND symbol=? AND state='CLOSED' "
                      "AND ABS(entry_price-?)<1e-6 AND ABS(exit_price-?)<1e-6 AND ABS(exit_time-?)<=300",
                      (SID, SYMBOL, WRONG, WRONG, int(live["exit_time"]))).fetchall()
    for t in twins:
        print(f"  shadow twin {t['paper_trade_id'][:12]} qty={t['qty']} exit {WRONG} → {RIGHT}")
    if not twins:
        print("  no shadow twin carries the bad exit")

    if not a.apply:
        print(f"[{FENCE}] DRY RUN — nothing written. Re-run with --apply.")
        return

    bdir = Path.home() / ".scalp-app" / "patch_backups" / f"FILL_TRUTH_REPAIR_{int(time.time())}"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(str(bdir / "app.db"))
    c.backup(dst)
    dst.close()
    print(f"  backup → {bdir / 'app.db'}")

    from app.trading.zerodha_charges_calc import calculate_option_charges
    # The trades table refuses any update to a closed row (trigger
    # prevent_double_close — the right guard for the engine). The repair lifts
    # it for this ONE statement and restores the identical trigger inside the
    # same transaction: either everything lands, or nothing does.
    trig = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                     "AND name='prevent_double_close'").fetchone()
    c.isolation_level = None
    c.execute("BEGIN IMMEDIATE")
    try:
        if trig:
            c.execute("DROP TRIGGER prevent_double_close")
        n = c.execute("UPDATE trades SET exit_price=? WHERE trade_id=? AND ABS(exit_price-?)<1e-6",
                      (RIGHT, live["trade_id"], WRONG)).rowcount
        if trig:
            c.execute(trig[0])
        assert n == 1, n
        for t in twins:
            q = int(t["qty"])
            gross = (RIGHT - float(t["entry_price"])) * q          # twins of a LONG leg
            ch = calculate_option_charges(entry_price=float(t["entry_price"]), exit_price=RIGHT, qty=q)
            c.execute("UPDATE paper_trades SET exit_price=?, pnl_points=?, pnl_value=?, brokerage=?, stt=?, "
                      "exchange_charges=?, sebi_charges=?, stamp_duty=?, gst=?, total_charges=?, net_pnl=? "
                      "WHERE paper_trade_id=?",
                      (RIGHT, gross / q if q else 0, gross, ch.brokerage, ch.stt, ch.exchange_charges,
                       ch.sebi_charges, ch.stamp_duty, ch.gst, ch.total_charges, gross - ch.total_charges,
                       t["paper_trade_id"]))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    still = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                      "AND name='prevent_double_close'").fetchone()
    assert (still and trig and still[0] == trig[0]) or (not trig and not still), "trigger not restored!"
    print(f"  written: 1 live row, {len(twins)} twin(s); double-close guard restored")

    # verify the day against Kite
    g = 0.0
    for r in c.execute("SELECT qty, entry_price, exit_price, trade_direction FROM trades "
                       "WHERE strategy_id=? AND exit_time>=? AND exit_time<? AND exit_price IS NOT NULL",
                       (SID, lo, hi)):
        q, e, x, d = int(r[0]), float(r[1]), float(r[2]), (r[3] or "LONG")
        g += (e - x) * q if d == "SHORT" else (x - e) * q
    ok = abs(g - EXPECTED_DAY_GROSS) < 0.01
    print(f"  IC_V2 LIVE gross on {DAY}: ₹{g:,.2f}  (Kite ₹{EXPECTED_DAY_GROSS:,.2f}) "
          f"{'✓ matches' if ok else '— check: other rows differ'}")
    c.close()

    try:
        from app.parity import parity_core as PC
        for book in PC.books_for(DAY, SID):
            res = PC.run_day(SID, DAY, book)
            res.setdefault("strategy_id", SID); res.setdefault("day", DAY.isoformat()); res.setdefault("book", book)
            if not res.get("ok"):          # never overwrite a stored result with an error
                print(f"  parity {book} NOT re-stored ({str(res.get('error'))[:80]}) — re-run 02 Nov from Parity")
                continue
            PC.store(res, "repair")
            print(f"  parity re-stored: {PC.line_for(res)}")
    except Exception as e:
        print(f"  parity not re-run ({type(e).__name__}: {e}) — open Parity in the app and run 02 Nov again")

    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(f"[{FENCE}] {SID} {SYMBOL} exit {WRONG}→{RIGHT} trade_id={live['trade_id']} "
                        f"twins={len(twins)} backup={bdir}")
    except Exception:
        pass
    print(f"[{FENCE}] done. Restart the app (or reload the Dashboard) to see the corrected figures.")


if __name__ == "__main__":
    main()
