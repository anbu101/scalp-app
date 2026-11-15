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


# ── STRICT_READS_ALL_20261115 ── the same truth for every other strategy ────
# The strategies outside the core four (and the older slot reconciliation
# job) still called get_open_positions() / get_gtts(), which answer [] when
# the session is not ready — "nothing is there" — and then closed LIVE trades
# on it (the 13/14 Nov shape). Every one of those call sites already treats an
# EXCEPTION as "cannot tell → do nothing this cycle", so the drop-in readers
# below turn an unreadable answer into exactly that exception.
#   * a broker with strict readers (Zerodha): None → BrokerUnreadable;
#   * a broker without them (Angel, Dhan, test stubs): the old call, unchanged;
#   * in_session_only=True (the periodic monitors): outside the derivatives
#     session nothing may be inferred — a GTT cannot fire then.
class BrokerUnreadable(Exception):
    """The broker's answer could not be read — nothing may be concluded."""


def _strict_capable(executor, name: str) -> bool:
    raw = getattr(executor, "_executor", executor)
    return callable(getattr(raw, name, None))


def infer_allowed(executor) -> bool:
    """False when a strict-capable broker is outside the derivatives session."""
    if not _strict_capable(executor, "get_open_positions_or_none"):
        return True
    return session_open()


def positions_strict(executor, *, in_session_only: bool = False) -> list:
    """Drop-in for executor.get_open_positions(): the same list, or raises
    BrokerUnreadable instead of returning an empty list it cannot vouch for."""
    if not _strict_capable(executor, "get_open_positions_or_none"):
        return list(executor.get_open_positions() or [])
    if in_session_only and not session_open():
        raise BrokerUnreadable("outside the derivatives session — nothing inferred")
    pos = executor.get_open_positions_or_none()
    if pos is None:
        raise BrokerUnreadable("positions unreadable (session not ready or call failed)")
    return list(pos)


def gtts_strict(executor, *, in_session_only: bool = False) -> list:
    """Drop-in for executor.get_gtts(): the same list, or raises BrokerUnreadable."""
    if not _strict_capable(executor, "get_gtts_or_none"):
        return list(executor.get_gtts() or [])
    if in_session_only and not session_open():
        raise BrokerUnreadable("outside the derivatives session — nothing inferred")
    g = executor.get_gtts_or_none()
    if g is None:
        raise BrokerUnreadable("GTT list unreadable (session not ready or call failed)")
    return list(g)


def session_open() -> bool:
    """True only while the derivatives session is open (holiday-aware).
    Fail CLOSED: if the calendar cannot be read, nothing may be inferred."""
    try:
        from app.utils.market_hours import is_market_open
        return bool(is_market_open())
    except Exception:
        return False


# ── GTT_MONITOR_PROOF_20261115 ── which orders did THIS APP place? ──────────
# "A COMPLETE exit-side order on the contract" is the evidence the monitors
# use when a GTT has not listed its own order, and the proof that a leg which
# reads flat really was closed by the broker or by hand. But on one account
# that order can be ANOTHER strategy's exit (Icarus booked Tigris's buy-back
# as its own stop fill). A GTT's order and a manual order are never placed by
# the app, so: every order the ExecutionRouter places is noted here, and that
# evidence may only come from orders that are NOT in this set.
# An order a strategy has CLAIMED — a GTT fill a monitor booked, a GTT order a
# forced exit adopted, a manual exit accepted as proof for one leg — is noted
# too, so the same order can never serve a second strategy.
# In memory, and appended to a small per-day file so a restart keeps today's.
_APP_ORDERS: set = set()
_APP_ORDERS_DAY: List[str] = [""]
_APP_ORDERS_LOCK = threading.Lock()


def _app_orders_file():
    import os
    import time as _t
    from pathlib import Path
    day = _t.strftime("%Y%m%d")
    if os.environ.get("SCALP_TEST_MODE") == "1":
        return day, None                      # tests: memory only
    return day, Path.home() / ".scalp-app" / "state" / f"app_orders_{day}.txt"


