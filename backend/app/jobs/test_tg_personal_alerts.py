# backend/app/jobs/test_tg_personal_alerts.py
# ── TG_PERSONAL_ALERTS_20261027 ── personal alerts (Kite login, liveness) go
# ONLY to channels with "Personal Alerts" on — your own chat — never to the
# group/channel that gets trade summaries. Hermetic: run with HOME pointed at
# a temp folder; Telegram's network sink is replaced by a recorder.
#
#   cd backend && HOME=$(mktemp -d) python3 app/jobs/test_tg_personal_alerts.py
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
ROOT = HERE.parents[3]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.api.telegram_api as TG                  # noqa: E402
import app.jobs.broker_session_watch as B         # noqa: E402
import app.jobs.liveness_ping as LP               # noqa: E402
import app.event_bus.inapp_events as IE           # noqa: E402
import app.utils.market_hours as MH               # noqa: E402
import app.brokers.zerodha_auth as ZA             # noqa: E402
from app.config import zerodha_credentials_store as CS   # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


ME, GROUP = "123456789", "-1001987654321"
SENT = []
TG.send_telegram_message = (lambda tok, chat, msg, parse_mode="HTML", reply_markup=None:
                            SENT.append({"chat": chat, "msg": msg, "markup": reply_markup}) or True)
IE.record_alert = lambda *a, **k: None
IE.recent_alert_within = lambda *a, **k: False


def chan(i, chat, **notif):
    n = {"tradeActivity": True, "positionUpdates": False,
         "dailySummary": True, "criticalAlerts": True}
    n.update(notif)
    return {"id": f"channel_{i}", "name": f"c{i}", "chat_id": chat, "enabled": True,
            "strategy_filter": [], "mode_filter": "all", "notifications": n,
            "schedule": {"enabled": False}}


def chats():
    return sorted({s["chat"] for s in SENT})


# ── 1) which chats count as "yours" ────────────────────────────────────────
check("private: positive id", TG._is_private_chat("123456789"))
check("private: group id is not", not TG._is_private_chat("-1001234567890"))
check("private: small group id is not", not TG._is_private_chat("-4567"))
check("private: @username is not", not TG._is_private_chat("@marketmakers"))
check("private: blank is not", not TG._is_private_chat(""))

# ── 2) migration of an existing config (no personalAlerts key yet) ─────────
legacy = {"bot_token": "T", "channels": [chan(1, GROUP), chan(2, ME)]}
n = TG._normalize_config(json.loads(json.dumps(legacy)))
check("migrate: group channel -> personal OFF",
      n["channels"][0]["notifications"]["personalAlerts"] is False)
check("migrate: private chat -> personal ON",
      n["channels"][1]["notifications"]["personalAlerts"] is True)
check("migrate: other toggles untouched",
      n["channels"][0]["notifications"]["dailySummary"] is True
      and n["channels"][0]["notifications"]["criticalAlerts"] is True)
expl = {"bot_token": "T", "channels": [chan(1, GROUP, personalAlerts=True),
                                       chan(2, ME, personalAlerts=False)]}
n = TG._normalize_config(expl)
check("explicit: user's choice wins over the heuristic",
      n["channels"][0]["notifications"]["personalAlerts"] is True
      and n["channels"][1]["notifications"]["personalAlerts"] is False)
# the save path (pydantic model → dict) keeps an explicit False and derives a None
m = TG.TelegramConfigModel(**{"bot_token": "T", "channels": [
    chan(1, GROUP), chan(2, ME, personalAlerts=False)]})
n = TG._normalize_config(m.dict())
check("save: missing key on group stays OFF",
      n["channels"][0]["notifications"]["personalAlerts"] is False)
check("save: explicit OFF on private chat stays OFF",
      n["channels"][1]["notifications"]["personalAlerts"] is False)
check("save: model accepts the new key", hasattr(m.channels[0].notifications, "personalAlerts"))

# ── 3) routing ────────────────────────────────────────────────────────────
TG.TELEGRAM_CONFIG = TG._normalize_config(json.loads(json.dumps(legacy)))
SENT.clear()
TG.notify_system_alert({"message": "relay down", "severity": "warning"})
check("route: normal system alert -> both chats", chats() == sorted([ME, GROUP]), repr(chats()))
SENT.clear()
TG.notify_system_alert({"message": "login", "severity": "info", "personal": True})
check("route: personal system alert -> only you", chats() == [ME], repr(chats()))
SENT.clear()
TG.notify_critical({"message": "GTT failed", "severity": "error"})
check("route: normal critical -> both chats", chats() == sorted([ME, GROUP]), repr(chats()))
SENT.clear()
TG.notify_critical({"message": "Kite LOST", "severity": "error", "personal": True})
check("route: personal critical -> only you", chats() == [ME], repr(chats()))
# personal alerts do not need criticalAlerts on that chat
TG.TELEGRAM_CONFIG = TG._normalize_config({"bot_token": "T", "channels": [
    chan(1, GROUP), chan(2, ME, criticalAlerts=False, personalAlerts=True)]})
