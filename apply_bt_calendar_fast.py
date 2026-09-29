#!/usr/bin/env python3
# apply_bt_calendar_fast.py — fence BT_CALENDAR_FAST_20260929
# (requires BT_DTE_SESSIONS_20260911 — the corpus trading-day calendar)
#
# WHY RESULTS TOOK 15–20 s AFTER A RUN: the results page fetches
# GET /api/backtest/runs/{id}, which attaches sessions-to-expiry (DTE) to
# every trade from the corpus trading-day calendar. That calendar was
#   SELECT DISTINCT date(ts) FROM backtest_candles_1m
#    WHERE underlying=? AND instrument_type IN ('CE','PE')
# — a walk over EVERY NIFTY option row (measured 3.3 s per 6M rows; the full
# corpus is several times that). It was cached on backtest.db's mtime, but a
# finished run is persisted INTO backtest.db, so the cache was invalid exactly
# when you were waiting for the results — every single run.
#
# FIX
#   trading_calendar.py  same dates, found with one indexed existence probe per
#                        calendar day (LIMIT 1 seek on idx_bt1m_under_type_ts,
#                        CE then PE) between the first and last print: 5.5 ms
#                        on 6M rows vs 3.3 s. A span > 40 years falls back to
#                        the old scan. Cache unchanged (a miss now costs ms).
#   backtest_routes.py   run detail rendered straight to JSON (skips FastAPI's
#                        per-value encoder walk: ~250 ms per 6k trades), with
#                        the old path as fallback for anything non-JSON.
#   test_trading_calendar_fast.py (new, 23 checks): probe == old scan on a
#                        1.26M-row corpus incl. Saturday session, PE-only day,
#                        SPOT/FUT-only and other-underlying days excluded, IST
#                        day edges; cache + mtime invalidation; attach_dte and
#                        the route's JSON byte-for-byte the same as before.
#
# Deploy: ./desktop/build-scalp.sh backend   (no UI change; results, Compare,
# Breakdown and Trade Replay all read the same calendar)
#
# Run ONLY this script:
#   cd /Users/anbu/dev/scalp-app && python3 apply_bt_calendar_fast.py [--allow-dirty]
from __future__ import annotations
import base64, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = "BT_CALENDAR_FAST_20260929"
ROOT = os.path.dirname(os.path.abspath(__file__))
MIRRORS = (("backend/app", "desktop/src-tauri/backend/app"),)
PREREQ = {"backend/app/backtest/repo/trading_calendar.py": "BT_DTE_SESSIONS_20260911",
          "backend/app/api/backtest_routes.py": "def run_detail(run_id: str):"}
