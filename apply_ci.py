#!/usr/bin/env python3
# =====================================================================
#  apply_ci.py  —  fence CI_20261117 (TD-041)
#
#  GitHub Actions CI on every push to main and on demand. Read-only: no
#  secrets, no broker credentials, nothing that talks to Kite.
#
#   .github/workflows/ci.yml          NEW  3 backend shards (Python 3.10, pip cache),
#                                          frontend build (Node 18, npm cache),
#                                          pre-commit (if .pre-commit-config.yaml) + tooling tests
#   scripts/ci/run_ci_suites.py       NEW  runs every backend suite the way the Mac does
#                                          (script / pytest-from-folder / repo-root), empty
#                                          HOME each, one line per suite + failures, SKIP and
#                                          KNOWN_FAIL lists with reasons and DEF ids
#   scripts/ci/test_ci.py             NEW  TC-CI-0001…0020
#   scripts/ci/requirements-ci.txt    NEW  pytest + pre-commit pins (CI only)
#   scripts/ci/suite_durations.json   NEW  measured seconds per suite (shard balance)
#   REPO_MAP.md                       regenerated if scripts/gen_repo_map.py is here
#  build-release.yml and everything under backend/ and frontend/ are untouched.
#
#     cd ~/dev/scalp-app && python3 apply_ci.py
#  Options: --repo PATH  --skip-tests  --rollback
#  Then: git add .github/workflows/ci.yml scripts/ci REPO_MAP.md && git commit && git push
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "CI_20261117"
ITEMS = json.loads(r"""[{"path": ".github/workflows/ci.yml", "new": "60f176fd425ef38fd1f20fbbf1a36a224df5db1424d7ef97af2fb1ff739024d0", "blob": "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"}, {"path": "scripts/ci/run_ci_suites.py", "new": "b79a649dd7482649d78ba3e08cdd1a8338ee86c30566f60c7dbac796e608863a", "blob": "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"}, {"path": "scripts/ci/test_ci.py", "new": "48179cbde3078d8dafb8a5ae3591c9da51e9b1688b111c4fb5cd7c1b2448f256", "blob": "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"}, {"path": "scripts/ci/requirements-ci.txt", "new": "e23e565935264ef60c3e2b6dc98ece3c588fbb64ffc0f3491acf9c6c396d8aa3", "blob": "eNpTVihOLsosKCnWT87UL0otLM0sSs1NzSsp1k3O1CupKFF41DBF4dGUBiBScPaMNzIwMjM0NDSHCZWkFpcolOTn5xQDZRUy84pLEnOA7Pw8oGCBQn6aQlJicnZqXgqK0SBzuQoqQXptbS30jPVMuAqKUnWT83NzM4EiJnpmekZcALUGOC4="}, {"path": "scripts/ci/suite_durations.json", "new": "2268a8e9494997b79c0b1b3db577471d86d1d50d9a9115f773ad99366e53ca05", "blob": "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"}]""")


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
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
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
if not os.path.isdir(os.path.join(repo, "frontend")) or not os.path.exists(os.path.join(repo, "backend", "requirements.txt")):
    die("backend/requirements.txt or frontend/ missing — not the expected tree")

blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
MAP = os.path.join(repo, "REPO_MAP.md")
GEN = os.path.join(repo, "scripts", "gen_repo_map.py")


def regen():
    if not os.path.exists(GEN):
        return True, "no generator"
    r = subprocess.run([sys.executable, GEN], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def tidy():
    for d in (os.path.join(repo, "scripts", "ci", "__pycache__"),):
        shutil.rmtree(d, ignore_errors=True)
    for d in (os.path.join(repo, "scripts", "ci"), os.path.join(repo, "scripts"), os.path.join(repo, ".github", "workflows")):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)