def _app_orders_roll() -> None:
    day, f = _app_orders_file()
    if _APP_ORDERS_DAY[0] == day:
        return
    _APP_ORDERS_DAY[0] = day
    _APP_ORDERS.clear()
    try:
        if f is not None and f.exists():
            _APP_ORDERS.update(x.strip() for x in f.read_text().splitlines() if x.strip())
    except Exception:
        pass


def note_app_order(order_id) -> None:
    """Called by the ExecutionRouter for every order it places. Never raises."""
    try:
        oid = order_id[0] if isinstance(order_id, (tuple, list)) else order_id
        if oid in (None, ""):
            return
        oid = str(oid)
        with _APP_ORDERS_LOCK:
            _app_orders_roll()
            if oid in _APP_ORDERS:
                return
            _APP_ORDERS.add(oid)
            _day, f = _app_orders_file()
            if f is not None:
                f.parent.mkdir(parents=True, exist_ok=True)
                with open(f, "a") as fh:
                    fh.write(oid + "\n")
    except Exception:
        pass


def claim_orders(order_ids) -> None:
    """Note order id(s) — a list, or the comma-joined form the managers keep."""
    try:
        if isinstance(order_ids, str):
            order_ids = order_ids.split(",")
        for oid in order_ids or []:
            if oid:
                note_app_order(str(oid).strip())
    except Exception:
        pass


def reset_app_orders() -> None:      # tests: a new fake broker reuses order ids
    with _APP_ORDERS_LOCK:
        _APP_ORDERS.clear()


def app_order_ids() -> set:
    """Ids of today's orders placed by this app (all strategies)."""
    try:
        with _APP_ORDERS_LOCK:
            _app_orders_roll()
            return set(_APP_ORDERS)
    except Exception:
        return set()


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

    def release(self, sid: str, legs: Optional[Dict[str, int]] = None) -> None:
        """── LEDGER_BOOT_20261115 ── with `legs`, only those contracts are
        released; another order sequence of the same strategy keeps its own
        reservation. Without `legs` everything is released (as before)."""
        with self._lock:
            if legs is None:
                self._reserved.pop(str(sid), None)
                return
            cur = self._reserved.get(str(sid))
            if not cur:
                return
            for s in legs:
                cur.pop(str(s), None)
            if not cur:
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
            # ── LEDGER_BOOT_20261115 ── …and so is a REGISTERED strategy whose
            # view shows nothing on this contract while its database row is
            # open: at start-up a manager registers before it has restored its
            # position (and a replaced manager object answers nothing). Until
            # the view shows the leg, the open row speaks for it. The cost is
            # a contract read as shared for a few seconds after a close —
            # the cautious side.
            if me is not None:
                for sid, q in (_db_holders().get(str(symbol)) or {}).items():
                    if sid == str(me) or sid in out or not q:
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


# ── GTT_KILL_LIST_20261115 ── GTTs the app could NOT confirm cancelled ──────
# A forced exit (EOD, kill, square-off) flattens the position even when the
# leg's GTT cannot be cancelled or verified — correct, the position matters
# more. But the GTT id was then dropped (Timberwolf) or left to a sweep that
# skips any contract another strategy still holds (Icarus). An armed GTT on a
# closed leg is the 12 Nov failure: it fires later and trades a flat — or
# someone else's — contract. Reproduced in test_adv_gtt_kill.py.
#   * every such id is kept here, on disk, until the broker CONFIRMS it gone;
#   * the owner retries (verified cancel) about twice a minute and at boot;
#   * while one is still armed the user is paged, and again every 10 minutes;
#   * if it FIRED before it could be cancelled the user is told to check Kite.
def _kill_dir():
    import os
    from pathlib import Path
    if os.environ.get("SCALP_TEST_MODE") == "1":       # never the real state dir
        import tempfile
        global _KILL_TMP
        try:
            return _KILL_TMP
        except NameError:
            _KILL_TMP = Path(tempfile.mkdtemp(prefix="gtt_kill_"))
            return _KILL_TMP
    return Path.home() / ".scalp-app" / "state"


