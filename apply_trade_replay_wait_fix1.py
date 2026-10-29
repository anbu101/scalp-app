#!/usr/bin/env python3
# apply_trade_replay_wait_fix1.py — ── TRADE_REPLAY_WAIT_FIX1_20261029 ──
#
# Fixes found verifying the 2026-10-29 12:20 screenshots of the wait panel:
#   1. "forming bar (closes 12:30)" at 12:21 — the live view pads the forming
#      minute to the end of its 15-minute bucket; the explainers counted the
#      padded FUTURE 5m bar as the forming one (label one bar late, and the
#      provisional state evaluated one flat future bar). Now cut at the
#      forming bar's own bucket (TMA_V1/V2, VET, EMC).
#   2. TMA_V2 "break: EMA13 back below EMA55" — a strict stack breaks when ANY
#      pair flips; the item now names the weakest pair (smallest gap).
#   3. "Wed, 28 Obtu, 29 Oct" — the sticky day label of a day showing only a
#      sliver at the left edge overprinted the next day's label; dropped.
#   4. EMC watch footer "arming spot cross not found in the drawn sessions" —
#      a trade-only builder note; dropped in watch mode.
# Requires apply_trade_replay_wait.py (TRADE_REPLAY_WAIT_20261029).
#
#   backend  app/backtest/replay/waiting.py, live_replay.py, test_live_replay.py (now 143 checks)
#   frontend components/replay/TradeReplay.jsx
#
#   cd <repo root> && python3 apply_trade_replay_wait_fix1.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh both   (outside market hours — the backend changes)

import os
import shutil
import subprocess
import sys

