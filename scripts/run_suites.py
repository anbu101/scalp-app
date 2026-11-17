#!/usr/bin/env python3
# scripts/run_suites.py  — ── REPO_MAP_20261117 ── (TD-038)
#
# Runs the repo's standalone test scripts and prints ONLY the failures (last
# 25 lines each) and one summary line. Stdlib only.
#
#   python3 scripts/run_suites.py PATH [PATH ...]
#       PATH = a test_*.py file or a folder (searched recursively).
#       Short prefixes from REPO_MAP.md work: E/ B/ A/ J/ (backend/app/...), F/.
#   --timeout SECONDS   per suite (default 900)
#   --quiet             summary line only (still exits 1 on a failure)
#
# backend/** suites: cwd=backend, PYTHONPATH=backend, SCALP_TEST_MODE=1, the
# backend venv's Python if there is one. frontend/uitest/** : cwd=frontend.
# Anything else (scripts/**): cwd=repo root. *.mjs runs with node.
# Exit 0 = all passed (env-missing suites are reported, not counted as failed),
# 1 = a failure, 2 = bad arguments.
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PFX = {"E/": "backend/app/engine/", "B/": "backend/app/backtest/", "A/": "backend/app/api/",
       "J/": "backend/app/jobs/", "F/": "frontend/src/"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".claude"}


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


def plan(path):
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    env = dict(os.environ, SCALP_TEST_MODE="1")
    if path.endswith(".mjs"):
        return ["node", path], ROOT, env
    if rel.startswith("backend/"):
        env["PYTHONPATH"] = os.path.join(ROOT, "backend")
        return [python_for_backend(), path], os.path.join(ROOT, "backend"), env
    if rel.startswith("frontend/"):
        env["PYTHONPATH"] = os.path.join(ROOT, "backend")
        return [python_for_backend(), path], os.path.join(ROOT, "frontend"), env
    return [sys.executable, path], ROOT, env


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
    for s in suites:
        rel = os.path.relpath(s, ROOT)
        cmd, cwd, env = plan(s)
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
        if "ModuleNotFoundError" in out and "No module named 'app" not in out:
            envmiss.append(rel)
            if not quiet:
                print(f"? {rel} — needs a package this Python lacks: "
                      + next((l for l in reversed(out.splitlines()) if "ModuleNotFoundError" in l), "").strip())
            continue
        failed.append(rel)
        if not quiet:
            print(f"✗ {rel} (exit {rc})")
            print("\n".join("    " + l for l in out.rstrip().splitlines()[-25:]))
    print(f"{passed} passed, {len(failed)} failed, {len(envmiss)} not runnable here — {len(suites)} suites")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
