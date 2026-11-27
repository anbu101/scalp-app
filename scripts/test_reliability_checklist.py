#!/usr/bin/env python3
# scripts/test_reliability_checklist.py — ── RELIABILITY_GATE_20261118 ── (TD-048, TD-049) TC-GATE-0001…0040
#                                     ── ISOLATED_HOME_20261122 ── (DEF-094) TC-GATE-0055…0056
#                                     ── VET_MODE_FOLLOW_20261125 ── (DEF-102) TC-GATE-0059…0061
#                                     ── HOOK_NO_ADD_20261126 ── (DEF-060) TC-GATE-0062…0064; 0038/0040 count 17 items
#                                     ── PYTEST_FALLBACK_20261126 ── (DEF-098) TC-GATE-0057…0058 (CL-18); 0038/0040 count 18 items
#                                     ── FLEET_MONITOR_FIX_20261127 ── (DEF-109) TC-GATE-0065…0067 (CL-19); 0038/0040 count 19 items
#                                     ── MONITOR_DEPLOY_FILES_20261127 ── (DEF-110) TC-GATE-0068…0071 (CL-20); 0038/0040 count 20 items
#                                     ── RELEASE_STAMP_GUARD_20261127 ── (DEF-112) TC-GATE-0072…0076 (CL-21, retired release script); 0038/0040 count 21 items
#                                     ── TEST_HOME_GUARD_20261127 ── (DEF-113) TC-GATE-0077…0079 (CL-22); 0038/0040 count 22 items
#
# Checks of the diff-triggered checklist (scripts/checklist/items.py) and the commit gate
# (scripts/hooks/reliability_checklist.py) in throw-away git repos — never this repo's index.
#   python3 scripts/test_reliability_checklist.py      (stdlib; exit 1 on a failure)
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "scripts", "hooks", "reliability_checklist.py")
ITEMS = os.path.join(ROOT, "scripts", "checklist", "items.py")
F = 0
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
ENV.pop("SCALP_GATE", None)
STUB_SUITES = ("import sys\nsrc = open(sys.argv[1]).read()\n"
               "if 'NOTRUN' in src: print('0 passed, 0 failed, 1 not runnable here'); sys.exit(0)\n"
               "sys.exit(1 if 'FAILME' in src else 0)\n")


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {str(info)[:600]}"))
    F += 0 if ok else 1


