# backend/app/execution/broker_truth.py
#
# ── BROKER_TRUTH_20261114 ── ONE place that decides what the app may believe
# about the broker. Written after 13 Nov 2026 (three live strategies on one
# account) exposed two assumptions baked into every exit path and monitor:
#
#   1. "An empty answer means nothing is there."  get_gtts() / get_open_
#      positions() return [] when they CANNOT read (stale morning token, 05:00
#      session flush, network). The GTT monitors read [] as "GTTs gone AND
#      position gone" and booked BROKER_EXIT on live shorts that were still
#      open — at 07:43 on app start, and again at 05:00 the next morning.
#   2. "The broker's net position on a symbol is MY position."  True only
#      while one strategy holds that contract. Velvet and Timberwolf were
#      both short 25050 CE: Velvet's exit saw "still short" after its own
#      buy-back had filled, bought again, and closed Timberwolf's short.
#
# The rules, used by Icarus, Timberwolf, Tigris and Velvet:
#
#   * read_positions() / read_gtts()  — None means UNREADABLE. Never [].
#   * session_open()                  — broker-side events (a GTT firing, a
#                                       fill) only happen 09:15–15:40 on a
#                                       trading day. Outside it a monitor
#                                       infers nothing, whatever it reads.
#   * LEDGER                          — every live manager registers a view of
#                                       the legs it holds, and reserves the
#                                       symbols of an entry before its first
#                                       order. `others(symbol, me)` is what
#                                       OTHER strategies hold there.
#   * own_net_qty()                   — the broker's net quantity for a symbol
#                                       ONLY when this strategy is its sole
#                                       holder; None when unreadable OR shared.
#                                       Every caller already treats None as
#                                       "trust my own orders' fills" — which
#                                       is exactly right for a shared contract.
#
# Pure helpers, no I/O of their own, never raise.
from __future__ import annotations

import threading
import weakref
from typing import Callable, Dict, List, Optional

FENCE = "BROKER_TRUTH_20261114"


# ── strict reads ───────────────────────────────────────────────────────────
def read_positions(executor) -> Optional[Dict[str, int]]:
    """{tradingsymbol: net qty} from a SUCCESSFUL read; None = unreadable.
    An executor without a strict read is unreadable by definition."""
    fn = getattr(executor, "get_open_positions_or_none", None)
    if not callable(fn):
        return None
    try:
        pos = fn()
    except Exception:
        return None
    if pos is None:
        return None
    out: Dict[str, int] = {}
    try:
        for p in pos:
            s = str(p.get("tradingsymbol") or "")
            if s:
                out[s] = out.get(s, 0) + int(p.get("quantity") or 0)
    except Exception:
        return None
    return out


def read_gtts(executor) -> Optional[List[dict]]:
    """The broker's GTT list from a SUCCESSFUL read; None = unreadable."""
    fn = getattr(executor, "get_gtts_or_none", None)
    if not callable(fn):
        return None
    try:
        g = fn()
    except Exception:
        return None
    return None if g is None else list(g)


def session_open() -> bool:
    """True only while the derivatives session is open (holiday-aware).
    Fail CLOSED: if the calendar cannot be read, nothing may be inferred."""
    try:
        from app.utils.market_hours import is_market_open
        return bool(is_market_open())
    except Exception:
        return False


