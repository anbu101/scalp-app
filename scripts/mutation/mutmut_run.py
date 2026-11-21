#!/usr/bin/env python3
# scripts/mutation/mutmut_run.py — ── MUTMUT_20261117 ── (TD-043)
#
# Weekend mutation testing (mutmut 3) of the safety-critical backend modules.
# Offline, resumable, and it never touches the live app: every module runs in
# its own copy under --work (default ~/scalp-mutation), with HOME pointed at
# an empty folder there, SCALP_TEST_MODE=1, broker/Telegram secrets stripped
# from the environment and every non-loopback socket blocked.
#
#   python3 scripts/mutation/mutmut_run.py --setup        once, online: venv + mutmut (from the
#                                                         backend venv's Python; --python PATH)
#   python3 scripts/mutation/mutmut_run.py                all modules (resumes)
#   python3 scripts/mutation/mutmut_run.py broker_truth kill_switch
#   python3 scripts/mutation/mutmut_run.py --summary      table only, runs nothing
#   python3 scripts/mutation/mutmut_run.py --fresh exit_order_driver   drop results first
#   options: --work DIR  -j N (parallel mutants, default CPUs-1)
#            --budget-min M (stop a module after M minutes; the next run resumes)
#            --list (modules, files, suites)
#
# Ctrl-C, a sleep or a crash loses at most the mutants in flight: mutmut keeps
# every result in <work>/<module>/mutants/**.meta and a rerun skips them. A
# module whose source or suites changed since its results were taken
# starts again by itself. Console output is the summary table; mutmut's own
# output goes to <work>/<module>/mutmut.log, survivors (file:line + diff) to
# <work>/<module>/survivors.txt, and the table to <work>/SUMMARY.md.
# Stdlib only (runs on the system python3; mutmut lives in <work>/.venv).
import argparse
import ast
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
HERE = os.path.dirname(os.path.abspath(__file__))
FENCE = "MUTMUT_20261117"
MUTMUT_PIN = "mutmut==3.8.1"
PYTEST_PIN = "pytest==8.3.4"

E, N = "app/execution/", "app/engine/"
MODULES = {
    # broker truth / strict reads + shared-contract LEDGER + exit-once + blind-stop + kill list
    "broker_truth": {
        "file": E + "broker_truth.py",
        "reach": r"broker_truth|_manager\b|_live_core|test_fleet_live|kill_switch",
        "suites": [E + "test_broker_truth.py", E + "test_strict_reads_all.py", E + "test_blind_stop.py",
                   E + "test_instance_guard.py", E + "test_mutmut_safety.py",
                   N + "test_exit_once_restart.py", N + "test_entry_reservation.py",
                   N + "test_tigris_exit_restart.py", N + "tma/test_tma_exit_paths.py",
                   N + "test_safety_layers.py", N + "test_fleet_live.py", N + "test_fleet_adversarial.py"],
    },
    # the order executor (Kite orders, GTTs, re-peg, wing fill)
    "zerodha_executor": {
        "file": E + "zerodha_executor.py",
        "reach": r"zerodha_executor|ZerodhaExecutor|testkit_fake_kite|test_fleet_live",
        "suites": [E + "test_order_once.py", E + "test_executor_pricing.py", E + "test_gtt_verify_strict.py",
                   E + "test_oco_stop_buffer.py", E + "test_wing_fast_fill.py", E + "test_exit_latency.py",
                   E + "test_order_latency.py", E + "test_exit_repeg_fast.py", E + "test_instance_guard.py",
                   "app/slippage/test_slip.py",
                   N + "test_fleet_live.py"],
    },
    # what strategies call instead of the executor
    "execution_router": {
        "file": E + "execution_router.py",
        "reach": r"execution_router|ExecutionRouter|test_fleet_live",
        "suites": [E + "test_execution_router_contract.py", E + "test_strict_reads_all.py",
                   E + "test_gtt_verify_strict.py", E + "test_oco_stop_buffer.py", E + "test_exit_latency.py",
                   E + "test_exit_repeg_fast.py", E + "test_wing_fast_fill.py", N + "test_fleet_live.py"],
    },
    # exit drivers
    "exit_order_driver": {
        "file": E + "exit_order_driver.py",
        "reach": r"exit_order_driver|drive_exit_order|_trade_manager|_group_manager|vet_manager|tsg_manager|"
                 r"wing_entry|test_fleet_live",
        "suites": [E + "test_exit_repeg_fast.py", E + "test_mutmut_safety.py"],
    },
    "kill_switch": {
        "file": E + "kill_switch.py",
        "reach": r"kill_switch",
        "suites": [E + "test_kill_switch.py", "app/trading/test_fleet_modes.py", E + "test_mutmut_safety.py"],
    },
}
ORDER = list(MODULES)
# Besides the suites named above, any backend suite that mentions a module's
# "reach" words (the module, its callers, the fleet harness) is offered to
# mutmut, which then keeps per function only the suites that really reach it.
REACH_SKIP = ("app/backtest/", "app/api/", "app/tests/", "app/dev/", "app/tools/")


