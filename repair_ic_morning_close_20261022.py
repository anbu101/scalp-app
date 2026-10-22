#!/usr/bin/env python3
# repair_ic_morning_close_20261022.py — fence IC_MORNING_REPAIR_20261022
# ============================================================================
# One-off repair for 22 Oct 2026. Kite was not logged in at 09:16, so IC_V2's
# PAPER morning close (NEXT_OPEN) found no price and booked every carried leg
# at its ENTRY price (exit = entry, P&L ₹0). This reprices those rows at the
# price the backtest and a live 09:16 market order use: the OPEN of the 09:16
# one-minute candle, fetched from Kite historical data.
#
# Only rows that match ALL of these are touched:
#   paper_trades · strategy_name = IC_V2 (or --strategy) · state CLOSED ·
#   exit_reason NEXT_OPEN · exit_price == entry_price · exited on --date
# P&L / charges are recomputed with the SAME formula close_paper_trade uses.
# exit_time is left as recorded.
#
# USAGE (repo root, Kite LOGGED IN first; use the backend's Python — the one
# with kiteconnect installed):
#     python3 repair_ic_morning_close_20261022.py            # dry run: shows the changes
#     python3 repair_ic_morning_close_20261022.py --apply    # writes them
# --apply copies app.db to app.db.bak-IC_MORNING_REPAIR_20261022 first.
# Safe to re-run: repaired rows no longer have exit == entry.
# ============================================================================

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

FENCE = "IC_MORNING_REPAIR_20261022"
IST = timezone(timedelta(hours=5, minutes=30))


def die(msg: str) -> None:
    print(f"\n[{FENCE}] ABORT: {msg}")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-22")
    ap.add_argument("--strategy", default="IC_V2")
    ap.add_argument("--time", default="09:16", help="candle whose OPEN is the exit")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir("backend/app"):
        die("run from the repo root (the folder that contains backend/)")
    sys.path.insert(0, os.path.abspath("backend"))
    try:
        from app.utils.app_paths import DB_PATH
        from app.trading.zerodha_charges_calc import calculate_option_charges
        from app.brokers.zerodha_manager import ZerodhaManager
    except ImportError as e:
        die(f"cannot import the backend ({e}). Run with the backend's Python "
            f"(the environment that has kiteconnect installed).")

    d = date.fromisoformat(a.date)
    hh, mm = (int(x) for x in a.time.split(":"))
    t0 = datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)
    day0 = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    day1 = day0 + 86400

    kite = None
    try:
        zm = ZerodhaManager()
        kite = zm.get_data_kite() or zm.get_kite()
    except Exception as e:
        die(f"Zerodha session unavailable ({e!r}) — log in to Kite first")
    if kite is None:
        die("Zerodha session unavailable — log in to Kite first")

    db = str(DB_PATH)
    c = sqlite3.connect(db, timeout=30)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        """SELECT * FROM paper_trades
           WHERE strategy_name=? AND state='CLOSED' AND exit_reason='NEXT_OPEN'
             AND exit_time >= ? AND exit_time < ?
             AND ABS(exit_price - entry_price) < 0.0001""",
        (a.strategy, day0, day1))]
    if not rows:
        print(f"[{FENCE}] nothing to repair: no {a.strategy} NEXT_OPEN rows on "
              f"{d} booked at entry (already repaired?)")
        return

    nfo = None

    def token_for(r):
        nonlocal nfo
        tok = r.get("token")
        if tok:
            return int(tok)
        if nfo is None:
            nfo = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NFO")}
        return int(nfo.get(r["symbol"]) or 0)

    plan = []
    for r in rows:
        tok = token_for(r)
        if not tok:
            die(f"no instrument token for {r['symbol']}")
        bars = kite.historical_data(tok, t0, t0 + timedelta(minutes=1), "minute") or []
        bar = None
        for b in bars:
            bd = b.get("date")
            if hasattr(bd, "astimezone"):
                bd = bd.astimezone(IST)
            if bd and bd.hour == hh and bd.minute == mm:
                bar = b
                break
        if bar is None:
            die(f"no {a.time} candle for {r['symbol']} (token {tok}) on {d}")
        px = float(bar["open"])
        entry, qty = float(r["entry_price"]), int(r["qty"])
        short = str(r.get("trade_direction") or "LONG").upper() == "SHORT"
        gross = (entry - px) * qty if short else (px - entry) * qty
        ch = calculate_option_charges(entry_price=entry, exit_price=px, qty=qty)
        plan.append((r, px, gross, ch, gross - ch.total_charges))

    print(f"\n[{FENCE}] {a.strategy} NEXT_OPEN rows on {d} — exit at the {a.time} candle OPEN\n")
    print(f"{'symbol':22} {'dir':5} {'qty':>5} {'entry':>8} {'old exit':>9} "
          f"{'new exit':>9} {'gross':>11} {'charges':>9} {'net':>11}")
    tot = 0.0
    for r, px, gross, ch, net in plan:
        tot += net
        print(f"{r['symbol']:22} {str(r.get('trade_direction'))[:5]:5} {r['qty']:>5} "
              f"{r['entry_price']:>8.2f} {r['exit_price']:>9.2f} {px:>9.2f} "
              f"{gross:>11,.2f} {ch.total_charges:>9,.2f} {net:>11,.2f}")
    print(f"\n   net P&L of the repaired legs: ₹{tot:,.2f}")

    if not a.apply:
        print("\n   DRY RUN — nothing written. Re-run with --apply to write.")
        return

    bak = f"{db}.bak-{FENCE}"
    if not os.path.exists(bak):
        c.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(db, bak)
        print(f"   backup: {bak}")
    with c:
        for r, px, gross, ch, net in plan:
            n = c.execute(
                """UPDATE paper_trades SET exit_price=?, pnl_points=?, pnl_value=?,
                       brokerage=?, stt=?, exchange_charges=?, sebi_charges=?,
                       stamp_duty=?, gst=?, total_charges=?, net_pnl=?
                   WHERE paper_trade_id=? AND state='CLOSED'
                     AND ABS(exit_price - entry_price) < 0.0001""",
                (px, gross / int(r["qty"]) if int(r["qty"]) else 0, gross,
                 ch.brokerage, ch.stt, ch.exchange_charges, ch.sebi_charges,
                 ch.stamp_duty, ch.gst, ch.total_charges, net,
                 r["paper_trade_id"])).rowcount
            if n != 1:
                die(f"update matched {n} rows for {r['paper_trade_id']} — rolled back")
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(f"[{FENCE}] repriced {len(plan)} {a.strategy} NEXT_OPEN "
                        f"row(s) on {d} at the {a.time} open — net ₹{tot:,.2f}")
    except Exception:
        pass
    print(f"\n[{FENCE}] DONE — {len(plan)} row(s) repaired. Refresh Trades / Dashboard.")


if __name__ == "__main__":
    main()
