# backend/app/marketdata/angel_instrument_resolver.py
# ============================================================
# ACC2 BEGIN — Kite-canonical symbol -> Angel (tradingsymbol, token)
#
# Source: Angel daily scrip master JSON, cached at
#   ~/.scalp-app/angelone/scrip_master.json (+ .meta with sync date)
#
# FAIL-CLOSED RULES:
#   - Cache stale > 1 calendar day behind today's IST date -> resolver
#     refuses LIVE resolution (raises AngelInstrumentStale).
#   - Any parse/lookup failure raises; callers must not guess tokens.
#
# Kite NFO option formats handled (index options only — NIFTY/BANKNIFTY):
#   Monthly : NIFTY25AUG24500CE           ({yy}{MON}{strike})
#   Weekly  : NIFTY2580724500CE           ({yy}{M}{dd}{strike},
#             M in 1..9, O=Oct, N=Nov, D=Dec)
# ============================================================

import datetime as dt
import json
import os
import re
import tempfile
import threading                                   # ── ANGEL_SCRIP_SYNC_20261030 ──
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

from app.event_bus.audit_logger import write_audit_log
from app.utils.app_paths import APP_HOME, ensure_app_dirs

ensure_app_dirs()

ANGEL_DIR = APP_HOME / "angelone"
ANGEL_DIR.mkdir(parents=True, exist_ok=True)

SCRIP_PATH = ANGEL_DIR / "scrip_master.json"
META_PATH = ANGEL_DIR / "scrip_master.meta.json"

SCRIP_URL = ("https://margincalculator.angelbroking.com"
             "/OpenAPI_File/files/OpenAPIScripMaster.json")

# ── ANGEL_SCRIP_SYNC_20261030 ── on-demand refresh limits + download helpers
ONDEMAND_COOLDOWN_S = 600      # at most one on-demand download per 10 min
ONDEMAND_TIMEOUT_S = 45        # an order waits at most this long for it
_MIN_ROWS = 1000               # Angel's full file has 100k+ rows