def fired_gtt_orders(executor, gtt_id, *, symbol: Optional[str] = None,
                     short: Optional[bool] = None, qtys=None) -> Optional[List[str]]:
    """Order ids a TRIGGERED GTT placed, from a successful read of the GTT
    list; None when the list is unreadable or the GTT does not list them yet.
    A forced exit ADOPTS these orders (drives them to filled / cancelled)
    instead of placing a second order next to a limit that may still rest.

    With `symbol` (+ `short`, `qtys`): when the GTT is listed but has not
    named its order yet (a known broker lag), the order book is searched for
    a WORKING exit-side order on the contract, of one of the leg's
    quantities, that this app did not place — that is the GTT's resting
    limit."""
    rows = read_gtts(executor)
    if rows is None:
        return None
    row = next((g for g in rows if str(g.get("id")) == str(gtt_id)), None)
    if row is None:
        return None
    out = []
    for o in row.get("orders") or []:
        res = (o.get("result") or {})
        oid = ((res.get("order_result") or {}).get("order_id")) or res.get("order_id")
        if oid:
            out.append(str(oid))
    if out or not symbol or short is None:
        return out or None
    side = "BUY" if short else "SELL"
    want = {int(q) for q in (qtys or []) if q}
    ours = app_order_ids()
    try:
        book = executor.get_orders() or []
    except Exception:
        return None
    for o in book:
        try:
            if (str(o.get("tradingsymbol") or "") == str(symbol)
                    and str(o.get("transaction_type") or "").upper() == side
                    and str(o.get("status") or "").upper() not in
                    ("COMPLETE", "REJECTED", "CANCELLED", "LAPSED", "")
                    and str(o.get("order_id") or "") not in ours
                    and (not want or int(o.get("quantity") or 0) in want)):
                out.append(str(o.get("order_id")))
        except Exception:
            continue
    return out or None


def foreign_exit_fill(executor, symbol: str, short: bool, *, want_qty: int = 0,
                      not_before_ts=None):
    """(quantity, vwap) of COMPLETE exit-side orders on `symbol` that this app
    did NOT place (a fired GTT's order, a manual exit), earliest first, up to
    `want_qty`. (0, None) when there is none or the book cannot be read."""
    side = "BUY" if short else "SELL"
    try:
        orders = executor.get_orders() or []
    except Exception:
        return 0, None
    ts_of = None
    try:
        from app.execution import fill_pick as _fp
        ts_of = _fp.order_ts
    except Exception:
        ts_of = None
    ours = app_order_ids()
    got_q, got_v, used = 0, 0.0, []
    for o in orders:
        try:
            if (str(o.get("tradingsymbol") or "") != str(symbol)
                    or str(o.get("transaction_type") or "").upper() != side
                    or str(o.get("status") or "").upper() != "COMPLETE"
                    or str(o.get("order_id") or "") in ours):
                continue
            px = float(o.get("average_price") or 0)
            q = int(o.get("filled_quantity") or o.get("quantity") or 0)
            if px <= 0 or q <= 0:
                continue
            if not_before_ts and ts_of is not None:
                t = ts_of(o)
                if t is not None and t < int(not_before_ts) - 5:
                    continue
            take = q if not want_qty else min(q, int(want_qty) - got_q)
            if take <= 0:
                break
            got_q += take
            got_v += take * px
            used.append(str(o.get("order_id") or ""))
        except Exception:
            continue
    claim_orders(used)                        # counted once, for one strategy
    return (got_q, round(got_v / got_q, 4)) if got_q else (0, None)