# Never offered, whatever else says: live probes and real credentials (DEF-047), the Mac's own data.
NEVER = {
    "app/tools/test_zerodha_executor.py": "builds a real Kite executor from hard-coded credentials (DEF-047)",
    "app/tests/test_historical_candles.py": "calls the Kite historical API (live probe)",
    "app/tests/test_zerodha_historical.py": "calls Kite (live probe)",
    "app/tests/test_strategy_entry.py": "needs the Mac's recorded candles",
    "app/dev/test_paper_eod.py": "runs against the Mac's app.db",
}


def ci_lists():
    """NEVER + CI's SKIP / KNOWN_FAIL (scripts/ci/run_ci_suites.py, if the repo has it), as
    backend-relative paths -> reason. Never offered to mutmut: live probes, credentials, failing on main."""
    out = dict(NEVER)
    try:
        with open(os.path.join(ROOT, "scripts", "ci", "run_ci_suites.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return out
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("SKIP", "KNOWN_FAIL") for t in n.targets):
            try:
                for k, v in ast.literal_eval(n.value).items():
                    if k.startswith("backend/"):
                        out[k[len("backend/"):]] = str(v)
            except ValueError:
                pass
    return out

# broker_truth holds five safety areas; the table splits it by function name.
AREAS = [
    ("kill list", r"GttKillList|_kill_dir|fired_gtt_orders"),
    ("blind stop", r"blind_stop_orders|foreign_exit_fill"),
    ("exit once", r"exit_intent|_intent_|send_exit_once|find_tagged|ExitCheckPending|_app_orders|"
                  r"note_app_order|claim_orders|reset_app_orders|app_order_ids"),
    ("ledger", r"LiveLedger|_db_holders|_same_account|own_net_qty|_exit_order_seen|flat_proven|expected_net"),
    ("strict reads", r"read_positions|read_gtts|_strict_capable|infer_allowed|positions_strict|gtts_strict|"
                     r"session_open|BrokerUnreadable"),
]
STATUS = {1: "killed", 3: "killed", 0: "survived", 5: "no tests", 33: "no tests", 34: "skipped",
          36: "timeout", -24: "timeout", 24: "timeout", 152: "timeout", 255: "timeout", 37: "type check"}
SECRET_ENV = re.compile(r"KITE|ZERODHA|DHAN|ANGEL|TELEGRAM|TOKEN|SECRET|PASSWORD|API_KEY|TOTP|LICEN[CS]E",
                        re.I)
COPY_SKIP = {"__pycache__", "venv", ".venv", "build", "dist", "node_modules", ".pytest_cache", "mutants"}


# ── helpers (tested by test_mutmut_run.py) ────────────────────────────────────
def area_of(mutant_name):
    for name, rx in AREAS:
        if re.search(rx, mutant_name):
            return name
    return "other"


def status_of(code):
    if code is None:
        return "not checked"
    return STATUS.get(code, "suspicious")


def suite_kind(path):
    """pytest or script, by CI's rule (scripts/ci/run_ci_suites.py): a file that imports pytest,
    or defines test_ functions with no runner of its own (or a pytest one), runs under pytest."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None
    has_main = has_tests = imports_pytest = main_pytest = False
    for n in tree.body:
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
        imports_pytest |= any(x.split(".")[0] == "pytest" for x in mods)
        has_tests |= isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        if isinstance(n, ast.If) and "__main__" in ast.dump(n.test):
            has_main, main_pytest = True, "'pytest'" in ast.dump(n)
    return "pytest" if imports_pytest or (has_tests and (not has_main or main_pytest)) else "script"


def unsafe_work_dir(work, home=None):
    """Why --work must not be used, or '' if it is fine."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    w = os.path.realpath(work)
    if w == ROOT or w.startswith(ROOT + os.sep):
        return "inside the repo"
    for live in (os.path.join(home, ".scalp-app"), os.path.join(home, "Library", "Application Support")):
        if w == live or w.startswith(live + os.sep):
            return f"inside the live app folder {live}"
    if w in (home, os.sep):
        return "the home folder itself"
    return ""


def clean_env(work, base=None):
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not SECRET_ENV.search(k) and not k.upper().endswith("_PROXY") and k != "PYTHONPATH"}
    env.update(HOME=os.path.join(work, "home"), SCALP_TEST_MODE="1", PYTHONDONTWRITEBYTECODE="1",
               http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9",
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="",
               OBJC_DISABLE_INITIALIZE_FORK_SAFETY="YES", TZ=env.get("TZ", "Asia/Kolkata"))
    return env


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def suites_for(mod, backend=None):
    """Named suites + every backend suite that mentions the module's reach words (sorted, unique)."""
    backend = backend or BACKEND
    spec = MODULES[mod]
    rx = re.compile(spec.get("reach") or re.escape(os.path.basename(spec["file"])[:-3]))
    found = set(spec["suites"])
    for d, dirs, files in os.walk(os.path.join(backend, "app")):
        dirs[:] = sorted(x for x in dirs if x not in COPY_SKIP)
        for fn in files:
            if not (fn.startswith("test_") and fn.endswith(".py")) or ".bak-" in fn:
                continue
            rel = os.path.relpath(os.path.join(d, fn), backend).replace(os.sep, "/")
            if rel.startswith(REACH_SKIP) or rel in found:
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    if rx.search(f.read()):
                        found.add(rel)
            except OSError:
                pass
    bad = ci_lists()
    return sorted(r for r in found if os.path.exists(os.path.join(backend, r)) and r not in bad)


