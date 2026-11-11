#!/usr/bin/env python3
# =====================================================================
#  apply_order_latency.py  —  fence ORDER_LATENCY_20261111
#
#  ⚠ LIVE ORDER PATH (Zerodha executor) — every strategy.
#  2026-11-11 09:20 Velvet LIVE: signal + wing ready 09:20:01, wing order
#  logged at 09:20:07, short at 09:20:12; the short slid 231.75 → 223.15.
#  Icarus at 09:18: legs at :04, :07, :10, :13.
#  Cause: before EVERY order on a symbol this process had not traded yet,
#  _get_lot_size() downloaded the whole NFO instrument list from Kite and
#  cached only that one symbol — one multi-second download per new strike,
#  per leg, every morning (and on exits after a restart). Proven in the
#  suite: old executor = 4 downloads for a 4-leg condor.
#
#   zerodha_executor.py
#     * lot sizes from ~/.scalp-app/state/instruments.csv, read ONCE in a
#       background thread at startup (off the boot path)
#     * Kite download only if a symbol is missing locally, or if qty fails
#       the local lot (a lot-size revision) — and it then caches every
#       symbol of the exchange, so at most once per session
#     * lot size only VALIDATES qty (it never sets it) — orders, prices,
#       limits, relay and sequencing are byte-for-byte unchanged
#     * every order logs where its time went:
#       [ZERODHA][LAT] BUY <sym> lot=..ms price=..ms send=..ms total=..ms
#   test_order_latency.py (new, 14 checks — VET + IC replays on a fake
#     Kite whose instrument download takes 1.5 s)
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_order_latency.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "ORDER_LATENCY_20261111"
ITEMS = json.loads(r'''[{"path": "backend/app/execution/zerodha_executor.py", "orig": "e9e7d61d53ce4cad47ad94b608b31bd1cf23a743951bfd45e3bb63013d99a42d", "new": "d029aa947eaeb7cea88f7f7538a7db0d97aa6b78993c4c2600edbaf6f34e5387", "blob": "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"}, {"path": "backend/app/execution/test_order_latency.py", "orig": null, "new": "c56b9f38fd679f4d8109a4c582032e7dd5497c5d823f66522868372b48492470", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["app/execution/test_order_latency.py", "app/slippage/test_slip.py",
         "app/execution/test_execution_router_contract.py", "app/engine/lvb/test_lvb.py",
         "app/engine/tsg/test_tsg_e2e.py", "app/engine/tma2/test_tma2_e2e.py",
         "app/engine/vet/test_vet_e2e.py", "app/engine/test_entry_fast_confirm.py"]


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
tmp = tempfile.mkdtemp(prefix="order_latency_")
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
say("  Undo: python3 apply_order_latency.py --rollback")
