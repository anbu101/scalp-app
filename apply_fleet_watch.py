#!/usr/bin/env python3
# =====================================================================
#  apply_fleet_watch.py  —  fence FLEET_WATCH_20261127   (on main 0e590f58, v12.0.25)
#  TD-112 — Fleet Monitor Phase 2 (server side): friends monitor from Telegram
#
#  A watcher thread in the monitor, every 30 s, posts to the Telegram targets
#  (the Market Makers channel), for machines whose "Telegram alerts" switch is ON:
#   * OFFLINE: silent > 3 min during market hours of a trading day → one
#     "🔴 OFFLINE · last seen HH:MM" (a machine that never started counts), and one
#     "✅ BACK ONLINE" when it returns;
#   * FLEET SUMMARY at 15:50 IST on a trading day: per machine net, realised,
#     charges, closed trades, carried legs, strategies; fleet net;
#   * REMINDER: a critical not acknowledged 10 min after it happened, once.
#  Trading day = any machine's snapshot said the market is open (the app's own
#  NSE calendar; no holiday list here). Each notice is recorded before it is
#  sent, so a restart never repeats it. Kite-login status on the cards needs an
#  app change: later.
#
#   monitor_server/watcher.py             NEW  the three checks + the thread
#   monitor_server/relay.py               broadcast(): one notice to every target
#   monitor_server/server.py              starts the watcher (MONSRV_NO_WATCHER=1 for tests)
#   monitor_server/test_monitor_server.py TC-MONSRV-0049…0056
#
#  Run ONLY this script:  cd ~/dev/scalp-app && python3 apply_fleet_watch.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "FLEET_WATCH_20261127"
ITEMS = json.loads(r'''[{"path": "monitor_server/watcher.py", "orig": null, "new": "f4f5733792676f3a8d8257d563f78d40b961a5f59a2846c567edf80d8d141727", "exec": false, "blob": "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"}, {"path": "monitor_server/relay.py", "orig": "e04eadfe93fe8449674a5eb421c469bc4e4b48117785603a571354dfc98be954", "new": "2c161702ac584aa4ddcec6484a33e3728b11f21c1c38170ea04a5045821f5995", "exec": false, "blob": "eNq9GNtu48b1nV9xwiAw2Wgp2UE3rXa1gOvVZp3Y3lQWGqSqQFDiyGJMkVzOyLYiCNinvhYoEqDIQ9OnokA/oH3up+wPNJ/Qc87MUKSsbLb7EMOwOTNnzpz7zXVdZ5FnicrLUIryRpTtUqTRKihW8PrVN/D6m1f4C8/O+v1heP7i4nT4YhAedY4eHh4efWxPveHTB4eHHd9xLkUWSxCIZgWZuIWTwenw9OT4DNrwYvC0PwgH/U/7J8P+U9y4/PJy2D8Pj8/6gyHMynwBUbaCJJMqSlNQuTMUqbgqo0ULZG73Jdwmag5ZDpNcQT4DNRdJCfltBl6WZw+ieJFkPkiVII5SRNM5RE4xzzMRwEmezZIr8Ah9Xqgkz6L0ETC7kEhENoPbucgIuYrKK6HwTObpjZB+13EApJiWQsm2kVfwlcwz3AZYu8qQGuqL0u3CaO1O55EKkxgX7uHRR+6mBY29B4edzutXf3c34xajARd5ClV+LTI65iPY/Xm/Ir0LIpWCBABpMhUZfmsNHkhE10awtpxGafHAnLYt/ZZYZoCo3z7bgorADdP0+o9/hqUUMczykp8isTNsC9UVQyR5F+ULsZhFy1RZ2ZEoNVtQFxjSKpVEGaNGHOeLeYIaWqCakkzIbtPcBv2z4y/Dz9Fqzo9Pnp9e9O8ZHuvX3IY50pIgarIF19oORKkoUR+ORLtBYCSAYJ6lAkmcRmWM5kAmIwH5zq4Enj5iy7U0oSlFpYIXF34Ax9lk6ahlmdFDbC8klXmi3zyPprKFbBNHUYq2F6+giBDlPFnQXcjT2HH5tTAWN6gU6cK1IJNvSgjxJVdZXooYJfQszfMYJRMpBJIKWBynF7AQUiJyCYUoK6ehb0S/VOIRqwXtVyGNaYwQ6FCkJfdDvIu4XVQEOcVvoul1ml/hAxn5baaITEG6xo3LIXpneKndAlmOyjJBd3A8PLNvTiNFkrqCZQHRTOHViaAliode8ImdDI1GMm6kYLKif0ygY5AcSJhoOvDyNEfeGFIuF4sII4mm9ILCCpRRIoV8ROhigkL3pqtXZb5Ec1RzkruOERHIeY6qU8lC5EsVOC7GOocDTRjOlqhHEYaQLAoCijKkMSLHko5j9ti/zXcu7dfLpVgKu9DvIbvVBj5mv5dlmiaToIhKubtXCkQjlaYmjpSge5YWu9anc7VI7YlAhy7MfhGpOaKyR5/jUh+oVUHyN/vH2aoFT5OpasFZYl/8GqWbZLPcAv0e16e4dpzTyyH0qrXnHsskan+Wp9eRilwK8P2TQX94GT49HSAcPerlMhDZTVLmWYB+77mYJS4HvwtrkBhWGBLFnqQodD8wgdXzSTpkGG1wTXhyfd85Oz3pX1z2w+EnP/FIBdg/638yOD6nAPa2kY8est7Ug4eONfYefNSBX8DDjtM/f/HpKa7Xrs1jFJd/+P67f9A7zYRmTv5JJ/XMxpH8u7/9999/wojqhC9xyRYU/Jb+jtSySMXYxUdqu94iupPJ16J31OlgVg3JdbqsxJFUpVbkaJbmkRqPibyNE5KTi7gOhB5vDzmEYQzvwehZhDlj7DgORmsIyXi9ossi9uHBk9p1tJtxl+O3Klddx6YfFCPGP3aNAAmIpVcEhCVU4k55Pnr7DIpA3FGQ93ydodY6lYi7qSgU9PlfQp6LlcIWc1Eixd7MHXHYH8O62MAyI9zRJBVdWIsNGuAOHYhaszLl3O79KBMYYJF9zXDdhtHu6rHXvIB2U0FvbVGfcfLDU3zAW1hz3GZQHzAl4P29+67rB3gvKTyDS1cLpBilMywlgy3ae2UFYxmNWcyJ5OiZTYWHzh0j0z6nZKXv2jzuj/kpvEBR2L5IgBWVFehWGzXK6jULMY3XRtXW2Ed7b+a0LrCRbfS7VlGN2sYUEO62WjJfVp3anBBjd0ebLbi/x0rHlaaezFzcaMZijlokM2ODaTQRKQFwIPWIndgC85nRk8lLFCLoGsJh/m1eM6LLY5FFC1Hpl1Vz/5AdAfMPYZtTitqNKtX33sjCO1DttJx6Sbgbb+5TqRKVWhI1MGhg398whLgZuddJhvokAdtv6wxUBqEhzNw1R0R744CgDvDGgQ5wB/4GHk+erIm/zeP25An851+wZrFuXPgQ0Ll5h6WJOygoLVgjnK2lGrLlajHJ07pZMi1BVBSY/RHdJQN04TFJ+sm6zvjoQN8+GCOPj9sM4NYYskjuC8tUVluNjrq/7nTGe+7aRB1QUqUPNJpF4XFk9qxZWa9lQPqDUbIFmGc5FMx4xx3i3y588Lz7wXn3g0s6taZn/Mf9Q+YGX+VJ5t1xoLijQKE1g/K6843fFGmU7XcbrGlDru+wluT80QQgiPy2C0w7OxQD4bkJoFg8DbGirMpOlXMJZvqChAo4qq+9AssqrO0EFflTqg8T5QeusXxdjJMtCb4oiIstZcSKsMZHR2xujfilMdzLRyMNRNV0DxsRsmCxcvXmDIuN+c6bhhBGegsPwKisqTE88uFxz1bBYxMK8O6PYhNMJG7xo/oG1p7drTTpasURI9syMy0TijJY9nqHW+z6xYZo3uvtOP02MVKlW/nHD99/+xd2yT3x7oAd84A5PbB1OPsKOm6JVXiWiSlVDVxMuw7c/8GAkIrMYwLR93XjwMLwpA/emvjZMFcJmoLPAwUpdMuqO7BznXuBqtAoda3JT6kkbCZFLoKYcNRsy+ZABU+MBh92jLat2FgDW9kiMNGqcaNabSPVbTBWk16VgjAi+n4DSiOxgGQm1TGFsiZKU5qNkGxSvlluOen4GBoPrUXsHOuM/hOEN1T++q/fwtpiKfKCsWy45duqhpuAd7CLvVbgvq1e3yA61q4VkYapRz/k0AQ4uZwsEvVuIY7DGhaZVUDjYrsWvbANtfEtgIHQnf48p2FAtoJbgSLkKj2uAlqjNn7//x1f6LnZxz4LzgwcDvQwwYwrYjFNYiFb2qx5rKYrpaD26ikNqjDyT9FZXXmdFHD8FA1Edz9TUY3OsDSIUPUJhe0pdvBzGgzgf4YGC63yPKh7DVXlVY3EwzIsUQ7ZPNB0ez3odHecg1XW2Ua12RUq1RboddQUK/F0VKsNxzrwmn1bH45/4omFvKKsYrNf3RRajcRb3aAKfnu/ijJvepWAFjyswde69zyhYQqNAPAyKJYqRFuP0HK9XYZbXFSrZkm92Ik4W7I/7JlgUf8xrZVuIZ8t03Q/JbrFqjosBocZgrMHG9OHuMzRO+Oa04bYRNO05DYvr0Xp3evCsnfp77QnwyxKUmpc1+K9ck+D1zF+Pymx2ZxGUnkUlrkRuefODe/74nh48vxHPO7I5/HSLfm8YHND2wfPTKywJ6SJEoKYVYwkrnTyNwOpNpKHLQ+mO58KoYZjApVJVPkgZhpJ2lEghrw0ETGPvdhmG/5ezSu14785/NCEcjsL2x+Mfgavewcf2usk7+IgZAV7fGSPf7yFb7zZL4x5/ExuURn623oGpc7YY1mxW7TAyMmsmv4yyfN0z1iHGmXUZn1iGeACEwL2TV59AmC+qHlHxNS54z9c8Z1wgeDUrz4fnp/tdKlv+HHjRNKQJ7wVk5BG5mFRiptE3BIqVS6Fu/EDQ0tdDi+3JJuBajDQ/1Ggc6UK2W23oyIJqrlfXl610bTWLK5Nm0R3btq9FguhR3+2b3Dtu/MELvOCK7KXLTtc7v3KJ72Wez2mxEYvUktJyfKo03mnYMmtljUItcKaTfhBGNJ0IQz3WAePYKyFWAMlA7jAyKffuZ0jOhiieGtjHz2YqZRMyqWi9SWn/3thpGZ7O5f8Hb+nHCyxNCy8o6bXvgGFs+d2J+j80ja7u/7XZK8iUQ8/R51aKKptInckgu1bdqIfDPnL05GsZ17B0gJF3qsKoTgSizzrEQrq5xGr52+nq1IoLMXLkNvhXQJ1RzNNRWRjhy3b7d7/AEKpNBw="}, {"path": "monitor_server/server.py", "orig": "3e846abe14950ec77ac9b7c0015516673cb9d75913d026ae876f3d18e6bd4961", "new": "92aa5200561037cc06d0c401abcca469e9217504146f8b2bee0d4efa158216a4", "exec": false, "blob": "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"}, {"path": "monitor_server/test_monitor_server.py", "orig": "940c1439af1a665b6b7130df54df75dad322cddae13af9f0237f473410c1909d", "new": "3f1c0e08fccefb59ba880882d3c8f81a2ef40c981c6ac25b41d4b085fcf150d5", "exec": false, "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "monitor_server/test_monitor_server.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"monitor_server/relay.py": ["FLEET_RELAY_PER_MACHINE_20261127"],
           "monitor_server/test_monitor_server.py": ["FLEET_OPEN_CADENCE_20261127"]}
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
                if os.path.isdir(os.path.join(repo, b)):        # the build tree exists: new folders too
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
tmp = tempfile.mkdtemp(prefix="fleet_watch_")
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
        if it.get("exec"):
            os.chmod(p, 0o755)
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
    env["HOME"] = tempfile.mkdtemp(prefix="apply_home_")   # DEF-113: tests never get the real HOME (CL-22)
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
say("  Server only: deploy the monitor (./monitor_server/deploy-monitor.command). No app release.")
say("  Undo: python3 apply_fleet_watch.py --rollback")
