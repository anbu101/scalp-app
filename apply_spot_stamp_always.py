#!/usr/bin/env python3
# apply_spot_stamp_always.py — ── SPOT_STAMP_ALWAYS_20261031 ── ── LOG_RETENTION_60_20261031 ──
#
# 1. Spot timestamp repair at EVERY boot. spot_stamp_repair.py fixed the
#    one-minute-early Dhan spot stamps on 18 Aug and set a one-time marker, so
#    it never looked again — the seam check then found 9 days (19–31 Aug)
#    written after the marker and still early. Now:
#      * the marker no longer stops it — every spot day is re-checked;
#      * same evidence rule as before: only a day whose first bar in the
#        09:00–09:30 window is 09:14 is moved (session rows 09:14–15:28 →
#        09:15–15:29, one transaction per day); strays are never moved;
#      * every lookup uses (underlying, expiry='', ts) — the corpus's
#        idx_bt1m_under_exp_ts — instead of a full-table scan, and the boot
#        hook runs it on its own thread, so start-up never waits on it;
#      * quiet when clean; one audit line per repaired day.
# 2. Audit logs kept 60 days instead of 10 (the Aug backfill's log was
#    already gone when we needed it).
#
#   EDIT backend/app/backtest/spot_stamp_repair.py · utils/housekeeping.py ·
#        backend/app/api_server.py (background start)
#   NEW  backend/app/backtest/test_spot_stamp_always.py (11 checks)
#
#   cd <repo root> && python3 apply_spot_stamp_always.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh backend
#
# Run ONLY this script — it carries every file it writes.

import os
import shutil
import subprocess
import sys

