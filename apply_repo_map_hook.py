#!/usr/bin/env python3
# =====================================================================
#  apply_repo_map_hook.py  —  fence REPO_MAP_HOOK_20261117 (TD-038 follow-up)
#  REQUIRES apply_repo_map.py (REPO_MAP_20261117), committed or not.
#
#  Makes the REPO_MAP.md refresh automatic: a git pre-commit hook regenerates
#  the map when it is stale and adds it to the same commit. It never blocks a
#  commit (warning only); `git commit --no-verify` skips it.
#
#   .githooks/pre-commit            NEW  the hook (chains an existing .git/hooks/pre-commit)
#   scripts/test_repo_map_hook.py   NEW  TC-REPOMAP-0024…0031 (throw-away repos)
#   CLAUDE.md                       one line appended
#   REPO_MAP.md                     regenerated (suite count +1)
#   git config core.hooksPath .githooks   (this clone; old value kept for --rollback)
#
#     cd ~/dev/scalp-app && python3 apply_repo_map_hook.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Another clone that already has these files committed: run it there once to
#  switch the hook on (it only sets core.hooksPath).
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "REPO_MAP_HOOK_20261117"
PARENT = "REPO_MAP_20261117"
P = json.loads(r"""{"items": [{"path": ".githooks/pre-commit", "new": "68483d08e161d93b22f2900ed8dd4a485a45fe90789cfbe7251c07639c6fac90", "blob": "eNp1VNFO2zAUfc9X3IUKtdKStCBtUxHbkIY2hAYITdrDNFWufdNYuLZnu4UIJvERfCFfsus0tGGFqlWT+Picc8/1zc6bYip14atkB/KZDJUxV76wDjNu5nMZAB7vH+Dx4Z6+cHl8cT75fnQx+XZ+fjrZG+69G41G759W+z++ZMP9D4Nkh7hOEa1fb8jnAvjCOdRhDEYDLtHVsFJ4C7KEUCHMmQXpwQemEEhZeuJxOEONjgUUwDT9hKCrYJodns2xZcnhJICOvDBVhl95YO1K1JMhcplrDYxzs9AB+gyumdNSz2hd1YMcjjWbKuK2RMGV0QjTGpi1qp44tGZC9iYxndzWYyKLH8qLRHQpZ/TnMG/Cu2Ch2kRJyCOIV2SYBdIkY8ohEzVI3cCKrch9kEqBW2gPpXSeEoqVr2qgAoXkEUPPfJ4EYw97/WjE4TKzzHmELPOVuc5oSVEgCvY+FgKXhV4oNYC7O8Abgg8TLiDtESjtPEtm4gU6um+8GZ0J6Z7xJdS8X5BpopqJFH7D7m68v2nut0ojwEHsnE7gNUDa+7wx1PuUlJJMoT703EkbfEHX635QKxISKyMX6qi+qcTWVEgkjdllS7A1NUTvR8TW0yaVW/KEvDKQRvqM6MegTYuAkk6NaGahe6a1oQNQIb9CkR60ygfwN0nSnq3TJ1tZ1mBgExtFuDtKHD+kApsA057jBMQ/MOpEJGNlHaL/CGLWsVU0E11X6+3b9XS9x6O4mrVYlcPSoa+2p4wmsm1NzEd5fJH459Hl2cnZV4Iu1CqWlvCZZCO00ON1M5qyiJCajKobBE3fsBPEa3Ltu8E4KJmMw9tvRyg6aN4DKAavyUbR1Vn5B0Hto3A=", "mode": 493}, {"path": "scripts/test_repo_map_hook.py", "new": "6daf6873c0bb8a4585c919b29ef0ab7ac5dc47137513c373c62355241510d2a8", "blob": "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", "mode": 420}], "sec": "eNo9jUEKwjAURPc9xbhTMNF2oRsRXAiKSIsXaGr9SUPbVPJTobc3KLoaBua9SXYzIXA7Fnl5PRTlKc8vZbbONmmabiHEPhFQ0tjQDEPLq6cnUQ99b4OCJ+2JG2KoHy77h8LgQC/yE75DzFXEY3Hamhie5MdVVKHB36wWEmcNJUTdUN0qcDUxOFQdLeOTIUe+CoQ76WhA8CMH6ww66whu7O/kWSZvTVtFBw==", "sec_sha": "77bf18ade4e543127a1603fc8829d0ba432800ca8b59e372290b81ed57ef651e", "psec": "eNplU8tu2zAQvPsrpsnFQS3ZTtAWdYIAbu2gPdgxklx6MmlxZROmSIKkkurWf+gf9ku6VNIiRXVakfuYnRkOrt4UBe6Wm9vtar7Znk/O30+n0w8oiuvB6SnukwwJBwo0+5tUNmowuPVkIV4dCdQ6xFRiQwExBZlo32G461A5RVY2dAadkIOIdCCQ3WtLI+xkdUwUE0JrLYURFNWyNSlijHtKSdt9Dj+9pBW1Cw0MlyJIu6c4gpeWDKRV6PvEVqf+2LTPk1QrDVIgHhYPMpBC41RrGEeuqclWFEvM68TIpVI8EC5wzmOOap0TGRuE79LB2QvEKmif4nhPdhvIu20jfek7gaEoiupA1VEgyi5C13llHZkPaeisxAPji7P/O3H77TNs7oPN/OELN6ulNm3g4W95paaRoYOzpjsrB1mZu7zAYFBg+Uh8Ux0yGbyf9rxWxO16iUimLipnk2S21AhaUeNdIpsgpPem215lOa4z9hkUK/bCBsR6vlpuv/G3Wi0WYgRR7uSxuLpZrj8vr0WvWeuZY144OGPyvyixIKMZTU/682qQxrFSw5NMYEb/6u6ENymw+CPODKSYLZF7kVVj0asj6sAL9P+X//aNDk8hU9YfC0XxmJwfx1AVSbZBc4Ndq41i/3nNWU8HdiyndqDvmo3Ks8WeB7I2qY2C0YF6Ktnye0rYETuN2FypOmQjDCVbYCWynJKLnEfUe3t2yWxH4keSUXh2F7Pbk5gH3JFU/YWRvTVFw1IwnTZP4jbZXAiUcXJVcA0aalzocu3aIbPJz8Ebx2ZaOfvrx8+boDH5OJtMOJ6+m11M8PX+oRz8BjwkS+M=", "psec_sha": "cc6a9248d8c30dc081b11360df394155e81b7dd991ec35ae74f4d1019cbf65ae"}""")


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