def inputs_sha(src, suites):
    """The module and its suites: results stand while these bytes are the same (HEAD may move)."""
    h = hashlib.sha256(sha(src).encode())
    for rel in suites:
        p = os.path.join(BACKEND, rel)
        h.update((rel + ":" + (sha(p) if os.path.exists(p) else "-")).encode())
    return h.hexdigest()


def git_head():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=20).stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def read_results(ws, rel):
    """{mutant_name: exit_code} from mutmut's .meta for one source file."""
    meta = os.path.join(ws, "mutants", rel + ".meta")
    try:
        with open(meta, encoding="utf-8") as f:
            return dict(json.load(f).get("exit_code_by_key") or {})
    except (OSError, ValueError):
        return {}


def tally(results, by_area=False):
    rows = {}
    for name, code in results.items():
        key = area_of(name) if by_area else "all"
        r = rows.setdefault(key, {"mutants": 0, "killed": 0, "survived": 0, "timeout": 0, "no tests": 0,
                                  "suspicious": 0, "not checked": 0})
        r["mutants"] += 1
        st = status_of(code)
        r[st if st in r else "suspicious"] += 1
    for r in rows.values():
        tested = r["killed"] + r["survived"] + r["timeout"]
        r["score"] = round(100.0 * (r["killed"] + r["timeout"]) / tested, 1) if tested else None
    return rows


