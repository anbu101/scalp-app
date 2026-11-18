#!/usr/bin/env python3
# =====================================================================
#  apply_index_left.py  —  fence INDEX_LEFT_20261118  (DEF-064)
#  REQUIRES: apply_ready_panel.py (READY_PANEL_20261118). Frontend only.  (DEF-064)
#
#  The NIFTY / BANKNIFTY chips are right-aligned by design (traded hero, above the
#  chart). In the pre-open "no trades yet" hero they sit in the left column and
#  floated to its right edge, off the left-hand text. They now start at the left,
#  under This week / This month. Edits CalmHero.jsx; adds TC-RDY-0021.
#
#  Run ONLY this script (frontend only, safe any time; build after 15:30):
#     cd ~/dev/scalp-app && python3 apply_index_left.py
#  Options: --repo PATH  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, json, os, py_compile, subprocess, sys, zlib

FENCE = "INDEX_LEFT_20261118"
DATA = json.loads(r"""[["frontend/src/components/calm/CalmHero.jsx", [["eNpLK81LLsnMz1PwzEtJrXDOyCwo1qhWyE0syk4t0VHIz9NRKMlPT89J1VFIzs8tSEwuUajVVKgGACSDE0g=", "eNpLK81LLsnMz1PwzEtJrXDOyCwo1qhWyE0syk4t0VHIz9NRKMlPT89J1VFIzs8tSEwGChaXJBaVKNgqpCXmFKcq1GoqVCsoKOjrKzya0gBECp5+Lq4R8T6ubiHxRgZGZoaGhhYwKbBOK4Wc1LQS3cSczPQ8BY2ColTd/ILUPIWM1KJ8TQALTTIm"], ["eNrLKi0uyUyrdM7PK0nNK7FSSM7PLUhMLlGwV1BKy0mt0C0uSSwqUVKwgnJT81KUdBRyM/PCM1NKMqwUDICcxAooR8nQwEBVSaG21g4AG50cIQ==", "eNrLKi0uyUyrdM7PK0nNK7FSSM7PLUhMLlGoqVEoLkksKlGwV1BKy0mt0AXzlBSsoNzUvBQlHYXczLzwzJSSDCsFAyAnsQLKUTI0MFBVUqittQMAU/cfhw=="], ["eNpTUICA6uSMzILiWi4FGD+/LLUoJ7GyWC8nNS+9JEPBTsFAQU1NwcY5MSfXOSOxqEQhJzMvtdi2Ojq2FgCSURTH", "eNo1TbsOwjAQ2/kKTx2BGTVlyMQ3IIaonJKKy6VKUgSK7t8BtXix5YcMrGjR5QdVnNFf5E4vG6a5YDXNFiqSmJZEUZP3TKatrBhTnN34LW5CUarLFYcBJ8jCrDv8j9KTMrt32TOJrwEDjug69NZxtOG34kmomHa96Qe6TTXW"]]], ["frontend/uitest/test_ui_ready.py", [["eNpLK8rPVUhOzMmNT8usKCktSlXIzC3ILypRKChKjc8vSM3TUcjNrIgvTk7NS1UAAJLUETI=", "eNpLK8rPVUhOzMmNT8usKCktSlXIzC3ILypRKChKjc8vSM3TUcjNrIgvTk7NS9VR8HUM8nYN0VEICHKN9/Rz8XR2DVYAADntFtM="], ["eNpLTMnNzLMNKSpN1VEoyUjNTbVVSkksylbStFJQUFBWCHJ1dImM9/F39/SL93F0cvWJNzIwMjM0NLSwgigHAIMVEtw=", "eNpLTMnNzLMNKSpN1VEoyUjNTbVVSkksylbSUcgFUqkltm6JOcWpmlYKCgrKCkGuji6R8T7+7p5+8T6OTq4+8UYGRmaGhoYWVhDN1gqefi6uEfE+rm4hSHIQowAIvSAD"], ["eNoFwUEKgCAQBdB9p/jMKiWyhaugw0xoYUxqqYuI7t57AJD38U6t+p60NpyDuX0WfozWNED4XB3jmnGNW5MtiPSlcm1lsZMdcJQUl5ecrxyEZlBM0dOnVPcDElwcfw==", "eNqNj8FKw0AURff9isu4aULpNKWIBLqoOoXQNkjNQldhbN7YaaeTNDMBRfx3h9SVKLh7Fy7nnQsAzeu4rTtPQxbHXDaat9QY+c7jmI1g5OmlkjinOI9VZ5Q2Zui89J2bzyazEQ6utvMPVpGX2rAUzNaW2GcUDQIZWuEk2yP5NIQrZPm9eCrXYlmU08n0OkmSmxR+T8izZfEMjttFvrrcu71uHCxR1Re0regNKsSei5/aly9lqOkduT+8e9fNYrsSxbcffpuvDJHn/0A9bEUZJmV34jGKBl/YiWTB"], ["eNpTUFBQSM5ITc7WUApx1g1yidQ1MDC0VNJRyMsvUUhKTNEBAIyXCKU=", "eNqVU8Fq20AQvfcrBvWQFZVcywTTJlWgiW0ILT6kPjQEI1arsbRY0qq7oyolGPoR/cJ+SUeyfShNMVl0eOyb3Xlv9BYA4DX8/vWTP7hdzuZfk8/zxSqZjCfTKIregZjNF+F4eu5fABUIy9vF6h7ewvXH5ac9VoVuHDhNUOKGAmjrDC2sCu2gQ9xy7YArU1MBojYEm9JIwgyszgvyX7ECUPQYQJNDDKbBOsmkK0QaQCXtFile2Rb3dU0+6qSmZGNs4rBERcYK7yGTJENCRzqLlSyrULOIx3CQtoY9PWzFg+a1FwDpCk1L8XTMa395354b4HdZtixQeJ4nhA/xFTyBMrVjh1yRGdVWWNPoW4v2x5ejiLN/RRRozRqaM3+UI10bHoyu85tS8+E7PiT8YGjL7l907Qlvp9t1L2rXoNUmO/T7/+WXYJFaW/OsmvICytE+DYqxOuCOcXfEaY9TQ2QqrqK+ikwDu0vY+cLn0R+CUaDaCm91E97N7sPxeBLxv8tB1hnI1In8wVOlt4YQGDWMfPgQw/nzfPc333PE3FU8kOlQFh2H1K+NNwz2GHGSlkDS8A56F4BZjqcC/5Tv/IMXtNbBmyFjCWNj3TH7I1Uah+J5y9F7tty/m1RmwR8c6yoe"]]]]""")
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
            die(f"{rel}: the lines to fix were not found. Send me: grep -n 'IndexChips' {rel}")

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
    + ("" if rollback else " Next: ./desktop/build-scalp.sh both, then restart the app.\n  Undo: python3 apply_index_left.py --rollback"))
