# backend/app/marketdata/shared_tick_feed.py
#
# ── STRATEGY_INDEPENDENCE_20261015 ── a tick socket for BB_V1 / BB_V2 / HA_V1
# that does not need SCALP_V1.
#
# BUG: BB_V1, BB_V2 and HA_V1 have no socket of their own. They receive every
# tick through get_ws_engines()[0] — the ZerodhaTickEngine that ONLY SCALP_V1's
# selection_loop creates (it fans each tick out to BB_ENGINE_REGISTRY /
# HA_ENGINE_REGISTRY). A licence with BB or HA but without SCALP_V1 never got
# a socket: those strategies silently never traded.
#
# FIX: when (and only when) a BB/HA strategy is licensed+enabled and SCALP_V1
# is NOT, this feed provides exactly the plumbing they use from the shared
# engine — and nothing of SCALP_V1's (no candles, indicators, signals, paper
# exits, MTM guard, timeline writes):
#   * registers itself in ws_registry → engines[0] for BB / HA
#   * same universe as SCALP_V1's engine: NIFTY weekly ±800 + BANKNIFTY
#     current-month FUT + NIFTY / BANKNIFTY / SENSEX index tokens, MODE_FULL
#   * .builders / .strategies[token].symbol (HA's universe discovery),
#     .subscribe_additional_tokens (BB FUT/options, HA selections)
#   * every tick → BB / HA on_tick(token, ltp, ts); index ticks →
#     MarketIndicesState; universe CE/PE → LTPStore (as the shared engine)
#   * on_connect → BB / HA on_ws_reconnect(); zombie-socket watchdog
# Where SCALP_V1 runs (every machine today that runs BB/HA) this module does
# nothing: the launcher logs "not needed" and returns. One socket either way.

from __future__ import annotations

import asyncio
import threading
import time
from typing import Dict, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log

