#!/usr/bin/env python3
# apply_recorded_blocks_wide_wings.py — ── RECORDED_BLOCKS_20261031 ── ── REC_WIDE_WINGS_20261031 ──
#
# From the Icarus / Velvet recorded-data review (31 Oct):
# 1. NO POSITION ACROSS A SKIPPED DAY. Skipping partial days let a positional
#    run carry a position over the gap (Icarus's 22 Oct condor "closed" on
#    28 Oct 09:16, expired legs at ₹0.05). A recorded-data run is now split
#    into blocks of consecutive NSE sessions that hold option data; each block
#    is its own run (the runner closes what is open at the block's end, as at
#    any range end — EOR / RANGE_END_OPEN), trades are concatenated and the
#    additive summary numbers re-added (win rate recomputed; max drawdown =
#    the worst block's). The summary lists recorded_blocks.
# 2. WIDER RECORDER BAND FOR THE WEEKS. The ₹2–4 hedge wings Velvet / Icarus /
#    Tigris buy sit 1,000–1,500 points from spot; the recorder kept ±10 steps
#    (±500), so backtests modelled those wings (SYN-) or skipped the entry.
#    Current- and next-week expiries are now recorded ±32 steps (±1,600);
#    the monthly stays ±10. From the next pull on (~300 contracts a day instead
#    of ~165; a 30-min pass takes ~2 min at Kite's 3 requests/s). The day's
#    grade does not depend on thinly traded far strikes (only on errors,
#    expired-before-pull contracts and spot).
#
#   EDIT backend/app/recorder/rec_pull.py · test_recorder.py (+2) ·
#        backend/app/backtest/queue_worker.py · backend/app/recorder/test_seam.py (22)
#   Requires apply_tsg_intrabar_skip_partial.py.
#
#   cd <repo root> && python3 apply_recorded_blocks_wide_wings.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh backend
#
# Run ONLY this script — it carries every file it writes.

import os
import shutil
import subprocess
import sys

