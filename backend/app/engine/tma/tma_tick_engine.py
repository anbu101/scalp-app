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
                 capture_dir: Optional[str] = None):
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

    # ── websocket ────────────────────────────────────────────────────
    def start(self):
        from kiteconnect import KiteTicker
        kite = self.zm.get_kite()
        self._kws = KiteTicker(kite.api_key, kite.access_token)
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = lambda ws, code, reason: write_audit_log(
            f"[TMA_TICK][WS] closed code={code} reason={reason}")
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
        self._kws.connect(threaded=True)
        threading.Thread(target=self._boundary_timer, daemon=True,
                         name="tma-minute-boundary").start()

    def stop(self):
        self._stop = True
        try:
            if self._kws:
                self._kws.close()
        except Exception:
            pass

    def _on_connect(self, ws, response):
        tokens = list(self._builders.keys())
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_LTP, tokens)
        write_audit_log(f"[TMA_TICK][WS] connected — {len(tokens)} tokens LTP mode")

    def _on_ticks(self, ws, ticks):
        now = int(time.time())
        with self._lock:
            for t in ticks:
                tok = t.get("instrument_token")
                ltp = t.get("last_price")
                if tok not in self._builders or not ltp:
                    continue
                ts = t.get("exchange_timestamp") or t.get("last_trade_time")
                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else now
                done = self._builders[tok].add_tick(float(ltp), epoch)
                if done:
                    self._store_candle(tok, done)

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