def wr(p, b, mode=None):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    t = p + ".tmp-" + FENCE
    with open(t, "wb") as f:
        f.write(b)
    if mode:
        os.chmod(t, mode)
    os.replace(t, p)


def unz(s):
    return zlib.decompress(base64.b64decode(s))


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo (no backend/app) — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")


def git(*a):
    r = subprocess.run(["git", *a], cwd=repo, capture_output=True, timeout=60)
    return r.returncode, r.stdout


rc, gd = git("rev-parse", "--git-common-dir")
if rc != 0:
    die("not a git checkout — the hook needs git")
gd = gd.decode().strip()
gd = gd if os.path.isabs(gd) else os.path.join(repo, gd)
PREV = os.path.join(gd, "hookspath.bak-" + FENCE)


def hooks_path():
    rc, v = git("config", "--local", "--get", "core.hooksPath")
    return v.decode().strip() if rc == 0 else ""


def set_hooks_path(v):
    if v:
        git("config", "--local", "core.hooksPath", v)
    else:
        git("config", "--local", "--unset", "core.hooksPath")


blobs = {}
for it in P["items"]:
    b = unz(it["blob"])
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
SEC, PSEC = unz(P["sec"]), unz(P["psec"])
if sha(SEC) != P["sec_sha"] or sha(PSEC) != P["psec_sha"]:
    die("CLAUDE.md section payload is corrupt")
CM = os.path.join(repo, "CLAUDE.md")
CM_BAK = CM + ".bak-" + FENCE
MAP = os.path.join(repo, "REPO_MAP.md")
GEN = os.path.join(repo, "scripts", "gen_repo_map.py")


def regen():
    if os.path.exists(GEN):
        r = subprocess.run([sys.executable, GEN], capture_output=True, text=True)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    return True, "no generator"


def tidy():
    for d in (os.path.join(repo, ".githooks"),):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    pc = os.path.join(repo, "scripts", "__pycache__")
    if os.path.isdir(pc):
        for f in os.listdir(pc):
            if f.startswith("test_repo_map_hook"):
                os.remove(os.path.join(pc, f))


# ── rollback ─────────────────────────────────────────────────────────
if "--rollback" in args:
    acts, later = [], []
    for it in P["items"]:
        p = os.path.join(repo, it["path"])
        cur = rd(p)
        if cur is None:
            continue
        (acts if sha(cur) == it["new"] else later).append(p if sha(cur) == it["new"] else it["path"])
    cur = rd(CM)
    new_cm = None
    if cur is not None and FENCE.encode() in cur:
        if SEC in cur:
            new_cm = cur.replace(SEC, b"", 1)
        else:
            later.append("CLAUDE.md (its hook line was edited)")
    if later:
        die("cannot roll back — changed after this script: " + ", ".join(later) + ". Undo those edits first.")
    for p in acts:
        os.remove(p)
        say(f"  removed {os.path.relpath(p, repo)}")
    if new_cm is not None:
        wr(CM, new_cm)
        say("  removed the hook line from CLAUDE.md")
    prev = rd(PREV)
    if prev is not None or hooks_path() == ".githooks":
        set_hooks_path(prev.decode().strip() if prev is not None else "")
        say("  core.hooksPath restored to " + (repr(prev.decode().strip()) if prev and prev.strip() else "(unset)"))
    for b in (CM_BAK, PREV, MAP + ".bak-" + FENCE):
        if os.path.exists(b):
            os.remove(b)
    ok, msg = regen()
    say("  REPO_MAP.md regenerated" if ok else f"  !! generator: {msg}")
    tidy()
    say("rolled back")
    sys.exit(0)

