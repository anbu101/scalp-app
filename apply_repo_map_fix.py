#!/usr/bin/env python3
# =====================================================================
#  apply_repo_map_fix.py  —  fence REPO_MAP_FIX_20261117 (TD-038 follow-up)
#  REQUIRES apply_repo_map.py (REPO_MAP_20261117), committed or not.
#
#  REPO_MAP.md said the suites are "standalone scripts, not pytest" — wrong for
#  17 (Icarus, kill switch, two invariant suites). Worse, run_suites.py ran
#  those files as plain scripts, so they exited 0 WITHOUT testing anything,
#  and it called BRK / test_ic_iv_sl import errors "not runnable here".
#
#   scripts/run_suites.py     each suite runs its own way: scripts from backend/,
#                             pytest files via `-m pytest` from their folder,
#                             "Run from repo root" suites from the root; uses
#                             scripts/ci/run_ci_suites.py's rules when present.
#                             A repo module that will not import is a FAILURE;
#                             only a missing third-party package is "not runnable".
#   scripts/gen_repo_map.py   Tests section written from those rules (real counts)
#   scripts/test_repo_map.py  + TC-REPOMAP-0038…0043
#   REPO_MAP.md               regenerated
#  Nothing under backend/ or frontend/ changes.
#
#     cd ~/dev/scalp-app && python3 apply_repo_map_fix.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "REPO_MAP_FIX_20261117"
ITEMS = json.loads(r"""[{"path": "scripts/gen_repo_map.py", "orig": "4087034dcae35307ea69d2f46962338d23204e34fcdfc6d4c7748dcfe8c8962c", "new": "4d60bc97b1517ee42768f917a233fb99e8c267e2f2ede426ed598ca151a878aa", "blob": "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"}, {"path": "scripts/run_suites.py", "orig": "7deacf2a6aa397e47f93af9a43acda2879f4f5e44e5b9fd813af61ff0aaa2c0f", "new": "06069b98eed68459e565016ea3151e4844dd53b301559e6d4eda0df6f4438797", "blob": "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"}, {"path": "scripts/test_repo_map.py", "orig": "b9aaba3b9c55d821351a63691067ec2a8aa56e93108fb52931689a9a9ec94c76", "new": "6f6ca2122959155b657ad8669b8a12a9bb6115505dc9b4e8430ccd0487a9fcff", "blob": "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"}]""")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def say(m):
    print(m, flush=True)


def die(m):
    say(f"\n✗ ABORTED — {m}\n  Nothing was changed.")
    sys.exit(1)


def rd(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


def wr(p, b):
    t = p + ".tmp-" + FENCE
    with open(t, "wb") as f:
        f.write(b)
    os.replace(t, p)


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo (no backend/app) — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
MAP = os.path.join(repo, "REPO_MAP.md")
GEN = os.path.join(repo, "scripts", "gen_repo_map.py")


def regen():
    r = subprocess.run([sys.executable, GEN], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def tidy():
    pc = os.path.join(repo, "scripts", "__pycache__")
    if os.path.isdir(pc):
        for f in os.listdir(pc):
            if f.split(".")[0] in ("gen_repo_map", "run_suites", "test_repo_map"):
                os.remove(os.path.join(pc, f))
        if not os.listdir(pc):
            os.rmdir(pc)


# ── rollback ─────────────────────────────────────────────────────────
if "--rollback" in args:
    later = []
    for it in ITEMS:
        p = os.path.join(repo, it["path"])
        cur, bak = rd(p), rd(p + ".bak-" + FENCE)
        if cur is not None and sha(cur) == it["new"] and (bak is None or sha(bak) != it["orig"]):
            later.append(it["path"] + " (its .bak-" + FENCE + " is missing or changed)")
        elif cur is not None and sha(cur) not in (it["new"], it["orig"]):
            later.append(it["path"])
    if later:
        die("cannot roll back — " + ", ".join(later))
    n = 0
    for it in ITEMS:
        p = os.path.join(repo, it["path"])
        if sha(rd(p)) == it["new"]:
            shutil.copy2(p + ".bak-" + FENCE, p)
            n += 1
        if os.path.exists(p + ".bak-" + FENCE):
            os.remove(p + ".bak-" + FENCE)
    ok, msg = regen()
    say("  REPO_MAP.md regenerated" if ok else f"  !! generator: {msg}")
    tidy()
    say(f"rolled back {n} file(s)")
    sys.exit(0)

# ── 1. state ─────────────────────────────────────────────────────────
plan, done = [], 0
for it in ITEMS:
    p = os.path.join(repo, it["path"])
    cur = rd(p)
    if cur is None:
        die("run apply_repo_map.py first — " + it["path"] + " is missing")
    if sha(cur) == it["new"]:
        done += 1
    elif sha(cur) == it["orig"]:
        plan.append((it, p, cur))
    else:
        die(f"{it['path']} differs from what apply_repo_map.py wrote — local edits? Commit/stash them and re-run")
if not plan:
    say(f"✓ already applied ({done} file(s) carry {FENCE}) — nothing to do")
    sys.exit(0)

# ── 2. git status: only apply_repo_map.py's own (uncommitted) output may be pending ──
if "--allow-dirty" not in args:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + [it["path"] for it, _, _ in plan], cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        ok_paths = {it["path"] for it, _, cur in plan if sha(cur) == it["orig"]}
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??") and l[3:].strip() not in ok_paths]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty))
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. compile ───────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="repo_map_fix_")
for it, _, _ in plan:
    f = os.path.join(tmp, os.path.basename(it["path"]))
    with open(f, "wb") as h:
        h.write(blobs[it["path"]])
    try:
        py_compile.compile(f, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"payload {it['path']} does not compile: {e}")
shutil.rmtree(tmp, ignore_errors=True)
say(f"payloads verified and compiled ({len(plan)} file(s))")

# ── 4. write, all or nothing ─────────────────────────────────────────
written, old_map = [], rd(MAP)


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                wr(p, old)
            if os.path.exists(p + ".bak-" + FENCE):
                os.remove(p + ".bak-" + FENCE)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")
    tidy()


try:
    for it, p, cur in plan:
        with open(p + ".bak-" + FENCE, "wb") as f:
            f.write(cur)
        wr(p, blobs[it["path"]])
        written.append((p, cur))
        say(f"  wrote {it['path']}")
    written.append((MAP, old_map))
    ok, msg = regen()
    if not ok:
        raise RuntimeError("generator: " + msg)
    say("  REPO_MAP.md regenerated")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")

# ── 5. tests ─────────────────────────────────────────────────────────
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    for t in ("scripts/test_repo_map.py",) + (("scripts/ci/test_ci.py",)
                                              if os.path.exists(os.path.join(repo, "scripts", "ci", "test_ci.py")) else ()):
        r = subprocess.run([sys.executable, os.path.join(repo, t)], cwd=repo, capture_output=True, text=True, timeout=900)
        outp = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            restore()
            die(f"{t} failed — every file restored:\n" + "\n".join(
                "    " + l for l in outp.splitlines() if "FAIL" in l or "Error" in l)[-3000:])
        say(f"  ✓ {t}  " + outp.strip().splitlines()[-1])

tidy()
say(f"\n✓ {FENCE} applied. REPO_MAP.md now gives the real split, and run_suites.py runs pytest files under pytest.")
say("  Commit: git add scripts/gen_repo_map.py scripts/run_suites.py scripts/test_repo_map.py REPO_MAP.md && git commit")
say("  Undo:   python3 apply_repo_map_fix.py --rollback")
