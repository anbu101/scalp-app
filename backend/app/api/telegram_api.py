"""
TELEGRAM NOTIFICATION API (FastAPI)
app/api/telegram_api.py
"""

import requests
from datetime import datetime, date
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Optional

# In-app event bus — recorded BEFORE the Telegram filter check so the in-app
# audio/toast feed is fully independent of the Telegram strategy/mode/level
# toggles. record_event() never raises (best-effort).
from app.event_bus.inapp_events import (
    record_event,
    EVENT_ENTER,
    EVENT_TP,
    EVENT_SL,
    EVENT_EXIT,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# ═══════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════

class NotificationLevels(BaseModel):
    tradeEntries: bool = True
    tpExits: bool = True
    slExits: bool = True
    manualExits: bool = True
    positionUpdates: bool = False
    dailySummary: bool = True
    systemAlerts: bool = True
    criticalAlerts: bool = True   # GTT failures, DB failures, unprotected positions


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str
    strategy_filter: str = "all"  # "all" | "bb" | "scalp"
    mode_filter: str = "all"      # "all" | "live" | "paper"
    notification_levels: NotificationLevels


class TelegramTestRequest(BaseModel):
    bot_token: str
    chat_id: str


# ═══════════════════════════════════════════════════════════
#  STORAGE
# ═══════════════════════════════════════════════════════════

import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".scalp-app" / "telegram_config.json"

def load_telegram_config_from_file():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[TELEGRAM] Failed to load config: {e}")
            return {}
    return {}

def save_telegram_config_to_file(config_dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_dict, f, indent=2)
        print(f"[TELEGRAM] Config saved to {CONFIG_FILE}")
    except Exception as e:
        print(f"[TELEGRAM] Failed to save config: {e}")

TELEGRAM_CONFIG = load_telegram_config_from_file()
print(f"[TELEGRAM] Config loaded from {CONFIG_FILE}")


# ═══════════════════════════════════════════════════════════
#  MARKET HOURS HELPER
# ═══════════════════════════════════════════════════════════

def _is_market_hours() -> bool:
    """Returns True between 09:15 and 15:30 on weekdays."""
    now = datetime.now()
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    t = now.hour * 60 + now.minute
    return 555 <= t < 930    # 09:15 → 15:30


# ═══════════════════════════════════════════════════════════
#  DB HELPERS
#  Self-contained queries — never trust caller-provided state.
# ═══════════════════════════════════════════════════════════

def _today_midnight_ts() -> int:
    """Unix timestamp of today's midnight (00:00:00 IST)."""
    today = date.today()
    return int(datetime(today.year, today.month, today.day, 0, 0, 0).timestamp())


def _query_today_live_summary() -> dict:
    """
    Query `trades` table for today's CLOSED LIVE trades.
    Returns { total_pnl, trade_count, wins, losses, by_strategy }.
    """
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()

        rows = conn.execute(
            """
            SELECT strategy_id, entry_price, exit_price, qty
            FROM trades
            WHERE state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()

        by_strategy: dict = {}
        total_pnl = 0.0
        wins = losses = 0

        for row in rows:
            strategy_id, entry_price, exit_price, qty = row
            pnl = (float(exit_price) - float(entry_price)) * int(qty)
            total_pnl += pnl
            if pnl > 0: wins   += 1
            else:        losses += 1

            s = by_strategy.setdefault(strategy_id, {"pnl": 0.0, "count": 0})
            s["pnl"]   += pnl
            s["count"] += 1

        return {
            "total_pnl":    round(total_pnl, 2),
            "trade_count":  len(rows),
            "wins":         wins,
            "losses":       losses,
            "by_strategy":  by_strategy,
        }

    except Exception as e:
        print(f"[TELEGRAM] Live summary DB error: {e}")
        return {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "by_strategy": {}}


def _query_today_paper_summary() -> dict:
    """
    Query `paper_trades` table for today's CLOSED PAPER trades.
    Returns { total_pnl, trade_count, wins, losses, by_strategy }.
    """
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()

        rows = conn.execute(
            """
            SELECT strategy_name, pnl_value
            FROM paper_trades
            WHERE state = 'CLOSED'
              AND exit_time  IS NOT NULL
              AND exit_price IS NOT NULL
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()

        by_strategy: dict = {}
        total_pnl = 0.0
        wins = losses = 0

        for row in rows:
            strategy_name, pnl_value = row
            pnl = float(pnl_value) if pnl_value is not None else 0.0
            total_pnl += pnl
            if pnl > 0: wins   += 1
            else:        losses += 1

            s = by_strategy.setdefault(strategy_name, {"pnl": 0.0, "count": 0})
            s["pnl"]   += pnl
            s["count"] += 1

        return {
            "total_pnl":    round(total_pnl, 2),
            "trade_count":  len(rows),
            "wins":         wins,
            "losses":       losses,
            "by_strategy":  by_strategy,
        }

    except Exception as e:
        print(f"[TELEGRAM] Paper summary DB error: {e}")
        return {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "by_strategy": {}}


def _query_open_live_positions() -> list:
    """
    Query `trades` table for today's genuinely OPEN live trades.
    Returns list of { symbol, strategy_id, entry_price, qty }.
    Only trades with state NOT CLOSED and entry_time today.
    """
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()

        rows = conn.execute(
            """
            SELECT symbol, strategy_id, entry_price, qty, state
            FROM trades
            WHERE state != 'CLOSED'
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()

        return [
            {
                "symbol":      row[0],
                "strategy_id": row[1],
                "entry_price": float(row[2]),
                "qty":         int(row[3]),
                "state":       row[4],
            }
            for row in rows
        ]

    except Exception as e:
        print(f"[TELEGRAM] Open positions DB error: {e}")
        return []


def _query_open_paper_positions() -> list:
    """
    Query `paper_trades` table for today's genuinely OPEN paper trades.
    """
    try:
        from app.db.sqlite import get_conn
        conn = get_conn()
        midnight = _today_midnight_ts()

        rows = conn.execute(
            """
            SELECT symbol, strategy_name, entry_price, qty
            FROM paper_trades
            WHERE state = 'OPEN'
              AND entry_time >= ?
            """,
            (midnight,),
        ).fetchall()

        return [
            {
                "symbol":        row[0],
                "strategy_name": row[1],
                "entry_price":   float(row[2]),
                "qty":           int(row[3]),
            }
            for row in rows
        ]

    except Exception as e:
        print(f"[TELEGRAM] Open paper positions DB error: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@router.get("/config")
async def get_telegram_config():
    return {
        "bot_token": TELEGRAM_CONFIG.get("bot_token", ""),
        "chat_id": TELEGRAM_CONFIG.get("chat_id", ""),
        "strategy_filter": TELEGRAM_CONFIG.get("strategy_filter", "all"),
        "mode_filter": TELEGRAM_CONFIG.get("mode_filter", "all"),
        "notification_levels": TELEGRAM_CONFIG.get("notification_levels", {
            "tradeEntries": True,
            "tpExits": True,
            "slExits": True,
            "manualExits": True,
            "positionUpdates": False,
            "dailySummary": True,
            "systemAlerts": True,
            "criticalAlerts": True,
        })
    }


@router.post("/config")
async def save_telegram_config(config: TelegramConfig):
    TELEGRAM_CONFIG["bot_token"] = config.bot_token
    TELEGRAM_CONFIG["chat_id"] = config.chat_id
    TELEGRAM_CONFIG["strategy_filter"] = config.strategy_filter
    TELEGRAM_CONFIG["mode_filter"] = config.mode_filter
    TELEGRAM_CONFIG["notification_levels"] = config.notification_levels.dict()
    save_telegram_config_to_file(TELEGRAM_CONFIG)
    return {"success": True}


@router.post("/test")
async def test_telegram_connection(request: TelegramTestRequest):
    message = "✅ Scalp Terminal Connected!\n\nThis is a test message."
    success = send_telegram_message(request.bot_token, request.chat_id, message)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to send test message.")
    return {"success": True}


# 🔥 DEBUG ENDPOINT
@router.post("/debug/send-mock-trade")
async def send_mock_trade_notification():
    """Debug endpoint to test trade notifications."""
    import asyncio

    try:
        notify_trade_entry({
            "strategy_id": "SCALP_V1",
            "mode": "paper",
            "symbol": "NIFTY24FEB22000CE",
            "side": "CE",
            "entry_price": 45.50,
            "quantity": 50,
            "sl": 40.00,
            "tp": 55.00
        })

        await asyncio.sleep(2)

        notify_tp_exit({
            "strategy_id": "SCALP_V1",
            "mode": "paper",
            "symbol": "NIFTY24FEB22000CE",
            "entry_price": 45.50,
            "exit_price": 55.00,
            "pnl": 475
        })

        return {"success": True, "message": "Sent 2 mock notifications. Check Telegram!"}

    except Exception as e:
        return {"success": False, "error": str(e)}


# 🔥 DEBUG ENDPOINT — fires ALL FOUR event types so you can A/B the tones.
# ENTER → TP → SL → EXIT(profit) → EXIT(loss), spaced ~2.5s apart.
# Each emits an in-app event (record_event runs inside every notify_* fn);
# Telegram filters may suppress the messages, but the in-app feed always gets
# the events. Use this to confirm each tone is distinct.
@router.post("/debug/send-all-event-types")
async def send_all_event_types():
    import asyncio
    base = {
        "strategy_id": "SCALP_V1",
        "mode": "paper",
        "symbol": "NIFTY24FEB22000CE",
        "side": "CE",
        "entry_price": 45.50,
    }
    try:
        # 1) ENTER  → positionEntered (rising pair)
        notify_trade_entry({**base, "quantity": 50, "sl": 40.00, "tp": 55.00})
        await asyncio.sleep(2.5)

        # 2) TP     → takeProfitHit (rising triad)
        notify_tp_exit({**base, "exit_price": 55.00, "pnl": 475})
        await asyncio.sleep(2.5)

        # 3) SL     → stopLossHit (descending triad)
        notify_sl_exit({**base, "exit_price": 40.00, "pnl": -275})
        await asyncio.sleep(2.5)

        # 4) EXIT, profit → (Option A) takeProfitHit, toast says "Closed"
        notify_manual_exit({**base, "exit_price": 52.00, "pnl": 325, "exit_reason": "SuperTrend"})
        await asyncio.sleep(2.5)

        # 5) EXIT, loss   → (Option A) stopLossHit, toast says "Closed"
        notify_manual_exit({**base, "exit_price": 41.00, "pnl": -225, "exit_reason": "SuperTrend"})

        return {
            "success": True,
            "message": "Fired ENTER, TP, SL, EXIT(+), EXIT(-). "
                       "Expect: rising pair, rising triad, falling triad, "
                       "rising triad (closed-profit), falling triad (closed-loss).",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
#  CORE SEND FUNCTION
# ═══════════════════════════════════════════════════════════

def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM] Send failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════
#  FILTER CHECK HELPERS
# ═══════════════════════════════════════════════════════════

def _passes_filters(trade_data: dict, level_key: str) -> bool:

    if not TELEGRAM_CONFIG:
        return False

    levels = TELEGRAM_CONFIG.get("notification_levels", {})
    if not levels.get(level_key, False):
        return False

    strategy = trade_data.get('strategy_id', '').lower()
    strategy_filter = TELEGRAM_CONFIG.get("strategy_filter", "all")

    if strategy_filter != "all":
        if strategy_filter == "bb" and "bb" not in strategy:
            return False
        if strategy_filter == "scalp" and "scalp" not in strategy:
            return False

    mode = trade_data.get('mode', 'live').lower()
    mode_filter = TELEGRAM_CONFIG.get("mode_filter", "all")

    if mode_filter != "all" and mode_filter != mode:
        return False

    return True


# ═══════════════════════════════════════════════════════════
#  NOTIFICATIONS
#
#  Each of the four trade notifications records an in-app event FIRST
#  (before _passes_filters), so the in-app audio/toast feed always fires
#  regardless of the Telegram strategy/mode/level toggles. The in-app feed
#  has its own mute controls (App Settings: notify_audio / notify_toast).
# ═══════════════════════════════════════════════════════════

def notify_trade_entry(trade_data: dict):

    # In-app event — independent of Telegram filters.
    record_event(
        EVENT_ENTER,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
    )

    if not _passes_filters(trade_data, "tradeEntries"):
        return

    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    sl_val = trade_data.get('sl')
    tp_val = trade_data.get('tp')
    sl_str = f"₹{sl_val}" if sl_val else "—"
    tp_str = f"₹{tp_val}" if tp_val else "—"

    note = trade_data.get("note", "")
    note_line = f"\n⚠️ {note}" if note else ""

    message = f"""
🎯 <b>TRADE ENTRY</b> {mode_badge}

Strategy: {trade_data.get('strategy_id', 'Unknown')}
Symbol: <code>{trade_data.get('symbol')}</code>
Side: {trade_data.get('side')}
Entry: ₹{trade_data.get('entry_price')}
Quantity: {trade_data.get('quantity')}

SL: {sl_str} | TP: {tp_str}
Time: {datetime.now().strftime('%H:%M:%S')}{note_line}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_tp_exit(trade_data: dict):

    # In-app event — independent of Telegram filters.
    record_event(
        EVENT_TP,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
        exit_price=trade_data.get("exit_price"),
        pnl=trade_data.get("pnl"),
    )

    if not _passes_filters(trade_data, "tpExits"):
        return

    pnl = trade_data.get("pnl") or 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
{pnl_emoji} <b>TARGET HIT</b> {mode_badge}

Strategy: {trade_data.get('strategy_id')}
Symbol: <code>{trade_data.get('symbol')}</code>
Entry: ₹{trade_data.get('entry_price')}
Exit: ₹{trade_data.get('exit_price')}

P&L: <b>₹{pnl:,.0f}</b>
Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_sl_exit(trade_data: dict):

    # In-app event — independent of Telegram filters.
    record_event(
        EVENT_SL,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
        exit_price=trade_data.get("exit_price"),
        pnl=trade_data.get("pnl"),
    )

    if not _passes_filters(trade_data, "slExits"):
        return

    pnl = trade_data.get("pnl") or 0
    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
🛑 <b>STOP-LOSS HIT</b> {mode_badge}

Strategy: {trade_data.get('strategy_id')}
Symbol: <code>{trade_data.get('symbol')}</code>
Entry: ₹{trade_data.get('entry_price')}
Exit: ₹{trade_data.get('exit_price')}

Loss: <b>₹{pnl:,.0f}</b>
Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_manual_exit(trade_data: dict):

    # In-app event — independent of Telegram filters.
    record_event(
        EVENT_EXIT,
        strategy_id=trade_data.get("strategy_id", ""),
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side"),
        mode=trade_data.get("mode", "live"),
        entry_price=trade_data.get("entry_price"),
        exit_price=trade_data.get("exit_price"),
        pnl=trade_data.get("pnl"),
    )

    if not _passes_filters(trade_data, "manualExits"):
        return

    pnl = trade_data.get("pnl") or 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
{pnl_emoji} <b>POSITION CLOSED</b> {mode_badge}

Strategy: {trade_data.get('strategy_id')}
Symbol: <code>{trade_data.get('symbol')}</code>
Reason: {trade_data.get('exit_reason', 'Manual')}

P&L: <b>₹{pnl:,.0f}</b>
Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_position_update(update_data: dict = None):
    """
    Position updates — only sent during market hours (09:15–15:30).

    Queries the DB directly for open positions and computes live
    unrealized P&L from LTPStore at the moment the function fires.
    Never trusts caller-provided data — the scheduler calls this
    with no args and gets a fresh snapshot every time.

    If no open positions exist in DB → silently skip (no notification).
    """

    if not TELEGRAM_CONFIG:
        return

    levels = TELEGRAM_CONFIG.get("notification_levels", {})
    if not levels.get("positionUpdates", False):
        return

    # Suppress outside market hours
    if not _is_market_hours():
        return

    # ── Query DB directly — source of truth ──────────────────────────
    live_open  = _query_open_live_positions()
    paper_open = _query_open_paper_positions()

    total_open = len(live_open) + len(paper_open)

    # Nothing open in DB → skip notification entirely
    # Prevents "phantom position" messages after a SuperTrend exit
    if total_open == 0:
        print("[TELEGRAM] Position update skipped — no open positions in DB")
        return

    # ── Compute live P&L from LTPStore ───────────────────────────────
    try:
        from app.marketdata.ltp_store import LTPStore
    except Exception:
        LTPStore = None

    def _live_pnl_line(symbol: str, entry_price: float, qty: int) -> str:
        result = LTPStore.get_with_timestamp(symbol) if LTPStore else None
        if result is not None:
            ltp, ts = result
            import time as _time
            staleness = _time.time() - ts
            if staleness > 300:  # older than 5 minutes = stale, don't show false data
                return f"  <code>{symbol}</code>  LTP stale ({int(staleness)}s old)"
            pnl = (ltp - entry_price) * qty
            arrow = "▲" if pnl >= 0 else "▼"
            return f"  <code>{symbol}</code>  {arrow} ₹{pnl:+,.0f}  (LTP {ltp:.2f})"
        return f"  <code>{symbol}</code>  LTP unavailable"

    # ── Build message — LIVE and PAPER kept strictly separate ────────
    sections = []

    if live_open:
        live_unrealised = 0.0
        live_lines = []
        import time as _time
        for p in live_open:
            result = LTPStore.get_with_timestamp(p["symbol"]) if LTPStore else None
            if result is not None:
                ltp, ts = result
                if (_time.time() - ts) <= 300 and p["entry_price"]:  # only fresh prices
                    live_unrealised += (ltp - p["entry_price"]) * p["qty"]

        live_arrow = "▲" if live_unrealised >= 0 else "▼"
        sections.append(
            "🟢 <b>LIVE</b>\n"
            + "\n".join(live_lines)
            + f"\n  Unrealized P&L: <b>{live_arrow} ₹{live_unrealised:+,.0f}</b>"
        )

    if paper_open:
        paper_unrealised = 0.0
        paper_lines = []
        for p in paper_open:
            result = LTPStore.get_with_timestamp(p["symbol"]) if LTPStore else None
            if result is not None:
                ltp, ts = result
                if (_time.time() - ts) <= 300 and p["entry_price"]:
                    paper_unrealised += (ltp - p["entry_price"]) * p["qty"]


        paper_arrow = "▲" if paper_unrealised >= 0 else "▼"
        sections.append(
            "📄 <b>PAPER</b>\n"
            + "\n".join(paper_lines)
            + f"\n  Unrealized P&L: <b>{paper_arrow} ₹{paper_unrealised:+,.0f}</b>"
        )

    body = "\n\n".join(sections)

    message = f"""
📊 <b>POSITION UPDATE</b>

{body}

Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_critical(alert_data: dict):
    """
    Critical / fatal alerts — GTT failures, DB failures, unprotected positions.
    Controlled by the 'criticalAlerts' toggle (default ON).
    NOT filtered by strategy/mode — always fires for live issues.
    """

    if not TELEGRAM_CONFIG:
        return

    levels = TELEGRAM_CONFIG.get("notification_levels", {})
    if not levels.get("criticalAlerts", True):
        return

    severity = alert_data.get("severity", "error")
    emoji = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "🚨")

    message = f"""
{emoji} <b>CRITICAL ALERT</b>

{alert_data.get('message', '')}

Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_daily_summary(summary_data: dict = None):
    """
    FIX: Queries BOTH `trades` (LIVE) and `paper_trades` (PAPER) tables
    directly instead of trusting the caller's summary_data.

    Old bug: caller only passed BB live P&L (₹94), completely missing
    the SCALP paper trades (-₹2,941).

    Now shows:
      - LIVE section: per-strategy breakdown from `trades` table
      - PAPER section: per-strategy breakdown from `paper_trades` table
      - Combined total across both
    """

    if not TELEGRAM_CONFIG or not TELEGRAM_CONFIG.get("notification_levels", {}).get("dailySummary"):
        return

    # ── Query DB directly ────────────────────────────────────────────
    live  = _query_today_live_summary()
    paper = _query_today_paper_summary()

    combined_pnl   = live["total_pnl"] + paper["total_pnl"]
    combined_emoji = "🟢" if combined_pnl >= 0 else "🔴"

    # ── LIVE section ─────────────────────────────────────────────────
    live_lines = []
    if live["trade_count"] > 0:
        live_lines.append(
            f"🟢 <b>LIVE</b> — {live['trade_count']} trades · "
            f"{live['wins']}W/{live['losses']}L"
        )
        for strat, data in live["by_strategy"].items():
            pnl_str = f"₹{data['pnl']:+,.0f}"
            live_lines.append(f"  {strat}: {pnl_str} ({data['count']} trades)")
        live_lines.append(
            f"  <b>Subtotal: ₹{live['total_pnl']:+,.0f}</b>"
        )
    else:
        live_lines.append("🟢 <b>LIVE</b> — No trades today")

    # ── PAPER section ────────────────────────────────────────────────
    paper_lines = []
    if paper["trade_count"] > 0:
        paper_lines.append(
            f"📄 <b>PAPER</b> — {paper['trade_count']} trades · "
            f"{paper['wins']}W/{paper['losses']}L"
        )
        for strat, data in paper["by_strategy"].items():
            pnl_str = f"₹{data['pnl']:+,.0f}"
            paper_lines.append(f"  {strat}: {pnl_str} ({data['count']} trades)")
        paper_lines.append(
            f"  <b>Subtotal: ₹{paper['total_pnl']:+,.0f}</b>"
        )
    else:
        paper_lines.append("📄 <b>PAPER</b> — No trades today")

    live_text  = "\n".join(live_lines)
    paper_text = "\n".join(paper_lines)

    message = f"""
📊 <b>DAILY SUMMARY</b>

{live_text}

{paper_text}

──────────────────
Combined P&L: {combined_emoji} <b>₹{combined_pnl:+,.0f}</b>
Date: {datetime.now().strftime('%d %b %Y')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_system_alert(alert_data: dict):

    if not TELEGRAM_CONFIG or not TELEGRAM_CONFIG.get("notification_levels", {}).get("systemAlerts"):
        return

    severity_emoji = {
        "error": "🚨",
        "warning": "⚠️",
        "info": "ℹ️"
    }

    emoji = severity_emoji.get(alert_data.get('severity', 'info'), "ℹ️")

    message = f"""
{emoji} <b>SYSTEM ALERT</b>

{alert_data.get('message')}

Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


# ==========================================================
# DEBUG - MANUAL DAILY SUMMARY
# ==========================================================

@router.get("/debug/run-daily-summary")
async def debug_run_daily_summary(request: Request):
    scheduler = request.app.state.telegram_scheduler
    scheduler.run_daily_summary_now()
    return {
        "success": True,
        "message": "Manual daily summary triggered."
    }