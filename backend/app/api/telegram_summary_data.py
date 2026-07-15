"""
EOD CARD DATA SOURCE — assembles CardData from the live repos.

app/api/telegram_summary_data.py   (proposed location)

Builds the CardData the renderer consumes. All P&L is NET:

  LIVE  (`trades`)        — per-row direction-aware gross, then
                            calculate_option_charges() per row for net.
  PAPER (`paper_trades`)  — net_pnl read straight from DB (already
                            direction-correct + charge-deducted).
  V3    (`scalp_v3_trades`, both modes) — gross realized_pnl minus
                            per-row LONG charges (V3 stores no net).
  V4    (`scalp_v4_trades`, both modes) — same as V3 (buy-hedge clone with
                            one extra entry gate; its own table, LONG charges).
  V5    (`scalpv5_trades`, both modes) — single-instrument LONG (option
                            BUYING). Gross from entry/exit/qty minus per-row
                            LONG charges (own table, no hedge_* columns).

Win = net >= 0 everywhere (consistent with _send_advanced_paper_summary).

Self-contained: only READS. Touches no existing function. Each strategy's
assembly is wrapped so one strategy failing to read cannot blank the card —
it just omits that row and logs.
"""

from __future__ import annotations

import time
from datetime import datetime

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.trading.zerodha_charges_calc import calculate_option_charges, LONG, SHORT
from app.db.scalp_v3_repo import get_closed_v3_trades_today_with_prices
from app.db.scalp_v4_repo import get_closed_v4_trades_today_with_prices
from app.db.scalpv5_repo import get_closed_v5_trades_today_with_prices

# Renderer dataclasses
from app.api.telegram_summary_card import CardData, StrategyRow


def _today_midnight_ts() -> int:
    t = datetime.now()
    return int(datetime(t.year, t.month, t.day, 0, 0, 0).timestamp())


# ────────────────────────────────────────────────────────────────────
#  LIVE  — `trades` table, per-row net via charges
# ────────────────────────────────────────────────────────────────────

