# backend/app/parity/test_parity_cfg_fallback.py
#
# ── PARITY_CFG_FALLBACK_20261102 ── a position carried in from a day before
# the recorder captured configs no longer fails the comparison: the earliest
# snapshot between that day and the compared day is used, with a note.
#   cd backend && PYTHONPATH=$PWD python3 app/parity/test_parity_cfg_fallback.py
import json
import os
import sys
import tempfile
from datetime import date

TMP = tempfile.mkdtemp(prefix="parity_cfg_")
os.environ["HOME"] = TMP
from app.recorder import rec_db as RD                     # noqa: E402
from app.parity import parity_core as PC                  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


check("throw-away HOME in force", str(RD.db_path()).startswith(TMP), RD.db_path())
c = RD.connect()
for day, lots in (("2026-10-29", 9), ("2026-10-30", 10), ("2026-11-02", 11), ("2026-11-03", 12)):
    c.execute("INSERT INTO session_config (day, strategy_id, mode, degraded, config_json, config_hash, captured_at) "
              "VALUES (?,?,?,?,?,?,?)", (day, "VET_V1", "PAPER", 0, json.dumps({"lots": lots}), "h",
                                         PC.day_start(date.fromisoformat(day)) + 9 * 3600 + 14 * 60))
c.commit()
c.close()

print("── config_for")
cfg, notes = PC.config_for(date(2026, 10, 26), "VET_V1")
check("without `until` a missing day is still None (unchanged behaviour)", cfg is None and not notes, (cfg, notes))
cfg, notes = PC.config_for(date(2026, 10, 26), "VET_V1", until=date(2026, 11, 2))
check("carried from 26 Oct (no capture) → the EARLIEST later snapshot (29 Oct)", cfg == {"lots": 9}, cfg)
check("…and the note says which day's config was used", any("captured on 2026-10-29" in n for n in notes), notes)
cfg, notes = PC.config_for(date(2026, 10, 30), "VET_V1", until=date(2026, 11, 2))
check("a day WITH its own snapshot uses it, no fallback note", cfg == {"lots": 10} and not notes, (cfg, notes))
c = RD.connect()
c.execute("DELETE FROM session_config WHERE day IN ('2026-10-29','2026-10-30','2026-11-02')")
c.commit()
c.close()
cfg, notes = PC.config_for(date(2026, 10, 26), "VET_V1", until=date(2026, 11, 2))
check("a snapshot AFTER the compared day is never used", cfg is None, (cfg, notes))
cfg, _ = PC.config_for(date(2026, 10, 26), "TMA_V2", until=date(2026, 11, 3))
check("another strategy's snapshot is never used", cfg is None)

print("── run_day passes the compared day as the limit")
seen = {}


def spy(d, sid, until=None):
    seen.update(d=d, sid=sid, until=until)
    return None, []


real = PC.config_for
PC.config_for = spy
PC.app_legs = lambda sid, d: ([{"symbol": "X", "side": "SELL", "type": "CE", "strike": 1.0, "qty": 650,
                                "entry_ts": PC.day_start(date(2026, 10, 26)) + 36000, "exit_ts": None,
                                "entry": 1.0, "exit": None, "reason": None, "book": "PAPER", "open": True,
                                "carried_in": True}], [])
r = PC.run_day("VET_V1", date(2026, 11, 2))
PC.config_for = real
check("run_day asks for the carried-in day's config with until = the compared day",
      seen.get("d") == date(2026, 10, 26) and seen.get("until") == date(2026, 11, 2), seen)
check("…and still explains a genuinely missing config", not r["ok"] and "no saved config" in r["error"], r)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PARITY CONFIG-FALLBACK CHECKS PASSED")
