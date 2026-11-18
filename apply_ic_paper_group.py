#!/usr/bin/env python3
# =====================================================================
#  apply_ic_paper_group.py  —  fence IC_PAPER_GROUP_20261118  (DEF-065)
#  REQUIRES: nothing. Backend only.  (DEF-065)
#
#  Paper Iron Condor (and any paper strategy that books legs with a group_id)
#  showed only its NEWEST leg on the Dashboard card's in-trade panel: after an
#  adjustment the card followed that one leg ("holding B 25000PE") instead of
#  the whole condor. The Paper trades loader (/paper_trades) never read back
#  group_id / trade_class, so the replay fell back to "same entry minute".
#  Live reads the trades table with group_id, so it was not affected.
#  Adds the two columns to the loader's SELECT; adds a check to
#  test_live_replay.py. BACKEND — restart needed: install after 15:30.
#
#  Run ONLY this script (backend; install after 15:30 — the backend restarts):
#     cd ~/dev/scalp-app && python3 apply_ic_paper_group.py
#  Options: --repo PATH  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, json, os, py_compile, subprocess, sys, zlib

FENCE = "IC_PAPER_GROUP_20261118"
DATA = json.loads(r"""[["backend/app/api/paper_trades_routes.py", [["eNpTUEAFxSWJJak6XGiiCslFqUDxlPjEEhQptyB/X4WCxILUoviSosSU1GIUWf8gF9cgBadIhdS8kqLK+JLM3FQFF9dgZwCZJBuc", "eNplj8FKw0AYhO8+xRxb2IQmaBE91SYVJZIQ6cHTss3+TVfT3bD719KbT+OD+SQG9RI7t5mB+RhgrMCKSVz8S9F4GnItFZ93rXeHXhotfm0U4WEpq0WV1/K+LteVTGfpPEmSa0yyfBXN5lfTG3TUBrgtnCU0zmrnBYxtuhiGA5R+PQTek+UzGnulSTadCuGPNkzBu44wKZI4Li4FinSRPX59fE5vcfSGmSw2J/COQLY1loLAUQVYeieP4ZnGRjVvI9SqLp/Qq568/CGGUVvWWV7j7mXYY3+SbPaELH9efgP4VFnb"]]], ["backend/app/backtest/replay/test_live_replay.py", [["eNodyLEKgzAQBuDdp/i5KQEtFlwdSqdCx77AkRxRIknwzsG3tzh+X1gkZEdc8Cq8nbYGRZYTq6JUw1ZrloijoRbYIvjtHEXROAn12KU5srv+os97+D7JP5KYIzW2Q8ljnjGNk+8up5khtg==", "eNqVVNtO20AQfc9XTJcHvJKD4jSgNlKEIpICFS1WEnpRhKzFniRubK/Z3QiiqhIfwTf0w/iSztrOjaqltRKv7Z3LObNzJpxhOHeYyKCbiWRp4lDDHJcQa8ikgUTKOUawyEFmYGYIIyUi1JCLKTIXFOYOM8UnemPnJ/ULj/GDKRqHaSPMQjMOnQ60Gi1e24Onxwf6wflJ4Hf9/iA4HVxe+UGz0TzyPO8NOL3+u3rj6JC3QUBhAaHMIqkgzihjjiooc8FUyUWu4YZwmuotiCMwUsLTwyPIHLM4m1rAlJWuRBhUIKJvC21SzIgXTsERuqDUE3p2I4WKILS3SKLmkJKlpZeIZWF0N5MJVnDcojRmJso4IpEZ1iYEM5i7EOhlau9xhLREscpoIZBEwXGYX5bIheHXD8MxEyYNQmTXVLuTvq3g8OxyMKIHowM5cXrNlgtvXWg2OHeh8m7ueOelt/+Sdw1evqoEr9cJQrzbwnZx+fH079Baa8+88vRf8PwPWM1u7/0/x/c8Fyg+bxcJyt5R8s6xJ0RtGnyyZ/CHo3Ihv3eqPaOJ3Rcbcf1tO8uh/dsC3Jpl5+iwwSnZHoSJ1Bi1SUdITVr2nm3Jei51bGJSksbQrppMUWQFxq7vH+A9hguDDrvye91Rf7fnh/3RutM7+/6pt09Y7FYQJkLrzjF8PusPdpys5TERJdoHOk9iUmWd8bFHlQvmnNdszlCmKW3wmoLOcz1v6s5rYTkotvTfLlORnFcyLeDRuCBdrjU5UTItVOp57UbjmQbZqgFUOTPknOaFyCLQUhmMHDVmRSB2PWZxpNl1MU3GGxltJLHTJJtGXjUmUa5ylAFd+P6jGlQ2MId4sgMCE42g1rSfHn4mJTGalKvxQgRWowC3iDH3NzoZ3hsnATsjEjsJtnlRFCJm8yeWJD0SxW2DXMWpUMvA7vExo+lMNq86u8fzCzQir4Y="]]]]""")
BUILD = {"backend/": "desktop/src-tauri/backend/"}
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

