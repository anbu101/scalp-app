#!/usr/bin/env python3
# =====================================================================
#  apply_cred_files_remove.py  —  fence CRED_FILES_REMOVE_20261126   (on main 3629b6ea)
#  DEF-047
#
#  Deletes the two files that held hard-coded Kite values, and the dead route
#  that imported one of them. Checked 26 Nov: nothing else imports, mounts,
#  calls or schedules any of them, and the app never ran them.
#   * backend/app/config/zerodha_credentials.py      API_KEY / API_SECRET (old values; the secret
#                                                    has been regenerated since — Anbu)
#   * backend/app/api/broker_routes.py               /broker/positions: never mounted in api_server.py
#                                                    (the app reads positions through broker_truth);
#                                                    the only importer of zerodha_credentials.py
#   * backend/app/tools/generate_zerodha_instruments.py
#                                                    "one-time" manual dump script with an API_KEY and a
#                                                    long-expired ACCESS_TOKEN. The app's daily instrument
#                                                    download is its own: api_server.py calls
#                                                    ensure_instruments_dump() with the logged-in session.
#  ARCHITECTURE.md: the broker_routes line now points at broker_truth.
#  The live app reads Kite credentials from Connections (~/.scalp-app/zerodha/credentials.json);
#  nothing about logging in, trading or the instrument download changes.
#  The old values stay in git history: making the repository private (planned) covers that.
#
#  Deleted files are backed up as <file>.bak-CRED_FILES_REMOVE_20261126 first; --rollback restores
#  them. Build copies under desktop/src-tauri/ follow.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_cred_files_remove.py
#  Then commit with `git add -A` (it stages the deletions) and release (the bundle loses dead code).
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "CRED_FILES_REMOVE_20261126"
ITEMS = json.loads(r'''[{"path": "ARCHITECTURE.md", "orig": "9ebb915fa417c242b7e00fe3eec7c709cba9fa75322f330a53b8aec2a5994025", "new": "a8ef85ff56c82f867bcc515d6268464d2fb4f5652e1785d9d087055c8484200b", "blob": "eNpVVMFu2kAQvfsrRkKqQMLQ0CoHekqC00YxAhGSqr2gZT3AimXX2tmFuqf+Q/+wX9Lx2m5Sn+zx25l5b95sD26cPCiP0geHSdLrweKM7qzwkqwPCFSRxxMoAiq18qCMt7CzwYHUKBxoUaGjaXI1gucH6K9QSD9IJiO4WfLnvSDPL4PkwwjWThTK7CEze2UQ+k9eeNwFPQSH5IXzKYkdDpKPI7h19ogO+t/R2eIg4M+v30BcUyLYHXgX/GEQW50JL2BxMdzCQZVJ2p0sLSmvrCF4B0uTT6HNxIi6DYQCpaIaMG0CsZm5MGKPjkFfFHnrKj4tQqH8FLTd0/gbP+l8ns5mI/6ODdwKeURTQB5lqEO9yDx+cyJRqg2xnOhGZTWFVg9A4zl7aVnNBjT+2TS4cTZ4pAjOLQs1BCm03nKZIbBKPtArS4eiYAL4A2Wo2Y63Mb6JAnEK6JN3SvoG+CnWaSH/ygzBIHcHJxuMx2IIF0GMP9kzFrCt4G6VzTb3D3n2tFll88VLtpm8n1xfXU2uBy1brsUttQKvObvpdARImQSXHpM4I0EhlK5ASIlE4GtkhLwIrQqW/21s6exZFRxitaIv6gkoA8+rvCnbuqkb6H/zqxMs2GLKsGKGTVMywbsMxrDM4s87y/pbzQWjGbTaoazYz/HnV6fqXuLg49xjdPXGo3BWIg4DYac0tmPPujG8qrFwBbombl2bXJQEj1yBezC8dA1bLVgTsDWcoJ8vPvM75A8vWSsyW2HfsL3p2mKiU55oye5LrWFdC0XHpt9a9T1sA3ujDHTgzHWY1Y0raqxJJXNUbKxmix6xglWoiaTRvKdAvrXFVlt55DAf5Pwlbzt1jhFE4RTPdGvKl4TQF4aA55sjupCvijfbmPwFWCt2iQ=="}, {"path": "backend/app/config/zerodha_credentials.py", "orig": "d03d9d460e6468e75af2c041d009206cc6debc71b504f3da72b8df877185996f", "new": null, "blob": null}, {"path": "backend/app/api/broker_routes.py", "orig": "e55ae3cc1c6f9d5c15126a94e5c1059140c5b030807c38c15f1360b2bd4f5d66", "new": null, "blob": null}, {"path": "backend/app/tools/generate_zerodha_instruments.py", "orig": "a23e595fcf7db0d100257ecec16333f444e359644bd7a6776cb002793f7f6061", "new": null, "blob": null}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "scripts/test_repo_map.py"], ["root", "scripts/test_reliability_checklist.py"], ["backend", "app/api/test_acc2_fno_scope.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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

# ── parent fences: the code this patches ─────────────────────────────
PARENTS = {"ARCHITECTURE.md": ["### API Layer"]}
if "--rollback" not in args:
    for _rel, _fs in PARENTS.items():
        _pf = os.path.join(repo, _rel)
        _txt = open(_pf, encoding="utf-8").read() if os.path.exists(_pf) else ""
        _miss = [f for f in _fs if f not in _txt]
        if _miss:
            die(f"{_rel} lacks {', '.join(_miss)} — pull the latest main first")


def refresh_master():
    """MASTER_SUITE.md and REPO_MAP.md follow the suites on disk (a new uitest file)."""
    for gen, a_, name in (("scripts/regression/master_suite.py", ["refresh"], "MASTER_SUITE.md"),
                          ("scripts/gen_repo_map.py", [], "REPO_MAP.md")):
        g = os.path.join(repo, gen)
        if not os.path.exists(g):
            continue
        r = subprocess.run([sys.executable, g] + a_, cwd=repo, capture_output=True, text=True, timeout=300)
        msg = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        say("  " + (msg[-1] if msg else f"{name} refreshed") if r.returncode == 0
            else f"  !! {name} refresh failed — pre-commit will refresh it")


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
    refresh_master()
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
tmp = tempfile.mkdtemp(prefix="cred_files_remove_")
blobs = {}
for it in ITEMS:
    if it["new"] is None:                # a deletion: nothing to decode
        continue
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
            bak = p + ".bak-" + FENCE
            if old is None:
                os.remove(p)
            elif os.path.exists(bak):
                shutil.copy2(bak, p)                 # bytes + mode back (also for a deleted file)
            else:
                open(p, "wb").write(old)
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        _mode = os.stat(p).st_mode if cur is not None else None
        if cur is not None:
            shutil.copy2(p, p + ".bak-" + FENCE)            # keeps the mode for --rollback
        if it["new"] is None:                # a deletion: backed up above, then removed
            os.remove(p)
            written.append((p, cur))
            say(f"  deleted {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
            continue
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        if _mode is not None:
            os.chmod(t, _mode)
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")
refresh_master()

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
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        if not os.path.exists(os.path.join(cwd, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.abspath(sys.executable) != os.path.abspath(py)):
            try:
                r = subprocess.run([sys.executable, t], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
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
        refresh_master()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: some suites need the backend's Python packages; re-run them with that Python")

# ── 5b. JSX parse gate (esbuild from frontend/node_modules, if installed) ──
_esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if os.path.exists(_esb) and "--skip-tests" not in args:
    bad = []
    for it in ITEMS:
        if it["new"] is not None and it["path"].startswith("frontend/src/") and it["path"].endswith((".jsx", ".js")):
            r = subprocess.run([_esb, os.path.join(repo, it["path"]), "--loader:.js=jsx", "--loader:.jsx=jsx",
                                "--log-level=error"], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(it["path"] + ": " + (r.stderr or "").strip()[:300])
    if bad:
        restore()
        refresh_master()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Commit with `git add -A` (stages the 3 deletions), then release with ./deploy-scalp.command.")
say("  Undo: python3 apply_cred_files_remove.py --rollback")