FENCE = "TRADE_REPLAY_WAIT_FIX1_20261029"
NEW_FILES = {}
EDITS = {'backend/app/backtest/replay/waiting.py': [('# closed bars decide; the forming bar (live only) gives a PROVISIONAL line.\n#\n', '# closed bars decide; the forming bar (live only) gives a PROVISIONAL line.\n# ── TRADE_REPLAY_WAIT_FIX1_20261029 ── the live corpus view pads the forming\n# minute flat to the end of its 15-minute bucket (so completed-bar code can\n# value the forming bar); everything after the forming bar\'s own bucket is\n# padding and is cut here (it made "closes 12:30" out of a 12:20–12:25 bar\n# and evaluated one flat future bar too).\n#\n'), ('    warm = warmup_bars([(ctx.corpus.spot_1m(p), day_start(p)) for p in prev], tf)\n    today = [b for b in aggregate(ctx.corpus.spot_1m(D), tf, day_start(D)) if b["complete"]]\n    closed = [b for b in today if forming_ts is None or b["ts"] + tf * 60 <= forming_ts]\n', '    warm = warmup_bars([(ctx.corpus.spot_1m(p), day_start(p)) for p in prev], tf)\n    today = [b for b in aggregate(ctx.corpus.spot_1m(D), tf, day_start(D)) if b["complete"]\n             and (forming_ts is None or b["ts"] <= forming_ts)]          # ── TRADE_REPLAY_WAIT_FIX1_20261029 ── no padded future bars\n    closed = [b for b in today if forming_ts is None or b["ts"] + tf * 60 <= forming_ts]\n'), ('        name, other = ("bullish", "E1 bearish") if up else ("bearish", "E2 bullish")\n        brk = e["e13"] - e["e55"]\n        out["headline"] = (f"The {name} stack has stood since {_since(bars[k][\'ts\'] + tf * 60, ctx.days[-1])} — V2 enters only when a "\n', '        name, other = ("bullish", "E1 bearish") if up else ("bearish", "E2 bullish")\n        # ── TRADE_REPLAY_WAIT_FIX1_20261029 ── a strict stack breaks when ANY pair\n        # flips — the nearest one to flipping is the smallest gap, not 13/55\n        own = pairs_up if up else pairs_dn\n        w_lbl, w_diff, _ = min(own, key=lambda p: abs(p[1]))\n        out["headline"] = (f"The {name} stack has stood since {_since(bars[k][\'ts\'] + tf * 60, ctx.days[-1])} — V2 enters only when a "\n'), ('        out["status"] = "waiting"\n        out["items"] = ([_item(f"break: EMA13 back {\'below\' if up else \'above\'} EMA55", False, _pts(brk))]\n                        + side_items(pairs_dn if up else pairs_up, "E1" if up else "E2") + gate_items + [win_item])\n', '        out["status"] = "waiting"\n        out["items"] = ([_item(f"break: nearest pair to flip — {w_lbl} (the stack breaks when any pair flips)", False,\n                               _pts(w_diff))]\n                        + side_items(pairs_dn if up else pairs_up, "E1" if up else "E2") + gate_items + [win_item])\n'), ('    allb, _ = _vet_bars(ctx, days, tf)\n    bars = [b for b in allb if forming_ts is None or b.ts + tf * 60 <= forming_ts]\n', '    allb, _ = _vet_bars(ctx, days, tf)\n    allb = [b for b in allb if forming_ts is None or b.ts <= forming_ts]   # ── TRADE_REPLAY_WAIT_FIX1_20261029 ──\n    bars = [b for b in allb if forming_ts is None or b.ts + tf * 60 <= forming_ts]\n'), ('    out = {"strategy_id": "EMC_V1", "items": [], "events": [], "provisional": None}\n    today = tf_bars(D)\n    closed = [b for b in today if forming_ts is None or b.ts + stf * 60 <= forming_ts]\n', '    out = {"strategy_id": "EMC_V1", "items": [], "events": [], "provisional": None}\n    today = [b for b in tf_bars(D) if forming_ts is None or b.ts <= forming_ts]   # ── TRADE_REPLAY_WAIT_FIX1_20261029 ──\n    closed = [b for b in today if forming_ts is None or b.ts + stf * 60 <= forming_ts]\n')], 'backend/app/backtest/replay/live_replay.py': [('    out["nav"] = {"index": 0, "count": 1, "prev_trade_id": None, "next_trade_id": None}\n    keep = [n for n in (out.get("notes") or []) if spot_sym not in n and "leg" not in n.lower()]\n    extra = ["no open position — watching the strategy\'s indicators for the next entry"]\n', '    out["nav"] = {"index": 0, "count": 1, "prev_trade_id": None, "next_trade_id": None}\n    keep = [n for n in (out.get("notes") or []) if spot_sym not in n and "leg" not in n.lower()\n            and "not found in the drawn sessions" not in n]   # ── TRADE_REPLAY_WAIT_FIX1_20261029 ── trade-only notes\n    extra = ["no open position — watching the strategy\'s indicators for the next entry"]\n')], 'backend/app/backtest/replay/test_live_replay.py': [('      standing is not None and "break" in standing["items"][0]["text"], standing and standing["headline"])\nw = watch("TMA_V2", 15, 5)\n', '      standing is not None and "break" in standing["items"][0]["text"], standing and standing["headline"])\n# ── TRADE_REPLAY_WAIT_FIX1_20261029 ── the break is the WEAKEST pair, not always 13/55\n_opp = [x for x in standing["items"][1:4]]\n_weak = min(_opp, key=lambda x: abs(float(x["value"].replace(",", ""))))\ncheck("TMA_V2: break item = the pair with the smallest gap (any pair breaks a strict stack)",\n      standing["items"][0]["value"] == _weak["value"] and _weak["text"].split(" · ")[1].split()[0] in standing["items"][0]["text"],\n      (standing["items"][0], _weak))\nw = watch("TMA_V2", 15, 5)\n'), ('check("TSG: time-based entry at 09:16", "09:16" in watch("TSG_V1", 11, 2)["waiting"]["headline"])\ncheck("ORB: not wired yet — says so with its entry rule", watch("ORB_V1", 11, 2)["waiting"]["status"] == "unknown"\n', 'check("TSG: time-based entry at 09:16", "09:16" in watch("TSG_V1", 11, 2)["waiting"]["headline"])\n# ── TRADE_REPLAY_WAIT_FIX1_20261029 ── mid-5m-bar (11:01:30 → bar 11:00–11:05) while the\n# 15m padding runs to 11:15: the provisional line is about THIS bar only\nfor _sid in ("TMA_V2", "VET_V1", "EMC_V1", "TMA_V1"):\n    _p = watch(_sid, 11, 1)["waiting"].get("provisional") or ""\n    check(f"{_sid}: provisional names the forming bar\'s own close (11:05, not a padded 11:15)", "closes 11:05" in _p, _p)\ncheck("EMC watch: trade-only builder notes dropped", not any("not found in the drawn sessions" in n\n      for n in watch("EMC_V1", 11, 2)["notes"]))\ncheck("ORB: not wired yet — says so with its entry rule", watch("ORB_V1", 11, 2)["waiting"]["status"] == "unknown"\n')], 'frontend/src/components/replay/TradeReplay.jsx': [('                      const firstVisible = k === Math.floor(w0 / SEG);\n                      const lblX = inPlot(xd) ? xd + 4 : (firstVisible ? PAD.l + 4 : null);\n                      return (\n', "                      const firstVisible = k === Math.floor(w0 / SEG);\n                      let lblX = inPlot(xd) ? xd + 4 : (firstVisible ? PAD.l + 4 : null);\n                      // ── TRADE_REPLAY_WAIT_FIX1_20261029 ── a sticky label for a day that is only a\n                      // sliver at the left edge would print over the next day's label — drop it\n                      if (lblX != null && !inPlot(xd) && k + 1 < dayStarts.length) {\n                        const nx = xOf(dayStarts[k + 1] + OPEN * 60);\n                        if (nx != null && nx - PAD.l < 130) lblX = null;\n                      }\n                      return (\n")]}
PARENTS = {
    "backend/app/backtest/replay/waiting.py": ["TRADE_REPLAY_WAIT_20261029"],
    "backend/app/backtest/replay/live_replay.py": ["TRADE_REPLAY_WAIT_20261029"],
    "backend/app/backtest/replay/test_live_replay.py": ["TRADE_REPLAY_WAIT_20261029"],
    "frontend/src/components/replay/TradeReplay.jsx": ["TRADE_REPLAY_WAIT_20261029"],
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
                die(f"{rel} lacks parent fence {parent} — apply apply_trade_replay_wait.py first")
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
        suites = ["app/backtest/replay/test_live_replay.py", "app/backtest/replay/test_trade_replay.py"]   # 143 + 165
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
                if rel.endswith(".jsx"):
                    r = subprocess.run([esb, os.path.join(root, rel), "--loader:.jsx=jsx", "--log-level=error"],
                                       capture_output=True, text=True)
                    if r.returncode != 0:
                        print(r.stderr[-2000:])
                        rollback(f"esbuild could not parse {rel}")
            print("  esbuild parsed every changed JSX file")
        else:
            print("  (frontend/node_modules missing — JSX parse skipped; build-scalp.sh will compile)")

    print(f"\n✓ {FENCE} applied. Backups: *.bak-{FENCE}")
    print("  Next: ./desktop/build-scalp.sh both   — outside market hours (backend changed)")
    print("  Then: the forming-bar line names the bar's own close (12:25 at 12:21, not 12:30), TMA_V2's break item")
    print("  names the weakest pair, and the day labels no longer overprint at a session boundary.")


if __name__ == "__main__":
    main()
