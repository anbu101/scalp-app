#!/usr/bin/env python3
# apply_kite_login_nudge.py — KITE_LOGIN_NUDGE_20261027
#
# One-tap morning Kite login from the phone (Option 2):
#   * 08:30 IST on trading days, if Kite is not logged in, the existing
#     broker-session watch (BROKER_SESSION_WATCH_20261022) sends ONE friendly
#     nudge — bell + Telegram — with a "🔑 Log in to Kite" button.
#   * Every not-logged-in alert it already sends (08:45 warning, 09:10
#     critical, mid-day "session LOST") now carries the same button.
#   * Tap → Kite login page → Kite redirects to /zerodha/callback, which the
#     app already publishes via Tailscale Funnel (TS_FUNNEL_SCOPE_20261017)
#     → the existing callback saves the token → next tick confirms
#     ("Kite logged in at 08:34 — session ready for the open").
#   * POST /api/telegram/debug/send-kite-login sends the nudge on demand
#     (any day/time) so the whole phone flow can be rehearsed on a weekend.
#   * Telegram goes only to channels configured on THIS install with
#     critical alerts on — non-admin installs (no Telegram) get the bell only.
#   * The api_secret never leaves the machine; the button holds only the
#     public api_key (it is in every Kite login URL).
#
# Touches (both trees — desktop/src-tauri/backend too when present):
#   backend/app/api/telegram_api.py        optional reply_markup / buttons /
#                                          title / emoji; debug route
#   backend/app/jobs/broker_session_watch.py  nudge + button + rehearsal fn
#   backend/app/jobs/test_kite_login_nudge.py NEW scenario suite
#
# USAGE (repo root):
#   python3 apply_kite_login_nudge.py --dry-run   # checks + tests, writes nothing
#   python3 apply_kite_login_nudge.py             # apply
#   python3 apply_kite_login_nudge.py --revert    # restore .bak-KITE_LOGIN_NUDGE_20261027
# Then: ./desktop/build-scalp.sh backend   (backend-only change)
import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "KITE_LOGIN_NUDGE_20261027"
BAK = ".bak-" + FENCE
MIRROR = Path("desktop/src-tauri/backend")
TEST_REL = "backend/app/jobs/test_kite_login_nudge.py"

