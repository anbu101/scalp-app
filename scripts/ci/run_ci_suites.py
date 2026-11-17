#!/usr/bin/env python3
# scripts/ci/run_ci_suites.py  — ── CI_20261117 ── (TD-041)
#
# Runs every backend test script (standalone, exit-code suites — not pytest)
# the way they are run on the Mac, in parallel, each with its own empty HOME
# (no ~/.scalp-app, no tokens, no broker) and prints:
#   one line per suite   PASS / FAIL / SKIP / XFAIL / XPASS, seconds, last line
#   the last 30 lines of every FAIL (in a collapsible group on GitHub)
#   one summary line;  a table in $GITHUB_STEP_SUMMARY when it is set
# Exit 1 only on FAIL (a suite not known to fail that failed). Stdlib only
# (pytest itself is needed for the suites that import it).
#
#   python3 scripts/ci/run_ci_suites.py [PATH ...] [-j N] [--timeout S] [--shard I/N] [--list]
#   PATH defaults to backend/ (folders searched for test_*.py, or single files).
#
# MANIFEST says which suites must not run in CI (and why) and which are known
# to fail on main (with their DEF id): those run, and report XFAIL; one that
# starts passing reports XPASS so it can be taken off the list.
import ast
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
HERE = os.path.dirname(os.path.abspath(__file__))

# How a suite is run, as on the Mac today:
#   script  python3 <file>, cwd=backend, PYTHONPATH=backend          (the default)
#   pytest  python3 -m pytest -q <file>, cwd=<its folder>,
#           PYTHONPATH=backend:.   (any file that imports pytest; the way
#           test_ic_gtt_strict.py runs the Icarus suites)
#   root    python3 <file>, cwd=repo root ("Run from repo root" suites)
# "path" adds folders to PYTHONPATH for a suite that imports a module from
# another folder by its bare name.
OVERRIDE = {
    "backend/app/engine/brk/test_brk_live_core.py": {"cwd": "root"},
    "backend/app/engine/brk/test_brk_manager.py": {"cwd": "root"},
    "backend/app/engine/ic/test_ic_iv_sl.py": {"path": ["backend/app/backtest/ic"]},
}
SKIP = {
    # live probes / real credentials: never in CI
    "backend/app/tests/test_historical_candles.py": "calls the Kite historical API (live probe, needs a login)",
    "backend/app/tests/test_zerodha_historical.py": "calls Kite (load_kite + instruments; live probe)",
    "backend/app/tools/test_zerodha_executor.py": "builds a real Kite executor from hard-coded credentials (DEF-047)",
    # need the Mac's own data under ~/.scalp-app (CI has an empty HOME)
    "backend/app/tests/test_strategy_entry.py": "needs the Mac's recorded candles (~/.scalp-app/candles/...)",
    "backend/app/dev/test_paper_eod.py": "dev script: runs the EOD job against the Mac's app.db",
    # not a test
    "backend/test_server.py": "not a test (FastAPI ping app)",
    # checks the local clone's git config (core.hooksPath) — meaningless on a CI checkout
    "scripts/test_repo_map_hook.py": "checks this clone's core.hooksPath (local setup check)",
}
KNOWN_FAIL = {
    "backend/app/backtest/cbo/test_cbo_runner_sim.py": "DEF-048 stale: expects exit label SL, runner returns SL_SPOT",
    "backend/app/config/test_ic_split_invariants.py": "DEF-049 stale: IC_V2 premium_max is 100, test wants IC_V1's 85",
    "backend/app/db/test_paper_trade_squareoff.py": "DEF-050 stale: overnight-exempt set grew (TMA_V2, VET_V1, TMA_V1)",
    "backend/app/tests/test_expiry_card.py": "DEF-051 date-dependent: holiday-shifted Monday expiries in the last month",
    "backend/app/tests/test_indicator_warmup.py": "DEF-052 stale: IndicatorEngine no longer exists",
    "backend/app/tools/test_trade_state.py": "DEF-053 stale dev script (LogOrderExecutor is abstract now)",
    "backend/app/tools/test_option_selection.py": "DEF-054 stale dev script (load_strategy_config needs strategy_id)",
    "backend/app/tools/test_full_logging_pipeline.py": "DEF-055 stale dev script (StrategyEngine needs 3 more args)",
}


def mode_of(rel):
    m = {"runner": "script", "cwd": "backend", "path": []}
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        has_main = has_tests = imports_pytest = main_pytest = False
        for n in tree.body:
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
            imports_pytest |= any(x.split(".")[0] == "pytest" for x in mods)
            has_tests |= isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
            if isinstance(n, ast.If) and "__main__" in ast.dump(n.test):
                has_main = True
                main_pytest = "'pytest'" in ast.dump(n)
        if imports_pytest or (has_tests and (not has_main or main_pytest)):
            m.update(runner="pytest", cwd="dir")
    except (OSError, SyntaxError, ValueError):
        pass
    if not rel.startswith("backend/") and m["runner"] == "script":
        m["cwd"] = "root"                # repo tooling tests (scripts/...) run from the root
    m.update(OVERRIDE.get(rel, {}))
    return m


