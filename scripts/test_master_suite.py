#!/usr/bin/env python3
# scripts/test_master_suite.py — ── MASTER_SUITE_20261122 ── (TD-079) TC-REGR-0001…0029
#                           ── ISOLATED_HOME_20261122 ── (DEF-094) TC-REGR-0030…0033
#
# The master regression suite (scripts/regression/master_suite.py), its weekly
# scheduler (schedule.py) and its pre-commit hook, checked in throw-away git
# repos (never this repo's index, never ~/Library/LaunchAgents, never port 47321).
#   python3 scripts/test_master_suite.py      (stdlib only; exit 1 on any failure)
import ast
import importlib.util
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "scripts", "regression")
F = 0
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {str(info)[-800:]}"))
    F += 0 if ok else 1


def put(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def git(d, *a):
    r = subprocess.run(["git", *a], cwd=d, env=ENV, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PASS = "print('  ok   TC-FAKE-0001 one')\nprint('  ok   TC-FAKE-0002 two')\n"
WRITES_HOME = ("import os, pathlib, sys\nh = pathlib.Path.home()\n"
               "print('HOME=' + str(h))\nprint('DBP=' + str(os.environ.get('DB_PATH')))\n"
               "p = h / '.scalp-app' / 'zerodha' / 'credentials.json'\n"
               "p.parent.mkdir(parents=True, exist_ok=True)\np.write_text('{\"api_key\": \"k1\"}')\n"
               "sys.exit(1 if os.environ.get('WH_FAIL') else 0)\n")
FAILS = ("import sys\nprint('  ok   TC-FAKE-0010 fine')\n"
         "print('  FAIL TC-FAKE-0011 the stop is placed  -> None')\nsys.exit(1)\n")


def fake_repo():
    """A tiny repo with the real runner + tool and a handful of suites."""
    d = tempfile.mkdtemp(prefix="msuite_t_")
    for rel in ("scripts/run_suites.py", "scripts/regression/master_suite.py", "scripts/regression/schedule.py",
                "scripts/hooks/master_suite_refresh.py"):
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), os.path.join(d, rel))
    put(d, ".gitignore", "ignored/\n")
    put(d, "backend/app/x/test_good.py", PASS)
    put(d, "backend/app/x/test_bad.py", FAILS)
    put(d, "backend/app/x/test_good.py.bak-OLD_20260101", PASS)
    put(d, "backend/app/x/helper.py", "X = 1\n")
    put(d, "backend/test_server.py", "print('not a suite')\n")
    put(d, "frontend/uitest/test_ui_fake.py", "print('ui')\n")
    put(d, ".claude/skills/s/test_skill.py", "raise SystemExit(1)\n")
    put(d, "ignored/test_ignored.py", "raise SystemExit(1)\n")
    git(d, "init", "-q", "-b", "main")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d


