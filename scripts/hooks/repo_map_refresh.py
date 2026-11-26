#!/usr/bin/env python3
# scripts/hooks/repo_map_refresh.py  — ── PRECOMMIT_20261117 ── (TD-038/TD-042)
# ── HOOK_NO_ADD_20261126 ── (DEF-060, TD-095) rewrite only, never `git add`
#
# pre-commit hook: if REPO_MAP.md is stale, regenerate it in the working tree and
# stop the commit with "git add REPO_MAP.md and commit again". It never stages the
# file itself: pre-commit stashes unstaged edits before the hooks run, and a hook
# that stages a file the stash also holds makes the stash restore fail
# ('patch does not apply') with those edits left only in ~/.cache/pre-commit.
# Left unstaged, pre-commit rolls the rewrite back on such a conflict and gives
# the edits back. A failing generator still only warns (exit 0).
# (Replaces .githooks/pre-commit from REPO_MAP_HOOK_20261117.)
import os
import subprocess
import sys


def main():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    root = root or os.getcwd()
    gen = os.path.join(root, "scripts", "gen_repo_map.py")
    if not os.path.exists(gen):
        return 0
    rc = subprocess.run([sys.executable, gen, "--check"], cwd=root, capture_output=True).returncode
    if rc == 0:
        return 0
    if rc == 1:
        r = subprocess.run([sys.executable, gen], cwd=root, capture_output=True)
        if r.returncode == 0:
            print("REPO_MAP.md was stale — refreshed. Stage it and commit again:\n"
                  "  git add REPO_MAP.md && git commit ...\n"
                  "(If REPO_MAP.md had unstaged edits, pre-commit kept yours instead: "
                  "run python3 scripts/gen_repo_map.py, then git add REPO_MAP.md.)")
            return 1
        print("WARNING: could not refresh REPO_MAP.md — run: python3 scripts/gen_repo_map.py")
        return 0
    print("WARNING: scripts/gen_repo_map.py failed (commit not blocked) — run it to see why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
