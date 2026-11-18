#!/usr/bin/env python3
# =====================================================================
#  apply_ready_panel.py  —  fence READY_PANEL_20261118  (DEF-061, TD-030)
#  REQUIRES: 11.9.4+ (WIDE_SCREEN_20261117 + INDEX_PATH_20261117). Backend + frontend.
#
#  Dashboard before the open (09:15, weekdays):
#   1. This week / This month (Live; Paper if Live has no trades this month)
#      show under the ₹0 before the first trade of the day.
#   2. "Ready for the open" panel beside the hero: Zerodha login (time, or a
#      Log in button that opens Kite), Angel login (only when Angel is set up;
#      Log in = the Connections page's Force Login), market feed, engine
#      (+ strategies armed), and "Carried stops at broker" (live Icarus legs
#      carried overnight: GTTs on record vs ACTIVE at Kite) when there are any.
#  New GET /api/fleet/ready — READ-ONLY (never places/cancels anything; reads
#  Kite's GTT list at most once a minute, only for live carried legs).
#
#  Edits only the lines it needs in CalmHero.jsx, CalmDashboard.jsx and
#  fleet_today_routes.py (appends one route), so other changes in those files
#  do not matter. New: api/fleet_ready.py (+ test), calm/ReadyPanel.jsx,
#  uitest/test_ui_ready.py.
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_ready_panel.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, json, os, py_compile, subprocess, sys, zlib

