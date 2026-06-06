import threading
import time
from datetime import datetime, timedelta, timezone

from app.api.telegram_api import (
    notify_daily_summary,
    send_telegram_message,
)
from app.db.trades_repo import get_total_pnl_for_strategy
from app.db.sqlite import get_conn
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.api.telegram_api import TELEGRAM_CONFIG
from app.utils.market_hours import is_market_open
from app.api.telegram_api import notify_system_alert
# SCALP_V3 lives in its OWN table (scalp_v3_trades), not `trades`/`paper_trades`,
# so it cannot be summed via get_total_pnl_for_strategy or read by the paper
# summary's paper_trades query. Pull V3 totals/rows from its own repo.
from app.db.scalp_v3_repo import (
    get_total_pnl_v3,
    get_closed_paper_v3_trades_today,
)

IST = timezone(timedelta(hours=5, minutes=30))


class TelegramScheduler:

    CHECK_INTERVAL_SEC = 30

    def __init__(self):
        self._thread = None
        self._running = False
        self._last_summary_date = None
        self._last_position_minute = None

    # ==========================================================
    # START
    # ==========================================================

    def start(self):
        if self._running:
            return

        self._running = True

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="TelegramScheduler",
        )
        self._thread.start()

        write_audit_log("[TELEGRAM] Scheduler started")

    # ==========================================================
    # LOOP
    # ==========================================================

    def _run_loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                write_audit_log(f"[TELEGRAM][ERROR] Scheduler crash: {e}")

            time.sleep(self.CHECK_INTERVAL_SEC)

    def _tick(self):
        if not TELEGRAM_CONFIG:
            return

        now = datetime.now(IST)

        self._handle_daily_summary(now)
        self._handle_position_update(now)
        self._handle_heartbeat(now)

    # ==========================================================
    # DAILY SUMMARY
    # ==========================================================

    def _handle_daily_summary(self, now: datetime):

        levels = TELEGRAM_CONFIG.get("notification_levels", {})
        if not levels.get("dailySummary", False):
            return

        if now.hour == 15 and now.minute == 30:

            today_str = now.date().isoformat()

            if self._last_summary_date == today_str:
                return

            # LIVE TOTAL
            live_total_pnl = (
                get_total_pnl_for_strategy("SCALP_V1") +
                get_total_pnl_for_strategy("BB_V1") +
                get_total_pnl_for_strategy("SCALP_V2") +
                get_total_pnl_v3(paper=False)          # SCALP_V3 LIVE realized P&L
            )

            notify_daily_summary({
                "total_pnl": live_total_pnl
            })

            # PAPER ADVANCED SUMMARY
            self._send_advanced_paper_summary()

            # SCALP_V3 PAPER SUMMARY (own table — separate from paper_trades)
            self._send_v3_paper_summary()

            self._last_summary_date = today_str

            write_audit_log("[TELEGRAM] Daily summary sent")

    # ==========================================================
    # MANUAL DAILY SUMMARY TRIGGER (DEBUG)
    # ==========================================================

    def run_daily_summary_now(self):
        write_audit_log("[TELEGRAM][DEBUG] Manual daily summary trigger")

        live_total_pnl = (
            get_total_pnl_for_strategy("SCALP_V1") +
            get_total_pnl_for_strategy("BB_V1") +
            get_total_pnl_for_strategy("SCALP_V2") +
            get_total_pnl_v3(paper=False)          # SCALP_V3 LIVE realized P&L
        )

        notify_daily_summary({
            "total_pnl": live_total_pnl
        })

        self._send_advanced_paper_summary()

        # SCALP_V3 PAPER SUMMARY (own table — separate from paper_trades)
        self._send_v3_paper_summary()

        write_audit_log("[TELEGRAM][DEBUG] Manual daily summary completed")

    # ==========================================================
    # PAPER ADVANCED SUMMARY
    #
    # FIX (direction-aware + charges):
    #   OLD: computed pnl = (exit_price - entry_price) * qty
    #        → always LONG formula → SHORT trades show inverted best/worst
    #        → no charges included
    #
    #   NEW: reads net_pnl, total_charges, trade_direction directly from DB.
    #        close_paper_trade() already stores the correct signed net_pnl
    #        for both LONG and SHORT trades, including all Zerodha charges.
    #        No recalculation needed here.
    #
    #   NOTE: this reads the `paper_trades` table only. SCALP_V3 paper trades
    #   live in `scalp_v3_trades` and are summarised separately by
    #   _send_v3_paper_summary().
    # ==========================================================

    def _send_advanced_paper_summary(self):

        conn = get_conn()

        # FIX: select net_pnl, total_charges, trade_direction from DB.
        # net_pnl is already direction-correct and charge-deducted.
        rows = conn.execute(
            """
            SELECT
                strategy_name,
                symbol,
                net_pnl,
                total_charges,
                trade_direction,
                exit_reason
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """
        ).fetchall()

        if not rows:
            return

        summary = {}

        for strategy_name, symbol, net_pnl, total_charges, trade_direction, exit_reason in rows:

            net_pnl      = float(net_pnl      or 0)
            total_charges = float(total_charges or 0)

            if strategy_name not in summary:
                summary[strategy_name] = {
                    "total":      0,
                    "wins":       0,
                    "losses":     0,
                    "total_net":  0,   # sum of net_pnl (charges already deducted)
                    "total_charges": 0,
                    "best_net":   None,
                    "worst_net":  None,
                    "win_sum":    0,
                    "loss_sum":   0,
                    "ce_count":   0,
                    "pe_count":   0,
                    "ce_net":     0,
                    "pe_net":     0,
                    "tp_hits":    0,
                    "sl_hits":    0,
                    "manual":     0,
                    "short_count": 0,
                }

            s = summary[strategy_name]

            s["total"]        += 1
            s["total_net"]    += net_pnl
            s["total_charges"] += total_charges

            if net_pnl >= 0:
                s["wins"]     += 1
                s["win_sum"]  += net_pnl
            else:
                s["losses"]   += 1
                s["loss_sum"] += net_pnl

            # FIX: best/worst now use net_pnl (direction-correct, post-charges)
            s["best_net"]  = net_pnl if s["best_net"]  is None else max(s["best_net"],  net_pnl)
            s["worst_net"] = net_pnl if s["worst_net"] is None else min(s["worst_net"], net_pnl)

            # CE vs PE split
            if symbol.endswith("CE"):
                s["ce_count"] += 1
                s["ce_net"]   += net_pnl
            elif symbol.endswith("PE"):
                s["pe_count"] += 1
                s["pe_net"]   += net_pnl

            if trade_direction == "SHORT":
                s["short_count"] += 1

            # Exit type
            if exit_reason in ("TP", "GTT_TP"):
                s["tp_hits"] += 1
            elif exit_reason in ("SL", "GTT_SL"):
                s["sl_hits"] += 1
            else:
                s["manual"] += 1

        for strategy_name, s in summary.items():

            total    = s["total"]
            win_rate = round((s["wins"] / total) * 100, 1) if total else 0
            avg_win  = round(s["win_sum"]  / s["wins"],   2) if s["wins"]   else 0
            avg_loss = round(s["loss_sum"] / s["losses"], 2) if s["losses"] else 0
            tp_ratio = round((s["tp_hits"] / total) * 100, 1) if total else 0

            pnl_emoji    = "🟢" if s["total_net"] >= 0 else "🔴"
            # Direction label for the header
            mode_label   = "SHORT" if s["short_count"] == total else (
                           "LONG"  if s["short_count"] == 0     else "MIXED")

            message = f"""
📊 <b>PAPER SUMMARY - {strategy_name}</b> [{mode_label}]

Trades: {total}
Wins: {s['wins']} | Losses: {s['losses']}
Win Rate: {win_rate}%

Avg Win (net): ₹{avg_win:,.2f}
Avg Loss (net): ₹{avg_loss:,.2f}

Best Trade (net): ₹{s['best_net']:,.2f}
Worst Trade (net): ₹{s['worst_net']:,.2f}

────────────
🎯 CE vs PE

CE Trades: {s['ce_count']} | Net ₹{s['ce_net']:,.0f}
PE Trades: {s['pe_count']} | Net ₹{s['pe_net']:,.0f}

────────────
💸 Charges

Total Charges: −₹{s['total_charges']:,.2f}
(Brokerage · STT · GST · Exchange · SEBI)

────────────
📌 Exit Quality

TP Hits: {s['tp_hits']}
SL Hits: {s['sl_hits']}
Manual/EOD: {s['manual']}
TP Ratio: {tp_ratio}%

────────────
Net P&L: {pnl_emoji} <b>₹{s['total_net']:,.0f}</b>
"""

            send_telegram_message(
                TELEGRAM_CONFIG.get("bot_token", ""),
                TELEGRAM_CONFIG.get("chat_id", ""),
                message.strip()
            )

        write_audit_log("[TELEGRAM] Advanced paper summary sent")

    # ==========================================================
    # SCALP_V3 PAPER SUMMARY
    #
    # V3 is an option-BUYING hedge strategy in its OWN table. It records only
    # GROSS realized_pnl ((exit - entry) * qty) — there is NO charge modelling
    # for V3 — so this summary is GROSS and intentionally OMITS the charges
    # block that the paper_trades summary shows. Header is [LONG] (the hedge is
    # always bought).
    #
    # CE/PE split is BY HEDGE (the instrument actually traded / carrying P&L),
    # not the signal side. A CE-signal trade buys a PE hedge → counts as PE.
    #
    # Exit-quality mapping (V3 reasons):
    #   SIG_TP            → TP hit  (signal target reached)
    #   SIG_SL, HEDGE_SL  → SL hit  (signal stop OR the hedge's own SL-GTT fired)
    #   EOD, MANUAL, *    → Manual/EOD
    # (Dead/cancelled/stale rows have NULL realized_pnl and are already excluded
    #  by the repo reader, so they never reach here.)
    # ==========================================================

    def _send_v3_paper_summary(self):

        rows = get_closed_paper_v3_trades_today()
        if not rows:
            return

        total      = 0
        wins       = 0
        losses     = 0
        total_pnl  = 0.0
        best       = None
        worst      = None
        win_sum    = 0.0
        loss_sum   = 0.0
        ce_count   = 0   # by HEDGE symbol
        pe_count   = 0
        ce_pnl     = 0.0
        pe_pnl     = 0.0
        tp_hits    = 0
        sl_hits    = 0
        manual     = 0

        for r in rows:
            pnl         = float(r.get("realized_pnl") or 0)
            exit_reason = r.get("exit_reason") or ""
            hedge_sym   = r.get("hedge_symbol") or ""

            total     += 1
            total_pnl += pnl

            if pnl >= 0:
                wins    += 1
                win_sum += pnl
            else:
                losses   += 1
                loss_sum += pnl

            best  = pnl if best  is None else max(best,  pnl)
            worst = pnl if worst is None else min(worst, pnl)

            # CE/PE split BY HEDGE (the traded instrument)
            if hedge_sym.endswith("CE"):
                ce_count += 1
                ce_pnl   += pnl
            elif hedge_sym.endswith("PE"):
                pe_count += 1
                pe_pnl   += pnl

            # Exit quality
            if exit_reason == "SIG_TP":
                tp_hits += 1
            elif exit_reason in ("SIG_SL", "HEDGE_SL"):
                sl_hits += 1
            else:
                manual += 1

        win_rate = round((wins / total) * 100, 1) if total else 0
        avg_win  = round(win_sum  / wins,   2) if wins   else 0
        avg_loss = round(loss_sum / losses, 2) if losses else 0
        tp_ratio = round((tp_hits / total) * 100, 1) if total else 0
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        message = f"""
📊 <b>PAPER SUMMARY - SCALP_V3</b> [LONG]

Trades: {total}
Wins: {wins} | Losses: {losses}
Win Rate: {win_rate}%

Avg Win (gross): ₹{avg_win:,.2f}
Avg Loss (gross): ₹{avg_loss:,.2f}

Best Trade (gross): ₹{best:,.2f}
Worst Trade (gross): ₹{worst:,.2f}

────────────
🎯 CE vs PE (by hedge)

CE Hedges: {ce_count} | Gross ₹{ce_pnl:,.0f}
PE Hedges: {pe_count} | Gross ₹{pe_pnl:,.0f}

────────────
📌 Exit Quality

TP Hits: {tp_hits}
SL Hits: {sl_hits}
Manual/EOD: {manual}
TP Ratio: {tp_ratio}%

────────────
Gross P&L: {pnl_emoji} <b>₹{total_pnl:,.0f}</b>
<i>(gross — V3 charges not modelled)</i>
"""

        send_telegram_message(
            TELEGRAM_CONFIG.get("bot_token", ""),
            TELEGRAM_CONFIG.get("chat_id", ""),
            message.strip()
        )

        write_audit_log("[TELEGRAM] SCALP_V3 paper summary sent")

    # ==========================================================
    # POSITION UPDATE
    # ==========================================================

    def _handle_position_update(self, now: datetime):

        levels = TELEGRAM_CONFIG.get("notification_levels", {})
        if not levels.get("positionUpdates", False):
            return

        if now.minute % 30 != 0:
            return

        minute_key = f"{now.hour}:{now.minute}"

        if self._last_position_minute == minute_key:
            return

        # Use kite.positions() for live P&L — always accurate,
        # avoids stale LTPStore values for option symbols.
        try:
            from app.brokers.zerodha_manager import ZerodhaManager
            zerodha = ZerodhaManager()

            if not zerodha.is_trade_ready():
                write_audit_log("[TELEGRAM][POS_UPDATE] Broker not ready — skipping")
                return

            kite = zerodha.get_trade_kite()
            all_positions = kite.positions().get("net", [])

            open_positions = [
                p for p in all_positions
                if p.get("quantity", 0) != 0
            ]

            if not open_positions:
                return

            total_unrealized = sum(
                p.get("unrealised", 0) or 0
                for p in open_positions
            )

        except Exception as e:
            write_audit_log(f"[TELEGRAM][POS_UPDATE][ERROR] {e}")
            return

        message = f"""
📈 <b>POSITION UPDATE</b>

Open Positions: {len(open_positions)}
Unrealized P&L: ₹{total_unrealized:,.0f}

Time: {now.strftime('%H:%M')}
"""

        send_telegram_message(
            TELEGRAM_CONFIG.get("bot_token", ""),
            TELEGRAM_CONFIG.get("chat_id", ""),
            message.strip()
        )

        self._last_position_minute = minute_key

        write_audit_log("[TELEGRAM] Position update sent")

    # ==========================================================
    # HEARTBEAT (Every 30 mins during market hours)
    # ==========================================================

    def _handle_heartbeat(self, now: datetime):

        if not is_market_open():
            return

        if now.minute % 30 != 0:
            return

        minute_key = f"hb-{now.hour}:{now.minute}"

        if getattr(self, "_last_heartbeat_minute", None) == minute_key:
            return

        notify_system_alert({
            "severity": "info",
            "message": f"💓 Backend alive - {now.strftime('%H:%M')}"
        })

        self._last_heartbeat_minute = minute_key

        write_audit_log("[TELEGRAM] Heartbeat sent")