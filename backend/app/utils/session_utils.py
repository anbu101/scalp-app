from datetime import datetime
from functools import lru_cache


# ── SESSION_PARSE_CACHE_20260826 ── Profile (D13): is_within_session ran
# 116,986 times in one backtest month — 233,972 strptime parses of the SAME
# handful of "HH:MM" literals (~18% of serial runtime). The parse is pure
# and datetime.time is immutable, so cache it. Behaviour is identical for
# every caller, live included: a malformed string still raises ValueError
# on EVERY call (lru_cache does not cache exceptions), and a config edit
# introducing a new session string is simply a new cache entry.
@lru_cache(maxsize=128)
def _parse_hhmm(s: str):
    return datetime.strptime(s, "%H:%M").time()


def is_within_session(now: datetime, start: str, end: str) -> bool:
    return _parse_hhmm(start) <= now.time() <= _parse_hhmm(end)
