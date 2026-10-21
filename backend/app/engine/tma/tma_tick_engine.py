# backend/app/engine/tma/tma_tick_engine.py
#
# ── TMA TICK ENGINE ── (own WebSocket — V3/PST doctrine, per-family socket)
# ============================================================================
# Deliberate CLONE of pst_tick_engine (house precedent: strategy families are
# cloned, not shared — per-strategy isolation beats shared mutable plumbing).
# Differences
# from PST, both intentional:
#   * BAND_STRIKES = 30 (±1500 points around spot, IC's CHAIN_STRIKE_RANGE):
#     the ₹2-3 BUY hedge sits far OTM — a ±1000 band would systematically
#     miss it and force cheapest-real fallbacks that diverge from selection
#     intent. ~120 weekly symbols + spot is well inside Kite WS limits.
#   * capture dir ~/.scalp-app/tma_capture/YYYY-MM-DD.jsonl for the
#     end-of-day replay audit (PST daily-parity-report pattern).
#
# Responsibilities, deliberately minimal:
#   1. Subscribe NIFTY 50 index + the weekly option chain in the band.
#   2. Build boundary-aligned 1m candles per token from ticks (IST minutes,
#      ts = bar-START epoch — the corpus shape). A minute with no ticks
#      produces NO candle (a gap — the manager's gap rules are parity-proven).
#   3. At each minute boundary (+grace) hand the finalized minute to the
#      TMAMinuteCoordinator, which owns all ordering.
# NO trading logic lives here. Zombie-WS doctrine applies: connection status
# is not proof of ticks — the coordinator watchdog alerts on spot silence.
# ============================================================================

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)

IST = 5 * 3600 + 30 * 60
BAND_STRIKES = 30          # ±30 × 50 = ±1500 points around spot (hedge reach)
STRIKE_STEP = 50


# ── TMA_TICK_LTP_PUBLISH_20261015 ── this socket's option ticks are the only
# live price for these legs on a machine without HA's wide WS universe
# (Trades LTP, panels, Dashboard fleet marks, VET mark_src all read LTPStore).
# Fail-open: a store error must never reach the candle path.
def _publish_ltps(pairs) -> None:
    try:
        from app.marketdata.ltp_store import LTPStore
    except Exception:
        return
    for sym, px in pairs:
        try:
            LTPStore.update(sym, px)
        except Exception:
            pass


