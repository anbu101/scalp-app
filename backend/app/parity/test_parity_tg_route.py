# backend/app/parity/test_parity_tg_route.py
#
# ── PARITY_TG_ROUTE_20261102 ── the 15:50 parity card goes where the 15:40 EOD
# card goes: every channel with Daily Summary ticked (whatever its mode
# filter); the Personal Alerts chat only when no channel takes Daily Summary.
#   cd backend && PYTHONPATH=$PWD python3 app/parity/test_parity_tg_route.py
import os
import sys
import tempfile
from datetime import date

os.environ["HOME"] = tempfile.mkdtemp(prefix="parity_tg_")
import app.api.telegram_api as TA                           # noqa: E402
import app.api.telegram_summary_send as TSS                 # noqa: E402
from app.parity import parity_card as PCARD                 # noqa: E402
from app.parity import parity_core as PC                    # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


GROUP, ME = "-1003971039466", "605541748"
CH = {TA.NOTIF_DAILY_SUMMARY: [("tok", GROUP, {})], TA.NOTIF_PERSONAL_ALERTS: [("tok", ME, {})]}
ASKED = []


def fake_iter(key, mode=None, **k):
    ASKED.append((key, mode))
    return iter(list(CH.get(key, [])))


TA._iter_active_channels = fake_iter
SENT = []
TSS._send_photo = lambda tok, chat, png, cap: SENT.append((chat, cap)) or True
PCARD.build_parity_card_png = lambda d, *a, **k: b"\x89PNG-fake"
D = date(2026, 11, 2)

print("── the picture")
ok = PCARD.send_card(D, ["Tigris ✓ 4/4", "Timberwolf ✓ 2/2"])
check("goes to the Daily Summary channel (the group), not the personal chat",
      ok and [s[0] for s in SENT] == [GROUP], SENT)
check("Daily Summary is asked without a mode filter (a 'Live'-only group still gets it)",
      ASKED[0] == (TA.NOTIF_DAILY_SUMMARY, None), ASKED)
check("caption carries the status lines", SENT and "Tigris ✓ 4/4" in SENT[0][1])
SENT.clear()
CH[TA.NOTIF_DAILY_SUMMARY] = [("tok", GROUP, {}), ("tok", "-100999", {})]
PCARD.send_card(D, ["x"])
check("every Daily Summary channel gets it", [s[0] for s in SENT] == [GROUP, "-100999"], SENT)
SENT.clear()
CH[TA.NOTIF_DAILY_SUMMARY] = []
PCARD.send_card(D, ["x"])
check("no channel takes Daily Summary → falls back to the personal chat", [s[0] for s in SENT] == [ME], SENT)

print("── the text fallback")
CH[TA.NOTIF_DAILY_SUMMARY] = [("tok", GROUP, {})]
FAN, CRIT = [], []
TA._fanout = lambda key, msg, **k: FAN.append((key, msg)) or 1
TA.notify_critical = lambda d: CRIT.append(d)
PC._notify("🧪 Parity · 02 Nov\nTigris ✓ 4/4")
check("text goes to Daily Summary channels", FAN and FAN[0][0] == TA.NOTIF_DAILY_SUMMARY and not CRIT, (FAN, CRIT))
FAN.clear()
TA._fanout = lambda key, msg, **k: FAN.append((key, msg)) or 0
PC._notify("x")
check("…and only to the personal chat when no Daily Summary channel took it",
      FAN and CRIT and CRIT[0].get("personal") is True, (FAN, CRIT))

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PARITY TELEGRAM-ROUTE CHECKS PASSED")
