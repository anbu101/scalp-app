#!/usr/bin/env python3
# =====================================================================
#  apply_relay_recovery_safe.py  —  fence RELAY_RECOVERY_SAFE_20261115
#  INDEPENDENT of the other scripts (touches only the relay monitor).
#
#  ⚠ ORDER RELAY — the health monitor that runs on each user's computer.
#
#  Reproduced in services/test_relay_recovery.py (fake SSH, fake clock):
#   * three failed health checks FROM THIS COMPUTER (90 s) restarted the relay
#     service on the server even when the server was healthy and only this
#     computer's network was broken: 13 restarts of a healthy relay in 20 min;
#   * a restart that took more than 5 s escalated to a REBOOT;
#   * no cooldown (the constant was defined, never used): a dead relay was
#     rebooted 13 times in 20 minutes.
#  Now: one attempt per host per 5 min, one reboot per 30 min; the relay is
#  asked ON THE SERVER (over SSH) before anything is touched — healthy there
#  means nothing is restarted and the user is told to check this computer's
#  network; a restart gets 20 s before a reboot is considered.
#  NOT changed here: the health check and the relay traffic are still plain
#  HTTP. Moving them to HTTPS needs a certificate on the relay server and a
#  redeploy of every relay — a separate change.
#
#   services/relay_deployer.py        _attempt_relay_recovery rewritten
#   services/test_relay_recovery.py   NEW — 11 checks
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_relay_recovery_safe.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then (after all four): ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "RELAY_RECOVERY_SAFE_20261115"
ITEMS = json.loads(r'''[{"path": "backend/app/services/relay_deployer.py", "orig": "063cb531fffd504850df830ac299b66d4125f0f85003aeb0c7e9a266a606b47b", "new": "96c630f9be0ab81cb9279256ceda54e59696bdef090e01068f346ae125d6b68c", "blob": "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"}, {"path": "backend/app/services/test_relay_recovery.py", "orig": null, "new": "69df674dccc3a9d850674f134b56a9077466115ed48af7f356629c5ed06e38b5", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [         "app/services/test_relay_recovery.py"
]


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

# ── prerequisite: a repo with the order relay
if "--rollback" not in args:
    _pf = os.path.join(repo, *['backend', 'app', 'services', 'relay_deployer.py'])
    if not os.path.exists(_pf) or 'def _attempt_relay_recovery' not in open(_pf, encoding="utf-8").read():
        die("run a repo with the order relay first — this script builds on it")


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
tmp = tempfile.mkdtemp(prefix="relay_recovery_safe_")
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
    env = dict(os.environ, PYTHONPATH=backend, ADV_STAGE="6", SCALP_TEST_MODE="1")
    failed, envmiss = [], []
    for t in TESTS:
        if not os.path.exists(os.path.join(backend, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
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
say("  Undo: python3 apply_relay_recovery_safe.py --rollback")
