#!/usr/bin/env python3
# apply_recorded_source_fix2.py — ── RECORDED_SOURCE_FIX2_20261031 ──
#
# "Use recorded data" ticked, Run backtest still said "no NIFTY option data in
# range": the Run button does NOT go through queue_worker._dispatch_run_impl —
# backtest_routes.run_start keeps its own hand-maintained dispatch chain with
# backtest.db hard-coded per strategy, so the recorded-data switch (added to the
# queue dispatcher) never reached it. A recorded-data Run is now handed to the
# dispatcher, which builds the corpus ∪ recorder pack, sets the real calendar
# and enforces the seam-check / NIFTY / 120-day rules; the result is persisted
# exactly like any other run (without the data_source key in its config).
# The new check fails on the old route (the recorded path was never called).
#
#   EDIT backend/app/api/backtest_routes.py · backend/app/recorder/test_seam.py (15)
#   Requires apply_seam_check.py.
#
#   cd <repo root> && python3 apply_recorded_source_fix2.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh backend
#
# Run ONLY this script — it carries every file it writes.

import os
import shutil
import subprocess
import sys

FENCE = "RECORDED_SOURCE_FIX2_20261031"
NEW_FILES = {}
EDITS = {'backend/app/api/backtest_routes.py': [('            from app.event_bus.audit_logger import audit_muted\n            with audit_muted():\n                if req.strategy_id in ("BB_V1", "BB_V2"):\n                    from app.utils.app_paths import APP_HOME\n                    from app.backtest.bb.backtest_bb_runner import run_bb_backtest\n', '            from app.event_bus.audit_logger import audit_muted\n            with audit_muted():\n                # ── RECORDED_SOURCE_FIX2_20261031 ── "Use recorded data": the Run\n                # button\'s own dispatch chain hard-codes backtest.db per strategy,\n                # so a recorded-data run is handed to queue_worker._dispatch_run_impl\n                # (which builds the corpus ∪ recorder pack, sets the calendar, and\n                # enforces the seam-check / NIFTY / 120-day rules). Same result\n                # shape, persisted below exactly like any other run.\n                if str((req.config_override or {}).get("data_source") or "").lower() == "recorded":\n                    from app.backtest import queue_worker as _QW\n                    result = _QW._dispatch_run_impl(\n                        strategy_id=req.strategy_id, underlying=req.underlying, df=df, dt=dt,\n                        config=(req.config_override or {}), progress_cb=_cb,\n                        cancel_cb=lambda: _JOBS.run.get("cancel", False)) or {}\n                    result.setdefault("strategy_id", req.strategy_id)\n                    result.setdefault("config", {k: v for k, v in (req.config_override or {}).items() if k != "data_source"})\n                elif req.strategy_id in ("BB_V1", "BB_V2"):\n                    from app.utils.app_paths import APP_HOME\n                    from app.backtest.bb.backtest_bb_runner import run_bb_backtest\n')], 'backend/app/recorder/test_seam.py': [('check("coverage info: seam passed, recorded_from from the recorder\'s clean days", info["seam_ok"] is True and "recorded_from" in info, info)\n\nprint(f"\\n{PASSES[0]} passed, {len(FAILS)} failed")\nif FAILS:\n', 'check("coverage info: seam passed, recorded_from from the recorder\'s clean days", info["seam_ok"] is True and "recorded_from" in info, info)\n\nprint("── 4. RECORDED_SOURCE_FIX2_20261031: the Run button path")\nimport time as _time                                         # noqa: E402\nfrom app.api.backtest_routes import run_start, RunRequest, _JOBS   # noqa: E402\n_CALLS = []\n_orig_rec = QW._dispatch_recorded\nQW._dispatch_recorded = lambda **k: (_CALLS.append(1), _orig_rec(**k))[1]\nwith audit_muted():\n    run_start(RunRequest(strategy_id="EMC_V1", underlying="NIFTY", date_from="2025-02-24", date_to="2025-02-27",\n                         config_override={**load_strategy_config("EMC_V1"), "data_source": "recorded"}))\n    for _ in range(600):\n        if not _JOBS.run["running"] or _JOBS.run.get("result") or _JOBS.run.get("error"):\n            if _JOBS.run.get("result") or _JOBS.run.get("error"):\n                break\n        _time.sleep(0.1)\ncheck("Run button + \'Use recorded data\' → the run goes through the recorded pack and is persisted",\n      _CALLS == [1] and _JOBS.run.get("error") is None and (_JOBS.run.get("result") or {}).get("run_id"),\n      (_CALLS, _JOBS.run.get("error"), _JOBS.run.get("result")))\ncheck("…and the stored config carries no data_source key", "data_source" not in ((_JOBS.run.get("result") or {}).get("config") or {}))\n\nprint(f"\\n{PASSES[0]} passed, {len(FAILS)} failed")\nif FAILS:\n')]}
PARENTS = {'backend/app/api/backtest_routes.py': ['RECORDED_SOURCE_20261031'], 'backend/app/recorder/test_seam.py': ['SEAM_CHECK_20261031']}
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
        suites = ["app/recorder/test_seam.py"]                                  # 15
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
    print("  Then: Backtest → Run → tick 'Use recorded data' → Run backtest (14–30 Oct).")


if __name__ == "__main__":
    main()