# ── who holds what (live books only) ───────────────────────────────────────
class LiveLedger:
    """Process-wide view of the LIVE legs each strategy holds.

    A provider returns {tradingsymbol: signed qty} (short negative) for the
    strategy's open live legs — it may OVER-report (a leg mid-exit), never
    under-report. A reservation covers the gap between choosing an entry's
    contracts and the manager's own state showing them."""

    def __init__(self):
        self._lock = threading.RLock()
        # sid -> providers. Several manager objects can exist for one strategy
        # (a restore builds a new one; a paper instance beside the live one):
        # every live object is asked, a dead one drops out by itself, and a
        # paper instance simply answers {} — it can never hide a live holding.
        self._providers: Dict[str, list] = {}
        self._reserved: Dict[str, Dict[str, int]] = {}

    def register(self, sid: str, provider: Callable[[], Dict[str, int]]) -> None:
        try:
            ref = weakref.WeakMethod(provider)        # bound method: don't keep the manager alive
        except TypeError:
            ref = (lambda p=provider: p)              # plain function
        with self._lock:
            self._providers.setdefault(str(sid), []).append(ref)

    def unregister(self, sid: str) -> None:
        with self._lock:
            self._providers.pop(str(sid), None)
            self._reserved.pop(str(sid), None)

    def reserve(self, sid: str, legs: Dict[str, int]) -> None:
        with self._lock:
            cur = self._reserved.setdefault(str(sid), {})
            for s, q in (legs or {}).items():
                if s:
                    cur[str(s)] = cur.get(str(s), 0) + int(q or 0)

    def release(self, sid: str) -> None:
        with self._lock:
            self._reserved.pop(str(sid), None)

    def _view(self, sid: str) -> Optional[Dict[str, int]]:
        """Holdings of one strategy; None when its provider failed (unknown)."""
        out: Dict[str, int] = {}
        alive = []
        for ref in list(self._providers.get(sid) or []):
            fn = ref()
            if fn is None:
                continue                              # manager object is gone
            alive.append(ref)
            try:
                for s, q in (fn() or {}).items():
                    q = int(q or 0)
                    # two objects describing the same position must not double it
                    if s and q and abs(q) > abs(out.get(str(s), 0)):
                        out[str(s)] = q
            except Exception:
                return None
        if sid in self._providers:
            self._providers[sid] = alive
        for s, q in (self._reserved.get(sid) or {}).items():
            if s not in out and q:
                out[s] = int(q)
        return out

    def holders(self, symbol: str, me: Optional[str] = None) -> Optional[Dict[str, int]]:
        """{strategy: signed qty} of every OTHER strategy holding `symbol`.
        None = some provider could not answer (treat the symbol as shared)."""
        with self._lock:
            sids = set(self._providers) | set(self._reserved)
            out: Dict[str, int] = {}
            for sid in sids:
                if me is not None and sid == str(me):
                    continue
                if me is not None and not _same_account(str(me), sid):
                    continue                      # a different broker account
                v = self._view(sid)
                if v is None:
                    return None
                q = v.get(str(symbol), 0)
                if q:
                    out[sid] = q
            # Strategies that do not register a view (anything outside the
            # four core ones) are read from their OPEN LIVE rows instead, so
            # a contract they hold is still known to be shared.
            if me is not None:
                for sid, q in (_db_holders().get(str(symbol)) or {}).items():
                    if sid == str(me) or sid in self._providers or not q:
                        continue
                    if _same_account(str(me), sid):
                        out[sid] = int(q)
            return out

    def others(self, symbol: str, me: str) -> Optional[int]:
        """Signed quantity other strategies hold on `symbol`; None = unknown."""
        h = self.holders(symbol, me)
        return None if h is None else sum(h.values())

    def shared(self, symbol: str, me: str) -> bool:
        """True when another strategy holds (or is entering) `symbol`, or when
        that cannot be ruled out."""
        h = self.holders(symbol, me)
        return h is None or bool(h)

    def opposite(self, symbol: str, me: str, side: str) -> Optional[str]:
        """Name of another strategy that holds `symbol` on the OPPOSITE side
        of a new `side` ("BUY" / "SELL") leg, else None.

        Two strategies on the same side of a contract simply add up. On
        OPPOSITE sides they cancel at the broker: no margin is held for the
        short, and whichever leg is closed later turns the netted quantity
        into a fresh position the broker must margin — an exit that can be
        REJECTED for funds. New legs therefore avoid such a contract (the
        next candidate is taken). Unknown holdings never block an entry."""
        h = self.holders(symbol, me)
        if not h:
            return None
        buy = str(side).upper() == "BUY"
        for sid, q in h.items():
            if (q < 0) if buy else (q > 0):
                return sid
        return None

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            out = {}
            for sid in set(self._providers) | set(self._reserved):
                v = self._view(sid)
                if v:
                    out[sid] = v
            return out

    def clear(self) -> None:          # tests
        with self._lock:
            self._providers.clear()
            self._reserved.clear()


# ── holdings of strategies without a registered view (from their DB rows) ──
DB_FALLBACK = True
_DB_BOOK: Dict[str, object] = {"ts": 0.0, "who": {}}
_DB_TTL_S = 3.0


def _db_holders() -> Dict[str, Dict[str, int]]:
    """{symbol: {strategy: signed qty}} of OPEN LIVE legs on the Zerodha
    account, read through the broker-check's own reader. Cached briefly;
    any failure reads as "nothing known" (the registered views still apply)."""
    if not DB_FALLBACK:
        return {}
    import time as _t
    now = _t.time()
    if now - float(_DB_BOOK["ts"]) < _DB_TTL_S:
        return _DB_BOOK["who"]                        # type: ignore[return-value]
    who: Dict[str, Dict[str, int]] = {}
    try:
        from app.recon import recon_core as _RC
        _qty, who, _bad = _RC.app_book(_RC.zerodha_strategies())
    except Exception:
        who = {}
    _DB_BOOK["ts"], _DB_BOOK["who"] = now, (who or {})
    return _DB_BOOK["who"]                            # type: ignore[return-value]


def _same_account(a: str, b: str) -> bool:
    """Two strategies can only share a position when they trade through the
    same broker account. Unknown → assume they do (the cautious answer)."""
    try:
        from app.config.account_bindings import resolve_broker
        ba, bb = resolve_broker(a), resolve_broker(b)
        return (not ba) or (not bb) or ba == bb
    except Exception:
        return True


LEDGER = LiveLedger()
_SHARED_LOGGED: Dict[tuple, float] = {}


def own_net_qty(executor, symbol: str, me: str, *, log=None) -> Optional[int]:
    """Broker net quantity that is certainly THIS strategy's.

    Returns the broker's net qty for `symbol` (0 when absent) only when the
    positions were read AND no other strategy holds the contract. None when
    unreadable or shared — the caller then acts on its own orders alone
    (place exactly its own quantity; its own COMPLETE fill is the truth)."""
    if LEDGER.shared(symbol, me):
        if log is not None:
            try:
                import time as _t
                key = (str(me), str(symbol))
                if _t.time() - _SHARED_LOGGED.get(key, 0.0) > 600:      # once per 10 min
                    _SHARED_LOGGED[key] = _t.time()
                    h = LEDGER.holders(symbol, me)
                    log(f"[BROKER_TRUTH] {symbol} is also held by "
                        f"{', '.join(sorted(h)) if h else 'another strategy (unknown)'} — "
                        f"{me} acts on its own orders only (net position not used)")
            except Exception:
                pass
        return None
    pos = read_positions(executor)
    if pos is None:
        return None
    return int(pos.get(str(symbol), 0))


def expected_net(symbol: str, me: str, own_qty: int) -> Optional[int]:
    """What the broker should show on `symbol`: this strategy's quantity plus
    every other strategy's. None = another strategy's holding is unknown."""
    o = LEDGER.others(symbol, me)
    return None if o is None else int(own_qty) + int(o)
