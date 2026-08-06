import threading
import time
from datetime import datetime, timedelta, timezone

from app.api.telegram_api import (
    send_telegram_message,
    TELEGRAM_CONFIG,
    NOTIF_DAILY_SUMMARY,
    _iter_active_channels,
    _query_today_live_summary,
    _query_today_paper_summary,
)
from app.db.trades_repo import get_total_pnl_for_strategy
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.api.telegram_summary_send import (
    send_daily_summary_card,
    build_card_data_once,
)
# SCALP_V3 lives in its OWN table (scalp_v3_trades). Pull V3 totals/rows from
# its own repo for the text fallback's V3 paper summary.
from app.db.scalp_v3_repo import (
    get_total_pnl_v3,
    get_closed_paper_v3_trades_today,
)

IST = timezone(timedelta(hours=5, minutes=30))


class TelegramScheduler:

    CHECK_INTERVAL_SEC = 30

    # ── CAS_2026 ── Daily-summary fire time. Was 15:30, which was the FNO
    # close before the Closing Auction Session rollout on 2026-08-03; the
    # FNO session now runs to 15:40, so a 15:30 card cut off the last ten
    # minutes of live trading. Named constants (not inline literals) so the
    # scheduler, the audit line and the tests all read the same value.
    SUMMARY_HOUR   = 15
    SUMMARY_MINUTE = 40

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
            target=self._run_loop, daemon=True, name="TelegramScheduler",
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
        # Heartbeat removed.

    # ==========================================================
    # DAILY SUMMARY  (multi-channel, card-first)
    #
    # For each channel that wants a daily summary (dailySummary ON + schedule
    # passes), send the dark PNG card; on any per-channel card failure, fall
    # back to the text summary FOR THAT CHANNEL. CardData is built ONCE and
    # reused across channels.
    #
    # The fire still happens once/day, now at SUMMARY_HOUR:SUMMARY_MINUTE
    # (15:40 post-CAS). NOTE: schedule is a strict start <= now < end window,
    # so a channel that wants the summary must set its window end AFTER 15:40
    # (the 15:45 default still works; the UI hints this).
    # ==========================================================

    def _handle_daily_summary(self, now: datetime):
        if not (now.hour == self.SUMMARY_HOUR
                and now.minute == self.SUMMARY_MINUTE):
            return

        today_str = now.date().isoformat()
        if self._last_summary_date == today_str:
            return

        self._dispatch_daily_summary(now)
        self._last_summary_date = today_str
        write_audit_log(f"[TELEGRAM] Daily summary dispatched (multi-channel) "
                        f"@ {self.SUMMARY_HOUR:02d}:{self.SUMMARY_MINUTE:02d}")

    def _dispatch_daily_summary(self, now: datetime):
        """
        Fan out the daily summary to every channel with dailySummary ON whose
        schedule passes. Card-first per channel, text fallback per channel.
        """
        # Build the card data once; reuse for all channels.
        card_data = build_card_data_once()

        targets = list(_iter_active_channels(NOTIF_DAILY_SUMMARY, now=now))
        if not targets:
            write_audit_log("[TELEGRAM] Daily summary — no eligible channels")
            return

        for bot_token, chat_id, _ch in targets:
            try:
                send_daily_summary_card(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text_fallback=self._send_text_summary_to,
                    data=card_data,
                )
            except Exception as e:
                write_audit_log(
                    f"[TELEGRAM] Daily summary channel send failed chat={chat_id}: {e}"
                )
                # Last-resort: try the text summary directly for this channel.
                try:
                    self._send_text_summary_to(bot_token, chat_id)
                except Exception as e2:
                    write_audit_log(f"[TELEGRAM] Text fallback also failed chat={chat_id}: {e2}")

    # ==========================================================
    # MANUAL DAILY SUMMARY TRIGGER (DEBUG)
    # ==========================================================

    def run_daily_summary_now(self):
        write_audit_log("[TELEGRAM][DEBUG] Manual daily summary trigger")
        self._dispatch_daily_summary(datetime.now(IST))
        write_audit_log("[TELEGRAM][DEBUG] Manual daily summary completed")

    # ==========================================================
    # TEXT SUMMARY (FALLBACK) — CHANNEL-AWARE
    #
    # Sends the combined text summary + the detailed advanced paper / V3 paper
    # breakdowns to ONE channel's chat. Reached only when a channel's card path
    # fails. Mirrors the old four-part text summary, but targeted at a single
    # (bot_token, chat_id) instead of the global config chat.
    # ==========================================================

    def _send_text_summary_to(self, bot_token: str, chat_id: str):
        # 1) Combined LIVE+PAPER headline summary.
        try:
            self._send_combined_text_summary(bot_token, chat_id)
        except Exception as e:
            write_audit_log(f"[TELEGRAM] combined text summary failed chat={chat_id}: {e}")

        # 2) Advanced per-strategy PAPER summary (paper_trades).
        try:
            self._send_advanced_paper_summary(bot_token, chat_id)
        except Exception as e:
            write_audit_log(f"[TELEGRAM] advanced paper summary failed chat={chat_id}: {e}")

        # 3) SCALP_V3 PAPER summary (own table).
        try:
            self._send_v3_paper_summary(bot_token, chat_id)
        except Exception as e:
            write_audit_log(f"[TELEGRAM] V3 paper summary failed chat={chat_id}: {e}")

        write_audit_log(f"[TELEGRAM] Text summary (fallback) sent -> {chat_id}")

    def _send_combined_text_summary(self, bot_token: str, chat_id: str):
        live  = _query_today_live_summary()
        paper = _query_today_paper_summary()
        combined_pnl   = live["total_pnl"] + paper["total_pnl"]
        combined_emoji = "🟢" if combined_pnl >= 0 else "🔴"

        live_lines = []
        if live["trade_count"] > 0:
            live_lines.append(f"🟢 <b>LIVE</b> — {live['trade_count']} trades · {live['wins']}W/{live['losses']}L")
            for strat, data in live["by_strategy"].items():
                live_lines.append(f"  {strat}: ₹{data['pnl']:+,.0f} ({data['count']} trades)")
            live_lines.append(f"  <b>Subtotal: ₹{live['total_pnl']:+,.0f}</b>")
        else:
            live_lines.append("🟢 <b>LIVE</b> — No trades today")

        paper_lines = []
        if paper["trade_count"] > 0:
            paper_lines.append(f"📄 <b>PAPER</b> — {paper['trade_count']} trades · {paper['wins']}W/{paper['losses']}L")
            for strat, data in paper["by_strategy"].items():
                paper_lines.append(f"  {strat}: ₹{data['pnl']:+,.0f} ({data['count']} trades)")
            paper_lines.append(f"  <b>Subtotal: ₹{paper['total_pnl']:+,.0f}</b>")
        else:
            paper_lines.append("📄 <b>PAPER</b> — No trades today")

        message = f"""
📊 <b>DAILY SUMMARY</b>

{chr(10).join(live_lines)}

{chr(10).join(paper_lines)}

──────────────────
Combined P&L: {combined_emoji} <b>₹{combined_pnl:+,.0f}</b>
Date: {datetime.now().strftime('%d %b %Y')}
""".strip()

        send_telegram_message(bot_token, chat_id, message)

    # ==========================================================
    # PAPER ADVANCED SUMMARY — direction-aware + charges.
    # Reads paper_trades only. Targeted at a single chat.
    # ==========================================================

    def _send_advanced_paper_summary(self, bot_token: str, chat_id: str):
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT strategy_name, symbol, net_pnl, total_charges,
                   trade_direction, exit_reason
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_price IS NOT NULL
              AND net_pnl IS NOT NULL
              AND date(entry_time, 'unixepoch', 'localtime') = date('now', 'localtime')
            """
        ).fetchall()

        if not rows:
            return

        summary = {}
        for strategy_name, symbol, net_pnl, total_charges, trade_direction, exit_reason in rows:
            net_pnl = float(net_pnl or 0)
            total_charges = float(total_charges or 0)
            if strategy_name not in summary:
                summary[strategy_name] = {
                    "total": 0, "wins": 0, "losses": 0, "total_net": 0,
                    "total_charges": 0, "best_net": None, "worst_net": None,
                    "win_sum": 0, "loss_sum": 0, "ce_count": 0, "pe_count": 0,
                    "ce_net": 0, "pe_net": 0, "tp_hits": 0, "sl_hits": 0,
                    "manual": 0, "short_count": 0,
                }
            s = summary[strategy_name]
            s["total"] += 1
            s["total_net"] += net_pnl
            s["total_charges"] += total_charges
            if net_pnl >= 0:
                s["wins"] += 1; s["win_sum"] += net_pnl
            else:
                s["losses"] += 1; s["loss_sum"] += net_pnl
            s["best_net"]  = net_pnl if s["best_net"]  is None else max(s["best_net"],  net_pnl)
            s["worst_net"] = net_pnl if s["worst_net"] is None else min(s["worst_net"], net_pnl)
            if symbol.endswith("CE"):
                s["ce_count"] += 1; s["ce_net"] += net_pnl
            elif symbol.endswith("PE"):
                s["pe_count"] += 1; s["pe_net"] += net_pnl
            if trade_direction == "SHORT":
                s["short_count"] += 1
            if exit_reason in ("TP", "GTT_TP"):
                s["tp_hits"] += 1
            elif exit_reason in ("SL", "GTT_SL"):
                s["sl_hits"] += 1
            else:
                s["manual"] += 1

        for strategy_name, s in summary.items():
            total = s["total"]
            win_rate = round((s["wins"] / total) * 100, 1) if total else 0
            avg_win  = round(s["win_sum"]  / s["wins"],   2) if s["wins"]   else 0
            avg_loss = round(s["loss_sum"] / s["losses"], 2) if s["losses"] else 0
            tp_ratio = round((s["tp_hits"] / total) * 100, 1) if total else 0
            pnl_emoji  = "🟢" if s["total_net"] >= 0 else "🔴"
            mode_label = "SHORT" if s["short_count"] == total else ("LONG" if s["short_count"] == 0 else "MIXED")

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
""".strip()

            send_telegram_message(bot_token, chat_id, message)

        write_audit_log(f"[TELEGRAM] Advanced paper summary sent -> {chat_id}")

    # ==========================================================
    # SCALP_V3 PAPER SUMMARY — own table, GROSS. Targeted at a single chat.
    # ==========================================================

    def _send_v3_paper_summary(self, bot_token: str, chat_id: str):
        rows = get_closed_paper_v3_trades_today()
        if not rows:
            return

        total = wins = losses = 0
        total_pnl = 0.0
        best = worst = None
        win_sum = loss_sum = 0.0
        ce_count = pe_count = 0
        ce_pnl = pe_pnl = 0.0
        tp_hits = sl_hits = manual = 0

        for r in rows:
            pnl = float(r.get("realized_pnl") or 0)
            exit_reason = r.get("exit_reason") or ""
            hedge_sym = r.get("hedge_symbol") or ""
            total += 1
            total_pnl += pnl
            if pnl >= 0:
                wins += 1; win_sum += pnl
            else:
                losses += 1; loss_sum += pnl
            best = pnl if best is None else max(best, pnl)
            worst = pnl if worst is None else min(worst, pnl)
            if hedge_sym.endswith("CE"):
                ce_count += 1; ce_pnl += pnl
            elif hedge_sym.endswith("PE"):
                pe_count += 1; pe_pnl += pnl
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
""".strip()

        send_telegram_message(bot_token, chat_id, message)
        write_audit_log(f"[TELEGRAM] SCALP_V3 paper summary sent -> {chat_id}")

    # ==========================================================
    # POSITION UPDATE  (fan-out handled inside notify_position_update)
    # ==========================================================

    def _handle_position_update(self, now: datetime):
        if now.minute % 30 != 0:
            return
        minute_key = f"{now.hour}:{now.minute}"
        if self._last_position_minute == minute_key:
            return

        # notify_position_update() does its own market-hours gate, DB read,
        # live-LTP compute, and per-channel fan-out (positionUpdates toggle +
        # schedule). The scheduler only handles the 30-min cadence + dedup.
        try:
            from app.api.telegram_api import notify_position_update
            notify_position_update()
        except Exception as e:
            write_audit_log(f"[TELEGRAM][POS_UPDATE][ERROR] {e}")
            return

        self._last_position_minute = minute_key
        write_audit_log("[TELEGRAM] Position update tick")