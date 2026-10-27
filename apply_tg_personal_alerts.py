#!/usr/bin/env python3
# apply_tg_personal_alerts.py — TG_PERSONAL_ALERTS_20261027
#
# Personal alerts go ONLY to your own Telegram chat, never to the group:
#   * New per-channel toggle "Personal Alerts" (Connections → Telegram).
#     Existing channels are migrated automatically: a private chat (positive
#     chat id) → ON, a group/channel (negative id or @name) → OFF. Your
#     choice in the UI overrides that from then on.
#   * Kite-login alerts (the 08:30 nudge, 08:45 warning, 09:10 critical,
#     mid-day "session LOST", trading-disabled, "ready") route to Personal
#     Alerts instead of Critical Alerts.
#   * Liveness ("laptop silent 3 min" / "back online"): the app now gives the
#     droplet the chat with Personal Alerts on, instead of the FIRST enabled
#     channel (which is how the group ended up getting it). No personal chat
#     → no user alert from the droplet (it never falls back to the group).
#   * Droplet: when your chat IS the admin chat, the admin copy is skipped,
#     so you get ONE liveness message, not two. Friends' machines still copy
#     the admin as before.
#   * Everything else (trade activity, daily summary, critical alerts) is
#     unchanged and still goes wherever it went before.
#
# REQUIRES apply_kite_login_nudge.py (KITE_LOGIN_NUDGE_20261027) first.
#
# Touches:
#   backend/app/api/telegram_api.py            (+ desktop mirror)
#   backend/app/jobs/broker_session_watch.py   (+ desktop mirror)
#   backend/app/jobs/liveness_ping.py          (+ desktop mirror)
#   backend/app/jobs/test_kite_login_nudge.py  (fixture: personal chat)
#   backend/app/jobs/test_tg_personal_alerts.py  NEW
#   frontend/src/pages/Connections.jsx         (one more toggle)
#   license_server/liveness_watch.py           (no duplicate admin copy)
#
# USAGE (repo root):
#   python3 apply_tg_personal_alerts.py --dry-run
#   python3 apply_tg_personal_alerts.py
#   python3 apply_tg_personal_alerts.py --revert
# Then: ./desktop/build-scalp.sh both   and   ./license_server/deploy-license-server.command
import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "TG_PERSONAL_ALERTS_20261027"
PARENT = "KITE_LOGIN_NUDGE_20261027"
BAK = ".bak-" + FENCE
MIRROR = Path("desktop/src-tauri/backend")
TEST_REL = "backend/app/jobs/test_tg_personal_alerts.py"
F = "# ── " + FENCE + " ──"

TG_EDITS = [
('''NOTIF_CRITICAL_ALERTS  = "criticalAlerts"
''',
'''NOTIF_CRITICAL_ALERTS  = "criticalAlerts"
NOTIF_PERSONAL_ALERTS  = "personalAlerts"    ''' + F + '''
'''),
('''    NOTIF_CRITICAL_ALERTS:  True,
}

_DEFAULT_SCHEDULE''',
'''    NOTIF_CRITICAL_ALERTS:  True,
    NOTIF_PERSONAL_ALERTS:  False,           ''' + F + ''' (derived from chat_id on migrate)
}

_DEFAULT_SCHEDULE'''),
('''    criticalAlerts:  bool = True
''',
'''    criticalAlerts:  bool = True
    personalAlerts:  Optional[bool] = None   ''' + F + ''' None → derived from chat_id
'''),
('''def _normalize_config(raw: dict) -> dict:
''',
'''# ── ''' + FENCE + ''' ──
# "Personal" alerts (Kite login, laptop liveness) are for the operator only.
# A Telegram private chat has a POSITIVE numeric id; groups and channels are
# negative (-100…) or @usernames. An existing channel with no explicit
# setting therefore defaults to ON only when it is a private chat.
def _is_private_chat(chat_id) -> bool:
    s = str(chat_id or "").strip()
    return s.isdigit() and int(s) > 0


def _personal_flag(val, chat_id) -> bool:
    if val is None:
        return _is_private_chat(chat_id)
    return bool(val)


def _normalize_config(raw: dict) -> dict:
'''),
('''            NOTIF_CRITICAL_ALERTS:  bool(notif.get(NOTIF_CRITICAL_ALERTS,  _DEFAULT_NOTIFICATIONS[NOTIF_CRITICAL_ALERTS])),
        }
''',
'''            NOTIF_CRITICAL_ALERTS:  bool(notif.get(NOTIF_CRITICAL_ALERTS,  _DEFAULT_NOTIFICATIONS[NOTIF_CRITICAL_ALERTS])),
            NOTIF_PERSONAL_ALERTS:  _personal_flag(notif.get(NOTIF_PERSONAL_ALERTS), base["chat_id"]),   ''' + F + '''
        }
'''),
('''# ── KITE_LOGIN_NUDGE_20261027 ──
def _buttons_markup(alert_data) -> Optional[dict]:
''',
'''# ── ''' + FENCE + ''' ── alert_data["personal"] → only your own chat(s)
def _route_key(alert_data) -> str:
    return (NOTIF_PERSONAL_ALERTS if (alert_data or {}).get("personal")
            else NOTIF_CRITICAL_ALERTS)


# ── KITE_LOGIN_NUDGE_20261027 ──
def _buttons_markup(alert_data) -> Optional[dict]:
'''),
('''    _fanout(NOTIF_CRITICAL_ALERTS, message, strategy_id=strategy_id,
            reply_markup=_buttons_markup(alert_data))   # ── KITE_LOGIN_NUDGE_20261027 ──
''',
'''    _fanout(_route_key(alert_data), message, strategy_id=strategy_id,   ''' + F + '''
            reply_markup=_buttons_markup(alert_data))   # ── KITE_LOGIN_NUDGE_20261027 ──
'''),
('''    _fanout(NOTIF_CRITICAL_ALERTS, message,
            reply_markup=_buttons_markup(alert_data))
''',
'''    _fanout(_route_key(alert_data), message,   ''' + F + '''
            reply_markup=_buttons_markup(alert_data))
'''),
]

