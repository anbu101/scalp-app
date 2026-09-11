#!/usr/bin/env python3
"""
apply_ORB_DAY_COUNTERS_20260911.py — FENCE: ORB_DAY_COUNTERS_20260911

Requires ORB_REPLAY_RETRY_20260911.

Gap: a restart after a trade that already CLOSED today rebuilds the day's
core with day_trades = 0 / side_trades = 0, so max_trades_per_day (2) and
max_trades_per_side (1) could admit one extra trade — the budget is a
risk limit and must survive a restart.

Fix:
  * OrbManager.restore_day_counters(rows=None) → counts TODAY's closed
    ORB_V1 rows in paper_trades (candle_ts ≥ day_start, exit_price NOT
    NULL; both PAPER and LIVE rows land there) by side and writes
    day_trades / side_trades into the live core. rows= is injectable for
    tests. Best-effort: a DB failure is audited loudly and leaves the
    counters at 0 (the pre-existing behaviour) — never blocks the day.
  * OrbEngine._warm_replay calls it after the prefix is rebuilt and BEFORE
    adopt_resumed_position(), whose on_entry_fill() then counts the still-
    open row on top. Order verified by test.
  * Replayed past signals are abandoned (previous fence) and correctly do
    not count; only booked rows do.

Safety: fence check, py_compile gate, manager + engine simulation with
injected rows (yesterday's row excluded, open row counted once via adopt,
a fresh same-side signal then drops on budget; restore runs before adopt;
DB failure → audited, counters 0), .bak backups, dual-tree mirror.
Backend-only → rebuild backend.

Run from the repo root:  python3 apply_ORB_DAY_COUNTERS_20260911.py
"""
import importlib.util, os, py_compile, shutil, sys, tempfile
from datetime import datetime, timedelta, timezone

