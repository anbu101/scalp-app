#!/usr/bin/env python3
# scripts/mutation/test_weekly.py — ── WEEKLY_MUTATION_20261128 ── (TD-133) TC-MUT-0016…0022
#
# Checks the weekly mutation job without mutmut: reading SUMMARY.md and
# survivors.txt, the comparison with last week, a whole run with a fake runner
# (snapshot, report, latest, pruning, exit code) and the launchd plist. Every
# folder is a temp one and HOME is a temp one: the real ~/scalp-mutation and
# ~/Library/LaunchAgents are never read or written.
#   python3 scripts/mutation/test_weekly.py     (stdlib only; exit 1 on any failure)
import ast
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="weekly_mut_t_")
os.environ["HOME"] = os.path.join(TMP, "home")          # CL-22: never the real HOME
os.makedirs(os.environ["HOME"])
F = 0


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


spec = importlib.util.spec_from_file_location("weekly_t", os.path.join(HERE, "weekly.py"))
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

SUMMARY = """# Mutation summary (MUTMUT_20261117) 2026-11-26 23:47 — repo f7672dcd3e

| module            | area         | mutants | killed | survived | timeout | no tests | suspicious | not checked | score % | minutes | state |
|-------------------|--------------|---------|--------|----------|---------|----------|------------|-------------|---------|---------|-------|
| broker_truth      | all          | 1717    | 1488   | 221      | 0       | 8        | 0          | 0           | 87.1    | 70      | done  |
|                   | blind stop   | 311     | 297    | 14       | 0       | 0        | 0          | 0           | 95.5    |         |       |
| exit_order_driver | all          | 406     | 394    | 12       | 0       | 0        | 0          | 0           | 97.0    | 9       | done  |
"""


def surv(entries):
    """survivors.txt text: entries = [(mutant name, line no, old line, new line)]"""
    out = [f"# {len(entries)} survivors in app/execution/exit_order_driver.py", ""]
    for name, ln, old, new in entries:
        out += [f"## app.execution.exit_order_driver.{name}  [exit_order_driver]  app/execution/exit_order_driver.py:{ln}",
                f"# app.execution.exit_order_driver.{name}: survived",
                "--- app/execution/exit_order_driver.py", "+++ app/execution/exit_order_driver.py",
                "@@ -1,2 +1,2 @@", " def _tick(x):", f"-{old}", f"+{new}", ""]
    return "\n".join(out)


# ── TC-MUT-0016 SUMMARY.md table ────────────────────────────────────
rows = W.parse_summary(SUMMARY)
alls = [r for r in rows if r["area"] == "all"]
sub = [r for r in rows if r["area"] == "blind stop"]
chk("TC-MUT-0016", "SUMMARY.md: one row per module and area, numbers read, a sub-area row keeps its module",
    len(rows) == 3 and [r["module"] for r in alls] == ["broker_truth", "exit_order_driver"]
    and alls[0]["survived"] == 221 and alls[0]["score"] == 87.1 and alls[0]["state"] == "done"
    and sub and sub[0]["module"] == "broker_truth" and sub[0]["minutes"] is None, rows)

# ── TC-MUT-0017 survivor keys ignore mutant numbers and line numbers ─
a = W.survivor_keys(surv([("x__tick__mutmut_8", 60, "return round(x, 2)", "return round(x, 3)"),
                          ("x__tick__mutmut_9", 61, "y = 1", "y = 2")]))
b = W.survivor_keys(surv([("x__tick__mutmut_12", 75, "return round(x, 2)", "return round(x, 3)"),
                          ("x__tick__mutmut_13", 76, "y = 1", "y = 2")]))
chk("TC-MUT-0017", "a survivor's key is its file, function and changed line: renumbered mutants and moved "
    "lines keep their keys", a == b and len(a) == 2 and "x__tick" in a[0] and "60" not in a[0]
    and "+return round(x, 3)" in a[0], (a, b))

# ── TC-MUT-0018 comparison with the week before ─────────────────────
k_old = W.survivor_keys(surv([("x__tick__mutmut_1", 1, "a = 1", "a = 2"), ("x__tick__mutmut_2", 2, "b = 1", "b = 2")]))
k_new = W.survivor_keys(surv([("x__tick__mutmut_1", 1, "a = 1", "a = 2"), ("x__tick__mutmut_3", 3, "c = 1", "c = 2"),
                              ("x__tick__mutmut_4", 4, "c = 1", "c = 2")]))