FENCE = "READY_PANEL_20261118"
DATA = json.loads(r'''{"new": {"backend/app/api/fleet_ready.py": "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", "backend/app/api/test_fleet_ready.py": "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", "frontend/src/components/calm/ReadyPanel.jsx": "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", "frontend/uitest/test_ui_ready.py": "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"}, "append": {"backend/app/api/fleet_today_routes.py": "eNpdUE1Lw0AQvedXDOslAU1tDyLxYqH1JCK9iUjYmkkbku4ss1M1lII/wl/oL3GyflWHgZ2Z994M+5LkCN7fXjVhMZ/O7srb6c38upycTs7G4/H5N2YWaKseamKQNQJ5dAbIxWZmw3pJlqsCNpZbFKgRKwhiBcG6aiDplSDkA7ToBaxE4ZKpRY5Lu+YJ4dEyN6rscBUgZb14Qq7rLyCgLvJ+ZH0zqjtEKQewz32fJZdMW0HOVyip+WWMIsNkSYU1KFYe6NKsSEBDuP8shqiZNsORXFfkB2RoNp5YIDalt31HtvpRMcqW3V8wzSKML4/DX+fxadQrGwCL/8qdodYUcGW7gMdgkJlYe0bPKWY6GbzUwc5EP7UyW9c6enZmr+iXZWU0V8H7h33yAY9km3Q="}, "edits": [["frontend/src/components/calm/CalmHero.jsx", [["eNo9izEOAjEMBL+yJUgp+AAVDQU8wkocXYRjR8YRSCf+TmioZker4fcwDxSuNCVQp+ZopriQ9Cu7HXaEBckzYVBsC2qve9ME8s4FZ5wSMrm3v1gflGNJXRknFAq+NV2rkz/49+gUweeI/Qs4BitO", "eNo9jD0KwkAUhK8ypcJCfgqxsQgqWEQRO6vwSF5wcX/C5gWFIHgIT+hJXEWFgZmPYYavnQ+ChlsajKAdXC3aOyzJ2A0HPxkhXsj0Ch3JKZrzl612ChQsN1ggVagpBP0HbzuqJUIbZ6zQkHCpXUyWwpnfjRuMiQ+9bvhLuE0xAkgSPB/3KBzWxepY7YvduqzyNJ9lWTb/dZ/lC74yQCw="], ["eNpLK81LLsnMz1PwySxLDUlM19BUqAYAULAHHw==", "eNqdVFFP2zAQfu+vuFlIS7SQtkhDU6GgDvqAxqACNjRVVevWbmPh2FHstFQhEo/bO/9l7/sp/JKdk7YUjadFVWPfne/u+75z6nV4fnrEH1x1O6c/hr3ORfd8uNfY2282m5/WPqVtJNQMbEoZZ7DkFqxmdNkCtBtYcH733gBVrNrHWtkIDQrjjBVS1up1MJFegHcu5hwWEVcgLETUVCnN1rkAuDQcejTh6QGmmPMUKHNlrZ5xG/HUr00zNbFCK+jxVGh2EonEeDkGWCpNABMdJ3RiofAhrwFulbEw1voO2tAn52ffuyQA0uv0uldkEE6FYp439qF9BF6V4jjsjwfHYdnPcNXhwwM0fDiChlu9jnP4/wnzD7C0mIL3zlX2IeU2SxWoTMqDTVMWO6pS9V3UwHlWgR4uAQ6ZmAOjlu5ajlSyNplQGe8mJe7diQNOKr87387df4GkLyVv5zkwYRLpdCJTye8R9YwmLbjuhSYOwJluU2cgC3wRKIqjsihA3u8Th8rxdLOWmAwCpK8kZWOvdoNBGNPE8/qCBSDpmMtBSWe+SreGO0e4cyoz7tkABCspqp5XoKunhH7Hl+1csKLCWMGuDFZYh3G0k1ssCx+ArCQgAzg+hkaxHi1+LyxOjxFqwmEreprqGGNRMPL8+ESKAOjU4rBNIprOuBkVW73AC6WxULeC2ai1GTMsBi1ofmxUlG47muggDfTTzGokbaxTxtMWjJrJPRgtsZWd/CSUQvFitHZfUSYy04KrzunZt+tQKMXTABK8BHgH8exOXgpYYIpyKWe4HG2pt+LPJFS9NQpjqSdO2TAMb8KYW+pujNTY1kkYZ44rTJWXMhbw5zfkny8vvwwvOl+71ZAWh3WX+n+qjTVbIks4NLdczCLbgv1GY+VTWbxpxGrFvblfNjKN7ZlKcVe8aEeTJNX3Tmkg8PzrJ3E8kzcaO6zjFL0YNhNX+JW8Gz96itrLh8V9p27ozPMh/wtApp/T"], ["eNotjUEKwjAQAL+y7Nn4gGI8+QBBP7BNtnVxmy1JCobSv9uip4GZw1wKhyqWgLKQU+pZPT4tUkOIVMlVLlWix0A6uRdn+/ve7O0xWWKEUpuyX1eIUmal1gEOyh88wYGb5N9j18F0mdIeRpo7eNzPOsK2Xb9t6C5X", "eNqFj8FqwkAQhu99imGPYmoiRYo1LYIeCqVIWxBPMmbHZOlmN+yONUGEPkSfsE/STY1IT10Wfuaf4Z9vJp4yVtYAOoWRxg3pVLxZiY0AiYwRk2clU5GhLqOCnO38jbXvqTDWkADPjab0cACpfKWxGYPYaqpFH1qZKXfaMYbMlhVmDA8gMqt3pREQZp3dd6NLh1Uw9kGCg1rl5pGp9F1g5Bkdh07ejl3CXhfXOg9JQeu61nA83l9B9yZSfVwA25Q/GAkkgDu2vyBtcTOKq5a8VGapJBdjiPv/3nW+piM78QSKsP8w6MH312f48DKfzlbrxfR5/rQexsNRkiS3556mLcMp5Q64IKjQkAav2MOGvJIEiqE3OP4A3/mO+Q=="], ["eNo9Tr0KwjAY3H2Kmzra1EmkiUNnn0AcQvhoPkiT0gRFwvfuDjW95bg/OGBHdZ7XLCc0nd60BfvN50BxLh4GCl2HcbJhmbzdCgJHyro+X4JW1sdMENPnwVHXnQWeePZFV5eW1bqCO4aLwg3DVQn+5pEKetPOjH0mVzhF8wPdIji6", "eNptjjELwjAQhXd/xZs62tZJJKlDZ8FdHEIamoM0KUmoSMh/V2lrHbzl3XH3vXvAXOyqPLmu1TQGRBeFCTzNmiHdMAoZeVqajLLZLSCS/DB5m92kvBHPsDfK9lGjQYWiAGuFGVotfIQhq972t3vGesy/WIZ1jwtZnmbN0Ip6vX3HGfWhwgn1sfqfbQvDyo6mn6wiUKfWNSuDkpGcbV70NFOL"], ["eNpTUICA6uSMzILiWi4oV8HGOTEn1zkjsahEISczL7XYthpM1QIAKIoOUA==", "eNpTUICA6sTizJTUWi4FGD85I7OgGMG3cU7MyXXOSCwqUcjJzEsttq0GU7UABaoSWA=="]]], ["frontend/src/pages/CalmDashboard.jsx", [["eNrLzC3ILypRqFYoLU71zEtJrQhILMkoVqhVSCvKz1VQ0tPTT84HKslLzSsp1k9OzMnVR1GoZA0AFdMXvQ==", "eNrLzC3ILypRqFYoLU71zEtJrQhILMkoVqhVSCvKz1VQ0tPTT84HKslLzSsp1k9OzMnVR1GoZM2VCTEhKDUxpTIgMS81B7dWhBolawUFBX19hUdTGoBIIcjV0SUyPsDRz9Un3sjAyMzQ0NACKgcAWPU5uQ=="], ["eNpLzs8tSEwusa3OLPbNT8rMSa1VSEksSfXJzEu1rYaxahVyE4uyU4GqIHStgr4dAIcbFdw=", "eNpLzs8tSEwusa3OLPbNT8rMSa1VSEksSfXJzEu1rYaxahVyE4uyU4GqIHQtlwIcJBZnpgCV2gSlJqZUBiTmpeYo5OWX+2bm2VZD6FqFxKLc1BTb6rScVKBWhcxix5TczLzQTJCdUGatQjKmO/TtQBhoRbW+lsKjKQ1ApBDk6ugSGR/g6OfqE29kYGRmaGhoAZPT0q8FAE1FSic="]]]]}''')
BUILD = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = ["app/api/test_fleet_ready.py", "app/api/test_fleet_today_routes.py"]
un = lambda b: zlib.decompress(base64.b64decode(b)).decode()


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
rollback = "--rollback" in args
rd = lambda p: open(p, encoding="utf-8").read()