BSW_EDITS = [
('''#   * send_login_nudge() = on-demand rehearsal (debug route).
''',
'''#   * send_login_nudge() = on-demand rehearsal (debug route).
# ── ''' + FENCE + ''' ── every alert from this job is PERSONAL: it goes
#   only to Telegram chats with "Personal Alerts" on (your own chat), never
#   to the group that gets trade summaries.
'''),
('''            payload = {"message": msg, "severity": "error"}
''',
'''            payload = {"message": msg, "severity": "error",
                       "personal": True}           ''' + F + '''
'''),
('''        payload = {"message": msg, "severity": severity}
''',
'''        payload = {"message": msg, "severity": severity,
                   "personal": True}               ''' + F + '''
'''),
('''        return True, ("Sent to the bell and to every Telegram channel with "
                      "critical alerts on (if its schedule window allows).")
''',
'''        return True, ("Sent to the bell and to your Telegram chat(s) with "
                      "Personal Alerts on (if the schedule window allows).")   ''' + F + '''
'''),
]

LP_EDITS = [
('''    """{bot_token, chat_id} for the first enabled channel, or None."""
''',
'''    """{bot_token, chat_id} for the first enabled channel with Personal
    Alerts on (your own chat), or None.  ''' + F + '''"""
'''),
('''        for ch in cfg.get("channels") or []:
            if ch.get("enabled") and str(ch.get("chat_id") or "").strip():
''',
'''        for ch in cfg.get("channels") or []:
            ''' + F + ''' liveness is personal: never a group.
            if not (ch.get("notifications") or {}).get("personalAlerts"):
                continue
            if ch.get("enabled") and str(ch.get("chat_id") or "").strip():
'''),
]

NUDGE_TEST_EDITS = [
('''        "notifications": {"criticalAlerts": True},
''',
'''        "notifications": {"criticalAlerts": True, "personalAlerts": True},   ''' + F + '''
'''),
]

LW_EDITS = [
('''def _admin_copy(text: str) -> None:
    try:
        import notify
        if notify.enabled():
            notify.send(text)
''',
'''def _admin_copy(text: str, tg: dict | None = None) -> None:
    try:
        import notify
        if notify.enabled():
            ''' + F + ''' the user alert already went to this very
            # chat (the admin's own machine) → don't send it twice.
            admin_chat = str((notify._config() or {}).get("chat_id") or "").strip()
            if tg and admin_chat and str(tg.get("chat_id") or "").strip() == admin_chat:
                return
            notify.send(text)
'''),
('''                    _admin_copy(f"✅ liveness: {label} back online")
''',
'''                    _admin_copy(f"✅ liveness: {label} back online", tg)
'''),
('''                _admin_copy(f"🔴 liveness: {label} silent {silent // 60} min")
''',
'''                _admin_copy(f"🔴 liveness: {label} silent {silent // 60} min", tg)
'''),
]

