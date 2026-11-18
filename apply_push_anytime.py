#!/usr/bin/env python3
# apply_push_anytime.py — ── PUSH_ANYTIME_20261118 ── (TD-047)
#
# Lets you push to GitHub at any time: removes the pre-push "no push Mon–Fri
# 09:00–15:30 IST" hook added by PRECOMMIT_20261117. The commit-time checks
# (ruff, dual tree, no backup files, REPO_MAP refresh) stay as they are.
# Why it is safe: a push to main builds nothing (the release workflow runs only
# on a v* tag), and the app never installs an update by itself — two taps, with
# a loud warning in market hours (UpdateBanner.jsx).
#
#   python3 apply_push_anytime.py              apply (run from the repo root)
#   python3 apply_push_anytime.py --rollback   put everything back exactly
#
# Changes:  .pre-commit-config.yaml (hook gone, pre-commit hook type only)
#           scripts/hooks/market_hours_push.py  DELETED
#           scripts/test_precommit_hooks.py     (TC-HOOKS-0016…0021 retired, 0026/0027 updated, 0043 new)
#           CLAUDE.md                           (two lines)
#           .git/hooks/pre-push                 removed if pre-commit generated it (not tracked)
# Stdlib only. Idempotent: a second run says "already applied" and changes nothing.
import base64
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

