"""
EOD CARD SENDER — sendPhoto + fail-open fallback to the text summary.

app/api/telegram_summary_send.py   (proposed location)

send_daily_summary_card() is the single entry point the scheduler calls in
place of the old text-only path. Contract:

  1. Build CardData from the repos.
  2. Render PNG.  If render returns None  -> fall back to text summary.
  3. sendPhoto with a text caption carrying the combined headline (so the
     notification preview still shows the number — photos don't).
     If sendPhoto fails (network / API) -> fall back to text summary.

The fallback is the EXISTING notify_daily_summary() + the scheduler's advanced
paper / V3 summaries, passed in as a callable so this module stays decoupled
and the old behaviour is preserved byte-for-byte when anything goes wrong.
"""

from __future__ import annotations

import requests

from app.api.telegram_api import TELEGRAM_CONFIG
from app.event_bus.audit_logger import write_audit_log
from app.api.telegram_summary_card import build_summary_card_png, _fmt_headline
from app.api.telegram_summary_data import build_card_data


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


def send_daily_summary_card(*, text_fallback) -> None:
    """
    Render + send the EOD card. On ANY failure, call text_fallback() (a
    zero-arg callable that sends the existing text summary). text_fallback
    is invoked exactly once if the card path does not succeed.

    The card path is considered successful only when sendPhoto returns 200.
    """
    if not TELEGRAM_CONFIG:
        return

    bot_token = TELEGRAM_CONFIG.get("bot_token", "")
    chat_id   = TELEGRAM_CONFIG.get("chat_id", "")
    if not bot_token or not chat_id:
        write_audit_log("[TELEGRAM][CARD] no bot_token/chat_id — skipping")
        return

    try:
        data = build_card_data()
    except Exception as e:
        write_audit_log(f"[TELEGRAM][CARD] build_card_data failed: {e} — text fallback")
        text_fallback()
        return

    png = build_summary_card_png(data)
    if not png:
        write_audit_log("[TELEGRAM][CARD] render returned None — text fallback")
        text_fallback()
        return

    caption = (
        f"\U0001F4CA <b>Daily summary</b> \u00b7 {data.date_str}\n"
        f"Combined net P&amp;L: <b>{_fmt_headline(data.combined)}</b>"
    )

    if _send_photo(bot_token, chat_id, png, caption):
        write_audit_log("[TELEGRAM][CARD] daily summary card sent")
    else:
        write_audit_log("[TELEGRAM][CARD] sendPhoto failed — text fallback")
        text_fallback()