FENCE = "STRATEGY_INDEPENDENCE_20261015"
FEED_CLIENTS = ("BB_V1", "BB_V2", "HA_V1")
ATM_RANGE = 800            # = selection_engine.ATM_RANGE (the shared universe)
STRIKE_STEP = 50
RETRY_S = 120
INDEX_ALLOWLIST = {"NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "SENSEX": "SENSEX"}


class _Sym:
    __slots__ = ("symbol",)

    def __init__(self, symbol: str):
        self.symbol = symbol


def _bb_registry():
    try:
        from app.core.engine_registry import BB_ENGINE_REGISTRY
        return BB_ENGINE_REGISTRY
    except Exception:
        return []


def _ha_registry():
    try:
        from app.core.ha_engine_registry import HA_ENGINE_REGISTRY
        return HA_ENGINE_REGISTRY
    except Exception:
        return []


def _market_open() -> bool:
    from app.utils.market_hours import is_market_open
    return is_market_open()


class SharedTickFeed:
    """Feed-only stand-in for ZerodhaTickEngine's shared role (see header)."""

    def __init__(self, kite_data, instrument_tokens: List[int], instruments_df=None,
                 ticker_factory=None, register: bool = True):
        self.strategy_id = "SHARED_FEED"
        self.kite_data = kite_data
        if instruments_df is None:
            from app.fetcher.zerodha_instruments import load_instruments_df
            instruments_df = load_instruments_df()
        self.instruments_df = instruments_df
        self._lock = threading.Lock()
        self._started = False
        self._connected = False
        self._extra_tokens: set = set()
        self._last_tick_ts = 0.0
        self._last_connect_ts = 0.0
        self._last_wd_action_ts = 0.0
        self._wd_strikes = 0
        self.ticks = 0

        self.index_tokens: Dict[int, str] = {}
        try:
            rows = instruments_df[instruments_df["segment"].isin(["INDICES", "BSE-INDICES"])]
            for _, r in rows.iterrows():
                ts = str(r["tradingsymbol"]).upper()
                if ts in INDEX_ALLOWLIST:
                    self.index_tokens[int(r["instrument_token"])] = INDEX_ALLOWLIST[ts]
        except Exception as e:
            write_audit_log(f"[SHARED_FEED] index token resolve failed: {e!r}")

        self.builders: Dict[int, None] = {}
        self.strategies: Dict[int, _Sym] = {}
        try:
            sym_of = dict(zip(instruments_df["instrument_token"].astype(int),
                              instruments_df["tradingsymbol"].astype(str)))
        except Exception:
            sym_of = {}
        for tok in instrument_tokens:
            tok = int(tok)
            sym = sym_of.get(tok)
            if not sym:
                continue
            self.builders[tok] = None
            self.strategies[tok] = _Sym(sym)

        if ticker_factory is None:
            from app.marketdata.rotating_ticker import RotatingKiteTicker
            ticker_factory = lambda: RotatingKiteTicker(       # noqa: E731
                api_key=kite_data.api_key, access_token=kite_data.access_token, kind="data")
        self.kws = ticker_factory()
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

        if register:
            from app.marketdata.ws_registry import register_ws_engine
            register_ws_engine(self)
        write_audit_log(f"[SHARED_FEED] built: {len(self.builders)} contracts + "
                        f"{len(self.index_tokens)} indices (no SCALP_V1 on this machine)")

    # ── the API BB / HA call on engines[0] ──
    def subscribe_additional_tokens(self, tokens: List[int]):
        if not tokens:
            return
        for t in tokens:
            self._extra_tokens.add(int(t))

        def _do():
            try:
                time.sleep(0.3)
                if not self._connected:
                    return              # _on_connect subscribes _extra_tokens
                self.kws.subscribe(list(tokens))
                self.kws.set_mode(self.kws.MODE_FULL, list(tokens))
                write_audit_log(f"[SHARED_FEED] additional tokens subscribed: {len(tokens)}")
            except Exception as e:
                write_audit_log(f"[SHARED_FEED][ERROR] subscribe_additional_tokens: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._wait_and_connect, daemon=True, name="shared-feed-connect").start()
        threading.Thread(target=self._tick_watchdog, daemon=True, name="shared-feed-watchdog").start()

    def _wait_and_connect(self):
        while not _market_open():
            time.sleep(30)
        try:
            self.kws.connect(threaded=True)
        except Exception as e:
            write_audit_log(f"[SHARED_FEED][FATAL] connect: {e!r}")

    # ── callbacks ──
    def _on_connect(self, ws, response):
        base = list(self.builders) + list(self.index_tokens)
        toks = base + [t for t in self._extra_tokens if t not in base]
        ws.subscribe(toks)
        ws.set_mode(ws.MODE_FULL, toks)
        write_audit_log(f"[SHARED_FEED] connected — {len(toks)} tokens "
                        f"({len(base)} initial + {len(toks) - len(base)} extra)")
        with self._lock:
            self._connected = True
            self._last_connect_ts = time.time()
        for reg, tag in ((_bb_registry(), "BB"), (_ha_registry(), "HA")):
            try:
                for e in reg:
                    e.on_ws_reconnect()
            except Exception as ex:
                write_audit_log(f"[SHARED_FEED][{tag}_RECONNECT_NOTIFY_ERROR] {ex}")

    def _on_close(self, ws, code, reason):
        write_audit_log(f"[SHARED_FEED] closed {code} {reason}")
        with self._lock:
            self._connected = False

    def _on_error(self, ws, code, reason):
        write_audit_log(f"[SHARED_FEED] error {code} {reason}")

    def _on_ticks(self, ws, ticks):
        self._last_tick_ts = time.time()
        bb, ha = _bb_registry(), _ha_registry()
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if token is None or ltp is None:
                continue
            self.ticks += 1
            ts = int(time.time())
            try:
                for e in bb:
                    e.on_tick(token, ltp, ts)
            except Exception as ex:
                write_audit_log(f"[SHARED_FEED][BB_DISPATCH_ERROR] {ex}")
            try:
                for e in ha:
                    e.on_tick(token, ltp, ts)
            except Exception as ex:
                write_audit_log(f"[SHARED_FEED][HA_DISPATCH_ERROR] {ex}")
            name = self.index_tokens.get(token)
            if name:
                try:
                    from app.marketdata.market_indices_state import MarketIndicesState
                    MarketIndicesState.update_ltp(name, ltp)
                except Exception:
                    pass
                continue
            s = self.strategies.get(token)
            if s and (s.symbol.endswith("CE") or s.symbol.endswith("PE")):
                try:
                    from app.marketdata.ltp_store import LTPStore
                    LTPStore.update(s.symbol, ltp)
                except Exception:
                    pass

    def _tick_watchdog(self):
        """Zombie-socket guard — same rule as ZerodhaTickEngine (2026-06-11):
        connected + market open + zero ticks for 120s → drop the protocol so
        KiteTicker's auto-reconnect heals it."""
        SILENT_S, POLL_S, COOLDOWN_S, BACKOFF_S = 120, 30, 90, 1800
        while True:
            try:
                time.sleep(POLL_S)
                if not self._connected or not _market_open():
                    continue
                base = max(self._last_tick_ts, self._last_connect_ts)
                if base <= 0:
                    continue
                silent = time.time() - base
                if silent < SILENT_S or time.time() - self._last_wd_action_ts < COOLDOWN_S:
                    continue
                if self._last_tick_ts > self._last_wd_action_ts:
                    self._wd_strikes = 0
                if self._wd_strikes >= 3 and silent < BACKOFF_S:
                    continue
                write_audit_log(f"[SHARED_FEED][WATCHDOG] connected but ZERO ticks for "
                                f"{int(silent)}s during market hours — forcing reconnect")
                self._wd_strikes += 1
                self._last_wd_action_ts = time.time()
                ws = getattr(self.kws, "ws", None)
                if ws is not None:
                    ws.sendClose(1000, "tick-watchdog")
            except Exception as e:
                try:
                    write_audit_log(f"[SHARED_FEED][WATCHDOG] loop error: {e!r}")
                except Exception:
                    pass


# ── launcher ─────────────────────────────────────────────────────────────
def needs_shared_feed(allows=None, registry=None) -> Tuple[bool, str]:
    """(needed, reason). Needed iff a BB/HA strategy will run and SCALP_V1
    (the only creator of the shared engine) will not."""
    if registry is None:
        from app.strategy.strategy_registry import STRATEGIES as registry
    if allows is None:
        from app.license import license_state
        allows = license_state.license_allows_strategy

    def runs(sid):
        try:
            return bool((registry.get(sid) or {}).get("enabled")) and bool(allows(sid))
        except Exception:
            return False

    clients = [s for s in FEED_CLIENTS if runs(s)]
    if not clients:
        return False, "no BB/HA strategy on this licence"
    if runs("SCALP_V1"):
        return False, f"SCALP_V1 runs here and owns the shared socket ({', '.join(clients)} ride it)"
    return True, f"{', '.join(clients)} licensed without SCALP_V1"


def _build_tokens(kite_trade) -> List[int]:
    from app.fetcher.zerodha_instruments import load_nifty_weekly_universe
    universe = load_nifty_weekly_universe(api_key=kite_trade.api_key,
                                          access_token=kite_trade.access_token,
                                          atm_range=ATM_RANGE, strike_step=STRIKE_STEP)
    tokens = [int(o["instrument_token"]) for o in (universe or [])]
    if not tokens:
        return []
    try:
        from app.engine.bb_options.futures_resolver import resolve_current_month_banknifty_fut
        r = resolve_current_month_banknifty_fut()
        if r and int(r[0]) not in tokens:
            tokens.append(int(r[0]))
    except Exception as e:
        write_audit_log(f"[SHARED_FEED][BB_FUT_INJECT_ERROR] {e}")
    return tokens


async def shared_tick_feed_loop(zerodha_manager, *, _need=None, _build=None, _feed_cls=None,
                                _engines=None, _sleep=asyncio.sleep, _max_cycles=None):
    """Launched once at boot. Returns immediately when not needed; otherwise
    waits for the broker, builds the universe, starts the feed and returns."""
    await asyncio.sleep(0)
    need, why = (_need or needs_shared_feed)()
    write_audit_log(f"[SHARED_FEED] {'starting' if need else 'not needed'} — {why}")
    if not need:
        return None
    build = _build or _build_tokens
    feed_cls = _feed_cls or SharedTickFeed
    if _engines is None:
        from app.marketdata.ws_registry import get_ws_engines as _engines
    n = 0
    while _max_cycles is None or n < _max_cycles:
        n += 1
        try:
            if _engines():
                write_audit_log("[SHARED_FEED] a shared engine is already registered — standing down")
                return None
            if not zerodha_manager.is_ready():
                await _sleep(30)
                continue
            kt, kd = zerodha_manager.get_trade_kite(), zerodha_manager.get_data_kite()
            if not kt or not kd:
                await _sleep(30)
                continue
            tokens = await asyncio.to_thread(build, kt)
            if not tokens:
                write_audit_log(f"[SHARED_FEED] weekly universe empty — retry in {RETRY_S}s")
                await _sleep(RETRY_S)
                continue
            feed = await asyncio.to_thread(feed_cls, kd, tokens)
            feed.start()
            write_audit_log(f"[SHARED_FEED] started for {why}")
            return feed
        except Exception as e:
            write_audit_log(f"[SHARED_FEED][ERROR] {e!r} — retry in {RETRY_S}s")
            await _sleep(RETRY_S)
    return None
