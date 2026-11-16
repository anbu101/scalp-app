"""
zerodha_executor.py
====================
Zerodha Order Executor with optional OCI relay.

Changes vs previous version:
  - get_order_fill()    : NEW - single-call {status, avg_price, found} read of
                          the order book, used by BBTradeManager._resolve_fill()
                          to distinguish "still pending" from "filled (COMPLETE)"
                          from "dead (REJECTED/CANCELLED/LAPSED)". This fixes
                          recorded entry price drifting from the true broker fill
                          on slow BANKNIFTY limit fills, and prevents phantom
                          GTT/DB rows on rejected orders.
  - get_last_avg_price() : UNCHANGED - still used by exit-side _poll_for_fill().

Earlier changes (retained):
  - place_sell_entry()  : SELL entry for SCALP_V1 short positions
  - place_buy_exit()    : BUY back to close a short position
  - place_gtt_oco()     : direction="LONG"|"SHORT" param (default "LONG")
      LONG  -> existing SELL GTT OCO logic (BB/HA unchanged)
      SHORT -> inverted BUY GTT OCO logic (SCALP_V1 short)

All READ operations (positions, orders, GTT status) are always direct.

backend/app/execution/zerodha_executor.py
"""

from typing import Optional, List, Dict
import time
import csv          # ── ORDER_LATENCY_20261111 ──
import functools    # ── ORDER_LATENCY_20261111 ──
import threading    # ── ORDER_LATENCY_20261111 ──
import json
import re      # ── RELAY_REJECT_PASSTHRU_20260915 ──
import requests
from pathlib import Path

from kiteconnect import KiteConnect

from app.execution.base_executor import BaseOrderExecutor
from app.config.trading_config import MAX_QTY_PER_ORDER
from app.brokers.zerodha_manager import ZerodhaManager
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log


# ── LVB_V1_20261023 ── F&O exchange from the tradingsymbol. BSE derivatives (SENSEX /
# BANKEX weekly options) trade on BFO; every other symbol this app has ever
# traded (NIFTY / BANKNIFTY …) stays on NFO — byte-identical orders for the
# whole existing fleet. Used ONLY by _get_lot_size, _resolve_ltp, place_buy
# and place_market_sell (the LVB_V1 order path); every GTT / sell-entry /
# basket path is untouched and stays NFO-only.
def _fo_exchange(symbol: str) -> str:
    s = str(symbol or "").upper()
    return "BFO" if s.startswith(("SENSEX", "BANKEX")) else "NFO"


# ── ORDER_LATENCY_20261111 BEGIN ─────────────────────────────────────
# 2026-11-11 09:20 VET_V1 LIVE: signal + wing pick done at 09:20:01, wing
# order sent 09:20:08, short 09:20:12 — the short slid 231.75 → 223.15
# meanwhile. _get_lot_size() downloaded the WHOLE NFO instrument list from
# Kite for every symbol this process had not traded yet, and cached only
# that one symbol — so every new strike (each leg, every morning, every
# exit after a restart) paid a multi-second download before its order.
#
# Now: lot sizes come from the instruments.csv the app already keeps in
# ~/.scalp-app/state (read once, in the background at startup); a Kite
# download happens only if a symbol is missing there or the local value
# fails the qty check (a lot-size revision), and then caches EVERY symbol
# of that exchange. The lot size here only VALIDATES qty — it never sets
# it — so a stale local value can at worst trigger that verification.
# Every order also logs where its time went: [ZERODHA][LAT].
_LOT_CACHE: Dict[str, int] = {}
_LOT_SRC: Dict[str, str] = {}            # symbol -> "csv" | "kite"
_LOT_LOCK = threading.Lock()
_LOT_CSV_DONE = threading.Event()
_LOT_CSV_PATH = Path.home() / ".scalp-app" / "state" / "instruments.csv"
_TL = threading.local()                  # per-thread order timing


def _load_lot_sizes_from_csv(path: Optional[Path] = None) -> int:
    """One pass over the local instrument dump (NFO + BFO rows only).
    Idempotent and thread-safe; never raises. Returns symbols added."""
    with _LOT_LOCK:
        if _LOT_CSV_DONE.is_set():
            return 0
        n, t0 = 0, time.time()
        p = path or _LOT_CSV_PATH
        try:
            with open(p, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("exchange") not in ("NFO", "BFO"):
                        continue
                    sym = row.get("tradingsymbol")
                    try:
                        lot = int(float(row.get("lot_size") or 0))
                    except (TypeError, ValueError):
                        continue
                    if sym and lot > 0 and sym not in _LOT_CACHE:
                        _LOT_CACHE[sym] = lot
                        _LOT_SRC[sym] = "csv"
                        n += 1
            write_audit_log(f"[ZERODHA][LOT_SIZE] {n} F&O lot sizes loaded from "
                            f"{p.name} in {(time.time() - t0) * 1000:.0f} ms")
        except FileNotFoundError:
            write_audit_log(f"[ZERODHA][LOT_SIZE] {p} not found — first order per "
                            f"exchange will download the list from Kite")
        except Exception as e:
            write_audit_log(f"[ZERODHA][LOT_SIZE] local list unreadable ({e!r}) — "
                            f"Kite download on demand")
        finally:
            _LOT_CSV_DONE.set()
        return n


def _order_timed(fn):
    """Marks the start of an order-placing method for [ZERODHA][LAT]."""
    @functools.wraps(fn)
    def _w(self, *a, **k):
        _TL.t0, _TL.lot_ms = time.time(), None
        try:
            return fn(self, *a, **k)
        finally:
            _TL.t0 = None
    return _w
# ── ORDER_LATENCY_20261111 END ───────────────────────────────────────


# --------------------------------------------------
# RELAY CONFIG
# --------------------------------------------------

_RELAY_CONFIG_PATH = Path.home() / ".scalp-app" / "relay_config.json"
_relay_cfg: Optional[dict] = None


def _load_relay_config() -> Optional[dict]:
    global _relay_cfg
    if _relay_cfg is not None:
        return _relay_cfg

    if not _RELAY_CONFIG_PATH.exists():
        return None

    try:
        cfg = json.loads(_RELAY_CONFIG_PATH.read_text())

        if not cfg.get("enabled"):
            return None

        if "relays" not in cfg and cfg.get("url"):
            cfg["relays"] = [{
                "url":          cfg["url"],
                "host":         cfg.get("host", cfg["url"]),
                "secret":       cfg.get("secret", ""),
                "ssh_username": cfg.get("ssh_username"),
                "ssh_key":      cfg.get("ssh_key"),
                "instance_id":  cfg.get("instance_id"),
                "is_primary":   True,
            }]
            write_audit_log("[RELAY] Migrated single-relay config to multi-relay format")

        if cfg.get("relays"):
            _relay_cfg = cfg
            hosts = [r.get("host") for r in cfg["relays"]]
            write_audit_log(f"[RELAY] Order relay ENABLED -> {hosts}")
        else:
            write_audit_log("[RELAY] relay_config.json found but no relay entries")

    except Exception as e:
        write_audit_log(f"[RELAY] Failed to load relay_config.json ERR={e}")

    return _relay_cfg


def relay_is_active() -> bool:
    return _load_relay_config() is not None


def _invalidate_relay_cache():
    global _relay_cfg
    _relay_cfg = None
    write_audit_log("[RELAY] Config cache invalidated")


# --------------------------------------------------
# RELAY HTTP CLIENT
# --------------------------------------------------

class RelayClient:
    TIMEOUT = 10

    def __init__(self, url: str, secret: str, api_key: str, access_token: str):
        self.base_url     = url.rstrip("/")
        self.secret       = secret
        self.api_key      = api_key
        self.access_token = access_token
        self._headers     = {"Authorization": f"Bearer {secret}"}

    def _post(self, path: str, payload: dict) -> dict:
        payload["api_key"]      = self.api_key
        payload["access_token"] = self.access_token
        resp = requests.post(
            f"{self.base_url}{path}", json=payload,
            headers=self._headers, timeout=self.TIMEOUT,
        )
        if not resp.ok:
            _raise_relay_error(path, resp)   # ── RELAY_REJECT_PASSTHRU_20260915 ──
        return resp.json()

    def place_order(self, **kwargs) -> str:
        result = self._post("/relay/place_order", kwargs)
        return str(result["order_id"])

    def place_gtt(self, **kwargs) -> dict:
        return self._post("/relay/place_gtt", kwargs)

    def cancel_gtt(self, trigger_id: int):
        resp = requests.delete(
            f"{self.base_url}/relay/gtt/{trigger_id}",
            params={"api_key": self.api_key, "access_token": self.access_token},
            headers=self._headers, timeout=self.TIMEOUT,
        )
        if not resp.ok:
            _raise_relay_error(f"/relay/gtt/{trigger_id}", resp)   # ── RELAY_REJECT_PASSTHRU_20260915 ──

    def cancel_order(self, variety: str, order_id: str):
        self._post("/relay/cancel_order", {"variety": variety, "order_id": order_id})

    # ── TSG_ENTRY_REPEG ── order modify (D1). Modification is an
    # order-management action under the SEBI static-IP framework, so it
    # routes through the relay exactly like placement. Requires the relay
    # servers to be REDEPLOYED with the matching /relay/modify_order
    # endpoint; an old relay 404s, _relay_call falls to the next relay and
    # finally to direct (same degradation contract as every other op).
    def modify_order(self, **kwargs) -> str:
        result = self._post("/relay/modify_order", kwargs)
        return str(result.get("order_id", kwargs.get("order_id", "")))


_RELAY_TRANSIENT_ERRORS = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)


# ── ORDER_ONCE_20261115 ── helpers (see _relay_call_inner) ──────────────────
_NOT_SENT_MARKS = ("NewConnectionError", "Connection refused", "Name or service not known",
                   "nodename nor servname", "No route to host", "Network is unreachable",
                   "getaddrinfo failed", "Failed to establish a new connection")
_TAG_N = [0]