def discover(args):
    roots = args or ["backend"]
    out = []
    for a in roots:
        p = a if os.path.isabs(a) else os.path.join(ROOT, a)
        if os.path.isfile(p):
            out.append(os.path.relpath(p, ROOT).replace(os.sep, "/"))
            continue
        for d, dirs, fs in os.walk(p):
            dirs[:] = sorted(x for x in dirs if x not in {"__pycache__", "venv", ".venv", "node_modules", "build", "dist"})
            for f in sorted(fs):
                if f.startswith("test_") and f.endswith(".py") and ".bak-" not in f:
                    out.append(os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/"))
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def run_one(rel, timeout):
    if rel in SKIP:
        return rel, "SKIP", 0.0, SKIP[rel], ""
    m = mode_of(rel)
    home = tempfile.mkdtemp(prefix="ci_home_")
    path = os.path.join(ROOT, rel)
    cwd = {"backend": BACKEND, "root": ROOT, "dir": os.path.dirname(path)}[m["cwd"]]
    pp = [BACKEND] + [os.path.join(ROOT, x) for x in m["path"]] + (["."] if m["runner"] == "pytest" else [])
    env = dict(os.environ, HOME=home, PYTHONPATH=os.pathsep.join(pp), SCALP_TEST_MODE="1", MPLBACKEND="Agg",
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", os.path.basename(path)] \
        if m["runner"] == "pytest" else [sys.executable, path]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += f"\n(timeout after {timeout}s)"
        rc = -9
    finally:
        shutil.rmtree(home, ignore_errors=True)
    dt = time.time() - t0
    last = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "")[:160]
    if rel in KNOWN_FAIL:
        return rel, ("XFAIL" if rc != 0 else "XPASS"), dt, \
            (KNOWN_FAIL[rel] if rc != 0 else "now passes — take it off KNOWN_FAIL"), out
    return rel, ("PASS" if rc == 0 else "FAIL"), dt, (last if rc == 0 else f"exit {rc}: {last}"), out


def shard(suites, i, n):
    """Longest-first onto the lightest shard, by the times in suite_durations.json."""
    try:
        with open(os.path.join(HERE, "suite_durations.json"), encoding="utf-8") as f:
            dur = json.load(f)
    except (OSError, ValueError):
        dur = {}
    load, pick = [0.0] * n, [[] for _ in range(n)]
    for s in sorted(suites, key=lambda x: (-dur.get(x, 5.0), x)):
        k = min(range(n), key=lambda j: (load[j], j))
        load[k] += dur.get(s, 5.0)
        pick[k].append(s)
    return sorted(pick[i])


def main(argv):
    jobs, timeout, paths, sh, listing = os.cpu_count() or 2, 600, [], None, False
    it = iter(argv)
    for a in it:
        if a == "-j":
            jobs = int(next(it))
        elif a == "--timeout":
            timeout = int(next(it))
        elif a == "--shard":
            i, n = next(it).split("/")
            sh = (int(i) - 1, int(n))
        elif a == "--list":
            listing = True
        else:
            paths.append(a)
    suites = discover(paths)
    if sh:
        suites = shard(suites, *sh)
    if not suites:
        print("no suites found")
        return 2
    if listing:
        for s_ in suites:
            m = mode_of(s_)
            how = "skip: " + SKIP[s_] if s_ in SKIP else f"{m['runner']} cwd={m['cwd']}" + (
                " +path " + ",".join(m["path"]) if m["path"] else "") + (" [known-fail]" if s_ in KNOWN_FAIL else "")
            print(f"{s_}  {how}")
        return 0
    gh = os.environ.get("GITHUB_ACTIONS") == "true"
    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(run_one, s, timeout) for s in suites]
        for f in cf.as_completed(futs):
            rel, st, dt, note, out = f.result()
            results.append((rel, st, dt, note, out))
            print(f"{st:5} {dt:6.1f}s  {rel}  — {note}", flush=True)
    fails = [r for r in results if r[1] == "FAIL"]
    for rel, st, dt, note, out in sorted(fails):
        print(f"::group::FAIL {rel}" if gh else f"\n----- FAIL {rel} (last 30 lines) -----")
        print("\n".join(out.rstrip().splitlines()[-30:]))
        if gh:
            print("::endgroup::")
            print(f"::error file={rel}::suite failed — {note}")
    for rel, st, dt, note, out in results:
        if gh and st == "XPASS":
            print(f"::warning file={rel}::known-failing suite now passes — take it off KNOWN_FAIL")
    count = {k: sum(1 for r in results if r[1] == k) for k in ("PASS", "FAIL", "XFAIL", "XPASS", "SKIP")}
    line = (f"{count['PASS']} passed, {count['FAIL']} failed, {count['XFAIL']} known-failing, "
            f"{count['XPASS']} now passing, {count['SKIP']} skipped — {len(results)} suites in {time.time() - t0:.0f}s")
    print(line)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(f"### Backend suites\n\n**{line}**\n\n| Result | Suite | s | Note |\n|---|---|---|---|\n")
            order = {"FAIL": 0, "XPASS": 1, "XFAIL": 2, "SKIP": 3, "PASS": 4}
            for rel, st, dt, note, _ in sorted(results, key=lambda r: (order[r[1]], r[0])):
                f.write(f"| {st} | `{rel}` | {dt:.1f} | {note.replace('|', '/')} |\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