EDITS = {"backend/app/backtest/repo/trading_calendar.py": [["    try:\n        c = sqlite3.connect(db)\n        rows = c.execute(\n            \"SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d\"\n            \" FROM backtest_candles_1m WHERE underlying = ?\"\n            \" AND instrument_type IN ('CE','PE') ORDER BY d\", (underlying,)).fetchall()\n        c.close()\n        out = [r[0] for r in rows if r[0]]\n", "    try:\n        c = sqlite3.connect(db)\n        try:\n            out = _probe_days(c, underlying)\n        finally:\n            c.close()\n"], ["def attach_dte(run: dict, db_path: Optional[str] = None) -> dict:", "# \u2500\u2500 BT_CALENDAR_FAST_20260929 \u2500\u2500 the calendar used to be\n#   SELECT DISTINCT date(ts) \u2026 WHERE underlying=? AND instrument_type IN ('CE','PE')\n# which walks EVERY option row of the underlying (tens of millions for NIFTY:\n# ~3 s per 6M rows, ~15\u201320 s on the full corpus). The cache above is keyed on\n# the backtest.db mtime, and runs are persisted into that same file \u2014 so the\n# first results fetch after every completed run paid the full scan. Now: one\n# indexed existence probe per calendar day (idx_bt1m_under_type_ts seek,\n# LIMIT 1), CE first then PE \u2014 the identical set of dates in milliseconds.\n_MAX_PROBE_DAYS = 366 * 40          # a stray timestamp decades off \u2192 fall back to the scan\n\n_PROBE_SQL = (\"SELECT 1 FROM backtest_candles_1m WHERE underlying = ? AND instrument_type = ?\"\n              \" AND ts >= ? AND ts < ? LIMIT 1\")\n\n\ndef _scan_days(c: sqlite3.Connection, underlying: str) -> List[str]:\n    \"\"\"The original full DISTINCT scan (reference definition; fallback only).\"\"\"\n    rows = c.execute(\n        \"SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d\"\n        \" FROM backtest_candles_1m WHERE underlying = ?\"\n        \" AND instrument_type IN ('CE','PE') ORDER BY d\", (underlying,)).fetchall()\n    return [r[0] for r in rows if r[0]]\n\n\ndef _probe_days(c: sqlite3.Connection, underlying: str) -> List[str]:\n    \"\"\"IST dates with \u22651 CE or PE candle for `underlying` \u2014 same set as\n    _scan_days, via per-day index seeks between the first and last print.\"\"\"\n    lo = hi = None\n    for typ in (\"CE\", \"PE\"):\n        # separate MIN and MAX: SQLite only turns a LONE min()/max() into a\n        # single index seek \u2014 MIN(ts), MAX(ts) together walks the whole range\n        a = c.execute(\"SELECT MIN(ts) FROM backtest_candles_1m\"\n                      \" WHERE underlying = ? AND instrument_type = ?\", (underlying, typ)).fetchone()[0]\n        b = c.execute(\"SELECT MAX(ts) FROM backtest_candles_1m\"\n                      \" WHERE underlying = ? AND instrument_type = ?\", (underlying, typ)).fetchone()[0]\n        if a is not None:\n            lo = a if lo is None else min(lo, a)\n            hi = b if hi is None else max(hi, b)\n    if lo is None:\n        return []\n    d = datetime.fromtimestamp(int(lo), _IST).date()\n    end = datetime.fromtimestamp(int(hi), _IST).date()\n    if (end - d).days > _MAX_PROBE_DAYS:\n        return _scan_days(c, underlying)\n    out: List[str] = []\n    while d <= end:\n        ds = int(datetime(d.year, d.month, d.day, tzinfo=_IST).timestamp())\n        if (c.execute(_PROBE_SQL, (underlying, \"CE\", ds, ds + 86400)).fetchone()\n                or c.execute(_PROBE_SQL, (underlying, \"PE\", ds, ds + 86400)).fetchone()):\n            out.append(d.isoformat())\n        d += timedelta(days=1)\n    return out\n\n\ndef attach_dte(run: dict, db_path: Optional[str] = None) -> dict:"]], "backend/app/api/backtest_routes.py": [["        d = attach_dte(d)\n    except Exception as _e:\n        write_audit_log(f\"[BACKTEST][CALENDAR] {_e!r}\")\n    return d\n", "        d = attach_dte(d)\n    except Exception as _e:\n        write_audit_log(f\"[BACKTEST][CALENDAR] {_e!r}\")\n    # \u2500\u2500 BT_CALENDAR_FAST_20260929 \u2500\u2500 the detail is plain JSON types (SQLite\n    # scalars + json.loads'd summary/config): render it directly instead of\n    # FastAPI's per-value jsonable_encoder walk (~250 ms per 6k trades).\n    # Anything unexpected falls back to the default encoder path.\n    try:\n        from fastapi.responses import JSONResponse\n        return JSONResponse(content=d)\n    except (TypeError, ValueError):\n        return d\n"]]}
NEW = {"backend/app/backtest/repo/test_trading_calendar_fast.py": "IyBiYWNrZW5kL2FwcC9iYWNrdGVzdC9yZXBvL3Rlc3RfdHJhZGluZ19jYWxlbmRhcl9mYXN0LnB5CiMKIyDilIDilIAgQlRfQ0FMRU5EQVJfRkFTVF8yMDI2MDkyOSDilIDilIAgdGhlIHRyYWRpbmctZGF5IGNhbGVuZGFyIChzZXNzaW9ucy10by1leHBpcnkKIyBmb3IgdGhlIHJlc3VsdHMgcGFnZSBhbmQgVHJhZGUgUmVwbGF5KSBtdXN0IHByb2R1Y2UgRVhBQ1RMWSB0aGUgZGF0ZXMgb2YgdGhlCiMgb2xkIGZ1bGwgRElTVElOQ1Qgc2NhbiwgaW4gbWlsbGlzZWNvbmRzLCBhbmQgdGhlIHJ1bi1kZXRhaWwgcm91dGUgbXVzdAojIHJldHVybiB0aGUgc2FtZSBKU09OIGl0IGFsd2F5cyBkaWQuIFJlYWwgZXBvY2ggdGltZXN0YW1wcyB0aHJvdWdob3V0LgojIE5vIFRlc3RDbGllbnQgKGJ1aWxkLU1hYyBodHRweCBwaW4pOiB0aGUgcm91dGUgZnVuY3Rpb24gaXMgY2FsbGVkIGRpcmVjdGx5LgojCiMgICBjZCBiYWNrZW5kICYmIFBZVEhPTlBBVEg9JFBXRCBweXRob24zIGFwcC9iYWNrdGVzdC9yZXBvL3Rlc3RfdHJhZGluZ19jYWxlbmRhcl9mYXN0LnB5CgppbXBvcnQganNvbgppbXBvcnQgb3MKaW1wb3J0IHNxbGl0ZTMKaW1wb3J0IHN5cwppbXBvcnQgdGVtcGZpbGUKaW1wb3J0IHRpbWUKZnJvbSBkYXRldGltZSBpbXBvcnQgZGF0ZSwgZGF0ZXRpbWUsIHRpbWVkZWx0YSwgdGltZXpvbmUKZnJvbSBwYXRobGliIGltcG9ydCBQYXRoCgpGQUlMUywgUEFTU0VTID0gW10sIFswXQoKCmRlZiBjaGVjayhuYW1lLCBjb25kLCBkZXRhaWw9IiIpOgogICAgaWYgY29uZDoKICAgICAgICBQQVNTRVNbMF0gKz0gMQogICAgICAgIHByaW50KGYiICBQQVNTICB7bmFtZX0iKQogICAgZWxzZToKICAgICAgICBGQUlMUy5hcHBlbmQobmFtZSkKICAgICAgICBwcmludChmIiAgRkFJTCAge25hbWV9ICBbe3N0cihkZXRhaWwpWzozMDBdfV0iKQoKCklTVCA9IHRpbWV6b25lKHRpbWVkZWx0YShob3Vycz01LCBtaW51dGVzPTMwKSkKCgpkZWYgZHNfb2YoZCk6CiAgICByZXR1cm4gaW50KGRhdGV0aW1lKGQueWVhciwgZC5tb250aCwgZC5kYXksIHR6aW5mbz1JU1QpLnRpbWVzdGFtcCgpKQoKClRNUCA9IHRlbXBmaWxlLm1rZHRlbXAocHJlZml4PSJidF9jYWxfZmFzdF8iKQpEQiA9IG9zLnBhdGguam9pbihUTVAsICJiYWNrdGVzdC5kYiIpClNDSEVNQSA9IChQYXRoKF9fZmlsZV9fKS5yZXNvbHZlKCkucGFyZW50IC8gInNjaGVtYS5zcWwiKS5yZWFkX3RleHQoKQoKaW1wb3J0IGFwcC5iYWNrdGVzdC5yZXBvLmJhY2t0ZXN0X3JlcG8gYXMgQlIgICAgICAgICAgIyBub3FhOiBFNDAyCkJSLl9kYl9wYXRoID0gbGFtYmRhOiBQYXRoKERCKQppbXBvcnQgYXBwLmJhY2t0ZXN0LnJlcG8udHJhZGluZ19jYWxlbmRhciBhcyBUQyAgICAgICAgIyBub3FhOiBFNDAyCgpjID0gc3FsaXRlMy5jb25uZWN0KERCKQpjLmV4ZWN1dGVzY3JpcHQoU0NIRU1BKQpjLmV4ZWN1dGUoIlBSQUdNQSBqb3VybmFsX21vZGU9V0FMIikKCnByaW50KCLilIDilIAgMS4gY29ycHVzIHdpdGggdGhlIGF3a3dhcmQgY2FzZXMiKQpyb3dzID0gW10KCgpkZWYgcHV0KHN5bSwgdHlwLCB1bmQsIHRzLCBzdHJpa2U9MjMwMDAuMCk6CiAgICByb3dzLmFwcGVuZCgoYWJzKGhhc2goc3ltKSkgJSAxMCAqKiA4LCB0cywgdW5kLCBzeW0sIHR5cCwgc3RyaWtlLCAiMjA5OS0wMS0wMSIsIDEsIDEsIDEsIDEsIDAsIDApKQoKCmQsIG4gPSBkYXRlKDIwMjQsIDEsIDEpLCAwCndlZWtkYXlzID0gW10Kd2hpbGUgbiA8IDgwOgogICAgaWYgZC53ZWVrZGF5KCkgPCA1OgogICAgICAgIHdlZWtkYXlzLmFwcGVuZChkKQogICAgICAgIGRzID0gZHNfb2YoZCkKICAgICAgICBmb3IgayBpbiByYW5nZSgyMSk6CiAgICAgICAgICAgIGZvciB0eXAgaW4gKCJDRSIsICJQRSIpOgogICAgICAgICAgICAgICAgc3ltID0gZiJOSUZUWXtkOiV5JW0lZH17MjIwMDAgKyA1MCAqIGt9e3R5cH0iCiAgICAgICAgICAgICAgICBmb3IgbSBpbiByYW5nZSgzNzUpOgogICAgICAgICAgICAgICAgICAgIHB1dChzeW0sIHR5cCwgIk5JRlRZIiwgZHMgKyAoNTU1ICsgbSkgKiA2MCkKICAgICAgICBuICs9IDEKICAgIGQgKz0gdGltZWRlbHRhKGRheXM9MSkKc2F0ID0gZGF0ZSgyMDI0LCAxLCAyMCkgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIyBzcGVjaWFsIFNhdHVyZGF5IHNlc3Npb246IENFIG9ubHkKZm9yIG0gaW4gcmFuZ2UoMCwgMzc1LCA1KToKICAgIHB1dCgiTklGVFkyNFNBVENFIiwgIkNFIiwgIk5JRlRZIiwgZHNfb2Yoc2F0KSArICg1NTUgKyBtKSAqIDYwKQpwZV9vbmx5ID0gZGF0ZSgyMDI0LCA1LCA2KSAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAjIFBFIHByaW50cyBvbmx5CnB1dCgiTklGVFkyNFBFT05MWSIsICJQRSIsICJOSUZUWSIsIGRzX29mKHBlX29ubHkpICsgNjAwICogNjApCnNwb3Rfb25seSA9IGRhdGUoMjAyNCwgNSwgNykgICAgICAgICAgICAgICAgICAgICAgICAgICAgICMgU1BPVCAvIEZVVCBvbmx5IOKGkiBub3QgYSB0cmFkaW5nIGRheQpwdXQoIk5JRlRZIDUwIiwgIlNQT1QiLCAiTklGVFkiLCBkc19vZihzcG90X29ubHkpICsgNjAwICogNjApCnB1dCgiTklGVFlGVVQiLCAiRlVUIiwgIk5JRlRZIiwgZHNfb2Yoc3BvdF9vbmx5KSArIDYwMSAqIDYwKQpibmZfb25seSA9IGRhdGUoMjAyNCwgNSwgOCkgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAjIGFub3RoZXIgdW5kZXJseWluZydzIG9wdGlvbnMgb25seQpwdXQoIkJBTktOSUZUWTI0TUFZQ0UiLCAiQ0UiLCAiQkFOS05JRlRZIiwgZHNfb2YoYm5mX29ubHkpICsgNjAwICogNjApCnN0cmF5ID0gZGF0ZSgyMDI0LCA1LCAxMikgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICMgU3VuZGF5LCBvbmUgMDI6MDAgSVNUIHByaW50CnB1dCgiTklGVFkyNFNUUkFZQ0UiLCAiQ0UiLCAiTklGVFkiLCBkc19vZihzdHJheSkgKyAxMjAgKiA2MCkKZWRnZSA9IGRhdGUoMjAyNCwgNSwgMTMpICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIyBwcmludCBhdCAyMzo1OTo1OSBJU1QgKGRheSBlZGdlKQpwdXQoIk5JRlRZMjRFREdFUEUiLCAiUEUiLCAiTklGVFkiLCBkc19vZihlZGdlKSArIDg2Mzk5KQpjLmV4ZWN1dGVtYW55KCJJTlNFUlQgT1IgSUdOT1JFIElOVE8gYmFja3Rlc3RfY2FuZGxlc18xbSBWQUxVRVMgKD8sPyw/LD8sPyw/LD8sPyw/LD8sPyw/LD8pIiwgcm93cykKYy5jb21taXQoKQpjaGVjayhmImNvcnB1cyBidWlsdCAoe2xlbihyb3dzKTosfSByb3dzKSIsIGxlbihyb3dzKSA+IDFfMDAwXzAwMCkKCnByaW50KCLilIDilIAgMi4gaWRlbnRpY2FsIHRvIHRoZSBvbGQgZnVsbCBzY2FuIikKdCA9IHRpbWUudGltZSgpCm9sZCA9IFRDLl9zY2FuX2RheXMoYywgIk5JRlRZIikKdF9vbGQgPSB0aW1lLnRpbWUoKSAtIHQKdCA9IHRpbWUudGltZSgpCm5ldyA9IFRDLl9wcm9iZV9kYXlzKGMsICJOSUZUWSIpCnRfbmV3ID0gdGltZS50aW1lKCkgLSB0CmNoZWNrKGYicHJvYmUgPT0gc2NhbiAoe2xlbihuZXcpfSBkYXRlcykiLCBuZXcgPT0gb2xkLCAobGVuKG5ldyksIGxlbihvbGQpLCBzb3J0ZWQoc2V0KG9sZCkgXiBzZXQobmV3KSlbOjZdKSkKY2hlY2soIlNhdHVyZGF5IHNwZWNpYWwgc2Vzc2lvbiBjb3VudGVkIiwgc2F0Lmlzb2Zvcm1hdCgpIGluIG5ldykKY2hlY2soIlBFLW9ubHkgZGF5IGNvdW50ZWQiLCBwZV9vbmx5Lmlzb2Zvcm1hdCgpIGluIG5ldykKY2hlY2soIlNQT1QvRlVULW9ubHkgZGF5IGV4Y2x1ZGVkIiwgc3BvdF9vbmx5Lmlzb2Zvcm1hdCgpIG5vdCBpbiBuZXcpCmNoZWNrKCJvdGhlciB1bmRlcmx5aW5nJ3MgZGF5IGV4Y2x1ZGVkIiwgYm5mX29ubHkuaXNvZm9ybWF0KCkgbm90IGluIG5ldykKY2hlY2soIjAyOjAwIElTVCBzdHJheSBwcmludCBjb3VudGVkIChzYW1lIGFzIHRoZSBzY2FuKSIsIHN0cmF5Lmlzb2Zvcm1hdCgpIGluIG5ldyBhbmQgc3RyYXkuaXNvZm9ybWF0KCkgaW4gb2xkKQpjaGVjaygiMjM6NTk6NTkgSVNUIHByaW50IGxhbmRzIG9uIGl0cyBJU1QgZGF0ZSIsIGVkZ2UuaXNvZm9ybWF0KCkgaW4gbmV3IGFuZCAoZWRnZSArIHRpbWVkZWx0YShkYXlzPTEpKS5pc29mb3JtYXQoKSBub3QgaW4gbmV3KQpjaGVjaygiQkFOS05JRlRZIGNhbGVuZGFyIGlkZW50aWNhbCB0b28iLCBUQy5fcHJvYmVfZGF5cyhjLCAiQkFOS05JRlRZIikgPT0gVEMuX3NjYW5fZGF5cyhjLCAiQkFOS05JRlRZIikgPT0gW2JuZl9vbmx5Lmlzb2Zvcm1hdCgpXSkKY2hlY2soInVua25vd24gdW5kZXJseWluZyDihpIgW10iLCBUQy5fcHJvYmVfZGF5cyhjLCAiRklOTklGVFkiKSA9PSBbXSA9PSBUQy5fc2Nhbl9kYXlzKGMsICJGSU5OSUZUWSIpKQpjaGVjayhmInByb2JlIG11Y2ggZmFzdGVyIHRoYW4gdGhlIHNjYW4gKHt0X25ldyAqIDEwMDA6LjFmfSBtcyB2cyB7dF9vbGQgKiAxMDAwOi4wZn0gbXMpIiwKICAgICAgdF9uZXcgPCAwLjIgYW5kIHRfbmV3ICogMTAgPCB0X29sZCwgKHRfbmV3LCB0X29sZCkpCnBsYW5zID0gWyIgIi5qb2luKHN0cih4KSBmb3IgeCBpbiByKSBmb3IgcSBpbiAoVEMuX1BST0JFX1NRTCwKICAgICAgICAgIlNFTEVDVCBNSU4odHMpIEZST00gYmFja3Rlc3RfY2FuZGxlc18xbSBXSEVSRSB1bmRlcmx5aW5nID0gPyBBTkQgaW5zdHJ1bWVudF90eXBlID0gPyIpCiAgICAgICAgIGZvciByIGluIGMuZXhlY3V0ZSgiRVhQTEFJTiBRVUVSWSBQTEFOICIgKyBxLCAoKCJOSUZUWSIsICJDRSIsIDAsIDEpIGlmICJMSU1JVCIgaW4gcSBlbHNlICgiTklGVFkiLCAiQ0UiKSkpXQpjaGVjaygicHJvYmUgKyBtaW4vbWF4IGFyZSBjb3ZlcmluZy1pbmRleCBzZWVrcyIsIGFsbCgiaWR4X2J0MW1fdW5kZXJfdHlwZV90cyIgaW4gcCBmb3IgcCBpbiBwbGFucyksIHBsYW5zKQpfc2F2ZWQgPSBUQy5fTUFYX1BST0JFX0RBWVMKVEMuX01BWF9QUk9CRV9EQVlTID0gMTAKY2hlY2soImFic3VyZCBzcGFuIGZhbGxzIGJhY2sgdG8gdGhlIHNjYW4gKHNhbWUgYW5zd2VyKSIsIFRDLl9wcm9iZV9kYXlzKGMsICJOSUZUWSIpID09IG9sZCkKVEMuX01BWF9QUk9CRV9EQVlTID0gX3NhdmVkCgpwcmludCgi4pSA4pSAIDMuIHB1YmxpYyB0cmFkaW5nX2RhdGVzOiBjYWNoZSwgaW52YWxpZGF0aW9uLCBEVEUiKQpUQy5fY2FjaGUuY2xlYXIoKQpjYWxscyA9IFswXQpfcmVhbF9jb25uZWN0ID0gVEMuc3FsaXRlMy5jb25uZWN0CgoKZGVmIF9jb3VudGluZ19jb25uZWN0KCphLCAqKmspOgogICAgY2FsbHNbMF0gKz0gMQogICAgcmV0dXJuIF9yZWFsX2Nvbm5lY3QoKmEsICoqaykKCgpUQy5zcWxpdGUzLmNvbm5lY3QgPSBfY291bnRpbmdfY29ubmVjdAp0ID0gdGltZS50aW1lKCkKZmlyc3QgPSBUQy50cmFkaW5nX2RhdGVzKCJOSUZUWSIpCnRfZmlyc3QgPSB0aW1lLnRpbWUoKSAtIHQKc2Vjb25kID0gVEMudHJhZGluZ19kYXRlcygiTklGVFkiKQpjaGVjayhmInRyYWRpbmdfZGF0ZXMgPT0gc2NhbiwgY29sZCBjYWxsIHt0X2ZpcnN0ICogMTAwMDouMWZ9IG1zIiwgZmlyc3QgPT0gb2xkIGFuZCB0X2ZpcnN0IDwgMC4yLCB0X2ZpcnN0KQpjaGVjaygic2Vjb25kIGNhbGwgc2VydmVkIGZyb20gdGhlIGNhY2hlIChubyBTUUwpIiwgc2Vjb25kID09IGZpcnN0IGFuZCBjYWxsc1swXSA9PSAxLCBjYWxsc1swXSkKIyBhIHJ1biBwZXJzaXN0ZWQgaW50byB0aGUgc2FtZSBmaWxlIGJ1bXBzIGl0cyBtdGltZSDigJQgdGhlIGNhc2UgdGhhdCB1c2VkIHRvIGNvc3QgMTXigJMyMCBzCnRpbWUuc2xlZXAoMS4xKQpjLmV4ZWN1dGUoIklOU0VSVCBJTlRPIGJhY2t0ZXN0X3J1bnMgKHJ1bl9pZCwgc3RyYXRlZ3lfaWQsIHVuZGVybHlpbmcsIGRhdGVfZnJvbSwgZGF0ZV90bywgY29uZmlnX2pzb24sIGZpbGxfbW9kZWwsIHN0YXR1cywgY3JlYXRlZF9hdCkgIgogICAgICAgICAgIlZBTFVFUyAoJ3ItbXRpbWUnLCAnT1JCX1YxJywgJ05JRlRZJywgJzIwMjQtMDEtMDEnLCAnMjAyNC0wNS0wMScsICd7fScsICdwZXNzaW1pc3RpYycsICdkb25lJywgMCkiKQpjLmNvbW1pdCgpCmMuZXhlY3V0ZSgiUFJBR01BIHdhbF9jaGVja3BvaW50KEZVTEwpIikKdCA9IHRpbWUudGltZSgpCnRoaXJkID0gVEMudHJhZGluZ19kYXRlcygiTklGVFkiKQp0X3RoaXJkID0gdGltZS50aW1lKCkgLSB0CmNoZWNrKGYiYWZ0ZXIgYSBydW4gd3JpdGU6IHJlY29tcHV0ZWQgKHtjYWxsc1swXX0gcmVhZHMpIGluIHt0X3RoaXJkICogMTAwMDouMWZ9IG1zLCBzYW1lIGRhdGVzIiwKICAgICAgdGhpcmQgPT0gb2xkIGFuZCBjYWxsc1swXSA9PSAyIGFuZCB0X3RoaXJkIDwgMC4yLCAoY2FsbHNbMF0sIHRfdGhpcmQpKQpUQy5zcWxpdGUzLmNvbm5lY3QgPSBfcmVhbF9jb25uZWN0Cgp0cmFkZXMgPSBbXQpmb3IgaSwgZGQgaW4gZW51bWVyYXRlKHdlZWtkYXlzWzo0MF0pOgogICAgZXhwID0gd2Vla2RheXNbbWluKGxlbih3ZWVrZGF5cykgLSAxLCBpICsgKGkgJSA1KSldCiAgICB0cmFkZXMuYXBwZW5kKHsiaWQiOiBpLCAiZW50cnlfdHMiOiBkc19vZihkZCkgKyAzNjAwMCwgImV4cGlyeSI6IGV4cC5pc29mb3JtYXQoKX0pCnJ1biA9IFRDLmF0dGFjaF9kdGUoeyJ1bmRlcmx5aW5nIjogIk5JRlRZIiwgInRyYWRlcyI6IFtkaWN0KHQpIGZvciB0IGluIHRyYWRlc119KQp3YW50ID0gW1RDLnNlc3Npb25zX3RvX2V4cGlyeShvbGQsIGRkLmlzb2Zvcm1hdCgpLCB0WyJleHBpcnkiXSkgZm9yIGRkLCB0IGluIHppcCh3ZWVrZGF5c1s6NDBdLCB0cmFkZXMpXQpjaGVjaygiYXR0YWNoX2R0ZSBpZGVudGljYWwgdG8gdGhlIHNjYW4gY2FsZW5kYXIiLCBbdC5nZXQoImR0ZSIpIGZvciB0IGluIHJ1blsidHJhZGVzIl1dID09IHdhbnQsCiAgICAgIChbdC5nZXQoImR0ZSIpIGZvciB0IGluIHJ1blsidHJhZGVzIl1dWzo2XSwgd2FudFs6Nl0pKQplID0gdGVtcGZpbGUubWtkdGVtcChwcmVmaXg9ImJ0X2NhbF9lbXB0eV8iKQplYyA9IHNxbGl0ZTMuY29ubmVjdChvcy5wYXRoLmpvaW4oZSwgImJhY2t0ZXN0LmRiIikpCmVjLmV4ZWN1dGVzY3JpcHQoU0NIRU1BKQplYy5jbG9zZSgpCmNoZWNrKCJlbXB0eSBjb3JwdXMg4oaSIFtdIChEVEUgc2ltcGx5IGFic2VudCkiLCBUQy50cmFkaW5nX2RhdGVzKCJOSUZUWSIsIGRiX3BhdGg9b3MucGF0aC5qb2luKGUsICJiYWNrdGVzdC5kYiIpKSA9PSBbXSkKCnByaW50KCLilIDilIAgNC4gcnVuIGRldGFpbCByb3V0ZTogc2FtZSBKU09OLCBkaXJlY3QgcmVuZGVyIikKdHJ5OgogICAgZnJvbSBmYXN0YXBpLmVuY29kZXJzIGltcG9ydCBqc29uYWJsZV9lbmNvZGVyCiAgICBmcm9tIGFwcC5hcGkuYmFja3Rlc3Rfcm91dGVzIGltcG9ydCBydW5fZGV0YWlsCiAgICBjLmV4ZWN1dGUoIklOU0VSVCBJTlRPIGJhY2t0ZXN0X3J1bnMgKHJ1bl9pZCwgc3RyYXRlZ3lfaWQsIHVuZGVybHlpbmcsIGRhdGVfZnJvbSwgZGF0ZV90bywgY29uZmlnX2pzb24sIGZpbGxfbW9kZWwsIHN0YXR1cywgIgogICAgICAgICAgICAgICJjcmVhdGVkX2F0LCBzdW1tYXJ5X2pzb24pIFZBTFVFUyAoJ3ItanNvbicsICdPUkJfVjEnLCAnTklGVFknLCAnMjAyNC0wMS0wMScsICcyMDI0LTA1LTAxJywgPywgJ3Blc3NpbWlzdGljJywgJ2RvbmUnLCAwLCA/KSIsCiAgICAgICAgICAgICAgKGpzb24uZHVtcHMoeyJsb3RzIjogMTAsICJuZXN0ZWQiOiB7ImEiOiBbMSwgMl19fSksIGpzb24uZHVtcHMoeyJuZXQiOiAxMi41LCAidHJhZGVzIjogMn0pKSkKICAgIGZvciBpLCBkZCBpbiBlbnVtZXJhdGUod2Vla2RheXNbOjZdKToKICAgICAgICBjLmV4ZWN1dGUoIklOU0VSVCBJTlRPIGJhY2t0ZXN0X3RyYWRlcyAocnVuX2lkLCB0cmFkaW5nc3ltYm9sLCBpbnN0cnVtZW50X3R5cGUsIHN0cmlrZSwgZXhwaXJ5LCBkaXJlY3Rpb24sIGVudHJ5X3RzLCBlbnRyeV9wcmljZSwgIgogICAgICAgICAgICAgICAgICAic2wsIHRwLCBleGl0X3RzLCBleGl0X3ByaWNlLCBleGl0X3JlYXNvbiwgcG5sLCBxdHksIGNoYXJnZXMsIG5ldF9wbmwpIFZBTFVFUyAiCiAgICAgICAgICAgICAgICAgICIoJ3ItanNvbicsID8sICdDRScsIDIzMDAwLCA/LCAnQlVZJywgPywgMTAwLjUsIE5VTEwsIDE0MCwgPywgMTIwLjI1LCAnVFAnLCAxMjgzLjc1LCA2NSwgNDAuMSwgMTI0My42NSkiLAogICAgICAgICAgICAgICAgICAoZiJOSUZUWXtpfUNFIiwgd2Vla2RheXNbaSArIDJdLmlzb2Zvcm1hdCgpLCBkc19vZihkZCkgKyAzNjAwMCwgZHNfb2YoZGQpICsgMzk2MDApKQogICAgYy5jb21taXQoKQogICAgcmVzcCA9IHJ1bl9kZXRhaWwoInItanNvbiIpCiAgICBib2R5ID0ganNvbi5sb2FkcyhyZXNwLmJvZHkpCiAgICByZWYgPSBqc29uYWJsZV9lbmNvZGVyKFRDLmF0dGFjaF9kdGUoQlIuZ2V0X3J1bigici1qc29uIikpKQogICAgY2hlY2soInJvdXRlIHJldHVybnMgYSByZW5kZXJlZCBKU09OUmVzcG9uc2UiLCB0eXBlKHJlc3ApLl9fbmFtZV9fID09ICJKU09OUmVzcG9uc2UiIGFuZCByZXNwLm1lZGlhX3R5cGUgPT0gImFwcGxpY2F0aW9uL2pzb24iKQogICAgY2hlY2soImJvZHkgaWRlbnRpY2FsIHRvIHRoZSBvbGQganNvbmFibGVfZW5jb2RlciBvdXRwdXQiLCBib2R5ID09IGpzb24ubG9hZHMoanNvbi5kdW1wcyhyZWYpKSwgKGxpc3QoYm9keSlbOjVdLCBsaXN0KHJlZilbOjVdKSkKICAgIGNoZWNrKCJEVEUgcHJlc2VudCBpbiB0aGUgZGV0YWlsIiwgYWxsKCJkdGUiIGluIHQgZm9yIHQgaW4gYm9keVsidHJhZGVzIl0pKQogICAgaW1wb3J0IGFwcC5hcGkuYmFja3Rlc3Rfcm91dGVzIGFzIFJUCiAgICBfZyA9IEJSLmdldF9ydW4KICAgIEJSLmdldF9ydW4gPSBsYW1iZGEgcmlkOiB7InJ1bl9pZCI6IHJpZCwgInRyYWRlcyI6IFtdLCAib2RkIjogezEsIDJ9fSAgICAgIyBub3QgSlNPTi1uYXRpdmUg4oaSIGZhbGxiYWNrCiAgICBvdXQgPSBydW5fZGV0YWlsKCJyLW9kZCIpCiAgICBCUi5nZXRfcnVuID0gX2cKICAgIGNoZWNrKCJub24tSlNPTiBjb250ZW50IGZhbGxzIGJhY2sgdG8gRmFzdEFQSSdzIGVuY29kZXIgcGF0aCIsIGlzaW5zdGFuY2Uob3V0LCBkaWN0KSBhbmQgb3V0WyJydW5faWQiXSA9PSAici1vZGQiKQogICAgdHJ5OgogICAgICAgIHJ1bl9kZXRhaWwoIm1pc3NpbmciKQogICAgICAgIGNoZWNrKCJtaXNzaW5nIHJ1biDihpIgNDA0IiwgRmFsc2UpCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGV4OgogICAgICAgIGNoZWNrKCJtaXNzaW5nIHJ1biDihpIgNDA0IiwgZ2V0YXR0cihleCwgInN0YXR1c19jb2RlIiwgTm9uZSkgPT0gNDA0LCBleCkKZXhjZXB0IEltcG9ydEVycm9yIGFzIGV4OgogICAgcHJpbnQoZiIgIChyb3V0ZSBjaGVja3Mgc2tpcHBlZCBoZXJlOiB7ZXh9KSIpCmMuY2xvc2UoKQoKcHJpbnQoKQpwcmludChmIntQQVNTRVNbMF19IHBhc3NlZCwge2xlbihGQUlMUyl9IGZhaWxlZCIpCmlmIEZBSUxTOgogICAgZm9yIGYgaW4gRkFJTFM6CiAgICAgICAgcHJpbnQoIiAgLSIsIGYpCiAgICBzeXMuZXhpdCgxKQpwcmludCgiQUxMIEJUX0NBTEVOREFSX0ZBU1QgQ0hFQ0tTIFBBU1NFRCIpCg=="}
TESTS = ["app/backtest/repo/test_trading_calendar_fast.py", "app/backtest/tma/test_tma_v2_dte_lots.py"]