def copies(rel):
    out = [(os.path.join(repo, rel), False)]
    for a, b in BUILD.items():
        if rel.startswith(a):
            q = os.path.join(repo, b + rel[len(a):])
            if os.path.isdir(os.path.dirname(q)):
                out.append((q, True))
    return out


if not rollback:
    for rel, tag in (("frontend/src/pages/CalmDashboard.jsx", "WIDE_SCREEN_20261117"),
                     ("frontend/src/components/calm/CalmHero.jsx", "INDEX_PATH_20261117")):
        p = os.path.join(repo, rel)
        if not os.path.exists(p) or tag not in rd(p):
            die(f"{rel} has no {tag} — update to 11.9.4 or later first")

# plan: (path, old_content or None, new_content or None(=delete), is_build)
plan, done, notes = [], 0, []
for rel, blob in DATA["new"].items():
    want = un(blob)
    for p, build in copies(rel):
        if build and not os.path.isdir(os.path.dirname(p)):
            continue
        cur = rd(p) if os.path.exists(p) else None
        if rollback:
            if cur is None:
                continue
            if cur != want:
                if build:
                    continue
                die(f"cannot roll back — {rel} was changed after this script")
            plan.append((p, cur, None, build))
        elif cur == want:
            done += 1
        elif cur is None:
            plan.append((p, None, want, build))
        elif build:
            plan.append((p, cur, want, build))
        else:
            die(f"{rel} already exists with other content — not overwriting it")
for rel, blob in DATA["append"].items():
    add = un(blob)
    for p, build in copies(rel):
        if not os.path.exists(p):
            if not build:
                die(f"{rel} is missing")
            continue
        cur = rd(p)
        if rollback:
            if add in cur:
                plan.append((p, cur, cur.replace(add, "", 1), build))
            elif FENCE in cur and not build:
                die(f"cannot roll back — {rel}: the appended route was changed")
        elif add in cur:
            done += 1
        elif FENCE in cur:
            if build:
                notes.append(f"  skipped stale build copy {os.path.relpath(p, repo)}")
                continue
            die(f"{rel} carries {FENCE} but not this script's block — changed by hand?")
        else:
            plan.append((p, cur, cur.rstrip("\n") + "\n" + add, build))