# ════════════════════════════════════════════════════════════════════════════
#  EDITS — (old, new); every old must occur exactly once in the file
# ════════════════════════════════════════════════════════════════════════════
TG_EDITS = [
# 1) core sender: optional inline keyboard
("""def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:
""",
"""def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML",
                          reply_markup: Optional[dict] = None) -> bool:
"""),
("""    payload = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode}
""",
"""    payload = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode}
    # ── KITE_LOGIN_NUDGE_20261027 ── optional inline keyboard (URL buttons)
    if reply_markup:
        payload["reply_markup"] = reply_markup
"""),
# 2) fan-out passes it through — and ONLY when present, so existing
#    fixed-signature test mocks of send_telegram_message keep working
("""def _fanout(notif_key: str, message: str, *,
            mode: Optional[str] = None,
            strategy_id: Optional[str] = None):
    \"\"\"Send `message` to every channel that wants a `notif_key` alert.\"\"\"
    sent = 0
    for token, chat_id, _ch in _iter_active_channels(
        notif_key, mode=mode, strategy_id=strategy_id
    ):
        if send_telegram_message(token, chat_id, message):
            sent += 1
    return sent
""",
"""def _fanout(notif_key: str, message: str, *,
            mode: Optional[str] = None,
            strategy_id: Optional[str] = None,
            reply_markup: Optional[dict] = None):
    \"\"\"Send `message` to every channel that wants a `notif_key` alert.\"\"\"
    sent = 0
    # ── KITE_LOGIN_NUDGE_20261027 ── kwarg only when there is a keyboard
    _kw = {"reply_markup": reply_markup} if reply_markup else {}
    for token, chat_id, _ch in _iter_active_channels(
        notif_key, mode=mode, strategy_id=strategy_id
    ):
        if send_telegram_message(token, chat_id, message, **_kw):
            sent += 1
    return sent
"""),
# 3) helper: alert_data["buttons"] → inline keyboard
("""def notify_critical(alert_data: dict):
""",
"""# ── KITE_LOGIN_NUDGE_20261027 ──
def _buttons_markup(alert_data) -> Optional[dict]:
    \"\"\"alert_data["buttons"] = [{"text": .., "url": "https://.."}] -> one URL
    button per row, or None. Non-https / empty entries are dropped.\"\"\"
    try:
        rows = []
        for b in (alert_data or {}).get("buttons") or []:
            text = str((b or {}).get("text") or "").strip()
            url = str((b or {}).get("url") or "").strip()
            if text and url.startswith("https://"):
                rows.append([{"text": text, "url": url}])
        return {"inline_keyboard": rows} if rows else None
    except Exception:
        return None


def notify_critical(alert_data: dict):
"""),
# 4) critical alerts may carry buttons
("""{alert_data.get('message', '')}

Time: {datetime.now().strftime('%H:%M:%S')}
\"\"\".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message, strategy_id=strategy_id)
""",
"""{alert_data.get('message', '')}

Time: {datetime.now().strftime('%H:%M:%S')}
\"\"\".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message, strategy_id=strategy_id,
            reply_markup=_buttons_markup(alert_data))   # ── KITE_LOGIN_NUDGE_20261027 ──
"""),
# 5) system alerts: optional title / emoji / buttons
("""    message = f\"\"\"
{emoji} <b>SYSTEM ALERT</b>

{alert_data.get('message')}

Time: {datetime.now().strftime('%H:%M:%S')}
\"\"\".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message)
""",
"""    # ── KITE_LOGIN_NUDGE_20261027 ── optional title / emoji / URL buttons
    emoji = alert_data.get("emoji") or emoji
    title = alert_data.get("title") or "SYSTEM ALERT"
    message = f\"\"\"
{emoji} <b>{title}</b>

{alert_data.get('message')}

Time: {datetime.now().strftime('%H:%M:%S')}
\"\"\".strip()

    _fanout(NOTIF_CRITICAL_ALERTS, message,
            reply_markup=_buttons_markup(alert_data))
"""),
# 6) on-demand rehearsal route
("""    return {"success": True, "message": "Critical alert dispatched to all eligible channels."}
""",
"""    return {"success": True, "message": "Critical alert dispatched to all eligible channels."}


# ── KITE_LOGIN_NUDGE_20261027 ── send the morning Kite-login nudge NOW (any
# day, any time) so the Telegram button → phone login → callback flow can be
# rehearsed outside market hours.
@router.post("/debug/send-kite-login")
async def debug_send_kite_login():
    from app.jobs.broker_session_watch import send_login_nudge
    ok, detail = send_login_nudge()
    return {"success": ok, "message": detail}
"""),
]

