# backend/app/jobs/test_kite_login_nudge.py
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
        "notifications": {"criticalAlerts": True, "personalAlerts": True},   # ── TG_PERSONAL_ALERTS_20261027 ──
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
