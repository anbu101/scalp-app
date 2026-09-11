#!/usr/bin/env python3
"""
apply_ORB_RECONCILE_20260911.py — FENCE: ORB_RECONCILE_20260911

Situation (2026-09-11): the working copy of backend/app/engine/orb/
orb_engine.py is an UNCOMMITTED pre-FLEET copy with ORB_SILENTZERO_20260905
applied (same import bug fixed a second way). It lacks ORB_LATEBOOT and the
FLEET in-app alerts; origin/main (commit 3d95aa4f) has FLEET. Both cannot
stand — the running app was built from the local copy, which is why ORB
refused every restarted day.

Reconciliation = origin/main (FLEET) + the two SILENTZERO pieces worth
keeping, so every later ORB fence applies on top:
  * module-level `from app.engine.ic.ic_selection import snapshot_weekly_chain`
    (an ImportError now fails at boot where the real-import smoke sees it;
    the lazy import inside _chain_snapshot is dropped)
  * the config-sanity guard in _roll_day (empty/partial cfg → CFG_MISSING
    alert, day refused, instead of a KeyError-per-bar silent zero day)
SILENTZERO's CHAIN_EMPTY alert is covered by FLEET's CHAIN_FAIL alerts;
its audit-only NO_CANDIDATE is superseded by FLEET's alert.

How: the base is read with `git show origin/main:<path>` (run `git fetch
origin` first). The local file is backed up as .bak-ORB_RECONCILE_20260911
(it is otherwise lost — it was never committed).

Safety: refuses unless the local file is the SILENTZERO variant and the
git base is the FLEET variant; py_compile gate; the existing ORB suites
run against the reconciled module; a snapshot simulation (module-level
import used, tuple contract unpacked, CFG_MISSING guard fires). Then run
apply_ORB_REPLAY_RETRY_20260911.py and apply_ORB_DAY_COUNTERS_20260911.py.

Run from the repo root:  python3 apply_ORB_RECONCILE_20260911.py
"""
import importlib.util, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = "ORB_RECONCILE_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = "backend/app/engine/orb/orb_engine.py"
P = os.path.join(ROOT, REL)
if not os.path.isfile(P):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


local = read(P)
if FENCE in local:
    die(f"{FENCE} already present — nothing to do")
if "ORB_LATEBOOT" in local:
    die("local orb_engine.py already has ORB_LATEBOOT — nothing to reconcile; "
        "run apply_ORB_REPLAY_RETRY_20260911.py directly")
if "ORB_SILENTZERO_20260905" not in local:
    die("local orb_engine.py is neither the SILENTZERO variant nor FLEET — "
        "send me the file before doing anything")

