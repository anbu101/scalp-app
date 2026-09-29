"""
EOD CARD SENDER — sendPhoto + fail-open fallback to the text summary.
app/api/telegram_summary_send.py

MULTI-CHANNEL CHANGE
--------------------
Previously this read TELEGRAM_CONFIG["chat_id"] directly and sent one photo to
one chat. Under the multi-channel config there is no top-level chat_id, so the
function now takes an EXPLICIT (bot_token, chat_id) target plus the text
fallback. The scheduler calls it ONCE PER CHANNEL whose dailySummary toggle +
schedule pass, so each channel independently gets card-or-text.

Contract (per channel):
  1. Build CardData from the repos (shared across channels — built once by the
     caller and passed in, OR built here if not provided).
  2. Render PNG. If render returns None -> call text_fallback(bot_token, chat_id).
  3. sendPhoto with a headline caption. If sendPhoto fails -> text_fallback.

The fallback is now a CHANNEL-AWARE callable: text_fallback(bot_token, chat_id)
sends the existing text summary to that one chat. This preserves the original
fail-open guarantee per channel.
"""

from __future__ import annotations

import requests
from typing import Callable, Optional

from app.event_bus.audit_logger import write_audit_log
from app.api.telegram_summary_card import build_summary_card_png, _fmt_headline
from app.api.telegram_summary_data import build_card_data
from app.api.telegram_summary_card import CardData


def _send_photo(bot_token: str, chat_id: str, png: bytes, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("daily_summary.png", png, "image/png")},
            timeout=20,
        )
        if resp.status_code != 200:
            write_audit_log(
                f"[TELEGRAM][CARD] sendPhoto non-200: {resp.status_code} {resp.text[:200]}"
            )
            return False
        return True
    except Exception as e:
        write_audit_log(f"[TELEGRAM][CARD] sendPhoto failed: {e}")
        return False


def send_daily_summary_card(
    *,
    bot_token: str,
    chat_id: str,
    text_fallback: Callable[[str, str], None],
    data: Optional[CardData] = None,
) -> None:
    """
    Render + send the EOD card to ONE channel (bot_token, chat_id). On ANY
    failure, call text_fallback(bot_token, chat_id) exactly once.

    `data` may be passed in (built once by the scheduler and reused across
    channels). If omitted, it is built here. The card path is considered
    successful only when sendPhoto returns 200.
    """
    if not bot_token or not chat_id:
        write_audit_log("[TELEGRAM][CARD] missing bot_token/chat_id — skipping channel")
        return

    if data is None:
        try:
            data = build_card_data()
        except Exception as e:
            write_audit_log(f"[TELEGRAM][CARD] build_card_data failed: {e} — text fallback")
            text_fallback(bot_token, chat_id)
            return

    png = build_summary_card_png(data)
    if not png:
        write_audit_log("[TELEGRAM][CARD] render returned None — text fallback")
        text_fallback(bot_token, chat_id)
        return

    caption = (
        f"\U0001F4CA <b>Daily summary</b> \u00b7 {data.date_str}\n"
        f"Combined net P&amp;L: <b>{_fmt_headline(data.combined)}</b>\n"
        # ── GROSS_RECON ── broker's Positions page shows GROSS; the card is NET.
        f"LIVE reconciliation: gross {_fmt_headline(data.live_gross)} "
        f"(broker basis) · net {_fmt_headline(data.live_subtotal)} (after charges)"
    )

    if _send_photo(bot_token, chat_id, png, caption):
        write_audit_log(f"[TELEGRAM][CARD] daily summary card sent -> {chat_id}")
    else:
        write_audit_log("[TELEGRAM][CARD] sendPhoto failed — text fallback")
        text_fallback(bot_token, chat_id)


def build_card_data_once() -> Optional[CardData]:
    """
    Convenience for the scheduler: build CardData a single time to reuse across
    all channels. Returns None on failure (scheduler then routes every channel
    straight to its text fallback).
    """
    try:
        return build_card_data()
    except Exception as e:
        write_audit_log(f"[TELEGRAM][CARD] build_card_data_once failed: {e}")
        return None