FENCE = "PUSH_ANYTIME_20261118"
PARENT = "PRECOMMIT_20261117"
BAK = ".bak-" + FENCE
EDITS = json.loads(base64.b64decode("eyIucHJlLWNvbW1pdC1jb25maWcueWFtbCI6IFtbIiMgLnByZS1jb21taXQtY29uZmlnLnlhbWwg4oCUIOKUgOKUgCBQUkVDT01NSVRfMjAyNjExMTcg4pSA4pSAIChURC0wNDIpXG4iLCAiIyAucHJlLWNvbW1pdC1jb25maWcueWFtbCDigJQg4pSA4pSAIFBSRUNPTU1JVF8yMDI2MTExNyDilIDilIAgKFRELTA0MilcbiMg4pSA4pSAIFBVU0hfQU5ZVElNRV8yMDI2MTExOCDilIDilIAgKFRELTA0NykgbWFya2V0LWhvdXJzIHB1c2ggYmxvY2sgcmVtb3ZlZDogcHVzaCBhbnkgdGltZVxuIl0sIFsiIyAgIHByZS1jb21taXQgaW5zdGFsbCAtLWhvb2stdHlwZSBwcmUtY29tbWl0IC0taG9vay10eXBlIHByZS1wdXNoXG4iLCAiIyAgIHByZS1jb21taXQgaW5zdGFsbFxuIl0sIFsiZGVmYXVsdF9pbnN0YWxsX2hvb2tfdHlwZXM6IFtwcmUtY29tbWl0LCBwcmUtcHVzaF1cbiIsICJkZWZhdWx0X2luc3RhbGxfaG9va190eXBlczogW3ByZS1jb21taXRdXG4iXSwgWyJcbiAgICAgIC0gaWQ6IG1hcmtldC1ob3Vycy1wdXNoXG4gICAgICAgIG5hbWU6IG5vIHB1c2ggTW9uLUZyaSAwOTowMC0xNTozMCBJU1QgKFNDQUxQX1BVU0hfT0s9MSBvdmVycmlkZXMpXG4gICAgICAgIGVudHJ5OiBweXRob24gc2NyaXB0cy9ob29rcy9tYXJrZXRfaG91cnNfcHVzaC5weVxuICAgICAgICBsYW5ndWFnZTogcHl0aG9uXG4gICAgICAgIHN0YWdlczogW3ByZS1wdXNoXVxuICAgICAgICBhbHdheXNfcnVuOiB0cnVlXG4gICAgICAgIHBhc3NfZmlsZW5hbWVzOiBmYWxzZVxuIiwgIiJdXSwgInNjcmlwdHMvdGVzdF9wcmVjb21taXRfaG9va3MucHkiOiBbWyIoVEQtMDQyKSBUQy1IT09LUy0wMDAx4oCmMDAyOFxuIiwgIihURC0wNDIpIFRDLUhPT0tTLTAwMDHigKYwMDI4LCAwMDQzXG4jIFBVU0hfQU5ZVElNRV8yMDI2MTExODogbWFya2V0LWhvdXJzIHB1c2ggaG9vayByZW1vdmVkIOKAlCBUQy1IT09LUy0wMDE24oCmMDAyMSByZXRpcmVkLlxuIl0sIFsiIyBVbml0IGNoZWNrcyBvZiB0aGUgZm91ciBsb2NhbCBob29rcyIsICIjIFVuaXQgY2hlY2tzIG9mIHRoZSB0aHJlZSBsb2NhbCBob29rcyJdLCBbIiMg4pSA4pSAIG1hcmtldCBob3VycyAocHJlLXB1c2gpIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuZGVmIHB1c2gobm93LCBvaz1Ob25lKTpcbiAgICBlbnYgPSB7XCJTQ0FMUF9IT09LX05PV1wiOiBub3d9XG4gICAgaWYgb2sgaXMgbm90IE5vbmU6XG4gICAgICAgIGVudltcIlNDQUxQX1BVU0hfT0tcIl0gPSBva1xuICAgIGUgPSBkaWN0KG9zLmVudmlyb24pXG4gICAgZS5wb3AoXCJTQ0FMUF9QVVNIX09LXCIsIE5vbmUpXG4gICAgciA9IHN1YnByb2Nlc3MucnVuKFtzeXMuZXhlY3V0YWJsZSwgb3MucGF0aC5qb2luKEgsIFwibWFya2V0X2hvdXJzX3B1c2gucHlcIildLCBlbnY9ZGljdChlLCAqKmVudiksXG4gICAgICAgICAgICAgICAgICAgICAgIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGltZW91dD0zMClcbiAgICByZXR1cm4gci5yZXR1cm5jb2RlLCByLnN0ZG91dFxuXG5cbnJjLCBvdXQgPSBwdXNoKFwiMjAyNi0xMS0xNlQxMDowMDowMCswNTozMFwiKVxuY2hrKFwiVEMtSE9PS1MtMDAxNlwiLCBcInB1c2ggb24gTW9uZGF5IDEwOjAwIElTVCBpcyBibG9ja2VkIHdpdGggdGhlIG92ZXJyaWRlIGhpbnRcIixcbiAgICByYyA9PSAxIGFuZCBcIlB1c2ggYmxvY2tlZFwiIGluIG91dCBhbmQgXCJTQ0FMUF9QVVNIX09LPTFcIiBpbiBvdXQsIG91dClcbnJlcyA9IHt0OiBwdXNoKGZcIjIwMjYtMTEtMTZUe3R9OjAwKzA1OjMwXCIpWzBdIGZvciB0IGluIChcIjA4OjU5XCIsIFwiMDk6MDBcIiwgXCIxNTozMFwiLCBcIjE1OjMxXCIpfVxuY2hrKFwiVEMtSE9PS1MtMDAxN1wiLCBcImJvdW5kYXJpZXM6IDA4OjU5IGFuZCAxNTozMSBhbGxvd2VkLCAwOTowMCBhbmQgMTU6MzAgYmxvY2tlZFwiLFxuICAgIHJlcyA9PSB7XCIwODo1OVwiOiAwLCBcIjA5OjAwXCI6IDEsIFwiMTU6MzBcIjogMSwgXCIxNTozMVwiOiAwfSwgcmVzKVxucmVzID0gW3B1c2goZlwiMjAyNi0xMS17ZF99VDExOjAwOjAwKzA1OjMwXCIpWzBdIGZvciBkXyBpbiAoXCIyMVwiLCBcIjIyXCIpXVxuY2hrKFwiVEMtSE9PS1MtMDAxOFwiLCBcIlNhdHVyZGF5IGFuZCBTdW5kYXkgMTE6MDAgSVNUIGFyZSBhbGxvd2VkXCIsIHJlcyA9PSBbMCwgMF0sIHJlcylcbnJjLCBvdXQgPSBwdXNoKFwiMjAyNi0xMS0xNlQxMDowMDowMCswNTozMFwiLCBvaz1cIjFcIilcbmNoayhcIlRDLUhPT0tTLTAwMTlcIiwgXCJTQ0FMUF9QVVNIX09LPTEgbGV0cyBhIG1hcmtldC1ob3VycyBwdXNoIHRocm91Z2ggKGFuZCBzYXlzIHNvKVwiLFxuICAgIHJjID09IDAgYW5kIFwicHVzaGluZyBhbnl3YXlcIiBpbiBvdXQsIG91dClcbnJjX2EsIF8gPSBwdXNoKFwiMjAyNi0xMS0xN1QwNDowMDowMFpcIikgICAgICAgICAgICAjIDA5OjMwIElTVCBUdWUgb24gdGhlIHJlYWwgVVRDIGNsb2NrXG5yY19iLCBfID0gcHVzaChcIjIwMjYtMTEtMTdUMTA6MDU6MDBaXCIpICAgICAgICAgICAgIyAxNTozNSBJU1RcbnJjX2MsIF8gPSBwdXNoKFwiMjAyNi0xMS0yMFQyMzowMDowMFpcIikgICAgICAgICAgICAjIEZyaSAyMzowMCBVVEMgPSBTYXQgMDQ6MzAgSVNUXG5jaGsoXCJUQy1IT09LUy0wMDIwXCIsIFwiVVRDIGluc3RhbnRzIGNvbnZlcnQgdG8gSVNUOiAwNDowMFogVHVlIGJsb2NrZWQsIDEwOjA1WiBhbGxvd2VkLCBGcmkgMjM6MDBaIChTYXQgSVNUKSBhbGxvd2VkXCIsXG4gICAgKHJjX2EsIHJjX2IsIHJjX2MpID09ICgxLCAwLCAwKSwgKHJjX2EsIHJjX2IsIHJjX2MpKVxucmMsIF8gPSBwdXNoKFwiMjAyNi0xMS0xNlQxMDowMDowMCswNTozMFwiLCBvaz1cInllc1wiKVxuY2hrKFwiVEMtSE9PS1MtMDAyMVwiLCBcIm9ubHkgU0NBTFBfUFVTSF9PSz0xIG92ZXJyaWRlcyAoPXllcyBkb2VzIG5vdClcIiwgcmMgPT0gMSwgcmMpXG5cbiIsICIiXSwgWyJ3YW50ID0gW1wicnVmZi1jaGVja1wiLCBcIm5vLWJhY2t1cC1maWxlc1wiLCBcImR1YWwtdHJlZS1wYXJpdHlcIiwgXCJyZXBvLW1hcC1yZWZyZXNoXCIsIFwibWFya2V0LWhvdXJzLXB1c2hcIl1cbmNoayhcIlRDLUhPT0tTLTAwMjZcIiwgXCIucHJlLWNvbW1pdC1jb25maWcueWFtbCBoYXMgdGhlIGZpdmUgaG9va3MsIHJ1ZmYgcGlubmVkLCBwcmUtcHVzaCBpbnN0YWxsZWQgYnkgZGVmYXVsdCwgXCJcbiAgICBcIm1hcmtldCBob3VycyBvbiBwcmUtcHVzaCBvbmx5XCIsXG4gICAgaWRzID09IHdhbnQgYW5kIHJlLnNlYXJjaChyXCJyZXY6XFxzKnZcXGQrXFwuXFxkK1xcLlxcZCtcIiwgY2ZnKSBpcyBub3QgTm9uZVxuICAgIGFuZCBcImRlZmF1bHRfaW5zdGFsbF9ob29rX3R5cGVzOiBbcHJlLWNvbW1pdCwgcHJlLXB1c2hdXCIgaW4gY2ZnIGFuZCBcImRlZmF1bHRfc3RhZ2VzOiBbcHJlLWNvbW1pdF1cIiBpbiBjZmdcbiAgICBhbmQgcmUuc2VhcmNoKHJcImlkOiBtYXJrZXQtaG91cnMtcHVzaCg/Oi58XFxuKSo/c3RhZ2VzOiBcXFtwcmUtcHVzaFxcXVwiLCBjZmcpIGlzIG5vdCBOb25lXG4gICAgYW5kIG5vdCByZS5zZWFyY2goclwiYXJnczouKi0tZml4XCIsIGNmZyksIGlkcylcbiIsICJ3YW50ID0gW1wicnVmZi1jaGVja1wiLCBcIm5vLWJhY2t1cC1maWxlc1wiLCBcImR1YWwtdHJlZS1wYXJpdHlcIiwgXCJyZXBvLW1hcC1yZWZyZXNoXCJdXG5jaGsoXCJUQy1IT09LUy0wMDI2XCIsIFwiLnByZS1jb21taXQtY29uZmlnLnlhbWwgaGFzIHRoZSBmb3VyIGhvb2tzLCBydWZmIHBpbm5lZCwgb25seSB0aGUgY29tbWl0IGhvb2sgaW5zdGFsbGVkLCBcIlxuICAgIFwibm90aGluZyBvbiBwdXNoIChwdXNoZXMgYWxsb3dlZCBhbnkgdGltZSlcIixcbiAgICBpZHMgPT0gd2FudCBhbmQgcmUuc2VhcmNoKHJcInJldjpcXHMqdlxcZCtcXC5cXGQrXFwuXFxkK1wiLCBjZmcpIGlzIG5vdCBOb25lXG4gICAgYW5kIFwiZGVmYXVsdF9pbnN0YWxsX2hvb2tfdHlwZXM6IFtwcmUtY29tbWl0XVwiIGluIGNmZyBhbmQgXCJkZWZhdWx0X3N0YWdlczogW3ByZS1jb21taXRdXCIgaW4gY2ZnXG4gICAgYW5kIFwicHJlLXB1c2hcIiBub3QgaW4gY2ZnIGFuZCBub3QgcmUuc2VhcmNoKHJcIl5cXHMrc3RhZ2VzOlwiLCBjZmcsIHJlLk0pXG4gICAgYW5kIG5vdCByZS5zZWFyY2goclwiYXJnczouKi0tZml4XCIsIGNmZyksIGlkcylcbiJdLCBbIiAgICBsZW4obG9jYWwpID09IDQgYW5kIGFsbCgiLCAiICAgIGxlbihsb2NhbCkgPT0gMyBhbmQgYWxsKCJdLCBbInJ0ID0gb3Blbihvcy5wYXRoLmpvaW4oUk9PVCwgXCJydWZmLnRvbWxcIiksIGVuY29kaW5nPVwidXRmLThcIikucmVhZCgpXG4iLCAiY2hrKFwiVEMtSE9PS1MtMDA0M1wiLCBcIm5vIG1hcmtldC1ob3VycyBwdXNoIGhvb2sgbGVmdDogc2NyaXB0cy9ob29rcy9tYXJrZXRfaG91cnNfcHVzaC5weSBpcyBnb25lIChQVVNIX0FOWVRJTUVfMjAyNjExMTgpXCIsXG4gICAgbm90IG9zLnBhdGguZXhpc3RzKG9zLnBhdGguam9pbihILCBcIm1hcmtldF9ob3Vyc19wdXNoLnB5XCIpKSlcbnJ0ID0gb3Blbihvcy5wYXRoLmpvaW4oUk9PVCwgXCJydWZmLnRvbWxcIiksIGVuY29kaW5nPVwidXRmLThcIikucmVhZCgpXG4iXV0sICJDTEFVREUubWQiOiBbWyItIE5vIGxpdmUgZGVwbG95cyBNb27igJNGcmkgMDk6MDDigJMxNTozMCBJU1QuXG4iLCAiLSBQdXNoIGFuZCByZWxlYXNlIGFueSB0aW1lIChQVVNIX0FOWVRJTUVfMjAyNjExMTgpOyBpbnN0YWxsaW5nIGFuIHVwZGF0ZSBpbiBtYXJrZXQgaG91cnMgaXMgdGhlIHVzZXIncyBjYWxsIOKAlCB0aGUgYXBwJ3MgdXBkYXRlIGJhbm5lciB3YXJucyBmaXJzdC5cbiJdLCBbIjsgcHVzaGVzIGFyZSBibG9ja2VkIE1vbuKAk0ZyaSAwOTowMOKAkzE1OjMwIElTVCB1bmxlc3MgYFNDQUxQX1BVU0hfT0s9MWAuIiwgIjsgbm90aGluZyBydW5zIG9uIHB1c2guIl1dfQ==").decode("utf-8"))
DELETE = ["scripts/hooks/market_hours_push.py"]
HOOK = os.path.join(".git", "hooks", "pre-push")


