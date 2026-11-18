#!/usr/bin/env python3
# scripts/test_precommit_hooks.py  — ── PRECOMMIT_20261117 ── (TD-042) TC-HOOKS-0001…0028, 0043
# PUSH_ANYTIME_20261118: market-hours push hook removed — TC-HOOKS-0016…0021 retired.
#
# Unit checks of the three local hooks and the config, in throw-away folders and
# git repos (never this repo's index). The pre-commit framework itself is not
# needed here.   python3 scripts/test_precommit_hooks.py   (stdlib; exit 1 on a failure)
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = os.path.join(ROOT, "scripts", "hooks")
F = 0
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


def run(script, *args, cwd=None, env=None):
    r = subprocess.run([sys.executable, os.path.join(H, script), *args], cwd=cwd or ROOT,
                       env=dict(ENV, **(env or {})), capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def git(d, *a):
    r = subprocess.run(["git", *a], cwd=d, env=ENV, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def put(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


# ── forbidden files ──────────────────────────────────────────────────
rc, out = run("check_forbidden_files.py", "backend/app/x.py", "frontend/src/App.jsx", "README.md")
chk("TC-HOOKS-0001", "ordinary files pass the backup-file check", rc == 0 and out == "", out)
rc, out = run("check_forbidden_files.py", "backend/app/x.py", "backend/app/api_server.py.bak-FOO_20261117")
chk("TC-HOOKS-0002", "a .bak-<FENCE> file is refused and named", rc == 1 and "api_server.py.bak-FOO_20261117" in out
    and "x.py" not in out.split("must not")[1].split("Unstage")[0], out)
rc, out = run("check_forbidden_files.py", "backend/app/x.py.staging-ABC")
chk("TC-HOOKS-0003", "a .staging-* file is refused", rc == 1 and "staging copy" in out, out)
rc, out = run("check_forbidden_files.py", "backend/app/__pycache__/x.cpython-310.pyc")
chk("TC-HOOKS-0004", "a file under __pycache__/ is refused", rc == 1 and "__pycache__" in out, out)
rc, out = run("check_forbidden_files.py", "frontend/src/.DS_Store", ".DS_Store")
chk("TC-HOOKS-0005", "a .DS_Store anywhere is refused (both listed)", rc == 1 and out.count(".DS_Store") >= 3, out)
rc, out = run("check_forbidden_files.py", "backup.py", "docs/staging_notes.md", "my.bak.py", "pycache_tools.py",
              "DS_Store.md", "config.json.bak")
chk("TC-HOOKS-0006", "look-alike names (backup.py, my.bak.py, staging_notes.md, DS_Store.md …) pass", rc == 0, out)

# ── dual tree ────────────────────────────────────────────────────────
d = tempfile.mkdtemp(prefix="dualtree_")
git(d, "init", "-q")
put(d, "backend/app/a.py", "A\n")
put(d, "frontend/src/App.jsx", "J\n")
rc, out = run("check_dual_tree.py", "--root", d, "backend/app/a.py", "frontend/src/App.jsx")
chk("TC-HOOKS-0007", "no build copies on the machine (fresh clone / CI): passes, compares nothing", rc == 0, out)
rc, out = run("check_dual_tree.py", "--root", d, "--full")
chk("TC-HOOKS-0008", "--full with no build copies says so and passes", rc == 0 and "no build copy" in out, out)
put(d, "desktop/src-tauri/backend/app/a.py", "A\n")
put(d, "desktop/src-tauri/frontend/src/App.jsx", "J\n")
rc, out = run("check_dual_tree.py", "--root", d, "backend/app/a.py", "frontend/src/App.jsx")
chk("TC-HOOKS-0009", "identical build copies pass", rc == 0 and out == "", out)
put(d, "desktop/src-tauri/backend/app/a.py", "A changed\n")
rc, out = run("check_dual_tree.py", "--root", d, "backend/app/a.py", "frontend/src/App.jsx")
chk("TC-HOOKS-0010", "a backend file that differs from its copy fails and is named",
    rc == 1 and "backend/app/a.py  — differs" in out and "App.jsx" not in out, out)
put(d, "desktop/src-tauri/backend/app/a.py", "A\n")
put(d, "backend/app/new.py", "N\n")
rc, out = run("check_dual_tree.py", "--root", d, "backend/app/new.py")
chk("TC-HOOKS-0011", "a new backend file not yet in the copy fails ('missing in the build copy')",
    rc == 1 and "new.py  — missing" in out, out)
put(d, "frontend/src/App.jsx", "J2\n")
rc, out = run("check_dual_tree.py", "--root", d, "frontend/src/App.jsx")
chk("TC-HOOKS-0012", "a frontend file that differs from desktop/src-tauri/frontend fails",
    rc == 1 and "frontend/src/App.jsx  — differs" in out, out)
put(d, "frontend/src/App.jsx", "J\n")
rc, out = run("check_dual_tree.py", "--root", d, "frontend/node_modules/x/index.js", "frontend/build/main.js",
              "backend/app/a.py.bak-X_20261117", "docs/readme.md", "backend/main.py")
chk("TC-HOOKS-0013", "node_modules/, build/, .bak-* and paths outside the two trees are not compared", rc == 0, out)
os.remove(os.path.join(d, "backend/app/new.py"))
put(d, "backend/app/gone.py", "G\n")
put(d, "desktop/src-tauri/backend/app/gone.py", "G\n")
git(d, "add", "backend/app/gone.py")
git(d, "commit", "-q", "-m", "base")
git(d, "rm", "-q", "backend/app/gone.py")
rc, out = run("check_dual_tree.py", "--root", d)
chk("TC-HOOKS-0014", "a staged deletion whose copy is still there fails (copy path named)",
    rc == 1 and "desktop/src-tauri/backend/app/gone.py  — still in the build copy" in out, out)
os.remove(os.path.join(d, "desktop/src-tauri/backend/app/gone.py"))
put(d, "desktop/src-tauri/backend/app/extra.py", "E\n")
put(d, "desktop/src-tauri/frontend/src/App.jsx", "J-old\n")
rc, out = run("check_dual_tree.py", "--root", d, "--full")
chk("TC-HOOKS-0015", "--full lists both ways: copy-only files and differing files",
    rc == 1 and "extra.py  — in the build copy only" in out and "frontend/src/App.jsx  — differs" in out, out)
shutil.rmtree(d, ignore_errors=True)

# ── REPO_MAP refresh ─────────────────────────────────────────────────
STUB = ("import os, sys\nroot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "mp = os.path.join(root, 'REPO_MAP.md')\n"
        "if os.path.exists(os.path.join(root, 'crash')): sys.exit(2)\n"
        "want = 'fresh ' + open(os.path.join(root, 'src.txt')).read()\n"
        "if '--check' in sys.argv: sys.exit(0 if os.path.exists(mp) and open(mp).read() == want else 1)\n"
        "open(mp, 'w').write(want)\n")
d = tempfile.mkdtemp(prefix="rmref_")
git(d, "init", "-q")
put(d, "scripts/gen_repo_map.py", STUB)
put(d, "src.txt", "v1\n")
put(d, "REPO_MAP.md", "fresh v1\n")
git(d, "add", "-A")
git(d, "commit", "-q", "-m", "base")
put(d, "src.txt", "v2\n")
git(d, "add", "src.txt")
rc, out = run("repo_map_refresh.py", cwd=d)
_, staged = git(d, "diff", "--cached", "--name-only")
chk("TC-HOOKS-0022", "stale REPO_MAP.md is regenerated and staged for this commit",
    rc == 0 and "refreshed" in out and "REPO_MAP.md" in staged and open(os.path.join(d, "REPO_MAP.md")).read() == "fresh v2\n",
    (rc, out, staged))
git(d, "commit", "-q", "-m", "c2")
rc, out = run("repo_map_refresh.py", cwd=d)
chk("TC-HOOKS-0023", "current REPO_MAP.md: nothing staged, nothing printed", rc == 0 and out == "", out)
put(d, "crash", "1")
rc, out = run("repo_map_refresh.py", cwd=d)
chk("TC-HOOKS-0024", "a failing generator warns and does not block (exit 0)", rc == 0 and "WARNING" in out, out)
os.remove(os.path.join(d, "scripts/gen_repo_map.py"))
rc, out = run("repo_map_refresh.py", cwd=d)
chk("TC-HOOKS-0025", "no generator in the checkout: silent no-op", rc == 0 and out == "", out)
shutil.rmtree(d, ignore_errors=True)

# ── config ───────────────────────────────────────────────────────────
cfg = open(os.path.join(ROOT, ".pre-commit-config.yaml"), encoding="utf-8").read()
ids = re.findall(r"^\s*- id:\s*(\S+)", cfg, re.M)
want = ["ruff-check", "no-backup-files", "dual-tree-parity", "repo-map-refresh", "reliability-checklist"]
chk("TC-HOOKS-0026", ".pre-commit-config.yaml has the five hooks, ruff pinned, commit + commit-msg hooks installed, "
    "nothing on push, the checklist on commit-msg only",
    ids == want and re.search(r"rev:\s*v\d+\.\d+\.\d+", cfg) is not None
    and "default_install_hook_types: [pre-commit, commit-msg]" in cfg and "default_stages: [pre-commit]" in cfg
    and "pre-push" not in cfg and re.findall(r"^\s+stages:\s*(\[.*\])", cfg, re.M) == ["[commit-msg]"]
    and re.search(r"id: reliability-checklist(?:.|\n)*?stages: \[commit-msg\]", cfg) is not None
    and not re.search(r"args:.*--fix", cfg), ids)
local = re.findall(r"entry:\s*python3? (scripts/hooks/\S+)", cfg)   # RELIABILITY_GATE_20261118: 4 local hooks
chk("TC-HOOKS-0027", "every local hook runs a stdlib script under scripts/hooks/ (not tools/) that exists",
    len(local) == 4 and all(os.path.exists(os.path.join(ROOT, p)) for p in local)
    and "language: python" in cfg and "tools/" not in cfg, local)
chk("TC-HOOKS-0043", "no market-hours push hook left: scripts/hooks/market_hours_push.py is gone (PUSH_ANYTIME_20261118)",
    not os.path.exists(os.path.join(H, "market_hours_push.py")))
rt = open(os.path.join(ROOT, "ruff.toml"), encoding="utf-8").read()
sel = re.findall(r'"([A-Z]+\d*)",\s*#', rt)
chk("TC-HOOKS-0028", "ruff.toml selects errors only (E9, F63, F7, F82), no fixing, one baselined file",
    sel == ["E9", "F63", "F7", "F82"] and "force-exclude = true" in rt
    and re.findall(r'^"([^"]+)" = \[', rt, re.M) == ["backend/app/brokers/zerodha.py"], (sel, rt[-200:]))

print("\n" + ("ALL PRE-COMMIT HOOK CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
