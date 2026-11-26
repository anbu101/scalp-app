#!/usr/bin/env python3
# scripts/mutation/test_mutmut_run.py — ── MUTMUT_20261117 ── (TD-043) [+ MUT_FLAKY_RETRY_20261126: TC-MUT-0013…0015] TC-MUT-0001…0012 · MUT_SKIP_LOGS_20261122 · MUT_TMP_SWEEP_20261122
#
# Checks the weekend mutation runner without mutmut: scope and suite choice,
# the safety rails (work folder, environment, network block), the per-suite
# fork shim, resume/restart stamps and the summary table.
#   python3 scripts/mutation/test_mutmut_run.py     (stdlib only; exit 1 on any failure)
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
F = 0


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = load("mutmut_run_t", os.path.join(HERE, "mutmut_run.py"))

# 0001 scope: the five modules exist; named suites are offered; no live probe / CI-skipped suite is
missing = [m for m, s in R.MODULES.items() if not os.path.exists(os.path.join(R.BACKEND, s["file"]))]
chk("TC-MUT-0001", "every module file exists", not missing, missing)
bad = R.ci_lists()
offered = {m: R.suites_for(m) for m in R.MODULES}
leak = [(m, s) for m, ss in offered.items() for s in ss if s in bad or s.startswith("app/tools/")]
named = [(m, s) for m, sp in R.MODULES.items() for s in sp["suites"]
         if os.path.exists(os.path.join(R.BACKEND, s)) and s not in offered[m]]
chk("TC-MUT-0001", "named suites offered; CI SKIP/KNOWN_FAIL and app/tools never", not leak and not named,
    f"leak={leak} named-missing={named}")
chk("TC-MUT-0001", "real-credential suite tools/test_zerodha_executor.py is out of every module",
    all("app/tools/test_zerodha_executor.py" not in v for v in offered.values()))

# 0002 work-folder guard
home = tempfile.mkdtemp()
cases = {os.path.join(R.ROOT, "x"): True, os.path.join(home, ".scalp-app", "m"): True,
         os.path.join(home, "Library", "Application Support", "m"): True, home: True,
         os.path.join(home, "scalp-mutation"): False}
got = {p: bool(R.unsafe_work_dir(p, home=home)) for p in cases}
chk("TC-MUT-0002", "refuses repo / ~/.scalp-app / App Support / HOME; accepts ~/scalp-mutation", got == cases, got)

# 0003 environment
env = R.clean_env("/w", base={"KITE_API_KEY": "k", "TELEGRAM_BOT_TOKEN": "t", "HTTPS_PROXY": "p",
                              "ZERODHA_PASSWORD": "x", "PATH": "/bin", "PYTHONPATH": "/repo/backend"})
chk("TC-MUT-0003", "secrets, real proxy and PYTHONPATH stripped; HOME in work; SCALP_TEST_MODE=1",
    "KITE_API_KEY" not in env and "TELEGRAM_BOT_TOKEN" not in env and "ZERODHA_PASSWORD" not in env
    and env["HTTPS_PROXY"] == "http://127.0.0.1:9" and env["HOME"] == "/w/home"
    and env["SCALP_TEST_MODE"] == "1" and env["PATH"] == "/bin" and "PYTHONPATH" not in env, env)

# 0004 broker_truth areas
names = {"app.execution.broker_truth.x_positions_strict__mutmut_1": "strict reads",
         "app.execution.broker_truth.xǁLiveLedgerǁreserve__mutmut_2": "ledger",
         "app.execution.broker_truth.x_send_exit_once__mutmut_3": "exit once",
         "app.execution.broker_truth.x_blind_stop_orders__mutmut_4": "blind stop",
         "app.execution.broker_truth.xǁGttKillListǁretry__mutmut_5": "kill list",
         "app.execution.broker_truth.x_flat_proven__mutmut_6": "ledger"}
got = {n: R.area_of(n) for n in names}
chk("TC-MUT-0004", "mutant names map to the five broker_truth areas", got == names, got)

# 0005 status codes and score
res = {"a": 1, "b": 0, "c": 36, "d": 33, "e": None, "f": -11}
t = R.tally(res)["all"]
chk("TC-MUT-0005", "killed+timeout over tested; no-tests / not-checked / segfault kept apart",
    (t["killed"], t["survived"], t["timeout"], t["no tests"], t["not checked"], t["suspicious"], t["score"])
    == (1, 1, 1, 1, 1, 1, 66.7), t)