def table(state, work):
    head = ["module", "area", "mutants", "killed", "survived", "timeout", "no tests", "suspicious",
            "not checked", "score %", "minutes", "state"]
    out = [head]
    for mod in ORDER:
        ws = os.path.join(work, mod)
        res = read_results(ws, MODULES[mod]["file"])
        st = state.get(mod, {})
        if not res:
            out.append([mod, "-", "-", "", "", "", "", "", "", "", "", st.get("status", "not run")])
            continue
        mins = f"{st.get('seconds', 0) / 60:.0f}"
        groups = [("all", tally(res)["all"])]
        if mod == "broker_truth":
            groups += sorted(tally(res, by_area=True).items())
        for area, r in groups:
            out.append([mod if area == "all" else "", area, r["mutants"], r["killed"], r["survived"],
                        r["timeout"], r["no tests"], r["suspicious"], r["not checked"],
                        "-" if r["score"] is None else r["score"],
                        mins if area == "all" else "", st.get("status", "") if area == "all" else ""])
    w = [max(len(str(r[i])) for r in out) for i in range(len(head))]
    lines = ["| " + " | ".join(str(c).ljust(w[i]) for i, c in enumerate(r)) + " |" for r in out]
    lines.insert(1, "|" + "|".join("-" * (x + 2) for x in w) + "|")
    return "\n".join(lines)


# ── workspace ────────────────────────────────────────────────────────────────
def mutmut_bin(work):
    b = os.path.join(work, ".venv", "bin", "mutmut")
    return b if os.path.exists(b) else None


def base_python(explicit=None):
    """The Python the mutation venv is built from: --python, else the app's own backend venv
    (same version as the app, 3.10 on the build Mac), else this one. mutmut 3 needs 3.10+."""
    if explicit:
        return explicit
    for c in (os.path.join(BACKEND, "venv", "bin", "python"), os.path.join(BACKEND, ".venv", "bin", "python")):
        if os.path.exists(c):
            return os.path.realpath(c)
    return sys.executable


def setup(work, python=None):
    venv = os.path.join(work, ".venv")
    if not os.path.exists(os.path.join(venv, "bin", "python")):
        py0 = base_python(python)
        v = subprocess.run([py0, "-c", "import sys;print(sys.version_info >= (3, 10))"], capture_output=True,
                           text=True).stdout.strip()
        if v != "True":
            raise SystemExit(f"{py0} is older than Python 3.10 (mutmut 3 needs 3.10+) — pass --python PATH")
        subprocess.run([py0, "-m", "venv", venv], check=True)
    py = os.path.join(venv, "bin", "python")
    subprocess.run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([py, "-m", "pip", "install", "-q", "-r", os.path.join(BACKEND, "requirements.txt"),
                    MUTMUT_PIN, PYTEST_PIN], check=True)
    print(f"ready: {venv} ({MUTMUT_PIN}, {PYTEST_PIN})")


def _ignore(d, names):
    return [n for n in names if n in COPY_SKIP or ".bak-" in n or n.endswith((".pyc", ".db", ".sqlite"))]


