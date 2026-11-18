#!/usr/bin/env python3
# =====================================================================
#  apply_repo_map.py  —  fence REPO_MAP_20261117 (TD-038)
#
#  A map so every session opens the right file first. Docs + dev scripts only:
#  nothing under backend/ or frontend/ changes, no build, no restart.
#
#   REPO_MAP.md                 NEW  generated map (strategies by codename ->
#                                    engine, backtest, defaults/Settings/form
#                                    lines, panel, tests; dual tree; suites +
#                                    commands; shared modules; fences)
#   scripts/gen_repo_map.py     NEW  stdlib generator (ast + JSX scan); --check
#   scripts/run_suites.py       NEW  runs test_*.py, prints failures + summary only
#   scripts/test_repo_map.py    NEW  TC-REPOMAP-0001…0015
#   CLAUDE.md                   existing text kept; a short "Start here" +
#                                    rules section is APPENDED
#
#  If this tree differs from GitHub main, REPO_MAP.md is regenerated for it.
#
#     cd ~/dev/scalp-app && python3 apply_repo_map.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: git add REPO_MAP.md CLAUDE.md scripts/ && git commit
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "REPO_MAP_20261117"
ITEMS = json.loads(r"""[{"path": "scripts/gen_repo_map.py", "new": "4087034dcae35307ea69d2f46962338d23204e34fcdfc6d4c7748dcfe8c8962c", "blob": "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", "generated": false}, {"path": "scripts/run_suites.py", "new": "7deacf2a6aa397e47f93af9a43acda2879f4f5e44e5b9fd813af61ff0aaa2c0f", "blob": "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", "generated": false}, {"path": "scripts/test_repo_map.py", "new": "b9aaba3b9c55d821351a63691067ec2a8aa56e93108fb52931689a9a9ec94c76", "blob": "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", "generated": false}, {"path": "REPO_MAP.md", "new": "7718960ce1965b5ce7efe80b8074aa46bfd00b7a612c25ddbcd484cd987b4d90", "blob": "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", "generated": true}]""")
SECTION = json.loads(r"""{"path": "CLAUDE.md", "sha": "cc6a9248d8c30dc081b11360df394155e81b7dd991ec35ae74f4d1019cbf65ae", "blob": "eNplU8tu2zAQvPsrpsnFQS3ZTtAWdYIAbu2gPdgxklx6MmlxZROmSIKkkurWf+gf9ku6VNIiRXVakfuYnRkOrt4UBe6Wm9vtar7Znk/O30+n0w8oiuvB6SnukwwJBwo0+5tUNmowuPVkIV4dCdQ6xFRiQwExBZlo32G461A5RVY2dAadkIOIdCCQ3WtLI+xkdUwUE0JrLYURFNWyNSlijHtKSdt9Dj+9pBW1Cw0MlyJIu6c4gpeWDKRV6PvEVqf+2LTPk1QrDVIgHhYPMpBC41RrGEeuqclWFEvM68TIpVI8EC5wzmOOap0TGRuE79LB2QvEKmif4nhPdhvIu20jfek7gaEoiupA1VEgyi5C13llHZkPaeisxAPji7P/O3H77TNs7oPN/OELN6ulNm3g4W95paaRoYOzpjsrB1mZu7zAYFBg+Uh8Ux0yGbyf9rxWxO16iUimLipnk2S21AhaUeNdIpsgpPem215lOa4z9hkUK/bCBsR6vlpuv/G3Wi0WYgRR7uSxuLpZrj8vr0WvWeuZY144OGPyvyixIKMZTU/682qQxrFSw5NMYEb/6u6ENymw+CPODKSYLZF7kVVj0asj6sAL9P+X//aNDk8hU9YfC0XxmJwfx1AVSbZBc4Ndq41i/3nNWU8HdiyndqDvmo3Ks8WeB7I2qY2C0YF6Ktnye0rYETuN2FypOmQjDCVbYCWynJKLnEfUe3t2yWxH4keSUXh2F7Pbk5gH3JFU/YWRvTVFw1IwnTZP4jbZXAiUcXJVcA0aalzocu3aIbPJz8Ebx2ZaOfvrx8+boDH5OJtMOJ6+m11M8PX+oRz8BjwkS+M="}""")


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


def tidy():
    """Remove this script's bytecode and an emptied scripts/ folder."""
    sd = os.path.join(repo, "scripts")
    pc = os.path.join(sd, "__pycache__")
    if os.path.isdir(pc):
        for f in os.listdir(pc):
            if f.split(".")[0] in ("gen_repo_map", "run_suites", "test_repo_map"):
                os.remove(os.path.join(pc, f))
        if not os.listdir(pc):
            os.rmdir(pc)
    if os.path.isdir(sd) and not os.listdir(sd):
        os.rmdir(sd)


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
SEC = zlib.decompress(base64.b64decode(SECTION["blob"]))
if sha(SEC) != SECTION["sha"]:
    die("CLAUDE.md section payload is corrupt")
CM = os.path.join(repo, SECTION["path"])
CM_BAK = CM + ".bak-" + FENCE