# 0006 suite kind = CI's rule
k1 = R.suite_kind(os.path.join(R.BACKEND, "app/execution/test_kill_switch.py"))
k2 = R.suite_kind(os.path.join(R.BACKEND, "app/execution/test_blind_stop.py"))
chk("TC-MUT-0006", "pytest file → pytest, standalone script → script", (k1, k2) == ("pytest", "script"), (k1, k2))

# 0007 network block (conftest)
code = ("import socket,runpy;runpy.run_path(%r)\n"
        "s=socket.socket()\ntry:\n s.connect(('10.255.255.1',80));print('OPEN')\n"
        "except OSError as e:\n print('BLOCK' if 'MUTMUT_20261117' in str(e) else 'OTHER',e)\n"
        "l=socket.socket();l.bind(('127.0.0.1',0));l.listen(1);c=socket.socket();c.connect(l.getsockname());"
        "print('LOOP')") % os.path.join(HERE, "mut_shim_conftest.py")
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout
chk("TC-MUT-0007", "non-loopback connect blocked at once, loopback allowed", "BLOCK" in out and "LOOP" in out,
    out)

# 0008 fork shim: isolation, exit codes, bare-name alias
ws = tempfile.mkdtemp()
os.makedirs(os.path.join(ws, "app", "pkg"))
os.makedirs(os.path.join(ws, "tests"))
for p in ("app/__init__.py", "app/pkg/__init__.py"):
    open(os.path.join(ws, p), "w").close()
with open(os.path.join(ws, "app/pkg/mod.py"), "w") as f:
    f.write("def f():\n    return 1\n")
suites = {"app/pkg/test_patch.py": "import time\ntime.sleep = lambda s: None\nimport sys\nsys.exit(0)\n",
          "app/pkg/test_clean.py": "import time,sys\nsys.exit(0 if time.sleep.__name__ == 'sleep' else 1)\n",
          "app/pkg/test_fail.py": "assert False, 'boom'\n",
          "app/pkg/test_alias.py": "import mod,sys\nsys.exit(0 if mod.f.__module__ == 'app.pkg.mod' else 1)\n",
          "app/pkg/test_hang.py": "import time\nwhile True:\n    time.sleep(0.2)\n"}
for p, src in suites.items():
    with open(os.path.join(ws, p), "w") as f:
        f.write(src)
with open(os.path.join(ws, "tests", "suites.json"), "w") as f:
    json.dump({"suites": [], "aliases": {"mod": "app.pkg.mod"}}, f)
shutil.copy2(os.path.join(HERE, "mut_shim_suites.py"), os.path.join(ws, "tests", "test_mut_suites.py"))
probe = ("import sys,time;sys.path.insert(0,%r);import test_mut_suites as S\n"
         "r=[S.run_suite(p,'script',%r) for p in ('app/pkg/test_patch.py','app/pkg/test_clean.py',"
         "'app/pkg/test_fail.py','app/pkg/test_alias.py')]\n"
         "h=S.run_suite('app/pkg/test_hang.py','script',%r,limit_s=2)\nprint(r, time.sleep.__name__, h)") % (
    os.path.join(ws, "tests"), ws, ws)
pr = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
chk("TC-MUT-0008", "own fork per suite: patches don't leak, failures count, bare import aliased, hang killed",
    "[0, 0, 1, 0] sleep 124" in pr.stdout, pr.stdout[-400:] + pr.stderr[-400:])

# 0009 stamps: same inputs resume, a changed module starts again
fake = tempfile.mkdtemp()
os.makedirs(os.path.join(fake, "app", "execution"))
open(os.path.join(fake, "app", "__init__.py"), "w").close()
with open(os.path.join(fake, "app", "execution", "m.py"), "w") as f:
    f.write("X = 1\n")
with open(os.path.join(fake, "app", "execution", "test_m.py"), "w") as f:
    f.write("import sys\nsys.exit(0)\n")
