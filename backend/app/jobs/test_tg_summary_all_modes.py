# backend/app/jobs/test_tg_summary_all_modes.py
# ── TG_SUMMARY_ALL_MODES_20261028 ── the daily summary (and the expiry-day
# card, which rides the same toggle) is a whole-day digest: it goes to every
# enabled channel with "Daily Summary" ticked, whatever that channel's
# Paper / Live / All mode filter says. Every other alert type keeps the mode
# filter exactly as before. Hermetic: HOME points at a temp folder and all
# Telegram sends are replaced by recorders.
#
#   cd backend && HOME=$(mktemp -d) python3 app/jobs/test_tg_summary_all_modes.py
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_TEST_MODE", "1")

import app.api.telegram_api as TG                    # noqa: E402
import app.services.telegram_scheduler as TS         # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  {detail}")


IST = timezone(timedelta(hours=5, minutes=30))
AT_1540 = datetime(2026, 10, 28, 15, 40, 27, tzinfo=IST)     # real clock, the miss
AT_2000 = datetime(2026, 10, 28, 20, 0, 0, tzinfo=IST)       # manual curl later

GROUP, ME, LIVEONLY = "-1003971039466", "605541748", "-1009999999999"


def chan(i, chat, mode="all", sched=None, enabled=True, **notif):
    n = {"tradeActivity": True, "positionUpdates": False, "dailySummary": True,
         "criticalAlerts": True, "personalAlerts": False}
    n.update(notif)
    return {"id": f"channel_{i}", "name": f"c{i}", "chat_id": chat, "enabled": enabled,
            "strategy_filter": [], "mode_filter": mode, "notifications": n,
            "schedule": sched or {"enabled": False, "start": "09:15", "end": "15:45"}}


def set_cfg(*channels):
    TG.TELEGRAM_CONFIG.clear()
    TG.TELEGRAM_CONFIG.update(TG._normalize_config({"bot_token": "T", "channels": list(channels)}))


def targets(key, mode=None, now=AT_1540, sid=None):
    return sorted(c for _t, c, _ch in TG._iter_active_channels(key, mode=mode, now=now,
                                                               strategy_id=sid))


DS = TG.NOTIF_DAILY_SUMMARY

# ── 1) today's exact config: group = Paper + summary ON, private = summary OFF ──
set_cfg(chan(1, GROUP, mode="paper", positionUpdates=True),
        chan(2, ME, mode="all", sched={"enabled": False, "start": "09:15", "end": "10:00"},
             tradeActivity=False, dailySummary=False, criticalAlerts=False,
             personalAlerts=True))
check("today: Paper group receives the daily summary at 15:40",
      targets(DS) == [GROUP], repr(targets(DS)))
check("today: manual trigger at 20:00 also reaches the group",
      targets(DS, now=AT_2000) == [GROUP], repr(targets(DS, now=AT_2000)))
check("today: private chat (summary OFF) still gets nothing", ME not in targets(DS))

# ── 2) every mode filter receives it ─────────────────────────────────────────
for mf in ("paper", "live", "all"):
    set_cfg(chan(1, GROUP, mode=mf), chan(2, ME, dailySummary=False))
    check(f"mode={mf}: summary delivered", targets(DS) == [GROUP], repr(targets(DS)))
    for passed in (None, "live", "paper"):
        check(f"mode={mf}: summary ignores caller mode={passed}",
              targets(DS, mode=passed) == [GROUP])

# ── 3) the Daily Summary tick, enabled switch and schedule still rule ───────
set_cfg(chan(1, GROUP, mode="paper", dailySummary=False), chan(2, ME, mode="live"))
check("summary OFF on group -> group skipped", targets(DS) == [ME], repr(targets(DS)))
set_cfg(chan(1, GROUP, mode="paper", enabled=False), chan(2, ME, dailySummary=False))
check("disabled channel -> skipped", targets(DS) == [])
set_cfg(chan(1, GROUP, mode="paper", sched={"enabled": True, "start": "09:15", "end": "15:40"}),
        chan(2, ME, dailySummary=False))
check("schedule window ending 15:40 still excludes the 15:40 summary", targets(DS) == [])
set_cfg(chan(1, GROUP, mode="paper", sched={"enabled": True, "start": "09:15", "end": "15:45"}),
        chan(2, ME, dailySummary=False))
check("schedule window to 15:45 includes it", targets(DS) == [GROUP])

# ── 4) everything else keeps the mode filter (no behaviour change) ──────────
set_cfg(chan(1, GROUP, mode="paper"), chan(2, LIVEONLY, mode="live"))
TA, CA, PU = TG.NOTIF_TRADE_ACTIVITY, TG.NOTIF_CRITICAL_ALERTS, TG.NOTIF_POSITION_UPDATES
check("trade: PAPER trade -> paper channel only", targets(TA, mode="paper") == [GROUP])
check("trade: LIVE trade -> live channel only", targets(TA, mode="live") == [LIVEONLY])
check("trade: untagged (None=live) unchanged", targets(TA, mode=None) == [LIVEONLY])
check("critical: LIVE critical not in paper group", targets(CA, mode="live") == [LIVEONLY])
check("position updates: paper snapshot -> paper channel",
      targets(PU, mode="paper") == [])          # positionUpdates OFF in chan() default
set_cfg(chan(1, GROUP, mode="paper", positionUpdates=True), chan(2, LIVEONLY, mode="live"))
check("position updates: live snapshot not in paper group",
      targets(PU, mode="live") == [])

# ── 5) the scheduler path end to end (card + expiry), sends recorded ───────
SENT_CARD, SENT_EXP = [], []
TS.build_card_data_once = lambda: object()
TS.send_daily_summary_card = lambda *, bot_token, chat_id, text_fallback, data: \
    SENT_CARD.append(chat_id)
TS.build_expiry_card_data_once = lambda expiry: object()
TS.send_expiry_summary_card = lambda *, bot_token, chat_id, data: SENT_EXP.append(chat_id)
TS.is_expiry_day = lambda d: True

set_cfg(chan(1, GROUP, mode="paper"),
        chan(2, ME, tradeActivity=False, dailySummary=False, criticalAlerts=False,
             personalAlerts=True))
s = TS.TelegramScheduler()
s._handle_daily_summary(AT_1540)
check("scheduler @15:40: card sent to the Paper group", SENT_CARD == [GROUP], repr(SENT_CARD))
s._handle_daily_summary(AT_1540)
check("scheduler @15:40: second tick does not resend", SENT_CARD == [GROUP], repr(SENT_CARD))
s._handle_expiry_summary(AT_1540)
check("scheduler @15:40 expiry day: expiry card to the Paper group",
      SENT_EXP == [GROUP], repr(SENT_EXP))
SENT_CARD.clear()
s.run_daily_summary_now()
check("manual trigger (debug route): card to the Paper group", SENT_CARD == [GROUP],
      repr(SENT_CARD))

# ── 6) the text fallback fans out the same way ─────────────────────────────
SENT_TXT = []
TG.send_telegram_message = (lambda tok, chat, msg, parse_mode="HTML", reply_markup=None:
                            SENT_TXT.append(chat) or True)
empty = {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "by_strategy": {}}
TG._query_today_live_summary = lambda: dict(empty)
TG._query_today_paper_summary = lambda: dict(empty)
TG.notify_daily_summary()
check("text fallback: reaches the Paper group", SENT_TXT == [GROUP], repr(SENT_TXT))

total = PASSES[0] + len(FAILS)
if FAILS:
    print(f"{len(FAILS)} of {total} checks FAILED")
    sys.exit(1)
print(f"{total}/{total} checks passed")
