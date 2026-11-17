#!/usr/bin/env python3
# scripts/test_repo_map_hook.py  — ── REPO_MAP_HOOK_20261117 ── (TD-038) TC-REPOMAP-0024…0031
#
# Drives .githooks/pre-commit in throw-away git repos with a stub generator
# (the real generator is covered by test_repo_map.py). Never touches this
# repo's index or history.
#   python3 scripts/test_repo_map_hook.py      (stdlib only; exit 1 on any failure)
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".githooks", "pre-commit")
F = 0
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

STUB = r'''import os, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mp = os.path.join(root, "REPO_MAP.md")
mode = open(os.path.join(root, "mode")).read().strip() if os.path.exists(os.path.join(root, "mode")) else "ok"
if mode == "crash":
    sys.exit(2)
want = "fresh " + open(os.path.join(root, "src.txt")).read()
if "--check" in sys.argv:
    sys.exit(0 if os.path.exists(mp) and open(mp).read() == want else 1)
open(mp, "w").write(want)
'''


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


def git(d, *a):
    r = subprocess.run(["git", *a], cwd=d, env=ENV, capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr)


def repo(with_gen=True):
    d = tempfile.mkdtemp(prefix="rmhook_")
    git(d, "init", "-q")
    os.makedirs(os.path.join(d, ".githooks"))
    shutil.copy2(HOOK, os.path.join(d, ".githooks", "pre-commit"))
    os.chmod(os.path.join(d, ".githooks", "pre-commit"), 0o755)
    git(d, "config", "core.hooksPath", ".githooks")
    if with_gen:
        os.makedirs(os.path.join(d, "scripts"))
        open(os.path.join(d, "scripts", "gen_repo_map.py"), "w").write(STUB)
    open(os.path.join(d, "src.txt"), "w").write("v1\n")
    open(os.path.join(d, "REPO_MAP.md"), "w").write("fresh v1\n")
    git(d, "add", "-A")
    git(d, "commit", "-q", "--no-verify", "-m", "base")
    return d


def edit_and_commit(d, text="v2\n"):
    open(os.path.join(d, "src.txt"), "w").write(text)
    git(d, "add", "src.txt")
    return git(d, "commit", "-m", "edit")


# 0024 the hook in the repo is sound
src = open(HOOK, encoding="utf-8").read() if os.path.exists(HOOK) else ""
mode_ok = os.path.exists(HOOK) and bool(os.stat(HOOK).st_mode & stat.S_IXUSR)
syn = subprocess.run(["sh", "-n", HOOK], capture_output=True, text=True) if os.path.exists(HOOK) else None
chk("TC-REPOMAP-0024", ".githooks/pre-commit exists, is executable, carries its fence and parses as sh",
    mode_ok and "REPO_MAP_HOOK_20261117" in src and syn is not None and syn.returncode == 0,
    (mode_ok, syn and syn.stderr))

# 0025 stale map -> refreshed and inside the same commit
d = repo()
rc, out = edit_and_commit(d)
_, shown = git(d, "show", "HEAD:REPO_MAP.md")
_, st = git(d, "status", "--porcelain")
chk("TC-REPOMAP-0025", "a commit that makes the map stale gets the refreshed REPO_MAP.md in the same commit",
    rc == 0 and shown == "fresh v2\n" and "refreshed" in out and st.strip() == "", (rc, out, shown, st))

# 0026 current map -> commit untouched, hook silent
rc, out = git(d, "commit", "--allow-empty", "-m", "noop")
_, files = git(d, "show", "--name-only", "--format=", "HEAD")
chk("TC-REPOMAP-0026", "when the map is current the hook adds nothing and prints nothing",
    rc == 0 and "repo-map" not in out and files.strip() == "", (rc, out, files))
shutil.rmtree(d, ignore_errors=True)

# 0027 generator fails -> warning, commit still made
d = repo()
open(os.path.join(d, "mode"), "w").write("crash\n")
git(d, "add", "mode")
rc, out = edit_and_commit(d)
_, log = git(d, "log", "--oneline")
chk("TC-REPOMAP-0027", "a failing generator prints a warning and does not block the commit",
    rc == 0 and "WARNING generator failed" in out and "edit" in log, (rc, out))
shutil.rmtree(d, ignore_errors=True)

# 0028 an existing .git/hooks/pre-commit still runs; its 'no' stands
d = repo()
old = os.path.join(d, ".git", "hooks", "pre-commit")
open(old, "w").write("#!/bin/sh\necho old-hook-ran\nexit 1\n")
os.chmod(old, 0o755)
rc, out = edit_and_commit(d)
_, log = git(d, "log", "--oneline")
blocked = rc != 0 and "old-hook-ran" in out and "edit" not in log
open(old, "w").write("#!/bin/sh\necho old-hook-ran\nexit 0\n")
rc2, out2 = git(d, "commit", "-m", "edit")
_, shown = git(d, "show", "HEAD:REPO_MAP.md")
chk("TC-REPOMAP-0028", "a pre-existing .git/hooks/pre-commit runs first: its failure blocks, its pass lets the map refresh",
    blocked and rc2 == 0 and "old-hook-ran" in out2 and shown == "fresh v2\n", (rc, out, rc2, out2))
shutil.rmtree(d, ignore_errors=True)

# 0029 a clone without the generator -> silent no-op
d = repo(with_gen=False)
rc, out = edit_and_commit(d)
chk("TC-REPOMAP-0029", "with no scripts/gen_repo_map.py (old branch) the hook is a silent no-op",
    rc == 0 and "repo-map" not in out, (rc, out))
shutil.rmtree(d, ignore_errors=True)

# 0030 git commit --no-verify skips it (documented escape hatch)
d = repo()
open(os.path.join(d, "src.txt"), "w").write("v3\n")
git(d, "add", "src.txt")
rc, out = git(d, "commit", "--no-verify", "-m", "skip")
_, shown = git(d, "show", "HEAD:REPO_MAP.md")
chk("TC-REPOMAP-0030", "git commit --no-verify skips the hook (map left as it was)",
    rc == 0 and shown == "fresh v1\n", (rc, shown))
shutil.rmtree(d, ignore_errors=True)

# 0031 this clone has the hook switched on
rc, hp = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=ROOT, capture_output=True,
                        text=True).returncode, subprocess.run(["git", "config", "--get", "core.hooksPath"],
                                                             cwd=ROOT, capture_output=True, text=True).stdout.strip()
chk("TC-REPOMAP-0031", "this clone has core.hooksPath = .githooks (else run: git config core.hooksPath .githooks)",
    hp == ".githooks", hp or "(not set)")

print("\n" + ("ALL REPO_MAP HOOK CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
