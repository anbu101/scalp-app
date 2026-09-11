#!/usr/bin/env python3
"""
apply_TSG_BANK_FIX1_20260911.py — FENCE: TSG_BANK_FIX1_20260911

Requires TSG_BANK_20260911 applied.

Bug (found by the user's first run: option 1 byte-identical to baseline,
0 re-entry rows over 1,732 days): the runner passed bank_reentry_cutoff to
the core as a MINUTE-OF-DAY (e.g. 870 for 14:30) while the core's `minutes`
are EPOCH SECONDS. `m_next <= 870` is never true for an epoch, so the bank
never armed — silently, because the fail-closed branch is the quiet one.
The unit tests used small integers for minutes, which hid it.

Fix: the runner converts the cutoff onto the day's clock
(day_start + cutoff_min*60) before calling the core; the core parameter is
renamed to bank_reentry_cutoff_ts and documented as "same unit as minutes".
A regression test drives the core with REAL epoch minutes.

Safety: fence checks (this one absent, parent present), py_compile gate,
the test file re-run on the patched module, an epoch-clock simulation that
must bank, .bak backups, dual-tree mirror. Backend-only → rebuild backend.

Run from the repo root:  python3 apply_TSG_BANK_FIX1_20260911.py
"""
import importlib.util, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = "TSG_BANK_FIX1_20260911"
PARENT = "TSG_BANK_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"runner": "app/backtest/tsg/backtest_tsg_runner.py",
       "test":   "app/backtest/tsg/test_tsg_runner.py"}
