#!/usr/bin/env python3
# read_vet_wing_diag.py — pull wing-sourcing diagnostics for EXISTING runs.
#
# The wing split (real vs synthetic) is stored in each run's summary JSON
# under diag_vet; runs made before the audit-line patch never printed it, so
# this reads it straight from the backtest DB.
#
#   python3 read_vet_wing_diag.py            # all VET runs, newest first
#   python3 read_vet_wing_diag.py eb9589c0   # filter by run-id prefix
import json, os, sqlite3, sys
DB = os.path.expanduser("~/.scalp-app/backtest/backtest.db")
pat = sys.argv[1] if len(sys.argv) > 1 else ""
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")]
runs_tbl = next((t for t in tables if "run" in t.lower()
                 and "trade" not in t.lower()), None)
if not runs_tbl:
    sys.exit(f"no runs table found in {DB} (tables: {tables})")
cols = [r[1] for r in con.execute(f"PRAGMA table_info({runs_tbl})")]
shown = 0
for row in con.execute(f"SELECT * FROM {runs_tbl} ORDER BY rowid DESC"):
    rid = str(row[cols[0]])
    blob = " ".join(str(row[c]) for c in cols if row[c] is not None)
    if pat and pat not in rid and pat not in blob:
        continue
    if "diag_vet" not in blob:
        continue
    for c in cols:
        v = row[c]
        if isinstance(v, str) and "diag_vet" in v:
            try:
                d = json.loads(v).get("diag_vet") or {}
            except Exception:
                continue
            if not d:
                continue
            print(f"run {rid[:12]}  leg {d.get('leg_action','?')}"
                  f"  hedge_cap {d.get('hedge_max_premium')}")
            print(f"  wings: real {d.get('hedge_real', 0)}"
                  f" / synth {d.get('hedge_synth', 0)}"
                  f"  (synth fails {d.get('hedge_synth_fail', 0)},"
                  f" stale fills {d.get('hedge_stale_fills', 0)},"
                  f" entries skipped {d.get('no_hedge_entries', 0)})")
            print(f"  model-attributed wing P&L: "
                  f"{d.get('hedge_synth_pnl_gross', 0.0):+,.0f}"
                  f"   total wing P&L: {d.get('hedge_cost_total', 0.0):+,.0f}")
            shown += 1
            break
    if shown >= 12 and not pat:
        break
if not shown:
    print("no VET runs with diag_vet found"
          + (f" matching '{pat}'" if pat else ""))
