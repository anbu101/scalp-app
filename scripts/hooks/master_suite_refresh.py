#!/usr/bin/env python3
# scripts/hooks/master_suite_refresh.py — ── MASTER_SUITE_20261122 ── (TD-079)
#
# pre-commit hook: if scripts/regression/MASTER_SUITE.md no longer matches the
# test suites in the tree (a test added, changed or deleted), rewrite it and add
# it to this commit. Never blocks a commit on its own account (a warning only).
import os
import subprocess
import sys

REL = "scripts/regression/MASTER_SUITE.md"


def main():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    root = root or os.getcwd()
    tool = os.path.join(root, "scripts", "regression", "master_suite.py")
    if not os.path.exists(tool):
        return 0
    rc = subprocess.run([sys.executable, tool, "check"], cwd=root, capture_output=True).returncode
    if rc == 0:
        return 0
    if rc == 1:
        r = subprocess.run([sys.executable, tool, "refresh"], cwd=root, capture_output=True)
        a = subprocess.run(["git", "add", REL], cwd=root, capture_output=True) if r.returncode == 0 else r
        if r.returncode == 0 and a.returncode == 0:
            print("MASTER_SUITE.md was behind the tests — refreshed and added to this commit")
        else:
            print("WARNING: could not refresh MASTER_SUITE.md — run: python3 scripts/regression/master_suite.py refresh")
        return 0
    print("WARNING: master_suite.py check failed (commit not blocked) — run it to see why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
