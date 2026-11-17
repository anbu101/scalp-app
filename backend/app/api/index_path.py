# backend/app/api/index_path.py
#
# ── INDEX_PATH_20261117 ── today's 1-minute NIFTY and BANKNIFTY line for the Dashboard
# (option C: the market drawn behind the MTM chart). Served by GET /api/fleet/indices
# (fleet_today_routes). Read-only; no orders, no strategy state.
#
#   path   Kite historical 1-minute closes for today, at most ONE call per index per
#          KITE_EVERY_S (cached; a lock keeps concurrent polls from doubling calls),
#          then the minutes since — and every minute when Kite is unavailable — from
#          the app's own samples of the live index price (MarketIndicesState, written
#          by the tick engines). Samples are taken by the per-minute fleet job and on
#          every read, kept for today only, in memory.
#   ltp / prev_close   MarketIndicesState (the same numbers the index badges show).
#
# Never raises to the caller: a failure is a note, and the line shows what is known.
import threading
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TOKENS = {"NIFTY": 256265, "BANKNIFTY": 260105}   # Kite's NIFTY 50 / NIFTY BANK index tokens
OPEN_MIN, CLOSE_MIN = 555, 930                   # 09:15 … 15:30 IST
KITE_EVERY_S = 60.0

_LOCK = threading.Lock()
_KITE_CACHE = {}        # idx -> {"day": "YYYY-MM-DD", "at": monotonic, "rows": {min: close}, "err": str|None}
_SAMPLES = {}           # idx -> {"day": "YYYY-MM-DD", "pts": {min: ltp}}


def _now() -> datetime:
    return datetime.now(IST)


def _mono() -> float:
    return time.monotonic()


def _snapshot() -> dict:
    from app.marketdata.market_indices_state import MarketIndicesState
    return MarketIndicesState.snapshot()


def _kite():
    from app.backtest.replay.live_replay import _kite as k
    return k()


def _session_min(now: datetime) -> int:
    return now.hour * 60 + now.minute


def sample(now: datetime = None) -> None:
    """Record the live index price for this minute (09:15–15:30, weekdays). Never raises."""
    try:
        now = now or _now()
        m = _session_min(now)
        if now.weekday() >= 5 or not (OPEN_MIN <= m <= CLOSE_MIN):
            return
        snap = _snapshot()
        day = now.date().isoformat()
        with _LOCK:
            for idx in TOKENS:
                ltp = (snap.get(idx) or {}).get("ltp")
                if not isinstance(ltp, (int, float)) or ltp <= 0:
                    continue
                s = _SAMPLES.get(idx)
                if not s or s["day"] != day:
                    s = _SAMPLES[idx] = {"day": day, "pts": {}}
                s["pts"][m] = float(ltp)
    except Exception:
        pass


def _kite_rows(idx: str, now: datetime) -> tuple:
    """{minute: close} for today from Kite, cached KITE_EVERY_S. Returns (rows, err)."""
    day = now.date().isoformat()
    with _LOCK:
        c = _KITE_CACHE.get(idx)
        every = KITE_EVERY_S if _session_min(now) <= CLOSE_MIN + 5 else 900.0   # after the close the line is final
        if c and c["day"] == day and _mono() - c["at"] < every:
            return c["rows"], c["err"]
        # claim the slot before calling out, so a concurrent poll serves the cache
        _KITE_CACHE[idx] = {"day": day, "at": _mono(), "rows": (c or {}).get("rows", {}) if c and c["day"] == day else {}, "err": None}
    rows, err = {}, None
    try:
        kite = _kite()
        if kite is None:
            err = "no Kite session"
        else:
            data = kite.historical_data(instrument_token=TOKENS[idx],
                                        from_date=now.strftime("%Y-%m-%d 09:15:00"),
                                        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                                        interval="minute") or []
            for r in data:
                d = r.get("date")
                if isinstance(d, str):
                    d = datetime.fromisoformat(d)
                if d is None:
                    continue
                if d.tzinfo is not None:
                    d = d.astimezone(IST)
                if d.date() != now.date():
                    continue
                m = d.hour * 60 + d.minute
                if OPEN_MIN <= m <= CLOSE_MIN and r.get("close"):
                    rows[m] = float(r["close"])
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
    with _LOCK:
        prev = _KITE_CACHE.get(idx) or {}
        keep = rows or (prev.get("rows") or {})          # a failed call keeps the last good rows
        _KITE_CACHE[idx] = {"day": day, "at": _mono(), "rows": keep, "err": err}
        return keep, err


def build(now: datetime = None) -> dict:
    now = now or _now()
    sample(now)
    snap = _snapshot()
    m_now = _session_min(now)
    trading = now.weekday() < 5 and m_now >= OPEN_MIN
    out = {"ok": True, "day": now.date().isoformat(), "now_min": m_now, "indices": {}}
    for idx in TOKENS:
        s = snap.get(idx) or {}
        kite_rows, err = (_kite_rows(idx, now) if trading else ({}, None))
        with _LOCK:
            smp = dict((_SAMPLES.get(idx) or {}).get("pts", {})) if (_SAMPLES.get(idx) or {}).get("day") == out["day"] else {}
        pts = dict(smp)
        pts.update(kite_rows)                      # Kite's closed minutes win; own samples fill the rest
        last_kite = max(kite_rows) if kite_rows else -1
        for k_, v in smp.items():
            if k_ > last_kite:
                pts[k_] = v                       # minutes Kite has not closed yet: the live samples
        ltp = s.get("ltp")
        if trading and OPEN_MIN <= m_now <= CLOSE_MIN and isinstance(ltp, (int, float)) and ltp > 0:
            pts[m_now] = float(ltp)               # the forming minute = the live price
        path = [[k_, round(pts[k_], 2)] for k_ in sorted(pts)]
        src = "kite+live" if kite_rows else ("live samples" if smp else "none")
        out["indices"][idx] = {"ltp": ltp, "prev_close": s.get("prev_close"), "path": path, "source": src,
                               "note": err if (err and not kite_rows) else None}
    return out
