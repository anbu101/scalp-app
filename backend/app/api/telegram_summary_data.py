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
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1

    # V3 live
    _merge_v3(out, paper=False)
    # V4 live
    _merge_v4(out, paper=False)
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
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1

    # V3 paper
    _merge_v3(out, paper=True)
    # V4 paper
    _merge_v4(out, paper=True)
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
        if net >= 0: b["wins"]   += 1
        else:        b["losses"] += 1


def _to_rows(agg: dict, mode: str) -> list[StrategyRow]:
    rows = [
        StrategyRow(name=name, trades=d["trades"], wins=d["wins"],
                    losses=d["losses"], net=d["net"], mode=mode)
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