UI_EDITS = [
('''  { key: "criticalAlerts",  title: "Critical Alerts",
    desc: "Order rejections, GTT failures, relay/system issues" },
];
''',
'''  { key: "criticalAlerts",  title: "Critical Alerts",
    desc: "Order rejections, GTT failures, relay/system issues" },
  /* ── ''' + FENCE + ''' ── */
  { key: "personalAlerts",  title: "Personal Alerts",
    desc: "Kite login reminders and laptop-offline alerts — turn on for your own chat only" },
];
'''),
('''  criticalAlerts:  true,
};
''',
'''  criticalAlerts:  true,
  personalAlerts:  false,   /* ── ''' + FENCE + ''' ── */
};
'''),
]

# (rel, edits, append, parents, mirrored, python)
TARGETS = [
    ("backend/app/api/telegram_api.py", TG_EDITS, [PARENT, "TEST_ALERT_GUARD_20261015"], True, True),
    ("backend/app/jobs/broker_session_watch.py", BSW_EDITS, [PARENT], True, True),
    ("backend/app/jobs/liveness_ping.py", LP_EDITS, ["LIVENESS PING"], True, True),
    ("backend/app/jobs/test_kite_login_nudge.py", NUDGE_TEST_EDITS, [PARENT], True, True),
    ("frontend/src/pages/Connections.jsx", UI_EDITS, ["NOTIFICATION_TYPES"], False, False),
    ("license_server/liveness_watch.py", LW_EDITS, ["LIVENESS WATCH"], False, True),
]

TEST_SRC = r'''# backend/app/jobs/test_tg_personal_alerts.py
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
'''


def die(msg, code=2):
    print("\nABORTED — nothing written.\n  " + msg)
    sys.exit(code)


def find_root(arg):
    for c in ([Path(arg)] if arg else []) + [Path.cwd(), Path(__file__).resolve().parent]:
        if (c / "backend/app/jobs/broker_session_watch.py").exists():
            return c.resolve()
    die("Could not find the repo root. Run from the repo root or pass --repo PATH.")


def dests(root, rel, mirrored):
    out = [root / rel]
    if mirrored and rel.startswith("backend/"):
        m = root / MIRROR / Path(rel).relative_to("backend")
        if m.exists() or (rel == TEST_REL and m.parent.exists()):
            out.append(m)
    return out


def git_dirty(root, rel):
    if not (root / ".git").exists() or not shutil.which("git"):
        return False
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", rel],
                       capture_output=True, text=True)
    return any(l[:2].strip() and not l.startswith("??") for l in r.stdout.splitlines())


def patch(text, edits, label):
    for i, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            die(f"{label}: anchor #{i} found {n}× (expected 1) — not the version this "
                f"script targets. Push it to GitHub and ask for a rebase.\n  anchor: "
                f"{old.strip().splitlines()[0][:90]}")
        text = text.replace(old, new, 1)
    return text


def run_suite(root, staged):
    tmp = Path(tempfile.mkdtemp(prefix="tgp_suite_"))
    try:
        ign = shutil.ignore_patterns("__pycache__", "*.bak-*", "*.pyc", "data", "secrets")
        shutil.copytree(root / "backend/app", tmp / "backend/app", ignore=ign)
        shutil.copytree(root / "license_server", tmp / "license_server", ignore=ign)
        for rel, txt in staged.items():
            if rel.startswith(("backend/", "license_server/")):
                (tmp / rel).write_text(txt, encoding="utf-8")
        results = []
        for t in ("app/jobs/test_tg_personal_alerts.py", "app/jobs/test_kite_login_nudge.py"):
            home = Path(tempfile.mkdtemp(prefix="home_", dir=tmp))
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       SCALP_TEST_MODE="1", PYTHONPATH=str(tmp / "backend"))
            env.pop("LICSRV_DATA_DIR", None)
            r = subprocess.run([sys.executable, t], cwd=tmp / "backend", env=env,
                               capture_output=True, text=True, timeout=300)
            results.append((t, r.returncode == 0, (r.stdout + r.stderr).strip()))
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def jsx_check(root, txt):
    """Parse the patched JSX with esbuild when the frontend has it."""
    fe = root / "frontend"
    eb = fe / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if not eb.exists():
        return None, "esbuild not installed in frontend/ — JSX parse check skipped"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "Connections.jsx"
        f.write_text(txt, encoding="utf-8")
        r = subprocess.run([str(eb), str(f), "--loader:.jsx=jsx", "--log-level=error",
                            "--outfile=" + str(Path(td) / "o.js")],
                           capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or "esbuild parse OK").strip()


