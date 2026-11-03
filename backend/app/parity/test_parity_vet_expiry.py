# backend/app/parity/test_parity_vet_expiry.py
#
# ── PARITY_VET_EXPIRY_20261103 ── Velvet replays use live's expiry-day exit
# time and live's entry cutoff (they have different key names in the runner).
# 2026-11-03: the replay exited at 15:04 and re-entered next week's 23750PE at
# 15:04; live squared off at 15:15 and (cutoff 15:00) opened nothing.
#   cd backend && PYTHONPATH=$PWD python3 app/parity/test_parity_vet_expiry.py
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

os.environ["HOME"] = tempfile.mkdtemp(prefix="parity_vet_")
from app.utils.app_paths import APP_HOME                                 # noqa: E402
from app.backtest.replay.replay_synth import build                       # noqa: E402
from app.parity import parity_core as PC                                 # noqa: E402
from app.config.strategy_loader import load_strategy_config              # noqa: E402
from app.backtest.engine.expiry_calendar import expected_expiry_for_day  # noqa: E402
from app.event_bus.audit_logger import audit_muted                       # noqa: E402

FAILS, PASSES = [], [0]
IST = timezone(timedelta(hours=5, minutes=30))


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


hm = lambda ts: datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")    # noqa: E731
LIVE = dict(load_strategy_config("VET_V1"))

print("── 1. translation")
out, notes = PC.to_runner_config("VET_V1", LIVE, date(2026, 11, 3))
check("live entry_cutoff → runner entry_cutoff_time", out.get("entry_cutoff_time") == LIVE.get("entry_cutoff", "15:00"), out)
check("no rollover live → rollover off, roll_time one 5m bar before exit_time (decided at 15:14)",
      out.get("rollover_enabled") is False and out.get("roll_time") == "15:10", out.get("roll_time"))
check("expiry day carries a note", any("expiry day" in n for n in notes), notes)
_, n2 = PC.to_runner_config("VET_V1", LIVE, date(2026, 11, 2))
check("…other days don't", not any("expiry day" in n for n in n2), n2)
o3, _ = PC.to_runner_config("VET_V1", dict(LIVE, entry_cutoff_time="14:30"), date(2026, 11, 3))
check("an explicit entry_cutoff_time is kept", o3["entry_cutoff_time"] == "14:30")
o4, _ = PC.to_runner_config("VET_V1", dict(LIVE, rollover_enabled=True, roll_time="15:00"), date(2026, 11, 3))
check("a config that DOES roll is left alone", o4["rollover_enabled"] is True and o4["roll_time"] == "15:00")
o5, _ = PC.to_runner_config("VET_V1", dict(LIVE, exit_time="15:25"), date(2026, 11, 3))
check("exit_time later than 15:20 is capped like live (15:20 → roll_time 15:15)", o5["roll_time"] == "15:15", o5["roll_time"])

print("── 2. replay on a synthetic corpus")
m = APP_HOME / "backtest" / "backtest.db"
m.parent.mkdir(parents=True, exist_ok=True)
days = build(str(m), date(2025, 1, 1), date(2025, 3, 14))
exp = [d for d in days if expected_expiry_for_day(d) == d and d > date(2025, 1, 20)][:4]
raw_late, new_late, raw_exit, new_exit = 0, 0, set(), set()
for d in exp:
    prev = days[days.index(d) - 3]
    pack, _ = PC.build_pack(d, str(m), d_from=prev)
    with audit_muted():
        raw = PC.replay("VET_V1", d, pack, dict(LIVE), d_from=prev)
        new = PC.replay("VET_V1", d, pack, PC.to_runner_config("VET_V1", LIVE, d)[0], d_from=prev)
    os.unlink(pack)
    for rows, late, ex in ((raw, "raw", raw_exit), (new, "new", new_exit)):
        for r in rows:
            if r["entry_ts"] and hm(r["entry_ts"]) >= "15:00":
                if late == "raw":
                    raw_late += 1
                else:
                    new_late += 1
            if r.get("reason") == "EXPIRY_EXIT" and r.get("exit_ts"):
                ex.add(hm(r["exit_ts"]))
check("untranslated: expiry exits at 15:04 (runner default roll_time 15:00)", raw_exit == {"15:04"}, raw_exit)
check("translated: expiry exits at 15:14 — the live 15:15 square-off minute", new_exit == {"15:14"}, new_exit)
check("translated: no entry at/after the 15:00 cutoff", new_late == 0, new_late)
check("(untranslated entered after 15:00 on some days — the missing cutoff)", raw_late >= 1, raw_late)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PARITY VELVET-EXPIRY CHECKS PASSED")