work = tempfile.mkdtemp()
os.makedirs(os.path.join(work, ".venv", "bin"))
os.symlink(sys.executable, os.path.join(work, ".venv", "bin", "python"))
R.BACKEND = fake
R.MODULES = {"m": {"file": "app/execution/m.py", "reach": r"\bm\b", "suites": ["app/execution/test_m.py"]}}
R.ORDER = ["m"]
os.makedirs(os.path.join(fake, "app", "state", "logs", "debug"))
with open(os.path.join(fake, "app", "state", "logs", "debug", "candle_debug.tsv"), "w") as f:
    f.write("x" * 4096)
with open(os.path.join(fake, "app", "state", "keep.json"), "w") as f:
    f.write("{}")
ws1, n1 = R.build_workspace(work, "m")
open(os.path.join(ws1, "marker"), "w").close()
# 0011 (DEF-083) app run-time logs are never copied; an old workspace that has them is pruned on resume
no_logs = not os.path.exists(os.path.join(ws1, "app", "state", "logs"))
keeps_rest = os.path.exists(os.path.join(ws1, "app", "state", "keep.json"))
os.makedirs(os.path.join(ws1, "app", "state", "logs", "debug"), exist_ok=True)
os.makedirs(os.path.join(ws1, "mutants", "app", "state", "logs"))
with open(os.path.join(ws1, "mutants", "app", "state", "logs", "old.tsv"), "w") as f:
    f.write("x" * 4096)                  # bytes freed must not turn into a restart note (n2 == "")
os.makedirs(os.path.join(ws1, "mutants", "app", "execution"), exist_ok=True)
with open(os.path.join(ws1, "mutants", "app", "execution", "m.py.meta"), "w") as f:
    f.write("{}")
ws2, n2 = R.build_workspace(work, "m")
kept = os.path.exists(os.path.join(ws2, "marker"))
pruned = not any(os.path.exists(os.path.join(ws2, *p)) for p in (("app", "state", "logs"),
                                                                    ("mutants", "app", "state", "logs")))
meta_kept = os.path.exists(os.path.join(ws2, "mutants", "app", "execution", "m.py.meta"))
chk("TC-MUT-0011", "app/state/logs not copied (rest of app/state is); pruned from an old workspace + "
    "mutants/ on resume, results kept", no_logs and keeps_rest and pruned and meta_kept,
    (no_logs, keeps_rest, pruned, meta_kept))
shutil.rmtree(os.path.join(ws2, "mutants"))
with open(os.path.join(fake, "app", "execution", "m.py"), "a") as f:
    f.write("Y = 2\n")
ws3, n3 = R.build_workspace(work, "m")
with open(os.path.join(ws3, "tests", "suites.json")) as f:
    sj = json.load(f)
with open(os.path.join(ws3, "pyproject.toml")) as f:
    pp = f.read()
chk("TC-MUT-0009", "same inputs → resume (results kept); module changed → started again",
    kept and n2 == "" and n3.startswith("source changed") and not os.path.exists(os.path.join(ws3, "marker")),
    (kept, n2, n3))
chk("TC-MUT-0009", "config mutates only the module; the suite passed preflight",
    'only_mutate = ["app/execution/m.py"]' in pp and len(sj["suites"]) == 1
    and sj["suites"][0]["path"] == "app/execution/test_m.py" and sj["suites"][0]["kind"] == "script"
    and sj["suites"][0]["secs"] >= 0, (pp, sj))

# 0010 summary table
os.makedirs(os.path.join(ws3, "mutants", "app", "execution"), exist_ok=True)
with open(os.path.join(ws3, "mutants", "app", "execution", "m.py.meta"), "w") as f:
    json.dump({"exit_code_by_key": {"a": 1, "b": 0, "c": 1}}, f)
tb = R.table({"m": {"seconds": 120, "status": "done"}}, work)
row = [ln for ln in tb.splitlines() if ln.startswith("| m ")]
chk("TC-MUT-0010", "table row: 3 mutants, 2 killed, 1 survived, 66.7 %, 2 min, done",
    row and all(x in row[0] for x in ("| 3 ", "| 2 ", "| 1 ", "66.7", "| done")), tb)

# 0010 survivor file:line lands on the right copy of a repeated line
with open(os.path.join(ws3, "app", "execution", "m.py"), "w") as f:
    f.write("def f(s):\n    if s == 1:\n        return 'DEAD'\n    x = 2\n    if s == 3:\n        return 'DEAD'\n")
