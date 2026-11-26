#!/usr/bin/env python3
# =====================================================================
#  apply_test_tree_cleanup.py  —  fence TEST_TREE_CLEANUP_20261126   (on main f8e720c7)
#  DEF-048 · DEF-052 · DEF-053 · DEF-054 · DEF-055 · DEF-090 · DEF-045 · DEF-091 · TD-103
#
#  Test tree and tooling only — the app is unchanged, nothing to release.
#   * DEF-048  test_cbo_runner_sim.py: a spot-stop exit is labelled SL_SPOT
#              since CBO_PREM_SL_20260830 (3 checks expected 'SL').
#   * DEF-052–055, DEF-090  DELETED (Anbu, 26 Nov): 8 legacy suites whose code
#              is gone or moved — tests/test_indicator_warmup.py,
#              tools/test_trade_state.py, tools/test_option_selection.py,
#              tools/test_full_logging_pipeline.py, tests/test_historical_candles.py,
#              tests/test_zerodha_historical.py, tests/test_strategy_entry.py,
#              tools/test_zerodha_executor.py (also held the hard-coded Kite keys of
#              DEF-047: they stay in git history, so rotating the secret still matters).
#   * DEF-045  DELETED backend/app/brokers/zerodha.py (orphan, 10 x undefined
#              `kite`, imported by nothing) and its two lint baselines
#              (ruff.toml per-file-ignores, check_undefined_names.py BASELINE).
#              TC-HOOKS-0028 now wants no baselined file.
#   * DEF-091  TC-REPOMAP-0012 cap 1,200 → 2,500 chars (Anbu, 26 Nov); CLAUDE.md unchanged.
#  Deleted files are backed up as <file>.bak-TEST_TREE_CLEANUP_20261126 first;
#  --rollback restores them. Build copies under desktop/src-tauri/ follow.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_test_tree_cleanup.py
#  Then commit with `git add -A` (it stages the deletions).
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "TEST_TREE_CLEANUP_20261126"
ITEMS = json.loads(r'''[{"path": "backend/app/backtest/cbo/test_cbo_runner_sim.py", "orig": "90ecbbbd7b500a13d25556f33abde38a8fd53bc68836806a4e57cf70ece9779d", "new": "59f4cf6a65c03d7b7b5ac9f3f0cfb9819aae6f20b1a1d42adbcbf19c67fde23f", "blob": "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"}, {"path": "ruff.toml", "orig": "1623676135ec389fb932c32a0845805639c9eacc91ff9d0179e29d6c5d87fd23", "new": "bcf3c74a7b79f7b8f167b17966248ed754234daa18c51f55f14b3eaf93c0aac0", "blob": "eNptUttum0AUfOcrRvihtmTAkDRpU/WhSogUqbnIdZ+iKF7DIay87KLdJbb7lI/IN/TD8iU9OHblSkUIwTl7ZoY5M4Dtqir2plF4e3nF2+sL37ib5ue319dXs8dskp2kaXq67wxnF9HkOBsFA+TWGutgtNqcwW20F2vQe03oElo05OBr4bEynSpRCanAX7bTXjYUY8rUmMTp6QfHcGalUVIlOuXhyEM6NF1RYyVLshimk3GWHaOWvqcEK7oxz2iE1KMxnMHckaLCz3mOwWjdKllIH/MpX0v9hJoswVJlbCMYojAlfWF1hNqYJTQ9Mwkrc4iiSq7jwAv7RD7ispPM9xVhuzlKJ2HACAVFtC5UVxLXve0IB9eAEbXpLPZHeACVVGxGaykqTNNIj1Y4x5XFZutTQGtPujxAvQ/jQgl+D8cIS3JLb9rE2SLyorMyWYhiyQP/b1bW6B4ufAiCeyW1fwjezelxAyDMP/Ngr3S3tQRXt7vV9e3Lk6O+P4DUz0LJks1qWmGlM2zPkPeykr6GgJKerFBjTs7v0Xbw9F9cqbcOr1jPE1olCsLQku+shum848UyStXpwrPFBzCfsnf+TnMgpKZ9mIbcSRN+ZP3jaBTwDw72yZzlP2aPs2meP55/z7/d/LzbZTc7+Zvdi/ySw/txBG3Qko36rWAh2BwmgaLKn20FG9vWgmkZfGd0Ito2WViz5Dgkv8iashZxu+ljifWBzvmSPZmPsBKOw6zIUxkHfwBUux/t"}, {"path": "check_undefined_names.py", "orig": "c8bcb1fb542cc01bb2ba518bb0380224ddd4a938bebf1cfae12bb582cf4db499", "new": "68daf445b7a2a856207805b7f7ad8187b09e852d5ee318bf800db6f7e24dd4c7", "blob": "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"}, {"path": "scripts/test_precommit_hooks.py", "orig": "6f652194f585a38538237047f9706e33df481ca2dd49586316bf0c5066a07964", "new": "318d576b7cd5e8804c6318834173b433e1d0f8a7be3974a0621f0b86db0a9aea", "blob": "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"}, {"path": "scripts/test_repo_map.py", "orig": "6f6ca2122959155b657ad8669b8a12a9bb6115505dc9b4e8430ccd0487a9fcff", "new": "d2943347d6c51e0582a968e6d9e0070d35e2a77dc64cb25eec40cae34e02dc79", "blob": "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"}, {"path": "backend/app/tests/test_indicator_warmup.py", "orig": "6da6fc2196eda85c747f47ad168c3d9f389433c676d0778b2da910f7f1f406ac", "new": null, "blob": null}, {"path": "backend/app/tools/test_trade_state.py", "orig": "d999cea60b1d20851dfaffaf835737c768a69d62f6b36099c62eb8ecb51b2e86", "new": null, "blob": null}, {"path": "backend/app/tools/test_option_selection.py", "orig": "1e3b89718a5fab88c870e88a88f56344e97e7104d2989ee8951f359d46edb309", "new": null, "blob": null}, {"path": "backend/app/tools/test_full_logging_pipeline.py", "orig": "305a9e5371eb69a96a5f93b3ca3cb0b2e4e79169a76afb4c1a5b3b09e88bc332", "new": null, "blob": null}, {"path": "backend/app/tests/test_historical_candles.py", "orig": "86213882abda529331a01c1bf94e6c61bd8528f8699a521f95acde845dc4dda5", "new": null, "blob": null}, {"path": "backend/app/tests/test_zerodha_historical.py", "orig": "7d7b9a52682bedc11fe5d81a2e42e1c0a04b3d75e29d62a4f3489c5038b3c7c1", "new": null, "blob": null}, {"path": "backend/app/tests/test_strategy_entry.py", "orig": "23fdd2e54ce8e3195d5b0b6ed27bd1bd8cb6a7f589619b3a3100735466fef914", "new": null, "blob": null}, {"path": "backend/app/tools/test_zerodha_executor.py", "orig": "3f2cabda2d0240744d84228d32f9bdfcc90252141dc8b0923741e2fa61214dde", "new": null, "blob": null}, {"path": "backend/app/brokers/zerodha.py", "orig": "a103db56db41f33b5cd714443b2ba95633a0cc1aa5273781fb9e6d03c332beb8", "new": null, "blob": null}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["backend", "app/backtest/cbo/test_cbo_runner_sim.py"], ["root", "scripts/test_precommit_hooks.py"], ["root", "scripts/test_repo_map.py"], ["root", "scripts/test_master_suite.py"], ["root", "scripts/mutation/test_mutmut_run.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"backend/app/backtest/cbo/test_cbo_runner_sim.py": ["CBO_V1 RUNNER BEHAVIOURAL SIMULATION"], "ruff.toml": ["PRECOMMIT_20261117"], "check_undefined_names.py": ["UNDEFINED_NAME_GATE_20260817"], "scripts/test_precommit_hooks.py": ["HOOK_NO_ADD_20261126"], "scripts/test_repo_map.py": ["REPO_MAP"]}
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
tmp = tempfile.mkdtemp(prefix="test_tree_cleanup_")
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
say("  Commit with `git add -A` (stages the 9 deletions): test tree + tooling only, no release needed.")
say("  Undo: python3 apply_test_tree_cleanup.py --rollback")