def build_workspace(work, mod, fresh=False):
    """Copy backend/ into <work>/<mod>/ and write the mutmut config + suite shim. Returns (ws, note)."""
    spec = MODULES[mod]
    ws = os.path.join(work, mod)
    src = os.path.join(BACKEND, spec["file"])
    suites_all = suites_for(mod)
    stamp = {"sha": inputs_sha(src, suites_all), "head": git_head(), "fence": FENCE}
    stamp_f = os.path.join(ws, ".scalp_mut.json")
    old = {}
    if os.path.exists(stamp_f):
        with open(stamp_f, encoding="utf-8") as f:
            old = json.load(f)
    note = ""
    if os.path.isdir(ws) and (fresh or old.get("sha") != stamp["sha"]):
        note = "fresh" if fresh else "source changed: started again"
        shutil.rmtree(ws)
    if not os.path.isdir(ws):
        shutil.copytree(BACKEND, ws, ignore=_ignore)
        os.makedirs(os.path.join(ws, "tests"), exist_ok=True)
        shutil.copy2(os.path.join(HERE, "mut_shim_suites.py"), os.path.join(ws, "tests", "test_mut_suites.py"))
        shutil.copy2(os.path.join(HERE, "mut_shim_conftest.py"), os.path.join(ws, "tests", "conftest.py"))
        suites, dropped = [], {}
        for rel in suites_all:
            kind = suite_kind(os.path.join(ws, rel))
            if not kind:
                continue
            t0 = time.time()
            why = preflight(work, ws, rel, kind)
            if why:
                dropped[rel] = why
            else:
                suites.append({"path": rel, "kind": kind, "secs": round(time.time() - t0, 2)})
        aliases = {os.path.basename(m["file"])[:-3]: m["file"][:-3].replace("/", ".") for m in MODULES.values()}
        with open(os.path.join(ws, "tests", "suites.json"), "w", encoding="utf-8") as f:
            json.dump({"module": mod, "suites": suites, "dropped": dropped, "aliases": aliases}, f, indent=1)
        top = sorted(n for n in os.listdir(ws) if n not in ("app", "tests", "home") and not n.startswith("."))
        with open(os.path.join(ws, "pyproject.toml"), "w", encoding="utf-8") as f:
            f.write(f"# {FENCE} — generated by scripts/mutation/mutmut_run.py\n[tool.mutmut]\n"
                    f'source_paths = ["app/"]\nonly_mutate = ["{spec["file"]}"]\n'
                    f'also_copy = {json.dumps(top)}\n'
                    'pytest_add_cli_args_test_selection = ["tests/"]\n'
                    'pytest_add_cli_args = ["-p", "no:cacheprovider", "-p", "no:randomly"]\n'
                    "timeout_multiplier = 3.0\ntimeout_constant = 30.0\n")
        with open(stamp_f, "w", encoding="utf-8") as f:
            json.dump(stamp, f)
    os.makedirs(os.path.join(ws, "home"), exist_ok=True)
    return ws, note