prev = {"rows": [{"module": "m", "area": "all", "score": 90.0, "survived": 2},
                 {"module": "gone_mod", "area": "all", "score": 50.0, "survived": 9}],
        "survivor_keys": {"m": k_old}}
cur = {"rows": [{"module": "m", "area": "all", "score": 85.5, "survived": 3},
                {"module": "fresh_mod", "area": "all", "score": 70.0, "survived": 1}],
       "survivor_keys": {"m": k_new}}
c = W.compare(cur, prev)
chk("TC-MUT-0018", "compare: score change, new survivors counted (two alike count twice), gone ones, "
    "a new module and one missing from this run",
    c["m"]["delta"] == -4.5 and len(c["m"]["new"]) == 2 and c["m"]["gone"] == 1 and not c["m"]["new_module"]
    and c["fresh_mod"]["new_module"] and c["gone_mod"].get("missing"), c)
chk("TC-MUT-0018", "compare: the first run has nothing to compare with", W.compare(cur, None) is None)

# ── TC-MUT-0019 a whole run with a fake runner ───────────────────────
work, out = os.path.join(TMP, "work"), os.path.join(TMP, "out")
os.makedirs(os.path.join(work, "exit_order_driver"))
fake = os.path.join(TMP, "fake_runner.py")
with open(fake, "w") as f:
    f.write("import json, os, sys\n"
            "a = sys.argv[1:]\n"
            "w = a[a.index('--work') + 1]\n"
            "open(os.path.join(os.path.dirname(w), 'argv.json'), 'w').write(json.dumps(a))\n"
            "rc = int(os.environ.get('FAKE_RC', '0'))\n"
            "st = os.environ.get('FAKE_STATE', 'done')\n"
            "open(os.path.join(w, 'SUMMARY.md'), 'w').write(open(os.environ['FAKE_SUMMARY']).read())\n"
            "json.dump({'broker_truth': {'status': st}, 'exit_order_driver': {'status': 'done'}},"
            " open(os.path.join(w, 'state.json'), 'w'))\n"
            "open(os.path.join(w, 'exit_order_driver', 'survivors.txt'), 'w').write(os.environ['FAKE_SURV'])\n"
            "open(os.path.join(w, 'exit_order_driver', 'mutmut.log'), 'w').write('line\\n' * 500)\n"
            "sys.exit(rc)\n")
sfile = os.path.join(TMP, "summary.md")
open(sfile, "w").write(SUMMARY)
os.environ.update(FAKE_SUMMARY=sfile, FAKE_SURV=surv([("x__tick__mutmut_1", 1, "a = 1", "a = 2")]))
rc1 = W.do_run(work, out, keep=2, runner=fake)
argv = json.load(open(os.path.join(TMP, "argv.json")))
first = os.path.realpath(os.path.join(out, "latest"))
res = json.load(open(os.path.join(first, "result.json")))
tail = open(os.path.join(first, "logs", "exit_order_driver.log.tail")).read().count("\n")
chk("TC-MUT-0019", "a run starts every module afresh (--fresh, the given --work) and keeps a dated snapshot: "
    "SUMMARY, state, survivors, log tails, result.json, report.md; latest points at it; exit 0",
    rc1 == 0 and argv == ["--fresh", "--work", work] and res["complete"] and res["runner_exit"] == 0
    and os.path.exists(os.path.join(first, "survivors", "exit_order_driver.txt"))
    and os.path.exists(os.path.join(first, "SUMMARY.md")) and tail == 200
    and "First run here" in open(os.path.join(first, "report.md")).read(), (rc1, argv, res.get("complete"), tail))
os.environ["FAKE_SURV"] = surv([("x__tick__mutmut_1", 1, "a = 1", "a = 2"), ("x__tick__mutmut_2", 2, "b = 1", "b = 9")])
W.do_run(work, out, keep=2, runner=fake)
second = os.path.realpath(os.path.join(out, "latest"))
rep2 = open(os.path.join(second, "report.md")).read()
chk("TC-MUT-0019", "the next run compares with the last one: the new survivor's diff is in the report",
    second != first and f"Compared with `{os.path.basename(first)}`" in rep2
    and "New survivors in exit_order_driver (1)" in rep2 and "+b = 9" in rep2, rep2[:800])
W.do_run(work, out, keep=2, runner=fake)
runs = sorted(d for d in os.listdir(out) if W.RUN_DIR.fullmatch(d))
chk("TC-MUT-0019", "only the newest --keep runs are kept", len(runs) == 2 and os.path.basename(first) not in runs, runs)
nd = W.new_run_dir(out, W.time.mktime(W.time.strptime(runs[-1][:15], "%Y-%m-%d_%H%M")))
chk("TC-MUT-0019", "a new run in the same minute sorts after every run kept, even after its first folder "
    "was pruned", os.path.basename(nd) > runs[-1], (nd, runs))
