# backend/app/backtest/engine/test_lot_comp_echo.py
#
# ── LOT_COMP_ECHO_20260930 ── standalone:
#   cd backend && python3 app/backtest/engine/test_lot_comp_echo.py
# Defect: VET_V1, GC_V1 and FVG_V1 rebuild their echoed config from a whitelist
# of their own keys, so lot_comp_* never reached backtest_runs.config_json —
# the sizing ran (it reads the raw override) but the results page, the Tax
# card, the Compare page and the persist_run tax stamp all saw "no
# compounding". Fix: persist_run restores lot_comp_* from the submitted config
# (meta.config) and backfill_lot_comp_config() repairs runs already stored,
# from the queue job that produced them.
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
os.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")

FAILS = []


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if not ok:
        FAILS.append(name)


# results DB → a temp file (never the user's ~/.scalp-app/backtest/backtest.db)
_meta_dir = tempfile.mkdtemp(prefix="lot_comp_echo_")
import atexit, shutil   # noqa: E402
atexit.register(lambda: shutil.rmtree(_meta_dir, ignore_errors=True))
import app.backtest.repo.backtest_repo as R   # noqa: E402
R._db_path = lambda: Path(_meta_dir) / "backtest.db"
from app.backtest.repo import backtest_queue_repo as Q   # noqa: E402

src = open(os.path.join(HERE, "test_lot_comp_runners.py"), encoding="utf-8").read()
head = src.split("RUNNERS = [")[0].replace("while len(all_days) < 45:", "while len(all_days) < 30:")
ns = {"__file__": os.path.join(HERE, "test_lot_comp_runners.py"), "__name__": "_lcr_head"}
exec(compile(head, "lcr_head", "exec"), ns)
RUN_FROM, RUN_TO = ns["RUN_FROM"], ns["RUN_TO"]

K = {"lot_comp_mode": "equity", "lot_comp_capital_per_lot": 20000, "lot_comp_max_lots": 30,
     "lot_comp_dd_limit_pct": 10, "lot_comp_dd_mode": "proportional",
     "lot_comp_tax_pct": 30, "lot_comp_tax_fy_start_month": 3}


def run_raw(name):
    f, kw, cfg = ns["_runner"](name)
    kw["strategy_id"] = name
    raw = {**cfg, **K}
    return f(config_override=raw, **kw), raw


def meta_for(name, raw, with_config=True):
    m = {"strategy_id": name, "underlying": "NIFTY", "date_from": RUN_FROM.isoformat(),
         "date_to": RUN_TO.isoformat(), "created_at": 1}
    if with_config:
        m["config"] = raw
    return m


print("── persist_run keeps the submitted lot_comp keys ──")
stamps = {}
for name in ["VET_V1", "GC_V1", "FVG_V1", "TSG_V1"]:
    try:
        r, raw = run_raw(name)
        echoed = {k for k in K if k in (r.get("config") or {})}
        r["meta"] = meta_for(name, raw)
        rid = R.persist_run(r)
        got = R.get_run(rid)
        kept = {k for k in K if k in (got.get("config") or {})}
        check(f"{name}: runner echoed {len(echoed)}/{len(K)} keys → persisted {len(kept)}/{len(K)}", kept == set(K), sorted(set(K) - kept))
        check(f"{name}: tax stamp written", "lot_comp_tax_paid" in (got.get("summary") or {}))
        check(f"{name}: values are the submitted ones",
              all(got["config"][k] == K[k] for k in K))
        stamps[name] = (got["summary"].get("lot_comp_tax_paid"), got["summary"].get("lot_comp_tax_accrued"))
        if name == "TSG_V1":
            check("TSG: a runner that already echoed the keys is unchanged", echoed == set(K) and got["config"] == json.loads(json.dumps(r["config"])))
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(f"{name}: persists", False, repr(e))

print("── runner values win over the submitted ones (no silent overwrite) ──")
r, raw = run_raw("TSG_V1")
r["config"] = dict(r["config"], lot_comp_max_lots=12)          # pretend the runner normalised it
r["meta"] = meta_for("TSG_V1", raw)
got = R.get_run(R.persist_run(r))
check("echoed key kept, only MISSING keys restored", got["config"]["lot_comp_max_lots"] == 12)

print("── backfill repairs a run persisted before the fix ──")
try:
    r, raw = run_raw("VET_V1")
    r["meta"] = meta_for("VET_V1", raw, with_config=False)       # the old, stripped path
    rid = R.persist_run(r)
    before = R.get_run(rid)
    check("stripped run: no lot_comp keys, no tax stamp",
          not any(k in (before["config"] or {}) for k in K) and "lot_comp_tax_paid" not in before["summary"])
    job = Q.enqueue(strategy_id="VET_V1", underlying="NIFTY", date_from=RUN_FROM.isoformat(),
                    date_to=RUN_TO.isoformat(), config=raw)
    Q.mark_done(job["job_id"], rid)
    fixed = R.backfill_lot_comp_config()
    after = R.get_run(rid)
    check("backfill reports the run", [x["run_id"] for x in fixed] == [rid], fixed)
    check("backfill restored every key", all(after["config"].get(k) == K[k] for k in K))
    check("backfill wrote the tax stamp = the live-path stamp",
          (after["summary"].get("lot_comp_tax_paid"), after["summary"].get("lot_comp_tax_accrued")) == stamps.get("VET_V1"),
          (after["summary"].get("lot_comp_tax_paid"), stamps.get("VET_V1")))
    check("backfill kept every other summary field", all(after["summary"].get(k) == v for k, v in before["summary"].items()))
    check("backfill kept the trades", len(after["trades"]) == len(before["trades"]))
    check("backfill is idempotent", R.backfill_lot_comp_config() == [])
    dry = R.backfill_lot_comp_config(dry_run=True)
    check("dry run on a clean DB finds nothing", dry == [])
except Exception as e:
    import traceback
    traceback.print_exc()
    check("backfill executes", False, repr(e))

print("── backfill never touches runs without a queue job or without lot_comp ──")
r, raw = run_raw("GC_V1")
r["meta"] = meta_for("GC_V1", raw, with_config=False)
orphan = R.persist_run(r)                                        # direct Run-button run: no queue row
f, kw, _c = ns["_runner"]("FVG_V1"); kw["strategy_id"] = "FVG_V1"
r2 = f(config_override=dict(_c), **kw)        # a run with no compounding at all
r2["meta"] = meta_for("FVG_V1", _c, with_config=False)
plain = R.persist_run(r2)
j2 = Q.enqueue(strategy_id="FVG_V1", underlying="NIFTY", date_from=RUN_FROM.isoformat(), date_to=RUN_TO.isoformat(), config=_c)
Q.mark_done(j2["job_id"], plain)
check("nothing to backfill", R.backfill_lot_comp_config() == [])
check("orphan run untouched", not any(k in (R.get_run(orphan)["config"] or {}) for k in K))

print()
if FAILS:
    print(f"FAILED {len(FAILS)}:")
    for x in FAILS:
        print("  -", x)
    sys.exit(1)
print("ALL LOT_COMP_ECHO CHECKS PASSED")