def _tag_params(params: dict) -> Optional[str]:
    """Give an order a unique tag (Kite: alphanumeric, max 20) and return it.
    An order that already carries a caller's tag keeps it and returns None:
    such a tag is not unique, so it cannot be used to look the order up."""
    tag = params.get("tag")
    if tag:
        return None
    # ── INSTANCE_GUARD_20261115 ── "ST" + this machine's id: an order of a
    # second Scalp Terminal on the same account is recognisable in the book.
    # ── EXIT_INTENT_20261115 ── a tag the strategy already wrote down (so it
    # can find this order again after a restart) is used as is.
    try:
        from app.execution import instance_guard as _IG
        tag = _IG.take_tag_hint() or _IG.new_order_tag()
    except Exception:
        _TAG_N[0] = (_TAG_N[0] + 1) % 1296
        tag = f"ST{int(time.time() * 1000) % 10**12}{_TAG_N[0]:04d}"[:20]
    params["tag"] = tag
    return tag


def _maybe_sent(e, direct: bool = False) -> bool:
    """Could the request have reached the broker? False only when it certainly
    did not: the connection was never made, or the relay itself refused (4xx)."""
    try:
        if isinstance(e, requests.exceptions.ConnectTimeout):
            return False
        text = repr(e)
        if any(m in text for m in _NOT_SENT_MARKS):
            return False
        if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        if direct:
            # kiteconnect: NetworkException / DataException = no usable answer;
            # every other Kite exception is the broker saying no.
            return type(e).__name__ in ("NetworkException", "DataException", "Timeout",
                                        "ReadTimeout", "ConnectionError", "TimeoutError")
        m = re.search(r"failed HTTP (\d{3})", str(e))
        if m:
            return int(m.group(1)) >= 500          # the relay answered: 4xx = refused, 5xx = unclear
        return True                                # e.g. an unreadable answer body
    except Exception:
        return False


def _once_kw(tag, find) -> dict:
    """Only pass tag/find when set, so older 4-argument hooks keep working."""
    kw = {}
    if tag is not None:
        kw["tag"] = tag
    if find is not None:
        kw["find"] = find
    return kw


def _start_bg(fn, name: str):
    threading.Thread(target=fn, daemon=True, name=name).start()


# ── RELAY_REJECT_PASSTHRU_20260915 BEGIN ─────────────────────────────────
# 2026-09-15 09:16: an "Insufficient funds" rejection relayed back as HTTP
# 500 was treated as "relay failed", retried on the second relay, then fell
# through to a DIRECT call that could only ever answer PermissionException
# (SEBI static-IP whitelist). The phone got the IP error; the margin reason
# never left the audit log.
#
#   RelayBrokerRejection  — the BROKER answered through the relay (Kite
#                           exception). Terminal: no next relay, no direct.
#   RelayUnavailableError — every relay was unreachable / faulted. Carries
#                           each relay's error text so an old (not yet
#                           redeployed) relay's 500 body still surfaces.
#
# Direct fallback for writes is OFF when relays are configured (every op
# behind _relay_call is order management and is IP-gated). Escape hatch for
# a machine whose own IP is whitelisted: relay_config.json
# "allow_direct_fallback": true.

class RelayBrokerRejection(RuntimeError):
    """Kite rejected the request; message is Kite's own text."""
    def __init__(self, kite_exception: str, message: str, host: str = ""):
        self.kite_exception = kite_exception
        self.host = host
        super().__init__(f"{kite_exception}: {message}" if kite_exception else message)


class RelayUnavailableError(RuntimeError):
    """All configured relays failed; message lists each relay's error."""


_LEARN_MORE_RE = re.compile(r"\s*Learn more\s*-\s*https?://\S*", re.I)


def _clean_broker_text(msg: str, limit: int = 300) -> str:
    s = _LEARN_MORE_RE.sub("", " ".join(str(msg or "").split())).strip(" .·-")
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _raise_relay_error(path: str, resp) -> None:
    """Non-2xx relay answer → the right exception class."""
    body = None
    try:
        body = resp.json()
    except Exception:
        pass
    if isinstance(body, dict) and body.get("kite_exception") and resp.status_code in (422, 502):
        raise RelayBrokerRejection(str(body["kite_exception"]),
                                   _clean_broker_text(body.get("error", "")))
    raise RuntimeError(
        f"Relay {path} failed HTTP {resp.status_code}: {resp.text[:200]}"
    )
# ── RELAY_REJECT_PASSTHRU_20260915 END ───────────────────────────────────


# --------------------------------------------------
# EXECUTOR
# --------------------------------------------------

class TradingDisabledError(RuntimeError):
    pass