class GttKillList:
    RETRY_GAP_S = 25.0
    REPAGE_S = 600.0

    def __init__(self, sid: str, path=None):
        self.sid = str(sid)
        self._lock = threading.RLock()
        self._path = path
        self._items: Dict[str, dict] = {}
        self._last = 0.0
        self._load()

    def _file(self):
        from pathlib import Path
        return Path(self._path) if self._path else _kill_dir() / f"{self.sid}_gtt_kill.json"

    def _load(self) -> None:
        try:
            import json
            f = self._file()
            if f.exists():
                self._items = {str(k): dict(v) for k, v in json.loads(f.read_text()).items()}
        except Exception:
            self._items = {}

    def _save(self) -> None:
        try:
            import json
            f = self._file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._items))
            tmp.replace(f)
        except Exception:
            pass

    def __len__(self) -> int:
        return len(self._items)

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._items)

    SPENT_MAX_TRIES = 20

    def add(self, gtt_id, symbol: str, why: str = "", *, spent: bool = False) -> None:
        """spent=True: the leg's OWN stop GTT that has already triggered. It
        is listed only so that an order it may have left RESTING (gap past
        the limit, id not listed at close time) is cancelled; a normal filled
        stop is dropped silently."""
        import time as _t
        with self._lock:
            if not gtt_id or str(gtt_id) in self._items:
                return
            self._items[str(gtt_id)] = {"symbol": str(symbol), "why": str(why)[:120],
                                        "since": _t.time(), "paged": 0.0, "tries": 0,
                                        "spent": bool(spent)}
            self._save()

    @staticmethod
    def _settle_fired(executor, gid, _log):
        """A listed GTT has fired. Cancel whatever its order still has resting.
        Returns (order ids STILL working, filled quantity or None if unknown)."""
        try:
            rows = read_gtts(executor)
            row = next((g for g in (rows or []) if str(g.get("id")) == str(gid)), None)
        except Exception:
            row = None
        if row is None:
            return [], None
        oids = []
        for o in row.get("orders") or []:
            res = (o.get("result") or {})
            oid = ((res.get("order_result") or {}).get("order_id")) or res.get("order_id")
            if oid:
                oids.append(str(oid))
        if not oids:
            return [], None
        resting, filled = [], 0
        for oid in oids:
            try:
                info = executor.get_order_fill(oid) or {}
                st = (info.get("status") or "").upper()
                if st and st not in ("COMPLETE", "REJECTED", "CANCELLED", "LAPSED"):
                    try:
                        executor.cancel_order(oid)
                    except Exception as e:
                        _log(f"[GTT_KILL] cancel of fired GTT order {oid} raised {e!r}")
                    info = executor.get_order_fill(oid) or {}
                    st = (info.get("status") or "").upper()
                if not st:
                    return [oid], None                    # unreadable: assume it rests
                if st not in ("COMPLETE", "REJECTED", "CANCELLED", "LAPSED"):
                    resting.append(oid)
                filled += int(info.get("filled_qty") or 0)
            except Exception:
                return [oid], None
        return resting, filled

    def retry(self, executor, *, log=None, page=None, force: bool = False) -> int:
        """One verified-cancel attempt per listed GTT. Returns how many are
        still on the list. Never raises."""
        import time as _t
        with self._lock:
            if not self._items or executor is None:
                return len(self._items)
            now = _t.time()
            if not force and now - self._last < self.RETRY_GAP_S:
                return len(self._items)
            self._last = now
            todo = list(self._items.items())

        def _log(m):
            if log is not None:
                try:
                    log(m)
                except Exception:
                    pass

        def _page(m, sev="error"):
            if page is not None:
                try:
                    page(m, sev)
                except Exception:
                    pass

        changed = False
        for gid, meta in todo:
            sym = meta.get("symbol", "")
            status = None
            try:
                fn = getattr(executor, "get_gtt_status", None)
                status = fn(gid) if callable(fn) else None
            except Exception:
                status = None
            if meta.get("spent"):
                # the leg's own fired stop: only a RESTING order matters
                n_before = None
                try:
                    ids = fired_gtt_orders(executor, gid)
                    n_before = [o for o in (ids or [])
                                if ((executor.get_order_fill(o) or {}).get("status") or "").upper()
                                not in ("COMPLETE", "REJECTED", "CANCELLED", "LAPSED", "")]
                except Exception:
                    ids, n_before = None, None
                resting, _dq = self._settle_fired(executor, gid, _log) if ids else ([], None)
                with self._lock:
                    cur = self._items.get(gid)
                    if cur is None:
                        continue
                    changed = True
                    if resting:
                        if now - float(cur.get("paged") or 0) >= self.REPAGE_S:
                            cur["paged"] = now
                            _page(f"{self.sid}: the stop GTT {gid} on {sym} triggered but its "
                                  f"order {', '.join(resting)} is still OPEN after the position "
                                  f"was closed, and could not be cancelled. CANCEL IT IN KITE "
                                  f"(Orders → Open) now.")
                        continue
                    if ids:
                        self._items.pop(gid, None)
                        if n_before:
                            _log(f"[GTT_KILL][{self.sid}] gtt={gid} on {sym}: resting order(s) "
                                 f"{n_before} of the fired stop cancelled after the close")
                            _page(f"{self.sid}: a resting order of the fired stop GTT on {sym} "
                                  f"was still open after the position closed — it is now "
                                  f"CANCELLED. Verify in Kite.", "warning")
                        continue
                    cur["tries"] = int(cur.get("tries") or 0) + 1
                    if cur["tries"] >= self.SPENT_MAX_TRIES:
                        self._items.pop(gid, None)
                        _log(f"[GTT_KILL][{self.sid}] gtt={gid} on {sym}: fired stop never "
                             f"listed its order — dropped")
                continue
            if status == "triggered":
                # It fired before it could be cancelled. Its order may still be
                # RESTING (a gap past the limit): cancel that, so it cannot
                # fill later; whatever already filled is an unintended trade.
                resting, done_q = self._settle_fired(executor, gid, _log)
                if resting:
                    with self._lock:
                        cur = self._items.get(gid)
                        if cur is not None and now - float(cur.get("paged") or 0) >= self.REPAGE_S:
                            cur["paged"] = now
                            _page(f"{self.sid}: GTT {gid} on {sym} — left armed after its "
                                  f"position was closed — has TRIGGERED and its order "
                                  f"{', '.join(resting)} is still OPEN and could not be "
                                  f"cancelled. CANCEL IT IN KITE (Orders → Open) now.")
                    changed = True
                    continue
                _log(f"[GTT_KILL][{self.sid}] gtt={gid} on {sym} FIRED before it could be "
                     f"cancelled (filled {done_q if done_q is not None else 'unknown'})")
                if done_q == 0:
                    _page(f"{self.sid}: GTT {gid} on {sym} — left armed after its position "
                          f"was closed — TRIGGERED, but its order did not fill and is now "
                          f"cancelled. No trade resulted; verify in Kite.", "warning")
                else:
                    _page(f"{self.sid}: GTT {gid} on {sym} — left armed after its position was "
                          f"closed — has TRIGGERED"
                          + (f" and filled {done_q}" if done_q else "")
                          + f". Check Kite Orders and Positions for an "
                            f"unintended trade on {sym} NOW.")
                with self._lock:
                    self._items.pop(gid, None)
                changed = True
                continue
            ok = False
            try:
                cv = getattr(executor, "cancel_gtt_verified", None)
                if callable(cv):
                    try:
                        ok = bool(cv(gid, retries=0))
                    except TypeError:
                        ok = bool(cv(gid))
            except Exception as e:
                _log(f"[GTT_KILL][{self.sid}] gtt={gid} cancel raised {e!r}")
            with self._lock:
                if ok:
                    self._items.pop(gid, None)
                    changed = True
                    _log(f"[GTT_KILL][{self.sid}] gtt={gid} on {sym} confirmed cancelled")
                    if meta.get("paged"):
                        _page(f"{self.sid}: GTT {gid} on {sym} is now CANCELLED "
                              f"(it had stayed armed after the position closed).", "warning")
                    continue
                cur = self._items.get(gid)
                if cur is None:
                    continue
                cur["tries"] = int(cur.get("tries") or 0) + 1
                changed = True
                if now - float(cur.get("paged") or 0) >= self.REPAGE_S:
                    cur["paged"] = now
                    _log(f"[GTT_KILL][{self.sid}] gtt={gid} on {sym} STILL ARMED on a closed leg")
                    _page(f"{self.sid}: GTT {gid} on {sym} is STILL ARMED although its "
                          f"position is closed, and could not be cancelled. DELETE IT IN "
                          f"KITE (GTT tab) now. The app keeps retrying.")
        if changed:
            with self._lock:
                self._save()
        return len(self._items)
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
    q = int(pos.get(str(symbol), 0))
    if q:                                   # ── FLAT_NEEDS_PROOF_20261115 ── an open
        _FLAT_SEEN.pop((str(me), str(symbol)), None)   # reading ends a run of flat ones
    return q