def git(d, *a):
    r = subprocess.run(["git", *a], cwd=d, env=ENV, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def put(d, rel, text):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p) or d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def repo(mode="observe", items_src=None):
    d = tempfile.mkdtemp(prefix="gate_")
    git(d, "init", "-q", "-b", "main")
    put(d, "scripts/hooks/reliability_checklist.py", open(GATE, encoding="utf-8").read())
    put(d, "scripts/checklist/items.py", items_src or open(ITEMS, encoding="utf-8").read())
    put(d, "scripts/checklist/MODE", mode + "\n")
    put(d, "scripts/run_suites.py", STUB_SUITES)
    for s in ("backend/app/engine/test_fleet_live.py", "backend/app/engine/test_fleet_adversarial.py",
              "backend/app/engine/test_safety_layers.py", "backend/app/execution/test_broker_truth.py"):
        put(d, s, "# suite\n")
    put(d, "backend/app/api_server.py", "import os\napp = FastAPI()\napp.include_router(r)\n")
    put(d, "backend/app/engine/ic/ic_engine.py", "x = 1\n")
    put(d, "frontend/src/pages/Backtest.jsx",
        "const [a, setA] = useState(1);\nconst buildConfig = useCallback((sid) => {\n  return {a};\n}, [a]);\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "base")
    return d


def commit(d, msg="change", env=None):
    """Run the hook the way git does (commit-msg with the message file), then commit if it passed."""
    mp = os.path.join(d, ".git", "COMMIT_EDITMSG")
    put(d, ".git/COMMIT_EDITMSG", msg + "\n")
    r = subprocess.run([sys.executable, "scripts/hooks/reliability_checklist.py", mp], cwd=d,
                       env=dict(ENV, SCALP_SUITE_PYTHON=sys.executable, **(env or {})), capture_output=True, text=True)
    out = r.stdout + r.stderr
    final = open(mp, encoding="utf-8").read()
    if r.returncode == 0:
        git(d, "commit", "-q", "--no-verify", "-F", mp)
    return r.returncode, out, final


def stamp(final):
    return [ln for ln in final.splitlines() if ln.startswith("Checklist: ")]


def one(d, rel, text, msg="change", env=None):
    put(d, rel, text)
    git(d, "add", "-A")
    return commit(d, msg, env)


def status_of(final, cid):
    s = stamp(final)
    for part in (s[0].split(" · ") if s else []):
        if part.startswith(cid + " "):
            return part.split(" ", 1)[1]
    return None


# ── items: trigger + check, positive and negative ───────────────────
d = repo()
rc, out, fin = one(d, "backend/app/config/creds.py", 'API_KEY = "a1b2c3d4e5f6g7h8i9j0"\n')
chk("TC-GATE-0001", "CL-01: a 20-char literal assigned to API_KEY fails", status_of(fin, "CL-01") == "fail", fin)
rc, out, fin = one(d, "backend/app/config/creds2.py", 'api_secret = os.environ["KITE_SECRET"]\nPASSWORD = "never-leaves-this"\n')
chk("TC-GATE-0002", "CL-01: env lookups and hyphenated fixture words do not trigger", status_of(fin, "CL-01") is None, fin)
rc, out, fin = one(d, "backend/app/engine/vet/vet_monitor.py", "pos = self.kite.positions()\n")
chk("TC-GATE-0003", "CL-02: a direct .positions() read in engine code fails",
    status_of(fin, "CL-02") == "fail" and "broker_truth" in out, out)
rc, out, fin = one(d, "backend/app/execution/broker_truth.py", "p = kite.positions()\n")
chk("TC-GATE-0004", "CL-02: broker_truth.py itself is exempt", status_of(fin, "CL-02") is None, fin)
rc, out, fin = one(d, "frontend/src/pages/Backtest.jsx",
                   "const [a, setA] = useState(1);\nconst [b, setB] = useState(2);\n"
                   "const buildConfig = useCallback((sid) => {\n  return {a, b};\n}, [a]);\n")
chk("TC-GATE-0005", "CL-03: new state read by buildConfig but missing from deps fails",
    status_of(fin, "CL-03") == "fail" and "'b'" in out, out)
rc, out, fin = one(d, "frontend/src/pages/Backtest.jsx",
                   "const [a, setA] = useState(1);\nconst [b, setB] = useState(2);\nconst [c, setC] = useState(3);\n"
                   "const buildConfig = useCallback((sid) => {\n  return {a, b, c};\n}, [a, b, c]);\n")
chk("TC-GATE-0006", "CL-03: new state in the dep array passes", status_of(fin, "CL-03") == "pass", fin)
rc, out, fin = one(d, "backend/app/api_server.py",
                   "import os\napp = FastAPI()\napp.include_router(r)\nprint('x')\nrepair_corpus()\n")
chk("TC-GATE-0007", "CL-04: a new module-level call in api_server.py fails (print is fine)",
    status_of(fin, "CL-04") == "fail" and "repair_corpus" in out and "print" not in out.split("CL-04", 1)[1][:200], out)
rc, out, fin = one(d, "backend/app/api_server.py",
                   "import os\napp = FastAPI()\napp.include_router(r)\nprint('x')\nrepair_corpus()\n"
                   "app.include_router(r2)\nrouter = APIRouter()\nthreading.Thread(target=f).start()\n")
chk("TC-GATE-0008", "CL-04: routers, constructors and a thread start pass (old call not re-flagged)",
    status_of(fin, "CL-04") == "pass", out)
rc, out, fin = one(d, "backend/app/jobs/new_job.py", "s = BackgroundScheduler()\n")
chk("TC-GATE-0009", "CL-05: a new scheduler outside api_server.py fails", status_of(fin, "CL-05") == "fail", fin)
rc, out, fin = one(d, "backend/app/engine/tma2/tma2_mon.py", "notify_critical({'x': 1})\n")
chk("TC-GATE-0010", "CL-06: an added notify_critical( needs an ack", status_of(fin, "CL-06") == "needs-ack", fin)
rc, out, fin = one(d, "backend/app/engine/ic/ic_engine.py", "x = 2\n")
chk("TC-GATE-0011", "CL-07: an Icarus engine change runs the fleet suites (pass)", status_of(fin, "CL-07") == "pass", fin)
put(d, "backend/app/engine/test_fleet_live.py", "# FAILME\n")
rc, out, fin = one(d, "backend/app/engine/ic/ic_engine.py", "x = 3\n")
chk("TC-GATE-0012", "CL-07: a failing fleet suite fails the item and names the suite",
    status_of(fin, "CL-07") == "fail" and "test_fleet_live.py" in out, out)
put(d, "backend/app/engine/test_fleet_live.py", "# NOTRUN\n")
for s in ("test_fleet_adversarial.py", "test_safety_layers.py"):
    put(d, "backend/app/engine/" + s, "# NOTRUN\n")
rc, out, fin = one(d, "backend/app/engine/ic/ic_engine.py", "x = 4\n")
chk("TC-GATE-0013", "CL-07: suites not runnable with this Python -> skipped (not pass)",
    status_of(fin, "CL-07") == "skipped", fin)
rc, out, fin = one(d, "frontend/src/components/Card.jsx", "const s = { maxWidth: 1360, padding: 4 };\n")
chk("TC-GATE-0014", "CL-09: an added size value in frontend/src needs an ack", status_of(fin, "CL-09") == "needs-ack", fin)
put(d, "frontend/uitest/test_card.py", "# bbox check\n")
rc, out, fin = one(d, "frontend/src/components/Card.jsx", "const s = { maxWidth: 2000, padding: 4 };\n")
chk("TC-GATE-0015", "CL-09: a uitest change in the same commit satisfies it", status_of(fin, "CL-09") == "pass", fin)
rc, out, fin = one(d, "backend/app/execution/test_fast.py", "assert elapsed < 0.2, elapsed\n")
chk("TC-GATE-0016", "CL-10: a sub-second timing bound in a test fails", status_of(fin, "CL-10") == "fail", fin)
rc, out, fin = one(d, "apply_thing.py", "FENCE = 'THING_20261118'\nprint('no way back')\n")
chk("TC-GATE-0017", "CL-11: an apply script with no backups / --rollback fails", status_of(fin, "CL-11") == "fail", fin)
rc, out, fin = one(d, "scripts/run_suites.py", STUB_SUITES + "# runs every suite with the caller's HOME\n")
chk("TC-GATE-0055", "CL-15: a suite runner that does not give suites a throw-away HOME fails (DEF-094)",
    status_of(fin, "CL-15") == "fail", fin)
rc, out, fin = one(d, "scripts/run_suites.py", "import os, tempfile\nenv = dict(os.environ)\n"
                   "env['HOME'] = tempfile.mkdtemp()\n" + STUB_SUITES)
chk("TC-GATE-0056", "CL-15: a runner that points HOME at a temp folder passes", status_of(fin, "CL-15") == "pass", fin)
QGAP = "GAP = r\"No module named '([\\w.]+)'\"\n"
rc, out, fin = one(d, "scripts/run_suites.py", STUB_SUITES + "# HOME = tempfile.mkdtemp()\n" + QGAP)
chk("TC-GATE-0057", "CL-18: a runner that reads only \"No module named 'x'\" (not python -m's bare line) fails (DEF-098)",
    status_of(fin, "CL-18") == "fail", fin)
rc, out, fin = one(d, "scripts/run_suites.py", STUB_SUITES + "# HOME = tempfile.mkdtemp()\n" + QGAP
                   + "BARE = r\"python[\\d.]*: No module named ([\\w.]+)\"\n")
chk("TC-GATE-0058", "CL-18: a runner that reads both forms passes", status_of(fin, "CL-18") == "pass", fin)
one(d, "scripts/run_suites.py", STUB_SUITES)
rc, out, fin = one(d, "backend/app/engine/vet/vet_loop_x.py",
                   "def arm(mode):\n    executor = None\n    if mode == 'LIVE':\n"
                   "        executor = get_executor_for_strategy('VET_V1')\n    return executor\n")
chk("TC-GATE-0059", "CL-16: an engine that builds its executor only when the arm-time mode is LIVE fails (DEF-102)",
    status_of(fin, "CL-16") == "fail", fin)
rc, out, fin = one(d, "backend/app/engine/vet/vet_loop_y.py",
                   "def arm(mode):\n    try:\n        executor = get_executor_for_strategy('VET_V1')\n"
                   "    except Exception:\n        executor = None\n    return executor\n")
chk("TC-GATE-0060", "CL-16: an engine that pre-builds its executor whatever the mode passes",
    status_of(fin, "CL-16") == "pass", fin)
rc, out, fin = one(d, "backend/app/engine/bb_options/bb_rt_x.py",
                   "def arm(trade_mode):\n    if trade_mode == 'LIVE':\n"
                   "        executor = get_executor_for_strategy('BB_V1')\n    else:\n"
                   "        executor = get_executor_for_strategy('BB_V1')\n    return executor\n")
chk("TC-GATE-0061", "CL-16: a LIVE branch plus a PAPER pre-build (the BB / HA shape) passes",
    status_of(fin, "CL-16") == "pass", fin)
rc, out, fin = one(d, "scripts/hooks/regen_x.py",
                   "import subprocess\nsubprocess.run(['python3', 'gen.py'])\nsubprocess.run([\"git\", \"add\", \"MAP.md\"])\n")
chk("TC-GATE-0062", "CL-17: a hook script that runs git add fails (DEF-060)", status_of(fin, "CL-17") == "fail", fin)
rc, out, fin = one(d, "scripts/hooks/regen_x.py",
                   "import subprocess, sys\nsubprocess.run(['python3', 'gen.py'])\n"
                   "# never: subprocess.run(['git', 'add', 'MAP.md'])\nprint('refreshed — git add MAP.md and commit again')\n"
                   "sys.exit(1)\n")
chk("TC-GATE-0063", "CL-17: a hook that rewrites, exits 1 and only tells you to git add passes",
    status_of(fin, "CL-17") == "pass", fin)
rc, out, fin = one(d, "scripts/regen_tool.py", "import subprocess\nsubprocess.run(['git', 'add', 'x'])\n")
chk("TC-GATE-0064", "CL-17: a git add outside scripts/hooks/ does not trigger it", status_of(fin, "CL-17") is None, fin)
rc, out, fin = one(d, "ops/deploy-x.command",
                   "set -e\n( crontab -l 2>/dev/null | grep -v 'x-backup' ; \\\n  echo '1 1 * * * b # x-backup' \\\n) | crontab -\n")
chk("TC-GATE-0065", "CL-19: an unguarded `crontab -l | grep -v` in a deploy script fails (DEF-109)",
    status_of(fin, "CL-19") == "fail", fin)
rc, out, fin = one(d, "ops/deploy-x.command",
                   "set -e\n{ { crontab -l 2>/dev/null || true; } | { grep -v 'x-backup' || true; }; \\\n"
                   "  echo '1 1 * * * b # x-backup'; \\\n} | crontab -\n")
chk("TC-GATE-0066", "CL-19: the guarded form passes", status_of(fin, "CL-19") == "pass", fin)
rc, out, fin = one(d, "ops/notes.sh", "echo 'nothing about cron here'\n")
chk("TC-GATE-0067", "CL-19: a script without crontab -l does not trigger it", status_of(fin, "CL-19") is None, fin)
put(d, "monitor_server/deploy-monitor.command", 'FILES="server.py store.py requirements.txt"\n')
git(d, "add", "-A")
git(d, "commit", "-qm", "deploy base", "--no-verify")
rc, out, fin = one(d, "monitor_server/accounts.py", "x = 1\n")
chk("TC-GATE-0068", "CL-20: a new monitor_server module missing from the deploy list fails (DEF-110)",
    status_of(fin, "CL-20") == "fail", fin)
put(d, "monitor_server/deploy-monitor.command", 'FILES="server.py store.py accounts.py requirements.txt"\n')
rc, out, fin = one(d, "monitor_server/relay2.py", "x = 2\n")
chk("TC-GATE-0069", "CL-20: still failing for a module the list does not name (relay2.py), even with the list in the commit",
    status_of(fin, "CL-20") == "fail" and "relay2.py" in out, (fin, out[-300:]))
put(d, "monitor_server/deploy-monitor.command", "FILES=\"$(ls *.py | grep -v '^test_' | tr '\\n' ' ')requirements.txt\"\n")
rc, out, fin = one(d, "monitor_server/relay3.py", "x = 3\n")
chk("TC-GATE-0070", "CL-20: a list built from *.py passes", status_of(fin, "CL-20") == "pass", fin)
rc, out, fin = one(d, "monitor_server/test_relay3.py", "x = 4\n")
chk("TC-GATE-0071", "CL-20: a new test file does not trigger it", status_of(fin, "CL-20") is None, fin)
CONF = '{\n  "productName": "Scalp",\n  "version": "%s",\n  "identifier": "x"\n}\n'
put(d, "desktop/src-tauri/tauri.conf.json", CONF % "1.0.0")
put(d, "backend/app/version_stamp.txt", "1.0.0\n")
git(d, "add", "-A")
git(d, "commit", "-qm", "conf base", "--no-verify")
rc, out, fin = one(d, "desktop/src-tauri/tauri.conf.json", CONF % "1.0.1")
chk("TC-GATE-0072", "CL-21: a version bump without the backend stamp fails (DEF-112)", status_of(fin, "CL-21") == "fail", fin)
put(d, "desktop/src-tauri/tauri.conf.json", CONF % "1.0.2")
rc, out, fin = one(d, "backend/app/version_stamp.txt", "1.0.2\n")
chk("TC-GATE-0073", "CL-21: bump + matching stamp in the same commit passes", status_of(fin, "CL-21") == "pass", fin)
put(d, "desktop/src-tauri/tauri.conf.json", CONF % "1.0.3")
rc, out, fin = one(d, "backend/app/version_stamp.txt", "1.0.9\n")
chk("TC-GATE-0074", "CL-21: a stamp with a different version fails, naming both",
    status_of(fin, "CL-21") == "fail" and "1.0.3" in out and "1.0.9" in out, (fin, out[-300:]))
rc, out, fin = one(d, "desktop/src-tauri/tauri.conf.json", (CONF % "1.0.3").replace('"x"', '"y"'))
chk("TC-GATE-0075", "CL-21: a tauri.conf.json change that leaves the version alone does not trigger it",
    status_of(fin, "CL-21") is None, fin)
_old = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop", "deploy-scalp.command")
_r = subprocess.run(["bash", _old], capture_output=True, text=True, timeout=30)
chk("TC-GATE-0076", "the old desktop/deploy-scalp.command is retired: exits 1 and points to ./deploy-scalp.command (DEF-112)",
    _r.returncode == 1 and "./deploy-scalp.command" in _r.stdout and "retired" in _r.stdout, (_r.returncode, _r.stdout))
APPLY_RUN = ("FENCE = 'X_20261127'\nimport os, subprocess, sys, tempfile\nTESTS = ['app/jobs/test_x.py']\n"
             "# .bak- backups, --rollback\n%s\nfor t in TESTS:\n    subprocess.run([sys.executable, t], env=env)\n")
rc, out, fin = one(d, "apply_home_a.py", APPLY_RUN % "env = dict(os.environ, PYTHONPATH='backend', SCALP_TEST_MODE='1')")
chk("TC-GATE-0077", "CL-22: an apply script that runs its tests with the caller's HOME fails (DEF-113)",
    status_of(fin, "CL-22") == "fail" and "apply_home_a.py" in out, (fin, out[-300:]))
rc, out, fin = one(d, "apply_home_b.py", APPLY_RUN % ("env = dict(os.environ, PYTHONPATH='backend', SCALP_TEST_MODE='1')\n"
                                                      "env['HOME'] = tempfile.mkdtemp(prefix='apply_home_')"))
chk("TC-GATE-0078", "CL-22: an apply script whose tests get HOME = a temp folder passes", status_of(fin, "CL-22") == "pass", fin)
rc, out, fin = one(d, "apply_home_c.py", "FENCE = 'Y_20261127'\n# .bak- backups, --rollback\nprint('no tests')\n")
chk("TC-GATE-0079", "CL-22: an apply script that runs no tests does not trigger it", status_of(fin, "CL-22") is None, fin)
rc, out, fin = one(d, "backend/app/engine/vet/vet_exit.py", "if now_ts > deadline_ts:\n    close_trade(x)\n")
chk("TC-GATE-0018", "CL-08 and CL-12: a ts comparison and a close in engine code need acks",
    status_of(fin, "CL-08") == "needs-ack" and status_of(fin, "CL-12") == "needs-ack", fin)
rc, out, fin = one(d, "backend/app/engine/vet/vet_sig.py", "stop = entry * 1.3\n")
chk("TC-GATE-0019", "CL-13: Velvet stop logic changed with nothing under backtest/vet/ needs an ack",
    status_of(fin, "CL-13") == "needs-ack", fin)
put(d, "backend/app/backtest/vet/runner.py", "stop = 1\n")
rc, out, fin = one(d, "backend/app/engine/vet/vet_sig.py", "stop = entry * 1.4\n")
chk("TC-GATE-0020", "CL-13: the same with a backtest/vet/ change in the commit does not trigger",
    status_of(fin, "CL-13") is None, fin)
rc, out, fin = one(d, "backend/app/engine/tsg/tsg_x.py", "from app.engine.tma2.tma2_state import STATE\n")
chk("TC-GATE-0021", "CL-14: Tigris importing Timberwolf's package needs an ack", status_of(fin, "CL-14") == "needs-ack", fin)
rc, out, fin = one(d, "README.md", "docs only\n")
chk("TC-GATE-0022", "a docs-only commit: 'none triggered'", stamp(fin) == ["Checklist: observe · none triggered"], fin)
shutil.rmtree(d, ignore_errors=True)

# ── modes, acks, valve ───────────────────────────────────────────────
d = repo("observe")
rc, out, fin = one(d, "backend/app/engine/vet/vet_monitor.py", "pos = self.kite.positions()\n")
log = [json.loads(x) for x in open(os.path.join(d, ".git", "scalp-gate.log"))]
chk("TC-GATE-0023", "observe: a failing item does not block; logged as would_block",
    rc == 0 and log[-1]["would_block"] is True and not log[-1].get("blocked"), (rc, log[-1:]))
shutil.rmtree(d, ignore_errors=True)

d = repo("enforce")
put(d, "backend/app/engine/vet/vet_monitor.py", "pos = self.kite.positions()\n")
git(d, "add", "-A")
rc, out, fin = commit(d, "v1")
chk("TC-GATE-0024", "enforce: a failing item blocks and prints the ack command",
    rc == 1 and 'echo "CL-02' in out and ".git/scalp-ack" in out, out)
rc, out, fin = commit(d, "v1\n\nAck: CL-02 display only, not a close decision")
chk("TC-GATE-0025", "enforce: 'Ack: CL-02 <why>' in the message lets it through, stamped acked",
    rc == 0 and status_of(fin, "CL-02") == "acked", (rc, fin))
put(d, "backend/app/engine/vet/vet_monitor2.py", "pos = self.kite.positions()\n")
git(d, "add", "-A")
put(d, ".git/scalp-ack", "CL-02 read for the UI only\n")
rc, out, fin = commit(d, "v2 [arm] [intel] [win]")
chk("TC-GATE-0026", "enforce: an ack in .git/scalp-ack works (deploy-scalp.command path) and is used up",
    rc == 0 and status_of(fin, "CL-02") == "acked" and not os.path.exists(os.path.join(d, ".git", "scalp-ack"))
    and "[arm] [intel] [win]" in fin.splitlines()[0], (rc, fin))
put(d, "backend/app/engine/vet/vet_monitor3.py", "pos = self.kite.positions()\n")
git(d, "add", "-A")
rc, out, fin = commit(d, "v3\n\nUnverified: hotfix during an incident")
chk("TC-GATE-0027", "enforce: 'Unverified: <why>' lets it through, stamped unverified",
    rc == 0 and stamp(fin) and stamp(fin)[0].endswith("· unverified"), (rc, fin))
os.remove(os.path.join(d, ".git", "scalp-gate-state.json")) if os.path.exists(os.path.join(d, ".git", "scalp-gate-state.json")) else None
put(d, "backend/app/engine/vet/vet_monitor4.py", "pos = self.kite.positions()\n")
git(d, "add", "-A")
r1 = commit(d, "v4")[0]
r2 = commit(d, "v4")[0]
r3, out3, fin3 = commit(d, "v4")
chk("TC-GATE-0028", "enforce: blocked twice in an hour, the third commit goes through stamped 'forced'",
    (r1, r2, r3) == (1, 1, 0) and stamp(fin3) and "forced after 2 blocks" in stamp(fin3)[0], (r1, r2, r3, fin3))
put(d, "backend/app/engine/vet/vet_monitor5.py", "pos = self.kite.positions()\n")
git(d, "add", "-A")
rc, out, fin = commit(d, "v5", env={"SCALP_GATE": "observe"})
chk("TC-GATE-0029", "SCALP_GATE=observe for one command overrides enforce", rc == 0 and stamp(fin)[0].startswith("Checklist: observe"), fin)
put(d, "README.md", "x\n")
git(d, "add", "-A")
rc, out, fin = commit(d, "v6", env={"SCALP_GATE": "off"})
chk("TC-GATE-0030", "SCALP_GATE=off: nothing runs, no stamp", rc == 0 and not stamp(fin), fin)
shutil.rmtree(d, ignore_errors=True)

# ── the change cannot loosen its own checks ─────────────────────────
d = repo("enforce")
src = open(os.path.join(d, "scripts/checklist/items.py")).read().replace('dict(id="CL-02"', 'dict(id="CL-02", disabled=True')
put(d, "scripts/checklist/items.py", src.replace("ITEMS = [", "ITEMS = [] and [", 1))
put(d, "scripts/checklist/MODE", "off\n")
put(d, "backend/app/engine/vet/vet_monitor.py", "pos = self.kite.positions()\n")
git(d, "add", "-A")
rc, out, fin = commit(d, "sneaky")
chk("TC-GATE-0031", "items.py emptied and MODE set to off IN the same commit: HEAD's items + enforce still apply",
    rc == 1 and "CL-02" in out and "items from HEAD" in out, out)
shutil.rmtree(d, ignore_errors=True)

d = repo("observe", items_src=open(ITEMS).read().replace("def cl01(ctx):\n", "def cl01(ctx):\n    raise ValueError('boom')\n"))
rc, out, fin = one(d, "backend/app/x.py", 'API_KEY = "a1b2c3d4e5f6g7h8i9j0"\n')
chk("TC-GATE-0032", "a broken item is reported as error and never blocks", rc == 0 and status_of(fin, "CL-01") == "error", fin)
shutil.rmtree(d, ignore_errors=True)

# ── stamp format ─────────────────────────────────────────────────────
d = repo()
rc, out, fin = one(d, "README.md", "a\n", msg="subject\n\nbody line\n\nCo-Authored-By: X <x@x>")
lines = fin.rstrip("\n").splitlines()
chk("TC-GATE-0033", "stamp joins an existing trailer block (no blank line before it)",
    lines[-1].startswith("Checklist: ") and lines[-2].startswith("Co-Authored-By:"), lines)
put(d, "README.md", "b\n")
git(d, "add", "-A")
rc, out, fin = commit(d, "subject\n\nChecklist: observe · CL-09 needs-ack")
chk("TC-GATE-0034", "an old Checklist line (amend) is replaced, not duplicated", len(stamp(fin)) == 1 and "none triggered" in fin, fin)
shutil.rmtree(d, ignore_errors=True)

# ── CLI modes ────────────────────────────────────────────────────────
d = repo()
put(d, "backend/app/engine/vet/vet_monitor.py", "pos = self.kite.positions()\n")
put(d, "backend/app/engine/vet/new_file.py", "p = kite.positions()\n")
git(d, "add", "backend/app/engine/vet/vet_monitor.py")


def cli(*a):
    r = subprocess.run([sys.executable, "scripts/hooks/reliability_checklist.py", *a], cwd=d, env=ENV,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


rc, out = cli("--dry-run")
chk("TC-GATE-0035", "--dry-run: staged file only, exit 1 on a fail, no log written",
    rc == 1 and "vet_monitor.py" in out and "new_file.py" not in out
    and not os.path.exists(os.path.join(d, ".git", "scalp-gate.log")), out)
rc, out = cli("--worktree")
chk("TC-GATE-0036", "--worktree: staged, unstaged and untracked files (Claude's check on a patched clone)",
    rc == 1 and "vet_monitor.py" in out and "new_file.py" in out, out)
git(d, "add", "-A")
git(d, "commit", "-qm", "c1", "--no-verify")
rc, out = cli("--range", "HEAD~1..HEAD")
chk("TC-GATE-0037", "--range A..B replays a past commit", rc == 1 and "CL-02 fail" in out, out)
rc, out = cli("--list")
chk("TC-GATE-0038", "--list shows 22 items, each with trigger, check and incidents",
    "22 items" in out and out.count("incidents:") == 22 and out.count("trigger:") == 22, out[:300])
commit(d, "x")
rc, out = cli("--report")
chk("TC-GATE-0039", "--report summarises the log", rc == 0 and "commits logged" in out, out)
shutil.rmtree(d, ignore_errors=True)

# ── items.py itself ──────────────────────────────────────────────────
ns = {}
exec(compile(open(ITEMS).read(), ITEMS, "exec"), ns)
ids = [i["id"] for i in ns["ITEMS"]]
chk("TC-GATE-0040", "items: unique ids CL-01…CL-22, kind auto|ack, every item names its incidents",
    ids == [f"CL-{n:02d}" for n in range(1, 23)] and all(i["kind"] in ("auto", "ack") and i["incidents"]
                                                         and i["trigger"] and i["check"] for i in ns["ITEMS"]), ids)

print("\n" + ("ALL RELIABILITY CHECKLIST CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