try:
    base = subprocess.run(["git", "show", f"origin/main:{REL}"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout
except Exception as e:
    die(f"could not read origin/main:{REL} — run `git fetch origin` first ({e})")
if "ORB_LATEBOOT" not in base or "FLEET_EOD_ALERT_FIX_20260908" not in base:
    die("origin/main orb_engine.py is not the FLEET variant — stop and check git")

# ── SILENTZERO piece 1: module-level import; drop the lazy one ──────────
r = sub1(base,
    'from app.backtest.orb.backtest_orb_runner import pick_candidate\n',
    'from app.backtest.orb.backtest_orb_runner import pick_candidate\n'
    '# ── ORB_SILENTZERO_20260905 (kept by ORB_RECONCILE_20260911) ── MODULE-LEVEL\n'
    '# on purpose: the original lazy import pointed at a module that does not\n'
    '# exist and the try/except turned every signal into a silent NO_CANDIDATE.\n'
    '# Top-level imports are what Gate-2 and the real-import smoke can catch.\n'
    'from app.engine.ic.ic_selection import snapshot_weekly_chain\n',
    "module import")
r = sub1(r,
    '        try:\n'
    '            from app.engine.ic.ic_selection import snapshot_weekly_chain\n'
    '            api_key = getattr(kite, "api_key", None)\n',
    '        try:\n'
    '            api_key = getattr(kite, "api_key", None)\n',
    "drop lazy import")

# ── SILENTZERO piece 2: config-sanity guard in _roll_day ─────────────────
r = sub1(r,
    '        cfg = dict(self.gm.cfg())\n'
    '        self.gm.day = OrbLiveDay(day_start_epoch=self._day_start_epoch(now),\n'
    '                                 cfg=cfg)\n',
    '        cfg = dict(self.gm.cfg())\n'
    '        if "orb_minutes" not in cfg or "sl_points" not in cfg:\n'
    '            # ── ORB_SILENTZERO_20260905 (kept by ORB_RECONCILE_20260911) ──\n'
    '            # an empty/partial config means the loader path is broken; the\n'
    '            # old code KeyError\'d on every bar into the loop\'s catch-all —\n'
    '            # a perfectly silent zero-trade day (2026-09-05). Fail closed\n'
    '            # and SAY SO once.\n'
    '            self.gm._alert("CFG_MISSING", "strategy config empty/partial — "\n'
    '                           "day refused (loader path broken?)", "critical")\n'
    '            self.gm.day = None\n'
    '            self.gm.day_stats = {"signals": 0, "entries": 0, "exits": {},\n'
    '                                 "refused": "CFG_MISSING", "frozen": None}\n'
    '            return\n'
    '        self.gm.day = OrbLiveDay(day_start_epoch=self._day_start_epoch(now),\n'
    '                                 cfg=cfg)\n',
    "cfg guard")
r = r.replace("# ── ORB_V1 ENGINE ── live day loop. Fence: ORB_LIVE_20260903\n",
              "# ── ORB_V1 ENGINE ── live day loop. Fence: ORB_LIVE_20260903\n"
              "# ── ORB_RECONCILE_20260911 ── origin/main (FLEET) + SILENTZERO's\n"
              "#    module-level import and cfg guard; see the apply script.\n", 1)
if FENCE not in r:
    die("header anchor missing — file layout changed")

# ── gates ────────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmpf = os.path.join(TMP, "orb_engine.py")
with open(tmpf, "w", encoding="utf-8") as f:
    f.write(r)
try:
    py_compile.compile(tmpf, doraise=True)
except py_compile.PyCompileError as e:
    die(f"py_compile failed: {e}")
print("py_compile gate: OK")

sys.path.insert(0, BACKEND)
spec = importlib.util.spec_from_file_location("app.engine.orb.orb_engine", tmpf)
ORB = importlib.util.module_from_spec(spec); sys.modules[spec.name] = ORB; spec.loader.exec_module(ORB)
import app.engine.ic.ic_selection as ICS
audit = []; ORB.write_audit_log = lambda s: audit.append(s)
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)
print("reconciled-module simulation")
check("module-level import present (ImportError would fail at boot)", hasattr(ORB, "snapshot_weekly_chain"))
check("ORB_LATEBOOT retained from FLEET", "ORB_LATEBOOT" in r and "_warm_replay(now)" in r)
opened, alerts = [], []
class _GM:
    def __init__(self, cfg): self._cfg = cfg; self.day = None; self.day_stats = {}; self.pos = None
    def cfg(self): return self._cfg
    def _alert(self, code, msg, sev="warning"): alerts.append((code, msg))
    def open_trade(self, **k): opened.append(k); return True
class _K: api_key = "k"; access_token = "t"
class _B:
    def get_data_kite(self): return _K()
# snapshot via the MODULE-LEVEL name (patch the module attribute, not ic_selection)
ORB.snapshot_weekly_chain = lambda *a, **k: ("2026-09-15",
    [{"tradingsymbol": "NIFTY24500CE", "instrument_token": 11, "instrument_type": "CE"}], {"NIFTY24500CE": 185.0})
e = ORB.OrbEngine(_GM({"premium_min": 150, "premium_max": 200}), _B())
e.gm.day = type("D", (), {"on_entry_abandoned": lambda self: None})(); e.gm.day_stats = {"signals": 0}
e._on_signal("CE", 1_700_000_000, 24510.0)
check("signal → tuple contract unpacked → candidate opened", len(opened) == 1 and opened[0]["symbol"] == "NIFTY24500CE")
# FLEET alerts intact
opened.clear(); alerts.clear()
ORB.snapshot_weekly_chain = lambda *a, **k: (None, [], {})
e2 = ORB.OrbEngine(_GM({"premium_min": 150, "premium_max": 200}), _B())
e2.gm.day = type("D", (), {"on_entry_abandoned": lambda self: None})(); e2.gm.day_stats = {"signals": 0}
e2._on_signal("PE", 1_700_000_000, 24510.0)
check("empty snapshot → CHAIN_FAIL in-app alert (FLEET) retained", any(a[0] == "CHAIN_FAIL" for a in alerts) and not opened)
# cfg guard (SILENTZERO) fires
alerts.clear()
e3 = ORB.OrbEngine(_GM({}), _B()); e3._roll_day(ORB.now_ist())
check("empty cfg → CFG_MISSING alert, day None (SILENTZERO guard kept)", any(a[0] == "CFG_MISSING" for a in alerts) and e3.gm.day is None)
if FAILS:
    die(f"simulation failed: {FAILS}")
# existing suites against the reconciled module (they import the manager/core, unaffected) — sanity only
for t in ("app/engine/orb/test_orb_manager.py", "app/engine/orb/test_orb_live_core.py"):
    pr = subprocess.run([sys.executable, t], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    ok = pr.returncode == 0 and "PASSED" in pr.stdout
    check(f"{os.path.basename(t)} passes", ok, pr.stdout[-200:] + pr.stderr[-200:])
if FAILS:
    die(f"suites failed: {FAILS}")
print("simulation: OK")

# ── write ────────────────────────────────────────────────────────────────
shutil.copy2(P, P + f".bak-{FENCE}")           # the never-committed SILENTZERO copy
with open(P, "w", encoding="utf-8") as f:
    f.write(r)
print(f"written {REL} (+ .bak-{FENCE} = your local SILENTZERO copy)")
if os.path.isdir(DUAL):
    dst = os.path.join(DUAL, REL.replace("backend/", "", 1)); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(P, dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Next: apply_ORB_REPLAY_RETRY_20260911.py → apply_ORB_DAY_COUNTERS_20260911.py → rebuild")