for rel, eds in DATA["edits"]:
    for p, build in copies(rel):
        if not os.path.exists(p):
            if not build:
                die(f"{rel} is missing")
            continue
        cur = rd(p)
        new, state = cur, None
        for ob, nb in eds:
            o, n = un(ob), un(nb)
            if rollback:                 # the new text holds the old one in places: test the new text first
                if new.count(n) == 1:
                    new, state = new.replace(n, o), state or "edit"
                else:
                    state = state or "done"
            elif n in new:
                state = state or "done"
            elif new.count(o) == 1:
                new, state = new.replace(o, n), state or "edit"
            else:
                state = "missing"
                break
        if state == "edit":
            plan.append((p, cur, new, build))
        elif state == "done":
            done += 1
        elif build:
            notes.append(f"  skipped stale build copy {os.path.relpath(p, repo)} (the next build re-syncs it)")
        else:
            die(f"{rel}: a line this script edits was not found (the file differs from 11.9.x there). "
                f"Send me: grep -n 'aside\\|function LiveTag\\|data-book=\"none\"\\|{{chips}}\\|useIndexPaths\\|market={{market}}' {rel}")

if not plan:
    say(f"✓ {'nothing to roll back' if rollback else 'already applied'} ({done} part(s)) — nothing to do")
    sys.exit(0)

if not rollback and "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for p, c, _, b in plan if not b and c is not None]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo, capture_output=True, text=True, timeout=30).stdout if rels else ""
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# compile every Python result before writing anything
import tempfile
tmp = tempfile.mkdtemp(prefix="ready_panel_")
for i, (p, _c, new, _b) in enumerate(plan):
    if new is not None and p.endswith(".py"):
        f = os.path.join(tmp, f"{i}.py")
        open(f, "w", encoding="utf-8").write(new)
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"{os.path.relpath(p, repo)} would not compile: {e}")

written = []


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                os.remove(p)
            else:
                open(p, "w", encoding="utf-8").write(old)
            if os.path.exists(p + ".bak-" + FENCE) and not rollback:
                os.remove(p + ".bak-" + FENCE)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for p, cur, new, build in plan:
        if not rollback and cur is not None:
            open(p + ".bak-" + FENCE, "w", encoding="utf-8").write(cur)
        if new is None:
            os.remove(p)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            t = p + ".tmp-" + FENCE
            open(t, "w", encoding="utf-8").write(new)
            os.replace(t, p)
        if rollback and os.path.exists(p + ".bak-" + FENCE):
            os.remove(p + ".bak-" + FENCE)
        written.append((p, cur))
        say(f"  {'removed' if new is None else 'restored' if rollback else 'wrote'} {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for n in notes:
    say(n)
if rollback:
    say(f"\n✓ {FENCE} rolled back ({len(written)} file(s)).")
    sys.exit(0)

backend = os.path.join(repo, "backend")
py = next((c for c in (os.path.join(backend, "venv", "bin", "python"), os.path.join(backend, ".venv", "bin", "python"),
                       os.path.join(repo, "venv", "bin", "python"), os.path.join(repo, ".venv", "bin", "python"))
           if os.path.exists(c)), sys.executable)
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    say(f"running tests with {py}")
    env = dict(os.environ, PYTHONPATH=backend, SCALP_TEST_MODE="1")
    failed = []
    for t in TESTS:
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            out = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, out = None, "timeout"
        last = (out.strip().splitlines() or [""])[-1]
        if r is not None and r.returncode == 0:
            say(f"  ✓ {t}  {last}")
        else:
            failed.append(t)
            say(f"  ✗ {t}\n" + "\n".join("      " + l for l in out.strip().splitlines()[-20:]))
    if failed:
        restore()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if os.path.exists(esb) and "--skip-tests" not in args:
    bad = []
    for p, _c, new, b in plan:
        if not b and new is not None and p.endswith(".jsx"):
            r = subprocess.run([esb, p, "--loader:.js=jsx", "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(os.path.relpath(p, repo) + ": " + (r.stderr or "").strip()[:300])
    if bad:
        restore()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.\n"
    "  Undo: python3 apply_ready_panel.py --rollback")
