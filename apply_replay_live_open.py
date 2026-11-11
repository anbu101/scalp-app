#!/usr/bin/env python3
# =====================================================================
#  apply_replay_live_open.py  —  fence REPLAY_LIVE_OPEN_20261111
#
#  Dashboard live chart showed "watching for an entry · no open position"
#  for a LIVE Icarus condor that was open (11 Nov, friend's app), while
#  paper Icarus and live Velvet charts were fine.
#
#  Cause: the replay decides "open" from a row's state. Live legs of
#  Icarus / Tigris (and other strategies that book into the shared
#  `trades` table) carry the order state machine's value — "PROTECTED",
#  "BUY_PLACED" — and the rule accepted only "OPEN" (REPLAY_STALE_OPEN,
#  28 Oct). Paper rows say OPEN; Velvet/Timberwolf have their own tables
#  that say OPEN — hence only live Icarus/Tigris were affected.
#
#   live_replay.py  open = explicit OPEN, or no exit time and not a
#                   terminal state (CLOSED, STALE, EXITED, CANCELLED, …).
#                   The 28 Oct orphans (STALE) stay out; an open live leg
#                   is no longer drawn as "stale".
#   tests  test_replay_live_open.py (16 checks, rows written by the real
#          trades_repo exactly as Icarus books them; fails on the old
#          code with the 11 Nov symptom) + the three existing replay suites
#
#  Display only — no trading code touched; safe to install any time.
#     cd ~/dev/scalp-app && python3 apply_replay_live_open.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "REPLAY_LIVE_OPEN_20261111"
ITEMS = json.loads(r'''[{"path": "backend/app/backtest/replay/live_replay.py", "orig": "0010b4c62c1c21d5fa4ea00c9e10b3f16e629a6fae58bd4bced787ae7d69ae28", "new": "3df185b4bbdd3e0c8d9038136f14040b182001ae9047490cb9094a6a102d9647", "blob": "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"}, {"path": "backend/app/backtest/replay/test_replay_live_open.py", "orig": null, "new": "c8dc8373be0e106cca71e154899cfca6060e121d13b55fb22108b5f8f8d54260", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["app/backtest/replay/test_replay_live_open.py", "app/backtest/replay/test_replay_stale_rows.py",
         "app/backtest/replay/test_live_replay.py", "app/backtest/replay/test_trade_replay.py"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def say(m):
    print(m, flush=True)


def die(m):
    say(f"\n✗ ABORTED — {m}\n  Nothing was changed.")
    sys.exit(1)


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo (no backend/app) — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")


def targets():
    """(item, absolute path, is_build_copy)"""
    out = []
    for it in ITEMS:
        out.append((it, os.path.join(repo, it["path"]), False))
        for a, b in BUILD_COPIES.items():
            if it["path"].startswith(a):
                p = os.path.join(repo, b + it["path"][len(a):])
                if os.path.isdir(os.path.dirname(p)):
                    out.append((it, p, True))
    return out


# ── rollback mode ────────────────────────────────────────────────────
if "--rollback" in args:
    n = 0
    for it, p, _ in targets():
        bak = p + ".bak-" + FENCE
        if os.path.exists(bak):
            shutil.copy2(bak, p)
            os.remove(bak)
            n += 1
        elif it["orig"] is None and os.path.exists(p):
            with open(p, "rb") as f:
                if sha(f.read()) == it["new"]:
                    os.remove(p)
                    n += 1
    say(f"rolled back {n} file(s)")
    sys.exit(0)

# ── 1. state of every target ─────────────────────────────────────────
plan, done, skipped = [], 0, []
for it, p, build in targets():
    cur = open(p, "rb").read() if os.path.exists(p) else None
    cs = sha(cur) if cur is not None else None
    rel = os.path.relpath(p, repo)
    if cs == it["new"]:
        done += 1
        continue
    if cs == it["orig"]:
        plan.append((it, p, cur, build))
        continue
    if build:
        skipped.append(rel)              # a stale build copy — the build re-syncs it
        continue
    if cur is None:
        die(f"{rel} is missing")
    die(f"{rel} differs from GitHub main and from the patched version — local edits? "
        f"(commit/stash them or pull main, then re-run)")
if not plan:
    say(f"✓ already applied ({done} file(s) carry {FENCE}) — nothing to do")
    sys.exit(0)

# ── 2. git status: an uncommitted change on a target is a stop sign ──
if "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for _, p, _, b in plan if not b]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="replay_live_open_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
        open(f, "wb").write(b)
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
say(f"payloads verified and compiled ({len(ITEMS)} files)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                os.remove(p)
            else:
                open(p, "wb").write(old)
            bak = p + ".bak-" + FENCE
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        if cur is not None:
            open(p + ".bak-" + FENCE, "wb").write(cur)
        t = p + ".tmp-" + FENCE
        open(t, "wb").write(blobs[it["path"]])
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")

# ── 5. tests ─────────────────────────────────────────────────────────
backend = os.path.join(repo, "backend")
py = next((c for c in (os.path.join(backend, "venv", "bin", "python"), os.path.join(backend, ".venv", "bin", "python"),
                       os.path.join(repo, "venv", "bin", "python"), os.path.join(repo, ".venv", "bin", "python"))
           if os.path.exists(c)), sys.executable)
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    say(f"running tests with {py}")
    env = dict(os.environ, PYTHONPATH=backend)
    failed, envmiss = [], []
    for t in TESTS:
        if not os.path.exists(os.path.join(backend, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, timeout=600)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        last = (outp.strip().splitlines() or [""])[-1]
        if r is not None and r.returncode == 0:
            say(f"  ✓ {t}  {last}")
        elif "ModuleNotFoundError" in outp and "No module named 'app" not in outp:
            envmiss.append(t)
            say(f"  ? {t}  could not run here: {last}")
        else:
            failed.append(t)
            say(f"  ✗ {t}\n" + "\n".join("      " + l for l in outp.strip().splitlines()[-25:]))
    if failed:
        restore()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: some suites need the backend's Python packages; re-run them with that Python")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.")
say("  Undo: python3 apply_replay_live_open.py --rollback")
