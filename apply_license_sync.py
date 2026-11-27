#!/usr/bin/env python3
# =====================================================================
#  apply_license_sync.py  —  fence LICENSE_SYNC_ADMIN_ONLY_20261127   (on main 3b3d79bd, v12.0.22)
#  TD-114  License-server strategy lists back in sync with the app
#
#  The license deploy's gate (check_strategy_sync.py) failed on main, so every
#  license deploy needed SKIP_STRATEGY_SYNC=1:
#   * Tripwire (LVB_V1) is in the app but on no licensable list. Anbu 27 Nov:
#     it is only for him (his ADMIN licence covers it). It is now ADMIN_ONLY
#     (gen_strategy_defaults.py): never demanded on the lists, and the gate fails
#     if someone puts it on one (it would become licensable by accident).
#   * strategy_defaults.json (the Strategy-config TEMPLATE on the admin page,
#     applies nothing by itself) was from 17 Oct: IC_V2 lacked
#     hedge_close_when_flat (true) and VET_V1 had hedge_max_premium 3.0 (5.0).
#     Regenerated from the app = what Anbu's Mac runs and what friends get.
#     No machine's config changes.
#
#   license_server/gen_strategy_defaults.py   ADMIN_ONLY = {"LVB_V1"}; skipped in the template
#   license_server/check_strategy_sync.py     ADMIN_ONLY honoured; listing one fails
#   license_server/strategy_defaults.json     regenerated (2 lines)
#   license_server/test_strategy_sync.py      NEW  TC-LICSYNC-0001…0005
#
#  Run ONLY this script:  cd ~/dev/scalp-app && python3 apply_license_sync.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "LICENSE_SYNC_ADMIN_ONLY_20261127"
ITEMS = json.loads(r'''[{"path": "license_server/gen_strategy_defaults.py", "orig": "7aa77317289bcde99257882869025b9c2478052301fa89325ff88d76561809ab", "new": "327e12840c6c10c1cac983963556ceead902414fca10187202473b19706c34a5", "exec": false, "blob": "eNqdVt1O40YUvvdTnHovalfBNLRqpVS5oBDYtDRBiaFCCI0c+ySZxZ4ZzYwJEULah9gn3CfpmXGCCT/Sbi0UJTPn9/u+c8yHH/Zro/dnXOyjuAO1tkspfgk+QMlzFAaZQX2Hen+BghmrM4uLNStwntWlNYlaB2EYBkcnp2x8OZhMhscD+Pr5C2gke3TW8Nrpk5EC5lKDXSJkRcUFXAyTIBjck2luDRwPTg4vzlI2TSeH6eD0ih2NRyfD0ynMtawaL6VgluW3KAqIDBeLkhLJWucYdwLKyJXxdnPNyWRPrgQWUEqKrTK7NBBRgCSXYs4XiTtmqyW3WHJjY8hEEVAhBTK8x7y2XApWyQIhksruUbElEiAdENKCxUqVrkshuaHczhlWmmKZ4AWAb+NAbU9q0TY2GZyPYTIep7BaEoLk+D4a+TITCzQd5ygCjQWqUq59mL+m4xFkpRQLw6nypgRiqxcEQM/5VfpxPDo/TD/2tyhuiP8u2nmlpLbg+gh8B7lUa9icFojK/W5uHOwln20vz+ln0Nw8Y+IpUSkJfr01fg+AV/67TG7dz8bplLlep0Ewvkih77NHjM15iYzFico0Cgv7EL7NURjQOHz98pn+4Gx4NBhNB2x6NTpih8f/DEdsPDq7Ygc/H/zW7R78vrWL0uO9bvfXeCt/jk6QmQVNbEtRrukDDsWs/tGAD9MAnyOlisKfQoKS4PciruIE0iWugcqEESmjoSibldjzIiRJPhEMf4/G/45aqIbH0w6FdGaZaIaN1TxZ2qok/XDlZNyqeIkaEzrH/LZl3axF7iJrzApXEDcJRUwlyPmcSJICwcrNpJke5SiAW3dEgjJ0t5Lg6DB+NjRW1Jgz8LmCFkLi5SE8u/yTXXbDR9IopaAxXnFq2m0UhxUQviN514PQI+hWSOVGb0UMBUQZMCczIq9Ei5GcfepAIa3FIu552RtcGErTnCVGldxGYRLG/jKvNd2Rk//lYpN5g+3CXPf2ujdNEPfwOXDaOsZmxFhEnh3fYtxauMfq9e5Bm4Y+r7mwEcWOb3Zs8D5HZSG6zMoaB1pLij0UBd777/HrgBptrcXTMUn/dXEFz18W91RHssCmjmchDO4av8hBGZw7N8SGeN+0zGgI+w1+BF/wbci9Qm2LlQsX31BAl9Tp45Y2jPcGs8wU/rF5lxikWcuAFJ3jUpa0SIL/Ba7KjAm+FVIHpJLKF9nxFcYbTVYZF9HGUtYOkIfHVmK86EA+9zp7b80ltM8qE8U78iNH59OOTw/efL53ce0iL4Xlosa2Szc9m70eUdmtZnLfffjWezPc4rG1dY0rV327mRsROjCub17QsDPTeQdUG4jgvCYnp4ncH9J6T/zbl1m8t5F/wRZ1pUxEph1KWdCi7x906H8Fbdktrk0/1TXGTUilnczm4UpL2oUPFOwRoocShfOOH5+t8jh07BINjImsopcI9PsQMua4ZixsGmiID/4D60/zKA=="}, {"path": "license_server/check_strategy_sync.py", "orig": "69b03ec1b19b1861c2f290fe179ba75afe2f25cec74ae6bb0f8325e7d9cdfccc", "new": "5239aaae41ce82c0bcb03a0e200ab1e0a91fe21ed7f06e3b66f0b1e1a228a37a", "exec": false, "blob": "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"}, {"path": "license_server/strategy_defaults.json", "orig": "5b149a55c87b611e7fde6739959ad3b2b2a25d3d63461bcaf0b2ae63b40eebe4", "new": "23dddc536b5df6093f75ee13cc1bf1b2b581c028b11166b803b7b1638888713c", "exec": false, "blob": "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"}, {"path": "license_server/test_strategy_sync.py", "orig": null, "new": "19427846e19f8f8a12dd10b5fec1af4bebf850ee2ca6794b048feed3780df276", "exec": false, "blob": "eNqVVt1u2zYUvtdTHKgXkVpFtZ1kAwz4wnPczqiXBLUXoPAMgZFoi4tMqSRVxwgC9CH6DHuwPskOSUlWEqfrjACheP5/+J3zCjIWUy5pJKn4QsVbRaWKpBJE0fUukjseh8XOeQXfv33FP5hORuOL2TiafboYRcPzPyYX0eXF9FPU6/R+6XZ7v9Z83vz8uNs99WE+OkYZzX7c6XS637/+g//OUOE8pbVtSGiR5bsjCWs0C16c0vj2mRM+kDVhXCpQKEqKog9zwYotExTVedPr36Lrrg9MAkk2jB/nPNvB96/fgFMMrLJFbjIagMyry4RuCE9oYvjwpFWjslll+jjO+YqtQdFNkRnXGqcSuiJlpmT4t8y5D/RzSTJZe4aR1PQA1W1TFqdABNIyNL1NiYIhvylBlFyCNxlF1z1IabKmUZzlWIttSnm0QougRIn+Xo/nGBtqskwbchcVgm5YuYEzP8R7gGKn0pyf/FQ5wfy8lcg3xmNBixxEnivfYZsiFwpYXp90ePVZpqViWfO1k/VR52fFMuoYlZg0Re9Uxm6goguaYJVi7UmSl8qyFUSlLZ4r/HSc38cfxzAwH14UaZ1R5IeCyjz7Qj0/LDCLXDloO9TyIfYDFcrrYE2V8LS07/+IWimAt+DekPiW8sT1m6gP9B0QCSObL3gFPP9M+jA+7fRqkTUW6llPaKH3z0VM1Ngdoe2qsJHLcpJgM1Yqz8fvhn9O59Fs/nE4H7//FI0uL95N3s+eqnPeDSfTWQCXHzBhi2UAi87ScRx0wgbi8QDiAJKB6/p9R3vDkGJP+nf5AQXgzQC6zVUhGFfeygW4Gs7Q4D1/cH1DpZmke1FjOcRQMH8e9w/Iaw4jD7C418lP/EX/tNNZPixRo/USu997/fp2W3l3U64wEJaH+PoYX08uPat4y1T6tIM8ZPb3/ogYJUfhBuHBajQUQVUpOBIDrTtcU/WFZCV2EdrXl6gHxbQXvmMz5j5BK/M6LDhZaCqIlFRCzpGCQKMERUzgOcw+TK72BdPyCDAUgcV3A2fv4wA6BmVcxkF3lwt40G6YyxplJMSl0F1ak42r6LVCOnqsn2Soe0Z6pqd1Mx/GJVc/HZJE+jl62OcrfEhpgAC0M/kyn43ES1nomSw0EPgi0qHKx8jWh/9GNrfKjmsh7hDANRk07mp857mCi5xTkzaTjaTcFNLT+dHgLlR0S3dyMEcTvs56i6dKwROmyoKZAKhk4RrP3aVuGs89GIBrRo2WNmIrLIjyrLCNxV0u3GfhuEvj0FnYqaPysBqBsWqNWcsB3D/4P7TelqkMPhNqG/ax/ocLfNLM0cdTlOsCQ91ZsCHiFiflfrj2sRBNXwT6I05ZEZjyELjl+ZYDS5ryuVa3aeoXMc48hBbj+3C/ZDwmaivIoJPQqt5T8ig0/kYky2qcZlQ2z8YSSxamapO5/g8V2YkambgiluyVWALOVT1K9COtB1g9FsPNbaLPHtZixe4GLg5p/fwjLVAyZH/JofbzdXR3HeLZCqaoZcJLHOYZial3hFMGd6XhdFpneTKe6UnhzkbD6ZXpl6MAXmarEhA85u+iyyLuGUjqVfBpHBoc9u5wy51CUSqFKG+6bL+uNc2GCEtaKxse0ckVYRXyrE3HSbLTKmTeAtmefl5dW8KW4oWNZllDahUA1ut/ZrV5a5gL163zcWLUnfxMPm5IgmwJiy1amIs9ZNSkR5fBc1gcnISdfUckNdq3nG5hHiozXp4aL08rL2vcHjxRcrhiZzhOSQaJYCul0UHiJphBTMp1qvpYqz1MMCl1VZrtUoOCDAwLyVqDxAzjVuVOWpWrIq+KdWIu0f8Wh1W2ygUsLHNTWozS00I2WL0OmrU1FBs9rnXKAmBrngsaUSFyUY8Ap95e/uL3R3p/GZ/34QjegH4l4d857hZm7/H1HmVOZiuCI3w7Zl8anx/huuPdm8Xq4e19tWBBRmvJB9+12ym9Y8rrPlGEBf0XQl1RGQ=="}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "license_server/test_strategy_sync.py"], ["root", "license_server/test_portal_users.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"license_server/check_strategy_sync.py": ["LICENSE_STRATEGY_SYNC_20261016"]}
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
tmp = tempfile.mkdtemp(prefix="license_sync_")
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
say("  Then deploy the license server WITHOUT SKIP_STRATEGY_SYNC: ./license_server/deploy-license-server.command")
say("  Undo: python3 apply_license_sync.py --rollback")
