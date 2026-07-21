# backend/app/engine/pst/pst_tick_engine.py
#
# ── PST TICK ENGINE ── (Phase 1, Delivery 3 — own WebSocket, V3 doctrine)
#
# SEPARATE KiteTicker from V1/BB/HA/V3/V4 (one strategy family, one socket —
# house pattern). Responsibilities, deliberately minimal:
#   1. Subscribe NIFTY 50 index + the weekly option chain in a strike band
#      around spot (±BAND_STRIKES × 50), refreshed each morning — wide
#      enough that every contract under the premium cap has candles, which
#      is what selection parity with the backtest requires.
#   2. Build boundary-aligned 1m candles per token from ticks (IST minutes,
#      ts = bar-START epoch — the corpus shape). A minute with no ticks
#      produces NO candle (a gap — the managers' gap rules are parity-proven).
#   3. At each minute boundary (+grace), hand the finalized minute to the
#      PSTMinuteCoordinator, which owns all ordering.
#   4. Append every finalized candle to the day's capture file
#      (~/.scalp-app/pst_capture/YYYY-MM-DD.jsonl) for the end-of-day
#      replay audit (pst_daily_parity_report).
#
# NO trading logic lives here. Zombie-WS doctrine applies: connection
# status is not proof of ticks — the coordinator watchdog alerts if no
# spot candle forms for N minutes during session hours.

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
BAND_STRIKES = 20          # ±20 × 50 = ±1000 points around spot
STRIKE_STEP = 50


class CandleBuilder:
    """One instrument's tick→1m aggregation. ts = bar-START epoch."""

    def __init__(self):
        self.cur_ts: Optional[int] = None
        self.o = self.h = self.l = self.c = None

    def add_tick(self, ltp: float, epoch: int) -> Optional[dict]:
        """Returns the FINALIZED previous candle when the tick opens a new
        minute, else None."""
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
        """Finalize the open candle if its minute ended at/before boundary
        (no tick arrived to roll it). Used by the coordinator's timer."""
        if self.cur_ts is not None and self.cur_ts + 60 <= boundary_ts:
            done = {"ts": self.cur_ts, "open": self.o, "high": self.h,
                    "low": self.l, "close": self.c}
            self.cur_ts = None
            self.o = self.h = self.l = self.c = None
            return done
        return None

    # ── PST_EARLY_EXIT BEGIN ──
    def peek(self, bar_ts: int) -> Optional[dict]:
        """Running O/H/L/C of the IN-PROGRESS bar, iff that bar is the one
        starting at bar_ts. None otherwise (no open bar, or a different
        bar). SAFETY: the running high/low is by construction a SUBSET of
        the eventual finalized high/low, so a trigger evaluated against a
        peek would ALSO have triggered against the finalized bar — early
        exits can never be spurious, only earlier."""
        if self.cur_ts is None or self.cur_ts != int(bar_ts) or self.o is None:
            return None
        return {"ts": self.cur_ts, "open": self.o, "high": self.h,
                "low": self.l, "close": self.c, "partial": True}
    # ── PST_EARLY_EXIT END ──


class PSTChainStore:
    """Finalized candles per symbol per minute + metadata — the live
    implementation of the manager/harness chain-view duck-type."""

    def __init__(self):
        self._c: Dict[str, Dict[int, dict]] = {}
        self._meta: Dict[str, dict] = {}
        self.now: int = -1

    def put_meta(self, symbol: str, strike: float, expiry: str, side: str):
        self._meta[symbol] = {"strike": strike, "expiry": expiry, "side": side}

    def put_candle(self, symbol: str, candle: dict):
        self._c.setdefault(symbol, {})[int(candle["ts"])] = candle

    # chain-view API (identical semantics to the parity harness's view)
    def candle(self, symbol: str, ts: int) -> Optional[dict]:
        if ts > self.now:
            return None
        return self._c.get(symbol, {}).get(ts)

    # ── PST_EARLY_EXIT BEGIN ── live-only; the parity harness's ChainView
    # has no peek() and never needs one (harness runs PAPER, which returns
    # before any peek call). Wired by PSTTickEngine at construction.
    def peek(self, symbol: str, bar_ts: int) -> Optional[dict]:
        fn = getattr(self, "_peek_fn", None)
        if fn is None:
            return None
        try:
            return fn(symbol, int(bar_ts))
        except Exception:
            return None
    # ── PST_EARLY_EXIT END ──

    def symbols(self, side: str) -> List[str]:
        return [s for s, m in self._meta.items() if m["side"] == side]

    def meta(self, symbol: str) -> Optional[dict]:
        return self._meta.get(symbol)

    def reset_day(self):
        self._c = {}
        self.now = -1