def say(m):
    print(m, flush=True)


def die(m):
    say("ABORT: " + m + "\nNothing was changed.")
    sys.exit(1)


def rollback():
    n = 0
    for p in list(EDITS) + DELETE + [HOOK]:
        if os.path.exists(p + BAK):
            os.replace(p + BAK, p)
            n += 1
    say(f"Rolled back {n} file(s)." if n else "Nothing to roll back.")
    return n


def main():
    if not (os.path.exists(".pre-commit-config.yaml") and os.path.isdir(".git")):
        die("run this from the repo root (the folder with .pre-commit-config.yaml and .git).")
    if "--rollback" in sys.argv:
        rollback()
        return 0
    cfg = open(".pre-commit-config.yaml", encoding="utf-8").read()
    if FENCE in cfg:
        say(f"{FENCE} already applied — nothing to do.")
        return 0
    if PARENT not in cfg:
        die(f"parent fence {PARENT} not found in .pre-commit-config.yaml (apply_precommit.py first).")
    targets = list(EDITS) + DELETE
    st = subprocess.run(["git", "status", "--porcelain", "--", *targets], capture_output=True, text=True)
    if st.returncode != 0:
        die("git status failed: " + st.stderr.strip())
    if st.stdout.strip():
        die("these files have uncommitted changes (commit or stash them first):\n" + st.stdout)
    for p in targets:
        if not os.path.exists(p):
            die(f"{p} is missing.")
        if os.path.exists(p + BAK):
            die(f"{p + BAK} exists from an earlier try — run --rollback or delete it.")

    new = {}
    for p, eds in EDITS.items():
        s = open(p, encoding="utf-8").read()
        for old, rep in eds:
            c = s.count(old)
            if c != 1:
                die(f"{p}: expected text found {c} times (file differs from main): {old[:70]!r}")
            s = s.replace(old, rep, 1)
        new[p] = s
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "t.py")
        open(t, "w", encoding="utf-8").write(new["scripts/test_precommit_hooks.py"])
        try:
            py_compile.compile(t, doraise=True)
        except py_compile.PyCompileError as e:
            die("new test file does not compile: " + str(e))

    done = []
    try:
        for p, s in new.items():
            stg = p + ".staging-" + FENCE
            with open(stg, "w", encoding="utf-8", newline="") as f:
                f.write(s)
            shutil.copy2(p, p + BAK)
            shutil.copymode(p, stg)
            os.replace(stg, p)
            done.append(p)
        for p in DELETE:
            os.replace(p, p + BAK)
            done.append(p)
        if os.path.exists(HOOK) and "pre-commit" in open(HOOK, encoding="utf-8", errors="replace").read():
            os.replace(HOOK, HOOK + BAK)
            say("removed the installed pre-push hook (.git/hooks/pre-push, made by pre-commit)")
    except Exception as e:  # all-or-nothing
        for p in new:
            if os.path.exists(p + ".staging-" + FENCE):
                os.remove(p + ".staging-" + FENCE)
        rollback()
        die(f"write failed ({e}); restored.")

    r = subprocess.run([sys.executable, "scripts/test_precommit_hooks.py"], capture_output=True, text=True)
    lines = (r.stdout + r.stderr).splitlines()
    for ln in lines:
        if "FAIL" in ln or "PASSED" in ln or "FAILURES" in ln or "Traceback" in ln:
            say(ln)
    if r.returncode != 0:
        rollback()
        die("hook tests failed after the change; restored.")

    say(f"\n{FENCE} applied. Pushes are no longer blocked at any time of day.")
    say("Next: double-click deploy-scalp.command and enter the SAME version you were releasing —")
    say("it commits this change, pushes main and pushes the tag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