BSW_EDITS = [
("""# check so a fresh morning login is seen immediately. Never raises.
""",
"""# check so a fresh morning login is seen immediately. Never raises.
#
# ── KITE_LOGIN_NUDGE_20261027 ──
#   * 08:30 nudge (once) when Kite is not logged in — bell + Telegram — with a
#     "Log in to Kite" URL button. Every not-logged-in alert (nudge, 08:45
#     warning, 09:10 critical, mid-day LOST) carries the same button, so the
#     login can be done from the phone. Kite redirects to /zerodha/callback,
#     which Tailscale Funnel already publishes (TS_FUNNEL_SCOPE_20261017); the
#     existing callback saves the token and the next tick confirms.
#   * Telegram reaches only the channels configured on this install with
#     critical alerts on; installs without Telegram get the bell.
#   * send_login_nudge() = on-demand rehearsal (debug route).
"""),
("""WINDOW_START_MIN = 8 * 60 + 45
""",
"""WINDOW_START_MIN = 8 * 60 + 45
NUDGE_MIN = 8 * 60 + 30                  # ── KITE_LOGIN_NUDGE_20261027 ──
LOGIN_BUTTON_TEXT = "🔑 Log in to Kite"
"""),
("""            "lost": False, "td_warned": False}
""",
"""            "lost": False, "td_warned": False,
            "nudged": False}             # ── KITE_LOGIN_NUDGE_20261027 ──
"""),
("""    out: List[Tuple[str, str]] = []
    if not (WINDOW_START_MIN <= now_min <= WINDOW_END_MIN):
        return out
""",
"""    out: List[Tuple[str, str]] = []
    if not (NUDGE_MIN <= now_min <= WINDOW_END_MIN):   # ── KITE_LOGIN_NUDGE_20261027 ──
        return out
"""),
("""    if trade_ready:
        if state["warned"] or state["critical"] or state["lost"] or state["td_warned"]:
            out.append(("info", f"Kite session ready at {_hm(now_min)} — "
                                f"strategies can price and trade again."))
            state.update(warned=False, critical=False, lost=False, td_warned=False)
        state["was_ready"] = True
        return out
""",
"""    if trade_ready:
        if state["warned"] or state["critical"] or state["lost"] or state["td_warned"]:
            out.append(("info", f"Kite session ready at {_hm(now_min)} — "
                                f"strategies can price and trade again."))
        # ── KITE_LOGIN_NUDGE_20261027 ── a login after the morning nudge (and
        # nothing worse) gets its own short confirmation.
        elif state.get("nudged"):
            out.append(("info", f"Kite logged in at {_hm(now_min)} — session "
                                f"ready for the open."))
        state.update(warned=False, critical=False, lost=False, td_warned=False,
                     nudged=False)
        state["was_ready"] = True
        return out
"""),
("""        if live_ids and not state["td_warned"]:
""",
"""        if live_ids and not state["td_warned"] and now_min >= WINDOW_START_MIN:
"""),
("""    if now_min >= ESCALATE_MIN:
        if not state["critical"]:
""",
"""    # ── KITE_LOGIN_NUDGE_20261027 ── 08:30–08:44: one friendly nudge
    if now_min < WINDOW_START_MIN:
        if not state.get("nudged"):
            state["nudged"] = True
            out.append(("info", f"Good morning — Kite is not logged in yet "
                                f"({_hm(now_min)}). Log in before 09:15; the "
                                f"app picks the session up by itself."))
        return out
    if now_min >= ESCALATE_MIN:
        if not state["critical"]:
"""),
("""def _emit(severity: str, msg: str) -> None:
    write_audit_log(f"[BROKER_WATCH][{severity.upper()}] {msg}")
    if severity == "critical":
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"message": msg, "severity": "error"})   # mirrors to bell
        except Exception as e:
            write_audit_log(f"[BROKER_WATCH] critical notify failed: {e!r}")
        return
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert("BROKER_SESSION", msg, severity=severity)
    except Exception:
        pass
    try:
        from app.api.telegram_api import notify_system_alert
        notify_system_alert({"message": msg, "severity": severity})
    except Exception as e:
        write_audit_log(f"[BROKER_WATCH] notify failed: {e!r}")
""",
"""# ── KITE_LOGIN_NUDGE_20261027 BEGIN ──
def _login_url() -> Optional[str]:
    \"\"\"Kite Connect login URL for this install's API key, or None. Never
    raises. The api_key is public by design (it is in every login URL); the
    api_secret never leaves this machine.\"\"\"
    try:
        from app.config.zerodha_credentials_store import load_credentials
        creds = load_credentials() or {}
        api_key = str(creds.get("api_key") or "").strip()
        if not api_key:
            return None
        from kiteconnect import KiteConnect
        url = str(KiteConnect(api_key=api_key).login_url())
        return url if url.startswith("https://") else None
    except Exception as e:
        write_audit_log(f"[BROKER_WATCH] login url unavailable: {e!r}")
        return None
# ── KITE_LOGIN_NUDGE_20261027 END ──


def _emit(severity: str, msg: str, login_url: Optional[str] = None) -> None:
    write_audit_log(f"[BROKER_WATCH][{severity.upper()}] {msg}"
                    + (" [login button]" if login_url else ""))
    buttons = ([{"text": LOGIN_BUTTON_TEXT, "url": login_url}]
               if login_url else None)           # ── KITE_LOGIN_NUDGE_20261027 ──
    if severity == "critical":
        try:
            from app.api.telegram_api import notify_critical
            payload = {"message": msg, "severity": "error"}
            if buttons:
                payload["buttons"] = buttons
            notify_critical(payload)   # mirrors to bell
        except Exception as e:
            write_audit_log(f"[BROKER_WATCH] critical notify failed: {e!r}")
        return
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert("BROKER_SESSION", msg, severity=severity)
    except Exception:
        pass
    try:
        from app.api.telegram_api import notify_system_alert
        payload = {"message": msg, "severity": severity}
        if buttons:
            payload.update(buttons=buttons, title="KITE LOGIN", emoji="🔑")
        notify_system_alert(payload)
    except Exception as e:
        write_audit_log(f"[BROKER_WATCH] notify failed: {e!r}")
"""),
("""        if not (WINDOW_START_MIN <= now_min <= WINDOW_END_MIN):
            return
""",
"""        if not (NUDGE_MIN <= now_min <= WINDOW_END_MIN):   # ── KITE_LOGIN_NUDGE_20261027 ──
            return
"""),
("""        for sev, msg in alerts:
            _emit(sev, msg)
""",
"""        # ── KITE_LOGIN_NUDGE_20261027 ── not logged in → every alert carries
        # the Kite login button (nudge, warning, critical, LOST).
        url = _login_url() if (alerts and not logged_in) else None
        for sev, msg in alerts:
            _emit(sev, msg, login_url=url)
"""),
]