# ── FLAT_NEEDS_PROOF_20261115 ── "already flat" is a claim, not a fact ──────
# Every exit path skips its order when the broker shows the leg flat ("someone
# already closed it") and books the close. ONE positions answer decided that.
# A read that succeeds but is empty or stale (seen around login and the 05:00
# flush on 13/14 Nov, outside the session) would, inside the session, book a
# live short as closed with no order — after its GTT was already cancelled.
# Reproduced for Icarus, Timberwolf and Velvet in test_adv_flat_proof.py.
# A leg that reads flat WITHOUT any fill of our own is booked only when
#   * today's order book holds a COMPLETE exit-side order on the contract at
#     or after the leg's entry that this app did NOT place (a GTT, a manual
#     exit — real exits leave one; another strategy's exit does not count), or
#   * the position has read flat on FLAT_MIN_READS separate attempts spread
#     over at least FLAT_MIN_SPAN_S seconds (an exit on an earlier day leaves
#     no order today; a glitch does not last).
# Until then nothing is booked and NO order is sent (a buy on a truly flat
# leg would open a position): the leg stays open, the caller pages and retries.
FLAT_MIN_READS = 3
FLAT_MIN_SPAN_S = 120.0
FLAT_FORGET_S = 600.0
FLAT_UNPROVEN_WHY = ("Kite shows this leg FLAT but no exit order proves it — NOT booked, "
                     "no order sent. Check Kite Positions; the app re-checks and books "
                     "it only when the flat reading is confirmed")
