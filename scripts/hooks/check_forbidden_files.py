#!/usr/bin/env python3
# scripts/hooks/check_forbidden_files.py  — ── PRECOMMIT_20261117 ── (TD-042)
#
# pre-commit hook: refuse apply-script backups and machine junk in a commit.
#   *.bak-<FENCE>     apply-script backups (they stay on disk, not in git)
#   *.staging-*       half-written staging copies
#   __pycache__/      bytecode
#   .DS_Store         macOS Finder files
# Gets the staged paths from pre-commit; prints every offending path.
# Stdlib only. Exit 0 = clean, 1 = something to unstage.
import os
import re
import sys

RULES = [
    (re.compile(r"\.bak-"), "apply-script backup (.bak-*)"),
    (re.compile(r"(^|\.)staging-"), "staging copy (.staging-*)"),
]


def why(path):
    parts = path.replace("\\", "/").split("/")
    base = parts[-1]
    if "__pycache__" in parts[:-1] or base == "__pycache__":
        return "bytecode (__pycache__)"
    if base == ".DS_Store":
        return "macOS .DS_Store"
    for rx, label in RULES:
        if rx.search(base):
            return label
    return None


def main(paths):
    bad = [(p, why(p)) for p in paths if why(p)]
    if not bad:
        return 0
    print(f"These {len(bad)} file(s) must not be committed:")
    for p, w in bad:
        print(f"  {p}  — {w}")
    print("Unstage them:  git restore --staged <path>   (they stay on disk; .gitignore keeps them out of `git add -A`)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