BSW_APPEND = '''

# ── KITE_LOGIN_NUDGE_20261027 ── on-demand rehearsal
# (POST /api/telegram/debug/send-kite-login). Ignores the calendar, the clock
# and the once-per-day state. Never raises.
def send_login_nudge() -> Tuple[bool, str]:
    try:
        url = _login_url()
        if not url:
            return False, ("No Zerodha API key configured (Connections page) — "
                           "there is no login link to send.")
        try:
            from app.brokers.zerodha_auth import is_token_valid
            logged_in = bool(is_token_valid())
        except Exception:
            logged_in = False
        if logged_in:
            msg = ("Kite login link (test send). You are already logged in — "
                   "no need to tap it now.")
        else:
            msg = ("Kite login link (test send). Tap the button, log in, and "
                   "the app picks the session up by itself.")
        _emit("info", msg, login_url=url)
        return True, ("Sent to the bell and to every Telegram channel with "
                      "critical alerts on (if its schedule window allows).")
    except Exception as e:
        return False, f"send failed: {e!r}"
'''

TARGETS = [
    ("backend/app/api/telegram_api.py", TG_EDITS, "",
     ["TEST_ALERT_GUARD_20261015", "CRITICAL_ALERT_ROUTING_20260909"]),
    ("backend/app/jobs/broker_session_watch.py", BSW_EDITS, BSW_APPEND,
     ["BROKER_SESSION_WATCH_20261022"]),
]
# guard the callback path the phone login relies on
PREREQ = [("backend/app/utils/funnel_guard.py", "TS_FUNNEL_SCOPE_20261017"),
          ("backend/app/api/zerodha_routes.py", '@router.get("/callback")')]

