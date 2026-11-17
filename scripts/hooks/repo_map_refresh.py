#!/usr/bin/env python3
# scripts/hooks/repo_map_refresh.py  — ── PRECOMMIT_20261117 ── (TD-038/TD-042)
#
# pre-commit hook: if REPO_MAP.md is stale, regenerate it and add it to this
# commit. Never blocks a commit on its own account (a warning only).
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
        a = subprocess.run(["git", "add", "REPO_MAP.md"], cwd=root, capture_output=True) if r.returncode == 0 else r
        if r.returncode == 0 and a.returncode == 0:
            print("REPO_MAP.md was stale — refreshed and added to this commit")
        else:
            print("WARNING: could not refresh REPO_MAP.md — run: python3 scripts/gen_repo_map.py")
        return 0
    print("WARNING: scripts/gen_repo_map.py failed (commit not blocked) — run it to see why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
