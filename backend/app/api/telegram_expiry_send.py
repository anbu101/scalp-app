"""
EXPIRY CARD SENDER — expiry-day gate, caption, sendPhoto + text fallback.

app/api/telegram_expiry_send.py          ── EXPIRY_CARD_20260929 ──

Contract (per channel):
  1. ExpiryCardData is built ONCE by the scheduler (build_expiry_card_data_once)
     and passed to every channel.
  2. Render PNG. If render returns None -> send the text fallback (the same
     four period nets as a message) so the summary is never lost.
  3. sendPhoto with a caption carrying the four nets (Live · Paper for the
     week and for the month — never summed). If sendPhoto fails -> text
     fallback.
  4. If the data could not be built at all -> a one-line notice, so the
     absence of a card is visible rather than silent.

Expiry-day gate: is_expiry_day(day) is live_dte(day) == 0 — the same
resolver dte_lot_mult uses at the 09:15 day roll, so a Tuesday holiday's
Monday expiry is handled without special casing. None (calendar failure)
tells the scheduler to skip and log.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.api.telegram_api import send_telegram_message
from app.api.telegram_summary_send import _send_photo
from app.api.telegram_summary_card import _fmt_signed_rs
from app.api.telegram_expiry_card import ExpiryCardData, build_expiry_card_png
from app.api.telegram_expiry_data import build_expiry_card_data


def is_expiry_day(day: date) -> Optional[bool]:
    """True on the session the weekly contract expires (0 DTE), False otherwise,
    None if the calendar cannot be evaluated."""
    try:
        from app.engine.dte_live import live_dte
        dte = live_dte(day)
        return None if dte is None else (dte == 0)
    except Exception as e:
        write_audit_log(f"[EXPIRY_CARD] expiry-day check failed: {e!r}")
        return None


def build_expiry_card_data_once(now: Optional[int] = None, *, expiry: bool = True) -> Optional[ExpiryCardData]:
    try:
        return build_expiry_card_data(now, expiry=expiry)
    except Exception as e:
        write_audit_log(f"[EXPIRY_CARD] build_expiry_card_data failed: {e!r}")
        return None


def _books_line(data: ExpiryCardData, period: str) -> str:
    def one(label, book):
        pb = data.book(period, book)
        return f"{label} {_fmt_signed_rs(pb.net)}" if pb.traded else f"{label} no trades"
    return one("Live", "LIVE") + " \u00b7 " + one("Paper", "PAPER")


def expiry_caption(data: ExpiryCardData) -> str:
    """HTML caption for sendPhoto — shows in the notification preview."""
    return (f"\U0001F4C5 <b>Expiry summary</b> \u00b7 {data.date_str}\n"
            f"Week: <b>{_books_line(data, 'week')}</b>\n"
            f"Month: <b>{_books_line(data, 'month')}</b>")


def expiry_text(data: ExpiryCardData) -> str:
    """Text fallback when the card cannot be rendered or sent."""
    return expiry_caption(data) + "\n<i>(card unavailable \u2014 text fallback)</i>"


def send_expiry_summary_card(*, bot_token: str, chat_id: str,
                             data: Optional[ExpiryCardData]) -> bool:
    """Card-first, text fallback. Returns True only when sendPhoto succeeded."""
    if not bot_token or not chat_id:
        write_audit_log("[EXPIRY_CARD] missing bot_token/chat_id \u2014 skipping channel")
        return False
    if data is None:
        send_telegram_message(bot_token, chat_id,
                              "\U0001F4C5 <b>Expiry summary</b> could not be built \u2014 see the audit log.")
        return False

    png = build_expiry_card_png(data)
    if not png:
        write_audit_log("[EXPIRY_CARD] render returned None \u2014 text fallback")
        send_telegram_message(bot_token, chat_id, expiry_text(data))
        return False

    if _send_photo(bot_token, chat_id, png, expiry_caption(data)):
        write_audit_log(f"[EXPIRY_CARD] expiry summary card sent -> {chat_id}")
        return True
    write_audit_log("[EXPIRY_CARD] sendPhoto failed \u2014 text fallback")
    send_telegram_message(bot_token, chat_id, expiry_text(data))
    return False
