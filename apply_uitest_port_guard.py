#!/usr/bin/env python3
# =====================================================================
#  apply_uitest_port_guard.py  —  fence UITEST_PORT_GUARD_20261120
#
#  The UI tests' server (frontend/uitest/ui_server.py) serves on 127.0.0.1:47321,
#  the running Scalp app's port. macOS lets it sit beside the app's *:47321, and
#  on 20 Nov (12:3x IST, market hours) the LIVE app's window talked to the test
#  server: "License not valid", empty dashboard, OFFLINE. start() now refuses to
#  run when anything answers on its port, and checks that the licence it then
#  reads is its own. Test harness only; nothing in the app changes.
#
#   frontend/uitest/ui_server.py               port_in_use(), guarded start()
#   frontend/uitest/test_ui_server_guard.py NEW TC-UITEST-0001…0003 (spare ports only)
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_uitest_port_guard.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "UITEST_PORT_GUARD_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/uitest/ui_server.py", "orig": "908e02296b3df5fc7135959b7d77140a5ef67716b09abd705fdf47e6232fa5ce", "new": "846b08f3ec3dc966b94a0157a0347d1e4733aa49e3188b0f2f10e9546e244e95", "blob": "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"}, {"path": "frontend/uitest/test_ui_server_guard.py", "orig": null, "new": "fd089b23fb14bf2735013bc29e4f88d4d668a8365d2a23af6614549d30eccdbc", "blob": "eNqVVcFu20YQvfMrpsxBZEJTlJw2AAsdBFdpjCSya0mHwg2IFbmUNqF26d2lFUEQ0H9ogV76dfmSzg5FxXbTQ+mDuDuzj2/ezD77vu9ZbmzWiMxwfc91tmqYLuJ6B19+/xMWl/PJbJ5dX93Ms58X45ufsmEy/GEwGCYpnI7ExjJtgxA0LxvDDVgFupGwXXMJwhqvVtqCMMAqzVmxA8s+YSRQEsZy2fQMvGd5CnbN3TEp5ApmOatqYHUNmPTy1flwEEZgFDBkBI4w5EyC5Ph1QvMUvSFCJe65O4ioWyELtQW2YkLGsCBma622Z2zLduBIGYSvdj92QKrJ15hE34s9D/C5/nX+5mp6PZ6/GcVxf8lyZF5AvbNrJc9RAsel/x8Kej6qKzZU/UejZPeuTPdmFOLZ02p3CjS6qsQy1vyuQWzPw1BcM7uOhcSP2CCJEKXdKYSWbMODbs2Wxv0GWVaKimdZGIYn1I4iMAML+N/PM5DqjqUweZkMPa9kojIRTGEEtx8iuE0+eJ5X8BJQxPxTYPMIciWLCDZmFaak5hST4MUIBrSqtZA2CHyAL3//AT6Ikg4ArwwH2v0L/BBeQOnvbX4A2CPSwQ/pMCZLZelAi+0eohRj97FJSCA8Eio155lTIDjy2Aq7Pooftz84vaiJ+Qpl4iWOD7IbDF/FCf4N/AiSMDwlaG4bLTFvxa3DoCaEtwOnwjMcVLwUsjgTEkqlH8020kvx4xtu125ZCWM5BXDU3RzugBWF5ga1rcQnTmfbgX6e0mh6y8bsUPXHBRxX49fZ5XQyj7ro7OribTab30zG70M62NWVUFXJsSqKtFSC73FJ8zIC2v5XgW2D/fnFWesPZ0mSOHkWsTuWCZmhDQSEgbJiR12nHgcftCSMjpqW/oMMMByvIjvKgyOL6jxP9wR6OIEy6i3dZZwLq3dtAxdHS2op0BaODtYzVZJ7/HPOawuzHSJvJp+Fda3n6YM0Y3XAw2/WOfRpoJ2dOQIOkNj4nbmd+okDLSmVwr+gV3St7CJfC+8cFItkR3tsLcGq2sCS4ww5RfQ94kYEaNiOjPbuAS4E7oZ8pw+hf+xoXikUG+9BWWNZD0T3jL7HnU6osg49uhSPjSfGpcLLFJT+2to67fdPtyHdl/Whb0jDfiVyjsaE0lix4aqxoyHdJ92KulJulpwFxpVihQl07LTC1n9T4nPEcfSeSkwwjvO7y4vJdDaJngp3mgWSCsdHOIff4hVERYFI5hxFQiCSCD8Sm7VqqiLjbgxGMNcN97zWmEr/N7nvvR5fvus5tyFvab2pd/W2d0hhT3Z2BpUTyEXDQ5/2Dq0FGh+9C2HoP+m+F0Ev/qjEKdd/gur7qIczesclGDyJJqH3D0M0bqk="}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["../frontend/uitest/test_ui_server_guard.py"]   # spare ports only, safe with the app running


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

# ── prerequisite: ui_server.start() as on main ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "uitest", "ui_server.py")
    if not os.path.exists(_pf) or "def start(port: int = 47321)" not in open(_pf, encoding="utf-8").read():
        die("frontend/uitest/ui_server.py has no start(port=47321) — pull the latest main first")


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
        # a planned file was just proven byte-identical to its expected base (sha == orig): only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="uitest_guard_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, it["path"].replace("/", "_"))
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
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Nothing to build: this only changes the UI test harness.")
say("  Check (any time, uses spare ports): cd frontend && PYTHONPATH=../backend python3 uitest/test_ui_server_guard.py")
say("  Undo: python3 apply_uitest_port_guard.py --rollback")
