# backend/app/api/test_tg_chat_names.py
#
# ── TG_CHAT_NAMES_20261111 ── Connections → Notification Channels: name the
# chat behind a chat ID, name the bot behind the token, and list the chats
# this install has used or the bot has recently seen. Telegram is faked.
# Run:  PYTHONPATH=backend python3 backend/app/api/test_tg_chat_names.py
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.api.telegram_api as TA                                    # noqa: E402

FAILS = []
CHANNEL, PRIVATE, GROUP, LOST = "-1003971039466", "605541748", "-1002200000000", "-100999"


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Resp:
    def __init__(self, js, code=200):
        self._js, self.status_code = js, code
        self.content = json.dumps(js).encode()

    def json(self):
        return self._js


CALLS = []
STATE = {"webhook": False, "member": {"status": "administrator", "can_post_messages": True}}
CHATS = {CHANNEL: {"id": int(CHANNEL), "type": "channel", "title": "Market Makers"},
         PRIVATE: {"id": int(PRIVATE), "type": "private", "first_name": "Anbarasu"},
         GROUP: {"id": int(GROUP), "type": "supergroup", "title": "Bulls-Eye"}}


def fake_get(url, params=None, timeout=None):
    method = url.rsplit("/", 1)[-1]
    CALLS.append((method, dict(params or {})))
    if method == "getMe":
        return Resp({"ok": True, "result": {"id": 42, "first_name": "Diva's Scalp", "username": "divas_scalp_bot"}})
    if method == "getChat":
        c = CHATS.get(str(params["chat_id"]))
        return Resp({"ok": True, "result": c} if c else {"ok": False, "description": "Bad Request: chat not found"}, 200 if c else 400)
    if method == "getChatMember":
        return Resp({"ok": True, "result": dict(STATE["member"])})
    if method == "getUpdates":
        if STATE["webhook"]:
            return Resp({"ok": False, "description": "Conflict: can't use getUpdates method while webhook is active"}, 409)
        return Resp({"ok": True, "result": [
            {"update_id": 1, "channel_post": {"date": 1700000300, "chat": CHATS[CHANNEL]}},
            {"update_id": 2, "my_chat_member": {"date": 1700000200, "chat": CHATS[GROUP]}},
        ]})
    return Resp({"ok": False, "description": "unknown"}, 404)


SAVED = []
TA.save_telegram_config_to_file = lambda cfg: SAVED.append(cfg)   # NEVER touch the real ~/.scalp-app config


def setup(history=None):
    TA.requests.get = fake_get
    TA._CHAT_INFO_CACHE.clear()
    CALLS.clear()
    d = Path(tempfile.mkdtemp(prefix="tgnames_"))
    TA.CHAT_HISTORY_FILE = d / "telegram_chat_history.json"
    if history is not None:
        TA.CHAT_HISTORY_FILE.write_text(json.dumps(history))
    TA.TELEGRAM_CONFIG = TA._normalize_config({"bot_token": "T", "channels": [
        {"chat_id": CHANNEL, "enabled": True}, {"chat_id": "", "enabled": False}]})
    STATE["webhook"] = False
    STATE["member"] = {"status": "administrator", "can_post_messages": True}


def t_lookup():
    setup()
    r = TA.telegram_chat_info(CHANNEL)
    check("channel id → 'Market Makers', channel, bot can post",
          (r["ok"], r["title"], r["type"], r["can_post"]) == (True, "Market Makers", "channel", True), str(r))
    n = len(CALLS)
    TA.telegram_chat_info(CHANNEL)
    check("second lookup served from cache (no Telegram calls)", len(CALLS) == n)
    TA.telegram_chat_info(CHANNEL, refresh=True)
    check("refresh=true bypasses the cache", len(CALLS) > n)

    setup()
    STATE["member"] = {"status": "member"}
    r = TA.telegram_chat_info(CHANNEL)
    check("bot only a member of a channel → can_post False (not an admin)", r["can_post"] is False, str(r))

    r = TA.telegram_chat_info(PRIVATE)
    check("private chat → person's name, can post", (r["title"], r["type"], r["can_post"]) == ("Anbarasu", "private", True), str(r))
    r = TA.telegram_chat_info(LOST)
    check("unknown chat → plain instruction to add the bot / press Start",
          not r["ok"] and "Add the bot" in r["error"], str(r))
    r = TA.telegram_chat_info("")
    check("empty id → asks for a chat ID, no Telegram call", not r["ok"] and r["error"] == "Enter a chat ID.")