# ── 0. prerequisite ──────────────────────────────────────────────────
g = rd(GEN)
c0 = rd(CM)
if g is None or PARENT.encode() not in g or c0 is None or PARENT.encode() not in c0:
    die("run apply_repo_map.py first — this script builds on it")
hp = hooks_path()
if hp not in ("", ".githooks"):
    die(f"core.hooksPath is already set to {hp!r} in this clone — not overriding it. "
        "Unset it or point it at .githooks yourself, then re-run")

# ── 1. state ─────────────────────────────────────────────────────────
plan, done = [], 0
for it in P["items"]:
    p = os.path.join(repo, it["path"])
    cur = rd(p)
    if cur is None:
        plan.append(it)
    elif sha(cur) == it["new"] or FENCE.encode() in cur:
        done += 1
    else:
        die(f"{it['path']} already exists with other content — move it aside and re-run")
cm_todo = FENCE.encode() not in c0
hp_todo = hp != ".githooks"
if not plan and not cm_todo and not hp_todo:
    say(f"✓ already applied (files in place, core.hooksPath = .githooks) — nothing to do")
    sys.exit(0)
if not plan and not cm_todo and hp_todo:
    with open(PREV, "wb") as f:
        f.write(hp.encode())
    set_hooks_path(".githooks")
    say("✓ files already in this clone — switched the hook on (core.hooksPath = .githooks)")
    sys.exit(0)

# ── 2. git status on CLAUDE.md: only apply_repo_map.py's own edit may be pending ──
if cm_todo and "--allow-dirty" not in args:
    rc, st = git("status", "--porcelain", "--", "CLAUDE.md")
    if rc == 0 and st.strip() and not st.startswith(b"??"):
        rc2, head = git("show", "HEAD:CLAUDE.md")
        ok = rc2 == 0 and c0 in (head + PSEC, head + b"\n" + PSEC)
        if not ok:
            die("uncommitted changes on CLAUDE.md beyond apply_repo_map.py's section "
                "(commit them, or re-run with --allow-dirty — the text is kept either way)")

# ── 3. compile ───────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="repo_map_hook_")
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
say(f"payloads verified and compiled ({len(P['items'])} files + CLAUDE.md line)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []
old_map = rd(MAP)


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                os.remove(p)
            else:
                wr(p, old)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")
    set_hooks_path(hp)
    for b in (CM_BAK, PREV):
        if os.path.exists(b):
            os.remove(b)
    tidy()


try:
    for it in plan:
        p = os.path.join(repo, it["path"])
        wr(p, blobs[it["path"]], it["mode"])
        written.append((p, None))
        say(f"  wrote {it['path']}")
    if cm_todo:
        with open(CM_BAK, "wb") as f:
            f.write(c0)
        wr(CM, c0 + (b"" if c0.endswith(b"\n") else b"\n") + SEC)
        written.append((CM, c0))
        say("  appended the hook line to CLAUDE.md")
    with open(PREV, "wb") as f:
        f.write(hp.encode())
    set_hooks_path(".githooks")
    say("  git config core.hooksPath .githooks")
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
    for t in ("scripts/test_repo_map.py", "scripts/test_repo_map_hook.py"):
        r = subprocess.run([sys.executable, os.path.join(repo, t)], cwd=repo, capture_output=True,
                           text=True, timeout=900)
        outp = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            restore()
            die(f"{t} failed — every file restored:\n" + "\n".join(
                "    " + l for l in outp.splitlines() if "FAIL" in l or "Error" in l)[-3000:])
        say(f"  ✓ {t}  " + outp.strip().splitlines()[-1])

say(f"\n✓ {FENCE} applied. From now on every commit refreshes REPO_MAP.md when it is stale.")
say("  Commit: git add .githooks CLAUDE.md REPO_MAP.md scripts/ && git commit -m 'REPO_MAP_HOOK_20261117: auto-refresh REPO_MAP.md (TD-038)'")
say("  Undo:   python3 apply_repo_map_hook.py --rollback")