def revert(root):
    n = 0
    for rel, _e, _p, mirrored, _py in TARGETS:
        for p in dests(root, rel, mirrored):
            b = p.with_name(p.name + BAK)
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
                n += 1
                print(f"  restored  {p.relative_to(root)}")
    for p in dests(root, TEST_REL, True):
        if p.exists() and FENCE in p.read_text(encoding="utf-8", errors="ignore"):
            p.unlink()
            print(f"  removed   {p.relative_to(root)}")
    print(f"\nRevert done ({n} file(s) restored). Rebuild + redeploy the license server.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"{FENCE}\nRepo: {root}")
    if a.revert:
        return revert(root)

    files = [p for rel, _e, _pa, mi, _py in TARGETS for p in dests(root, rel, mi) if p.exists()]
    missing = [rel for rel, *_ in TARGETS if not (root / rel).exists()]
    if missing:
        if "backend/app/jobs/test_kite_login_nudge.py" in missing:
            die("apply_kite_login_nudge.py (KITE_LOGIN_NUDGE_20261027) is not applied — "
                "run it first, then this script.")
        die("missing files: " + ", ".join(missing))
    fenced = [p for p in files if FENCE in p.read_text(encoding="utf-8")]
    if fenced and len(fenced) == len(files):
        print("Already applied — nothing to do.")
        return 0
    if fenced:
        die("mixed state — already fenced:\n  " + "\n  ".join(
            str(p.relative_to(root)) for p in fenced) + "\n  Run --revert first.")

    staged = {}
    for rel, edits, parents, mirrored, _py in TARGETS:
        if git_dirty(root, rel) and not a.allow_dirty:
            die(f"{rel} has uncommitted modifications (git status). Commit/stash first, "
                f"or re-run with --allow-dirty if that is intended (e.g. the nudge "
                f"patch is applied but not committed yet).")
        src = (root / rel).read_text(encoding="utf-8")
        for pf in parents:
            if pf not in src:
                die(f"{rel}: prerequisite marker '{pf}' not found"
                    + (" — run apply_kite_login_nudge.py first." if pf == PARENT else "."))
        staged[rel] = patch(src, edits, rel)
    staged[TEST_REL] = TEST_SRC

    with tempfile.TemporaryDirectory() as td:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                t = Path(td) / (rel.replace("/", "_"))
                t.write_text(txt, encoding="utf-8")
                try:
                    py_compile.compile(str(t), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    print("  py_compile: OK")
    ok, msg = jsx_check(root, staged["frontend/src/pages/Connections.jsx"])
    print(f"  Connections.jsx: {msg}")
    if ok is False:
        die("Connections.jsx does not parse after the edit.")

    print("  running suites…")
    bad = False
    for name, passed, out in run_suite(root, staged):
        summ = next((l for l in reversed(out.splitlines()) if "checks" in l), "")
        print(f"    {'PASS' if passed else 'FAIL'}  {name}: {summ}")
        if not passed:
            bad = True
            print("      " + "\n      ".join(out.splitlines()[-20:]))
    if bad:
        die("a suite failed (see above).")

    plan = []
    for rel, txt in staged.items():
        mirrored = next((t[3] for t in TARGETS if t[0] == rel), True)
        for d in dests(root, rel, mirrored):
            plan.append((d, txt))
    if a.dry_run:
        for d, _ in plan:
            print(f"  would write  {d.relative_to(root)}")
        print("\nDry run OK — nothing written.")
        return 0

    backups = []
    try:
        for d, _ in plan:
            if d.exists():
                b = d.with_name(d.name + BAK)
                shutil.copy2(d, b)
                backups.append((d, b))
        for d, txt in plan:
            d.parent.mkdir(parents=True, exist_ok=True)
            tmpf = d.with_name(d.name + ".tmp-" + FENCE)
            tmpf.write_text(txt, encoding="utf-8")
            os.replace(tmpf, d)
            print(f"  wrote  {d.relative_to(root)}")
    except Exception as e:
        for d, b in backups:
            shutil.copy2(b, d)
        for d, _ in plan:
            if d not in [x for x, _ in backups] and d.exists():
                d.unlink()
        die(f"write failed ({e!r}) — every file restored.")

    print("""
Done. Next:
  1. ./desktop/build-scalp.sh both            (backend + Connections page)
  2. ./license_server/deploy-license-server.command   (no-duplicate liveness)
  3. Connections → Telegram: your private chat now shows "Personal Alerts" ON,
     the group OFF (migrated from the chat ids). Press Save once to persist.
  4. Rehearse:  curl -X POST http://127.0.0.1:47321/api/telegram/debug/send-kite-login
     → arrives in your private chat only.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
