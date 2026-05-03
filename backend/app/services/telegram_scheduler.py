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
                get_total_pnl_for_strategy("BB_V1")
            )

            notify_daily_summary({
                "total_pnl": live_total_pnl
            })

            # PAPER ADVANCED SUMMARY
            self._send_advanced_paper_summary()

            self._last_summary_date = today_str

            write_audit_log("[TELEGRAM] Daily summary sent")

    # ==========================================================
    # MANUAL DAILY SUMMARY TRIGGER (DEBUG)
    # ==========================================================

    def run_daily_summary_now(self):
        """
        Manually trigger daily summary (for testing).
        Ignores time check.
        """

        write_audit_log("[TELEGRAM][DEBUG] Manual daily summary trigger")

        # LIVE TOTAL
        live_total_pnl = (
            get_total_pnl_for_strategy("SCALP_V1") +
            get_total_pnl_for_strategy("BB_V1")
        )

        notify_daily_summary({
            "total_pnl": live_total_pnl
        })

        # PAPER ADVANCED SUMMARY
        self._send_advanced_paper_summary()

        write_audit_log("[TELEGRAM][DEBUG] Manual daily summary completed")

    # ==========================================================
    # MANUAL DAILY SUMMARY TRIGGER (DEBUG)
    # ==========================================================

    def _send_advanced_paper_summary(self):

        conn = get_conn()

        rows = conn.execute(
            """
            SELECT strategy_name, symbol, entry_price, exit_price, qty, exit_reason
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') =
                  date('now', 'localtime')
            """
        ).fetchall()

        if not rows:
            return

        summary = {}

        for strategy_name, symbol, entry_price, exit_price, qty, exit_reason in rows:

            # Calculate PnL safely
            pnl = (exit_price - entry_price) * qty

            if strategy_name not in summary:
                summary[strategy_name] = {
                    "total": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_pnl": 0,
                    "best": None,
                    "worst": None,
                    "win_sum": 0,
                    "loss_sum": 0,
                    "ce_count": 0,
                    "pe_count": 0,
                    "ce_pnl": 0,
                    "pe_pnl": 0,
                    "tp_hits": 0,
                    "sl_hits": 0,
                    "manual": 0,
                }

            s = summary[strategy_name]

            s["total"] += 1
            s["total_pnl"] += pnl

            if pnl >= 0:
                s["wins"] += 1
                s["win_sum"] += pnl
            else:
                s["losses"] += 1
                s["loss_sum"] += pnl

            s["best"] = pnl if s["best"] is None else max(s["best"], pnl)
            s["worst"] = pnl if s["worst"] is None else min(s["worst"], pnl)

            # CE vs PE split
            if symbol.endswith("CE"):
                s["ce_count"] += 1
                s["ce_pnl"] += pnl
            elif symbol.endswith("PE"):
                s["pe_count"] += 1
                s["pe_pnl"] += pnl

            # Exit type
            if exit_reason == "TP":
                s["tp_hits"] += 1
            elif exit_reason == "SL":
                s["sl_hits"] += 1
            else:
                s["manual"] += 1

        for strategy_name, s in summary.items():

            total = s["total"]

            win_rate = round((s["wins"] / total) * 100, 1) if total else 0
            avg_win = round(s["win_sum"] / s["wins"], 2) if s["wins"] else 0
            avg_loss = round(s["loss_sum"] / s["losses"], 2) if s["losses"] else 0
            tp_ratio = round((s["tp_hits"] / total) * 100, 1) if total else 0

            pnl_emoji = "🟢" if s["total_pnl"] >= 0 else "🔴"

            message = f"""
📊 <b>PAPER SUMMARY - {strategy_name}</b>

Trades: {total}
Wins: {s['wins']} | Losses: {s['losses']}
Win Rate: {win_rate}%

Avg Win: ₹{avg_win}
Avg Loss: ₹{avg_loss}

Best: ₹{s['best']}
Worst: ₹{s['worst']}

────────────
🎯 CE vs PE

CE Trades: {s['ce_count']} | ₹{s['ce_pnl']:,.0f}
PE Trades: {s['pe_count']} | ₹{s['pe_pnl']:,.0f}

────────────
📌 Exit Quality

TP Hits: {s['tp_hits']}
SL Hits: {s['sl_hits']}
Manual: {s['manual']}
TP Ratio: {tp_ratio}%

────────────
Total P&L: {pnl_emoji} <b>₹{s['total_pnl']:,.0f}</b>
"""

            send_telegram_message(
                TELEGRAM_CONFIG.get("bot_token", ""),
                TELEGRAM_CONFIG.get("chat_id", ""),
                message.strip()
            )

        write_audit_log("[TELEGRAM] Advanced paper summary sent")


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

        # --------------------------------------------------
        # FIX: Use kite.positions() for live P&L.
        # The old approach used LTPStore.get(option_symbol) which
        # holds the WS price at entry time and rarely updates for
        # options that are not actively subscribed — always showed stale.
        # kite.positions() returns Zerodha-computed unrealised P&L using
        # their own live LTP, so it is always accurate.
        # --------------------------------------------------
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

        # Only during market hours
        if not is_market_open():
            return

        # Every 30 mins
        if now.minute % 30 != 0:
            return

        minute_key = f"hb-{now.hour}:{now.minute}"

        if getattr(self, "_last_heartbeat_minute", None) == minute_key:
            return

        message = f"""
💓 <b>BACKEND ALIVE</b>

Time: {now.strftime('%H:%M')}
"""    

        notify_system_alert({
            "severity": "info",
            "message": f"💓 Backend alive - {now.strftime('%H:%M')}"
        })


        self._last_heartbeat_minute = minute_key

        write_audit_log("[TELEGRAM] Heartbeat sent")

