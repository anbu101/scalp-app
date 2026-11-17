#!/usr/bin/env python3
# scripts/ci/test_ci.py  — ── CI_20261117 ── (TD-041) TC-CI-0001…0020
#
# Checks the CI runner and the workflow file without GitHub: suite modes,
# the SKIP / KNOWN_FAIL lists, sharding, output, exit codes, HOME isolation,
# the step summary, and the workflow's triggers, permissions, caches and pins.
#   python3 scripts/ci/test_ci.py     (stdlib only; exit 1 on any failure)
import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUN = os.path.join(ROOT, "scripts", "ci", "run_ci_suites.py")
WF = os.path.join(ROOT, ".github", "workflows", "ci.yml")
F = 0


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


def sh(*a, env=None):
    r = subprocess.run([sys.executable, RUN, *a], cwd=ROOT, capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, **(env or {})))
    return r.returncode, r.stdout + r.stderr


spec = importlib.util.spec_from_file_location("run_ci_suites_t", RUN)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

# 0001 stdlib only
mods = []
for n in ast.walk(ast.parse(open(RUN, encoding="utf-8").read())):
    mods += [a.name for a in n.names] if isinstance(n, ast.Import) else \
        [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
bad = [m for m in mods if m.split(".")[0] not in sys.stdlib_module_names]
chk("TC-CI-0001", "the runner imports the standard library only", not bad, bad)

# 0002 modes, as the suites are run on the Mac today
want = {
    "backend/app/engine/ic/test_ic_group_manager.py": ("pytest", "dir"),
    "backend/app/engine/ic/test_ic_gtt_strict.py": ("pytest", "dir"),
    "backend/app/execution/test_kill_switch.py": ("pytest", "dir"),
    "backend/app/engine/brk/test_brk_live_core.py": ("script", "root"),
    "backend/app/engine/vet/test_vet_manager.py": ("script", "backend"),
    "backend/app/engine/test_fleet_live.py": ("script", "backend"),
}
got = {k: (R.mode_of(k)["runner"], R.mode_of(k)["cwd"]) for k in want if os.path.exists(os.path.join(ROOT, k))}
chk("TC-CI-0002", "Icarus/kill-switch suites run under pytest from their folder, BRK from the repo root, the rest "
    "as scripts from backend/", got == {k: v for k, v in want.items() if k in got} and len(got) >= 5, got)
chk("TC-CI-0003", "test_ic_iv_sl.py gets backend/app/backtest/ic on its path (imports ic_v1_engine by bare name)",
    R.mode_of("backend/app/engine/ic/test_ic_iv_sl.py")["path"] == ["backend/app/backtest/ic"])

# 0004 lists point at real files, with reasons
stale = [p for p in list(R.SKIP) + list(R.KNOWN_FAIL) + list(R.OVERRIDE)
         if not os.path.exists(os.path.join(ROOT, p)) and not p.startswith("scripts/")]
chk("TC-CI-0004", "every SKIP / KNOWN_FAIL / OVERRIDE entry names a file that exists", not stale, stale)
chk("TC-CI-0005", "every SKIP has a reason; every KNOWN_FAIL cites its DEF id",
    all(v.strip() for v in R.SKIP.values()) and all(re.match(r"DEF-\d{3} ", v) for v in R.KNOWN_FAIL.values()))
kite_skips = [p for p, v in R.SKIP.items() if "Kite" in v]
chk("TC-CI-0006", "the suites that call Kite or use real credentials are skipped (3)",
    sorted(kite_skips) == sorted(["backend/app/tests/test_historical_candles.py",
                                  "backend/app/tests/test_zerodha_historical.py",
                                  "backend/app/tools/test_zerodha_executor.py"]), kite_skips)

# 0007 sharding
allsu = R.discover(["backend"])
parts = [R.shard(allsu, i, 3) for i in range(3)]
flat = [s for p in parts for s in p]
chk("TC-CI-0007", "3 shards cover every backend suite exactly once, the same way every run",
    sorted(flat) == sorted(allsu) and len(flat) == len(set(flat)) and parts == [R.shard(allsu, i, 3) for i in range(3)],
    (len(flat), len(allsu)))
import json  # noqa: E402
dur = json.load(open(os.path.join(ROOT, "scripts", "ci", "suite_durations.json")))
loads = [sum(dur.get(s, 5.0) for s in p) for p in parts]
chk("TC-CI-0008", f"shards are balanced by measured time (loads {[round(x) for x in loads]} s, within 10%)",
    max(loads) <= 1.10 * min(loads), loads)

# 0009-0014 behaviour on synthetic suites
d = tempfile.mkdtemp(prefix="ci_selftest_")
try:
    open(os.path.join(d, "test_ok.py"), "w").write(
        "import os, sys\nh = os.environ['HOME']\n"
        "assert 'ci_home_' in h and not os.listdir(h), h\n"
        "assert os.environ.get('SCALP_TEST_MODE') == '1'\nprint('noise\\n' * 50)\nprint('ALL OK CHECKS PASSED')\n")
    open(os.path.join(d, "test_bad.py"), "w").write(
        "import sys\nfor i in range(60): print('line', i)\nprint('BOOM at the end')\nsys.exit(1)\n")
    open(os.path.join(d, "test_slow.py"), "w").write("import time\ntime.sleep(30)\n")
    HAVE_PYTEST = importlib.util.find_spec("pytest") is not None
    open(os.path.join(d, "test_pyt.py" if HAVE_PYTEST else "pyt_probe.py"), "w").write(
        "import pytest\n\ndef test_a():\n    assert 1 == 1\n")
    NS = 4 if HAVE_PYTEST else 3
    summ = os.path.join(d, "summary.md")
    rc, out = sh(d, "-j", "4", "--timeout", "5", env={"GITHUB_STEP_SUMMARY": summ})
    lines = out.strip().splitlines()
    one = [l for l in lines if re.match(r"^(PASS|FAIL|SKIP|XFAIL|XPASS)\s+[\d.]+s\s", l)]
    chk("TC-CI-0009", f"one line per suite ({NS} suites -> {NS} lines) with result, seconds and its last line",
        len(one) == NS and any("test_ok.py  — ALL OK CHECKS PASSED" in l for l in one), one)
    chk("TC-CI-0010", "a failing suite shows only its last 30 lines (line 31..59 + BOOM, not line 30) and exits 1",
        rc == 1 and "BOOM at the end" in out and "line 31\n" in out and "line 30\n" not in out, out[-600:])
    chk("TC-CI-0011", "a suite over --timeout is killed and reported FAIL (timeout)",
        any(l.startswith("FAIL") and "test_slow.py" in l for l in one) and "timeout after 5s" in out, one)
    chk("TC-CI-0012", "each suite gets its own empty HOME and SCALP_TEST_MODE=1 (test_ok asserts both)",
        any(l.startswith("PASS") and "test_ok.py" in l for l in one), one)
    pyt = R.mode_of(os.path.relpath(os.path.join(d, "test_pyt.py" if HAVE_PYTEST else "pyt_probe.py"), ROOT))
    chk("TC-CI-0013", "a file that imports pytest runs under pytest and passes"
        + ("" if HAVE_PYTEST else " (pytest not installed here: mode checked only)"),
        pyt["runner"] == "pytest" and (not HAVE_PYTEST or any(l.startswith("PASS") and "test_pyt.py" in l for l in one)),
        (pyt, one))
    s_ = open(summ, encoding="utf-8").read() if os.path.exists(summ) else ""
    chk("TC-CI-0014", "summary line counts are right and the step-summary table lists FAILs first",
        lines[-1].startswith(f"{NS - 2} passed, 2 failed, 0 known-failing, 0 now passing, 0 skipped — {NS} suites")
        and s_.index("| FAIL |") < s_.index("| PASS |"), lines[-1])
    # known-fail handling, through the module
    rel_bad = os.path.relpath(os.path.join(d, "test_bad.py"), ROOT).replace(os.sep, "/")
    rel_ok = os.path.relpath(os.path.join(d, "test_ok.py"), ROOT).replace(os.sep, "/")
    R.KNOWN_FAIL[rel_bad] = "DEF-999 test"
    R.KNOWN_FAIL[rel_ok] = "DEF-998 test"
    st_bad, st_ok = R.run_one(rel_bad, 30)[1], R.run_one(rel_ok, 30)[1]
    chk("TC-CI-0015", "a KNOWN_FAIL suite that fails is XFAIL (no red build); one that passes is XPASS",
        (st_bad, st_ok) == ("XFAIL", "XPASS"), (st_bad, st_ok))
    R.SKIP[rel_bad] = "test skip"
    chk("TC-CI-0016", "a SKIP suite is not run at all", R.run_one(rel_bad, 30)[1:3] == ("SKIP", 0.0))
    os.remove(os.path.join(d, "test_bad.py"))
    os.remove(os.path.join(d, "test_slow.py"))
    rc, out = sh(d, "-j", "2")
    chk("TC-CI-0017", "all passing exits 0", rc == 0 and out.strip().splitlines()[-1].startswith(f"{NS - 2} passed, 0 failed"),
        out[-300:])
finally:
    shutil.rmtree(d, ignore_errors=True)

# 0018-0020 the workflow file
wf = open(WF, encoding="utf-8").read()
chk("TC-CI-0018", "runs on push to main and by hand only; contents: read; no secrets, no Kite/Zerodha settings",
    re.search(r"on:\n  push:\n    branches: \[main\]\n  workflow_dispatch:\n\n", wf) is not None
    and "permissions:\n  contents: read" in wf and "secrets." not in wf and "pull_request_target" not in wf
    and not re.search(r"(?i)(kite|zerodha|api_key|api_secret|access_token)\s*:", wf))
sp = re.findall(r'python-version:\s*"([^"]+)"', wf)
chk("TC-CI-0019", "Python 3.10 everywhere, pip + npm caches, node 18 like build-release.yml, every checkout "
    "without stored credentials, every job time-limited (≤15 min)",
    sp and set(sp) == {"3.10"} and wf.count("cache: pip") == 2 and "cache: npm" in wf
    and 'node-version: "18"' in wf and wf.count("actions/checkout@") == wf.count("persist-credentials: false")
    and all(int(x) <= 15 for x in re.findall(r"timeout-minutes:\s*(\d+)", wf))
    and len(re.findall(r"timeout-minutes:", wf)) == 3, sp)
chk("TC-CI-0020", "backend shards 1-3 call the runner with --shard N/3; frontend runs react-scripts build; "
    "pre-commit only when .pre-commit-config.yaml exists",
    "shard: [1, 2, 3]" in wf and '--shard "${{ matrix.shard }}/3"' in wf and "npm run build" in wf
    and "[ -f .pre-commit-config.yaml ]" in wf and wf.count("if: steps.cfg.outputs.present == 'true'") == 3)

print("\n" + ("ALL CI CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