FENCE = "SPOT_STAMP_ALWAYS_20261031"
NEW_FILES = {'backend/app/backtest/test_spot_stamp_always.py': '# backend/app/backtest/test_spot_stamp_always.py — ── SPOT_STAMP_ALWAYS_20261031 ── ── LOG_RETENTION_60_20261031 ──\n#   cd backend && PYTHONPATH=$PWD python3 app/backtest/test_spot_stamp_always.py\nimport os\nimport sqlite3\nimport sys\nimport tempfile\nimport time\nfrom datetime import date, datetime, timedelta, timezone\nfrom pathlib import Path\n\nFAILS, PASSES = [], [0]\n\n\ndef check(name, cond, detail=""):\n    if cond:\n        PASSES[0] += 1\n        print(f"  PASS  {name}")\n    else:\n        FAILS.append(name)\n        print(f"  FAIL  {name}  [{str(detail)[:300]}]")\n\n\nTMP = tempfile.mkdtemp(prefix="stamp_")\nos.environ["HOME"] = TMP\nIST = timezone(timedelta(hours=5, minutes=30))\nfrom app.backtest.replay.replay_synth import build          # noqa: E402\nfrom app.backtest import spot_stamp_repair as SSR           # noqa: E402\n\nDB = os.path.join(TMP, "backtest.db")\nbuild(DB, date(2025, 2, 17), date(2025, 2, 28))\nc = sqlite3.connect(DB)\nc.execute("UPDATE backtest_candles_1m SET underlying=\'NIFTY\'")\n\n\ndef ds(d):\n    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())\n\n\nBAD = [date(2025, 2, 24), date(2025, 2, 26)]\nfor d in BAD:\n    rows = c.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?", (ds(d), ds(d) + 86400)).fetchall()\n    c.execute("DELETE FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?", (ds(d), ds(d) + 86400))\n    c.executemany("INSERT INTO backtest_candles_1m VALUES (%s)" % ",".join("?" * len(rows[0])),\n                  [(r[0], r[1] - 60) + tuple(r[2:]) for r in rows])\nstray = ds(BAD[0]) + 5 * 3600 + 38 * 60\ntmpl = c.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' LIMIT 1").fetchone()\nc.execute("INSERT INTO backtest_candles_1m VALUES (%s)" % ",".join("?" * len(tmpl)), (tmpl[0], stray) + tuple(tmpl[2:]))\nc.execute("CREATE TABLE IF NOT EXISTS bt_meta (key TEXT PRIMARY KEY, value TEXT)")\nc.execute("INSERT OR REPLACE INTO bt_meta (key, value) VALUES (\'spot_stamp_repaired\', \'1\')")   # the old one-time marker\nc.commit()\nbefore = c.execute("SELECT COUNT(*), SUM(close) FROM backtest_candles_1m WHERE instrument_type=\'SPOT\'").fetchone()\ngood_before = c.execute("SELECT ts, close FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<? ORDER BY ts",\n                        (ds(date(2025, 2, 25)), ds(date(2025, 2, 25)) + 86400)).fetchall()\nc.close()\n\nprint("── 1. repair ignores the one-time marker")\nLOG = []\nr = SSR.ensure_spot_stamps(DB, log=LOG.append)\ncheck("marker already set, yet the two 09:14 days are repaired", r["days_repaired"] == 2 and r["rows_moved"] == 2 * 375, r)\nc = sqlite3.connect(DB)\nfirsts = {d: datetime.fromtimestamp(c.execute("SELECT MIN(ts) FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                                               (ds(d) + 9 * 3600, ds(d) + 9 * 3600 + 1800)).fetchone()[0], IST).strftime("%H:%M") for d in BAD}\ncheck("…both now open at 09:15", set(firsts.values()) == {"09:15"}, firsts)\ncheck("the out-of-session stray print (05:38) is not moved", c.execute(\n    "SELECT COUNT(*) FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts=?", (stray,)).fetchone()[0] == 1)\ncheck("correct days are untouched", c.execute("SELECT ts, close FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<? ORDER BY ts",\n                                               (ds(date(2025, 2, 25)), ds(date(2025, 2, 25)) + 86400)).fetchall() == good_before)\ncheck("no row lost or duplicated", c.execute("SELECT COUNT(*), SUM(close) FROM backtest_candles_1m WHERE instrument_type=\'SPOT\'").fetchone() == before)\nc.close()\ncheck("each repaired day is logged", sum("moved one minute later" in x for x in LOG) == 2, LOG)\nLOG.clear()\nr2 = SSR.ensure_spot_stamps(DB, log=LOG.append)\ncheck("next boot: nothing to do, and nothing logged (quiet when clean)", r2["days_repaired"] == 0 and LOG == [], (r2, LOG))\nc = sqlite3.connect(DB)\nrows = c.execute("SELECT * FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?", (ds(date(2025, 2, 27)), ds(date(2025, 2, 27)) + 86400)).fetchall()\nc.execute("DELETE FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?", (ds(date(2025, 2, 27)), ds(date(2025, 2, 27)) + 86400))\nc.executemany("INSERT INTO backtest_candles_1m VALUES (%s)" % ",".join("?" * len(rows[0])), [(x[0], x[1] - 60) + tuple(x[2:]) for x in rows])\nc.commit()\nc.close()\nr3 = SSR.ensure_spot_stamps(DB, log=lambda *a: None)\ncheck("a day written early LATER (a new backfill) is repaired on the next boot", r3["days_repaired"] == 1, r3)\n\nprint("── 2. audit logs kept 60 days")\nfrom app.utils import housekeeping as HK                    # noqa: E402\ncheck("retention is 60 days", HK.LOG_RETENTION_DAYS == 60)\nld = Path(TMP) / "logs"\nld.mkdir()\nold, mid = ld / "2026-08-20.log", ld / "2026-10-01.log"\nfor f, age in ((old, 61), (mid, 30)):\n    f.write_text("x")\n    t = time.time() - age * 86400\n    os.utime(f, (t, t))\nHK._cleanup_dir(ld, "*.log", HK.LOG_RETENTION_DAYS)\ncheck("a 61-day-old log is removed; a 30-day-old log is kept", not old.exists() and mid.exists())\n\nprint("── 3. boot runs it on its own thread")\nsrc = (Path(SSR.__file__).resolve().parents[1] / "api_server.py").read_text()\ncheck("api_server starts the check on a background thread", "spot-stamp-check" in src and "_spot_stamp_bg" in src)\n\nprint(f"\\n{PASSES[0]} passed, {len(FAILS)} failed")\nif FAILS:\n    sys.exit(1)\nprint("ALL SPOT_STAMP_ALWAYS CHECKS PASSED")\n'}
EDITS = {'backend/app/backtest/spot_stamp_repair.py': [('            days_repaired += 1\n            rows_moved += len(shifted)\n            if days_repaired % 100 == 0:\n                log(f"[SPOT_STAMP] repaired {days_repaired} day(s)…")\n\n        if not dry_run:\n            _marker_set(conn)\n        log(f"[SPOT_STAMP] done: checked={days_checked} "\n            f"repaired={days_repaired} rows_moved={rows_moved}"\n            + (" (DRY RUN — nothing written)" if dry_run else ""))\n        return {"status": "dry_run" if dry_run else "repaired",\n                "days_checked": days_checked,\n', '            days_repaired += 1\n            rows_moved += len(shifted)\n            log(f"[SPOT_STAMP] {und} {d}: first bar was 09:14 — {len(shifted)} spot rows moved one minute later")\n\n        if not dry_run:\n            _marker_set(conn)\n        if dry_run or days_repaired:\n            log(f"[SPOT_STAMP] done: checked={days_checked} "\n                f"repaired={days_repaired} rows_moved={rows_moved}"\n                + (" (DRY RUN — nothing written)" if dry_run else ""))\n        return {"status": "dry_run" if dry_run else "repaired",\n                "days_checked": days_checked,\n'), ('                conn.execute("BEGIN")\n                conn.execute(\n                    "DELETE FROM backtest_candles_1m "\n                    "WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                    (sess_lo, sess_hi))\n                conn.executemany(\n                    "INSERT OR REPLACE INTO backtest_candles_1m "\n', '                conn.execute("BEGIN")\n                conn.execute(\n                    "DELETE FROM backtest_candles_1m WHERE underlying=? AND expiry=\'\' "\n                    "AND instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                    (und, sess_lo, sess_hi))\n                conn.executemany(\n                    "INSERT OR REPLACE INTO backtest_candles_1m "\n'), ('                "SELECT instrument_token, ts, underlying, tradingsymbol, "\n                "instrument_type, strike, expiry, open, high, low, close, "\n                "volume, oi FROM backtest_candles_1m "\n                "WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                (sess_lo, sess_hi)).fetchall()\n            if not src:\n                continue\n', '                "SELECT instrument_token, ts, underlying, tradingsymbol, "\n                "instrument_type, strike, expiry, open, high, low, close, "\n                "volume, oi FROM backtest_candles_1m WHERE underlying=? AND expiry=\'\' "\n                "AND instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                (und, sess_lo, sess_hi)).fetchall()\n            if not src:\n                continue\n'), ('            win_hi = ds + (9 * 60 + 30) * 60     # 09:30 IST\n            row = cur.execute(\n                "SELECT MIN(ts) FROM backtest_candles_1m "\n                "WHERE instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                (win_lo, win_hi)).fetchone()\n            if not row or not row[0]:\n                log(f"[SPOT_STAMP] {d}: no bars in 09:00-09:30 — SKIPPED "\n                    f"(partial/anomalous day, manual review)")\n                continue\n            first = datetime.fromtimestamp(int(row[0]), IST)\n            if (first.hour, first.minute) == SESSION_OPEN_HM:\n                continue                      # already correct\n            if (first.hour, first.minute) != (9, 14):\n                log(f"[SPOT_STAMP] {d}: opening-window first bar "\n                    f"{first:%H:%M} — neither 09:14 nor 09:15; SKIPPED "\n                    f"(manual review)")\n                continue\n\n', '            win_hi = ds + (9 * 60 + 30) * 60     # 09:30 IST\n            row = cur.execute(\n                "SELECT MIN(ts) FROM backtest_candles_1m WHERE underlying=? AND expiry=\'\' "\n                "AND instrument_type=\'SPOT\' AND ts>=? AND ts<?",\n                (und, win_lo, win_hi)).fetchone()\n            if not row or not row[0]:\n                continue                      # partial / anomalous day (reported by the dry run)\n            first = datetime.fromtimestamp(int(row[0]), IST)\n            if (first.hour, first.minute) == SESSION_OPEN_HM:\n                continue                      # already correct\n            if (first.hour, first.minute) != (9, 14):\n                if dry_run:\n                    log(f"[SPOT_STAMP] {und} {d}: opening-window first bar "\n                        f"{first:%H:%M} — neither 09:14 nor 09:15; SKIPPED (manual review)")\n                continue\n\n'), ('    days_checked = days_repaired = rows_moved = 0\n    try:\n        if not dry_run and _marker_get(cur):\n            return {"status": "already_done", "days_checked": 0,\n                    "days_repaired": 0, "rows_moved": 0}\n\n        has_spot = cur.execute(\n            "SELECT 1 FROM backtest_candles_1m "\n            "WHERE instrument_type=\'SPOT\' LIMIT 1").fetchone()\n        if not has_spot:\n            if not dry_run:\n                _marker_set(conn)      # nothing to repair; future fills are born correct\n            return {"status": "no_spot_rows", "days_checked": 0,\n                    "days_repaired": 0, "rows_moved": 0}\n\n        days = [r[0] for r in cur.execute(\n            "SELECT DISTINCT date(ts,\'unixepoch\',\'+5 hours\',\'+30 minutes\') d "\n            "FROM backtest_candles_1m WHERE instrument_type=\'SPOT\' "\n            "ORDER BY d")]\n        log(f"[SPOT_STAMP] scanning {len(days)} spot day(s)")\n\n        for d in days:\n            days_checked += 1\n            ds = int(datetime.fromisoformat(d).replace(tzinfo=IST).timestamp())\n', '    days_checked = days_repaired = rows_moved = 0\n    try:\n        # ── SPOT_STAMP_ALWAYS_20261031 ── no longer gated by the one-time\n        # marker: the seam check (31 Oct) found 9 days (19–31 Aug) written\n        # AFTER the marker was set and still one minute early. Every boot now\n        # re-checks every spot day; correct days cost one indexed lookup.\n        # All lookups go through (underlying, expiry=\'\', ts) — the corpus\'s\n        # idx_bt1m_under_exp_ts — instead of a full-table scan.\n        unds = [u for u in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX") if cur.execute(\n            "SELECT 1 FROM backtest_candles_1m WHERE underlying=? AND expiry=\'\' "\n            "AND instrument_type=\'SPOT\' LIMIT 1", (u,)).fetchone()]\n        if not unds:\n            return {"status": "no_spot_rows", "days_checked": 0,\n                    "days_repaired": 0, "rows_moved": 0}\n\n        work = []\n        for u in unds:\n            work += [(u, r[0]) for r in cur.execute(\n                "SELECT DISTINCT date(ts,\'unixepoch\',\'+5 hours\',\'+30 minutes\') d "\n                "FROM backtest_candles_1m WHERE underlying=? AND expiry=\'\' "\n                "AND instrument_type=\'SPOT\' ORDER BY d", (u,))]\n        log(f"[SPOT_STAMP] scanning {len(work)} spot day(s)") if dry_run else None\n\n        for und, d in work:\n            days_checked += 1\n            ds = int(datetime.fromisoformat(d).replace(tzinfo=IST).timestamp())\n')], 'backend/app/utils/housekeeping.py': [('STATE_DIR = APP_HOME / "state"\n\nLOG_RETENTION_DAYS = 10\nCANDLE_DEBUG_RETENTION_DAYS = 7\n\n', 'STATE_DIR = APP_HOME / "state"\n\nLOG_RETENTION_DAYS = 60   # ── LOG_RETENTION_60_20261031 ── was 10: parity findings get checked weeks later\nCANDLE_DEBUG_RETENTION_DAYS = 7\n\n')], 'backend/app/api_server.py': [('    # spot stamp shift (see app/backtest/spot_stamp_repair.py). Runs on\n    # every machine, no-ops instantly once the marker is set.\n    try:\n        from app.backtest.spot_stamp_repair import ensure_spot_stamps\n        _spot = ensure_spot_stamps(log=write_audit_log)\n        write_audit_log(f"[BACKTEST][SPOT_STAMP] {_spot}")\n    except Exception as _e:\n        write_audit_log(f"[BACKTEST][SPOT_STAMP][FAIL] {_e!r}")\n    if _era.get("status") not in ("already_done", "no_db"):\n        print(f"[EXPIRY_ERA_STARTUP] {_era}")\n', '    # spot stamp shift (see app/backtest/spot_stamp_repair.py). Runs on\n    # every machine, no-ops instantly once the marker is set.\n    # ── SPOT_STAMP_ALWAYS_20261031 ── every boot, on its own thread (boot\n    # never waits on the scan); logs only when it repairs something.\n    def _spot_stamp_bg():\n        try:\n            from app.backtest.spot_stamp_repair import ensure_spot_stamps\n            _spot = ensure_spot_stamps(log=write_audit_log)\n            if _spot.get("days_repaired"):\n                write_audit_log(f"[BACKTEST][SPOT_STAMP] {_spot}")\n        except Exception as _e:\n            write_audit_log(f"[BACKTEST][SPOT_STAMP][FAIL] {_e!r}")\n    import threading as _ss_threading\n    _ss_threading.Thread(target=_spot_stamp_bg, daemon=True, name="spot-stamp-check").start()\n    if _era.get("status") not in ("already_done", "no_db"):\n        print(f"[EXPIRY_ERA_STARTUP] {_era}")\n')]}
PARENTS = {'backend/app/backtest/spot_stamp_repair.py': ['SPOT_STAMP_REPAIR'], 'backend/app/utils/housekeeping.py': ['LOG_RETENTION_DAYS'], 'backend/app/api_server.py': ['SPOT_STAMP_REPAIR']}
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
        suites = ["app/backtest/test_spot_stamp_always.py"]                    # 11
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
    print("  Then: every boot re-checks spot stamps in the background ([BACKTEST][SPOT_STAMP] only when it fixes a day);")
    print("        audit logs are kept 60 days.")


if __name__ == "__main__":
    main()
