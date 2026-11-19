#!/usr/bin/env python3
# =====================================================================
#  apply_ready_hero_fit.py  —  fence READY_HERO_FIT_20261119  (DEF-067)
#  REQUIRES: apply_ready_panel.py (READY_PANEL_20261118). Frontend only.  (DEF-067)
#
#  With carried positions before 09:15, the "Ready for the open" panel was
#  stretched tall (its flex size became a height in the hero's right column)
#  and an empty "The day's line starts at the first order." box sat under it.
#  Now the panel hugs its rows and the empty day chart is left out until the
#  day has a line. Edits CalmHero.jsx; adds TC-RDY-0022/0023.
#
#  Run ONLY this script (frontend only, safe any time; build after 15:30):
#     cd ~/dev/scalp-app && python3 apply_ready_hero_fit.py
#  Options: --repo PATH  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, json, os, py_compile, subprocess, sys, zlib

FENCE = "READY_HERO_FIT_20261119"
DATA = json.loads(r"""[["frontend/src/components/calm/CalmHero.jsx", [["eNrLKi0uyUyrdM7PK0nNK7FSUErLSa3QTc1LUdJRSE8ssFIIDtDLTVGorbXjUoCC6sTizJTUWgQ/OSOzoBjBt3FOzMl1zkgsKlHIycxLLbatBlO1AP27INQ=", "eNpdUMFKw0AQvfsVzxxqW2piehAMSYrEip6U6sVTWdxpd2W7WbLb1hoW/Ai/0C9xG1sEh4HhMW/ezJu3tXVysatq7Ui7DMxKTpggWih6P7eONS5CdoCkeTTCkpkMT4/xisP78gSHaI+jOZcbWLdTVLQtuLRGsd1BItqP/DJ9ngRiGcT1Wim/V0iG+P76DInZ9PrmZX43nT3Mb++f5+OL8WWaplfHdlNvsW2YMdRkcIJgmCZ1ZrHfASs/CNKCYSu5EyPo2gUgSC6FwzDxfze/Cmms/++h18OpkppsbOsV9ftqgKKEig1zIlakl06gRDroiPWGmmDQHhuTzk+wlVdMrSoRPohOrGi74n8Auut5VA=="], ["eNrLSM1MzyixrU7Ozy1ITC5RsFcwNDJQsFIwMjKoVYAKwmVrFfTtuBTAwEY/JbMMwrHRL05NLsnMz7MDALliF0Q=", "eNrLSM1MzyixrU7Ozy1ITC5RsFcwNDJQsFIwMjKoVYAKwmVrFfTtarkUwMBGPyWzzI4LwixOTS7JzM+zAwDX8RfB"]]], ["frontend/uitest/test_ui_ready.py", [["eNolirEKgzAURfd+xeVNCgoWnLvUL5AuIg5JfDYPNYYk0qH039vqdA6HAwDGspkzetzLtunKqrrWVMBvEcqNf/bkFxpwO30KNBSY6CXJwqgQhI9LkmwuIlmGV44XSITmKCMfTcsTbl81B2Tv3/7JKb98AY2QKIY=", "eNqtlO1u0zAUhv9zFWdGWh3RZG2BoqVkaGydmMTHVCakqaoiN3ESCzcOtsNaVZW4CK6QK+HEadnGNI0fVFXi+OP4fY+fYwCApODJV0ouT/zJ6ZXf6/VfkC5UygAr0+Y9JZUkMzhq25kmsy5k5FrYAhKmteBulrBClQZswaFiJZcgDMy5ESl3fXORQ1kv5lwDXeP0jUe8J7g5PIVfP3/gHybj49Or+N148ik+O7+MB73BsN/vHwI9HZ/5veErL7wVvKhzA8Ia0OraAC2VBWM1t+glBcuk9Jz6UgFfVHYFKVuhT6Yt1GWKGoR1m+cQQZUH/DuTNbOcEkIo9SA6gjUkaMdChTNSldQLXtrgW8316jOXPLFK0840ZZb5lhsr0khzlq58p27W8UYgMqB7lQcoqtYlepdy5PaEbWSnHLe/G/VYyl1gF9HFahdIho/IrZs2j0DyMsdD8KE/C3Ju3yr0Jsr8RAoUO8Fw1Lu7ZVL8q5uEyYVfcK1mcL/fJbIRtg2+tbiGnFUhfGC2CHSjhVYPyQrmylq1QOmNqe2X120PK4S9PYpS9/fhIBMadSuNZ3YQNBpwAN9Le6JKi+E8DzYj2HjUw6Nrgfob58EAcc4dDvmUoERk+XUEw15D8RZg5F1zX1W8DLeAZQ1cO8C6cB8wxDj/AzHX2sAzx1KMbaVNK8Uug0Qqw2k7Lc4w/wuxjE3CS+wcYQ9WFybMoKgI1uT9+ZcxCWGKJUYuji/GE/excXUy55nSHHqHYf9lUwrYxhpDxKUoOeAYWNVwvuJ2tzv6ahBvjMUpMwWdo2nJuY3irJWEiq+ZsDEuj82OBfIg2ZhKKxZc1TYa9vC3S/mjdZT8X/J2zCXwBpLbQEDYltojTDxHI6ga8/dRYfIwd+QWasSdt8DwxR1IdrnGDIc3d0sHk94xbqQDc7XcXjE3lxVdJ8WedrD8Bq5awHo="]]]]""")
BUILD = {"frontend/": "desktop/src-tauri/frontend/"}
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
rp = os.path.join(repo, "frontend/src/components/calm/ReadyPanel.jsx")
if not rollback and (not os.path.exists(rp) or "READY_PANEL_20261118" not in rd(rp)):
    die("run apply_ready_panel.py first — this script fixes its panel")

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
            die(f"{rel}: the lines to fix were not found. Send me: grep -n 'aside' {rel}")

if not plan:
    for n in notes: say(n)
    say(f"✓ {'nothing to roll back' if rollback else 'already applied'} — nothing to do"); sys.exit(0)
if not rollback and "--allow-dirty" not in args:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + [os.path.relpath(p, repo) for p, *_ in plan],
                            cwd=repo, capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        ok = {"frontend/src/components/calm/CalmHero.jsx", "frontend/uitest/test_ui_ready.py"}   # apply_ready_panel.py output, maybe uncommitted
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
    + ("" if rollback else " Next: ./desktop/build-scalp.sh both, then restart the app.\n  Undo: python3 apply_ready_hero_fit.py --rollback"))