class ZerodhaOrderExecutor(BaseOrderExecutor):

    def __init__(self, broker_manager: ZerodhaManager):
        self.broker_manager    = broker_manager
        self._instrument_cache: Dict[str, int] = {}
        # ── ORDER_LATENCY_20261111 ── warm the lot-size map off the boot path
        if not _LOT_CSV_DONE.is_set():
            threading.Thread(target=_load_lot_sizes_from_csv, daemon=True,
                             name="lot-size-warm").start()

    # ── relay helpers ─────────────────────────────────

    def _relay(self) -> Optional[RelayClient]:
        return None  # multi-relay routing handled inside _relay_call

    # ── SLIP_CAPTURE_20261029 ── every market/limit order the app sends gets a
    # reference price: the LTP the app held for the symbol just before sending.
    # The stamp is written AFTER the call returns, on its own thread (the
    # order is never delayed); any stamping error is swallowed.
    def _relay_call(self, relay_fn, direct_fn, op_name: str, symbol: str = "",
                    tag: Optional[str] = None, find=None):
        # ── ORDER_LATENCY_20261111 ── one line per order: where the time went
        t_send = time.time()
        ok = False
        try:
            out = self._relay_call_slip(relay_fn, direct_fn, op_name, symbol, tag, find)
            ok = True
            return out
        finally:
            t0 = getattr(_TL, "t0", None)
            if t0:
                try:
                    lot = getattr(_TL, "lot_ms", None)
                    pre = (t_send - t0) * 1000
                    write_audit_log(
                        f"[ZERODHA][LAT] {op_name} {symbol} "
                        + (f"lot={lot:.0f}ms " if lot is not None else "")
                        + f"price={pre - (lot or 0):.0f}ms "
                        f"send={(time.time() - t_send) * 1000:.0f}ms "
                        f"total={(time.time() - t0) * 1000:.0f}ms"
                        + ("" if ok else " FAILED"))
                except Exception:
                    pass

    def _relay_call_slip(self, relay_fn, direct_fn, op_name: str, symbol: str = "",
                         tag: Optional[str] = None, find=None):
        try:
            from app.slippage.slip_store import SIDE_OF as _SLIP_SIDE
        except Exception:
            _SLIP_SIDE = {}
        if op_name not in _SLIP_SIDE:
            return self._relay_call_inner(relay_fn, direct_fn, op_name, symbol, **_once_kw(tag, find))
        try:
            ref = LTPStore.get_with_timestamp(symbol)
        except Exception:
            ref = None
        t0, oid, err = time.time(), None, None
        try:
            oid = self._relay_call_inner(relay_fn, direct_fn, op_name, symbol, **_once_kw(tag, find))
            return oid
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
            raise
        finally:
            try:
                from app.slippage.slip_store import record_send
                record_send(op_name, symbol, ref, t0, time.time(), oid, err)
            except Exception:
                pass

    # ── ORDER_ONCE_20261115 BEGIN ──────────────────────────────────────────
    # WHAT WAS WRONG (fleet assurance, 15 Nov 2026 — test_order_once.py):
    # an order or GTT whose answer was LOST (read timeout, connection dropped
    # after the request left, a 5xx from the relay) was treated as "relay
    # failed": the same order was sent again through the next relay, or the
    # call raised and the strategy placed it again a minute later. The first
    # one was often already at the broker → two buy-backs (the account ends
    # long), two short entries (one of them with no stop), two GTTs for one
    # leg. Nothing could tell them apart: orders carried no identifier.
    # NOW
    #   * every order carries a unique tag (Kite `tag`; the relay forwards it);
    #   * an UNCLEAR failure — the request may have left — is looked up at the
    #     broker (by tag; a GTT by symbol/trigger/quantity) for ~2 s BEFORE
    #     anything is sent again. Found → that order IS the result;
    #   * not found → it goes on exactly as before (next relay / raise), and a
    #     background watch keeps looking for 2 minutes: an order that shows up
    #     late is cancelled if it is still working, and paged either way;
    #   * a CLEAR failure (could not connect, relay refused with 4xx, the
    #     broker rejected) behaves exactly as before.
    # Cost: nothing on a normal order (the tag is a field). The lookups only
    # run after a failure.
    _AMBIG_GAPS = (0.3, 0.7, 1.0)       # lookups after an unclear failure (~2 s)
    _LATE_TRIES, _LATE_GAP = 24, 5.0    # background watch: 2 minutes

    def _find_orders_by_tag(self, tag: str) -> List[str]:
        """Ids of orders in today's book carrying `tag`. [] if none / unreadable."""
        try:
            kite = self._kite()
            if not kite or not tag:
                return []
            out = []
            for o in kite.orders() or []:
                if o.get("tag") == tag or tag in (o.get("tags") or []):
                    out.append(str(o.get("order_id")))
            return out
        except Exception:
            return []

    def _find_gtts_like(self, gtt_params: dict) -> List[str]:
        """Ids of ACTIVE GTTs with the same contract, trigger(s) and quantity."""
        try:
            want_q = [int(o.get("quantity") or 0) for o in gtt_params.get("orders") or []]
            want_t = [round(float(t), 2) for t in gtt_params.get("trigger_values") or []]
            out = []
            for g in self.get_gtts_or_none() or []:
                cond = g.get("condition") or {}
                if str(g.get("status") or "").lower() != "active":
                    continue
                if cond.get("tradingsymbol") != gtt_params.get("tradingsymbol"):
                    continue
                if [round(float(t), 2) for t in cond.get("trigger_values") or []] != want_t:
                    continue
                if [int(o.get("quantity") or 0) for o in g.get("orders") or []] != want_q:
                    continue
                out.append(str(g.get("id")))
            return out
        except Exception:
            return []

    def _find_after_unclear(self, find, op_name: str, symbol: str, where: str, err) -> Optional[str]:
        write_audit_log(f"[ORDER_ONCE][{op_name}] {symbol} via {where}: outcome UNCLEAR "
                        f"({type(err).__name__}) — looking it up at the broker before resending")
        for gap in self._AMBIG_GAPS:
            time.sleep(gap)
            try:
                got = find() or []
            except Exception:
                got = []
            if got:
                write_audit_log(f"[ORDER_ONCE][{op_name}] {symbol}: it IS at the broker "
                                f"(id={got[0]}) — not sent again")
                return str(got[0])
        write_audit_log(f"[ORDER_ONCE][{op_name}] {symbol}: not at the broker after "
                        f"{sum(self._AMBIG_GAPS):.1f}s — treated as failed; watching for a late arrival")
        return None

    def _watch_late(self, find, op_name: str, symbol: str, resent_id: Optional[str]):
        """Background: did the order we gave up on arrive after all?"""
        def _run():
            for _ in range(self._LATE_TRIES):
                time.sleep(self._LATE_GAP)
                try:
                    late = [x for x in (find() or []) if str(x) != str(resent_id or "")]
                except Exception:
                    late = []
                if late:
                    self._settle_late(late[0], op_name, symbol, resent_id)
                    return
        try:
            _start_bg(_run, "order-once-watch")
        except Exception as e:
            write_audit_log(f"[ORDER_ONCE][WATCH_ERR] {e!r}")

    def _settle_late(self, late_id: str, op_name: str, symbol: str, resent_id: Optional[str]):
        what = "GTT" if op_name.startswith("GTT") else "order"
        why = (f"it was sent twice (the second is {resent_id})" if resent_id
               else "the app treated it as failed and is not tracking it")
        cancelled = False
        try:
            if what == "GTT":
                self.cancel_gtt(late_id)
                cancelled = True
            else:
                info = self.get_order_fill(late_id) or {}
                st = str(info.get("status") or "").upper()
                if st and st not in ("COMPLETE", "REJECTED", "CANCELLED", "LAPSED") \
                        and not int(info.get("filled_qty") or 0):
                    self.cancel_order(late_id)
                    cancelled = True
                elif st in ("REJECTED", "CANCELLED", "LAPSED") and not int(info.get("filled_qty") or 0):
                    write_audit_log(f"[ORDER_ONCE][{op_name}] {symbol}: late order {late_id} is {st} — nothing to do")
                    return
        except Exception as e:
            write_audit_log(f"[ORDER_ONCE][{op_name}] {symbol}: could not cancel late {what} {late_id}: {e!r}")
        msg = (f"{op_name} {symbol}: a {what} arrived at Zerodha LATE (id {late_id}) — {why}. "
               + (f"It has been CANCELLED. Verify in Kite." if cancelled else
                  f"It could NOT be cancelled (it may have filled). CHECK KITE Positions / "
                  f"Orders{' / GTT' if what == 'GTT' else ''} NOW."))
        write_audit_log(f"[ORDER_ONCE][LATE] {msg}")
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"message": msg, "severity": "warning" if cancelled else "error"})
        except Exception:
            pass

    def _direct_once(self, direct_fn, find, op_name: str, symbol: str):
        """The direct (no relay) call, with the same lookup after an unclear failure."""
        try:
            return direct_fn()
        except Exception as e:
            if find is None or not _maybe_sent(e, direct=True):
                raise
            got = self._find_after_unclear(find, op_name, symbol, "direct", e)
            if got:
                return got
            self._watch_late(find, op_name, symbol, None)
            raise
    # ── ORDER_ONCE_20261115 END ────────────────────────────────────────────

    def _relay_call_inner(self, relay_fn, direct_fn, op_name: str, symbol: str = "",
                          tag: Optional[str] = None, find=None):
        if find is None and tag:                       # ── ORDER_ONCE_20261115 ──
            find = lambda: self._find_orders_by_tag(tag)   # noqa: E731
        cfg = _load_relay_config()
        if not cfg or not cfg.get("relays"):
            return self._direct_once(direct_fn, find, op_name, symbol)

        kite = self._kite()
        if not kite:
            return direct_fn()
        unclear = False                                # ── ORDER_ONCE_20261115 ──

        relays   = cfg["relays"]
        ordered  = [r for r in relays if r.get("is_primary")] + \
                   [r for r in relays if not r.get("is_primary")]
        errors: List[str] = []     # ── RELAY_REJECT_PASSTHRU_20260915 ──

        for relay_entry in ordered:
            host   = relay_entry.get("host", relay_entry.get("url", ""))
            url    = relay_entry.get("url", "")
            secret = relay_entry.get("secret", "")

            if not url or not secret:
                write_audit_log(f"[RELAY][{op_name}] Skipping {host} - missing url/secret")
                continue

            try:
                relay  = RelayClient(url=url, secret=secret,
                                     api_key=kite.api_key,
                                     access_token=kite.access_token)
                result = relay_fn(relay)
                write_audit_log(f"[RELAY][{op_name}] {symbol} - SUCCESS via {host}")
                if unclear:                            # ── ORDER_ONCE_20261115 ── sent twice?
                    self._watch_late(find, op_name, symbol, str(result))
                return result

            except RelayBrokerRejection as e:
                # ── RELAY_REJECT_PASSTHRU_20260915 ── the broker answered.
                # Terminal: no next relay, no direct. Re-asking a rejection
                # is pointless; re-asking after an ambiguous accept is a
                # duplicate.
                e.host = host
                write_audit_log(
                    f"[RELAY][{op_name}][BROKER_REJECT] {symbol} via {host}: {e}"
                )
                raise
            except _RELAY_TRANSIENT_ERRORS as e:
                # ── ORDER_ONCE_20261115 ── the request may have left: look first
                if find is not None and _maybe_sent(e):
                    got = self._find_after_unclear(find, op_name, symbol, host, e)
                    if got:
                        return got
                    unclear = True
                write_audit_log(
                    f"[RELAY][{op_name}] {host} unreachable ({type(e).__name__}) - next relay"
                )
                errors.append(f"[{host}] unreachable ({type(e).__name__})")
                continue
            except Exception as e:
                if find is not None and _maybe_sent(e):    # ── ORDER_ONCE_20261115 ──
                    got = self._find_after_unclear(find, op_name, symbol, host, e)
                    if got:
                        return got
                    unclear = True
                write_audit_log(f"[RELAY][{op_name}] {host} error: {e} - next relay")
                errors.append(f"[{host}] {_clean_broker_text(e, 160)}")
                continue

        # ── RELAY_REJECT_PASSTHRU_20260915 ── all relays failed. Writes are
        # IP-gated: a direct call from a desktop can only answer
        # PermissionException and would MASK the reasons collected above.
        if not cfg.get("allow_direct_fallback", False):
            if unclear:                                # ── ORDER_ONCE_20261115 ──
                self._watch_late(find, op_name, symbol, None)
            write_audit_log(
                f"[RELAY][{op_name}][ALL_FAILED] {symbol} - NO direct fallback "
                f"(writes are IP-gated); errors: {' | '.join(errors) or 'none'}"
            )
            raise RelayUnavailableError(
                f"all {len(ordered)} relay(s) failed — "
                + ("; ".join(errors) if errors else "no relay had url/secret")
            )

        write_audit_log(
            f"[RELAY][{op_name}][ALL_FAILED] {symbol} - direct fallback "
            f"(allow_direct_fallback=true)"
        )
        try:
            result = direct_fn()
        except Exception:
            if unclear:                                # ── ORDER_ONCE_20261115 ──
                self._watch_late(find, op_name, symbol, None)
            raise
        write_audit_log(f"[RELAY][{op_name}][DIRECT_OK] {symbol}")
        if unclear:                                    # ── ORDER_ONCE_20261115 ── sent twice?
            self._watch_late(find, op_name, symbol, str(result))
        return result

    # ── internal helpers ──────────────────────────────

    def get_gtts(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        try:
            return kite.get_gtts()
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] GTT fetch failed ERR={e}")
            return []

    # ── GTT_FETCH_STRICT BEGIN ────────────────────────────────────
    def get_gtts_or_none(self) -> Optional[List[Dict]]:
        """
        STRICT variant of get_gtts(): returns None when the broker GTT list
        could not actually be read (session not ready, or the fetch raised),
        and a real list ([] included) ONLY on a successful read.

        get_gtts() returns [] on failure, which reconcile logic cannot
        distinguish from "no GTTs exist at the broker" — under a reconcile
        that treats a missing GTT as "it probably fired", that ambiguity is
        unsafe (2026-07-13 HA_V1 incident). New reconcile paths use this;
        existing get_gtts() consumers (BB/V2/V3/V4/IC) are untouched.
        """
        kite = self._kite()
        if not kite:
            write_audit_log(
                "[ZERODHA][WARN] STRICT GTT fetch skipped — session not ready"
            )
            return None
        try:
            return kite.get_gtts()
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] STRICT GTT fetch failed ERR={e}")
            return None
    # ── GTT_FETCH_STRICT END ──────────────────────────────────────

    def _kite(self) -> Optional[KiteConnect]:
        if not self.broker_manager.is_trade_ready():
            write_audit_log("[ZERODHA_EXECUTOR] Not ready - refreshing from disk")
            self.broker_manager.refresh()
        if not self.broker_manager.is_trade_ready():
            return None
        return self.broker_manager.get_trade_kite()

    def _ensure_trading_enabled(self):
        from app.config.global_loader import load_global_config
        if not load_global_config().get("trade_on", False):
            raise TradingDisabledError("TRADING_DISABLED (executor gate)")

    def resolve_symbol(self, symbol: str) -> str:
        return symbol

    def _get_lot_size(self, kite: KiteConnect, symbol: str, qty=None) -> int:
        # ── ORDER_LATENCY_20261111 ── memory → local list → Kite (rarely)
        t0 = time.time()
        try:
            v = self._instrument_cache.get(symbol) or _LOT_CACHE.get(symbol)
            if v is None:
                if not _LOT_CSV_DONE.is_set():
                    _load_lot_sizes_from_csv()      # waits for the warm-up if running
                v = _LOT_CACHE.get(symbol)
            if (v is not None and qty is not None and _LOT_SRC.get(symbol) == "csv"
                    and int(qty) % int(v) != 0):
                write_audit_log(f"[ZERODHA][LOT_SIZE] {symbol}: qty {qty} is not a "
                                f"multiple of the local lot {v} — verifying with Kite")
                v = None
            if v is None:
                v = self._lot_size_from_kite(kite, symbol)
            self._instrument_cache[symbol] = int(v)
            return int(v)
        finally:
            _TL.lot_ms = (time.time() - t0) * 1000

    def _lot_size_from_kite(self, kite: KiteConnect, symbol: str) -> int:
        """The old download — now caches every symbol of the exchange."""
        exch = _fo_exchange(symbol)   # ── LVB_V1_20261023 ──
        t0 = time.time()
        try:
            instruments = kite.instruments(exch)
        except Exception as e:
            raise RuntimeError(f"INSTRUMENT_FETCH_FAILED SYMBOL={symbol} ERR={e}")
        n = 0
        for inst in instruments or []:
            s_ = inst.get("tradingsymbol")
            try:
                lot = int(inst.get("lot_size") or 0)
            except (TypeError, ValueError):
                continue
            if s_ and lot > 0:
                _LOT_CACHE[s_] = lot
                _LOT_SRC[s_] = "kite"
                n += 1
        write_audit_log(f"[ZERODHA][LOT_SIZE] {exch} list downloaded for {symbol} in "
                        f"{(time.time() - t0) * 1000:.0f} ms — {n} symbols cached")
        lot_size = _LOT_CACHE.get(symbol) if _LOT_SRC.get(symbol) == "kite" else None
        if lot_size:
            return int(lot_size)
        raise RuntimeError(f"LOT_SIZE_NOT_FOUND SYMBOL={symbol}")

    @staticmethod
    def _protected_limit_price(ltp: float, side: str) -> float:
        """SEBI protection: LIMIT 1% away from LTP, rounded to NFO tick 0.05."""
        raw = ltp * 1.01 if side == "BUY" else ltp * 0.99
        return round(round(raw / 0.05) * 0.05, 2)

    # ── TSG_ENTRY_REPEG ── D2/D3 entry pricing ────────
    #
    # WHY (2026-08-10 TSG L1 incident): a SELL entry priced at LTP−1% went
    # unfilled for 22s at the open — LTP was already stale by the time the
    # order hit the book (24650CE, ltp 75.75 → limit 75.00, best bid had
    # dropped below 75). Two fixes, both scoped to ENTRY pricing only:
    #   D2: price sells off BEST BID (buys off BEST ASK) from quote depth —
    #       the touch is live truth; LTP is the last trade, seconds stale
    #       in minute one. LTP remains the fallback when depth is empty.
    #   D3: tiered buffer — 1% is generous on a ₹4 hedge and razor-thin on
    #       a ₹75 short. Premiums >= HIGH_PREMIUM_THRESHOLD get 2%.
    # _protected_limit_price is deliberately UNTOUCHED: exits (place_buy_exit)
    # and every other caller keep today's exact behaviour.

    HIGH_PREMIUM_THRESHOLD = 25.0   # ₹; >= this → wide buffer
    ENTRY_BUFFER_LOW  = 0.01        # 1% for cheap legs (wings)
    ENTRY_BUFFER_HIGH = 0.02        # 2% for rich legs (shorts at open)

    @classmethod
    def _entry_limit_price(cls, ref: float, side: str) -> float:
        """Marketable ENTRY limit from a reference price (best bid for SELL,
        best ask for BUY; LTP fallback), tiered buffer, NFO tick 0.05."""
        buf = (cls.ENTRY_BUFFER_HIGH if ref >= cls.HIGH_PREMIUM_THRESHOLD
               else cls.ENTRY_BUFFER_LOW)
        raw = ref * (1.0 + buf) if side == "BUY" else ref * (1.0 - buf)
        return round(round(raw / 0.05) * 0.05, 2)

    def _resolve_entry_quote(self, symbol: str) -> dict:
        """Full-quote fetch for entry pricing: {"ltp", "bid", "ask"} —
        bid/ask are the best-depth touch, 0.0 when depth is unavailable
        (callers fall back to ltp). Never raises; degraded reads return
        whatever is available (fail-open to the old LTP-only behaviour)."""
        out = {"ltp": 0.0, "bid": 0.0, "ask": 0.0}
        try:
            data_kite = self.broker_manager.get_data_kite()
            if data_kite:
                q = (data_kite.quote(f"NFO:{symbol}") or {}).get(
                    f"NFO:{symbol}", {})
                out["ltp"] = float(q.get("last_price") or 0.0)
                depth = q.get("depth") or {}
                buys  = depth.get("buy") or []
                sells = depth.get("sell") or []
                if buys and float(buys[0].get("price") or 0) > 0:
                    out["bid"] = float(buys[0]["price"])
                if sells and float(sells[0].get("price") or 0) > 0:
                    out["ask"] = float(sells[0]["price"])
        except Exception as e:
            write_audit_log(f"[ZERODHA][ENTRY_QUOTE] depth fetch failed "
                            f"for {symbol}: {e}")
        if out["ltp"] <= 0:
            fallback = self._resolve_ltp(symbol)
            out["ltp"] = float(fallback or 0.0)
        return out

    # ── WING_FAST_FILL_20261111 BEGIN ── hedge-wing BUY pricing.
    # A wing is protection: the seconds the short waits for it cost more
    # than a rupee on the wing. Old: LTP + 1% (₹5 → 5.05, one tick) — off a
    # last trade that can be seconds old, often BELOW the ask, so the order
    # rested while the short waited (IC/TMA2: up to 45 s, no re-price).
    # New: best ask + max(₹1, 20%), capped at ₹3 above the ask. A limit
    # above the ask still fills at the resting offer — the extra is only a
    # ceiling (it matters only if the book is thinner than the qty).
    # NSE limit price protection for index options <= ₹50 is ±₹20 of the
    # reference price, so a ≤ ₹3 buffer stays far inside it. Used ONLY by
    # the hedge-wing callers (TSG/IC/TMA2/VET); option-BUYING strategies keep
    # place_buy's pricing.
    WING_BUFFER_MIN = 1.00
    WING_BUFFER_PCT = 0.20
    WING_BUFFER_MAX = 3.00

    @classmethod
    def _wing_limit_price(cls, ref: float) -> float:
        buf = min(max(cls.WING_BUFFER_MIN, ref * cls.WING_BUFFER_PCT),
                  cls.WING_BUFFER_MAX)
        ticks = -(-round((ref + buf) * 100) // 5)          # ceil to the 0.05 tick
        return round(ticks * 0.05, 2)

    def fresh_wing_buy_limit(self, symbol: str):
        """Re-peg price for a working wing BUY: (limit, ref, src) or None."""
        q = self._resolve_entry_quote(symbol)
        ref = q["ask"] if q["ask"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._wing_limit_price(ref), ref, "ask" if q["ask"] > 0 else "ltp")

    @_order_timed   # ── ORDER_LATENCY_20261111 ──
    def place_wing_buy(self, symbol: str, token: int, qty: int):
        """Hedge-wing BUY entry. Returns (order_id, limit_price, qty)."""
        self._ensure_trading_enabled()
        if qty <= 0 or qty > MAX_QTY_PER_ORDER:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")
        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY SYMBOL={symbol}")
        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}")

        q = self._resolve_entry_quote(symbol)
        ref, src = (q["ask"], "ask") if q["ask"] > 0 else (q["ltp"], "ltp")
        if not ref or ref <= 0:
            ref, src = (LTPStore.get(symbol) or 0.0), "store"
        if not ref or ref <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price = self._wing_limit_price(float(ref))
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=_fo_exchange(symbol),
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )
        write_audit_log(f"[ZERODHA-WING-BUY] {symbol} qty={qty} ltp={q['ltp']} "
                        f"ask={q['ask']} ref={ref}({src}) limit={limit_price}")
        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="BUY", symbol=symbol,
            tag=_tag_params(order_params),   # ── ORDER_ONCE_20261115 ──
        )
        write_audit_log(
            f"[ZERODHA-BUY-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price} (wing)"
        )
        return str(order_id), limit_price, qty
    # ── WING_FAST_FILL_20261111 END ──

    def fresh_sell_entry_limit(self, symbol: str):
        """Re-peg price for a working SELL entry (D1 loop): best bid
        preferred, LTP fallback. Returns (limit, ref, src) or None."""
        q = self._resolve_entry_quote(symbol)
        ref = q["bid"] if q["bid"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._entry_limit_price(ref, "SELL"), ref,
                "bid" if q["bid"] > 0 else "ltp")

    def fresh_buy_entry_limit(self, symbol: str):
        """Re-peg price for a working BUY entry: best ask preferred."""
        q = self._resolve_entry_quote(symbol)
        ref = q["ask"] if q["ask"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._entry_limit_price(ref, "BUY"), ref,
                "ask" if q["ask"] > 0 else "ltp")
    # ── TSG_ENTRY_REPEG END (pricing helpers) ─────────

    # ── EXIT_REPEG_FAST_20261116 ── DEF-033. The re-price of a resting EXIT
    # order asked Kite for market depth with no time limit of its own; when
    # the call hung (7 s client timeout) the exit driver stopped for 7 s and
    # then modified an order that had already filled (16 Nov 09:16, Icarus
    # 26350 CE wing). Exits now wait at most EXIT_QUOTE_CAP_S for depth, then
    # re-price from a fresh live tick (no tick → this re-peg is skipped and
    # the order keeps its limit). A depth answer inside the cap prices
    # exactly as before. Entries keep fresh_*_entry_limit unchanged.
    EXIT_QUOTE_CAP_S = 1.5

    def _exit_quote(self, symbol: str) -> dict:
        box: dict = {}
        done = threading.Event()

        def _run():
            try:
                box["q"] = self._resolve_entry_quote(symbol)
            except Exception as e:                       # never raises to the exit path
                box["err"] = e
            finally:
                done.set()

        threading.Thread(target=_run, name=f"exit-quote-{symbol}", daemon=True).start()
        if done.wait(self.EXIT_QUOTE_CAP_S) and box.get("q"):
            return box["q"]
        out = {"ltp": 0.0, "bid": 0.0, "ask": 0.0}
        try:
            r = LTPStore.get_with_timestamp(symbol)
            if r and r[0] and float(r[0]) > 0 and (time.time() - float(r[1])) <= self.EXIT_TICK_FRESH_S:
                out["ltp"] = float(r[0])
        except Exception:
            pass
        write_audit_log(f"[ZERODHA][EXIT_QUOTE] {symbol}: depth call over "
                        f"{self.EXIT_QUOTE_CAP_S:.1f}s — "
                        + (f"re-priced from the live tick {out['ltp']}" if out["ltp"] > 0
                           else "no fresh tick, limit kept this round"))
        return out

    def fresh_sell_exit_limit(self, symbol: str):
        """Re-peg price for a working SELL exit: as fresh_sell_entry_limit,
        never waiting on a hung depth call."""
        q = self._exit_quote(symbol)
        ref = q["bid"] if q["bid"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._entry_limit_price(ref, "SELL"), ref,
                "bid" if q["bid"] > 0 else "ltp")

    def fresh_buy_exit_limit(self, symbol: str):
        """Re-peg price for a working BUY exit (short buy-back)."""
        q = self._exit_quote(symbol)
        ref = q["ask"] if q["ask"] > 0 else q["ltp"]
        if ref <= 0:
            return None
        return (self._entry_limit_price(ref, "BUY"), ref,
                "ask" if q["ask"] > 0 else "ltp")
    # ── EXIT_REPEG_FAST_20261116 END ──

    # ── EXIT_PRICE_FAST_20261115 ── exits are priced off a FRESH live tick when
    # there is one (no REST round trip at all), else REST as before. A REST
    # call that HANGS (Kite's client times out after 7 s) is not retried for
    # an exit: three hung calls held an SL buy-back for ~22 s.
    EXIT_TICK_FRESH_S = 2.0
    SLOW_CALL_S = 2.0

    def _exit_ltp(self, symbol: str, attempts: int = 3) -> Optional[float]:
        try:
            r = LTPStore.get_with_timestamp(symbol)
            if r and r[0] and float(r[0]) > 0 and (time.time() - float(r[1])) <= self.EXIT_TICK_FRESH_S:
                return float(r[0])
        except Exception:
            pass
        own = self.__dict__.get("_resolve_ltp")      # replaced on this object: call it as before
        if own is not None:
            return own(symbol)
        return self._resolve_ltp(symbol, attempts=attempts, stop_when_slow=True)

    def _resolve_ltp(self, symbol: str, attempts: int = 3,
                     stop_when_slow: bool = False) -> Optional[float]:
        """REST-primary LTP fetch (for order placement), retrying."""
        ltp = None
        # ── ORDER_LTP_RETRY_20260911 ── up to 3 REST attempts, rate-limit
        # aware, every failure audited. One silent one-shot cost a whole
        # TSG day on 2026-09-09 (wing outside the ticker universe + a 09:16
        # REST burst).
        for attempt in range(1, attempts + 1):
            _t0 = time.time()
            try:
                data_kite = self.broker_manager.get_data_kite()
                if not data_kite:
                    break
                _k = f"{_fo_exchange(symbol)}:{symbol}"   # ── LVB_V1_20261023 ──
                quote = data_kite.ltp(_k)
                rest_ltp = (quote or {}).get(_k, {}).get("last_price")
                if rest_ltp and rest_ltp > 0:
                    ltp = float(rest_ltp)
                    break
                write_audit_log(f"[ZERODHA][LTP_RETRY] {symbol} attempt {attempt}/3: "
                                f"empty quote")
            except Exception as e:
                write_audit_log(f"[ZERODHA][LTP_RETRY] {symbol} attempt {attempt}/{attempts}: {e!r}")
                if stop_when_slow and time.time() - _t0 >= self.SLOW_CALL_S:
                    write_audit_log(f"[ZERODHA][LTP_RETRY] {symbol}: price call hung "
                                    f"{time.time() - _t0:.1f}s — not retried for an exit")
                    break
                if attempt < attempts:
                    time.sleep(1.1 if "too many requests" in repr(e).lower() else 0.4)
                continue
            if attempt < attempts:
                time.sleep(0.4)

        if not ltp or ltp <= 0:
            ltp = LTPStore.get(symbol)
            if ltp and ltp > 0:
                write_audit_log(f"[ZERODHA][LTP_RETRY] {symbol}: REST exhausted, "
                                f"using LTPStore {ltp}")

        return ltp

    # ── BUY entry (LONG - existing behaviour) ─────────

    # ── ACC2_PST ── moved here verbatim from pst_order_executor.limit_buy():
    # same kwargs, same _relay_call(relay_fn, direct_fn) fallback policy.
    # Behaviour is byte-equivalent to the previous inline version; only the
    # call site moved, so PST can now talk to ANY executor.
    def place_limit_buy(self, symbol: str, qty: int, price: float,
                        product: str = "MIS", tag: str = "") -> Optional[str]:
        kite = self._kite()
        if kite is None:
            raise RuntimeError("place_limit_buy: no trade kite")
        kw = dict(variety=kite.VARIETY_REGULAR, exchange="NFO",
                  tradingsymbol=symbol, transaction_type="BUY",
                  quantity=int(qty),
                  product=("MIS" if product == "MIS" else "NRML"),
                  order_type=kite.ORDER_TYPE_LIMIT,
                  price=round(float(price), 1))
        if tag:
            kw["tag"] = tag
        oid = self._relay_call(
            relay_fn=lambda r: r.place_order(**kw),
            direct_fn=lambda: kite.place_order(**kw),
            op_name="LIMIT_BUY", symbol=symbol,
            tag=_tag_params(kw))   # ── ORDER_ONCE_20261115 ──
        write_audit_log(f"[LIMIT-BUY] {symbol} x{qty} @{price} "
                        f"product={product} ORDER_ID={oid}")
        return oid

    @_order_timed   # ── ORDER_LATENCY_20261111 ──
    def place_buy(self, symbol: str, token: int, qty: int):
        self._ensure_trading_enabled()

        if qty <= 0 or qty > MAX_QTY_PER_ORDER:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}")

        ltp = LTPStore.get(symbol)
        if not ltp or ltp <= 0:
            # ── ORDER_LTP_RETRY_20260911 ── was a silent one-shot REST call
            ltp = self._resolve_ltp(symbol)

        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price  = self._protected_limit_price(ltp, "BUY")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=_fo_exchange(symbol),   # ── LVB_V1_20261023 ── ("NFO" for every NSE symbol)
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(f"[ZERODHA-BUY] {symbol} qty={qty} ltp={ltp} limit={limit_price}")

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="BUY", symbol=symbol,
            tag=_tag_params(order_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-BUY-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return order_id, 0.0, qty

    # ── SELL entry (SHORT - NEW for SCALP_V1) ─────────

    @_order_timed   # ── ORDER_LATENCY_20261111 ──
    def place_sell_entry(self, symbol: str, token: int, qty: int):
        """
        Short entry: SELL the option at a protected limit price.
        Uses REST LTP as primary source (same as place_buy).
        Returns (order_id, limit_price, qty).
        """
        self._ensure_trading_enabled()
        # ── INSTANCE_GUARD_20261115 ── a second Scalp Terminal is trading this
        # account: no NEW shorts (the strategy unwinds the entry; exits go on).
        try:
            from app.execution import instance_guard as _IG
            _why = _IG.blocked()
        except Exception:
            _why = None
        if _why:
            write_audit_log(f"[ZERODHA][SELL_ENTRY][BLOCKED] {symbol} {_why}")
            raise RuntimeError(f"INSTANCE_GUARD: {_why}")

        if qty <= 0 or qty > MAX_QTY_PER_ORDER:
            raise RuntimeError(f"INVALID_QTY qty={qty} SYMBOL={symbol}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(f"INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}")

        # ── TSG_ENTRY_REPEG ── D2/D3: reference = best bid (touch), LTP
        # fallback; tiered buffer via _entry_limit_price. Shared callers
        # (IC / TMA / PST / SCALP short entries) inherit this — the change
        # is direction-safe: the limit is never LESS marketable than the
        # old LTP−1%.
        q = self._resolve_entry_quote(symbol)
        ltp = q["ltp"]
        ref = q["bid"] if q["bid"] > 0 else ltp
        if not ref or ref <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price  = self._entry_limit_price(ref, "SELL")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(
            f"[ZERODHA-SELL-ENTRY] {symbol} qty={qty} ltp={ltp} "
            f"bid={q['bid']} ref={ref} limit={limit_price}"
        )

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="SELL_ENTRY", symbol=symbol,
            tag=_tag_params(order_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-SELL-ENTRY-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return str(order_id), limit_price, qty

    # ── BUY exit (close SHORT - NEW for SCALP_V1) ─────

    @_order_timed   # ── ORDER_LATENCY_20261111 ──
    def place_buy_exit(self, symbol: str, qty: int, reason: str) -> str:
        """
        Buy back a short option position to close it.
        REST LTP primary, LTPStore fallback.
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_BUY_EXIT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY_FOR_BUY_EXIT SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(
                f"BUY_EXIT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = self._exit_ltp(symbol)          # ── EXIT_PRICE_FAST_20261115 ──
        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for buy_exit {symbol}")

        limit_price  = self._protected_limit_price(ltp, "BUY")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(
            f"[ZERODHA-BUY-EXIT] {symbol} qty={qty} ltp={ltp} "
            f"limit={limit_price} reason={reason}"
        )

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="BUY_EXIT", symbol=symbol,
            tag=_tag_params(order_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-BUY-EXIT-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return str(order_id)

    # ── AVG PRICE (read - always direct) ──────────────

    def get_last_avg_price(self, order_id: str) -> float:
        kite = self._kite()
        if not kite:
            return 0.0
        try:
            orders = kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return float(o.get("average_price") or 0.0)
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] Avg price fetch failed ERR={e}")
        return 0.0

    # ── ORDER FILL (read - always direct) ─────────────

    def get_order_fill(self, order_id: str) -> Dict:
        """
        Single-call order status + fill detail from the broker order book.

        Returns:
            {
              "status":      <str|None>,   # raw Kite status
              "avg_price":   <float>,      # average fill price (0.0 if none)
              "filled_qty":  <int>,        # quantity filled so far
              "pending_qty": <int>,        # quantity still working
              "found":       <bool>,       # order present in the book
            }

        status is the raw Kite order status, e.g.:
          "COMPLETE"                         -> filled; avg_price is valid
          "REJECTED" / "CANCELLED" / "LAPSED"-> dead; no position resulted
          "OPEN" / "TRIGGER PENDING" / etc.  -> still working
          None (found=False)                 -> order not in the book yet.
                                                The Kite order book can lag by
                                                seconds (occasionally minutes),
                                                so callers MUST treat this as
                                                "pending", never as "rejected".

        filled_qty / pending_qty let callers distinguish a clean cancel
        (filled_qty == 0) from a partial fill (0 < filled_qty < ordered qty)
        at a cancel boundary.
        """
        empty = {
            "status": None, "avg_price": 0.0,
            "filled_qty": 0, "pending_qty": 0, "found": False,
            "status_message": "",   # ── FLEET_EOD_ALERT_FIX_20260908 ──
        }
        kite = self._kite()
        if not kite:
            return empty
        try:
            orders = kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return {
                        "status":      o.get("status"),
                        "avg_price":   float(o.get("average_price") or 0.0),
                        "filled_qty":  int(o.get("filled_quantity") or 0),
                        "pending_qty": int(o.get("pending_quantity") or 0),
                        "found":       True,
                        # ── FLEET_EOD_ALERT_FIX_20260908 ── the broker's
                        # rejection text (e.g. "Insufficient funds. Margin
                        # required: ..."). Additive: every existing caller
                        # reads status/avg_price/filled_qty only.
                        "status_message": str(o.get("status_message")
                                              or o.get("status_message_raw")
                                              or ""),
                    }
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] Order fill fetch failed ERR={e}")
        return empty

    # ── GTT OCO ── direction-aware ─────────────────────

    def place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        tp_price: float,
        last_price: float = None,
        direction: str = "LONG",   # "LONG" (BB/HA) or "SHORT" (SCALP_V1)
        limit_buffer: Optional[float] = None,   # ── OCO_STOP_BUFFER_20261116 ──
    ) -> str:
        """
        limit_buffer (DEF-035): stop-leg limit multiplier as in
        place_gtt_sl_only_short/long (1.05 = 5%). None = the historical 0.5%
        (every caller except Icarus). The target leg is never changed.

        LONG  (default - BB/HA unchanged):
          trigger_values = [sl_lower, tp_upper]
          orders = SELL at sl_limit + SELL at tp_limit

        SHORT (SCALP_V1 short options):
          trigger_values = [tp_lower, sl_upper]
          orders = BUY at tp_limit + BUY at sl_limit
          (buy back the shorted premium)
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_GTT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_GTT")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(
                f"GTT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None:
            raise RuntimeError("LTP unavailable for GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger      = r(sl_price)
        tp_trigger      = r(tp_price) if tp_price and tp_price > 0 else None
        safe_last_price = round(ltp, 2)

        # ── LONG GTT (existing logic - BB/HA) ─────────
        if direction == "LONG":
            if sl_trigger <= 0:
                raise RuntimeError(f"GTT_INVALID_SL SL={sl_trigger}")

            sl_limit = r(sl_price * 0.995)
            if limit_buffer:                      # ── OCO_STOP_BUFFER_20261116 ──
                sl_limit = r(max(0.05, sl_trigger / max(1.0, float(limit_buffer))))

            if tp_trigger:
                if not (sl_trigger < safe_last_price < tp_trigger):
                    raise RuntimeError(
                        f"Invalid GTT band SL={sl_trigger} LAST={safe_last_price} TP={tp_trigger}"
                    )
                tp_limit   = r(tp_price * 0.997)
                gtt_params = dict(
                    trigger_type=kite.GTT_TYPE_OCO,
                    tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    trigger_values=[sl_trigger, tp_trigger],
                    last_price=safe_last_price,
                    orders=[
                        {
                            "transaction_type": kite.TRANSACTION_TYPE_SELL,
                            "quantity": qty,
                            "order_type": kite.ORDER_TYPE_LIMIT,
                            "price": sl_limit,
                            "product": kite.PRODUCT_NRML,
                        },
                        {
                            "transaction_type": kite.TRANSACTION_TYPE_SELL,
                            "quantity": qty,
                            "order_type": kite.ORDER_TYPE_LIMIT,
                            "price": tp_limit,
                            "product": kite.PRODUCT_NRML,
                        },
                    ],
                )
                log_suffix = f"LONG SL={sl_trigger}/{sl_limit} TP={tp_trigger}/{tp_limit}"
            else:
                if not (sl_trigger < safe_last_price):
                    raise RuntimeError(
                        f"Invalid GTT band SL={sl_trigger} LAST={safe_last_price}"
                    )
                gtt_params = dict(
                    trigger_type=kite.GTT_TYPE_SINGLE,
                    tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    trigger_values=[sl_trigger],
                    last_price=safe_last_price,
                    orders=[
                        {
                            "transaction_type": kite.TRANSACTION_TYPE_SELL,
                            "quantity": qty,
                            "order_type": kite.ORDER_TYPE_LIMIT,
                            "price": sl_limit,
                            "product": kite.PRODUCT_NRML,
                        },
                    ],
                )
                log_suffix = f"LONG SL={sl_trigger}/{sl_limit}"

        # ── SHORT GTT (NEW - SCALP_V1) ─────────────────
        else:
            # For short: sl_price > entry > tp_price
            # lower trigger = TP (buy back cheap = profit)
            # upper trigger = SL (buy back expensive = loss)
            if not tp_trigger:
                raise RuntimeError("SHORT GTT requires a valid tp_price")

            if not (tp_trigger < safe_last_price < sl_trigger):
                raise RuntimeError(
                    f"Invalid SHORT GTT band TP={tp_trigger} LAST={safe_last_price} SL={sl_trigger}"
                )

            # BUY limits: TP limit slightly above trigger (willing to pay a little more)
            #             SL limit slightly above trigger (must fill fast on stop)
            tp_limit = r(tp_trigger * 1.003)
            sl_limit = r(sl_trigger * 1.005)
            if limit_buffer:                      # ── OCO_STOP_BUFFER_20261116 ──
                sl_limit = r(sl_trigger * max(1.0, float(limit_buffer)))

            gtt_params = dict(
                trigger_type=kite.GTT_TYPE_OCO,
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NFO,
                trigger_values=[tp_trigger, sl_trigger],   # lower=TP, upper=SL
                last_price=safe_last_price,
                orders=[
                    {   # lower trigger -> TP hit -> buy back at profit
                        "transaction_type": kite.TRANSACTION_TYPE_BUY,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": tp_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                    {   # upper trigger -> SL hit -> buy back to cut loss
                        "transaction_type": kite.TRANSACTION_TYPE_BUY,
                        "quantity": qty,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": sl_limit,
                        "product": kite.PRODUCT_NRML,
                    },
                ],
            )
            log_suffix = (
                f"SHORT TP={tp_trigger}/{tp_limit} SL={sl_trigger}/{sl_limit}"
            )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gtt_id = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gtt_id)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gtt_id = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gtt_id)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt,
            direct_fn=_direct_gtt,
            op_name="GTT", symbol=symbol,
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} {log_suffix}"
        )
        return gtt_id
    
    # ── TP-ONLY GTT (LONG) — HA_V1 backstop ────────────
    # ── HA_TP_GTT BEGIN ──
    def place_gtt_tp_only_long(
        self,
        symbol: str,
        qty: int,
        tp_price: float,
        last_price: float = None,
    ) -> str:
        """
        Place a SINGLE (non-OCO) GTT that SELLS the LONG option position when
        price rises to tp_price. This is HA_V1's broker-side TP backstop: it
        fires even if the app is blind (lost tick subscription, crashed, or
        killed), which is the failure mode that left a position naked past TP.

        WHY TP-ONLY (no SL leg): HA's SL is evaluated on CANDLE CLOSE only —
        intra-candle wicks below SL must NOT exit. A broker SL-GTT triggers on
        the live tick, so it would fire on a wick and violate HA's SL semantics.
        The app keeps candle-close SL; the broker only holds the tick-based TP.
        The two never conflict because they protect different exit conditions.

        Distinct from place_gtt_oco():
          * place_gtt_oco LONG requires sl_price > 0 (raises GTT_INVALID_SL
            otherwise) and its SINGLE path is SL-only. It cannot express a
            TP-only LONG GTT, so this is a separate, additive method. BB / HA-OCO
            / SCALP_V1 / V3 GTT paths are untouched.

        Returns the GTT id (str). Raises on invalid band / broker-not-ready so
        the caller can alert and fall back to app-side monitoring.
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_TP_GTT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_TP_GTT")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(
                f"TP_GTT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None or ltp <= 0:
            raise RuntimeError("LTP unavailable for TP GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        tp_trigger = r(tp_price)
        safe_last  = round(ltp, 2)

        if tp_trigger <= 0:
            raise RuntimeError(f"TP_GTT_INVALID_TP TP={tp_trigger}")

        # A GTT to SELL on the way UP requires the trigger ABOVE the last price.
        # (If price is already at/above TP, the app-side tick monitor should have
        # exited already; refuse rather than place a would-trigger-instantly GTT.)
        if not (safe_last < tp_trigger):
            raise RuntimeError(
                f"Invalid TP-GTT band LAST={safe_last} !< TP={tp_trigger}"
            )

        # Sell limit slightly below the trigger so it fills promptly on trigger
        # (same 0.997 factor the LONG-OCO TP leg uses).
        tp_limit = r(tp_price * 0.997)

        gtt_params = dict(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[tp_trigger],
            last_price=safe_last,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "price": tp_limit,
                    "product": kite.PRODUCT_NRML,
                },
            ],
        )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt, direct_fn=_direct_gtt,
            op_name="GTT_TP_ONLY", symbol=symbol,
            find=lambda: self._find_gtts_like(gtt_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"TP_ONLY_LONG TP={tp_trigger}/{tp_limit} last={safe_last}"
        )
        return gtt_id
    # ── HA_TP_GTT END ──

    # ── IC_V1_GTT BEGIN ──
    def place_gtt_sl_only_short(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        last_price: float = None,
        limit_buffer: float = 1.003,
    ) -> str:
        """
        Place a SINGLE (non-OCO) GTT that BUYS BACK a SHORT option position
        when price RISES to sl_price. IC_V1's broker-side SL: its shorts
        default to tp_val=0 and place_gtt_oco(direction="SHORT") hard-raises
        without a valid tp_price, so this is a separate, ADDITIVE method —
        BB / HA / SCALP_V1..V5 GTT paths are untouched.

        Used twice by IC_V1: initial 42% SL protection at entry, and the
        Move-To-Cost re-pin (new GTT at the survivor short's entry price).

        Returns the GTT id (str). Raises on invalid band / broker-not-ready
        so the caller can fall back (IC: market-out per locked decision D5).
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_SL_GTT SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_SL_GTT")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(
                f"SL_GTT_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None or ltp <= 0:
            raise RuntimeError("LTP unavailable for SL GTT")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger = r(sl_price)
        safe_last  = round(ltp, 2)

        if sl_trigger <= 0:
            raise RuntimeError(f"SL_GTT_INVALID_SL SL={sl_trigger}")

        # A GTT to BUY BACK on the way UP requires the trigger ABOVE the last
        # price. If price is already at/through the stop, a resting GTT is the
        # wrong tool — refuse so the caller market-outs instead (IC D5).
        if not (safe_last < sl_trigger):
            raise RuntimeError(
                f"Invalid SL-GTT band LAST={safe_last} !< SL={sl_trigger}"
            )

        # Buy limit ABOVE the trigger so it fills on trigger. limit_buffer
        # defaults to the historical 1.003; IC_V2 passes a config-driven
        # wider buffer (gtt_limit_buffer_pct, default 5%) — gap defence
        # layer 1: a 0.3% limit rests off-market on any fast move and the
        # consumed GTT leaves the short naked (see ic_gtt_monitor escalation
        # for layer 2).
        sl_limit = r(sl_trigger * max(1.0, float(limit_buffer or 1.003)))

        gtt_params = dict(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[sl_trigger],
            last_price=safe_last,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_BUY,
                    "quantity": qty,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "price": sl_limit,
                    "product": kite.PRODUCT_NRML,
                },
            ],
        )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt, direct_fn=_direct_gtt,
            op_name="GTT_SL_ONLY_SHORT", symbol=symbol,
            find=lambda: self._find_gtts_like(gtt_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"SL_ONLY_SHORT SL={sl_trigger}/{sl_limit} last={safe_last}"
        )
        return gtt_id

    def place_gtt_sl_only_long(
        self,
        symbol: str,
        qty: int,
        sl_price: float,
        last_price: float = None,
        limit_buffer: float = 1.003,
    ) -> str:
        """
        ── IC_V2 (2026-07-26) ── Place a SINGLE (non-OCO) GTT that SELLS a
        LONG option position when price FALLS to sl_price. Used by IC_V1's
        ·ADJ adjustment legs (BUY with 25% SL, tp_val=0 by default —
        place_gtt_oco hard-raises without a valid tp_price, so this is a
        separate, ADDITIVE method mirroring place_gtt_sl_only_short).
        BB / HA / SCALP_V1..V5 / PST / TMA GTT paths are untouched.

        Sell limit slightly BELOW the trigger (limit_buffer divides) so the
        exit fills on trigger; the same triggered-but-unfilled escalation in
        ic_gtt_monitor covers the gap-down-past-buffer case.

        Returns the GTT id (str). Raises on invalid band / broker-not-ready
        so the caller can fall back (IC: tick-monitor-only + alert).
        """
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_SL_GTT_LONG SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_SL_GTT_LONG")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(
                f"SL_GTT_LONG_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = last_price or LTPStore.get(symbol)
        if ltp is None or ltp <= 0:
            raise RuntimeError("LTP unavailable for SL GTT (long)")

        def r(x: float) -> float:
            return round(round(x / 0.05) * 0.05, 2)

        sl_trigger = r(sl_price)
        safe_last  = round(ltp, 2)

        if sl_trigger <= 0:
            raise RuntimeError(f"SL_GTT_LONG_INVALID_SL SL={sl_trigger}")

        # A GTT to SELL on the way DOWN requires the trigger BELOW the last
        # price. If price is already at/through the stop, a resting GTT is
        # the wrong tool — refuse so the caller falls back.
        if not (safe_last > sl_trigger):
            raise RuntimeError(
                f"Invalid SL-GTT-LONG band LAST={safe_last} !> SL={sl_trigger}"
            )

        buf = max(1.0, float(limit_buffer or 1.003))
        sl_limit = r(max(0.05, sl_trigger / buf))

        gtt_params = dict(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[sl_trigger],
            last_price=safe_last,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "price": sl_limit,
                    "product": kite.PRODUCT_NRML,
                },
            ],
        )

        def _direct_gtt():
            result = kite.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        def _relay_gtt(relay):
            result = relay.place_gtt(**gtt_params)
            gid = result.get("trigger_id", result) if isinstance(result, dict) else result
            return str(gid)

        gtt_id = self._relay_call(
            relay_fn=_relay_gtt, direct_fn=_direct_gtt,
            op_name="GTT_SL_ONLY_LONG", symbol=symbol,
            find=lambda: self._find_gtts_like(gtt_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-GTT-PLACED] GTT_ID={gtt_id} SYMBOL={symbol} "
            f"SL_ONLY_LONG SL={sl_trigger}/{sl_limit} last={safe_last}"
        )
        return gtt_id
    # ── IC_V1_GTT END ──

    # ── IC_V1_MARGIN BEGIN ──
    def get_basket_margin(self, basket: list) -> dict:
        """
        IC_V1 margin guard (locked decision D8). basket: list of
        {"symbol","qty","transaction_type"} for the full 4-leg condor.
        Returns {"required": float, "available": float}.

        RAISES on any broker/API failure — the CALLER treats an exception as
        advisory-fail-open (cannot-compute != shortfall). Only a clean read
        showing required > available blocks entry.
        """
        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_MARGIN")

        params = [
            {
                "exchange":         "NFO",
                "tradingsymbol":    o["symbol"],
                "transaction_type": o["transaction_type"],
                "variety":          "regular",
                "product":          "NRML",
                "order_type":       "MARKET",
                "quantity":         int(o["qty"]),
                "price":            0,
                "trigger_price":    0,
            }
            for o in basket
        ]

        res = kite.basket_order_margins(params, consider_positions=True)
        required = float(
            ((res or {}).get("final") or {}).get("total")
            or ((res or {}).get("initial") or {}).get("total")
            or 0.0
        )

        m = kite.margins("equity") or {}
        avail = m.get("available") or {}
        available = float(
            avail.get("live_balance")
            or avail.get("cash")
            or m.get("net")
            or 0.0
        )

        write_audit_log(
            f"[IC][MARGIN_CHECK] required={required:.0f} available={available:.0f}"
        )
        return {"required": required, "available": available}
    # ── IC_V1_MARGIN END ──

    # ── existing methods preserved unchanged ──────────

    def cancel_order(self, order_id: str, symbol: str = ""):
        # ── TSG_ENTRY_REPEG ── D4: the op log used to render as
        # "[RELAY][CANCEL_ORDER]  - SUCCESS" (blank subject) — grep-hostile
        # during the 2026-08-10 incident. symbol is optional so every
        # existing cancel_order(oid) call site keeps working; order_id is
        # always in the log subject either way.
        kite = self._kite()
        if not kite:
            return
        self._relay_call(
            relay_fn=lambda r: r.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id),
            direct_fn=lambda: kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id),
            op_name="CANCEL_ORDER",
            symbol=f"{symbol} order_id={order_id}".strip(),
        )

    # ── TSG_ENTRY_REPEG ── D1: relay-routed order modify ─────────────
    def modify_order(self, order_id: str, price: float,
                     symbol: str = "") -> Optional[str]:
        """Modify a working LIMIT order's price in place (re-peg). Keeps
        the same order_id on Kite — no cancel/re-place orphan window.
        Returns the order_id on success, None on failure (caller decides
        whether to keep waiting on the old price or abort)."""
        kite = self._kite()
        if not kite:
            return None
        params = dict(variety=kite.VARIETY_REGULAR,
                      order_id=order_id, price=price)
        try:
            self._relay_call(
                relay_fn=lambda r: r.modify_order(**params),
                direct_fn=lambda: kite.modify_order(**params),
                op_name="MODIFY_ORDER",
                symbol=f"{symbol} order_id={order_id} price={price}".strip(),
            )
            write_audit_log(
                f"[ZERODHA-MODIFY] ORDER_ID={order_id} SYMBOL={symbol} "
                f"NEW_LIMIT={price}")
            return str(order_id)
        except Exception as e:
            write_audit_log(
                f"[ZERODHA-MODIFY-FAIL] ORDER_ID={order_id} "
                f"SYMBOL={symbol} price={price} ERR={e}")
            return None

    def get_orders(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        out = kite.orders()
        try:                                      # ── INSTANCE_GUARD_20261115 ── in memory only
            from app.execution import instance_guard as _IG
            _IG.observe(out)
            _IG.start_poll(self.get_orders)
        except Exception:
            pass
        return out

    def get_open_positions(self) -> List[Dict]:
        kite = self._kite()
        if not kite:
            return []
        positions = kite.positions()
        return [p for p in positions.get("net", []) if p.get("quantity", 0) != 0]

    def place_sl(self, symbol: str, qty: int, sl_price: float) -> str:
        raise RuntimeError("place_sl() not supported in GTT-only mode")

    def place_exit(self, symbol: str, qty: int, reason: str) -> str:
        raise RuntimeError("place_exit() not supported in GTT-only mode")

    def cancel_gtt(self, gtt_id: str):
        kite = self._kite()
        if not kite:
            raise RuntimeError("BROKER_NOT_READY_FOR_GTT_CANCEL")
        try:
            self._relay_call(
                relay_fn=lambda r: r.cancel_gtt(int(gtt_id)),
                direct_fn=lambda: kite.delete_gtt(int(gtt_id)),
                op_name="CANCEL_GTT",
            )
            write_audit_log(f"[ZERODHA-GTT-CANCELLED] GTT_ID={gtt_id}")
        except Exception as e:
            write_audit_log(f"[ZERODHA-GTT-CANCEL-WARN] GTT_ID={gtt_id} ERR={e}")
            raise

    # ── EXIT_LATENCY_20261115 ──
    _GTT_VERIFY_GAPS = (0.25, 0.35, 0.4, 0.5)      # checks at 0.25 / 0.6 / 1.0 / 1.5 s
    _GTT_VERIFY_WINDOW_S = 1.5                     # the old single wait
    _gtt_last_state: dict = {}                     # gtt id -> state seen by the last verify

    def gtt_last_state(self, gtt_id) -> Optional[str]:
        """The state cancel_gtt_verified last SAW for this GTT ("triggered",
        "missing", "cancelled", …) — no broker call. Lets an exit learn that
        its GTT had already fired without a second read of the GTT list."""
        return self._gtt_last_state.get(str(gtt_id))

    def cancel_gtt_verified(self, gtt_id: str, retries: int = 4) -> bool:
            """
            Cancel a GTT and VERIFY it is actually gone at the broker.

            Zerodha's delete_gtt can return success while the trigger remains
            armed (observed live). A live orphan GTT on a flat position can fire
            an unintended order, so we re-fetch the GTT list and confirm the id
            is no longer ACTIVE.

            "Armed" means status == "active" ONLY. A "triggered" GTT has already
            fired (did its job) and merely lingers in the list briefly; it is
            NOT an orphan and must not be treated as one — doing so produced
            false CRITICALs whenever a normal SL fired.

            ── GTT_VERIFY_STRICT_20261112 ── FAIL-CLOSED. The list used to come
            from get_gtts(), which answers [] when it CANNOT read the list
            (stale morning token, session not ready, fetch error) — and an
            empty list read as "gone". 12 Nov 2026, IC_V2 LIVE: the pre-market
            teardown ran before the day's Kite login, every cancel raised, the
            unreadable list "confirmed" both overnight GTTs gone, the app
            forgot their ids, squared the legs off at 09:16 and left both
            GTTs armed; one later fired and SOLD 195 of a strike the app had
            just closed. Now only a READABLE list counts: missing / cancelled /
            deleted / triggered / disabled / expired = gone (True); active or
            unreadable after every retry = NOT verified (False) — the caller
            keeps the id, alerts, and retries.

            Returns True if the GTT is confirmed gone/spent, False when it is
            still ACTIVE or its state could not be read. Never raises.
            """
            target = str(gtt_id)

            def _state() -> str:
                gl = self.get_gtts_or_none()
                if gl is None:
                    return "unreadable"
                for g in gl:
                    if str(g.get("id")) == target:
                        return str(g.get("status") or "unknown").lower()
                return "missing"

            last = "unreadable"
            for attempt in range(retries + 1):
                try:
                    self.cancel_gtt(gtt_id)
                except Exception as e:
                    write_audit_log(f"[GTT_VERIFY] cancel attempt {attempt} raised ERR={e}")

                # Give the broker time to reflect the delete before checking.
                # Zerodha's GTT list is eventually consistent and can lag the
                # cancel by a few seconds.
                # ── EXIT_LATENCY_20261115 ── this used to be ONE fixed
                # time.sleep(1.5) before the single check — 1.5 s added to
                # EVERY exit that has a GTT, before the exit order is placed
                # (twice that for a condor's two shorts). The list is now
                # checked after 0.25 s and then at growing gaps over the SAME
                # 1.5 s: the cancel is confirmed as soon as the broker shows
                # it, and never later than before. An unreadable list is not
                # re-read inside the window (each such read can be slow).
                waited = 0.0
                for gap in self._GTT_VERIFY_GAPS:
                    time.sleep(gap)
                    waited += gap
                    last = _state()
                    if last == "unreadable":
                        time.sleep(max(0.0, self._GTT_VERIFY_WINDOW_S - waited))
                        break
                    if last not in ("active", "unknown"):
                        break
                self._gtt_last_state[target] = last
                if last not in ("active", "unreadable", "unknown"):
                    write_audit_log(f"[GTT_VERIFY] CONFIRMED_GONE GTT_ID={target} "
                                    f"status={last} attempt={attempt} after {waited:.2f}s")
                    return True
                if last == "active":
                    write_audit_log(f"[GTT_VERIFY] STILL_ARMED GTT_ID={target} attempt={attempt} — retrying")
                else:
                    write_audit_log(f"[GTT_VERIFY] UNVERIFIED GTT_ID={target} attempt={attempt} — "
                                    f"GTT list unreadable (session / token / network) — retrying")

            if last == "active":
                write_audit_log(f"[GTT_VERIFY][CRITICAL] GTT_ID={target} STILL ARMED after {retries+1} attempts")
            else:
                write_audit_log(f"[GTT_VERIFY][CRITICAL] GTT_ID={target} NOT VERIFIED after {retries+1} "
                                f"attempts — GTT list unreadable; treating it as STILL ARMED")
            return False

    # ── TMA_GTT_RACE_20260814 BEGIN ─────────────────────────────────────
    # (2026-08-14 TMA double-exit incident) ADDITIVE ONLY — no existing
    # method's behavior changes; BB/IC/HA consumers are untouched.
    #
    # cancel_gtt_verified() deliberately treats status=="triggered" as
    # "gone/spent" (correct for the orphan-GTT problem it was built for).
    # An SL-race caller needs the OPPOSITE reading: "triggered" means the
    # broker already executed the exit and the app must NOT place its own.
    # These two methods let money paths read broker truth explicitly.

    def get_gtt_status(self, gtt_id: str) -> Optional[str]:
        """Broker-truth status of one GTT.
        Returns None      — GTT list UNREADABLE (session not ready / fetch
                            failed): caller must treat as unknown, never
                            as absent.
                "missing"  — clean read, id not in the list.
                else       — the broker's status string ("active",
                            "triggered", "disabled", ...)."""
        gtts = self.get_gtts_or_none()
        if gtts is None:
            return None
        for g in gtts:
            if str(g.get("id")) == str(gtt_id):
                return str(g.get("status") or "") or "unknown"
        return "missing"

    def get_open_positions_or_none(self) -> Optional[List[Dict]]:
        """STRICT variant of get_open_positions() (get_gtts_or_none
        precedent, 2026-07-13 lesson): returns None when the positions book
        could not actually be read, and a real list ([] included) ONLY on a
        successful read. get_open_positions() returns [] on failure, which
        an exit guard cannot distinguish from "flat at the broker" — under
        a guard that skips a buyback when flat, that ambiguity would leave
        a live short unprotected. Existing get_open_positions() consumers
        are untouched."""
        kite = self._kite()
        if not kite:
            write_audit_log(
                "[ZERODHA][WARN] STRICT positions fetch skipped — session not ready"
            )
            return None
        try:
            positions = kite.positions()
            return [p for p in positions.get("net", [])
                    if int(p.get("quantity") or 0) != 0]
        except Exception as e:
            write_audit_log(f"[ZERODHA][WARN] STRICT positions fetch failed ERR={e}")
            return None
    # ── TMA_GTT_RACE_20260814 END ───────────────────────────────────────

    @_order_timed   # ── ORDER_LATENCY_20261111 ──
    def place_market_sell(self, symbol: str, qty: int) -> str:
        """EOD square-off for LONG positions (BB/HA). Uses REST LTP primary."""
        self._ensure_trading_enabled()

        if qty <= 0:
            raise RuntimeError(f"INVALID_QTY_FOR_SELL SYMBOL={symbol} QTY={qty}")

        kite = self._kite()
        if not kite:
            raise RuntimeError(f"BROKER_NOT_READY_FOR_SELL SYMBOL={symbol}")

        lot_size = self._get_lot_size(kite, symbol, qty)   # ── ORDER_LATENCY_20261111 ──
        if qty % lot_size != 0:
            raise RuntimeError(
                f"SELL_INVALID_QTY qty={qty} lot_size={lot_size} SYMBOL={symbol}"
            )

        ltp = self._exit_ltp(symbol, attempts=1)   # ── EXIT_PRICE_FAST_20261115 ── tick, else REST once, else LTPStore

        if not ltp or ltp <= 0:
            raise RuntimeError(f"LTP unavailable for {symbol}")

        limit_price  = self._protected_limit_price(ltp, "SELL")
        order_params = dict(
            variety=kite.VARIETY_REGULAR,
            exchange=_fo_exchange(symbol),   # ── LVB_V1_20261023 ── ("NFO" for every NSE symbol)
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_NRML,
        )

        write_audit_log(f"[ZERODHA-SELL] {symbol} qty={qty} ltp={ltp} limit={limit_price}")

        order_id = self._relay_call(
            relay_fn=lambda r: r.place_order(**order_params),
            direct_fn=lambda: kite.place_order(**order_params),
            op_name="SELL", symbol=symbol,
            tag=_tag_params(order_params),   # ── ORDER_ONCE_20261115 ──
        )

        write_audit_log(
            f"[ZERODHA-SELL-PLACED] ORDER_ID={order_id} SYMBOL={symbol} "
            f"QTY={qty} LIMIT={limit_price}"
        )
        return str(order_id)