# ── rollback ─────────────────────────────────────────────────────────
if "--rollback" in args:
    acts, later = [], []
    for it in ITEMS:
        p = os.path.join(repo, it["path"])
        cur = rd(p)
        if cur is None:
            continue
        if sha(cur) == it["new"] or (it["generated"] and FENCE.encode() in cur):
            acts.append(("rm", p, None))
        else:
            later.append(it["path"])
    cur = rd(CM)
    if cur is not None and FENCE.encode() in cur:
        bak = rd(CM_BAK)
        stripped = cur.replace(SEC, b"", 1) if SEC in cur else None
        if bak is not None and stripped is not None and stripped.rstrip(b"\n") == bak.rstrip(b"\n"):
            acts.append(("put", CM, bak))                      # exactly what was there before
        elif bak is None and cur.strip() == SEC.strip():
            acts.append(("rm", CM, None))                      # this script created it
        elif stripped is not None:
            acts.append(("put", CM, stripped))                 # edited since: drop only our section
        else:
            later.append("CLAUDE.md (its REPO_MAP section was edited)")
    if later:
        die("cannot roll back — changed after this script: " + ", ".join(later)
            + ". Undo those edits (or restore by hand) first.")
    for kind, p, b in acts:
        if kind == "rm":
            os.remove(p)
        else:
            wr(p, b)
        say(f"  {'removed' if kind == 'rm' else 'restored'} {os.path.relpath(p, repo)}")
    if os.path.exists(CM_BAK):
        os.remove(CM_BAK)
    tidy()
    say(f"rolled back {len(acts)} file(s)")
    sys.exit(0)

# ── 1. state ─────────────────────────────────────────────────────────
plan, done = [], 0
for it in ITEMS:
    p = os.path.join(repo, it["path"])
    cur = rd(p)
    if cur is None:
        plan.append(it)
    elif sha(cur) == it["new"] or (it["generated"] and FENCE.encode() in cur):
        done += 1
    elif FENCE.encode() in cur:
        done += 1                       # this script's file, changed again since — leave it
        say(f"  note: {it['path']} changed after {FENCE} — left as is")
    else:
        die(f"{it['path']} already exists with other content — move it aside and re-run")
cm_cur = rd(CM)
cm_todo = cm_cur is None or FENCE.encode() not in cm_cur
if not plan and not cm_todo:
    say(f"✓ already applied ({done + 1} file(s) carry {FENCE}) — nothing to do")
    r = subprocess.run([sys.executable, os.path.join(repo, "scripts", "gen_repo_map.py"), "--check"],
                       capture_output=True, text=True)
    say("  " + (r.stdout or r.stderr).strip())
    sys.exit(0)

# ── 2. git status: an uncommitted change on CLAUDE.md is a stop sign ─
if cm_todo and cm_cur is not None and "--allow-dirty" not in args:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--", "CLAUDE.md"], cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        if dirty:
            die("uncommitted changes on CLAUDE.md:\n  " + "\n  ".join(dirty)
                + "\n  (commit them, or re-run with --allow-dirty — the text is kept either way)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. compile every Python payload before writing anything ─────────
tmp = tempfile.mkdtemp(prefix="repo_map_")
for it in plan:
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
        with open(f, "wb") as h:
            h.write(blobs[it["path"]])
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
shutil.rmtree(tmp, ignore_errors=True)
say(f"payloads verified and compiled ({len(ITEMS)} files + CLAUDE.md section)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []          # (path, old bytes or None)


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                os.remove(p)
            else:
                wr(p, old)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")
    if os.path.exists(CM_BAK) and ("CM_BAK_NEW" in globals()):
        os.remove(CM_BAK)
    tidy()


try:
    for it in plan:
        p = os.path.join(repo, it["path"])
        wr(p, blobs[it["path"]])
        written.append((p, None))
        say(f"  wrote {it['path']}")
    if cm_todo:
        if cm_cur is not None:
            with open(CM_BAK, "wb") as f:
                f.write(cm_cur)
            CM_BAK_NEW = True
            new = cm_cur + (b"" if cm_cur.endswith(b"\n") else b"\n") + SEC
        else:
            new = SEC.lstrip(b"\n")
        wr(CM, new)
        written.append((CM, cm_cur))
        say("  " + ("appended the REPO_MAP section to CLAUDE.md (existing text kept)" if cm_cur is not None
                    else "created CLAUDE.md"))
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")

# ── 5. map current for THIS tree, then the tests ─────────────────────
gen = os.path.join(repo, "scripts", "gen_repo_map.py")
r = subprocess.run([sys.executable, gen, "--check"], capture_output=True, text=True)
if r.returncode == 1:
    old_map = rd(os.path.join(repo, "REPO_MAP.md"))
    r2 = subprocess.run([sys.executable, gen], capture_output=True, text=True)
    if r2.returncode != 0:
        restore()
        die("generator failed on this tree — everything restored:\n  " + (r2.stdout + r2.stderr).strip())
    say("  this tree differs from GitHub main — REPO_MAP.md regenerated for it")
elif r.returncode != 0:
    restore()
    die("generator failed on this tree — everything restored:\n  " + (r.stdout + r.stderr).strip())

if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    t = subprocess.run([sys.executable, os.path.join(repo, "scripts", "test_repo_map.py")], cwd=repo,
                       capture_output=True, text=True, timeout=900)
    outp = (t.stdout or "") + (t.stderr or "")
    if t.returncode != 0:
        restore()
        die("tests failed — every file restored:\n" + "\n".join(
            "    " + l for l in outp.splitlines() if "FAIL" in l or "Error" in l)[-3000:])
    say("  ✓ scripts/test_repo_map.py  " + outp.strip().splitlines()[-1])

say(f"\n✓ {FENCE} applied. No build or restart needed (nothing under backend/ or frontend/ changed).")
say("  Commit: git add REPO_MAP.md CLAUDE.md scripts/ && git commit -m 'REPO_MAP_20261117: repo map + CLAUDE.md (TD-038)'")
say("  Undo:   python3 apply_repo_map.py --rollback")
