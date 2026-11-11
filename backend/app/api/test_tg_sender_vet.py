# backend/app/api/test_tg_sender_vet.py
#
# ── TG_SENDER_LABEL_20261111 + TG_VET_ACTIVITY_20261111 ──
# 2026-11-11, "Market Makers" channel with two installs posting:
#   1. every post showed only the channel's name — no way to tell which
#      install (bot) sent it;
#   2. the friend's live Icarus entry arrived, his live Velvet entry did not:
#      Velvet never sent Trade Activity at all.
# Run:  PYTHONPATH=backend python3 backend/app/api/test_tg_sender_vet.py
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.utils.test_guard as TGD                                   # noqa: E402
_REAL_GUARD = TGD.outbound_blocked
import app.api.telegram_api as TA                                    # noqa: E402
import app.api.telegram_summary_send as TS                           # noqa: E402

FAILS = []
CHANNEL, PRIVATE = "-1003971039466", "605541748"


def check(name, cond, detail=""):
    print(f"[{'ok  ' if cond else 'MISS'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Resp:
    def __init__(self, code=200, js=None):
        self.status_code, self._js, self.text = code, js or {}, ""

    def json(self):
        return self._js


GETME = []
POSTS = []


def fake_get(url, timeout=None):
    GETME.append(url)
    return Resp(200, {"ok": True, "result": {"first_name": "Diva's Scalp", "is_bot": True}})


def fake_post(url, json=None, data=None, files=None, timeout=None):
    POSTS.append((url, json or data))
    return Resp(200)


def setup():
    TGD.outbound_blocked = lambda: False          # let the send path run; HTTP is faked below
    TA.requests.get, TA.requests.post = fake_get, fake_post
    TS.requests.post = fake_post
    TA._BOT_NAME_CACHE.clear()
    GETME.clear()
    POSTS.clear()


# ── 1. sender label ────────────────────────────────────────────────────
def t_label():
    setup()
    TA.send_telegram_message("TOKEN-DIVA", CHANNEL, "🎯 <b>BASKET ENTRY</b> · Icarus")
    TA.send_telegram_message("TOKEN-DIVA", CHANNEL, "second message")
    TA.send_telegram_message("TOKEN-DIVA", PRIVATE, "private")
    msgs = [p[1]["text"] for p in POSTS]
    check("channel post opens with the sending bot's name",
          msgs[0].startswith("🤖 <b>Diva&#x27;s Scalp</b>\n") or msgs[0].startswith("🤖 <b>Diva's Scalp</b>\n"),
          repr(msgs[0]))
    check("…and keeps the original message intact after it", msgs[0].endswith("· Icarus"))
    check("bot name fetched ONCE per install (cached)", len(GETME) == 1, str(GETME))
    check("private chat unchanged (no prefix)", msgs[2] == "private", repr(msgs[2]))

    setup()
    TA.send_telegram_message("TOKEN-ANBU", CHANNEL, "x")
    TA.requests.get = lambda url, timeout=None: (_ for _ in ()).throw(RuntimeError("offline"))
    TA.send_telegram_message("TOKEN-NEW", CHANNEL, "y")
    check("getMe failure → message still sent, just without a label",
          POSTS[-1][1]["text"] == "y" and len(POSTS) == 2)


def t_card_caption():
    setup()
    TS._send_photo("TOKEN-DIVA", CHANNEL, b"png", "Live +₹1,000 · Paper −₹500")
    TS._send_photo("TOKEN-DIVA", PRIVATE, b"png", "Live +₹1,000")
    TS._send_photo("TOKEN-DIVA", CHANNEL, b"png", "x" * 1100)
    caps = [p[1]["caption"] for p in POSTS]
    check("daily / expiry / parity card captions carry the label in channels",
          "Scalp</b>\n" in caps[0] and caps[0].endswith("Paper −₹500"), repr(caps[0]))
    check("card caption in a private chat unchanged", caps[1] == "Live +₹1,000")
    check("caption kept within Telegram's 1024-character cap", len(caps[2]) <= 1024, str(len(caps[2])))


# ── 2. Velvet trade activity ───────────────────────────────────────────
def t_vet_activity():
    import app.engine.vet.vet_manager as VM
    from app.engine.vet.vet_common import VetRepo
    VM.write_audit_log = lambda m: None
    TGD.outbound_blocked = _REAL_GUARD         # the real guard: this IS a test process
    sent = []
    TA.notify_group_entry = lambda d: sent.append(("ENTRY", d))
    TA.notify_group_exit = lambda d: sent.append(("EXIT", d))
    px = {"NIFTY26N1725400PE": 223.0, "NIFTY26N1723400PE": 4.0}

    def chain(side, ts):
        return [{"tradingsymbol": s, "token": 1, "strike": float(s[10:15]), "expiry": "2026-11-17",
                 "instrument_type": s[-2:], "ltp": p} for s, p in px.items() if s.endswith(side)]
    cfg = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 5.0, "atm_offset": 0,
           "eod_square": False, "quantity": {"lots": 2, "lot_size": 65}, "_spot": 25400.0}
    repo = VetRepo(os.path.join(tempfile.mkdtemp(prefix="tgvet_"), "v.db"))
    repo.ensure_schema()
    m = VM.VetManager(cfg, repo=repo, chain_fn=chain, quote_fn=lambda s: px.get(s), mode="PAPER")
    if hasattr(m, "wing_search_fn"):
        m.wing_search_fn = None
    check("trade cards are OFF by default inside a test process (no junk in the real feed)",
          m.notify_activity is False)
    m.notify_activity = True
    m.open_position("PE", ts=1_794_000_000, bar_ts=1_794_000_000, condition=1)
    px["NIFTY26N1725400PE"], px["NIFTY26N1723400PE"] = 200.0, 3.0
    m.close_position("FLIP", ts=1_794_003_600)
    kinds = [k for k, _ in sent]
    check("Velvet entry and exit now reach Trade Activity", kinds == ["ENTRY", "EXIT"], str(kinds))
    e = sent[0][1]
    legs = {l["leg_id"]: l for l in e["legs"]}
    check("entry card: L1 SELL 25400PE @ 223 + L2 BUY wing 23400PE @ 4, 130 qty, strategy VET_V1",
          e["strategy_id"] == "VET_V1" and e["mode"] == "paper"
          and (legs["L1"]["action"], legs["L1"]["symbol"], legs["L1"]["qty"], legs["L1"]["entry_price"])
          == ("SELL", "NIFTY26N1725400PE", 130, 223.0)
          and (legs["L2"]["action"], legs["L2"]["symbol"]) == ("BUY", "NIFTY26N1723400PE"), str(e))
    x = sent[1][1]
    xl = {l["leg_id"]: l for l in x["legs"]}
    check("exit card: reason FLIP, exit prices for both legs",
          x["reason"] == "FLIP" and xl["L1"]["exit_price"] == 200.0 and xl["L2"]["exit_price"] == 3.0, str(x))

    def boom(d):
        raise RuntimeError("telegram down")
    TA.notify_group_entry = boom
    px["NIFTY26N1725400PE"], px["NIFTY26N1723400PE"] = 223.0, 4.0
    p = m.open_position("PE", ts=1_794_007_200, bar_ts=1_794_007_200, condition=1)
    check("a Telegram failure never blocks the trade", p is not None and m.pos is not None)


