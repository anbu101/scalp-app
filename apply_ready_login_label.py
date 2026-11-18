#!/usr/bin/env python3
# =====================================================================
#  apply_ready_login_label.py  —  fence READY_LOGIN_LABEL_20261118  (DEF-062)
#  REQUIRES: apply_ready_panel.py (READY_PANEL_20261118). Frontend only.
#
#  "Ready for the open": the Log in button's label used the page-background
#  colour, which is transparent on the Arcade and Kolam themes, so the button
#  showed as an empty pink block. The label now uses the theme's solid
#  background colour (readable on all five themes; the click was unaffected).
#  Edits one line of ReadyPanel.jsx and adds TC-RDY-0019 to test_ui_ready.py.
#
#  Run ONLY this script (frontend only, safe any time; build after 15:30):
#     cd ~/dev/scalp-app && python3 apply_ready_login_label.py
#  Options: --repo PATH  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, json, os, py_compile, subprocess, sys, zlib

FENCE = "READY_LOGIN_LABEL_20261118"
DATA = json.loads(r"""[["frontend/src/components/calm/ReadyPanel.jsx", [["eNpLSkzOTi/KL81LsVJw1ktMTk7NK9FRSM7PyS+yUlAqSyzS0NVN1k1K1y1ITE/VVNJRyC9ITM4sqbRSSCotrlSwVzDQM1OwUjBUqK21AwDP7Rfy", "eNo1js0KgkAcB1/lR6cKNz8OEnsoLC0iKejWSf6ui0m6K7tWSAg9RE/Yk9QlmOPATE7iWhp9UwXHekZCSNU5ELrWhmN0JzNmTLC8ZK2pGjL9ZORAtySqrufIb7bHEt4sBIePYVgAeLpTfN6vHzglUXzO0uN2d8jSaJWkWeAFoe/7c4zjZMO8MJj83QdZ/FNUSo7OkLItmd8QtEJkBBUSLva6psaB1eguEjXlssadVGUvssDUHb4k8ES1"]]], ["frontend/uitest/test_ui_ready.py", [["eNpLSU1TyC9IzYtPSSzO0EjSUUjLSU0tsfXLz0vVUaiyjYr399ZRSLR1jPd3c9NRKLINAguUZ6aUZNgampgY6ChkpGamZ5TYGhoaADnJOfnJ2bYBQa5ATSm5mXm2IUWlqZpWAJK4H/w=", "eNoljM0KgkAURl/lYpuCWXhFIoS7mEgjEo2hTW0Gc66N+BfTRNDTJ7X8Dud8hhuYHjxqUz3t8iag6Zk9FdPIAj501eVRQEVSl1kmwJH6gXdrvCWM41CA5fZuPSGG86j7qe7opNI5MkM70tm95iNveWAKTOW6YJUAwAJUKncXnZf7Q6FzuU1zHYXRGhE3yV//ApicLo4="], ["eNo1jbsOwjAMRXe+wvJUpKrsSB0YmBhhq1BkGqsJtHGVGMpD/DuhiMlXV9fnAADst4dqil7ZKN+1OCcJlb0OYypeaCm5k1C0pqeHXBXXgINYjgFLQHU88LeyFC/4Xi4XGQet3qGGUxV4Mq2EGXrzPI0StX7h5K26/DTfDHHsO/cF/8K7BPUDPyWw8bbGTfK02kl/ISX8CcYu87NlNozUcfHvK8MxSkzlnFvq+5SnzbGE5vgBNS5NJQ==", "eNo1jTELwjAQhXd/xZGpQqm70MHByVG3IiFtjiba5kJy2mrpfzdGnN7jcfd9AADn46WagmWUjDMXt0iu0o/Rx2IRWkXTkgpaDupFDxZ7ECNpDE6UINjgiGnKuW63m0SDjmeooa0cTrIjl5lPi5OnwPUiJqvZpJ+ciWHQ9ubL/ZW1BLYjvsmhtLoWh2jV7kTDXbESP4HvEz9ZssGrHov/XkkMgUIsc+/UMMR02lxLaK4fORJNUg=="], ["eNpTUFBQSM5ITc7WUApx1g1yidQ1MDC0UNJRyMsvUUgtKirWAQCWZgk5", "eNptVM1u2zgQvvcpZtVDyUZWJLlOE2fVRX6LAmoLtL20rmFQFC0JpkSVotykaYBee99X2Bfrk+yQlp11sYAOnPmGM998miEAwGP49fcP/ODd1dnlx0X69uWrN4v07PwqXcRhfBRF0TGQy6vrUXgU0ymYUkCqCqgakCwTEqoOtGA5y6QA1YBYC31ro2oB5ExzlgvoSvVV5MAQrVtzC23VrCCTiq/oI2QA6XkKCXieRwiF5AXcAVdNZyBDb654X4vGBF96TPxeSMGN0uTJLGeGjWzl2+Sb0Cov2Rw2TiM6U+WJw0ZSFVUzf0JPoVoC+SOjyNb0uoGml/LUlYehHO+wXiHMharb3oj8vbmVgmR4dYO3CJPOMSRdUDPDS3I4+/w5D+YHhwWF799hNqcItORNX2dC0/38sq9tBr7X40z7UPioxhwxHnSy4oKEPow3ich6iF7DYQLxZHK65b+GPxMIg3B8Eh/DXxaHKA5OYpjCa2bKoFVf8TYc2JjJhFrYHnyIg2fY0j3dZQqDOIqP4CloF/08msRoFJurz2NrZBJv7HezLJBwS3gXcCWVptjDzpMxviq06pv8wmFbBRkGLItAiqYwJbyAMRJfFrPxHElH++nTCGNRMbIsMHUaD1ZWPPC+AyNuzBSyoGoaoT+g4WO6KWw5WUrO+o2PD0y2JZsC84eaAJqZSg3SuUhCiDNqdkPSyFKgg5hWywGrmt8ximpFoZM7RMlQZ0Jxsl2djOXYxt29M5ZK45rYPSJezvTK88GTVVEaezBCY24m7XmlJKvtgblt8uh0R5qbGx9aK7tqRbPIWVeSzIdvyafF2+trf7OGiSnp7oayv6gIxJrJnhlBcPUeQNyQRhkgClc1BzXzrL7ePOiMrlq7mrikm933thFOSG+O/zIMTrZOp6V1JjjGD2wHCWamtLOudn6hdQcHjtcCz0p3/20w4FJ1gmxY8lLwFfE+XIzeXX4chWF0gsJYzpgX/7239zLtniWiWobPh29Hy2iG0/Xr5z8wnkbUvlhWfB+c9KjYILwPTnaclM0TRu6wwj316KP/53E88LC9+P8C+yWJTg=="]]]]""")
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
            die(f"{rel}: the line to fix was not found. Send me: grep -n 'ready-login' -A3 {rel}")

if not plan:
    for n in notes: say(n)
    say(f"✓ {'nothing to roll back' if rollback else 'already applied'} — nothing to do"); sys.exit(0)
if not rollback and "--allow-dirty" not in args:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + [os.path.relpath(p, repo) for p, *_ in plan],
                            cwd=repo, capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        ok = {"frontend/src/components/calm/ReadyPanel.jsx", "frontend/uitest/test_ui_ready.py"}   # apply_ready_panel.py output, maybe uncommitted
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
    + ("" if rollback else " Next: ./desktop/build-scalp.sh both, then restart the app.\n  Undo: python3 apply_ready_login_label.py --rollback"))