class CandleBuilder:
    """One instrument's tick→1m aggregation. ts = bar-START epoch."""

    def __init__(self):
        self.cur_ts: Optional[int] = None
        self.o = self.h = self.l = self.c = None

    def add_tick(self, ltp: float, epoch: int) -> Optional[dict]:
        bar = (int(epoch) // 60) * 60
        done = None
        if self.cur_ts is None:
            self.cur_ts = bar
            self.o = self.h = self.l = self.c = float(ltp)
            return None
        if bar > self.cur_ts:
            done = {"ts": self.cur_ts, "open": self.o, "high": self.h,
                    "low": self.l, "close": self.c}
            self.cur_ts = bar
            self.o = self.h = self.l = self.c = float(ltp)
            return done
        if bar == self.cur_ts:
            p = float(ltp)
            self.h = max(self.h, p)
            self.l = min(self.l, p)
            self.c = p
        return done   # late/out-of-order tick for an older minute: dropped

    def force_finalize_before(self, boundary_ts: int) -> Optional[dict]:
        if self.cur_ts is not None and self.cur_ts + 60 <= boundary_ts:
            done = {"ts": self.cur_ts, "open": self.o, "high": self.h,
                    "low": self.l, "close": self.c}
            self.cur_ts = None
            self.o = self.h = self.l = self.c = None
            return done
        return None


class TMAChainStore:
    """Finalized candles per symbol per minute + metadata — the live
    chain-view duck-type the manager consumes:
      candle(symbol, ts) -> dict|None; symbols(side) -> [str];
      meta(symbol) -> {"strike","expiry","side","token"}."""

    def __init__(self):
        self._c: Dict[str, Dict[int, dict]] = {}
        self._meta: Dict[str, dict] = {}
        self.now: int = -1

    def put_meta(self, symbol: str, strike: float, expiry: str,
                 side: str, token: int):
        self._meta[symbol] = {"strike": strike, "expiry": expiry,
                              "side": side, "token": int(token)}

    def put_candle(self, symbol: str, candle: dict):
        self._c.setdefault(symbol, {})[int(candle["ts"])] = candle

    def candle(self, symbol: str, ts: int) -> Optional[dict]:
        if ts > self.now:
            return None
        return self._c.get(symbol, {}).get(ts)

    def last_close_at_or_before(self, symbol: str, ts: int,
                                lookback_min: int = 30) -> Optional[float]:
        """Backtest _hedge_exit_price fallback shape: candle at ts, else the
        last candle ≤ ts within the lookback."""
        m = self._c.get(symbol) or {}
        for t in range(int(ts), int(ts) - lookback_min * 60 - 60, -60):
            c = m.get(t)
            if c:
                return float(c["close"])
        return None

    def symbols(self, side: str) -> List[str]:
        return [s for s, m in self._meta.items() if m["side"] == side]

    def meta(self, symbol: str) -> Optional[dict]:
        return self._meta.get(symbol)

    def reset_day(self):
        self._c = {}
        self.now = -1


class TMATickEngine:
    """Own KiteTicker; feeds a TMAMinuteCoordinator. Construction never
    connects — call start() from the selection loop once Zerodha is ready."""

    def __init__(self, zerodha_manager, instruments_df,
                 on_minute_cb: Callable[[int, Optional[dict], "TMAChainStore"], None],
                 capture_dir: Optional[str] = None,
                 notify: Optional[Callable] = None,       # ── WS_SELFHEAL_20261021 ──
                 log_tag: Optional[str] = None,
                 alert_label: Optional[str] = None):
        self.zm = zerodha_manager
        self.instruments_df = instruments_df
        self.on_minute_cb = on_minute_cb
        self.chain = TMAChainStore()
        self.capture_dir = capture_dir
        self._builders: Dict[int, CandleBuilder] = {}
        self._tok2sym: Dict[int, str] = {}
        self._spot_token: Optional[int] = None
        self._spot_candles: Dict[int, dict] = {}
        self._lock = threading.Lock()
        self._kws = None
        self._stop = False
        self.last_spot_candle_ts: int = 0     # watchdog input
        # ── WS_SELFHEAL_20261021 ── zombie-socket self-heal state
        self._tag = log_tag or "TMA_TICK"
        self._label = alert_label or "TMA"
        self._notify_cb = notify
        self._ws_connected = False
        self._last_spot_tick_ts = 0.0       # wall clock of the last NIFTY tick
        self._last_connect_ts = 0.0
        self._last_wd_action_ts = 0.0
        self._wd_strikes = 0
        self._heal_pending = False
        self.heal_count = 0

    # ── universe: NIFTY 50 index + weekly band around spot ──────────
    def resolve_universe(self, spot_ltp: float, expected_expiry_iso: str) -> int:
        from app.engine.pst.pst_live_warmup import resolve_nifty_index_token
        df = self.instruments_df
        self._spot_token = resolve_nifty_index_token(df)
        self._builders = {self._spot_token: CandleBuilder()}
        self._tok2sym = {}
        atm = int(round(spot_ltp / STRIKE_STEP) * STRIKE_STEP)
        lo, hi = atm - BAND_STRIKES * STRIKE_STEP, atm + BAND_STRIKES * STRIKE_STEP
        try:
            rows = df[(df["name"] == "NIFTY")
                      & (df["segment"] == "NFO-OPT")
                      & (df["expiry"].astype(str) == expected_expiry_iso)
                      & (df["strike"] >= lo) & (df["strike"] <= hi)]
            for _, r in rows.iterrows():
                tok = int(r["instrument_token"])
                sym = str(r["tradingsymbol"])
                self._builders[tok] = CandleBuilder()
                self._tok2sym[tok] = sym
                self.chain.put_meta(sym, float(r["strike"]),
                                    expected_expiry_iso,
                                    str(r["instrument_type"]), tok)
        except Exception as e:
            write_audit_log(f"[TMA_TICK] universe resolve failed: {e} — "
                            f"options unsubscribed (fail closed, spot only)")
        write_audit_log(f"[TMA_TICK] universe: spot + {len(self._tok2sym)} weekly "
                        f"contracts (ATM {atm}, band ±{BAND_STRIKES * STRIKE_STEP})")
        return len(self._tok2sym)

    # ── STRATEGY_INDEPENDENCE_20261015 BEGIN ──────────────────────────
    def ensure_symbols(self, symbols) -> List[str]:
        """Subscribe legs this engine must price even when they sit outside
        today's ±band (a positional carry after a gap). Builder + token map
        ONLY — deliberately no chain meta, so a carried strike never becomes
        a selection candidate (backtest band parity). Call before start()
        (on_connect subscribes every builder). Never raises."""
        added: List[str] = []
        df = self.instruments_df
        if df is None:
            return added
        have = set(self._tok2sym.values())
        for sym in symbols or []:
            if not sym or sym in have:
                continue
            try:
                rows = df[df["tradingsymbol"] == sym]
                if rows.empty:
                    write_audit_log(f"[TICK][CARRY] {sym} not in instruments — cannot subscribe")
                    continue
                tok = int(rows.iloc[0]["instrument_token"])
            except Exception as e:
                write_audit_log(f"[TICK][CARRY] {sym} lookup failed: {e!r}")
                continue
            with self._lock:
                self._builders.setdefault(tok, CandleBuilder())
                self._tok2sym[tok] = sym
            have.add(sym)
            added.append(sym)
        if added:
            write_audit_log(f"[TICK][CARRY] carried leg(s) outside today's band subscribed: {added}")
        return added

    def expiry_hint(self, expected_expiry_iso: str, today=None) -> str:
        """'computed expiry X, instruments list A, B, C' for empty-universe
        alerts (a holiday shift missing from the calendar reads at a glance)."""
        try:
            df = self.instruments_df
            t = (today or datetime.now().date()).isoformat()
            ex = sorted({str(x)[:10] for x in df[(df["name"] == "NIFTY")
                                                 & (df["segment"] == "NFO-OPT")]["expiry"]})
            ex = [e for e in ex if e >= t][:3]
            return (f"computed expiry {expected_expiry_iso}, instruments list "
                    f"{', '.join(ex) if ex else 'no NIFTY expiries'}")
        except Exception:
            return f"computed expiry {expected_expiry_iso}"
    # ── STRATEGY_INDEPENDENCE_20261015 END ────────────────────────────

    # ── websocket ────────────────────────────────────────────────────
    def start(self):
        from app.marketdata.rotating_ticker import RotatingKiteTicker
        kite = self.zm.get_kite()
        # ── TOKEN_ROTATE ── trade-kite creds → follow the trade token file
        self._kws = RotatingKiteTicker(kite.api_key, kite.access_token,
                                       kind="trade")
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = self._on_ws_close     # ── WS_SELFHEAL_20261021 ──
        self._kws.on_error = lambda ws, code, reason: write_audit_log(
            f"[TMA_TICK][WS] error code={code} reason={reason}")
        # ── WS_RECONNECT_VISIBILITY ── (2026-07-20: candle gaps while the
        # main feed stayed alive — every connect/drop cycle must be loud;
        # 6 concurrent KiteTickers now share one API key, over Zerodha's
        # documented 3-connection cap, so eviction churn is a live risk)
        self._kws.on_reconnect = lambda ws, attempts: write_audit_log(
            f"[TMA_TICK][WS] RECONNECTING attempt={attempts} — "
            f"resubscribe follows on_connect")
        self._kws.on_noreconnect = lambda ws: write_audit_log(
            "[TMA_TICK][WS][FATAL] reconnect retries EXHAUSTED — "
            "TMA candle stream dead until app restart")
        # ── WS_SELFHEAL_20261021 ── connect deferred to 09:13 IST + self-heal
        threading.Thread(target=self._deferred_connect, daemon=True,
                         name=f"{self._tag.lower()}-connect").start()
        threading.Thread(target=self._boundary_timer, daemon=True,
                         name="tma-minute-boundary").start()
        threading.Thread(target=self._ws_watchdog, daemon=True,
                         name=f"{self._tag.lower()}-ws-watchdog").start()

    def stop(self):
        self._stop = True
        try:
            if self._kws:
                self._kws.close()
        except Exception:
            pass

    def _on_connect(self, ws, response):
        self._ws_connected = True                 # ── WS_SELFHEAL_20261021 ──
        self._last_connect_ts = time.time()
        tokens = list(self._builders.keys())
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_LTP, tokens)
        write_audit_log(f"[TMA_TICK][WS] connected — {len(tokens)} tokens LTP mode")

    def _on_ticks(self, ws, ticks):
        now = int(time.time())
        pub = []    # ── TMA_TICK_LTP_PUBLISH_20261015 ── (symbol, ltp) for LTPStore
        with self._lock:
            for t in ticks:
                tok = t.get("instrument_token")
                ltp = t.get("last_price")
                if tok not in self._builders or not ltp:
                    continue
                ts = t.get("exchange_timestamp") or t.get("last_trade_time")
                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else now
                if tok == self._spot_token:
                    self._last_spot_tick_ts = time.time()   # ── WS_SELFHEAL_20261021 ──
                done = self._builders[tok].add_tick(float(ltp), epoch)
                if done:
                    self._store_candle(tok, done)
                if tok != self._spot_token:
                    sym = self._tok2sym.get(tok)
                    if sym:
                        pub.append((sym, float(ltp)))
        if pub:
            _publish_ltps(pub)   # outside the engine lock; never raises

    def _store_candle(self, tok: int, candle: dict):
        if tok == self._spot_token:
            self._spot_candles[int(candle["ts"])] = candle
            self.last_spot_candle_ts = int(candle["ts"])
        else:
            sym = self._tok2sym.get(tok)
            if sym:
                self.chain.put_candle(sym, candle)
        self._capture(tok, candle)

    def _capture(self, tok: int, candle: dict):
        if not self.capture_dir:
            return
        try:
            os.makedirs(self.capture_dir, exist_ok=True)
            day = datetime.utcfromtimestamp(candle["ts"] + IST).strftime("%Y-%m-%d")
            name = "SPOT" if tok == self._spot_token else self._tok2sym.get(tok, str(tok))
            with open(os.path.join(self.capture_dir, f"{day}.jsonl"), "a") as f:
                f.write(json.dumps({"sym": name, **candle}) + "\n")
        except Exception:
            pass   # capture must never disturb trading

    # ── WS_SELFHEAL_20261021 BEGIN ───────────────────────────────────────
    # 2026-10-21: the trade-key sockets (TMA, TMA2, VET) connected at 08:30,
    # took the flat pre-market snapshot until 08:35, then went silent with no
    # close/error event. The coordinator alerted at 09:16:01 but nothing
    # healed it. (a) never open the socket long before the first real tick;
    # (b) spot silence in the spot session → drop the protocol so KiteTicker
    # auto-reconnects. sendClose, NOT kws.close() (stop_retry kills the
    # auto-reconnect). Fully wrapped — cannot reach the candle path.
    WD_SILENT_S = 120
    WD_POLL_S = 30
    WD_COOLDOWN_S = 90
    WD_BACKOFF_S = 1800
    CONNECT_AT_MIN = 9 * 60 + 13

    @staticmethod
    def _ist_min(now: float) -> int:
        return int((now + IST) % 86400) // 60

    @staticmethod
    def _session_open_ts(now: float) -> int:
        day = (int(now + IST) // 86400) * 86400 - IST
        return day + (9 * 60 + 15) * 60

    @classmethod
    def _spot_session_at(cls, now: float) -> bool:
        """09:15–15:15 IST, weekday, not an NSE holiday — the window in which
        a silent NIFTY index is a fault (CAS_NOTE: after 15:15 it is not)."""
        ist = datetime.utcfromtimestamp(now + IST)
        if ist.weekday() >= 5:
            return False
        try:
            from app.utils.market_hours import is_nse_holiday
            if is_nse_holiday(ist.date()):
                return False
        except Exception:
            pass
        m = cls._ist_min(now)
        return (9 * 60 + 15) <= m < (15 * 60 + 15)

    def _connect_due(self, now: float) -> bool:
        return self._ist_min(now) >= self.CONNECT_AT_MIN

    def _deferred_connect(self):
        try:
            announced = False
            while not self._stop and not self._connect_due(time.time()):
                if not announced:
                    m = self._ist_min(time.time())
                    write_audit_log(
                        f"[{self._tag}][WS] connect deferred to 09:13 IST (now "
                        f"{m // 60:02d}:{m % 60:02d}) — fresh socket at the open, "
                        f"not one idling unproven through the pre-open")
                    announced = True
                time.sleep(5)
            if self._stop:
                return
            self._kws.connect(threaded=True)
        except Exception as e:
            write_audit_log(f"[{self._tag}][WS][FATAL] connect raised: {e!r}")

    def _on_ws_close(self, ws, code, reason):
        self._ws_connected = False
        write_audit_log(f"[{self._tag}][WS] closed code={code} reason={reason}")

    def _wd_notify(self, msg: str, severity: str) -> None:
        if not self._notify_cb:
            return
        try:
            self._notify_cb(msg, severity)
        except Exception:
            pass

    def _wd_check(self, now: float) -> str:
        """One watchdog decision. Returns a status string (tests read it)."""
        if self._heal_pending and self._last_spot_tick_ts > self._last_wd_action_ts:
            took = int(self._last_spot_tick_ts - self._last_wd_action_ts)
            self._heal_pending = False
            self._wd_strikes = 0
            self.heal_count += 1
            write_audit_log(f"[{self._tag}][WS][HEALED] NIFTY ticks back {took}s "
                            f"after the watchdog reconnect")
            self._wd_notify(f"{self._label}: spot feed back {took}s after an "
                            f"automatic WebSocket reconnect", "info")
        if self._stop:
            return "stopped"
        if not self._ws_connected:
            return "not_connected"
        if not self._spot_session_at(now):
            return "off_session"
        base = max(self._last_spot_tick_ts, self._last_connect_ts,
                   float(self._session_open_ts(now)))
        silent = now - base
        if silent < self.WD_SILENT_S:
            return "ok"
        if now - self._last_wd_action_ts < self.WD_COOLDOWN_S:
            return "cooldown"
        if self._wd_strikes >= 3 and silent < self.WD_BACKOFF_S:
            return "backoff"
        self._wd_strikes += 1
        self._last_wd_action_ts = now
        self._heal_pending = True
        write_audit_log(f"[{self._tag}][WS][WATCHDOG] connected but no NIFTY tick "
                        f"for {int(silent)}s in the spot session — forcing "
                        f"reconnect (strike {self._wd_strikes})")
        if self._wd_strikes == 1:
            self._wd_notify(f"{self._label}: no NIFTY tick for {int(silent)}s — "
                            f"forcing a WebSocket reconnect (auto-heal)", "warning")
        elif self._wd_strikes == 3:
            self._wd_notify(f"{self._label}: 3 automatic reconnects did not bring "
                            f"the spot feed back — restart the app", "error")
        ws = getattr(self._kws, "ws", None)
        if ws is None:
            write_audit_log(f"[{self._tag}][WS][WATCHDOG] no protocol object — skipped")
            return "no_protocol"
        ws.sendClose(1000, "tick-watchdog")
        return "dropped"

    def _ws_watchdog(self):
        while not self._stop:
            try:
                time.sleep(self.WD_POLL_S)
                self._wd_check(time.time())
            except Exception as e:
                try:
                    write_audit_log(f"[{self._tag}][WS][WATCHDOG] loop error: {e!r}")
                except Exception:
                    pass
    # ── WS_SELFHEAL_20261021 END ─────────────────────────────────────────

    # ── the minute boundary: finalize quiet builders, drive coordinator ──
    def _boundary_timer(self):
        GRACE = 1.5
        while not self._stop:
            now = time.time()
            next_b = (int(now) // 60 + 1) * 60 + GRACE
            time.sleep(max(0.2, next_b - now))
            boundary = (int(time.time()) // 60) * 60      # minute that just began
            completed = boundary - 60                      # minute that just ended
            with self._lock:
                for tok, b in self._builders.items():
                    done = b.force_finalize_before(boundary)
                    if done:
                        self._store_candle(tok, done)
                self.chain.now = completed
                spot_c = self._spot_candles.get(completed)
            try:
                self.on_minute_cb(completed, spot_c, self.chain)
            except Exception as e:
                write_audit_log(f"[TMA_TICK][FATAL] coordinator raised: {e}")