def tool(d, *a, env=None):
    r = subprocess.run([sys.executable, os.path.join(d, "scripts", "regression", "master_suite.py"), *a], cwd=d,
                       env=dict(ENV, **(env or {})), capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def results(out_dir):
    with open(os.path.join(out_dir, "latest", "results.json"), encoding="utf-8") as f:
        return json.load(f)


def by(R):
    return {r["suite"]: r for r in R["results"]}


# ── stdlib only ──────────────────────────────────────────────────────
std = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
bad = []
for f in ("master_suite.py", "schedule.py"):
    t = ast.parse(open(os.path.join(REG, f), encoding="utf-8").read())
    for n in ast.walk(t):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
        bad += [f"{f}:{m}" for m in mods if std is not None and m.split(".")[0] not in std]
chk("TC-REGR-0001", "master_suite.py and schedule.py import the standard library only", not bad, bad)

d = fake_repo()
out = tempfile.mkdtemp(prefix="msuite_out_")
busy = socket.socket()
busy.bind(("127.0.0.1", 0))
busy.listen(1)
PORT = {"MASTER_SUITE_APP_PORT": str(busy.getsockname()[1])}
try:
    M = load(os.path.join(d, "scripts", "regression", "master_suite.py"), "ms_t")
    found = {s["suite"]: s for s in M.discover(d)}
    chk("TC-REGR-0002", "discovery takes every tracked test_*.py, UI suites included",
        {"backend/app/x/test_good.py", "backend/app/x/test_bad.py", "frontend/uitest/test_ui_fake.py"} <= set(found),
        sorted(found))
    chk("TC-REGR-0003", "left out: .bak-* copies, git-ignored files, EXCLUDE (backend/test_server.py), .claude/",
        not any(k.endswith(".bak-OLD_20260101") or k.startswith(("ignored/", ".claude/")) or k == "backend/test_server.py"
                for k in found), sorted(found))
    put(d, "backend/app/x/test_new_unstaged.py", PASS)
    chk("TC-REGR-0004", "a new test not yet committed (not ignored) is in the master suite at once",
        "backend/app/x/test_new_unstaged.py" in {s["suite"] for s in M.discover(d)})
    os.remove(os.path.join(d, "backend/app/x/test_new_unstaged.py"))
    chk("TC-REGR-0005", "tiers and runners: backend file = core/script, frontend/uitest = ui/ui",
        (found["backend/app/x/test_good.py"]["tier"], found["backend/app/x/test_good.py"]["runner"],
         found["frontend/uitest/test_ui_fake.py"]["tier"]) == ("core", "script", "ui"), found)
    chk("TC-REGR-0006", "test-case IDs are read from each suite and shown as ranges",
        found["backend/app/x/test_good.py"]["tcs"] == ["TC-FAKE-0001", "TC-FAKE-0002"]
        and M.tc_span(["TC-A-0001", "TC-A-0002", "TC-A-0003", "TC-A-0007", "TC-B-0001"])
        == "TC-A-0001…0003, TC-A-0007, TC-B-0001", M.tc_span(["TC-A-0001", "TC-A-0002", "TC-A-0003", "TC-A-0007"]))

    crash = M.details("  PASS  one\nTraceback (most recent call last):\n  File \"x.py\", line 3\nKeyError: 'PAPER'\n")
    pyq = M.details("..F.\nFAILED test_x.py::test_a - assert 1 == 2\n1 failed, 3 passed in 0.31s\n")
    chk("TC-REGR-0027", "a suite that crashes is reported with its exception line; a pytest -q run with its summary "
        "and FAILED test", crash["fail_lines"] == ["KeyError: 'PAPER'"] and pyq.get("pytest") == "1 failed, 3 passed"
        and pyq["fail_lines"][0].startswith("FAILED test_x.py::test_a"), (crash, pyq))
    chk("TC-REGR-0028", "a suite that needs arguments gets them (test_vet_wing_input.mjs <- Settings.jsx)",
        M.ARGS.get("frontend/scripts/test_vet_wing_input.mjs") == ["frontend/src/pages/Settings.jsx"]
        and os.path.exists(os.path.join(ROOT, "frontend/src/pages/Settings.jsx")), M.ARGS)

    # ── manifest ──
    rc, o = tool(d, "check")
    chk("TC-REGR-0007", "check exits 1 when MASTER_SUITE.md is missing", rc == 1, o)
    rc, o = tool(d, "refresh")
    man = open(os.path.join(d, "scripts/regression/MASTER_SUITE.md"), encoding="utf-8").read()
    rc2, o2 = tool(d, "check")
    chk("TC-REGR-0008", "refresh writes a manifest listing every suite with runner, hash and TCs; check then exits 0",
        rc == 0 and rc2 == 0 and "`backend/app/x/test_bad.py` | script |" in man and "TC-FAKE-0010…0011" in man
        and "## UI" in man, o + o2)
    put(d, "backend/app/x/test_added.py", PASS)
    put(d, "backend/app/x/test_good.py", PASS + "print('more')\n")
    os.remove(os.path.join(d, "backend/app/x/test_bad.py"))
    rc, o = tool(d, "check")
    chk("TC-REGR-0009", "a test added (+), changed (~) and deleted (−) each make check exit 1 and are named",
        rc == 1 and "+ backend/app/x/test_added.py" in o and "~ backend/app/x/test_good.py" in o
        and "− backend/app/x/test_bad.py" in o, o)
    a, r, c = M.drift(M.discover(d), man)
    chk("TC-REGR-0010", "drift() returns the same added / removed / changed lists",
        (a, r, c) == (["backend/app/x/test_added.py"], ["backend/app/x/test_bad.py"], ["backend/app/x/test_good.py"]),
        (a, r, c))

    # ── hook ──
    git(d, "add", "-A")
    rh = subprocess.run([sys.executable, os.path.join(d, "scripts/hooks/master_suite_refresh.py")], cwd=d, env=ENV,
                        capture_output=True, text=True, timeout=300)
    _, staged = git(d, "diff", "--cached", "--name-only")
    rc3, o3 = tool(d, "check")
    chk("TC-REGR-0011", "the pre-commit hook refreshes a stale MASTER_SUITE.md, stages it, and never blocks",
        rh.returncode == 0 and "scripts/regression/MASTER_SUITE.md" in staged and rc3 == 0, rh.stdout + staged + o3)
    rh2 = subprocess.run([sys.executable, os.path.join(d, "scripts/hooks/master_suite_refresh.py")], cwd=d, env=ENV,
                         capture_output=True, text=True, timeout=300)
    chk("TC-REGR-0012", "the hook is silent when the manifest is current", rh2.returncode == 0 and not rh2.stdout.strip(),
        rh2.stdout)
    put(d, "backend/app/x/test_bad.py", FAILS)
    tool(d, "refresh")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "suites")

    # ── run ──
    rc, o = tool(d, "run", "--out", out, "--no-build", env=PORT)
    R = results(out)
    B = by(R)
    chk("TC-REGR-0013", "run exits 1 when a suite fails and counts pass / fail / skip",
        rc == 1 and B["backend/app/x/test_bad.py"]["status"] == "fail"
        and B["backend/app/x/test_good.py"]["status"] == "pass" and R["counts"]["fail"] == 1, o)
    chk("TC-REGR-0014", "a failing suite's report names the failing TC and the failing check line",
        B["backend/app/x/test_bad.py"].get("failed_tcs") == ["TC-FAKE-0011"]
        and any("the stop is placed" in x for x in B["backend/app/x/test_bad.py"].get("fail_lines", [])),
        B["backend/app/x/test_bad.py"])
    chk("TC-REGR-0015", "UI suites are skipped, never run, while the app's port is taken",
        B["frontend/uitest/test_ui_fake.py"]["status"] == "skip" and "app is running" in
        B["frontend/uitest/test_ui_fake.py"]["why"], B["frontend/uitest/test_ui_fake.py"])
    run_dir = os.path.realpath(os.path.join(out, "latest"))
    md = open(os.path.join(run_dir, "report.md"), encoding="utf-8").read()
    ht = open(os.path.join(run_dir, "report.html"), encoding="utf-8").read()
    logs = os.listdir(os.path.join(run_dir, "logs"))
    chk("TC-REGR-0016", "report.md / report.html / results.json / a full log per suite; latest -> this run",
        "### ✗ `backend/app/x/test_bad.py`" in md and "TC-FAKE-0011" in ht and "backend__app__x__test_bad.py.log" in logs
        and R["commit"] and R["version"] and R["machine"], (md[:600], logs))
    put(d, "backend/app/x/test_bad.py", PASS)
    put(d, "backend/app/x/test_good.py", FAILS)
    rc, o = tool(d, "run", "--out", out, "--no-build", env=PORT)
    C = results(out)["compare"]
    chk("TC-REGR-0017", "the next run lists newly failing and fixed suites against the previous run",
        C and C["newly_failing"] == ["backend/app/x/test_good.py"] and C["fixed"] == ["backend/app/x/test_bad.py"]
        and set(C["changed"]) == {"backend/app/x/test_good.py", "backend/app/x/test_bad.py"}, C)
    R2 = results(out)
    chk("TC-REGR-0018", "a run on a stale manifest still runs every suite and says the manifest is behind",
        any("MASTER_SUITE.md is behind" in n for n in R2["notes"]) and len(R2["results"]) == len(R2["suites"]),
        R2["notes"])
    put(d, "backend/app/x/test_slow.py", "import time\ntime.sleep(30)\n")
    rc, o = tool(d, "run", "--out", out, "--no-build", "--timeout", "2", "--only", "backend/app/x/test_slow.py",
                 env=PORT)
    R3 = results(out)
    chk("TC-REGR-0019", "a suite over --timeout is recorded as timed out (a failure); --only runs a subset",
        rc == 1 and [r["status"] for r in R3["results"]] == ["timeout"] and R3["subset"], o)
    os.remove(os.path.join(d, "backend/app/x/test_slow.py"))
    put(d, "backend/app/x/test_litter.py", "import tempfile, os\nd = tempfile.mkdtemp(prefix='litter_')\n"
        "print('TMP=' + d)\nopen(os.path.join(d, 'f'), 'w').write('x')\n")
    rc, o = tool(d, "run", "--out", out, "--no-build", "--only", "backend/app/x/test_litter.py", env=PORT)
    lg = open(os.path.join(os.path.realpath(os.path.join(out, "latest")), "logs",
                           "backend__app__x__test_litter.py.log"), encoding="utf-8").read()
    left = lg.split("TMP=")[1].split()[0] if "TMP=" in lg else ""
    chk("TC-REGR-0025", "a suite's temp folders go in a TMPDIR of its own under the run, deleted when it ends (DEF-085)",
        rc == 0 and bool(left) and os.path.realpath(left).startswith(os.path.realpath(out) + os.sep)
        and not os.path.exists(left)
        and not os.path.exists(os.path.join(os.path.realpath(os.path.join(out, "latest")), "tmp")), (rc, left, o))
    os.remove(os.path.join(d, "backend/app/x/test_litter.py"))
    vpy = os.path.join(d, "backend", "venv", "bin", "python")        # a venv Python that lacks a package
    put(d, "backend/venv/bin/python", "#!/bin/sh\necho \"$0: No module named pytest\"\nexit 1\n")
    os.chmod(vpy, 0o755)
    rc, o = tool(d, "run", "--out", out, "--no-build", "--only", "backend/app/x/test_bad.py", env=PORT)
    R4 = by(results(out))["backend/app/x/test_bad.py"]
    lg = open(os.path.join(os.path.realpath(os.path.join(out, "latest")), R4["log"]), encoding="utf-8").read()
    chk("TC-REGR-0026", "a suite whose backend-venv Python lacks a package (pytest) is run again with the system "
        "python3, and the log says so", R4["status"] == "pass" and "lacks 'pytest'; ran with" in lg, (R4, lg[:400]))
    shutil.rmtree(os.path.join(d, "backend", "venv"))
    # ── ISOLATED_HOME_20261122 ── DEF-094: suites saved into the real ~/.scalp-app (fake LIVE ORB trades in
    # app.db, placeholder Zerodha / Telegram credentials). The runner's own HOME here is a stand-in folder.
    realhome = tempfile.mkdtemp(prefix="msuite_realhome_")
    put(d, "backend/app/x/test_writes_home.py", WRITES_HOME)
    rc, o = tool(d, "run", "--out", out, "--no-build", "--only", "backend/app/x/test_writes_home.py",
                 env=dict(PORT, HOME=realhome, DB_PATH=os.path.join(realhome, ".scalp-app", "data", "app.db"),
                          SCALP_APP_HOME=os.path.join(realhome, ".scalp-app")))
    lg = open(os.path.join(os.path.realpath(os.path.join(out, "latest")), "logs",
                           "backend__app__x__test_writes_home.py.log"), encoding="utf-8").read()
    seen = lg.split("HOME=")[1].split()[0] if "HOME=" in lg else ""
    chk("TC-REGR-0030", "master suite: a suite that saves into ~/.scalp-app writes into an empty HOME of its own "
        "under the run, never the runner's HOME, and that HOME is deleted when the suite ends (DEF-094)",
        rc == 0 and bool(seen) and os.path.realpath(seen).startswith(os.path.realpath(out) + os.sep)
        and not os.path.exists(seen) and not os.path.exists(os.path.join(realhome, ".scalp-app")), (rc, seen, o))
    chk("TC-REGR-0031", "the app's folder variables (DB_PATH, SCALP_APP_HOME, …) are not passed on to a suite",
        "DBP=None" in lg, lg[:400])
    r = subprocess.run([sys.executable, os.path.join(d, "scripts", "run_suites.py"), "backend/app/x/test_writes_home.py"],
                       cwd=d, env=dict(ENV, HOME=realhome, WH_FAIL="1"), capture_output=True, text=True, timeout=120)
    seen2 = r.stdout.split("HOME=")[1].split()[0] if "HOME=" in r.stdout else ""
    chk("TC-REGR-0032", "run_suites.py: the same suite gets a throw-away HOME (not the caller's), deleted after the run",
        r.returncode == 1 and bool(seen2) and os.path.realpath(seen2) != os.path.realpath(realhome)
        and not os.path.exists(seen2) and not os.path.exists(os.path.join(realhome, ".scalp-app")),
        r.stdout + r.stderr)
    os.remove(os.path.join(d, "backend/app/x/test_writes_home.py"))
    shutil.rmtree(realhome, ignore_errors=True)
    RS = load(os.path.join(d, "scripts", "run_suites.py"), "rs_t")
    import site as _site
    h0 = tempfile.mkdtemp(prefix="msuite_h0_")
    _, _, e = RS.plan(os.path.join(d, "backend/app/x/test_good.py"), home=os.path.join(h0, "h"))
    chk("TC-REGR-0033", "plan(): HOME and USERPROFILE are the given folder (made if missing); pip --user packages stay "
        "reachable (PYTHONUSERBASE = the real user base)",
        e["HOME"] == e["USERPROFILE"] == os.path.join(h0, "h") and os.path.isdir(e["HOME"])
        and e.get("PYTHONUSERBASE") == os.environ.get("PYTHONUSERBASE", _site.getuserbase()),
        {k: e.get(k) for k in ("HOME", "USERPROFILE", "PYTHONUSERBASE")})
    shutil.rmtree(h0, ignore_errors=True)
    for _ in range(3):
        tool(d, "run", "--out", out, "--no-build", "--keep", "2", "--only", "backend/app/x/test_bad.py", env=PORT)
    runs = [x for x in os.listdir(out) if x[:2] == "20"]
    chk("TC-REGR-0020", "--keep N prunes older run folders", len(runs) == 2, runs)

    # ── scheduler ──
    S = load(os.path.join(d, "scripts", "regression", "schedule.py"), "sch_t")
    pl = S.build_plist(*S.parse_when("sat", "22:00"), "/tmp/x", python="/py", path_env="/a:/b", chrome="/c")
    chk("TC-REGR-0021", "the launchd job: Saturday 22:00, caffeinate, run --notify, this shell's PATH and CHROME",
        pl["StartCalendarInterval"] == {"Weekday": 6, "Hour": 22, "Minute": 0}
        and pl["ProgramArguments"][:3] == ["/usr/bin/caffeinate", "-i", "/py"]
        and pl["ProgramArguments"][3].endswith("scripts/regression/master_suite.py")
        and {"run", "--notify"} <= set(pl["ProgramArguments"]) and pl["EnvironmentVariables"]["PATH"] == "/a:/b"
        and pl["EnvironmentVariables"]["CHROME"] == "/c" and plistlib.loads(plistlib.dumps(pl)) == pl, pl)
    errs = 0
    for day, t in (("sat", "24:00"), ("xyz", "22:00"), ("sat", "9pm")):
        try:
            S.parse_when(day, t)
        except ValueError:
            errs += 1
    chk("TC-REGR-0022", "bad --day / --time are refused; other days and times are accepted",
        errs == 3 and S.parse_when("Sunday", "06:05") == (0, 6, 5))
    home = tempfile.mkdtemp(prefix="msuite_home_")
    r = subprocess.run([sys.executable, os.path.join(d, "scripts/regression/schedule.py"), "install", "--print"],
                       cwd=d, env=dict(ENV, HOME=home), capture_output=True, text=True, timeout=60)
    chk("TC-REGR-0023", "install --print shows the plist and writes nothing",
        r.returncode == 0 and "com.scalp.weekly-regression" in r.stdout and not os.path.exists(
            os.path.join(home, "Library")), r.stdout + r.stderr)
    pl2 = plistlib.loads(r.stdout.encode())
    chk("TC-REGR-0029", "install with no --day / --time schedules Friday 16:00 local time (IST on the Macs)",
        pl2["StartCalendarInterval"] == {"Weekday": 5, "Hour": 16, "Minute": 0}, pl2.get("StartCalendarInterval"))
    shutil.rmtree(home, ignore_errors=True)
finally:
    busy.close()
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)

# ── this repo ────────────────────────────────────────────────────────
r = subprocess.run([sys.executable, os.path.join(REG, "master_suite.py"), "check"], cwd=ROOT, capture_output=True,
                   text=True, timeout=300)
chk("TC-REGR-0024", "this repo's MASTER_SUITE.md lists every suite in the tree (check)", r.returncode == 0,
    r.stdout + r.stderr)

print(f"\n{'ALL PASSED' if not F else f'{F} FAILED'} — test_master_suite.py")
sys.exit(1 if F else 0)