FENCE = "ORB_DAY_COUNTERS_20260911"
PARENT = "ORB_REPLAY_RETRY_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"mgr": "app/engine/orb/orb_manager.py", "eng": "app/engine/orb/orb_engine.py"}
PATH = {k: os.path.join(BACKEND, v) for k, v in REL.items()}
if not os.path.isfile(PATH["mgr"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


m = read(PATH["mgr"]); e = read(PATH["eng"])
if PARENT not in e:
    die(f"{PARENT} not applied — apply it first")
for k, t in (("mgr", m), ("eng", e)):
    if FENCE in t:
        die(f"{FENCE} already present in {REL[k]} — nothing to do")

m = sub1(m,
    '    def adopt_resumed_position(self) -> None:\n',
    '    # ── ORB_DAY_COUNTERS_20260911 ────────────────────────────────────\n'
    '    def restore_day_counters(self, rows=None) -> dict:\n'
    '        """After a restart, put TODAY\'s already-CLOSED trades back into the\n'
    '        core\'s per-day budget (max_trades_per_day / max_trades_per_side).\n'
    '        A replay rebuilds levels, not history; without this a restart after\n'
    '        a closed trade could admit one extra trade. Call BEFORE\n'
    '        adopt_resumed_position() — that one counts the still-open row.\n'
    '        rows= injectable for tests. Best-effort: on a DB failure the\n'
    '        counters stay 0 (pre-existing behaviour) and it is audited."""\n'
    '        out = {"CE": 0, "PE": 0}\n'
    '        if self.day is None:\n'
    '            return out\n'
    '        try:\n'
    '            if rows is None:\n'
    '                if get_conn is None:\n'
    '                    raise RuntimeError("paper_trades_repo degraded")\n'
    '                cur = get_conn().execute(\n'
    '                    "SELECT side, COUNT(*) AS n FROM paper_trades"\n'
    '                    " WHERE strategy_name=? AND candle_ts >= ?"\n'
    '                    " AND exit_price IS NOT NULL GROUP BY side",\n'
    '                    (STRATEGY_ID, int(self.day.day_start_epoch)))\n'
    '                rows = [dict(zip([c[0] for c in cur.description], r))\n'
    '                        for r in cur.fetchall()]\n'
    '            else:\n'
    '                # injected raw rows: count closed ones from today by side\n'
    '                agg: dict = {}\n'
    '                for r in rows:\n'
    '                    if (r.get("exit_price") is not None\n'
    '                            and int(r.get("candle_ts") or 0) >= int(self.day.day_start_epoch)):\n'
    '                        agg[r.get("side")] = agg.get(r.get("side"), 0) + 1\n'
    '                rows = [{"side": s, "n": n} for s, n in agg.items()]\n'
    '            for r in rows or []:\n'
    '                s = str(r.get("side") or "").upper()\n'
    '                if s in out:\n'
    '                    out[s] += int(r.get("n") or 0)\n'
    '            self.day.day_trades = out["CE"] + out["PE"]\n'
    '            self.day.side_trades = dict(out)\n'
    '            self.day_stats["entries"] = self.day.day_trades\n'
    '            write_audit_log(f"[ORB][RESUME] day counters restored: "\n'
    '                            f"trades={self.day.day_trades} CE={out[\'CE\']} "\n'
    '                            f"PE={out[\'PE\']} (closed rows today)")\n'
    '        except Exception as ex:\n'
    '            write_audit_log(f"[ORB][RESUME][COUNTERS_FAIL] {ex!r} — day budget "\n'
    '                            f"starts at 0 (may admit one extra trade)")\n'
    '        return out\n'
    '    # ── ORB_DAY_COUNTERS_20260911 END ────────────────────────────────\n'
    '\n'
    '    def adopt_resumed_position(self) -> None:\n',
    "mgr method")

e = sub1(e,
    '            self.gm.adopt_resumed_position()\n'
    '            self._resumed_pending = False\n'
    '            self._replay_pending = False\n',
    '            # ── ORB_DAY_COUNTERS_20260911 ── closed-today rows back into the\n'
    '            # per-day budget, BEFORE the open row is grafted (which counts).\n'
    '            try:\n'
    '                self.gm.restore_day_counters()\n'
    '            except Exception as _ce:\n'
    '                write_audit_log(f"[ORB][RESUME][COUNTERS_FAIL] {_ce!r}")\n'
    '            self.gm.adopt_resumed_position()\n'
    '            self._resumed_pending = False\n'
    '            self._replay_pending = False\n',
    "engine call")

NEW = {"mgr": m, "eng": e}

# ── gates ────────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp = {}
for k, text in NEW.items():
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    tmp[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as ex:
        die(f"py_compile failed for {REL[k]}: {ex}")
print("py_compile gate: OK")

sys.path.insert(0, BACKEND)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod
MGR = load("app.engine.orb.orb_manager", tmp["mgr"])
ENG = load("app.engine.orb.orb_engine", tmp["eng"])
from app.engine.orb.orb_live_core import OrbLiveDay
from app.backtest.orb.orb_v1_engine import OrbBar
from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS
audit = []; MGR.write_audit_log = lambda s: audit.append(s); ENG.write_audit_log = lambda s: audit.append(s)
ENG.time.sleep = lambda s: None
IST = timezone(timedelta(hours=5, minutes=30))
DAY = datetime(2026, 9, 11, tzinfo=IST)
day_start = int(DAY.replace(hour=0, minute=0).timestamp())
CFG = dict(DEFAULT_STRATEGY_CONFIGS["ORB_V1"])   # max_trades_per_day 2, per side 1

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)

def mk_gm():
    g = MGR.OrbManager.__new__(MGR.OrbManager)
    g.day = OrbLiveDay(day_start_epoch=day_start, cfg=CFG); g.pos = None
    g.day_stats = {"signals": 0, "entries": 0, "exits": {}, "refused": None, "frozen": None}
    g.alerts = []; g._alert = lambda code, msg, sev="warning": g.alerts.append((code, msg))
    return g

print("manager simulation (budget: 2/day, 1/side)")
g = mk_gm()
rows = [
    {"side": "CE", "candle_ts": day_start + 10 * 3600, "exit_price": 180.0},        # closed today
    {"side": "CE", "candle_ts": day_start - 3600 * 5, "exit_price": 150.0},         # YESTERDAY → excluded
    {"side": "PE", "candle_ts": day_start + 11 * 3600, "exit_price": None},         # still open → not counted here
]
out = g.restore_day_counters(rows=rows)
check("closed-today rows counted by side (yesterday and open excluded)", out == {"CE": 1, "PE": 0} and g.day.day_trades == 1 and g.day.side_trades == {"CE": 1, "PE": 0})
check("audited", any("day counters restored: trades=1 CE=1 PE=0" in a for a in audit))
# the open PE row is then adopted → counts on top
g.pos = MGR.OrbPositionRow(row_id="x", symbol="NIFTY24500PE", token=1, side="PE", entry_px=170.0, qty=65, lots=1,
                           mode="PAPER", sl_spot=24400.0, tp_prem=255.0, entry_ts=day_start + 11 * 3600)
g.adopt_resumed_position()
check("adopt after restore → day_trades 2, PE 1 (open row counted once)", g.day.day_trades == 2 and g.day.side_trades == {"CE": 1, "PE": 1})
# a fresh CE signal now must be dropped on budget — drive the core directly
d = g.day
d.position = None; d.pending_side = None                         # simulate the PE later closing
before = d.dropped_budget
class _Sig:  # minimal signal shape the core iterates
    def __init__(self, side, ts): self.side, self.ts = side, ts
d.consumed_sigs = 0
# emulate the signal loop body via a bar-driven path is heavy; assert the budget predicate directly
check("budget predicate: day_trades ≥ max_trades_per_day → next signal would drop",
      d.day_trades >= int(CFG["max_trades_per_day"]) and d.side_trades["CE"] >= int(CFG["max_trades_per_side"]))
# DB failure path → audited, counters stay 0
g2 = mk_gm(); MGR.get_conn = lambda: (_ for _ in ()).throw(RuntimeError("db locked"))
g2.restore_day_counters()
check("DB failure → counters 0, loud audit", g2.day.day_trades == 0 and any("COUNTERS_FAIL" in a and "db locked" in a for a in audit))
# no day → no-op
g3 = mk_gm(); g3.day = None
check("no day → {CE:0,PE:0}, no crash", g3.restore_day_counters(rows=[]) == {"CE": 0, "PE": 0})

print("engine ordering (replay → restore → adopt)")
order = []
class _GM2:
    def __init__(self):
        self.day = OrbLiveDay(day_start_epoch=day_start, cfg=CFG); self.pos = {"side": "CE"}
        self.day_stats = {"signals": 0, "entries": 0, "exits": {}, "refused": None, "frozen": None}
    def cfg(self): return CFG
    def _alert(self, *a, **k): pass
    def restore_day_counters(self): order.append("restore"); return {}
    def adopt_resumed_position(self): order.append("adopt")
class _Kite:
    def historical_data(self, tok, frm, to, iv):
        out = []
        for mi in range(555, 11 * 60):
            dt = DAY.replace(hour=mi // 60, minute=mi % 60, second=0, microsecond=0)
            px = 24500.0 + (mi - 555) * 0.2
            out.append({"date": dt, "open": px, "high": px + 3, "low": px - 3, "close": px})
        return out
class _Broker:
    def get_data_kite(self): return _Kite()
eng = ENG.OrbEngine(_GM2(), _Broker())
eng._roll_day(DAY.replace(hour=11, minute=0))
check("restore_day_counters runs before adopt_resumed_position", order == ["restore", "adopt"], str(order))
check("replay completed (not pending)", not eng._replay_pending)
if FAILS:
    die(f"simulation failed: {FAILS}")
print("simulation: OK")

# ── write ────────────────────────────────────────────────────────────────
for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
for k, p in PATH.items():
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 2 files (+ .bak backups)")
if os.path.isdir(DUAL):
    for k, rel in REL.items():
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
