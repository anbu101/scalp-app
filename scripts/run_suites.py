#!/usr/bin/env python3
# scripts/run_suites.py  — ── REPO_MAP_20261117 ── (TD-038)
#                          ── REPO_MAP_FIX_20261117 ── each suite runs the way it is meant to run
#                          ── ISOLATED_HOME_20261122 ── (DEF-094) every suite gets a throw-away HOME
#
# Runs the repo's test suites and prints ONLY the failures (last 25 lines
# each) and one summary line. Stdlib only (pytest itself is needed for the
# suites that use it).
#
#   python3 scripts/run_suites.py PATH [PATH ...]
#       PATH = a test_*.py file or a folder (searched recursively).
#       Short prefixes from REPO_MAP.md work: E/ B/ A/ J/ (backend/app/...), F/.
#   --timeout SECONDS   per suite (default 900)
#   --quiet             summary line only (still exits 1 on a failure)
#
# How a suite runs (mode_of; scripts/ci/run_ci_suites.py's copy wins when it exists):
#   script  python3 <file>, cwd=backend, PYTHONPATH=backend, SCALP_TEST_MODE=1 (most suites)
#   pytest  python3 -m pytest -q <file> from the file's own folder, PYTHONPATH=backend:.
#           (files that import pytest, or define test_ functions with no runner of their own)
#   root    python3 <file> from the repo root ("Run from repo root" suites, e.g. BRK)
# Every suite runs with HOME (and USERPROFILE) pointed at an empty throw-away folder, so
# nothing a test saves can reach the real ~/.scalp-app (DEF-094: suites wrote fake LIVE ORB
# trades into app.db and replaced the Zerodha / Telegram credentials). pip --user packages
# and Playwright's browsers stay reachable; the folder is deleted when the run ends.
# frontend/uitest/** : cwd=frontend. Other paths (scripts/**): cwd=repo root. *.mjs: node.
# "not runnable here" = a third-party package this Python lacks (pytest, fastapi, …);
# a module that exists in this repo but cannot be imported is a FAILURE, not an env gap.
# Exit 0 = all passed, 1 = a failure, 2 = bad arguments.
import ast
import importlib.util
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PFX = {"E/": "backend/app/engine/", "B/": "backend/app/backtest/", "A/": "backend/app/api/",
       "J/": "backend/app/jobs/", "F/": "frontend/src/"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".claude"}

# Suites whose way of running cannot be read from the file (kept in step with
# scripts/ci/run_ci_suites.py OVERRIDE, which takes precedence when present).
OVERRIDE = {
    "backend/app/engine/ic/test_ic_iv_sl.py": {"path": ["backend/app/backtest/ic"]},
}


