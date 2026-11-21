#!/usr/bin/env python3
# =====================================================================
#  apply_paper_stt_side.py  —  fence PAPER_STT_SIDE_20261121
#
#  DEF-075. close_paper_trade() called calculate_option_charges() without the
#  trade's direction, so every SHORT paper row was charged as a LONG: STT 0.15%
#  on the buy-back price instead of the sell (entry) price. Seen on every
#  closed TSG_V1 / IC_V2 / TMA_V2 short paper row since 22 Sep 2026 (e.g. TSG
#  27 Oct 22700PE 102.00 -> 5.10: STT 4.97 instead of 99.45). Losing shorts
#  were over-charged, winners under-charged. The direction is now passed;
#  stamp duty keeps the backtest charges_model's convention, so paper and
#  backtest charges agree to the paisa. Rows closed from now on only; existing
#  rows are not rewritten.
#
#   backend/app/db/paper_trades_repo.py               direction= on the charges call
#   backend/app/db/test_paper_charges_direction.py    NEW TC-PCHG-0001…0006
#
#  Run ONLY this script (after market hours; the app picks it up on its next update/restart):
#     cd ~/dev/scalp-app && python3 apply_paper_stt_side.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "PAPER_STT_SIDE_20261121"
ITEMS = json.loads(r'''[{"path": "backend/app/db/paper_trades_repo.py", "orig": "881e38f3ef9ce525798d40c0bfe62a13c12ba7381da17b5ea615a2f6bd0b5879", "new": "6545b3753bd4fba8355b62b8123f4ea49638110e427113fd1ae31ff9d6c9a748", "blob": "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"}, {"path": "backend/app/db/test_paper_charges_direction.py", "orig": null, "new": "f0e7a059d1f0eb926633e0200087045fd59d30c034e987ed3365452638496b98", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["backend/app/db/test_paper_charges_direction.py", "backend/app/engine/tsg", "backend/app/engine/ic", "backend/app/engine/orb/test_orb_manager.py", "backend/app/engine/lvb/test_lvb.py", "backend/app/engine/emc/test_emc_live.py", "backend/app/engine/brk/test_brk_manager.py", "backend/app/trading/test_fleet_modes.py", "backend/app/engine/test_fleet_live.py"]


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

# ── prerequisite: close_paper_trade with direction-aware gross P&L, as on main ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "backend", "app", "db", "paper_trades_repo.py")
    if not os.path.exists(_pf) or "effective_direction" not in open(_pf, encoding="utf-8").read():
        die("paper_trades_repo.py has no effective_direction in close_paper_trade — pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="paper_stt_side_")
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

# ── 5. tests (scripts/run_suites.py: runs pytest files with pytest, picks the backend venv) ──
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    say("running tests: python3 scripts/run_suites.py " + " ".join(TESTS))
    try:
        r = subprocess.run([sys.executable, os.path.join(repo, "scripts", "run_suites.py")] + TESTS,
                           cwd=repo, capture_output=True, text=True, timeout=1800)
        outp, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        outp, rc = "timeout", 1
    say("\n".join("  " + l for l in outp.strip().splitlines()[-30:]))
    if rc != 0:
        restore()
        die("tests failed — every file restored")

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
say("  Backend change: build/release as usual; the running app keeps the old code until it restarts.")
say("  Check: cd backend/app/db && python3 -m pytest -q test_paper_charges_direction.py")
say("  Undo: python3 apply_paper_stt_side.py --rollback")