def preflight(work, ws, rel, kind, timeout=600):
    """'' if the suite passes on the unmutated copy, else why it is left out (mutmut needs a green start)."""
    py = os.path.join(work, ".venv", "bin", "python")
    path = os.path.join(ws, rel)
    env = clean_env(work)
    env["PYTHONPATH"] = ws
    if kind == "pytest":
        cmd, cwd = [py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", os.path.basename(path)], \
            os.path.dirname(path)
        env["PYTHONPATH"] = ws + os.pathsep + "."
    else:
        cmd, cwd = [py, path], ws
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"preflight timeout {timeout}s"
    return "" if r.returncode == 0 else f"fails unmutated (exit {r.returncode})"


def write_survivors(ws, mod, mm, env):
    rel = MODULES[mod]["file"]
    res = read_results(ws, rel)
    names = sorted(n for n, c in res.items() if status_of(c) == "survived")
    out = []
    for n in names:
        try:
            diff = subprocess.run([mm, "show", n], cwd=ws, env=env, capture_output=True, text=True,
                                  timeout=120).stdout
        except subprocess.SubprocessError:
            diff = ""
        out.append(f"## {n}  [{area_of(n) if mod == 'broker_truth' else mod}]  {survivor_line(ws, rel, diff)}"
                   f"\n{diff.strip()}\n")
    with open(os.path.join(ws, "survivors.txt"), "w", encoding="utf-8") as f:
        f.write(f"# {len(names)} survivors in {rel}\n\n" + "\n".join(out))


def survivor_line(ws, rel, diff):
    """file:line of the first changed line of a `mutmut show` diff: the hunk's unchanged context
    plus its removed lines are found in the original source (a line that repeats, like the
    three DriveResult returns, lands on the right copy)."""
    old, first = [], None
    in_hunk = False
    for ln in diff.splitlines():
        if ln.startswith("@@"):
            if in_hunk:
                break
            in_hunk = True
            continue
        if not in_hunk or ln.startswith("+"):
            continue
        if ln.startswith("-") and first is None:
            first = len(old)
        old.append(ln[1:] if ln[:1] in " -" else ln)
    if first is None:
        return rel
    try:
        with open(os.path.join(ws, rel), encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return rel
    at = src.find("\n".join(old))
    if at < 0:
        at = src.find(old[first])
        return f"{rel}:{src[:at].count(chr(10)) + 1}" if at >= 0 else rel
    return f"{rel}:{src[:at].count(chr(10)) + 1 + first}"


def run_module(work, mod, jobs, budget_min, fresh, state):
    mm = mutmut_bin(work)
    ws, note = build_workspace(work, mod, fresh)
    env = clean_env(work)
    st = state.setdefault(mod, {})
    if note:
        st.clear()
    st["status"] = "running"
    save_state(work, state)
    print(f"{mod}: running ({note or 'resume'}) — log {os.path.join(ws, 'mutmut.log')}", flush=True)
    t0 = time.time()
    with open(os.path.join(ws, "mutmut.log"), "a", encoding="utf-8") as log:
        log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} {FENCE} run -j {jobs}\n")
        log.flush()
        p = subprocess.Popen([mm, "run", "--max-children", str(jobs)], cwd=ws, env=env, stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            p.wait(timeout=budget_min * 60 if budget_min else None)
            st["status"] = "done" if p.returncode == 0 else f"mutmut exit {p.returncode} (see log)"
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGINT)
            try:
                p.wait(timeout=120)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            st["status"] = "paused (budget) — rerun to resume"
        except KeyboardInterrupt:
            os.killpg(p.pid, signal.SIGINT)
            p.wait()
            st["status"] = "paused (Ctrl-C) — rerun to resume"
            st["seconds"] = st.get("seconds", 0) + (time.time() - t0)
            save_state(work, state)
            raise
    st["seconds"] = st.get("seconds", 0) + (time.time() - t0)
    write_survivors(ws, mod, mm, env)
    save_state(work, state)


def load_state(work):
    try:
        with open(os.path.join(work, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(work, state):
    tmp = os.path.join(work, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, os.path.join(work, "state.json"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="mutmut 3 on the safety-critical modules (TD-043)")
    ap.add_argument("modules", nargs="*", help=f"default: all ({', '.join(ORDER)})")
    ap.add_argument("--work", default=os.path.expanduser("~/scalp-mutation"))
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--budget-min", type=float, default=0)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--python", help="with --setup: the Python to build the venv from (3.10+)")
    a = ap.parse_args(argv)
    bad = [m for m in a.modules if m not in MODULES]
    if bad:
        print(f"unknown module(s): {', '.join(bad)} — choose from {', '.join(ORDER)}", file=sys.stderr)
        return 2
    if a.list:
        for m in ORDER:
            print(f"{m}: backend/{MODULES[m]['file']}\n    " + "\n    ".join(suites_for(m)))
        return 0
    work = os.path.abspath(os.path.expanduser(a.work))
    why = unsafe_work_dir(work)
    if why:
        print(f"refusing --work {work}: {why}", file=sys.stderr)
        return 2
    os.makedirs(work, exist_ok=True)
    if a.setup:
        setup(work, a.python)
        return 0
    state = load_state(work)
    if not a.summary:
        if not mutmut_bin(work):
            print(f"no mutmut in {work}/.venv — run once (online): python3 {sys.argv[0]} --setup",
                  file=sys.stderr)
            return 2
        if platform.system() == "Darwin" and shutil.which("caffeinate") and not os.environ.get("MUT_CAFFEINATED"):
            os.environ["MUT_CAFFEINATED"] = "1"            # keep the Mac awake for the run
            os.execvp("caffeinate", ["caffeinate", "-i", sys.executable, os.path.abspath(__file__)]
                      + (argv if argv is not None else sys.argv[1:]))
        for mod in a.modules or ORDER:
            try:
                run_module(work, mod, a.j, a.budget_min, a.fresh, state)
            except KeyboardInterrupt:
                break
    t = table(state, work)
    with open(os.path.join(work, "SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write(f"# Mutation summary ({FENCE}) {time.strftime('%Y-%m-%d %H:%M')} — repo {git_head()[:10]}\n\n"
                + t + "\n")
    print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
