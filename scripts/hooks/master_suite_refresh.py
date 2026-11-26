#!/usr/bin/env python3
# scripts/hooks/master_suite_refresh.py — ── MASTER_SUITE_20261122 ── (TD-079)
# ── HOOK_NO_ADD_20261126 ── (DEF-060, TD-095) rewrite only, never `git add`
#
# pre-commit hook: if scripts/regression/MASTER_SUITE.md no longer matches the
# test suites in the tree (a test added, changed or deleted), rewrite it in the
# working tree and stop the commit with "git add it and commit again". It never
# stages the file itself (same reason as repo_map_refresh.py: a staged rewrite of
# a file with stashed unstaged edits breaks pre-commit's stash restore).
# A failing tool still only warns (exit 0).
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
        if r.returncode == 0:
            print("MASTER_SUITE.md was behind the tests — refreshed. Stage it and commit again:\n"
                  f"  git add {REL} && git commit ...\n"
                  "(If it had unstaged edits, pre-commit kept yours instead: "
                  f"run python3 scripts/regression/master_suite.py refresh, then git add {REL}.)")
            return 1
        print("WARNING: could not refresh MASTER_SUITE.md — run: python3 scripts/regression/master_suite.py refresh")
        return 0
    print("WARNING: master_suite.py check failed (commit not blocked) — run it to see why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