os.rmdir(nd)

# ── TC-MUT-0020 a run that did not finish ────────────────────────────
os.environ.update(FAKE_RC="1", FAKE_STATE="stopped")
rc3 = W.do_run(work, out, keep=5, runner=fake)
last = os.path.realpath(os.path.join(out, "latest"))
r3 = json.load(open(os.path.join(last, "result.json")))
head = open(os.path.join(last, "report.md")).read().splitlines()[2]
chk("TC-MUT-0020", "a module not done or a failed runner: exit 1, result not complete, report headed INCOMPLETE "
    "with the module and the runner's exit code", rc3 == 1 and not r3["complete"] and "INCOMPLETE" in head
    and "broker_truth stopped" in head and "runner exit 1" in head, (rc3, head))
os.environ.update(FAKE_RC="0", FAKE_STATE="done")
snap_out = os.path.join(TMP, "snap")
rc4 = W.do_run(work, snap_out, keep=5, runner="/nonexistent/runner.py", kind="snapshot")
r4 = json.load(open(os.path.join(os.path.realpath(os.path.join(snap_out, "latest")), "result.json")))
chk("TC-MUT-0020", "snapshot keeps what the work folder holds without running anything (the baseline)",
    rc4 == 0 and r4["kind"] == "snapshot" and r4["modules"]["exit_order_driver"]["status"] == "done", r4.get("kind"))

# ── TC-MUT-0021 the launchd job ──────────────────────────────────────
wd, hh, mm = W.parse_when(W.defaults()["--day"], W.defaults()["--time"])
pl = W.build_plist(wd, hh, mm, "/w", "/o", python="/py", path_env="/bin")
pa = pl["ProgramArguments"]
chk("TC-MUT-0021", "the default job is Saturday 10:00 local time (IST on the Macs), kept awake by caffeinate, "
    "at background priority, logging to <out>/launchd.log",
    pl["Label"] == "com.scalp.weekly-mutation" and pl["StartCalendarInterval"] == {"Weekday": 6, "Hour": 10, "Minute": 0}
    and pa[:3] == ["/usr/bin/caffeinate", "-i", "/py"] and pa[3].endswith("scripts/mutation/weekly.py")
    and pa[4:] == ["run", "--notify", "--work", "/w", "--out", "/o"] and pl["ProcessType"] == "Background"
    and pl["StandardOutPath"] == "/o/launchd.log" and pl["RunAtLoad"] is False, pl)
agents_before = os.path.exists(W.AGENTS) and sorted(os.listdir(W.AGENTS))
r = subprocess.run([sys.executable, os.path.join(HERE, "weekly.py"), "install", "--print", "--day", "sun",
                    "--time", "09:30", "--out", os.path.join(TMP, "o2")], capture_output=True, env=dict(os.environ))
shown = plistlib.loads(r.stdout) if r.returncode == 0 else {}
chk("TC-MUT-0021", "install --print shows the plist for another day / time and changes nothing",
    shown.get("StartCalendarInterval") == {"Weekday": 0, "Hour": 9, "Minute": 30}
    and not os.path.exists(os.path.join(TMP, "o2"))
    and (os.path.exists(W.AGENTS) and sorted(os.listdir(W.AGENTS))) == agents_before, (r.returncode, r.stderr[-300:]))
bad = subprocess.run([sys.executable, os.path.join(HERE, "weekly.py"), "install", "--print", "--day", "xyz"],
                     capture_output=True, text=True)
chk("TC-MUT-0021", "a bad --day is refused (exit 2)", bad.returncode == 2 and "bad arguments" in bad.stderr, bad.stderr)

# ── TC-MUT-0022 standard library only ────────────────────────────────
tree = ast.parse(open(os.path.join(HERE, "weekly.py"), encoding="utf-8").read())
mods = {(n.module if isinstance(n, ast.ImportFrom) else a.name).split(".")[0]
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])}
nonstd = sorted(m for m in mods if m not in sys.stdlib_module_names)
chk("TC-MUT-0022", "weekly.py imports the standard library only (it runs on the system python3)", not nonstd, nonstd)

shutil.rmtree(TMP, ignore_errors=True)
print("\nALL WEEKLY MUTATION CHECKS PASSED" if not F else f"\n{F} check(s) FAILED")
sys.exit(1 if F else 0)