def die(m):
    print("\nABORT:", m); sys.exit(1)


def mirror_of(rel):
    for src, dst in MIRRORS:
        if rel.startswith(src + "/"):
            root = os.path.join(ROOT, dst)
            return os.path.join(root, rel[len(src) + 1:]) if os.path.isdir(root) else None
    return None


print(f"── {FENCE}")
for rel, marker in PREREQ.items():
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        die(f"{rel} not found — run from the repo root")
    t = open(p, encoding="utf-8").read()
    if FENCE in t:
        die(f"{FENCE} already applied ({rel})")
    if marker not in t:
        die(f"prerequisite '{marker}' missing in {rel}")
for rel in NEW:
    if os.path.exists(os.path.join(ROOT, rel)):
        die(f"{rel} already exists — remove the hand-placed copy and re-run")

staged = {}
for rel, ops in EDITS.items():
    t = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    for old, new in ops:
        n = t.count(old)
        if n != 1:
            die(f"{rel}: anchor found {n}x (need 1): {old.strip().splitlines()[0][:80]!r}")
        t = t.replace(old, new)
    staged[rel] = t
for rel, b64 in NEW.items():
    staged[rel] = base64.b64decode(b64).decode("utf-8")

tmp = tempfile.mkdtemp(prefix="bt_cal_gate_")
for rel, txt in staged.items():
    p = os.path.join(tmp, os.path.basename(rel))
    open(p, "w", encoding="utf-8").write(txt)
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {rel}: {e}")
shutil.rmtree(tmp, ignore_errors=True)
print("   py_compile ok")