def _download(timeout: float):
    r = requests.get(SCRIP_URL, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _validate_rows(data) -> None:
    if not isinstance(data, list) or len(data) < _MIN_ROWS:
        raise ValueError(f"scrip master looks wrong "
                         f"({type(data).__name__}, {len(data) if hasattr(data, '__len__') else '?'} rows)")


def _disk_synced_date():
    try:
        return dt.date.fromisoformat(json.loads(META_PATH.read_text())["synced_date"])
    except Exception:
        return None

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_WEEKLY_M = {**{str(i): i for i in range(1, 10)}, "O": 10, "N": 11, "D": 12}

_MONTHLY_RE = re.compile(
    r"^(?P<name>NIFTY|BANKNIFTY)(?P<yy>\d{2})(?P<mon>[A-Z]{3})"
    r"(?P<strike>\d+)(?P<opt>CE|PE)$")
_WEEKLY_RE = re.compile(
    r"^(?P<name>NIFTY|BANKNIFTY)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})"
    r"(?P<strike>\d+)(?P<opt>CE|PE)$")


class AngelInstrumentError(RuntimeError):
    pass


class AngelInstrumentStale(AngelInstrumentError):
    pass


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------
# KITE SYMBOL PARSING
# --------------------------------------------------

def parse_kite_symbol(symbol: str) -> Tuple[str, dt.date, int, str]:
    """
    Returns (name, expiry_date, strike, opt_type).
    Monthly expiry date is NOT derivable from the symbol alone (last-Tue
    etc.), so monthly returns are matched against the scrip master by
    (name, year, month, strike, opt) instead — see _lookup().
    Weekly encodes the exact date, so it parses fully.
    """
    s = symbol.strip().upper()

    m = _WEEKLY_RE.match(s)
    if m:
        year = 2000 + int(m.group("yy"))
        month = _WEEKLY_M[m.group("m")]
        day = int(m.group("dd"))
        return (m.group("name"),
                dt.date(year, month, day),
                int(m.group("strike")),
                m.group("opt"))

    m = _MONTHLY_RE.match(s)
    if m:
        mon = m.group("mon")
        if mon not in _MONTHS:
            raise AngelInstrumentError(f"Bad month in symbol: {symbol}")
        year = 2000 + int(m.group("yy"))
        # day=0 sentinel: month-level match against scrip master
        return (m.group("name"),
                dt.date(year, _MONTHS[mon], 1),
                int(m.group("strike")),
                m.group("opt"))

    raise AngelInstrumentError(f"Unparseable Kite NFO symbol: {symbol}")


# --------------------------------------------------
# SCRIP MASTER SYNC + INDEX
# --------------------------------------------------

class AngelInstrumentResolver:

    def __init__(self):
        # index key: (name, expiry_date_iso, strike, opt) -> (symbol, token)
        self._index: Dict[Tuple[str, str, int, str], Tuple[str, str]] = {}
        self._synced_date: Optional[dt.date] = None
        self._lock = threading.RLock()          # ── ANGEL_SCRIP_SYNC_20261030 ──
        self._last_ondemand: float = 0.0        # epoch s of the last on-demand download
        self._load_cache()

    # ---------------- cache ----------------

    def _load_cache(self) -> None:
        if not (SCRIP_PATH.exists() and META_PATH.exists()):
            return
        try:
            meta = json.loads(META_PATH.read_text())
            synced = dt.date.fromisoformat(meta["synced_date"])
            rows = json.loads(SCRIP_PATH.read_text())
            with self._lock:
                self._build_index(rows)
                self._synced_date = synced
            write_audit_log(
                f"[ANGEL_INSTR] Cache loaded "
                f"synced={self._synced_date} rows={len(self._index)}")
        except Exception as e:
            write_audit_log(f"[ANGEL_INSTR][WARN] Cache load failed ERR={e}")
            with self._lock:
                self._index = {}
                self._synced_date = None

    def sync(self, force: bool = False, timeout: float = 120) -> bool:
        """Daily sync. Cheap no-op when already synced today (IST)."""
        today = dt.datetime.now(tz=IST).date()
        if not force and self._synced_date == today and self._index:
            return True
        with self._lock:
            if not force and self._synced_date == today and self._index:
                return True                     # another thread just did it
            try:
                data = _download(timeout)
                _validate_rows(data)            # a bad file never replaces a good cache
            except Exception as e:
                write_audit_log(f"[ANGEL_INSTR][WARN] Sync failed ERR={e}")
                return False
            _atomic_write_text(SCRIP_PATH, json.dumps(data))
            _atomic_write_text(META_PATH,
                               json.dumps({"synced_date": today.isoformat()}))
            self._build_index(data)
            self._synced_date = today
        write_audit_log(
            f"[ANGEL_INSTR] Synced scrip master rows={len(self._index)}")
        return True

    def _build_index(self, rows: list) -> None:
        idx: Dict[Tuple[str, str, int, str], Tuple[str, str]] = {}
        for row in rows:
            if row.get("exch_seg") != "NFO":
                continue
            if row.get("instrumenttype") != "OPTIDX":
                continue
            name = row.get("name")
            if name not in ("NIFTY", "BANKNIFTY"):
                continue
            sym = str(row.get("symbol", ""))
            opt = sym[-2:]
            if opt not in ("CE", "PE"):
                continue
            try:
                strike = int(float(row.get("strike", "0")) / 100)  # paise
                expiry = dt.datetime.strptime(
                    row.get("expiry", ""), "%d%b%Y").date()
            except Exception:
                continue
            idx[(name, expiry.isoformat(), strike, opt)] = (
                sym, str(row.get("token")))
        self._index = idx

    # ---------------- staleness gate ----------------

    def is_fresh(self) -> bool:
        today = dt.datetime.now(tz=IST).date()
        return self._synced_date is not None and (today - self._synced_date).days <= 1

    def assert_fresh_for_live(self) -> None:
        if not self.is_fresh():
            raise AngelInstrumentStale(
                f"Angel scrip master stale (synced={self._synced_date}); "
                f"LIVE resolution refused")

    # ── ANGEL_SCRIP_SYNC_20261030 ── self-healing gate. Fail-closed is kept
    # (a token is never guessed), but a stale list is now refreshed instead
    # of refusing every order: first re-read the disk cache (the 08:30 job or
    # the boot sync may have written it from another instance), then download
    # once, at most every ONDEMAND_COOLDOWN_S so an order burst never turns
    # into a download storm.
    def _ondemand_sync(self, why: str) -> bool:
        now = time.time()
        if now - self._last_ondemand < ONDEMAND_COOLDOWN_S:
            return False
        self._last_ondemand = now
        write_audit_log(f"[ANGEL_INSTR][ON_DEMAND] {why} — downloading the scrip master")
        return self.sync(force=True, timeout=ONDEMAND_TIMEOUT_S)

    def ensure_fresh_for_live(self) -> None:
        if self.is_fresh():
            return
        disk = _disk_synced_date()              # cheap: the small .meta file only
        if disk and (self._synced_date is None or disk > self._synced_date):
            self._load_cache()
        if not self.is_fresh():
            self._ondemand_sync(f"stale (synced={self._synced_date})")
        self.assert_fresh_for_live()

    # ---------------- lookup ----------------

    def _lookup(self, name: str, expiry: dt.date, strike: int,
                opt: str) -> Tuple[str, str]:
        # exact date first (weekly path)
        hit = self._index.get((name, expiry.isoformat(), strike, opt))
        if hit:
            return hit
        # month-level match (monthly symbols: day unknown from Kite symbol)
        month_hits = [
            v for (n, e, k, o), v in self._index.items()
            if n == name and k == strike and o == opt
            and e[:7] == expiry.isoformat()[:7]
        ]
        if len(month_hits) == 1:
            return month_hits[0]
        raise AngelInstrumentError(
            f"No unique Angel instrument for "
            f"{name} {expiry} {strike}{opt} (hits={len(month_hits)})")

    def resolve_from_kite_symbol(self, kite_symbol: str) -> Tuple[str, str]:
        """
        Kite canonical NFO symbol -> (angel_tradingsymbol, angel_token).
        Raises on any ambiguity — callers never guess.
        """
        self.ensure_fresh_for_live()            # ── ANGEL_SCRIP_SYNC_20261030 ──
        name, expiry, strike, opt = parse_kite_symbol(kite_symbol)
        try:
            return self._lookup(name, expiry, strike, opt)
        except AngelInstrumentError:
            # A strike/expiry listed after the cached copy was taken (Angel
            # adds strikes as spot moves). One forced refresh, then retry.
            if self._ondemand_sync(f"{kite_symbol} not in the cached list"):
                return self._lookup(name, expiry, strike, opt)
            raise


# ── ANGEL_SCRIP_SYNC_20261030 ── ONE resolver per process, shared by the
# Angel executor and the sync jobs, so a download by either is seen by both.
_SHARED: Optional[AngelInstrumentResolver] = None
_SHARED_LOCK = threading.Lock()


def get_shared_resolver() -> AngelInstrumentResolver:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = AngelInstrumentResolver()
        return _SHARED

# ACC2 END