diff = ("--- app/execution/m.py\n+++ app/execution/m.py\n@@ -1,3 +1,3 @@\n     x = 2\n     if s == 3:\n"
        "-        return 'DEAD'\n+        return 'dead'\n")
got = R.survivor_line(ws3, "app/execution/m.py", diff)
chk("TC-MUT-0010", "survivors.txt line of a repeated line: the copy in the mutated hunk (line 6, not 3)",
    got == "app/execution/m.py:6", got)

# 0012 (DEF-085) own TMPDIR per module, swept while mutmut runs, emptied after; low disk pauses
import time as _time                     # noqa: E402
e1, e2 = R.clean_env(work, mod="m"), R.clean_env(work)
tm = os.path.join(work, "tmp", "m")
chk("TC-MUT-0012", "TMPDIR / TMP / TEMP = <work>/tmp/<module> (shared one without a module), created",
    e1["TMPDIR"] == e1["TMP"] == e1["TEMP"] == tm and os.path.isdir(tm)
    and e2["TMPDIR"] == os.path.join(work, "tmp", "_shared"), (e1.get("TMPDIR"), e2.get("TMPDIR")))
for nm in ("old_dir", "new_dir"):
    os.makedirs(os.path.join(tm, nm, "deep"))
with open(os.path.join(tm, "old_file"), "w") as f:
    f.write("x")
now = _time.time()
for nm in ("old_dir", "old_file"):
    os.utime(os.path.join(tm, nm), (now - 1000, now - 1000))
n = R.sweep_tmp(tm, 900, now=now)
left = sorted(os.listdir(tm))
chk("TC-MUT-0012", "sweep deletes entries older than the age (a folder with its contents), keeps newer",
    n == 2 and left == ["new_dir"], (n, left))
chk("TC-MUT-0012", "sweep with age 0 empties it; a missing folder is 0, no error",
    R.sweep_tmp(tm, 0) == 1 and os.listdir(tm) == [] and R.sweep_tmp(os.path.join(work, "nope"), 0) == 0)
chk("TC-MUT-0012", "sweep age: 900 s, or longer than the slowest suite may run (10 x 100 s + 120)",
    R.sweep_age_s(ws3) == 900.0 and R.sweep_age_s(work) == 900.0, R.sweep_age_s(ws3))
with open(os.path.join(ws3, "tests", "suites.json")) as f:
    sj2 = json.load(f)
sj2["suites"][0]["secs"] = 100
with open(os.path.join(ws3, "tests", "suites.json"), "w") as f:
    json.dump(sj2, f)
chk("TC-MUT-0012", "…a 100 s suite makes it 1120 s", R.sweep_age_s(ws3) == 1120.0, R.sweep_age_s(ws3))


class _U:
    free = 3 * 2**30


chk("TC-MUT-0012", "free_gb reads the disk (3 GB); an unreadable disk is None",
    R.free_gb(work, usage=lambda p: _U) == 3.0 and R.free_gb("/nonexistent/x/y") is None)
fake_mm = os.path.join(work, ".venv", "bin", "mutmut")
os.makedirs(os.path.join(work, "home"), exist_ok=True)
with open(fake_mm, "w") as f:
    f.write("#!" + sys.executable + "\nimport os, sys, time, tempfile\n"
            "if sys.argv[1:2] == ['run']:\n"
            "    for i in range(3):\n        tempfile.mkdtemp(prefix='leak_')\n"
            "    open(os.path.join(os.environ['HOME'], 'tmpdir_seen'), 'w').write(tempfile.gettempdir())\n"
            "    time.sleep(20)\n")
os.chmod(fake_mm, 0o755)
R.SWEEP_EVERY_S, R.LOW_DISK_GB = 0.05, 2.0
_free = R.free_gb
_seen_f = os.path.join(work, "home", "tmpdir_seen")
R.free_gb = lambda p, usage=None: 1.5 if os.path.exists(_seen_f) else 50.0   # low once it has leaked
st = {}
t0 = _time.time()
R.run_module(work, "m", 1, 0, False, st)
took = _time.time() - t0
R.free_gb = _free
seen = open(os.path.join(work, "home", "tmpdir_seen")).read() if os.path.exists(
    os.path.join(work, "home", "tmpdir_seen")) else ""