st = subprocess.run(["git", "status", "--porcelain", "--"] + list(EDITS), cwd=ROOT, capture_output=True, text=True).stdout
dirty = [l for l in st.splitlines() if l.strip() and not l.startswith("??")]
if dirty and "--allow-dirty" not in sys.argv:
    die(f"uncommitted changes on targets ({dirty}); commit them or re-run with --allow-dirty")

written, created = [], []
for rel, txt in staged.items():
    p = os.path.join(ROOT, rel)
    if rel in EDITS:
        shutil.copy2(p, p + f".bak-{FENCE}")
        written.append(rel)
    else:
        created.append(rel)
    open(p, "w", encoding="utf-8").write(txt)
    m = mirror_of(rel)
    if m and os.path.isdir(os.path.dirname(m)):
        open(m, "w", encoding="utf-8").write(txt)
open(os.path.join(ROOT, f".{FENCE}.done"), "w").write("applied\n")
print(f"   edited {len(written)}, created {len(created)} (backups .bak-{FENCE}; desktop tree mirrored where present)")


def rollback(reason):
    for rel in written:
        p = os.path.join(ROOT, rel)
        shutil.move(p + f".bak-{FENCE}", p)
        m = mirror_of(rel)
        if m and os.path.isdir(os.path.dirname(m)):
            shutil.copy2(p, m)
    for rel in created:
        for p in (os.path.join(ROOT, rel), mirror_of(rel)):
            if p and os.path.exists(p):
                os.remove(p)
    os.remove(os.path.join(ROOT, f".{FENCE}.done"))
    die(reason)


env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "backend"))
for s in TESTS:
    r = subprocess.run([sys.executable, s], cwd=os.path.join(ROOT, "backend"), env=env,
                       capture_output=True, text=True, timeout=900)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"   {'ok  ' if r.returncode == 0 else 'FAIL'} {s} — {tail[:80]}")
    if r.returncode != 0:
        print("\n".join(l for l in r.stdout.splitlines() if "FAIL" in l)[-3000:])
        print((r.stderr or "")[-2000:])
        rollback(f"{s} failed; working copy restored")
print(f"── {FENCE} applied. Next: ./desktop/build-scalp.sh backend")