class PSTTickEngine:
    """Own KiteTicker; feeds a PSTMinuteCoordinator. Construction never
    connects — call start() from the selection loop once Zerodha is ready."""

    def __init__(self, zerodha_manager, instruments_df,
                 on_minute_cb: Callable[[int, Optional[dict], "PSTChainStore"], None],
                 capture_dir: Optional[str] = None,
                 on_pre_boundary_cb: Optional[Callable] = None):
        self.zm = zerodha_manager
        self.instruments_df = instruments_df
        self.on_minute_cb = on_minute_cb
        # ── PST_EARLY_EXIT ── fires at (boundary − PRE_BOUNDARY_LEAD); None
        # disables the early path entirely (loop passes None when the
        # config flag is off).
        self.on_pre_boundary_cb = on_pre_boundary_cb
        self.chain = PSTChainStore()
        self.chain._peek_fn = self._peek_symbol
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
                                    expected_expiry_iso, str(r["instrument_type"]))
        except Exception as e:
            write_audit_log(f"[PST_TICK] universe resolve failed: {e} — "
                            f"options unsubscribed (fail closed, spot only)")
        write_audit_log(f"[PST_TICK] universe: spot + {len(self._tok2sym)} weekly "
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
            f"[PST_TICK][WS] closed code={code} reason={reason}")
        self._kws.on_error = lambda ws, code, reason: write_audit_log(
            f"[PST_TICK][WS] error code={code} reason={reason}")
        self._kws.connect(threaded=True)
        threading.Thread(target=self._boundary_timer, daemon=True,
                         name="pst-minute-boundary").start()
        # ── PST_EARLY_EXIT ── only when the loop supplied a callback
        if self.on_pre_boundary_cb is not None:
            threading.Thread(target=self._pre_boundary_timer, daemon=True,
                             name="pst-pre-boundary").start()
            write_audit_log("[PST_TICK][EARLY] pre-boundary exit thread started "
                            "(T-1.0s)")

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
        write_audit_log(f"[PST_TICK][WS] connected — {len(tokens)} tokens LTP mode")

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

    # ── PST_EARLY_EXIT BEGIN ──
    def _peek_symbol(self, symbol: str, bar_ts: int) -> Optional[dict]:
        """In-progress bar for an OPTION symbol (reverse token lookup)."""
        with self._lock:
            for tok, sym in self._tok2sym.items():
                if sym == symbol:
                    b = self._builders.get(tok)
                    return b.peek(bar_ts) if b is not None else None
        return None

    def _peek_spot(self, bar_ts: int) -> Optional[dict]:
        with self._lock:
            b = self._builders.get(self._spot_token)
            return b.peek(bar_ts) if b is not None else None

    def _pre_boundary_timer(self):
        """Fires PRE_BOUNDARY_LEAD seconds BEFORE each minute boundary, so a
        breached level is acted on at ~T-1s instead of ~T+61s. Never
        finalizes a candle; read-only on the builders."""
        LEAD = 1.0
        while not self._stop:
            now = time.time()
            next_fire = (int(now) // 60 + 1) * 60 - LEAD
            if next_fire <= now:
                next_fire += 60
            time.sleep(max(0.05, next_fire - now))
            if self._stop:
                return
            in_progress = (int(time.time() + LEAD) // 60) * 60
            try:
                spot_pk = self._peek_spot(in_progress)
                self.chain.now_partial = in_progress
                self.on_pre_boundary_cb(in_progress, spot_pk, self.chain)
            except Exception as e:
                write_audit_log(f"[PST_TICK][EARLY] pre-boundary raised: {e}")
    # ── PST_EARLY_EXIT END ──

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
                write_audit_log(f"[PST_TICK][FATAL] coordinator raised: {e}")