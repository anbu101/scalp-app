#!/usr/bin/env python3
# apply_trade_replay_wait_fix2.py — ── TRADE_REPLAY_WAIT_FIX2_20261029 ──
#
# From verifying the 2026-10-29 12:43 screenshots:
#   1. "DTE 0" on a live VET position expiring 3 Nov (opened 29 Oct) — the
#      replay counted sessions to expiry from the backtest corpus's OWN
#      calendar, which ends today, so every future session was missing.
#      Sessions past the corpus end are now counted from the NSE calendar
#      (is_trading_day). Backtest replays (expiry inside the corpus) are
#      unchanged — asserted against the calendar-only count.
#   2. EMC watch footer "option trigger is intrabar: the entry can precede…"
#      — a trade-only builder note; dropped in watch mode.
# Backend only. Requires apply_trade_replay_wait_fix1.py.
#
#   cd <repo root> && python3 apply_trade_replay_wait_fix2.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh backend   (outside market hours)

import os
import shutil
import subprocess
import sys

FENCE = "TRADE_REPLAY_WAIT_FIX2_20261029"
NEW_FILES = {}
EDITS = {'backend/app/backtest/replay/replay_core.py': [('        t = legs[0]\n        return sessions_to_expiry(cal, ist_date(int(t["entry_ts"])).isoformat(),\n                                  str(t.get("expiry") or ""))\n    except Exception:\n', '        t = legs[0]\n        e_iso, x_iso = ist_date(int(t["entry_ts"])).isoformat(), str(t.get("expiry") or "")[:10]\n        n = sessions_to_expiry(cal, e_iso, x_iso)\n        # ── TRADE_REPLAY_WAIT_FIX2_20261029 ── the corpus calendar ends at the last\n        # session it holds; a LIVE position\'s expiry is usually after that, so the\n        # sessions past the corpus end are counted from the NSE calendar (a\n        # backtest replay\'s expiry is always inside the corpus — unchanged).\n        if n is not None and cal and x_iso > cal[-1] and len(x_iso) == 10:\n            from datetime import timedelta as _td\n            from app.utils.market_hours import is_trading_day\n            d = max(date.fromisoformat(cal[-1]), date.fromisoformat(e_iso)) + _td(days=1)\n            end = date.fromisoformat(x_iso)\n            while d <= end:\n                if is_trading_day(d):\n                    n += 1\n                d += _td(days=1)\n        return n\n    except Exception:\n')], 'backend/app/backtest/replay/live_replay.py': [('    keep = [n for n in (out.get("notes") or []) if spot_sym not in n and "leg" not in n.lower()\n            and "not found in the drawn sessions" not in n]   # ── TRADE_REPLAY_WAIT_FIX1_20261029 ── trade-only notes\n    extra = ["no open position — watching the strategy\'s indicators for the next entry"]\n', '    keep = [n for n in (out.get("notes") or []) if spot_sym not in n and "leg" not in n.lower()\n            and "not found in the drawn sessions" not in n   # ── TRADE_REPLAY_WAIT_FIX1_20261029 ── trade-only notes\n            and "the entry can precede" not in n]              # ── TRADE_REPLAY_WAIT_FIX2_20261029 ── (EMC intrabar entry note)\n    extra = ["no open position — watching the strategy\'s indicators for the next entry"]\n')], 'backend/app/backtest/replay/test_live_replay.py': [('\nprint("── 13. stream routes on the REAL api_server app")\n', '\nprint("── 12d. DTE of a live position whose expiry is past the corpus (TRADE_REPLAY_WAIT_FIX2_20261029)")\n# the synthetic corpus ends Fri 28 Feb 2025; a contract expiring Thu 6 Mar →\n# sessions 3, 4, 5, 6 Mar = 4 (the corpus calendar alone said 0)\n_leg = {"entry_ts": ts_of(date(2025, 2, 28), 10, 0), "expiry": "2025-03-06"}\ncheck("DTE counts NSE sessions past the corpus end (4, not 0)", RC._dte("NIFTY", [_leg]) == 4, RC._dte("NIFTY", [_leg]))\n_leg2 = {"entry_ts": ts_of(D24, 10, 0), "expiry": exp}\nfrom app.backtest.repo.trading_calendar import sessions_to_expiry as _s2x, trading_dates as _tdates   # noqa: E402\n_cal_only = _s2x(_tdates("NIFTY", db_path=DB), D24.isoformat(), exp)\ncheck("DTE for an expiry inside the corpus = the corpus calendar\'s own count (unchanged)",\n      RC._dte("NIFTY", [_leg2]) == _cal_only and _cal_only is not None, (RC._dte("NIFTY", [_leg2]), _cal_only))\ncheck("EMC watch: intrabar-entry trade note dropped", not any("the entry can precede" in n\n      for n in watch("EMC_V1", 11, 2)["notes"]))\nNOW[0] = ts_of(date(2025, 3, 10), 12, 0)\n\nprint("── 13. stream routes on the REAL api_server app")\n')]}
PARENTS = {
    "backend/app/backtest/replay/replay_core.py": ["TRADE_REPLAY_WAIT_20261029"],
    "backend/app/backtest/replay/live_replay.py": ["TRADE_REPLAY_WAIT_FIX1_20261029"],
    "backend/app/backtest/replay/test_live_replay.py": ["TRADE_REPLAY_WAIT_FIX1_20261029"],
}
MIRRORS = ("desktop/src-tauri",)       # build copies (gitignored); refreshed if present


