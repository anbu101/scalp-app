#!/usr/bin/env python3
# =====================================================================
#  apply_feed_preopen.py  —  fence FEED_PREOPEN_20261118  (DEF-062)
#  REQUIRES: apply_ready_panel.py (READY_PANEL_20261118). Frontend only.  (DEF-063)
#
#  "Ready for the open" showed Market feed "connecting..." (amber) every
#  morning. Both Kite feeds are built after the Zerodha login but, by design,
#  connect only at 09:15 (they wait for the market to open). Before the open
#  that state now reads "ready · connects at 09:15" (green). Edits one line of
#  ReadyPanel.jsx and adds TC-RDY-0020 to test_ui_ready.py.
#
#  Run ONLY this script (frontend only, safe any time; build after 15:30):
#     cd ~/dev/scalp-app && python3 apply_feed_preopen.py
#  Options: --repo PATH  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, json, os, py_compile, subprocess, sys, zlib

FENCE = "FEED_PREOPEN_20261118"
DATA = json.loads(r"""[["frontend/src/components/calm/ReadyPanel.jsx", [["eNpTUFBQsFJIS01N0SsuSSxJVbC1tVVQSs7Py0tNLsnMS1dSsFeIVipPLMpT0kEWf9SwTCkWADTTFBQ=", "eNo9jsFOwzAQRO98xcinVgo0KaKCShGXuhckqLgiFDnNJrUavJW9CHLjI/gX7nwKX4JjaKU9zc7MGwCYzfDz+REPa61X1eZRP2z0fTXP54uiKK4xWen1eb64nB5tNcsOd1YILVETYDyhfrW9wLRCHrIj9NxZF1XBlp2jrYBdP8AI8ptlcXWGP/Akege8GSto2cOG6sX4PUnFB3LTDIFRU/xQKh1FKE+dDZFDTQbHJwA1KuaTj94PScjgyTQDghihhFymyRdJQFmWUP9p6zqFWzwp3qtsZIy5769jeTgtV8+/vP5mFQ=="]]], ["frontend/uitest/test_ui_ready.py", [["eNpTUFBQSM5ITc7WUApx1g1yidQ1MDC0VNJRyMsvUUhKTNEBAIyXCKU=", "eNptkd9qwjAUxu/3FB/ZhZW10jomU+jVrDcDFdnNGKOk7WktSiJJRIcIu9oDjL3L7vcoPsli6mCyhZB/58v5Tn4BgEscPl5txyhJhul0lkymyTjtht1eFEW38IbJKAh71+0BMiqlIoT9QXQDjmxdL02QrU2wFrkUgnJDBUqyQ62hiBcvPoQ0YKdoLSp2YR2Rm62PVYUYckUiLbiee5kPFRd1brxZOrn3XZ54x7ThhtjgLIcPRqKqBWkbiOxuybVJTZ0vUl5RejwdS0H7dtu5qdIaKbnxVpXVHvOyJpDPKV947OEumA0fgzDshja1VXNR2Onp5P2MOAaTC+bOf+rQ4KYhwVALJze0NVb9W3Ws1iE4k/jOvWmlK6hh6WPD6+MdWM4wc3J4cHh7R6XIrloOKr4+8aeKFrydKvft09NIKY2r2ELupHYtlf4B38mXUpP3P4Goz5ovy3jhfwMVbKXr"]]]]""")
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
            die(f"{rel}: the line to fix was not found. Send me: grep -n 'connecting' {rel}")

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
    + ("" if rollback else " Next: ./desktop/build-scalp.sh both, then restart the app.\n  Undo: python3 apply_feed_preopen.py --rollback"))