TEST_SRC = r'''# backend/app/jobs/test_kite_login_nudge.py
# ── KITE_LOGIN_NUDGE_20261027 ── scenario suite for the morning Kite-login
# nudge + Telegram URL button. Hermetic: run it with HOME pointed at a temp
# folder (the apply script does); Telegram's network sink is replaced by a
# recorder and the file name (test_*) trips TEST_ALERT_GUARD as a backstop.
#
#   cd backend && HOME=$(mktemp -d) python3 app/jobs/test_kite_login_nudge.py
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))          # .../backend

os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.api.telegram_api as TG                  # noqa: E402
import app.jobs.broker_session_watch as B         # noqa: E402
import app.event_bus.inapp_events as IE           # noqa: E402
import app.utils.market_hours as MH               # noqa: E402
import app.brokers.zerodha_auth as ZA             # noqa: E402
from app.config import zerodha_credentials_store as CS   # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append(f"{name} {detail}")
        print(f"  FAIL  {name}  {detail}")


# ── recorders ───────────────────────────────────────────────────────────────
SENT, BELL = [], []


def _rec_send(token, chat_id, message, parse_mode="HTML", reply_markup=None):
    SENT.append({"chat": chat_id, "msg": message, "markup": reply_markup})
    return True


_ORIG_SEND = TG.send_telegram_message           # the real network sink
TG.send_telegram_message = _rec_send
IE.record_alert = lambda code, msg, **k: BELL.append((code, msg, k))
IE.recent_alert_within = lambda *a, **k: False
TG.TELEGRAM_CONFIG = {
    "bot_token": "T",
    "channels": [{
        "id": "c1", "name": "Admin", "chat_id": "42", "enabled": True,
        "strategy_filter": ["all"], "mode_filter": "all",
        "notifications": {"criticalAlerts": True},
        "schedule": {"enabled": False},
    }],
}
API_KEY = "testkey123"
LOGIN_URL = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"


def _creds(on=True):
    if on:
        CS.CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CS.CREDENTIALS_PATH.write_text(json.dumps(
            {"api_key": API_KEY, "api_secret": "never-leaves-this-machine"}))
    elif CS.CREDENTIALS_PATH.exists():
        CS.CREDENTIALS_PATH.unlink()


def _reset():
    SENT.clear()
    BELL.clear()
    B._STATE.clear()


def _st():
    return B._fresh_state("2026-10-28")


def _ev(m, **kw):
    a = dict(logged_in=False, trade_ready=False, live_ids=[], open_summary="")
    a.update(kw)
    return a


# ── 1) pure decision ───────────────────────────────────────────────────────
s = _st()
check("pure: 08:29 silent", B.evaluate(8 * 60 + 29, state=s, **_ev(0)) == [])
out = B.evaluate(8 * 60 + 30, state=s, **_ev(0))
check("pure: 08:30 nudge once", len(out) == 1 and out[0][0] == "info"
      and "not logged in" in out[0][1], repr(out))
check("pure: nudge sets state", s["nudged"] is True)
check("pure: 08:32 no repeat", B.evaluate(8 * 60 + 32, state=s, **_ev(0)) == [])
out = B.evaluate(8 * 60 + 45, state=s, **_ev(0))
check("pure: 08:45 warning still fires", len(out) == 1 and out[0][0] == "warning", repr(out))
out = B.evaluate(9 * 60 + 10, state=s, **_ev(0))
check("pure: 09:10 critical still fires", len(out) == 1 and out[0][0] == "critical", repr(out))
out = B.evaluate(9 * 60 + 12, state=s, **_ev(0, logged_in=True, trade_ready=True))
check("pure: recovery after critical = one 'ready' msg",
      len(out) == 1 and "ready" in out[0][1] and "again" in out[0][1], repr(out))
check("pure: recovery clears nudged", s["nudged"] is False)

s = _st()
B.evaluate(8 * 60 + 30, state=s, **_ev(0))
out = B.evaluate(8 * 60 + 34, state=s, **_ev(0, logged_in=True, trade_ready=True))
check("pure: login after nudge = confirmation",
      len(out) == 1 and out[0][0] == "info" and "logged in at 08:34" in out[0][1], repr(out))
check("pure: no second confirmation",
      B.evaluate(8 * 60 + 36, state=s, **_ev(0, logged_in=True, trade_ready=True)) == [])

s = _st()
check("pure: already logged in at 08:30 -> silent",
      B.evaluate(8 * 60 + 30, state=s, **_ev(0, logged_in=True, trade_ready=True)) == [])

s = _st()
out = B.evaluate(8 * 60 + 30, state=s, **_ev(0, logged_in=True, live_ids=["TSG_V1"]))
check("pure: trading-disabled warning NOT moved earlier (08:30 silent)", out == [], repr(out))
out = B.evaluate(8 * 60 + 45, state=s, **_ev(0, logged_in=True, live_ids=["TSG_V1"]))
check("pure: trading-disabled warning at 08:45 as before",
      len(out) == 1 and "DISABLED" in out[0][1], repr(out))

# ── 2) the job on the real clock, through the real Telegram fan-out ───────
IST = B.IST


class _FakeDT(datetime):
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


B.datetime = _FakeDT
MH.is_trading_day = lambda *a, **k: True
_valid = [False]
ZA.is_token_valid = lambda: _valid[0]


class _Mgr:
    ready = False

    def get_kite(self):
        return None

    def is_trade_ready(self):
        return self.ready


mgr = _Mgr()


def _tick(h, m, day=28):
    _FakeDT._now = datetime(2026, 10, day, h, m, 5, tzinfo=IST)
    B.broker_session_watch_job(mgr)


def _buttons(sent):
    mk = sent.get("markup") or {}
    return [b for row in mk.get("inline_keyboard", []) for b in row]


_creds(True)
_reset()
_tick(8, 28)
check("job: 08:28 silent", SENT == [] and BELL == [])
_tick(8, 30)
check("job: 08:30 one Telegram message", len(SENT) == 1, repr(SENT))
if SENT:
    btn = _buttons(SENT[0])
    check("job: nudge carries the login button",
          len(btn) == 1 and btn[0]["url"] == LOGIN_URL, repr(btn))
    check("job: nudge titled KITE LOGIN", "KITE LOGIN" in SENT[0]["msg"], SENT[0]["msg"])
    check("job: api secret never in Telegram",
          "never-leaves" not in json.dumps(SENT[0]), "")
check("job: nudge also on the bell", len(BELL) == 1 and BELL[0][0] == "BROKER_SESSION", repr(BELL))
_tick(8, 32)
check("job: 08:32 no repeat", len(SENT) == 1)
_tick(8, 46)
check("job: 08:46 warning with button", len(SENT) == 2 and len(_buttons(SENT[1])) == 1, repr(SENT[-1:]))
_tick(9, 10)
check("job: 09:10 critical with button",
      len(SENT) == 3 and "CRITICAL" in SENT[2]["msg"] and _buttons(SENT[2])
      and _buttons(SENT[2])[0]["url"] == LOGIN_URL, repr(SENT[-1:]))
mgr.ready, _valid[0] = True, True
_tick(9, 12)
check("job: recovery message sent", len(SENT) == 4 and "ready" in SENT[3]["msg"], repr(SENT[-1:]))
check("job: recovery message has NO button", len(SENT) == 4 and not _buttons(SENT[3]))

# mid-day loss → critical with button
mgr.ready, _valid[0] = False, False
_tick(11, 0)
check("job: mid-day LOST critical with button",
      len(SENT) == 5 and "LOST" in SENT[4]["msg"] and _buttons(SENT[4]), repr(SENT[-1:]))

# login straight after the nudge → short confirmation, no button
_reset()
mgr.ready, _valid[0] = False, False
_tick(8, 30, day=29)
mgr.ready, _valid[0] = True, True
_tick(8, 34, day=29)
check("job: confirmation after phone login",
      len(SENT) == 2 and "logged in at 08:34" in SENT[1]["msg"] and not _buttons(SENT[1]),
      repr(SENT))

# no API key configured → alerts still go out, just without a button
_creds(False)
_reset()
mgr.ready, _valid[0] = False, False
_tick(8, 30, day=30)
check("job: no credentials -> nudge still sent", len(SENT) == 1)
check("job: no credentials -> no button", len(SENT) == 1 and not _buttons(SENT[0]))
_creds(True)

# non-trading day → nothing at all
_reset()
MH.is_trading_day = lambda *a, **k: False
_tick(8, 30, day=31)
check("job: holiday silent", SENT == [] and BELL == [])
MH.is_trading_day = lambda *a, **k: True

# ── 3) Telegram plumbing ───────────────────────────────────────────────────
mk = TG._buttons_markup({"buttons": [{"text": "Go", "url": LOGIN_URL},
                                     {"text": "Bad", "url": "http://x"},
                                     {"text": "", "url": LOGIN_URL}]})
check("tg: only https buttons with text survive",
      mk == {"inline_keyboard": [[{"text": "Go", "url": LOGIN_URL}]]}, repr(mk))
check("tg: no buttons -> None", TG._buttons_markup({}) is None
      and TG._buttons_markup({"buttons": []}) is None)

# old-style fixed-signature mocks keep working (no surprise kwargs)
calls = []
TG.send_telegram_message = lambda tok, chat, msg, parse_mode="HTML": (calls.append(msg) or True)
try:
    TG.notify_system_alert({"message": "plain", "severity": "info"})
    ok = len(calls) == 1
except TypeError as e:
    ok = False
check("tg: buttonless alert passes no reply_markup kwarg", ok, repr(calls))
TG.send_telegram_message = _rec_send

# the network payload itself (network replaced by a recorder)
import app.utils.test_guard as TGD          # noqa: E402
posted = []


class _Resp:
    status_code = 200


_real_post, _real_block = TG.requests.post, TGD.outbound_blocked
TG.requests.post = lambda url, json=None, timeout=None: (posted.append(json) or _Resp())
TGD.outbound_blocked = lambda: False
try:
    _ORIG_SEND("T", "42", "hi",
               reply_markup={"inline_keyboard": [[{"text": "Go", "url": LOGIN_URL}]]})
    _ORIG_SEND("T", "42", "hi")
finally:
    TG.requests.post, TGD.outbound_blocked = _real_post, _real_block
check("tg: reply_markup lands in the sendMessage payload",
      len(posted) == 2 and posted[0].get("reply_markup", {}).get("inline_keyboard"), repr(posted))
check("tg: no reply_markup key when none given", len(posted) == 2 and "reply_markup" not in posted[1])

# on-demand rehearsal
_reset()
_valid[0] = False
ok, detail = B.send_login_nudge()
check("rehearsal: returns ok", ok is True, detail)
check("rehearsal: Telegram with button", len(SENT) == 1 and _buttons(SENT[0])
      and _buttons(SENT[0])[0]["url"] == LOGIN_URL, repr(SENT))
_creds(False)
ok, detail = B.send_login_nudge()
check("rehearsal: no key -> refused with reason", ok is False and "API key" in detail, detail)
_creds(True)

paths = {(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", []) or [])))
         for r in TG.router.routes}
check("route: POST /api/telegram/debug/send-kite-login registered",
      ("/api/telegram/debug/send-kite-login", ("POST",)) in paths)

total = PASSES[0] + len(FAILS)
if FAILS:
    print(f"{len(FAILS)} of {total} checks FAILED")
    sys.exit(1)
print(f"{total}/{total} checks passed")
'''