def t_bot_info():
    setup()
    r = TA.telegram_bot_info()
    check("bot-info names the bot behind the saved token",
          (r["ok"], r["name"], r["username"]) == (True, "Diva's Scalp", "divas_scalp_bot"), str(r))
    TA.TELEGRAM_CONFIG = TA._normalize_config({"bot_token": "", "channels": []})
    check("no token → clear message", TA.telegram_bot_info()["ok"] is False)


def t_known():
    setup({PRIVATE: {"last_used": 1700000400, "title": "", "type": ""}})
    r = TA.telegram_known_chats()
    rows = {c["chat_id"]: c for c in r["chats"]}
    check("lists used chats + chats the bot saw + the current channel ids",
          set(rows) == {PRIVATE, CHANNEL, GROUP}, str(rows))
    check("newest first", [c["chat_id"] for c in r["chats"]] == [PRIVATE, CHANNEL, GROUP],
          str([c["chat_id"] for c in r["chats"]]))
    check("names filled from updates and lookups",
          (rows[PRIVATE]["title"], rows[CHANNEL]["title"], rows[GROUP]["title"])
          == ("Anbarasu", "Market Makers", "Bulls-Eye"), str(rows))
    check("source marks used vs only seen by the bot",
          rows[PRIVATE]["source"] == "used" and rows[GROUP]["source"] == "seen")
    upd = [p for m, p in CALLS if m == "getUpdates"]
    check("getUpdates called WITHOUT an offset (never consumes updates)",
          upd and all("offset" not in p for p in upd), str(upd))

    setup()
    STATE["webhook"] = True
    r = TA.telegram_known_chats()
    check("bot with a webhook → still lists used chats, with an explanatory note",
          [c["chat_id"] for c in r["chats"]] == [CHANNEL] and "webhook" in r["note"], str(r))


def t_history_writes():
    setup()
    asyncio.run(TA.save_telegram_config(TA.TelegramConfigModel(**{"bot_token": "T", "channels": [
        {"id": "ch1", "name": "Channel 1", "chat_id": GROUP, "enabled": True},
        {"id": "ch2", "name": "Channel 2", "chat_id": "", "enabled": False}]})))
    hist = json.loads(TA.CHAT_HISTORY_FILE.read_text())
    check("saving channel settings records the chat ids used", GROUP in hist and "" not in hist, str(hist))
    check("(the real telegram_config.json is never written by this suite)", len(SAVED) == 1)
    TA.send_telegram_message = lambda *a, **k: True
    asyncio.run(TA.test_telegram_connection(TA.TelegramTestRequest(bot_token="T", chat_id=PRIVATE)))
    hist = json.loads(TA.CHAT_HISTORY_FILE.read_text())
    check("a successful test message records its chat id too", PRIVATE in hist, str(hist))
    TA.telegram_chat_info(GROUP)
    hist = json.loads(TA.CHAT_HISTORY_FILE.read_text())
    check("a lookup stores the name for a used chat", hist[GROUP].get("title") == "Bulls-Eye", str(hist))
    big = {str(-100 - i): {"last_used": i} for i in range(50)}
    TA._save_chat_history(big)
    check("history capped at 30, newest kept",
          len(json.loads(TA.CHAT_HISTORY_FILE.read_text())) == 30)


if __name__ == "__main__":
    for fn in (t_lookup, t_bot_info, t_known, t_history_writes):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__}: {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL TG CHAT-NAME CHECKS PASSED")
