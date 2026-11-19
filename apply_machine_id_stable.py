#!/usr/bin/env python3
# =====================================================================
#  apply_machine_id_stable.py  —  fence MACHINE_ID_STABLE_20261119   (on v12.0.6)
#
#  19 Nov 2026, Dhivakar's Intel Mac: the battery hit 0 %, the Mac went into
#  deep standby (16:07) and woke on the charger (16:13) with its T2 bridge
#  (en5) re-created at the END of the interface list. The licence fingerprint
#  is sha256(system|machine|uuid.getnode()), and getnode() takes the first MAC
#  the OS lists — now ap1, a random MAC — so at the 12.0.6 update restart the
#  app called his licence (bound to en5) "another machine" and asked for the
#  key; activating again was refused (machine_mismatch). Nothing was wrong.
#
#  Fix: an identity is kept while the MAC it was made from is still on this
#  machine — first the one the signed licence token is bound to, then the
#  cached one; only otherwise is it recomputed as before. A copied licence or
#  machine_id.txt still fails on a machine that lacks that MAC. His Mac heals
#  itself on the next launch (token on disk is bound to en5, en5 is present):
#  no admin rebind, no key to enter. Server unchanged.
#
#   backend/app/license/machine_id.py             get_machine_id(prefer=), _present_ids
#   backend/app/license/license_client.py         _evaluate_local passes the token's id
#   backend/app/license/test_machine_id_stable.py NEW  TC-LICMID-0001…0006
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_machine_id_stable.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, release.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "MACHINE_ID_STABLE_20261119"
ITEMS = json.loads(r'''[{"path": "backend/app/license/machine_id.py", "orig": "7cafe1ee77a8eead3b73cc4074b7c18799a668594f3ffe4b22d75dc419ebb860", "new": "b13a96e0a113d41ecfdad4cae6a9d86ec32f0eb0a425c8ad865756bfe5468a56", "blob": "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"}, {"path": "backend/app/license/license_client.py", "orig": "e33ade150474414bc1e4290c23eba0592cfe5b2c352bc3052503198daa59b1f0", "new": "f3ac30cc9fb2802f768b6b034d581367d9475a57805a4b5a95172bcbdf1a80ad", "blob": "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"}, {"path": "backend/app/license/test_machine_id_stable.py", "orig": null, "new": "04417dec7f37c1884705c2ba77e0fcf6ab99729a0fd0109b5fd1015d9f47e70e", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["app/license/test_machine_id_stable.py"]


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

# ── prerequisite: WS_WATCHDOG_FAST_20261119 (v12.0.6)
if "--rollback" not in args:
    _pf = os.path.join(repo, "backend", "app", "marketdata", "ws_watchdog.py")
    if not os.path.exists(_pf) or "WS_WATCHDOG_FAST_20261119" not in open(_pf, encoding="utf-8").read():
        die("backend/app/marketdata/ws_watchdog.py lacks WS_WATCHDOG_FAST_20261119 — pull main (v12.0.6) first")


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
        # a planned file was just proven byte-identical to v12.0.6 (sha == orig): only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="machine_id_stable_")
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
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
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
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.realpath(sys.executable) != os.path.realpath(py)):
            try:
                r = subprocess.run([sys.executable, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
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

# ── 5b. JSX parse gate (esbuild from frontend/node_modules, if installed) ──
_esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if os.path.exists(_esb) and "--skip-tests" not in args:
    bad = []
    for it in ITEMS:
        if it["path"].startswith("frontend/src/") and it["path"].endswith((".jsx", ".js")):
            r = subprocess.run([_esb, os.path.join(repo, it["path"]), "--loader:.js=jsx", "--loader:.jsx=jsx",
                                "--log-level=error"], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(it["path"] + ": " + (r.stderr or "").strip()[:300])
    if bad:
        restore()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.")
say("  Undo: python3 apply_machine_id_stable.py --rollback")
