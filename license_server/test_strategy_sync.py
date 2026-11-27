# license_server/test_strategy_sync.py
# ── LICENSE_SYNC_ADMIN_ONLY_20261127 ── (TD-114) TC-LICSYNC-0001…0005
# The license deploy's gate (check_strategy_sync.py) against the app: Tripwire
# (LVB_V1) is admin-only — never licensable, so never demanded — and the
# Strategy-config template (strategy_defaults.json) equals the app's defaults,
# which are also what Anbu runs (IC_V2 hedge_close_when_flat true, VET_V1
# hedge_max_premium 5).
#   python3 license_server/test_strategy_sync.py      (from the repo root)
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "backend"))
import check_strategy_sync as C       # noqa: E402
import gen_strategy_defaults as G     # noqa: E402
from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS   # noqa: E402

FAILS, OK = [], [0]


def check(n, c, d=""):
    if c:
        OK[0] += 1
        print(f"  PASS  {n}")
    else:
        FAILS.append(n)
        print(f"  FAIL  {n}  [{str(d)[:400]}]")


def run(**kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = C.main(**kw)
    return rc, buf.getvalue()


rc, out = run()
check("TC-LICSYNC-0001 the deploy gate passes on this tree (no SKIP_STRATEGY_SYNC needed)",
      rc == 0 and "in sync" in out and "templates current" in out, out)

tmpl = json.loads((HERE / "strategy_defaults.json").read_text())
fresh, why = C.fresh_defaults()
check("TC-LICSYNC-0002 the template equals the app's defaults = what Anbu runs: IC_V2 hedge_close_when_flat true, "
      "VET_V1 hedge_max_premium 5",
      fresh is not None and json.dumps(tmpl, sort_keys=True) == json.dumps(fresh, sort_keys=True)
      and tmpl["IC_V2"].get("hedge_close_when_flat") is True and float(tmpl["VET_V1"]["hedge_max_premium"]) == 5.0,
      (why, tmpl.get("IC_V2", {}).get("hedge_close_when_flat"), tmpl.get("VET_V1", {}).get("hedge_max_premium")))

check("TC-LICSYNC-0003 Tripwire (LVB_V1) is an app strategy marked admin-only: no template, no chip, not a known id",
      "LVB_V1" in DEFAULT_STRATEGY_CONFIGS and "LVB_V1" in G.ADMIN_ONLY and "LVB_V1" not in tmpl
      and "LVB_V1" not in C.admin_all_strategies(HERE / "admin_ui.html")
      and "LVB_V1" not in C.server_known_ids(HERE / "server.py"))

tmp = Path(tempfile.mkdtemp(prefix="licsync_"))
ui = (HERE / "admin_ui.html").read_text()
(tmp / "admin_ui.html").write_text(ui.replace('const ALL_STRATEGIES = ["SCALP_V1",', 'const ALL_STRATEGIES = ["LVB_V1","SCALP_V1",', 1))
rc2, out2 = run(admin=tmp / "admin_ui.html")
check("TC-LICSYNC-0004 putting an admin-only strategy on a licensable list fails the gate, saying so",
      rc2 == 1 and "admin-only [LVB_V1]" in out2, out2)

(tmp / "admin_ui.html").write_text(ui.replace('"VET_V1",', "", 1))
rc3, out3 = run(admin=tmp / "admin_ui.html")
bad = dict(tmpl)
bad["VET_V1"] = dict(bad["VET_V1"], hedge_max_premium=3.0)
(tmp / "d.json").write_text(json.dumps(bad))
rc4, out4 = run(defaults=tmp / "d.json")
check("TC-LICSYNC-0005 real drift is still caught: a strategy missing from the chips, a stale template value",
      rc3 == 1 and "VET_V1" in out3 and rc4 == 1 and "stale for [VET_V1]" in out4, (out3, out4))
shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}  ({OK[0]}/{OK[0] + len(FAILS)})")
sys.exit(1 if FAILS else 0)