plan, done, notes = [], 0, []
for rel, eds in DATA:
    targets = [(os.path.join(repo, rel), False)]
    for a, b in BUILD.items():
        if rel.startswith(a) and os.path.exists(os.path.join(repo, b + rel[len(a):])):
            targets.append((os.path.join(repo, b + rel[len(a):]), True))
    for p, build in targets:
        if not os.path.exists(p):
            if rel.endswith(".py"):
                notes.append(f"  note: {rel} not found — the UI test was left out")
                continue
            die(f"{rel} is missing")
        cur = rd(p); new, state = cur, None
        for ob, nb in eds:
            o, n = un(ob), un(nb)
            if rollback:
                if new.count(n) == 1:
                    new, state = new.replace(n, o), state or "edit"
                else:
                    state = state or "done"
            elif n in new:
                state = state or "done"
            elif new.count(o) == 1:
                new, state = new.replace(o, n), state or "edit"
            else:
                state = "missing"; break
        if state == "edit":
            plan.append((p, cur, new, build))
        elif state == "done":
            done += 1
        elif build:
            notes.append(f"  skipped stale build copy {os.path.relpath(p, repo)}")
        elif rel.endswith(".py"):
            notes.append(f"  note: {rel} differs — the UI test was left as it is (the fix still applies)")
        else:
            die(f"{rel}: the lines to fix were not found. Send me: grep -n -A3 'state,' {rel}")

if not plan:
    for n in notes: say(n)
    say(f"✓ {'nothing to roll back' if rollback else 'already applied'} — nothing to do"); sys.exit(0)
if not rollback and "--allow-dirty" not in args:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + [os.path.relpath(p, repo) for p, *_ in plan],
                            cwd=repo, capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        ok = set()
        dirty = [l for l in dirty if l[3:].strip() not in ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        pass
written = []
try:
    for p, cur, new, build in plan:
        if not rollback:
            open(p + ".bak-" + FENCE, "w", encoding="utf-8").write(cur)
        open(p + ".tmp-" + FENCE, "w", encoding="utf-8").write(new)
        os.replace(p + ".tmp-" + FENCE, p)
        if rollback and os.path.exists(p + ".bak-" + FENCE):
            os.remove(p + ".bak-" + FENCE)
        written.append((p, cur))
        say(f"  {'restored' if rollback else 'wrote'} {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
    for p, _c, new, _b in plan:
        if p.endswith(".py") and not rollback:
            py_compile.compile(p, doraise=True)
except Exception as e:
    for p, old in reversed(written):
        open(p, "w", encoding="utf-8").write(old)
    die(f"failed ({e!r}) — everything restored")
for n in notes: say(n)
say(f"\n✓ {FENCE} {'rolled back' if rollback else 'applied'} ({len(written)} file(s))."
    + ("" if rollback else " Next: ./desktop/build-scalp.sh both, then restart the app.\n  Undo: python3 apply_ic_paper_group.py --rollback"))