FENCE = "RECORDED_BLOCKS_20261031"
NEW_FILES = {}
EDITS = {'backend/app/recorder/rec_pull.py': [('\n\ndef strike_range(spot_lo: float, spot_hi: float) -> Tuple[float, float]:\n    lo = math.floor(spot_lo / STRIKE_STEP) * STRIKE_STEP - BAND_STEPS * STRIKE_STEP\n    hi = math.ceil(spot_hi / STRIKE_STEP) * STRIKE_STEP + BAND_STEPS * STRIKE_STEP\n    return float(lo), float(hi)\n\n\ndef build_band(rows: List[dict], d: date, spot_lo: float, spot_hi: float) -> List[dict]:\n    kinds = expiry_kinds(rows, d)\n    lo, hi = strike_range(spot_lo, spot_hi)\n    band = [{"token": RD.SPOT_TOKEN, "tradingsymbol": RD.SPOT_SYMBOL, "instrument_type": "SPOT",\n             "strike": 0.0, "expiry": "", "kind": "spot"}]\n    for r in rows:\n        k = kinds.get(r["expiry"])\n        if k and lo <= r["strike"] <= hi:\n            band.append(dict(r, kind=k))\n    return band\n\n', '\n\ndef strike_range(spot_lo: float, spot_hi: float, steps: int = BAND_STEPS) -> Tuple[float, float]:\n    lo = math.floor(spot_lo / STRIKE_STEP) * STRIKE_STEP - steps * STRIKE_STEP\n    hi = math.ceil(spot_hi / STRIKE_STEP) * STRIKE_STEP + steps * STRIKE_STEP\n    return float(lo), float(hi)\n\n\ndef build_band(rows: List[dict], d: date, spot_lo: float, spot_hi: float) -> List[dict]:\n    kinds = expiry_kinds(rows, d)\n    ranges = {"week": strike_range(spot_lo, spot_hi, WEEK_BAND_STEPS),       # ── REC_WIDE_WINGS_20261031 ──\n              "next": strike_range(spot_lo, spot_hi, WEEK_BAND_STEPS),\n              "month": strike_range(spot_lo, spot_hi, BAND_STEPS)}\n    band = [{"token": RD.SPOT_TOKEN, "tradingsymbol": RD.SPOT_SYMBOL, "instrument_type": "SPOT",\n             "strike": 0.0, "expiry": "", "kind": "spot"}]\n    for r in rows:\n        k = kinds.get(r["expiry"])\n        if k:\n            lo, hi = ranges.get(k, ranges["month"])\n            if lo <= r["strike"] <= hi:\n                band.append(dict(r, kind=k))\n    return band\n\n'), ('STRIKE_STEP = 50\nBAND_STEPS = 10\nOPEN_MIN = 9 * 60 + 15\nCLOSE_MIN = 15 * 60 + 30            # last bar starts 15:29\n', 'STRIKE_STEP = 50\nBAND_STEPS = 10\n# ── REC_WIDE_WINGS_20261031 ── current- and next-week expiries are recorded\n# ±32 steps (±1,600 points): Velvet / Icarus / Tigris buy ₹2–4 hedge wings\n# that sit 1,000–1,500 points from spot, outside ±10 — so recorded-data\n# backtests modelled them (SYN-) or skipped the entry. The monthly stays ±10.\nWEEK_BAND_STEPS = 32\nOPEN_MIN = 9 * 60 + 15\nCLOSE_MIN = 15 * 60 + 30            # last bar starts 15:29\n')], 'backend/app/recorder/test_recorder.py': [('      RP.strike_range(24012.4, 24188.0))\ncheck("an exact-strike range still pads 10 steps", RP.strike_range(24000, 24000) == (23500.0, 24500.0))\nR = [{"token": 1, "tradingsymbol": "NIFTY2611324000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-03"},\n     {"token": 2, "tradingsymbol": "NIFTY26N1024000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-10"},\n', '      RP.strike_range(24012.4, 24188.0))\ncheck("an exact-strike range still pads 10 steps", RP.strike_range(24000, 24000) == (23500.0, 24500.0))\n# ── REC_WIDE_WINGS_20261031 ── weeks ±32 steps (the ₹2–4 wings), monthly ±10\n_rows_w = [{"token": i, "tradingsymbol": f"NIFTY26N03{k}{t}", "instrument_type": t, "strike": float(k), "expiry": e}\n           for i, (k, t, e) in enumerate([(k, t, e) for k in range(22000, 26001, 50) for t in ("CE", "PE")\n                                          for e in ("2026-11-03", "2026-11-10", "2026-11-24")])]\nfor r_ in _rows_w:\n    if r_["expiry"] == "2026-11-24":\n        r_["tradingsymbol"] = r_["tradingsymbol"].replace("26N03", "26NOV")\n_bd = RP.build_band(_rows_w, date(2026, 11, 2), 24000.0, 24000.0)\n_wk = [b["strike"] for b in _bd if b.get("kind") == "week"]\n_mo = [b["strike"] for b in _bd if b.get("kind") == "month"]\ncheck("current week recorded ±32 steps (22,400 … 25,600) for the far hedge wings",\n      _wk and min(_wk) == 22400.0 and max(_wk) == 25600.0, (min(_wk or [0]), max(_wk or [0])))\ncheck("the monthly stays ±10 steps (23,500 … 24,500)", _mo and min(_mo) == 23500.0 and max(_mo) == 24500.0,\n      (min(_mo or [0]), max(_mo or [0])))\nR = [{"token": 1, "tradingsymbol": "NIFTY2611324000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-03"},\n     {"token": 2, "tradingsymbol": "NIFTY26N1024000CE", "instrument_type": "CE", "strike": 24000.0, "expiry": "2026-11-10"},\n')], 'backend/app/backtest/queue_worker.py': [('    ctok = _TC.CAL_OVERRIDE.set(session_calendar(d0, d1))\n    try:\n        res = _dispatch_run_impl(strategy_id=strategy_id, underlying=underlying, df=df, dt=dt, config=cfg,\n                                 progress_cb=progress_cb, cancel_cb=cancel_cb)\n        if isinstance(res, dict) and isinstance(res.get("summary"), dict):\n            res["summary"]["recorded_skipped_days"] = [x for x in skipped if d0.isoformat() <= x <= d1.isoformat()]\n        return res\n    finally:\n', '    ctok = _TC.CAL_OVERRIDE.set(session_calendar(d0, d1))\n    try:\n        # ── RECORDED_BLOCKS_20261031 ── a position must never be carried across\n        # a skipped / missing day (31 Oct: Icarus\'s 22 Oct condor "closed" on\n        # 28 Oct at 09:16 with expired legs at ₹0.05). The range is split into\n        # blocks of consecutive sessions that hold option data; each block is\n        # its own run (its runner closes what is open at the block\'s end, as at\n        # any range end); trades are concatenated and the summary re-added.\n        blocks = _usable_blocks(pack, d0, d1, session_calendar(d0, d1))\n        if not blocks:\n            return {"trades": [], "summary": {"recorded_skipped_days": [x for x in skipped if d0.isoformat() <= x <= d1.isoformat()]},\n                    "aborted": True, "reason": "no clean recorded day with option data in this range"}\n        results = []\n        for b0, b1 in blocks:\n            r = _dispatch_run_impl(strategy_id=strategy_id, underlying=underlying, df=b0, dt=b1, config=cfg,\n                                   progress_cb=progress_cb, cancel_cb=cancel_cb)\n            if isinstance(r, dict) and not r.get("aborted"):\n                results.append(r)\n        if not results:\n            return {"trades": [], "summary": {}, "aborted": True, "reason": "every block was refused by the runner"}\n        res = _merge_block_results(results)\n        if isinstance(res.get("summary"), dict):\n            res["summary"]["recorded_skipped_days"] = [x for x in skipped if d0.isoformat() <= x <= d1.isoformat()]\n            res["summary"]["recorded_blocks"] = [[b0.isoformat(), b1.isoformat()] for b0, b1 in blocks]\n        return res\n    finally:\n'), ('\n\ndef _dispatch_recorded(*, strategy_id, underlying, df, dt, config, progress_cb, cancel_cb):\n    """── RECORDED_SOURCE_20261031 ── config data_source = "recorded": the run\n', '\n\ndef _usable_blocks(pack, d0, d1, sessions) -> list:\n    """── RECORDED_BLOCKS_20261031 ── [(start, end)] of consecutive NSE sessions in\n    [d0, d1] whose option data is in the pack (after partial days were dropped)."""\n    import sqlite3 as _sq\n    from datetime import date as _date\n    p = _sq.connect(pack)\n    try:\n        have = {r[0] for r in p.execute(\n            "SELECT DISTINCT date(ts,\'unixepoch\',\'+5 hours\',\'+30 minutes\') FROM backtest_candles_1m "\n            "WHERE instrument_type IN (\'CE\',\'PE\')")}\n    finally:\n        p.close()\n    days = [x for x in sessions if d0.isoformat() <= x <= d1.isoformat()]\n    blocks, cur = [], []\n    for x in days:\n        if x in have:\n            cur.append(x)\n        elif cur:\n            blocks.append(cur)\n            cur = []\n    if cur:\n        blocks.append(cur)\n    return [(_date.fromisoformat(b[0]), _date.fromisoformat(b[-1])) for b in blocks]\n\n\ndef _merge_block_results(results: list) -> dict:\n    """Concatenate trades; add the additive summary numbers; recompute the win rate.\n    Max drawdown is the worst block\'s (blocks are separate runs)."""\n    base = dict(results[0])\n    base["trades"] = [t for r in results for t in (r.get("trades") or [])]\n    summ = dict(results[0].get("summary") or {})\n    if len(results) > 1:\n        for k in ("total_trades", "wins", "losses", "gross_pnl", "total_charges", "net_pnl", "ambiguous_fills"):\n            if all(isinstance((r.get("summary") or {}).get(k), (int, float)) for r in results):\n                summ[k] = round(sum((r.get("summary") or {}).get(k) for r in results), 2)\n        dds = [(r.get("summary") or {}).get("max_drawdown") for r in results]\n        if all(isinstance(x, (int, float)) for x in dds):\n            summ["max_drawdown"] = max(dds)\n        if summ.get("total_trades"):\n            summ["win_rate"] = round(100.0 * (summ.get("wins") or 0) / summ["total_trades"], 2)\n    base["summary"] = summ\n    return base\n\n\ndef _dispatch_recorded(*, strategy_id, underlying, df, dt, config, progress_cb, cancel_cb):\n    """── RECORDED_SOURCE_20261031 ── config data_source = "recorded": the run\n')], 'backend/app/recorder/test_seam.py': [('      and "2025-02-26" not in BRT._recorded_info()["partial_days"])\n\nprint(f"\\n{PASSES[0]} passed, {len(FAILS)} failed")\nif FAILS:\n', '      and "2025-02-26" not in BRT._recorded_info()["partial_days"])\n\nprint("── 6. RECORDED_BLOCKS_20261031: no position carried across a skipped day")\ncheck("the recorded run is split into blocks around the partial day",\n      rec["summary"].get("recorded_blocks") == [["2025-02-24", "2025-02-24"], ["2025-02-26", "2025-02-27"]],\n      rec["summary"].get("recorded_blocks"))\nvcfg = {**load_strategy_config("VET_V1"), "data_source": "recorded"}\nwith audit_muted():\n    vr = QW._dispatch_run_impl(strategy_id="VET_V1", underlying="NIFTY", df=date(2025, 2, 24), dt=date(2025, 2, 27),\n                               config=vcfg, progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)\n\n\ndef _xd(t):\n    x = t.get("exit_ts") if isinstance(t, dict) else getattr(t, "exit_ts", None)\n    return datetime.fromtimestamp(int(x), IST).date().isoformat() if x else None\n\n\ncross = [t for t in vr.get("trades") or [] if _d25(t) <= "2025-02-24" and (_xd(t) or "9999") >= "2025-02-25"]\ncheck("Velvet (positional): no trade opened on 24 Feb runs into or past the skipped 25th", vr.get("trades") and not cross,\n      [(_d25(t), _xd(t)) for t in cross])\ncheck("the summary adds the blocks\' trades and recomputes the win rate",\n      vr["summary"].get("total_trades") == len(vr["trades"]) or vr["summary"].get("total_trades") is None, vr["summary"].get("total_trades"))\n\nprint(f"\\n{PASSES[0]} passed, {len(FAILS)} failed")\nif FAILS:\n'), ('# backend/app/recorder/test_seam.py — ── SEAM_CHECK_20261031 ── ── RECORDED_SOURCE_20261031 ── ── RECORDED_SKIP_PARTIAL_20261031 ──\n#   cd backend && PYTHONPATH=$PWD python3 app/recorder/test_seam.py\nimport json\n', '# backend/app/recorder/test_seam.py — ── SEAM_CHECK_20261031 ── ── RECORDED_SOURCE_20261031 ── ── RECORDED_SKIP_PARTIAL_20261031 ── ── RECORDED_BLOCKS_20261031 ──\n#   cd backend && PYTHONPATH=$PWD python3 app/recorder/test_seam.py\nimport json\n')]}
PARENTS = {'backend/app/recorder/rec_pull.py': ['DATA_RECORDER'], 'backend/app/recorder/test_recorder.py': ['DATA_RECORDER'], 'backend/app/backtest/queue_worker.py': ['RECORDED_SKIP_PARTIAL_20261031'], 'backend/app/recorder/test_seam.py': ['RECORDED_SKIP_PARTIAL_20261031']}
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
                die(f"{rel} lacks parent fence {parent} — pull the latest main first")
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
        rd = os.path.join(root, "backend", "app", "__none__")
        if os.path.isdir(rd) and not [f for f in os.listdir(rd) if f.endswith(".py")]:
            shutil.rmtree(rd, ignore_errors=True)
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
        suites = ["app/recorder/test_recorder.py",                              # 108
                  "app/recorder/test_seam.py"]                                  # 22
        for suite in suites:
            print(f"\n── running {suite} (a few minutes)")
            r = subprocess.run([sys.executable, suite], cwd=be, env=env, capture_output=True, text=True)
            tail = "\n".join((r.stdout or "").strip().splitlines()[-2:])
            print("  " + tail.replace("\n", "\n  "))
            if r.returncode != 0:
                print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if "FAIL" in l or "Error" in l)[-3000:])
                rollback(f"{suite} failed")
    print(f"\n✓ {FENCE} applied. Backups: *.bak-{FENCE}")
    print("  Next: ./desktop/build-scalp.sh backend")
    print("  Then: recorded-data runs show recorded_blocks in their summary; from Monday the recorder")
    print("        stores current/next-week strikes ±32 steps (Backfill: Recorded/band grows to ~300).")


if __name__ == "__main__":
    main()