SENT.clear()
TG.notify_system_alert({"message": "login", "severity": "info", "personal": True})
check("route: personal works with critical OFF on your chat", chats() == [ME], repr(chats()))
# no personal channel at all → nothing on Telegram (never falls back to the group)
TG.TELEGRAM_CONFIG = TG._normalize_config({"bot_token": "T", "channels": [chan(1, GROUP)]})
SENT.clear()
TG.notify_system_alert({"message": "login", "severity": "info", "personal": True})
check("route: no personal channel -> group still gets nothing", SENT == [], repr(SENT))

# ── 4) the Kite-login watch end to end (real clock) ───────────────────────
TG.TELEGRAM_CONFIG = TG._normalize_config(json.loads(json.dumps(legacy)))
CS.CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
CS.CREDENTIALS_PATH.write_text(json.dumps({"api_key": "k1", "api_secret": "s"}))


class _FakeDT(datetime):
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


class _Mgr:
    def get_kite(self):
        return None

    def is_trade_ready(self):
        return False


B.datetime = _FakeDT
MH.is_trading_day = lambda *a, **k: True
ZA.is_token_valid = lambda: False
B._STATE.clear()
for hh, mm in ((8, 30), (8, 46), (9, 10)):
    SENT.clear()
    _FakeDT._now = datetime(2026, 10, 28, hh, mm, 5, tzinfo=B.IST)
    B.broker_session_watch_job(_Mgr())
    btn = (SENT[0]["markup"] or {}).get("inline_keyboard") if SENT else None
    check(f"watch {hh:02d}:{mm:02d}: only your chat, with the login button",
          chats() == [ME] and bool(btn), repr(SENT))
SENT.clear()
ok, detail = B.send_login_nudge()
check("watch: rehearsal goes only to you", ok and chats() == [ME], repr((ok, detail, SENT)))

# ── 5) liveness target sent to the droplet (app side) ─────────────────────
cfg_file = TG.CONFIG_FILE
cfg_file.parent.mkdir(parents=True, exist_ok=True)


def _target(cfg):
    cfg_file.write_text(json.dumps(cfg))
    return LP._telegram_target()


t = _target(legacy)
check("liveness: target is your chat, not the first (group) channel",
      t == {"bot_token": "T", "chat_id": ME}, repr(t))
t = _target({"bot_token": "T", "channels": [chan(1, GROUP)]})
check("liveness: group-only config -> no target", t is None, repr(t))
dis = chan(2, ME)
dis["enabled"] = False
t = _target({"bot_token": "T", "channels": [chan(1, GROUP), dis]})
check("liveness: disabled personal channel -> no target", t is None, repr(t))
t = _target({"bot_token": "T", "channels": [chan(1, GROUP, personalAlerts=True), chan(2, ME)]})
check("liveness: explicit choice honoured", t == {"bot_token": "T", "chat_id": GROUP}, repr(t))

# ── 6) droplet: no duplicate when your chat IS the admin chat ─────────────
LS = ROOT / "license_server"
if (LS / "liveness_watch.py").exists():
    os.environ["LICSRV_DATA_DIR"] = tempfile.mkdtemp(prefix="liv_")
    sys.path.insert(0, str(LS))
    import liveness_watch as LW            # noqa: E402
    import notify as NT                    # noqa: E402
    USER, ADMIN = [], []
    LW._send = lambda tok, chat, text: USER.append(chat) or True
    NT._config = lambda: {"bot_token": "A", "chat_id": ME}
    NT.send = lambda text: ADMIN.append(text)
    # a Wednesday 10:00 IST, record last seen 10 min earlier
    now = int(datetime(2026, 10, 28, 10, 0, tzinfo=LW.IST).timestamp())

    def _arm(key, chat):
        LW.record(key, "m", key, {"bot_token": "T", "chat_id": chat}, [])
        st = LW._load()
        st[key]["last_seen"] = now - 600
        LW._save(st)

    _arm("MINE", ME)
    _arm("FRIEND", "555000111")
    LW.scan_once(now)
    check("droplet: your machine -> one alert in your chat",
          USER.count(ME) == 1, repr(USER))
    check("droplet: admin copy skipped for your own machine",
          not any("MINE" in a for a in ADMIN), repr(ADMIN))
    check("droplet: friend's machine still copies the admin",
          any("FRIEND" in a for a in ADMIN), repr(ADMIN))
    # recovery
    USER.clear()
    ADMIN.clear()
    st = LW._load()
    for k in st:
        st[k]["last_seen"] = now + 60
    LW._save(st)
    LW.scan_once(now + 90)
    check("droplet: recovery -> your chat once, no admin duplicate",
          USER.count(ME) == 1 and not any("MINE" in a for a in ADMIN), repr((USER, ADMIN)))
    check("droplet: friend's recovery still copies the admin",
          any("FRIEND" in a for a in ADMIN), repr(ADMIN))
else:
    print("  SKIP  license_server not beside backend/")

total = PASSES[0] + len(FAILS)
if FAILS:
    print(f"{len(FAILS)} of {total} checks FAILED")
    sys.exit(1)
print(f"{total}/{total} checks passed")
