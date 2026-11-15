#!/usr/bin/env python3
# =====================================================================
#  apply_safety_layers_test.py  —  fence SAFETY_LAYERS_TEST_20261115
#  REQUIRES v11.8.2 (EXIT_ONCE_ALL_20261115).
#
#  Tracker T4. TEST ONLY — no app code changes.
#  The three layers that stop the 13/14 Nov false booking (strict reads,
#  session gate, "no order, no booking") are each proven ALONE, with the
#  other two switched back to the old behaviour, in three situations:
#                                         strict  session  no-order
#     before login (unreadable)            yes     yes      yes
#     logged in, empty answers pre-open    n/a     yes      yes
#     in session, empty answers            n/a     n/a      yes  ← only layer
#  With all three off the incident reproduces (so the test can see it).
#
#   engine/test_safety_layers.py   NEW — 14 checks
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_safety_layers_test.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "SAFETY_LAYERS_TEST_20261115"
ITEMS = json.loads(r'''[{"path": "backend/app/engine/test_safety_layers.py", "orig": null, "new": "ab0978efffa1b397c4fe7280cca52633f58305d7fa088136cf789216415dd74a", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [         "app/engine/test_safety_layers.py",
         "app/engine/test_fleet_live.py"
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

# ── prerequisite: v11.8.2 (EXIT_ONCE_ALL_20261115)
if "--rollback" not in args:
    _pf = os.path.join(repo, *['backend', 'app', 'execution', 'broker_truth.py'])
    if not os.path.exists(_pf) or 'EXIT_ONCE_ALL_20261115' not in open(_pf, encoding="utf-8").read():
        die("needs v11.8.2 (EXIT_ONCE_ALL_20261115) — this script builds on it")


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
    later = []
    for it, p, build in targets():
        if build or not os.path.exists(p) or not os.path.exists(p + ".bak-" + FENCE):
            continue
        with open(p, "rb") as f:
            if sha(f.read()) != it["new"]:
                later.append(os.path.relpath(p, repo))
    if later:
        die("cannot roll back — changed again after this script (by a later fix or a local edit): "
            + ", ".join(later) + ". Roll the later fixes back first, newest first. Nothing was changed.")
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
plan, done, skipped, later = [], 0, [], []
_applied = False                         # this script's marker is in the tree → it was applied
for it, p, build in targets():
    if not build and os.path.exists(p):
        with open(p, "rb") as f:
            if FENCE.encode() in f.read():
                _applied = True
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
    if _applied:
        later.append(rel)                # applied, then changed by a later fix
        continue
    die(f"{rel} differs from GitHub main and from the patched version — local edits? "
        f"(commit/stash them or pull main, then re-run)")
if not plan:
    say(f"✓ already applied ({done} file(s) carry {FENCE}"
        + (f"; {len(later)} changed again by later fixes" if later else "") + ") — nothing to do")
    sys.exit(0)
if later:
    die("partly applied: " + ", ".join(later) + " changed after this script while other files are "
        "not patched — check the tree (git status) before going on. Nothing was changed.")

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
tmp = tempfile.mkdtemp(prefix="safety_layers_")
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
    env = dict(os.environ, PYTHONPATH=backend, SCALP_TEST_MODE="1")
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
say("  Undo: python3 apply_safety_layers_test.py --rollback")
