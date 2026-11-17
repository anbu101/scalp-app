#!/usr/bin/env python3
# scripts/hooks/check_dual_tree.py  — ── PRECOMMIT_20261117 ── (TD-042)
#
# pre-commit hook: the source trees and their build copies must match.
#   backend/app/<x>  ==  desktop/src-tauri/backend/app/<x>
#   frontend/<x>     ==  desktop/src-tauri/frontend/<x>   (not node_modules/, build/)
# The copies are git-ignored; ./desktop/build-scalp.sh re-syncs them, apply
# scripts write both. A copy that is not on this machine (fresh clone, CI) is
# skipped. Stdlib only.
#
#   (hook)   check_dual_tree.py [staged paths]   — those paths + staged deletions
#   (manual) python3 scripts/hooks/check_dual_tree.py --full   — whole trees, both ways
#   --root PATH  run against another checkout
# Exit 0 = in step (or nothing to compare), 1 = differences listed.
import os
import subprocess
import sys

PAIRS = [("backend/app/", "desktop/src-tauri/backend/app/"),
         ("frontend/", "desktop/src-tauri/frontend/")]
SKIP_PARTS = {"node_modules", "build", "__pycache__", ".git"}
SKIP_SUFFIX = (".pyc", ".log")


def skipped(rel):
    parts = rel.split("/")
    base = parts[-1]
    return (any(p in SKIP_PARTS for p in parts[:-1]) or base.endswith(SKIP_SUFFIX) or ".bak-" in base
            or ".tmp-" in base or base in (".DS_Store", ".env"))


def pair_for(rel):
    for src, dst in PAIRS:
        if rel.startswith(src):
            return src, dst
    return None


def same(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as f, open(b, "rb") as g:
            while True:
                x, y = f.read(1 << 16), g.read(1 << 16)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def staged_deletions(root):
    try:
        r = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=D", "-z"], cwd=root,
                           capture_output=True, timeout=30)
        return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def walk(root, top):
    base = os.path.join(root, top)
    out = set()
    for d, dirs, fs in os.walk(base):
        dirs[:] = [x for x in dirs if x not in SKIP_PARTS]
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")
            if not skipped(top + rel):
                out.add(rel)
    return out


def main(argv):
    root = os.path.abspath(argv[argv.index("--root") + 1]) if "--root" in argv else None
    if root is None:
        try:
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                                  timeout=30).stdout.strip() or os.getcwd()
        except (OSError, subprocess.SubprocessError):
            root = os.getcwd()
    full = "--full" in argv
    paths = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--root")]
    present = {src: os.path.isdir(os.path.join(root, dst)) for src, dst in PAIRS}
    probs, notes = [], []
    for src, dst in PAIRS:
        if not present[src]:
            notes.append(f"no build copy at {dst} on this machine — {src} not compared")
    if full:
        for src, dst in PAIRS:
            if not present[src]:
                continue
            a, b = walk(root, src), walk(root, dst)
            probs += [(src + r, "differs from the build copy") for r in sorted(a & b)
                      if not same(os.path.join(root, src, r), os.path.join(root, dst, r))]
            probs += [(src + r, "missing in the build copy") for r in sorted(a - b)]
            probs += [(dst + r, "in the build copy only (deleted from the source?)") for r in sorted(b - a)]
    else:
        for p in paths:
            p = p.replace("\\", "/")
            pr = pair_for(p)
            if not pr or not present[pr[0]] or skipped(p):
                continue
            s, c = os.path.join(root, p), os.path.join(root, pr[1] + p[len(pr[0]):])
            if not os.path.exists(s):
                continue
            if not os.path.exists(c):
                probs.append((p, "missing in the build copy"))
            elif not same(s, c):
                probs.append((p, "differs from the build copy"))
        for p in staged_deletions(root):
            pr = pair_for(p)
            if pr and present[pr[0]] and not skipped(p) and os.path.exists(os.path.join(root, pr[1] + p[len(pr[0]):])):
                probs.append((pr[1] + p[len(pr[0]):], "still in the build copy (deleted from the source)"))
    if probs:
        print(f"Dual tree out of step — {len(probs)} path(s):")
        for p, w in probs:
            print(f"  {p}  — {w}")
        print("Fix: ./desktop/build-scalp.sh backend|frontend|both (re-syncs the copies), "
              "or skip once: SKIP=dual-tree-parity git commit ...")
        return 1
    if full:
        print("dual tree in step" + ("" if not notes else " (" + "; ".join(notes) + ")"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