def t_routing_market_makers():
    """The friend's channel 2 = Market Makers, mode Live, Trade Activity on."""
    TA.TELEGRAM_CONFIG = TA._normalize_config({"bot_token": "TOKEN-DIVA", "channels": [
        {"chat_id": "8028495823", "enabled": True, "mode_filter": "live",
         "notifications": {"tradeActivity": True}},
        {"chat_id": CHANNEL, "enabled": True, "mode_filter": "live",
         "notifications": {"tradeActivity": True}}]})
    live = [c for _, c, _ in TA._iter_active_channels(TA.NOTIF_TRADE_ACTIVITY, mode="live", strategy_id="VET_V1")]
    paper = [c for _, c, _ in TA._iter_active_channels(TA.NOTIF_TRADE_ACTIVITY, mode="paper", strategy_id="VET_V1")]
    check("a LIVE Velvet entry is routed to Market Makers (and the private chat)",
          CHANNEL in live and "8028495823" in live, str(live))
    check("a PAPER one is not (channels are Live-only)", paper == [], str(paper))


if __name__ == "__main__":
    for fn in (t_label, t_card_caption, t_vet_activity, t_routing_market_makers):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__}: {e!r}")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("\nALL TG SENDER / VELVET ACTIVITY CHECKS PASSED")
