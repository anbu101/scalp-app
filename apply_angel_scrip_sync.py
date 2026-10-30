#!/usr/bin/env python3
"""
apply_angel_scrip_sync.py  ── ANGEL_SCRIP_SYNC_20261030 ──

Keeps Angel One's daily contract list (scrip master) current, so LIVE orders
on Account 2 can be placed. Until now nothing ever downloaded it, and every
LIVE Angel order failed: "Angel scrip master stale (synced=None); LIVE
resolution refused" (IC_V2, 2026-10-30).

  * Boot: background download (never delays start-up).
  * 08:30 and 08:50 Mon–Fri: daily download (08:50 is a retry).
  * 09:05: if the list is still stale AND a strategy on Account 2 is in LIVE
    or PAPER+LIVE → error in the bell + Telegram, naming the strategies.
  * On demand: an order that finds the list stale re-reads the disk copy,
    then downloads once; a strike newer than the cached copy triggers one
    forced refresh (10-minute cool-down — no download storms).
  * Still fail-closed: a token is never guessed; a bad/empty download never
    replaces a good cache.
  * Nothing is downloaded when no strategy is bound to Account 2.

Run from the repo root:

    python3 apply_angel_scrip_sync.py --check
    python3 apply_angel_scrip_sync.py
    ./desktop/build-scalp.sh both      (non-trading hours)
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "ANGEL_SCRIP_SYNC_20261030"
PARENT = "ACC2 BEGIN"
PARENT_FILE = "backend/app/marketdata/angel_instrument_resolver.py"
NEW_FILES = {'backend/app/marketdata/angel_scrip_sync.py': '# backend/app/marketdata/angel_scrip_sync.py\n#\n# ── ANGEL_SCRIP_SYNC_20261030 ── keeps Angel One\'s daily contract list\n# ("scrip master") current so LIVE orders on Account 2 can be placed.\n#\n# Before this, nothing ever called AngelInstrumentResolver.sync(): the cache\n# was never written, and every LIVE Angel order failed with "Angel scrip\n# master stale (synced=None); LIVE resolution refused" (IC_V2, 2026-10-30).\n#\n#   boot          background download (never blocks start-up — BOOT_ISOLATION)\n#   08:30 / 08:50 daily download, Mon–Fri (08:50 = retry; no-op if 08:30 worked)\n#   09:05         pre-open check: list still stale AND a strategy bound to\n#                 Account 2 is in LIVE / PAPER+LIVE → error in the bell (+ Telegram)\n#   on demand     the resolver refreshes itself when an order finds it stale or\n#                 meets a strike newer than the cached copy (cool-down 10 min)\n#\n# Nothing is downloaded on installs where no strategy is bound to Angel One\n# (ACC2 doctrine: users without a secondary account pay zero cost).\n\nfrom __future__ import annotations\n\nimport threading\nfrom typing import List\n\nFENCE = "ANGEL_SCRIP_SYNC_20261030"\n\n\ndef _audit(msg: str) -> None:\n    try:\n        from app.event_bus.audit_logger import write_audit_log\n        write_audit_log(msg)\n    except Exception:\n        pass\n\n\ndef angel_bound_strategies() -> List[str]:\n    """Bindable strategies currently assigned to Account 2 · Angel One."""\n    try:\n        from app.config.account_bindings import load_bindings, BINDABLE_STRATEGIES\n        return sorted(s for s, b in load_bindings().items()\n                      if b == "ANGELONE" and s in BINDABLE_STRATEGIES)\n    except Exception:\n        return []\n\n\ndef angel_live_strategies() -> List[str]:\n    """Angel-bound strategies whose mode sends real orders."""\n    out = []\n    try:\n        from app.risk.execution_modes import strategy_mode, wants_live\n        for sid in angel_bound_strategies():\n            if wants_live(strategy_mode(sid)):\n                out.append(sid)\n    except Exception:\n        pass\n    return out\n\n\ndef sync_job(reason: str = "daily") -> bool:\n    bound = angel_bound_strategies()\n    if not bound:\n        _audit(f"[ANGEL_INSTR][SYNC][{reason}] skipped — no strategy on Account 2")\n        return False\n    try:\n        from app.marketdata.angel_instrument_resolver import get_shared_resolver\n        ok = get_shared_resolver().sync()\n    except Exception as e:\n        _audit(f"[ANGEL_INSTR][SYNC][{reason}] failed: {e!r}")\n        return False\n    _audit(f"[ANGEL_INSTR][SYNC][{reason}] {\'ok\' if ok else \'FAILED\'} "\n           f"(Account 2: {\', \'.join(bound)})")\n    return bool(ok)\n\n\ndef preopen_check_job() -> bool:\n    """True = an alert was raised."""\n    live = angel_live_strategies()\n    if not live:\n        return False\n    try:\n        from app.marketdata.angel_instrument_resolver import get_shared_resolver\n        r = get_shared_resolver()\n        if not r.is_fresh():\n            r.sync()                            # one more try before alerting\n        if r.is_fresh():\n            return False\n        synced = r._synced_date\n    except Exception as e:\n        synced = f"unknown ({e!r})"\n    msg = (f"Angel One contract list could not be downloaded (last: {synced}). "\n           f"LIVE orders on Account 2 will fail today for: {\', \'.join(live)}. "\n           f"Check the internet connection, or move these strategies to Account 1.")\n    _audit(f"[ANGEL_INSTR][PREOPEN][STALE] {msg}")\n    try:\n        from app.event_bus.inapp_events import record_alert\n        record_alert("ANGEL_SCRIP_STALE", msg, severity="error", mode="live")\n    except Exception:\n        pass\n    try:\n        from app.api.telegram_api import notify_critical\n        notify_critical({"message": msg, "severity": "error"})\n    except Exception:\n        pass\n    return True\n\n\ndef start_boot_sync() -> None:\n    """Fire-and-forget: never delays port binding or strategy launches."""\n    t = threading.Thread(target=sync_job, args=("boot",), name="angel-scrip-sync",\n                         daemon=True)\n    t.start()\n', 'backend/app/marketdata/test_angel_scrip_sync.py': '# backend/app/marketdata/test_angel_scrip_sync.py\n#\n# ── ANGEL_SCRIP_SYNC_20261030 ── behavioural suite for the Angel One contract\n# list: boot/daily/on-demand download, disk reload, new-strike refresh,\n# cool-down, pre-open alert, zero cost without Account 2. Hermetic: temp\n# cache dir, fake download, no network, no broker.\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/marketdata/test_angel_scrip_sync.py\n\nimport datetime as dt\nimport json\nimport sys\nimport tempfile\nfrom pathlib import Path\n\nFAILS = []\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if not cond:\n        FAILS.append(name)\n\n\nimport app.marketdata.angel_instrument_resolver as AR\nimport app.marketdata.angel_scrip_sync as SS\n\nTMP = Path(tempfile.mkdtemp(prefix="angel_scrip_"))\nAR.SCRIP_PATH = TMP / "scrip_master.json"\nAR.META_PATH = TMP / "scrip_master.meta.json"\nTODAY = dt.datetime.now(tz=AR.IST).date()\n\n\ndef row(strike, opt, expiry="04NOV2025", token="111"):\n    return {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY",\n            "symbol": f"NIFTY{expiry}{strike}{opt}", "strike": str(strike * 100),\n            "expiry": expiry, "token": token}\n\n\nBASE = [row(24000, "CE", token="1"), row(24000, "PE", token="2")] + \\\n       [row(20000 + i * 50, "CE", expiry="11NOV2025", token=str(1000 + i)) for i in range(1200)]\nDL = {"calls": 0, "fail": False, "rows": BASE}\n\n\ndef fake_download(timeout):\n    DL["calls"] += 1\n    if DL["fail"]:\n        raise RuntimeError("network down")\n    return list(DL["rows"])\n\n\nAR._download = fake_download\nCLOCK = {"t": 1_761_800_000.0}           # real epoch scale (real-clock rule)\nAR.time.time = lambda: CLOCK["t"]\n\n\ndef write_cache(rows, synced):\n    AR.SCRIP_PATH.write_text(json.dumps(rows))\n    AR.META_PATH.write_text(json.dumps({"synced_date": synced.isoformat()}))\n\n\ndef reset(rows=None, synced=None):\n    for p in (AR.SCRIP_PATH, AR.META_PATH):\n        if p.exists():\n            p.unlink()\n    if rows is not None:\n        write_cache(rows, synced)\n    DL.update(calls=0, fail=False, rows=BASE)\n    CLOCK["t"] += 10_000\n    return AR.AngelInstrumentResolver()\n\n\nSYM = "NIFTY25N0424000CE"                # Kite weekly → 2025-11-04 24000 CE\n\nprint("── 1. the 2026-10-30 failure: never synced → now heals on demand")\nr = reset()\ncheck("starts unsynced", r._synced_date is None)\ncheck("resolves after an on-demand download", r.resolve_from_kite_symbol(SYM) == ("NIFTY04NOV202524000CE", "1"))\ncheck("exactly one download", DL["calls"] == 1, DL)\ncheck("cache + meta written", AR.SCRIP_PATH.exists() and json.loads(AR.META_PATH.read_text())["synced_date"] == TODAY.isoformat())\n\nprint("── 2. fresh cache → no download")\nr = reset(BASE, TODAY)\nr.resolve_from_kite_symbol(SYM)\ncheck("today\'s cache used as is", DL["calls"] == 0)\nr = reset(BASE, TODAY - dt.timedelta(days=1))\nr.resolve_from_kite_symbol(SYM)\ncheck("yesterday\'s cache still fresh (unchanged rule)", DL["calls"] == 0)\n\nprint("── 3. download fails → fail closed, no download storm")\nr = reset(BASE, TODAY - dt.timedelta(days=3))\nDL["fail"] = True\ntry:\n    r.resolve_from_kite_symbol(SYM)\n    check("stale + failed download refuses", False)\nexcept AR.AngelInstrumentStale:\n    check("stale + failed download refuses", True)\nfor _ in range(5):\n    try:\n        r.resolve_from_kite_symbol(SYM)\n    except AR.AngelInstrumentStale:\n        pass\ncheck("cool-down: 6 orders → 1 download attempt", DL["calls"] == 1, DL)\nCLOCK["t"] += AR.ONDEMAND_COOLDOWN_S + 1\nDL["fail"] = False\ncheck("after the cool-down it retries and heals", r.resolve_from_kite_symbol(SYM)[1] == "1")\ncheck("second attempt counted", DL["calls"] == 2, DL)\n\nprint("── 4. another instance synced on disk → reload, no download")\nr = reset(BASE, TODAY - dt.timedelta(days=4))\nwrite_cache(BASE, TODAY)                 # e.g. the 08:30 job via the shared resolver\nr.resolve_from_kite_symbol(SYM)\ncheck("picked up from disk", r._synced_date == TODAY and DL["calls"] == 0, (r._synced_date, DL))\n\nprint("── 5. strike listed after the cached copy → one forced refresh")\nr = reset(BASE, TODAY)\nDL["rows"] = BASE + [row(26550, "PE", token="777")]\ncheck("new strike found after refresh", r.resolve_from_kite_symbol("NIFTY25N0426550PE")[1] == "777")\ncheck("one refresh", DL["calls"] == 1)\ntry:\n    r.resolve_from_kite_symbol("NIFTY25N0499999PE")\n    check("truly unknown strike still raises", False)\nexcept AR.AngelInstrumentError as e:\n    check("truly unknown strike still raises", not isinstance(e, AR.AngelInstrumentStale))\ncheck("…without another download inside the cool-down", DL["calls"] == 1, DL)\n\nprint("── 6. a bad download never overwrites a good cache")\nold_day = TODAY - dt.timedelta(days=3)\nr = reset(BASE, old_day)\nDL["rows"] = []                          # Angel served an empty file\ntry:\n    r.resolve_from_kite_symbol(SYM)\n    check("empty download refused", False)\nexcept AR.AngelInstrumentStale:\n    check("empty download refused", True)\ncheck("cache file untouched", len(json.loads(AR.SCRIP_PATH.read_text())) == len(BASE))\ncheck("meta date untouched", json.loads(AR.META_PATH.read_text())["synced_date"] == old_day.isoformat())\nfor bad in ([], {"error": "x"}, BASE[:10]):\n    try:\n        AR._validate_rows(bad)\n        check(f"rejects {type(bad).__name__}/{len(bad)}", False)\n    except Exception:\n        check(f"rejects {type(bad).__name__}/{len(bad)}", True)\n\nprint("── 7. shared resolver")\nAR._SHARED = None\na, b = AR.get_shared_resolver(), AR.get_shared_resolver()\ncheck("one instance per process", a is b)\ntry:\n    from app.execution.angel_executor import AngelOneExecutor\n    ex = AngelOneExecutor(manager=None)\n    check("Angel executor uses the shared resolver", ex._resolver is a)\nexcept ModuleNotFoundError as e:\n    print(f"  SKIP  angel_executor import ({e}) — run on the build Mac")\n\nprint("── 8. jobs: zero cost without Account 2; alert only when it matters")\nSS.angel_bound_strategies = lambda: []\nDL.update(calls=0, fail=False, rows=BASE)\ncheck("no Account-2 strategy → no download", SS.sync_job("test") is False and DL["calls"] == 0)\nSS.angel_bound_strategies = lambda: ["SCALP_V3"]\nAR._SHARED = None\nfor p in (AR.SCRIP_PATH, AR.META_PATH):\n    p.unlink(missing_ok=True)\ncheck("bound → daily job downloads", SS.sync_job("test") is True and DL["calls"] == 1, DL)\n\nALERTS = []\nimport app.event_bus.inapp_events as IE\nIE.record_alert = lambda kind, msg, **kw: ALERTS.append((kind, msg))\nSS.angel_live_strategies = lambda: []\ncheck("pre-open: Angel-bound but PAPER only → silent", SS.preopen_check_job() is False and not ALERTS)\nSS.angel_live_strategies = lambda: ["IC_V1"]\ncheck("pre-open: LIVE on Angel + fresh list → silent", SS.preopen_check_job() is False and not ALERTS)\nAR._SHARED = None\nfor p in (AR.SCRIP_PATH, AR.META_PATH):\n    p.unlink(missing_ok=True)\nDL["fail"] = True\ncheck("pre-open: LIVE on Angel + list unobtainable → alert", SS.preopen_check_job() is True)\ncheck("alert names the strategy and the fix", ALERTS and "IC_V1" in ALERTS[-1][1] and "Account 1" in ALERTS[-1][1], ALERTS)\n\nprint()\nif FAILS:\n    print(f"  {len(FAILS)} FAILED: {FAILS}")\n    sys.exit(1)\nprint("  ALL CHECKS PASSED")\n'}
EDITS = {'backend/app/marketdata/angel_instrument_resolver.py': [('import tempfile\nfrom pathlib import Path\n', 'import tempfile\nimport threading                                   # ── ANGEL_SCRIP_SYNC_20261030 ──\nimport time\nfrom pathlib import Path\n'), ('SCRIP_URL = ("https://margincalculator.angelbroking.com"\n             "/OpenAPI_File/files/OpenAPIScripMaster.json")\n', 'SCRIP_URL = ("https://margincalculator.angelbroking.com"\n             "/OpenAPI_File/files/OpenAPIScripMaster.json")\n\n# ── ANGEL_SCRIP_SYNC_20261030 ── on-demand refresh limits + download helpers\nONDEMAND_COOLDOWN_S = 600      # at most one on-demand download per 10 min\nONDEMAND_TIMEOUT_S = 45        # an order waits at most this long for it\n_MIN_ROWS = 1000               # Angel\'s full file has 100k+ rows\n\n\ndef _download(timeout: float):\n    r = requests.get(SCRIP_URL, timeout=timeout)\n    r.raise_for_status()\n    return r.json()\n\n\ndef _validate_rows(data) -> None:\n    if not isinstance(data, list) or len(data) < _MIN_ROWS:\n        raise ValueError(f"scrip master looks wrong "\n                         f"({type(data).__name__}, {len(data) if hasattr(data, \'__len__\') else \'?\'} rows)")\n\n\ndef _disk_synced_date():\n    try:\n        return dt.date.fromisoformat(json.loads(META_PATH.read_text())["synced_date"])\n    except Exception:\n        return None\n'), ('class AngelInstrumentResolver:\n\n    def __init__(self):\n        # index key: (name, expiry_date_iso, strike, opt) -> (symbol, token)\n        self._index: Dict[Tuple[str, str, int, str], Tuple[str, str]] = {}\n        self._synced_date: Optional[dt.date] = None\n        self._load_cache()\n\n    # ---------------- cache ----------------\n\n    def _load_cache(self) -> None:\n        if not (SCRIP_PATH.exists() and META_PATH.exists()):\n            return\n        try:\n            meta = json.loads(META_PATH.read_text())\n            self._synced_date = dt.date.fromisoformat(meta["synced_date"])\n            self._build_index(json.loads(SCRIP_PATH.read_text()))\n            write_audit_log(\n                f"[ANGEL_INSTR] Cache loaded "\n                f"synced={self._synced_date} rows={len(self._index)}")\n        except Exception as e:\n            write_audit_log(f"[ANGEL_INSTR][WARN] Cache load failed ERR={e}")\n            self._index = {}\n            self._synced_date = None\n\n    def sync(self, force: bool = False) -> bool:\n        """Daily sync. Cheap no-op when already synced today (IST)."""\n        today = dt.datetime.now(tz=IST).date()\n        if not force and self._synced_date == today and self._index:\n            return True\n        try:\n            r = requests.get(SCRIP_URL, timeout=120)\n            r.raise_for_status()\n            data = r.json()\n        except Exception as e:\n            write_audit_log(f"[ANGEL_INSTR][WARN] Sync failed ERR={e}")\n            return False\n        _atomic_write_text(SCRIP_PATH, json.dumps(data))\n        _atomic_write_text(META_PATH,\n                           json.dumps({"synced_date": today.isoformat()}))\n        self._synced_date = today\n        self._build_index(data)\n        write_audit_log(\n            f"[ANGEL_INSTR] Synced scrip master rows={len(self._index)}")\n        return True\n\n    def _build_index(self, rows: list) -> None:\n        idx: Dict[Tuple[str, str, int, str], Tuple[str, str]] = {}\n        for row in rows:\n            if row.get("exch_seg") != "NFO":\n                continue\n            if row.get("instrumenttype") != "OPTIDX":\n                continue\n            name = row.get("name")\n            if name not in ("NIFTY", "BANKNIFTY"):\n                continue\n            sym = str(row.get("symbol", ""))\n            opt = sym[-2:]\n            if opt not in ("CE", "PE"):\n                continue\n            try:\n                strike = int(float(row.get("strike", "0")) / 100)  # paise\n                expiry = dt.datetime.strptime(\n                    row.get("expiry", ""), "%d%b%Y").date()\n            except Exception:\n                continue\n            idx[(name, expiry.isoformat(), strike, opt)] = (\n                sym, str(row.get("token")))\n        self._index = idx\n\n    # ---------------- staleness gate ----------------\n\n    def assert_fresh_for_live(self) -> None:\n        today = dt.datetime.now(tz=IST).date()\n        if self._synced_date is None or (today - self._synced_date).days > 1:\n            raise AngelInstrumentStale(\n                f"Angel scrip master stale (synced={self._synced_date}); "\n                f"LIVE resolution refused")\n\n    # ---------------- lookup ----------------\n\n    def _lookup(self, name: str, expiry: dt.date, strike: int,\n                opt: str) -> Tuple[str, str]:\n        # exact date first (weekly path)\n        hit = self._index.get((name, expiry.isoformat(), strike, opt))\n        if hit:\n            return hit\n        # month-level match (monthly symbols: day unknown from Kite symbol)\n        month_hits = [\n            v for (n, e, k, o), v in self._index.items()\n            if n == name and k == strike and o == opt\n            and e[:7] == expiry.isoformat()[:7]\n        ]\n        if len(month_hits) == 1:\n            return month_hits[0]\n        raise AngelInstrumentError(\n            f"No unique Angel instrument for "\n            f"{name} {expiry} {strike}{opt} (hits={len(month_hits)})")\n\n    def resolve_from_kite_symbol(self, kite_symbol: str) -> Tuple[str, str]:\n        """\n        Kite canonical NFO symbol -> (angel_tradingsymbol, angel_token).\n        Raises on any ambiguity — callers never guess.\n        """\n        self.assert_fresh_for_live()\n        name, expiry, strike, opt = parse_kite_symbol(kite_symbol)\n        return self._lookup(name, expiry, strike, opt)\n\n# ACC2 END', 'class AngelInstrumentResolver:\n\n    def __init__(self):\n        # index key: (name, expiry_date_iso, strike, opt) -> (symbol, token)\n        self._index: Dict[Tuple[str, str, int, str], Tuple[str, str]] = {}\n        self._synced_date: Optional[dt.date] = None\n        self._lock = threading.RLock()          # ── ANGEL_SCRIP_SYNC_20261030 ──\n        self._last_ondemand: float = 0.0        # epoch s of the last on-demand download\n        self._load_cache()\n\n    # ---------------- cache ----------------\n\n    def _load_cache(self) -> None:\n        if not (SCRIP_PATH.exists() and META_PATH.exists()):\n            return\n        try:\n            meta = json.loads(META_PATH.read_text())\n            synced = dt.date.fromisoformat(meta["synced_date"])\n            rows = json.loads(SCRIP_PATH.read_text())\n            with self._lock:\n                self._build_index(rows)\n                self._synced_date = synced\n            write_audit_log(\n                f"[ANGEL_INSTR] Cache loaded "\n                f"synced={self._synced_date} rows={len(self._index)}")\n        except Exception as e:\n            write_audit_log(f"[ANGEL_INSTR][WARN] Cache load failed ERR={e}")\n            with self._lock:\n                self._index = {}\n                self._synced_date = None\n\n    def sync(self, force: bool = False, timeout: float = 120) -> bool:\n        """Daily sync. Cheap no-op when already synced today (IST)."""\n        today = dt.datetime.now(tz=IST).date()\n        if not force and self._synced_date == today and self._index:\n            return True\n        with self._lock:\n            if not force and self._synced_date == today and self._index:\n                return True                     # another thread just did it\n            try:\n                data = _download(timeout)\n                _validate_rows(data)            # a bad file never replaces a good cache\n            except Exception as e:\n                write_audit_log(f"[ANGEL_INSTR][WARN] Sync failed ERR={e}")\n                return False\n            _atomic_write_text(SCRIP_PATH, json.dumps(data))\n            _atomic_write_text(META_PATH,\n                               json.dumps({"synced_date": today.isoformat()}))\n            self._build_index(data)\n            self._synced_date = today\n        write_audit_log(\n            f"[ANGEL_INSTR] Synced scrip master rows={len(self._index)}")\n        return True\n\n    def _build_index(self, rows: list) -> None:\n        idx: Dict[Tuple[str, str, int, str], Tuple[str, str]] = {}\n        for row in rows:\n            if row.get("exch_seg") != "NFO":\n                continue\n            if row.get("instrumenttype") != "OPTIDX":\n                continue\n            name = row.get("name")\n            if name not in ("NIFTY", "BANKNIFTY"):\n                continue\n            sym = str(row.get("symbol", ""))\n            opt = sym[-2:]\n            if opt not in ("CE", "PE"):\n                continue\n            try:\n                strike = int(float(row.get("strike", "0")) / 100)  # paise\n                expiry = dt.datetime.strptime(\n                    row.get("expiry", ""), "%d%b%Y").date()\n            except Exception:\n                continue\n            idx[(name, expiry.isoformat(), strike, opt)] = (\n                sym, str(row.get("token")))\n        self._index = idx\n\n    # ---------------- staleness gate ----------------\n\n    def is_fresh(self) -> bool:\n        today = dt.datetime.now(tz=IST).date()\n        return self._synced_date is not None and (today - self._synced_date).days <= 1\n\n    def assert_fresh_for_live(self) -> None:\n        if not self.is_fresh():\n            raise AngelInstrumentStale(\n                f"Angel scrip master stale (synced={self._synced_date}); "\n                f"LIVE resolution refused")\n\n    # ── ANGEL_SCRIP_SYNC_20261030 ── self-healing gate. Fail-closed is kept\n    # (a token is never guessed), but a stale list is now refreshed instead\n    # of refusing every order: first re-read the disk cache (the 08:30 job or\n    # the boot sync may have written it from another instance), then download\n    # once, at most every ONDEMAND_COOLDOWN_S so an order burst never turns\n    # into a download storm.\n    def _ondemand_sync(self, why: str) -> bool:\n        now = time.time()\n        if now - self._last_ondemand < ONDEMAND_COOLDOWN_S:\n            return False\n        self._last_ondemand = now\n        write_audit_log(f"[ANGEL_INSTR][ON_DEMAND] {why} — downloading the scrip master")\n        return self.sync(force=True, timeout=ONDEMAND_TIMEOUT_S)\n\n    def ensure_fresh_for_live(self) -> None:\n        if self.is_fresh():\n            return\n        disk = _disk_synced_date()              # cheap: the small .meta file only\n        if disk and (self._synced_date is None or disk > self._synced_date):\n            self._load_cache()\n        if not self.is_fresh():\n            self._ondemand_sync(f"stale (synced={self._synced_date})")\n        self.assert_fresh_for_live()\n\n    # ---------------- lookup ----------------\n\n    def _lookup(self, name: str, expiry: dt.date, strike: int,\n                opt: str) -> Tuple[str, str]:\n        # exact date first (weekly path)\n        hit = self._index.get((name, expiry.isoformat(), strike, opt))\n        if hit:\n            return hit\n        # month-level match (monthly symbols: day unknown from Kite symbol)\n        month_hits = [\n            v for (n, e, k, o), v in self._index.items()\n            if n == name and k == strike and o == opt\n            and e[:7] == expiry.isoformat()[:7]\n        ]\n        if len(month_hits) == 1:\n            return month_hits[0]\n        raise AngelInstrumentError(\n            f"No unique Angel instrument for "\n            f"{name} {expiry} {strike}{opt} (hits={len(month_hits)})")\n\n    def resolve_from_kite_symbol(self, kite_symbol: str) -> Tuple[str, str]:\n        """\n        Kite canonical NFO symbol -> (angel_tradingsymbol, angel_token).\n        Raises on any ambiguity — callers never guess.\n        """\n        self.ensure_fresh_for_live()            # ── ANGEL_SCRIP_SYNC_20261030 ──\n        name, expiry, strike, opt = parse_kite_symbol(kite_symbol)\n        try:\n            return self._lookup(name, expiry, strike, opt)\n        except AngelInstrumentError:\n            # A strike/expiry listed after the cached copy was taken (Angel\n            # adds strikes as spot moves). One forced refresh, then retry.\n            if self._ondemand_sync(f"{kite_symbol} not in the cached list"):\n                return self._lookup(name, expiry, strike, opt)\n            raise\n\n\n# ── ANGEL_SCRIP_SYNC_20261030 ── ONE resolver per process, shared by the\n# Angel executor and the sync jobs, so a download by either is seen by both.\n_SHARED: Optional[AngelInstrumentResolver] = None\n_SHARED_LOCK = threading.Lock()\n\n\ndef get_shared_resolver() -> AngelInstrumentResolver:\n    global _SHARED\n    with _SHARED_LOCK:\n        if _SHARED is None:\n            _SHARED = AngelInstrumentResolver()\n        return _SHARED\n\n# ACC2 END')], 'backend/app/execution/angel_executor.py': [('from app.marketdata.angel_instrument_resolver import AngelInstrumentResolver\n', 'from app.marketdata.angel_instrument_resolver import AngelInstrumentResolver\nfrom app.marketdata.angel_instrument_resolver import get_shared_resolver   # ── ANGEL_SCRIP_SYNC_20261030 ──\n'), ('        self._resolver = resolver or AngelInstrumentResolver()\n', '        # ── ANGEL_SCRIP_SYNC_20261030 ── the process-wide resolver, so the\n        # daily/boot download is seen here without re-reading the file.\n        self._resolver = resolver or get_shared_resolver()\n')], 'backend/app/api_server.py': [('        attach_scheduler_observability(scheduler)\n        scheduler.start()\n', '        # ── ANGEL_SCRIP_SYNC_20261030 ── Angel One contract list: daily download\n        # 08:30 (+08:50 retry) and a 09:05 pre-open check, Mon–Fri; a background\n        # download at boot. All no-ops when no strategy is on Account 2.\n        try:\n            from app.marketdata.angel_scrip_sync import (\n                sync_job as _angel_sync_job, preopen_check_job as _angel_preopen_job,\n                start_boot_sync as _angel_boot_sync)\n            for _hh, _mm in ((8, 30), (8, 50)):\n                scheduler.add_job(_angel_sync_job, trigger="cron", day_of_week="mon-fri",\n                                  hour=_hh, minute=_mm, args=[f"{_hh:02d}:{_mm:02d}"],\n                                  id=f"angel_scrip_sync_{_hh:02d}{_mm:02d}",\n                                  replace_existing=True, timezone="Asia/Kolkata")\n            scheduler.add_job(_angel_preopen_job, trigger="cron", day_of_week="mon-fri",\n                              hour=9, minute=5, id="angel_scrip_preopen",\n                              replace_existing=True, timezone="Asia/Kolkata")\n            _angel_boot_sync()\n        except Exception as _angel_sync_err:\n            write_audit_log(f"[SYSTEM] Angel scrip sync NOT scheduled: {_angel_sync_err!r}")\n        attach_scheduler_observability(scheduler)\n        scheduler.start()\n')]}

MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}


def die(msg):
    print(f"\n✗ {msg}\n  Nothing was written.")
    sys.exit(1)


def main():
    root = Path.cwd()
    check_only = "--check" in sys.argv
    allow_dirty = "--allow-dirty" in sys.argv
    if not (root / "backend/app").is_dir() or not (root / "frontend/src").is_dir():
        die("run this from the scalp-app repo root (backend/ and frontend/ not found)")

    # ── prerequisites ───────────────────────────────────────────────────
    pf = root / PARENT_FILE
    if not pf.exists() or PARENT not in pf.read_text():
        die(f"parent marker {PARENT!r} missing from {PARENT_FILE} (Account 2 wiring not present)")
    for rel in list(EDITS) + list(NEW_FILES):
        p = root / rel
        if p.exists() and FENCE in p.read_text():
            die(f"{FENCE} already present in {rel} — already applied")

    # ── working-copy drift guard (ORB_RECONCILE scar) ───────────────────
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + list(EDITS),
                            capture_output=True, text=True, cwd=root, timeout=30)
        dirty = [l for l in st.stdout.splitlines() if l.strip()]
        if dirty and not allow_dirty:
            die("uncommitted changes in target files:\n    " + "\n    ".join(dirty)
                + "\n  Commit or stash them, or re-run with --allow-dirty if they are yours.")
    except FileNotFoundError:
        print("  ! git not found — skipping the working-copy check")

    # ── stage every change in memory; every anchor must match exactly once ─
    staged = {}
    for rel, pairs in EDITS.items():
        p = root / rel
        if not p.exists():
            die(f"target missing: {rel}")
        txt = p.read_text()
        for i, (old, new) in enumerate(pairs, 1):
            n = txt.count(old)
            if n != 1:
                die(f"{rel}: anchor {i} matched {n} times (expected 1) — the file differs "
                    f"from GitHub main. First line of the anchor:\n    {old.splitlines()[0]!r}")
            txt = txt.replace(old, new, 1)
        staged[rel] = txt
    for rel, txt in NEW_FILES.items():
        if (root / rel).exists():
            die(f"{rel} already exists — refusing to overwrite")
        staged[rel] = txt

    # ── py_compile gate on staged Python ────────────────────────────────
    tmpd = Path(tempfile.mkdtemp(prefix="angel_scrip_pyc_"))
    try:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                f = tmpd / rel.replace("/", "__")
                f.write_text(txt)
                try:
                    py_compile.compile(str(f), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    if check_only:
        print(f"✓ --check: {len(EDITS)} files patch cleanly, {len(NEW_FILES)} new files, py_compile OK.")
        return

    # ── write: backups first, then files, then mirrors ──────────────────
    written, backups = [], []

    def rollback():
        for rel in written:
            p = root / rel
            b = p.with_name(p.name + f".bak-{FENCE}")
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
            elif p.exists():
                p.unlink()

    try:
        for rel, txt in staged.items():
            p = root / rel
            if p.exists():
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append(str(b))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt)
            written.append(rel)
    except Exception as e:
        rollback()
        die(f"write failed ({e!r}) — rolled back")

    # ── behavioural suites on the patched tree ──────────────────────────
    env = dict(os.environ, PYTHONPATH=str(root / "backend"))
    suites = ["app/marketdata/test_angel_scrip_sync.py", "app/execution/test_strategy_executor.py",
              "app/trading/test_fleet_modes.py", "app/trading/test_paper_lots.py"]
    for s in suites:
        if not (root / "backend" / s).exists():
            print(f"\n── {s}: not present on this tree — skipped")
            continue
        r = subprocess.run([sys.executable, s], cwd=root / "backend", env=env,
                           capture_output=True, text=True, timeout=300)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
        print(f"\n── {s}\n{tail}")
        if r.returncode != 0:
            rollback()
            die(f"{s} failed — every file rolled back")

    # ── JSX parse check (esbuild if present) ────────────────────────────
    esb = None
    for cand in ("frontend/node_modules/.bin/esbuild", "desktop/node_modules/.bin/esbuild",
                 "desktop/src-tauri/frontend/node_modules/.bin/esbuild"):
        if (root / cand).exists():
            esb = root / cand
            break
    jsx = [rel for rel in staged if rel.endswith(".jsx")]
    if esb:
        for rel in jsx:
            r = subprocess.run([str(esb), str(root / rel), "--loader:.jsx=jsx", "--log-level=error",
                                "--outfile=/dev/null"], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                rollback()
                die(f"esbuild could not parse {rel}:\n{r.stderr[-1500:]}\n  rolled back")
        print(f"\n✓ esbuild parsed {len(jsx)} JSX files")
    else:
        print("\n! esbuild not found — JSX is checked by the frontend build instead")

    # ── mirror into the desktop build trees, if present ─────────────────
    mirrored = 0
    for rel, txt in staged.items():
        for src, dst in MIRRORS.items():
            if rel.startswith(src):
                mp = root / (dst + rel[len(src):])
                if (root / dst).is_dir():
                    mp.parent.mkdir(parents=True, exist_ok=True)
                    mp.write_text(txt)
                    mirrored += 1

    print(f"\n✓ {FENCE} applied: {len(EDITS)} files patched, {len(NEW_FILES)} new"
          f"{f', {mirrored} build-tree copies' if mirrored else ''}.")
    print("  Backups: *.bak-" + FENCE)
    print("  Next:    ./desktop/build-scalp.sh both   (non-trading hours)")


if __name__ == "__main__":
    main()