# ── rollback ─────────────────────────────────────────────────────────
if "--rollback" in args:
    rm, later = [], []
    for it in ITEMS:
        p = os.path.join(repo, it["path"])
        cur = rd(p)
        if cur is None:
            continue
        (rm if sha(cur) == it["new"] else later).append(p if sha(cur) == it["new"] else it["path"])
    if later:
        die("cannot roll back — changed after this script: " + ", ".join(later) + ". Undo those edits first.")
    for p in rm:
        os.remove(p)
        say(f"  removed {os.path.relpath(p, repo)}")
    if os.path.exists(GEN):
        ok, msg = regen()
        say("  REPO_MAP.md regenerated" if ok else f"  !! generator: {msg}")
    tidy()
    say(f"rolled back {len(rm)} file(s)")
    sys.exit(0)

# ── 1. state ─────────────────────────────────────────────────────────
plan, done = [], 0
for it in ITEMS:
    cur = rd(os.path.join(repo, it["path"]))
    if cur is None:
        plan.append(it)
    elif sha(cur) == it["new"] or (FENCE.encode() in cur and not it["path"].endswith(".json")):
        done += 1
    elif it["path"].endswith(".json"):
        done += 1                         # durations were re-measured locally: keep them
    else:
        die(f"{it['path']} already exists with other content — move it aside and re-run")
if not plan:
    say(f"✓ already applied ({done} file(s) in place) — nothing to do")
    sys.exit(0)

# ── 2. compile ───────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="ci_apply_")
for it in plan:
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
        with open(f, "wb") as h:
            h.write(blobs[it["path"]])
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
    if it["path"].endswith(".json"):
        try:
            json.loads(blobs[it["path"]])
        except ValueError as e:
            die(f"payload {it['path']} is not JSON: {e}")
shutil.rmtree(tmp, ignore_errors=True)
say(f"payloads verified and compiled ({len(plan)} file(s))")

# ── 3. write, all or nothing ─────────────────────────────────────────
written = []
old_map = rd(MAP)


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                wr(p, old)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")
    tidy()


try:
    for it in plan:
        p = os.path.join(repo, it["path"])
        wr(p, blobs[it["path"]])
        written.append((p, None))
        say(f"  wrote {it['path']}")
    if os.path.exists(GEN):
        written.append((MAP, old_map))
        ok, msg = regen()
        if not ok:
            raise RuntimeError("generator: " + msg)
        say("  REPO_MAP.md regenerated (new suite counted)")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")

# ── 4. checks ────────────────────────────────────────────────────────
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    tests = ["scripts/ci/test_ci.py"] + (["scripts/test_repo_map.py"]
                                         if os.path.exists(os.path.join(repo, "scripts", "test_repo_map.py")) else [])
    for t in tests:
        r = subprocess.run([sys.executable, os.path.join(repo, t)], cwd=repo, capture_output=True, text=True,
                           timeout=900)
        outp = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            restore()
            die(f"{t} failed — every file restored:\n" + "\n".join(
                "    " + l for l in outp.splitlines() if "FAIL" in l or "Error" in l)[-3000:])
        say(f"  ✓ {t}  " + outp.strip().splitlines()[-1])
    al = shutil.which("actionlint")
    if al:
        r = subprocess.run([al, "-no-color", os.path.join(repo, ".github", "workflows", "ci.yml")], cwd=repo,
                           capture_output=True, text=True)
        if r.returncode != 0:
            restore()
            die("actionlint found problems in ci.yml — every file restored:\n" + (r.stdout + r.stderr).strip())
        say("  ✓ actionlint: ci.yml clean")
    else:
        say("  (actionlint not installed — skipped; brew install actionlint to lint the workflow)")

tidy()
say(f"\n✓ {FENCE} applied. CI starts on the next push to main (or Actions -> CI -> Run workflow).")
say("  Commit: git add .github/workflows/ci.yml scripts/ci" + (" REPO_MAP.md" if os.path.exists(GEN) else "")
    + " && git commit -m 'CI_20261117: GitHub Actions CI (TD-041)' && git push")
say("  Local dry run of one shard:  python3 scripts/ci/run_ci_suites.py backend --shard 1/3")
say("  Undo:   python3 apply_ci.py --rollback")