PATH = {k: os.path.join(BACKEND, v) for k, v in REL.items()}
if not os.path.isfile(PATH["runner"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


r = read(PATH["runner"]); t = read(PATH["test"])
if PARENT not in r:
    die(f"{PARENT} not applied — run apply_{PARENT}.py first")
if FENCE in r:
    die(f"{FENCE} already present — nothing to do")

# core: rename + document the unit
r = sub1(r,
    '    bank_reentry_cutoff_min: Optional[int] = None,   # minute-of-day\n',
    '    bank_reentry_cutoff_ts: Optional[int] = None,    # SAME UNIT AS `minutes` (epoch) ── TSG_BANK_FIX1_20260911 ──\n',
    "core param")
r = sub1(r,
    '                    and (bank_reentry_cutoff_min is None\n'
    '                         or m_next <= bank_reentry_cutoff_min)):\n',
    '                    and (bank_reentry_cutoff_ts is None\n'
    '                         or m_next <= bank_reentry_cutoff_ts)):   # ── TSG_BANK_FIX1_20260911 ──\n',
    "core check")
r = sub1(r,
    '    exit on the pending minute cancels the re-entry. Cutoff: no bank if\n',
    '    exit on the pending minute cancels the re-entry. Cutoff: no bank if\n',
    "core doc (no-op guard)") if 'exit on the pending minute cancels the re-entry. Cutoff: no bank if\n' in r else r
r = sub1(r,
    '    the re-entry minute is past bank_reentry_cutoff_min or is the EOD\n',
    '    the re-entry minute is past bank_reentry_cutoff_ts (same unit as\n'
    '    `minutes`, i.e. epoch in the runner) or is the EOD\n',
    "core doc unit")
# runner: convert onto the day's clock
r = sub1(r,
    '                               bank_reentry_cutoff_min=bank_cutoff_min,\n',
    '                               # ── TSG_BANK_FIX1_20260911 ── epoch on the\n'
    '                               # day\'s clock; a bare minute-of-day never\n'
    '                               # compared true against epoch minutes.\n'
    '                               bank_reentry_cutoff_ts=day_start + bank_cutoff_min * 60,\n',
    "runner call")

# test: rename in the existing appended test + an epoch-clock regression
t = sub1(t,
    '                           mtm_bank_target=900.0, bank_reentry_cutoff_min=150)\n',
    '                           mtm_bank_target=900.0, bank_reentry_cutoff_ts=150)   # ── TSG_BANK_FIX1_20260911 ──\n',
    "test rename")
t += '''

# ── TSG_BANK_FIX1_20260911 ── the runner's real clock: epoch minutes ──
def test_bank_fires_on_epoch_minutes_with_day_clock_cutoff():
    day_start = 1_788_000_000 - (1_788_000_000 % 86400)     # some midnight
    entry_ts = day_start + (9 * 60 + 16) * 60
    eod_ts = day_start + (15 * 60 + 26) * 60
    minutes = list(range(entry_ts + 60, eod_ts + 1, 60))
    marks = {}
    for k, m in enumerate(minutes):
        l1 = 85.0 - min(k, 20) * 1.0                          # decays 85→65 over 20 min (+1300)
        marks[m] = {"L1": l1, "L2": 85.0, "L3": 5.0, "L4": 5.0}
    cutoff_ts = day_start + (14 * 60 + 30) * 60
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, hedge_map=dict(HEDGES),
                           mtm_bank_target=1000.0, bank_reentry_cutoff_ts=cutoff_ts)
    assert res["banks"] == 1, res["banks"]
    assert res["exits"]["L1"]["reason"] == "MTM_BANK"
    assert res["reentries"]["L1#1"]["ts"] == res["exits"]["L1"]["ts"] + 60
    # and a cutoff BEFORE the bank minute blocks it (still on the epoch clock)
    res2 = simulate_tsg_day(_legs(), minutes, marks, 0.0, hedge_map=dict(HEDGES),
                            mtm_bank_target=1000.0, bank_reentry_cutoff_ts=entry_ts + 5 * 60)
    assert res2["banks"] == 0

test_bank_fires_on_epoch_minutes_with_day_clock_cutoff()
print("  ok  1 TSG_BANK_FIX1_20260911 epoch-clock test (appended)")
'''

TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tr = os.path.join(TMP, "backtest_tsg_runner.py"); tt = os.path.join(TMP, "test_tsg_runner.py")
for text, dst in ((r, tr), (t, tt)):
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        py_compile.compile(dst, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed: {e}")
print("py_compile gate: OK")

ic_dir = os.path.join(BACKEND, "app", "backtest", "ic")
env = dict(os.environ, PYTHONPATH=os.pathsep.join([TMP, ic_dir, BACKEND, os.environ.get("PYTHONPATH", "")]))
proc = subprocess.run([sys.executable, tt], cwd=TMP, env=env, capture_output=True, text=True, timeout=300)
for ln in (proc.stdout + proc.stderr).strip().splitlines()[-4:]:
    print("   ", ln)
if proc.returncode != 0:
    die("test_tsg_runner.py failed on the patched module")

# belt and braces: the old (buggy) unit must NOT bank, the fixed one must —
# proves the regression test actually discriminates.
sys.path.insert(0, ic_dir); sys.path.insert(0, BACKEND)
spec = importlib.util.spec_from_file_location("tsg_fix1", tr); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
ds = 1_788_000_000 - (1_788_000_000 % 86400); e0 = ds + 556 * 60; mins = list(range(e0 + 60, ds + 926 * 60 + 1, 60))
mk = {m: {"L1": 85.0 - min(k, 20), "L2": 85.0, "L3": 5.0, "L4": 5.0} for k, m in enumerate(mins)}
legs = lambda: [{"id": i, "action": a, "entry_price": p, "qty": 65} for i, a, p in (("L1", "SELL", 85.0), ("L2", "SELL", 85.0), ("L3", "BUY", 5.0), ("L4", "BUY", 5.0))]
bad = M.simulate_tsg_day(legs(), mins, mk, 0.0, mtm_bank_target=1000.0, bank_reentry_cutoff_ts=870)          # the old bug, reproduced
good = M.simulate_tsg_day(legs(), mins, mk, 0.0, mtm_bank_target=1000.0, bank_reentry_cutoff_ts=ds + 870 * 60)
print(f"  minute-of-day cutoff (old bug) banks={bad['banks']}  |  day-clock cutoff banks={good['banks']}")
if not (bad["banks"] == 0 and good["banks"] == 1):
    die("discriminating check failed")
print("behavioural gate: OK")

for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
with open(PATH["runner"], "w", encoding="utf-8") as f: f.write(r)
with open(PATH["test"], "w", encoding="utf-8") as f: f.write(t)
print("written 2 files (+ .bak backups)")
if os.path.isdir(DUAL):
    for k, rel in REL.items():
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