def die(msg, code=2):
    print("\nABORTED — nothing written.\n  " + msg)
    sys.exit(code)


def find_root(arg):
    cands = ([Path(arg)] if arg else []) + [Path.cwd(), Path(__file__).resolve().parent]
    for c in cands:
        if (c / "backend/app/jobs/broker_session_watch.py").exists():
            return c.resolve()
    die("Could not find the repo root (folder containing backend/app). "
        "Run from the repo root or pass --repo PATH.")


def trees(root, rel):
    """backend path + desktop mirror (when that copy exists)."""
    out = [root / rel]
    m = root / MIRROR / Path(rel).relative_to("backend")
    if m.exists():
        out.append(m)
    return out


def git_dirty(root, rel):
    if not (root / ".git").exists() or not shutil.which("git"):
        return False
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", rel],
                       capture_output=True, text=True)
    return any(l[:2].strip() for l in r.stdout.splitlines())


def patch(text, edits, append, label):
    for i, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            die(f"{label}: anchor #{i} found {n}× (expected 1) — the file is not "
                f"the version this script was written against. Push it to GitHub "
                f"and ask for a rebase.\n  anchor starts: {old.strip().splitlines()[0][:80]}")
        text = text.replace(old, new, 1)
    return text + append


def run_suite(root, staged):
    tmp = Path(tempfile.mkdtemp(prefix="kln_suite_"))
    try:
        shutil.copytree(root / "backend/app", tmp / "backend/app",
                        ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "*.pyc"))
        for rel, txt in staged.items():
            if rel.startswith("backend/"):
                (tmp / rel).write_text(txt, encoding="utf-8")
        (tmp / TEST_REL).write_text(TEST_SRC, encoding="utf-8")
        home = tmp / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   SCALP_TEST_MODE="1", PYTHONPATH=str(tmp / "backend"))
        r = subprocess.run([sys.executable, "app/jobs/test_kite_login_nudge.py"],
                           cwd=tmp / "backend", env=env, capture_output=True,
                           text=True, timeout=300)
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def revert(root):
    restored = 0
    for rel, *_ in TARGETS:
        for p in trees(root, rel):
            b = p.with_name(p.name + BAK)
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
                restored += 1
                print(f"  restored  {p.relative_to(root)}")
    for p in trees(root, TEST_REL):
        if p.exists() and FENCE in p.read_text(encoding="utf-8", errors="ignore"):
            p.unlink()
            print(f"  removed   {p.relative_to(root)}")
    print(f"\nRevert done ({restored} file(s) restored). Rebuild the backend.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="patch even if git shows local modifications")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"{FENCE}\nRepo: {root}")
    if a.revert:
        return revert(root)

    # prerequisites
    for rel, marker in PREREQ:
        p = root / rel
        if not p.exists() or marker not in p.read_text(encoding="utf-8"):
            die(f"prerequisite missing: {marker} in {rel}")

    # state: all-applied / none-applied / mixed
    files = [p for rel, *_ in TARGETS for p in trees(root, rel)]
    fenced = [p for p in files if FENCE in p.read_text(encoding="utf-8")]
    if fenced and len(fenced) == len(files):
        print("Already applied — nothing to do.")
        return 0
    if fenced:
        die("mixed state — some files already carry the fence:\n  "
            + "\n  ".join(str(p.relative_to(root)) for p in fenced)
            + "\n  Run with --revert first, then re-run.")

    staged = {}
    for rel, edits, append, parents in TARGETS:
        if git_dirty(root, rel) and not a.allow_dirty:
            die(f"{rel} has local modifications (git status shows M). Commit or "
                f"stash first, or re-run with --allow-dirty if that is intended.")
        src = (root / rel).read_text(encoding="utf-8")
        for pf in parents:
            if pf not in src:
                die(f"{rel}: parent fence {pf} not found — apply it first.")
        staged[rel] = patch(src, edits, append, rel)
        for m in trees(root, rel)[1:]:
            msrc = m.read_text(encoding="utf-8")
            if msrc != src:
                print(f"  note: {m.relative_to(root)} differs from backend/ — "
                      f"it will be replaced by the patched backend copy "
                      f"(build-scalp.sh would sync it anyway).")
    staged[TEST_REL] = TEST_SRC

    # compile gate
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in staged.items():
            t = Path(td) / Path(rel).name
            t.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(t), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}:\n{e}")
    print("  py_compile: OK")

    # behaviour gate
    print("  running scenario suite…")
    ok, out = run_suite(root, staged)
    tail = "\n".join(out.splitlines()[-25:])
    print("    " + tail.replace("\n", "\n    "))
    if not ok:
        die("scenario suite failed (see above).")

    if a.dry_run:
        for rel in staged:
            for p in trees(root, rel) if (root / rel).exists() else [root / rel]:
                print(f"  would write  {p.relative_to(root)}")
        print("\nDry run OK — nothing written.")
        return 0

    # staged, all-or-nothing write
    writes = []
    for rel, txt in staged.items():
        dests = trees(root, rel) if (root / rel).exists() else [root / rel]
        if rel == TEST_REL and (root / MIRROR / "app/jobs").exists():
            dests = [root / rel, root / MIRROR / "app/jobs/test_kite_login_nudge.py"]
        for d in dests:
            writes.append((d, txt))
    backups = []
    try:
        for d, _ in writes:
            if d.exists():
                b = d.with_name(d.name + BAK)
                shutil.copy2(d, b)
                backups.append((d, b))
        for d, txt in writes:
            tmpf = d.with_name(d.name + ".tmp-" + FENCE)
            tmpf.write_text(txt, encoding="utf-8")
            os.replace(tmpf, d)
            print(f"  wrote  {d.relative_to(root)}")
    except Exception as e:
        for d, b in backups:
            shutil.copy2(b, d)
        for d, _ in writes:
            if not any(d == x for x, _ in backups) and d.exists():
                d.unlink()
        die(f"write failed ({e!r}) — every file restored.")

    print(f"""
Done. Next:
  1. ./desktop/build-scalp.sh backend    (backend-only change), restart the app.
  2. One-time: on developers.kite.trade set the app's Redirect URL to your
     Funnel callback  https://<your-machine>.ts.net/zerodha/callback
     (check: `tailscale funnel status` lists /zerodha/callback).
  3. Rehearse any time:
       curl -X POST http://127.0.0.1:47321/api/telegram/debug/send-kite-login
     tap the button on your phone, log in, watch the Connections page flip.
  4. If your Telegram channel has a schedule window, its start must be ≤ 08:30
     or the 08:30/08:45 messages are held back by design.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