_FLAT_SEEN: Dict[tuple, dict] = {}


def _exit_order_seen(executor, symbol: str, short: bool, not_before_ts=None) -> bool:
    side = "BUY" if short else "SELL"
    try:
        orders = executor.get_orders() or []
    except Exception:
        return False
    ts_of = None
    try:
        from app.execution import fill_pick as _fp
        ts_of = _fp.order_ts
    except Exception:
        ts_of = None
    ours = app_order_ids()
    for o in orders:
        try:
            if str(o.get("tradingsymbol") or "") != str(symbol):
                continue
            if str(o.get("order_id") or "") in ours:
                continue                      # an app order (any strategy) proves nothing here
            if str(o.get("transaction_type") or "").upper() != side:
                continue
            if str(o.get("status") or "").upper() != "COMPLETE":
                continue
            if float(o.get("average_price") or 0) <= 0:
                continue
            if not_before_ts and ts_of is not None:
                t = ts_of(o)
                if t is not None and t < int(not_before_ts) - 5:
                    continue
            claim_orders([o.get("order_id")])   # this exit proves ONE leg, once
            return True
        except Exception:
            continue
    return False


def flat_proven(executor, symbol: str, me: str, short: bool, *, not_before_ts=None,
                clock=None, log=None) -> bool:
    """Call when the broker reads `symbol` flat for `me` and none of its own
    orders filled. True = the close may be booked. Never raises."""
    import time as _t
    now = float((clock or _t.time)())
    key = (str(me), str(symbol))
    if _exit_order_seen(executor, symbol, short, not_before_ts):
        _FLAT_SEEN.pop(key, None)
        return True
    rec = _FLAT_SEEN.get(key)
    if rec is None or now - rec["last"] > FLAT_FORGET_S:
        rec = _FLAT_SEEN[key] = {"first": now, "last": now, "n": 0}
    rec["n"] += 1
    rec["last"] = now
    if rec["n"] >= FLAT_MIN_READS and now - rec["first"] >= FLAT_MIN_SPAN_S:
        _FLAT_SEEN.pop(key, None)
        if log is not None:
            try:
                log(f"[BROKER_TRUTH][FLAT] {me} {symbol}: flat on {rec['n']} reads over "
                    f"{now - rec['first']:.0f}s, no exit order today — accepted as closed "
                    f"(exit on an earlier day or outside the app)")
            except Exception:
                pass
        return True
    if log is not None:
        try:
            log(f"[BROKER_TRUTH][FLAT_UNPROVEN] {me} {symbol}: reads flat, no exit order "
                f"in today's book (read {rec['n']}/{FLAT_MIN_READS}) — NOT booked")
        except Exception:
            pass
    return False


def expected_net(symbol: str, me: str, own_qty: int) -> Optional[int]:
    """What the broker should show on `symbol`: this strategy's quantity plus
    every other strategy's. None = another strategy's holding is unknown."""
    o = LEDGER.others(symbol, me)
    return None if o is None else int(own_qty) + int(o)
