# backend/app/jobs/liveness_ping.py
#
# ── LIVENESS PING ── the app half of the dead-man's switch (2026-09-01)
# ============================================================================
# WHY THIS EXISTS. On 2026-09-01 the laptop lost Wi-Fi at 09:08 and stayed
# offline all day. Every on-box watchdog (relay monitor, BB/HA, VET) noticed
# and was correctly unable to tell anyone: a machine with no internet cannot
# send an alert. The alert therefore has to come from a host that is STILL
# online and notices the box went quiet — the license server droplet.
#
# THIS SIDE: POST a small liveness record to the license server every 60 s.
# THE OTHER SIDE: license_server/liveness_watch.py sends the user's OWN
# Telegram an alert if the pings stop for > 3 min on a trading day between
# 09:15 and 15:35 IST, and a recovery note when they resume.
#
# HARD RULES
#   * NEVER fails the app. Every path is try/except; transport errors are
#     rate-limited to ONE audit line per state change (ok→down, down→ok).
#     Missing key, missing Telegram, unreachable server: all silent no-ops.
#   * Telegram is OPTIONAL. Users without a bot get pinged anyway (the
#     admin still sees liveness on the droplet); they just get no alert.
#     Only the shared bot_token + the first ENABLED channel's chat_id is
#     sent — no message history, no strategy filters, nothing else.
#   * HOLIDAYS travel WITH the ping. The droplet has no copy of the NSE
#     calendar; the app sends its own upcoming holiday dates each beat, so
#     there is exactly ONE holiday list to maintain (market_hours.py).
#   * Same key/machine_id the license heartbeat uses — the droplet
#     validates the pair before storing anything.
# ============================================================================

from __future__ import annotations

import asyncio
import platform
import time
from datetime import date, timedelta
from typing import Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log

PING_EVERY_S = 60
TIMEOUT_S = 5.0


def _upcoming_holidays(days: int = 90) -> List[str]:
    """ISO dates of NSE holidays within the next `days` — from the app's
    single holiday source. Empty list on any problem (droplet then treats
    every weekday as trading, which errs toward MORE alerts, never fewer)."""
    try:
        from app.utils.market_hours import NSE_HOLIDAYS_DEFAULT
        today = date.today()
        horizon = today + timedelta(days=days)
        out = []
        for s in NSE_HOLIDAYS_DEFAULT:
            try:
                d = date.fromisoformat(str(s))
            except Exception:
                continue
            if today <= d <= horizon:
                out.append(d.isoformat())
        return sorted(out)
    except Exception:
        return []


def _telegram_target() -> Optional[Dict[str, str]]:
    """{bot_token, chat_id} for the first enabled channel, or None."""
    try:
        from app.api.telegram_api import load_telegram_config_from_file
        cfg = load_telegram_config_from_file() or {}
        token = str(cfg.get("bot_token") or "").strip()
        if not token:
            return None
        for ch in cfg.get("channels") or []:
            if ch.get("enabled") and str(ch.get("chat_id") or "").strip():
                return {"bot_token": token,
                        "chat_id": str(ch["chat_id"]).strip()}
    except Exception:
        pass
    return None


def _payload() -> Optional[Dict]:
    try:
        from app.license.license_client import (KEY_FILE, _read_text,
                                                get_machine_id)
        key = (_read_text(KEY_FILE) or "").strip()
        if not key:
            return None
        return {"key": key, "machine_id": get_machine_id(),
                "label": platform.node()[:64],
                "ts": int(time.time()),
                "telegram": _telegram_target(),
                "holidays": _upcoming_holidays()}
    except Exception:
        return None


def _post_once(payload: Dict) -> bool:
    try:
        import httpx
        from app.license.license_client import LICENSE_SERVER_URL
        r = httpx.post(f"{LICENSE_SERVER_URL}/liveness", json=payload,
                       timeout=TIMEOUT_S)
        return r.status_code < 500
    except Exception:
        return False


async def liveness_ping_loop() -> None:
    """Perpetual. Supervised by api_server like the license heartbeat."""
    write_audit_log("[LIVENESS] ping loop started (60s → license server)")
    up: Optional[bool] = None
    while True:
        try:
            p = _payload()
            if p is not None:
                ok = await asyncio.get_event_loop().run_in_executor(
                    None, _post_once, p)
                if ok != up:
                    up = ok
                    write_audit_log(
                        "[LIVENESS] server reachable — dead-man's switch armed"
                        + ("" if p.get("telegram") else
                           " (no Telegram configured: admin-side only)")
                        if ok else
                        "[LIVENESS] server unreachable — if this persists "
                        "during market hours the droplet will alert you")
        except Exception as e:                       # belt: never die
            write_audit_log(f"[LIVENESS] loop error swallowed: {e!r}")
        await asyncio.sleep(PING_EVERY_S)