def _ci_mode_of():
    p = os.path.join(ROOT, "scripts", "ci", "run_ci_suites.py")
    if not os.path.exists(p):
        return None
    try:
        spec = importlib.util.spec_from_file_location("_run_ci_suites", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.mode_of
    except Exception:
        return None


def local_mode_of(rel):
    """{'runner': script|pytest, 'cwd': backend|root|dir, 'path': [...]} for a repo-relative path."""
    m = {"runner": "script", "cwd": "backend", "path": []}
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        tree, src = None, ""
    if tree is not None:
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
        elif re.search(r"(?im)^#\s*Run from repo root", src):
            m["cwd"] = "root"
    if not rel.startswith("backend/") and m["runner"] == "script":
        m["cwd"] = "root"
    m.update(OVERRIDE.get(rel, {}))
    return m


_CI = _ci_mode_of()


def mode_of(rel):
    rel = rel.replace(os.sep, "/")
    if _CI is not None and rel.startswith("backend/"):
        return _CI(rel)
    return local_mode_of(rel)


def expand(arg):
    for a, b in PFX.items():
        if arg.startswith(a):
            arg = b + arg[len(a):]
    p = arg if os.path.isabs(arg) else os.path.join(ROOT, arg)
    if os.path.isfile(p):
        return [os.path.abspath(p)]
    if os.path.isdir(p):
        out = []
        for d, dirs, fs in os.walk(p):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            out += [os.path.join(d, f) for f in sorted(fs)
                    if f.startswith("test_") and f.endswith(".py") and ".bak-" not in f]
        return out
    return None


def python_for_backend():
    b = os.path.join(ROOT, "backend")
    for c in (os.path.join(b, "venv", "bin", "python"), os.path.join(b, ".venv", "bin", "python"),
              os.path.join(ROOT, "venv", "bin", "python"), os.path.join(ROOT, ".venv", "bin", "python")):
        if os.path.exists(c):
            return c
    return sys.executable


# Env vars the app reads its folders from (app/utils/app_paths.py): a suite must not inherit real ones.
APP_PATH_VARS = ("SCALP_APP_HOME", "DB_PATH", "SCALP_DATA_DIR", "SCALP_LOG_DIR", "SCALP_STATE_DIR",
                 "SCALP_CONFIG_DIR", "SCALP_ZERODHA_DIR")


def isolate_home(env, home):
    """── ISOLATED_HOME_20261122 ── (DEF-094) point the suite's home at `home`, never the real one."""
    os.makedirs(home, exist_ok=True)
    try:
        ub = site.getuserbase()                      # read with the REAL home, before it is swapped
    except Exception:
        ub = None
    pw = os.path.join(os.path.expanduser("~"), "Library", "Caches", "ms-playwright")
    for k in APP_PATH_VARS:
        env.pop(k, None)
    env["HOME"] = home
    env["USERPROFILE"] = home                        # Path.home() on Windows
    if ub:
        env.setdefault("PYTHONUSERBASE", ub)         # pip --user packages (pytest, jwt) stay importable
    if os.path.isdir(pw):
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", pw)
    return env


def plan(path, home=None):
    """(cmd, cwd, env) for one suite. env always has a throw-away HOME: `home`, or a new temp folder."""
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    env = isolate_home(dict(os.environ, SCALP_TEST_MODE="1"),
                       home or tempfile.mkdtemp(prefix="scalp-suite-home-"))
    backend = os.path.join(ROOT, "backend")
    if path.endswith(".mjs"):
        return ["node", path], ROOT, env
    if rel.startswith("frontend/"):
        env["PYTHONPATH"] = backend
        return [python_for_backend(), path], os.path.join(ROOT, "frontend"), env
    m = mode_of(rel)
    py = python_for_backend() if rel.startswith("backend/") else sys.executable
    pp = [backend] + [os.path.join(ROOT, x) for x in m["path"]] + (["."] if m["runner"] == "pytest" else [])
    env["PYTHONPATH"] = os.pathsep.join(pp)
    cwd = {"backend": backend, "root": ROOT, "dir": os.path.dirname(path)}[m["cwd"]]
    if m["runner"] == "pytest":
        return [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", os.path.basename(path)], cwd, env
    return [py, path], cwd, env


_LOCAL = None


def local_modules():
    """Names of every module / package folder under backend/ (bare-import targets)."""
    global _LOCAL
    if _LOCAL is None:
        _LOCAL = set()
        for d, dirs, fs in os.walk(os.path.join(ROOT, "backend")):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            _LOCAL.update(dirs)
            _LOCAL.update(f[:-3] for f in fs if f.endswith(".py"))
    return _LOCAL


def env_gap(out):
    """The missing module, if the failure is a third-party package this Python lacks."""
    for mm in re.finditer(r"No module named '([\w.]+)'", out):
        top = mm.group(1).split(".")[0]
        if top in ("app", "backend") or top in local_modules():
            return None                          # a repo module that would not import: a real failure
        return mm.group(1)
    return None


def main(argv):
    timeout, quiet, paths = 900, False, []
    it = iter(argv)
    for a in it:
        if a == "--timeout":
            timeout = int(next(it))
        elif a == "--quiet":
            quiet = True
        else:
            paths.append(a)
    if not paths:
        print("usage: python3 scripts/run_suites.py PATH [PATH ...]", file=sys.stderr)
        return 2
    suites = []
    for a in paths:
        got = expand(a)
        if got is None:
            print(f"no such file or folder: {a}", file=sys.stderr)
            return 2
        suites += [g for g in got if g not in suites]
    passed, failed, envmiss = 0, [], []
    homes = tempfile.mkdtemp(prefix="run_suites_home_")       # DEF-094: one throw-away HOME per suite
    try:
        return _run(suites, timeout, quiet, homes, passed, failed, envmiss)
    finally:
        shutil.rmtree(homes, ignore_errors=True)


def _run(suites, timeout, quiet, homes, passed, failed, envmiss):
    for i, s in enumerate(suites):
        rel = os.path.relpath(s, ROOT)
        cmd, cwd, env = plan(s, home=os.path.join(homes, str(i)))
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
        except subprocess.TimeoutExpired:
            out, rc = f"timeout after {timeout}s", -1
        except OSError as e:
            out, rc = f"could not start: {e}", -1
        if rc == 0:
            passed += 1
            continue
        gap = env_gap(out) if "ModuleNotFoundError" in out or "No module named" in out else None
        if gap:
            envmiss.append(rel)
            if not quiet:
                print(f"? {rel} — needs a package this Python lacks: {gap}")
            continue
        failed.append(rel)
        if not quiet:
            print(f"✗ {rel} (exit {rc})")
            print("\n".join("    " + l for l in out.rstrip().splitlines()[-25:]))
    print(f"{passed} passed, {len(failed)} failed, {len(envmiss)} not runnable here — {len(suites)} suites")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