chk("TC-MUT-0012", "mutmut runs with TMPDIR = <work>/tmp/m (python's tempfile goes there)",
    os.path.realpath(seen) == os.path.realpath(tm), seen)
chk("TC-MUT-0012", "disk under 2 GB free: mutmut stopped at once, status 'paused (disk low: 1.5 GB free)'",
    took < 15 and st["m"]["status"].startswith("paused (disk low: 1.5 GB free)"), (took, st))
chk("TC-MUT-0012", "the module's temp folder is empty after the run (its 3 leaked folders gone)",
    bool(seen) and os.path.isdir(tm) and os.listdir(tm) == [], os.listdir(tm))

# ── MUT_FLAKY_RETRY_20261126 ── (DEF-084) TC-MUT-0013…0015
_lg = os.path.join(tempfile.mkdtemp(prefix="mutlog_"), "mutmut.log")
with open(_lg, "w") as f:
    f.write("old run\nFailed to run clean test\n==== new run\n")
_off = len("old run\nFailed to run clean test\n")
_ok = R.clean_test_failed(_lg, 0) and not R.clean_test_failed(_lg, _off)
with open(_lg, "a") as f:
    f.write("Running clean tests\nFailed to run clean test\n")
chk("TC-MUT-0013", "a run whose own part of mutmut.log says 'Failed to run clean test' is flagged; an earlier run's "
    "failure is not", _ok and R.clean_test_failed(_lg, _off), _lg)
_sw = tempfile.mkdtemp(prefix="mutsurv_")
R.write_survivors(_sw, next(iter(R.MODULES)), None, {}, clean_failed=True)
_txt = open(os.path.join(_sw, "survivors.txt")).read()
chk("TC-MUT-0014", "clean pass failed: survivors.txt says NO MUTANTS TESTED (never '0 survivors'); status text says so",
    "NO MUTANTS TESTED" in _txt and "0 survivors" not in _txt and "NO mutants tested" in R.CLEAN_FAIL_STATUS, _txt)
with open(os.path.join(ws, "app/pkg/test_flaky.py"), "w") as f:
    f.write("import os,sys\nm=os.path.join(os.path.dirname(os.path.abspath(__file__)),'flaky.marker')\n"
            "if os.path.exists(m):\n    sys.exit(0)\nopen(m,'w').close()\nsys.exit(1)\n")
probe2 = ("import os,sys;sys.path.insert(0,%r);import test_mut_suites as S;S.ROOT=%r\n"
          "os.environ['MUTANT_UNDER_TEST']=sys.argv[1]\n"
          "m=os.path.join(%r,'app/pkg/flaky.marker')\n"
          "os.path.exists(m) and os.remove(m)\n"
          "try:\n    S.test_suite.__wrapped__('app/pkg/test_flaky.py','script',1.0) if hasattr(S.test_suite,'__wrapped__') "
          "else S.test_suite('app/pkg/test_flaky.py','script',1.0)\n    print('PASSED')\n"
          "except AssertionError as e:\n    print('FAILED', e)\n") % (os.path.join(ws, "tests"), ws, ws)
_r = {}
for name in ("", "stats", "app.pkg.mod.x_f__mutmut_1", "fail"):
    pr = subprocess.run([sys.executable, "-c", probe2, name], capture_output=True, text=True, timeout=180, cwd=ws)
    _r[name or "clean"] = ("PASSED" in pr.stdout, "MUT_FLAKY_RETRY_20261126" in pr.stdout, pr.stdout[-200:] + pr.stderr[-200:])
chk("TC-MUT-0015", "a suite that fails once is retried once in the clean and stats passes (passes), never for a "
    "mutant or the forced-fail check (fails, no retry)",
    _r["clean"][:2] == (True, True) and _r["stats"][:2] == (True, True)
    and _r["app.pkg.mod.x_f__mutmut_1"][:2] == (False, False) and _r["fail"][:2] == (False, False), _r)
shutil.rmtree(os.path.dirname(_lg), ignore_errors=True)
shutil.rmtree(_sw, ignore_errors=True)

for d in (home, ws, fake, work):
    shutil.rmtree(d, ignore_errors=True)
print(f"{'FAILED' if F else 'passed'}: {F} failure(s)")
sys.exit(1 if F else 0)