def _live_rows() -> list[StrategyRow]:
    midnight = _today_midnight_ts()
    out: dict[str, dict] = {}
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT strategy_id, entry_price, exit_price, qty,
                   COALESCE(trade_direction, 'LONG') AS trade_direction
            FROM trades
            WHERE state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()
    except Exception as e:
        write_audit_log(f"[CARD][LIVE] read failed: {e}")
        return []

    for strat, entry, exit_, qty, direction in rows:
        try:
            entry = float(entry); exit_ = float(exit_); qty = int(qty)
            if direction == "SHORT":
                gross = (entry - exit_) * qty
            else:
                gross = (exit_ - entry) * qty
            ch = calculate_option_charges(
                entry_price=entry, exit_price=exit_, qty=qty,
                direction=SHORT if direction == "SHORT" else LONG,
            )
            net = gross - ch.total_charges
        except Exception as e:
            write_audit_log(f"[CARD][LIVE] row charge calc failed strat={strat}: {e}")
            continue

        b = out.setdefault(strat, {"trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        b["trades"] += 1
        b["net"]    += net
        b["gross"]  = b.get("gross", 0.0) + gross   # ── GROSS_RECON ──
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1

    # V3 live
    _merge_v3(out, paper=False)
    # V4 live
    _merge_v4(out, paper=False)
    # V5 live
    _merge_v5(out, paper=False)
    _merge_pst(out, paper=False)   # ── PST ──
    return _to_rows(out, "LIVE")


# ────────────────────────────────────────────────────────────────────
#  PAPER — `paper_trades` table, net_pnl read directly
# ────────────────────────────────────────────────────────────────────

def _paper_rows() -> list[StrategyRow]:
    midnight = _today_midnight_ts()
    out: dict[str, dict] = {}
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT strategy_name, net_pnl
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl IS NOT NULL
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()
    except Exception as e:
        write_audit_log(f"[CARD][PAPER] read failed: {e}")
        return []

    for strat, net_pnl in rows:
        net = float(net_pnl or 0)
        b = out.setdefault(strat, {"trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        b["trades"] += 1
        b["net"]    += net
        # (no gross here — paper rows arrive pre-aggregated as (strat, net_pnl);
        #  the caption reconciliation is LIVE-only. A blind auto-insert of the
        #  gross accumulator here crashed the CARD at 15:30 on 2026-07-15 —
        #  "name 'gross' is not defined" — text fallback saved the summary.)
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1

    # V3 paper
    _merge_v3(out, paper=True)
    # V4 paper
    _merge_v4(out, paper=True)
    # V5 paper
    _merge_v5(out, paper=True)
    _merge_pst(out, paper=True)    # ── PST ──
    return _to_rows(out, "PAPER")


# ────────────────────────────────────────────────────────────────────
#  V3 — gross realized_pnl minus per-row LONG charges
# ────────────────────────────────────────────────────────────────────

def _merge_v3(out: dict, *, paper: bool):
    try:
        rows = get_closed_v3_trades_today_with_prices(paper=paper)
    except Exception as e:
        write_audit_log(f"[CARD][V3] read failed paper={int(paper)}: {e}")
        return

    for r in rows:
        try:
            entry = float(r["hedge_entry_price"])
            exit_ = float(r["exit_price"])
            qty   = int(r["hedge_qty"])
            gross = (exit_ - entry) * qty  # V3 is LONG
            ch = calculate_option_charges(
                entry_price=entry, exit_price=exit_, qty=qty, direction=LONG,
            )
            net = gross - ch.total_charges
        except Exception as e:
            write_audit_log(f"[CARD][V3] row charge calc failed: {e}")
            continue

        b = out.setdefault("SCALP_V3", {"trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        b["trades"] += 1
        b["net"]    += net
        b["gross"]  = b.get("gross", 0.0) + gross   # ── GROSS_RECON ──
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1


# ────────────────────────────────────────────────────────────────────
#  V4 — gross realized_pnl minus per-row LONG charges
#       (buy-hedge clone of V3 with one extra entry gate; own table)
# ────────────────────────────────────────────────────────────────────

def _merge_v4(out: dict, *, paper: bool):
    try:
        rows = get_closed_v4_trades_today_with_prices(paper=paper)
    except Exception as e:
        write_audit_log(f"[CARD][V4] read failed paper={int(paper)}: {e}")
        return

    for r in rows:
        try:
            entry = float(r["hedge_entry_price"])
            exit_ = float(r["exit_price"])
            qty   = int(r["hedge_qty"])
            gross = (exit_ - entry) * qty  # V4 is LONG
            ch = calculate_option_charges(
                entry_price=entry, exit_price=exit_, qty=qty, direction=LONG,
            )
            net = gross - ch.total_charges
        except Exception as e:
            write_audit_log(f"[CARD][V4] row charge calc failed: {e}")
            continue

        b = out.setdefault("SCALP_V4", {"trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        b["trades"] += 1
        b["net"]    += net
        b["gross"]  = b.get("gross", 0.0) + gross   # ── GROSS_RECON ──
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1


# ────────────────────────────────────────────────────────────────────
#  V5 — single-instrument LONG (option BUYING). Gross from entry/exit/qty
#       minus per-row LONG charges. Own table (scalpv5_trades), no hedge_*.
#       Kept on the NET card for consistency with V3/V4 (the V5 repo stores
#       only gross realized_pnl, so net is recomputed here from raw prices).
# ────────────────────────────────────────────────────────────────────

def _merge_v5(out: dict, *, paper: bool):
    try:
        rows = get_closed_v5_trades_today_with_prices(paper=paper)
    except Exception as e:
        write_audit_log(f"[CARD][V5] read failed paper={int(paper)}: {e}")
        return

    for r in rows:
        try:
            entry = float(r["entry_price"])
            exit_ = float(r["exit_price"])
            qty   = int(r["qty"])
            gross = (exit_ - entry) * qty          # V5 is LONG, single-instrument
            ch = calculate_option_charges(
                entry_price=entry, exit_price=exit_, qty=qty, direction=LONG,
            )
            net = gross - ch.total_charges
        except Exception as e:
            write_audit_log(f"[CARD][V5] row charge calc failed: {e}")
            continue

        b = out.setdefault("SCALP_V5", {"trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        b["trades"] += 1
        b["net"]    += net
        b["gross"]  = b.get("gross", 0.0) + gross   # ── GROSS_RECON ──
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1


# ── PST_SELL / PST_HEDGE (own tables, both modes) ── rows carry
# AUTHORITATIVE pnl (gross) + net_pnl from the backtest's charges_model —
# passed through, never recomputed. STALE restart-hygiene rows carry no
# P&L and are excluded (net_pnl IS NULL).
def _merge_pst(out: dict, *, paper: bool):
    midnight = _today_midnight_ts()
    mode = "PAPER" if paper else "LIVE"
    try:
        conn = get_conn()
        for sid, table in (("PST_SELL", "pst_sell_trades"),
                           ("PST_HEDGE", "pst_hedge_trades")):
            try:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone()
                if not exists:
                    continue
                rows = conn.execute(
                    f"""SELECT pnl, net_pnl FROM {table}
                        WHERE mode = ? AND status = 'CLOSED'
                          AND net_pnl IS NOT NULL AND entry_ts >= ?""",
                    (mode, midnight)).fetchall()
                for r in rows:
                    net = float(r["net_pnl"])
                    b = out.setdefault(sid, {"trades": 0, "wins": 0,
                                             "losses": 0, "net": 0.0})
                    b["trades"] += 1
                    b["net"]    += net
                    b["gross"]  = b.get("gross", 0.0) + float(r["pnl"] or 0.0)
                    if net >= 0: b["wins"]   += 1
                    else:        b["losses"] += 1
            except Exception as e:
                write_audit_log(f"[CARD][{sid}] read failed paper={int(paper)}: {e}")
    except Exception as e:
        write_audit_log(f"[CARD][PST] conn failed: {e}")


def _to_rows(agg: dict, mode: str) -> list[StrategyRow]:
    rows = [
        StrategyRow(name=name, trades=d["trades"], wins=d["wins"],
                    losses=d["losses"], net=d["net"], mode=mode,
                    gross=d.get("gross", 0.0))
        for name, d in agg.items()
    ]
    # stable, readable order: biggest absolute mover first within the table
    rows.sort(key=lambda r: abs(r.net), reverse=True)
    return rows


# ────────────────────────────────────────────────────────────────────
#  PUBLIC
# ────────────────────────────────────────────────────────────────────

def build_card_data() -> CardData:
    return CardData(
        date_str=datetime.now().strftime("%d %b %Y"),
        live_rows=_live_rows(),
        paper_rows=_paper_rows(),
    )