def die(msg, wrote=False):
    print(f"\n✗ {msg}" + ("" if wrote else "\nNothing was written."))
    sys.exit(1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    root = os.path.abspath(args[0] if args else os.getcwd())
    if not (os.path.isdir(os.path.join(root, "backend", "app")) and os.path.isdir(os.path.join(root, "frontend", "src"))):
        die(f"{root} is not the scalp-app repo root (expected backend/app and frontend/src)")
    print(f"── {FENCE} → {root}")

    # ── preflight ──
    for rel in EDITS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die(f"missing {rel}")
        txt = open(p, encoding="utf-8").read()
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied")
        for parent in PARENTS.get(rel, []):
            if parent not in txt:
                die(f"{rel} lacks parent fence {parent} — apply apply_trade_replay_wait_fix1.py first")
    for rel in NEW_FILES:
        if os.path.exists(os.path.join(root, rel)):
            die(f"{rel} already exists — already applied?")
    if "--allow-dirty" not in flags and os.path.isdir(os.path.join(root, ".git")):
        try:
            st = subprocess.run(["git", "status", "--porcelain", "--"] + list(EDITS), cwd=root,
                                capture_output=True, text=True, timeout=30).stdout
        except Exception as e:
            st = ""
            print(f"  (git status unavailable: {e!r} — continuing)")
        dirty = [ln for ln in st.splitlines() if ln[:2].strip()]
        if dirty:
            die("uncommitted changes in target files (commit/stash, or pass --allow-dirty):\n  " + "\n  ".join(dirty))

    staged = {}
    for rel, edits in EDITS.items():
        txt = open(os.path.join(root, rel), encoding="utf-8").read()
        for i, (old, new) in enumerate(edits, 1):
            n = txt.count(old)
            if n != 1:
                die(f"{rel}: anchor {i}/{len(edits)} found {n}× (expected 1):\n{old[:300]}")
            txt = txt.replace(old, new)
        staged[rel] = txt
    for rel, txt in list(staged.items()) + list(NEW_FILES.items()):
        if rel.endswith(".py"):
            try:
                compile(txt, rel, "exec")
            except SyntaxError as e:
                die(f"{rel} does not compile after staging: {e}")
    print(f"  preflight ok — {sum(len(e) for e in EDITS.values())} anchored edits in {len(EDITS)} files, {len(NEW_FILES)} new files")

    # ── write ──
    touched = []          # (path, backup or None)

    def write(path, txt):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bak = None
        if os.path.exists(path):
            bak = f"{path}.bak-{FENCE}"
            shutil.copy2(path, bak)
        touched.append((path, bak))
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)

    def rollback(why):
        for path, bak in reversed(touched):
            try:
                if bak:
                    shutil.copy2(bak, path)
                    os.remove(bak)
                elif os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                print(f"  !! could not restore {path}: {e!r}")
        die(f"{why}\nEvery file it touched was restored.", wrote=True)

    for rel, txt in list(staged.items()) + list(NEW_FILES.items()):
        write(os.path.join(root, rel), txt)
        print(f"  wrote {rel}")
        for m in MIRRORS:
            mp = os.path.join(root, m, rel)
            if os.path.isdir(os.path.join(root, m, rel.split("/")[0])):
                write(mp, txt)
                print(f"    mirrored → {m}/{rel}")

    if "--skip-tests" in flags:
        print("\n  --skip-tests: suites not run")
    else:
        be = os.path.join(root, "backend")
        env = dict(os.environ, PYTHONPATH=be)
        suites = ["app/backtest/replay/test_live_replay.py", "app/backtest/replay/test_trade_replay.py"]   # 146 + 165
        if os.path.exists(os.path.join(be, "app/backtest/replay/test_replay_stale_rows.py")):
            suites.append("app/backtest/replay/test_replay_stale_rows.py")                             # 22
        for suite in suites:
            print(f"\n── running {suite} (a few minutes)")
            r = subprocess.run([sys.executable, suite], cwd=be, env=env, capture_output=True, text=True)
            tail = "\n".join((r.stdout or "").strip().splitlines()[-3:])
            print("  " + tail.replace("\n", "\n  "))
            if r.returncode != 0:
                print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if "FAIL" in l or "Error" in l)[-3000:])
                rollback(f"{suite} failed")
        esb = os.path.join(root, "frontend", "node_modules", ".bin", "esbuild")
        if os.path.exists(esb):
            for rel in list(EDITS) + list(NEW_FILES):
                if rel.endswith(".jsx"):   # (none in this script)
                    r = subprocess.run([esb, os.path.join(root, rel), "--loader:.jsx=jsx", "--log-level=error"],
                                       capture_output=True, text=True)
                    if r.returncode != 0:
                        print(r.stderr[-2000:])
                        rollback(f"esbuild could not parse {rel}")
            print("  esbuild parsed every changed JSX file")
        else:
            print("  (frontend/node_modules missing — JSX parse skipped; build-scalp.sh will compile)")

    print(f"\n✓ {FENCE} applied. Backups: *.bak-{FENCE}")
    print("  Next: ./desktop/build-scalp.sh backend   — outside market hours (backend changed)")
    print("  Then: an open position's header shows its real DTE (e.g. a 3 Nov expiry on 29 Oct → DTE 3, was DTE 0).")
    print("  Backend only — ./desktop/build-scalp.sh backend is enough.")


if __name__ == "__main__":
    main()
