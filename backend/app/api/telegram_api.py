"""
TELEGRAM NOTIFICATION API (FastAPI)
app/api/telegram_api.py
"""

import requests
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Optional

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


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str
    strategy_filter: str = "all"  # "all" | "bb" | "scalp"
    mode_filter: str = "all"  # "all" | "live" | "paper"
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
            "systemAlerts": True
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
    """
    Debug endpoint to test trade notifications
    Sends mock entry + exit notification
    """
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

        return {
            "success": True,
            "message": "Sent 2 mock notifications. Check Telegram!"
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
# ═══════════════════════════════════════════════════════════

def notify_trade_entry(trade_data: dict):

    if not _passes_filters(trade_data, "tradeEntries"):
        return

    mode = trade_data.get("mode", "live").lower()
    mode_badge = "🟢 LIVE" if mode == "live" else "📄 PAPER"

    message = f"""
🎯 <b>TRADE ENTRY</b> {mode_badge}

Strategy: {trade_data.get('strategy_id', 'Unknown')}
Symbol: <code>{trade_data.get('symbol')}</code>
Side: {trade_data.get('side')}
Entry: ₹{trade_data.get('entry_price')}
Quantity: {trade_data.get('quantity')}

SL: ₹{trade_data.get('sl')} | TP: ₹{trade_data.get('tp')}
Time: {datetime.now().strftime('%H:%M:%S')}
"""

    send_telegram_message(
        TELEGRAM_CONFIG.get("bot_token", ""),
        TELEGRAM_CONFIG.get("chat_id", ""),
        message.strip()
    )


def notify_tp_exit(trade_data: dict):

    if not _passes_filters(trade_data, "tpExits"):
        return

    pnl = trade_data.get("pnl", 0)
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

    if not _passes_filters(trade_data, "slExits"):
        return

    pnl = trade_data.get("pnl", 0)
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

    if not _passes_filters(trade_data, "manualExits"):
        return

    pnl = trade_data.get("pnl", 0)
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


def notify_daily_summary(summary_data: dict):

    if not TELEGRAM_CONFIG or not TELEGRAM_CONFIG.get("notification_levels", {}).get("dailySummary"):
        return

    total_pnl = summary_data.get("total_pnl", 0)
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

    message = f"""
📊 <b>DAILY SUMMARY</b>

Total P&L: {pnl_emoji} <b>₹{total_pnl:,.0f}</b>

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

