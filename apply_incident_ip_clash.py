#!/usr/bin/env python3
# =====================================================================
#  apply_incident_ip_clash.py  —  fence INCIDENT_IP_CLASH_20261120   (on v12.0.9)
#
#  Docs only. Records the 19/20 Nov 2026 feed stalls on Dhivakar's Mac: an IP
#  clash with the OnePlus TV (static 192.168.1.6 inside the router's DHCP
#  pool), how it was found, what was ruled out (Zerodha, Wi-Fi, Kaspersky),
#  the app changes made along the way (12.0.6, 12.0.8) and the learnings.
#
#   docs/INCIDENT_2026-11-20_FEED_STALLS_IP_CLASH.md   NEW  full write-up
#   RUNBOOK.md                                          + "Feed stalls on one machine only"
#
#  Run ONLY this script (any time — no code changes):
#     cd ~/dev/scalp-app && python3 apply_incident_ip_clash.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "INCIDENT_IP_CLASH_20261120"
ITEMS = json.loads(r'''[{"path": "docs/INCIDENT_2026-11-20_FEED_STALLS_IP_CLASH.md", "orig": null, "new": "cc4d27e8b8b0a15688ef7cc9b11f306cf470430e9fe297af67250cc6aaa49eed", "blob": "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"}, {"path": "RUNBOOK.md", "orig": "860de72df34c031faa06fe94033206e171421cdf4a355ecb4deafbc659749663", "new": "7582c9b55827e372b45adf09e72e43a706e422ea3bed121410edc555f5211069", "blob": "eNp9VNtu4zYQfddXTJ2HJkAkX7ZdpG52i9SXxICTGLGTYPti0xItEZFIlaTiqOhDn/oBRb9wv6RnZDvJvtQwbEo8nDlz5nCO6K7Sa2OeguDoiIZC5TXNvbC+KoNutFvSWsRPUicBEVXPKjZWkyjV0kn7LG1flCWFoZW5EQkWpcGJs06nE/QO5zfWaL8PoMuCHL8NPkQ0NanS9KwE3U/o+DdpTZKJk+CHiB6kVZua2n/s3rVxxFeOPn0ibyu5I1tZpVO6FvZJ+iCk23VDiHKFn9I45ZXRjpDgfoLtX40nW+1eTG8vyViaTh5GJBzFRm9UWlmZADc0dHO7AMpzNC2pELoSOXSpdC6dIyt/rxRjmcRIJ2Q2EK5muS6Bt8JLclVRCFtzwWXtM6M/UFiQNyZ3UboHLRNWe3mAQq07+azklnKTuvYXfMLr63A4jPDc5JqLjfQ1GgYaIHojoT5JLda53JWyVUhVeZIvZa5i5ZsitH/FQjrnoUYYizyucnBIaKan2J9sWJbEVGt/uoftu0GGT64hHrdgp/ydjPltjYN3smlm6MCN1jUl0qlUY2NhYBlHwkoIFlYOqUoESgSfmnMo2igUgl3nDVCmlBqpBSI0ScYSRxA8zx0ZjS93Is4U/o3O6+D8uxC8bwaT4ehmsZzMloPpxfxq2ev0Pna7vQ6c+DmY10XpTdGnhcxlakVBLY32TsaLL+RV/EQbuODG0de//uVlzH4S9CjXcwPHwy8oVGsZ+xZ7uHhPIthmoE9CG5+hrjdqhGdyopD0qMKxIuVQqJYRjVgycrvIDU7413OpgRQOEbUnwduxjNhRA1OU0LBPiUqayN+k+96xWchlZkurx4vF4Gp4e7nCDdCIhjCvVAqlKy/dL9zoWrpTYnO4tx7bBtp4XoMdAjxFjNXmAH3PFmmB2hr7RKXwWcTeHWRyr6fQNJlRnAuX9REAjW5ocCdZ37cYQB3vNPr69z80lB73wTXrxWDWnsxOECpBerdXUiQJzOJ+3vVC6PpbMfh200pYTCNQ+JNSK0tqHZ83zCezzyetVUQXEHKzkZZ1zoRNtmzQfWDmiadCChgX5cHMmHbcl4xRXMVkFvHcGiv9vhs7XJ9WcWVzyrwv++32OVL2MQfP2s4lZXuHCeHuOHop8hUdLx4ctaEbipGxcN6dHPpgcYmlhUK7Q2iH8xFPxbF66VPK841hfFneKQrnvtV2KOl/BW6WrXvHQYZXg1kzQPbz7hChdUqZSjMeDw01oVN5SjJKI+r+1Iu6H8+ibtT7sXNyyuwLs+f2XhgUwrNDxdxxnk8YmN+U2eQuMRyjYFzlOW2t8pgZJVxvYtd+veR8t8NuN+x1luPRaLicLy6m0/nr3Y+KJPgPn9dIAQ=="}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = []


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

# ── prerequisite: WS_WATCHDOG_FAST_20261119 (v12.0.6+, which the write-up describes)
if "--rollback" not in args:
    _pf = os.path.join(repo, "backend", "app", "marketdata", "ws_watchdog.py")
    if not os.path.exists(_pf) or "WS_WATCHDOG_FAST_20261119" not in open(_pf, encoding="utf-8").read():
        die("backend/app/marketdata/ws_watchdog.py lacks WS_WATCHDOG_FAST_20261119 — pull main first")


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
        # a planned file was just proven byte-identical to v12.0.9 (sha == orig): only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="incident_ip_clash_")
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
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Docs only: commit with your next release; no build needed.")
say("  Undo: python3 apply_incident_ip_clash.py --rollback")
