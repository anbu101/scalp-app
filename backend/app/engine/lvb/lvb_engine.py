# backend/app/engine/lvb/lvb_engine.py
#
# ── LVB_V1_20261023 ── "Tripwire" live day loop.
#
# Shape: EmcEngine's — own daemon thread, REST only (NO new KiteTicker: the
# data api_key is already past Kite's socket budget). One tick() per second:
#
#   1. exits — while positions are open, ONE batched kite.ltp() every
#      EXIT_POLL_S for every held option + the index of each held position;
#      SL (option LTP) then TP (spot) per position; then each position's own
#      EOD minute; then any queued manual square-off the broker deferred.
#   2. housekeeping — ARMED conditions whose EOD minute has passed → EXPIRED.
#   3. bars — for each instrument with ARMED conditions, after each 5m close
#      (+SETTLE_S) read today's kite-historical 5m index candles (exchange-
#      true bars) and judge every NEW completed bar, strictly in order.
#      A bar judged more than LATE_S after it closed (restart, data stall)
#      never opens a trade — the condition stays ARMED and the miss is
#      alerted and noted, so a restart can't fire a stale breakout.
#
# Entries: weekly chain of the breakout side from the instrument dump
# (nearest listed expiry, ±20 strikes), one batched LTP read, highest
# premium inside the band, then the manager buys (paper: that LTP; live:
# marketable limit via the executor).

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from app.engine.lvb.lvb_core import (INSTRUMENTS, SESSION_OPEN_MIN, BAR_S, breakout, reference_for,
                                     condition_sees_bar, target_spot, target_already_past, chain_rows,
                                     pick_option, hm_to_min, minute_of, day_start_epoch_of, min_to_hm)

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:                                        # standalone tests
    def write_audit_log(msg):                              # type: ignore
        print(msg)

IST = 5 * 3600 + 30 * 60
SETTLE_S = 5                  # let Kite publish the closed 5m candle
LATE_S = 90                   # a breakout judged later than this is never traded
SPOT_WAIT_S = 60              # alert if the expected candle is still missing
FETCH_GAP_S = 5               # min seconds between candle reads per instrument
EXIT_POLL_S = 2               # quote cadence while positions are open (Kite REST budget)
MARKS_SAVE_S = 15             # persist marks for the page / restarts
WINDOW_FROM = 9 * 60 + 14
WINDOW_TO = 15 * 60 + 31


def _naive_ist(ts: int) -> datetime:
    return datetime.utcfromtimestamp(int(ts) + IST)


def _day_str(ts: int) -> str:
    return _naive_ist(ts).date().isoformat()


class LvbFeed:
    """All broker data access (injectable for tests)."""

    def __init__(self, kite_fn: Callable, rows_fn: Optional[Callable] = None):
        self.kite_fn = kite_fn
        self.rows_fn = rows_fn
        self._rows_day = None
        self._rows: List[dict] = []

    def kite(self):
        try:
            return self.kite_fn()
        except Exception:
            return None

    def bars_5m(self, instrument: str, now: int) -> List[dict]:
        """Today's COMPLETED 5m index candles, oldest first."""
        k = self.kite()
        if k is None:
            raise RuntimeError("data kite unavailable")
        ds = day_start_epoch_of(now)
        frm = _naive_ist(ds + SESSION_OPEN_MIN * 60)
        rows = k.historical_data(instrument_token=INSTRUMENTS[instrument]["index_token"], from_date=frm,
                                 to_date=_naive_ist(now), interval="5minute") or []
        out = []
        for r in rows:
            d = r["date"]
            ts = int(d.timestamp()) if hasattr(d, "timestamp") else int(d)
            ts -= ts % 60
            if ts + BAR_S <= now and day_start_epoch_of(ts) == ds:
                out.append({"ts": ts, "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"])})
        out.sort(key=lambda c: c["ts"])
        return out

    def ltp(self, keys: List[str]) -> Dict[str, float]:
        """{'NFO:SYM': px, 'NSE:NIFTY 50': px, ...}; missing keys omitted."""
        k = self.kite()
        if k is None or not keys:
            return {}
        q = k.ltp(list(dict.fromkeys(keys))) or {}
        out = {}
        for key, v in q.items():
            px = float((v or {}).get("last_price") or 0)
            if px > 0:
                out[key] = px
        try:
            from app.marketdata.ltp_store import LTPStore
            for key, px in out.items():
                if key.startswith(("NFO:", "BFO:")):
                    LTPStore.update(key.split(":", 1)[1], px)
        except Exception:
            pass
        return out

    def rows(self, today: date) -> List[dict]:
        """Instrument-dump rows for NIFTY (NFO) + SENSEX (BFO) options, cached per day."""
        if self.rows_fn is not None:
            return self.rows_fn()
        if self._rows_day == today and self._rows:
            return self._rows
        from app.fetcher.zerodha_instruments import load_instruments_df
        k = self.kite()
        df = load_instruments_df(getattr(k, "api_key", None), getattr(k, "access_token", None))
        if df is None or df.empty:
            raise RuntimeError("instrument dump empty")
        segs = {s["segment"] for s in INSTRUMENTS.values()}
        names = {s["name"] for s in INSTRUMENTS.values()}
        sub = df[df["segment"].isin(segs) & df["name"].astype(str).str.upper().isin(names)]
        self._rows = sub[["tradingsymbol", "instrument_token", "name", "exchange", "segment", "instrument_type",
                          "strike", "expiry", "lot_size"]].to_dict("records")
        self._rows_day = today
        return self._rows


class LvbEngine:
    def __init__(self, manager, repo, broker_manager=None, *, feed: Optional[LvbFeed] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 trading_day_fn: Optional[Callable[[date], bool]] = None):
        self.gm = manager
        self.repo = repo
        self.broker = broker_manager
        self.feed = feed or LvbFeed(lambda: broker_manager.get_data_kite())
        self.clock, self.sleep = clock, sleep
        self.trading_day_fn = trading_day_fn
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_poll_ts: Optional[int] = None
        self.pending_close: Dict[int, str] = {}
        self._reset_day(None)

    def _reset_day(self, day: Optional[date]):
        self.day = day
        self.day_s = day.isoformat() if day else None
        self.ds: Optional[int] = None
        self.bars: Dict[str, List[dict]] = {}
        self.done_ts: Dict[str, int] = {}
        self.last_fetch: Dict[str, int] = {}
        self.spot: Dict[str, tuple] = {}
        self.alerted: set = set()
        self._last_exit_poll = 0
        self._last_marks_save = 0
        self._chain_cache: Dict[tuple, tuple] = {}
        self.booted = False
        self.data_ok: Dict[str, bool] = {}

    def _alert_once(self, key: str, code: str, msg: str, severity: str = "warning"):
        if key in self.alerted:
            return
        self.alerted.add(key)
        self.gm._alert(code, msg, severity)

    def _is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        try:
            if self.trading_day_fn is not None:
                return bool(self.trading_day_fn(d))
            from app.utils.market_hours import is_trading_day
            return bool(is_trading_day(d))
        except Exception:
            return True

    # ── thread ──
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="lvb_v1_engine", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        write_audit_log("[LVB][ENGINE] loop up")
        while self._running:
            try:
                self.tick(self.clock())
            except Exception as e:
                write_audit_log(f"[LVB][ENGINE][ERR] {e!r}")
            self.sleep(1.0)
        write_audit_log("[LVB][ENGINE] loop down")

    # ── one step ──
    def tick(self, now: float) -> None:
        now_i = int(now)
        self.last_poll_ts = now_i
        d = _naive_ist(now_i).date()
        hm = minute_of(now_i)
        if not self._is_trading_day(d) or hm < WINDOW_FROM or hm > WINDOW_TO:
            return
        if self.day != d:
            self._reset_day(d)
            self.ds = day_start_epoch_of(now_i)
        if not self.booted:
            self._boot(now_i)
        # 1 ── exits
        if self.gm.pos and now_i - self._last_exit_poll >= EXIT_POLL_S:
            self._last_exit_poll = now_i
            self._poll_exits(now_i, hm)
        # 2 ── housekeeping
        try:
            for c in self.repo.armed(self.day_s):
                eod = hm_to_min(c["eod"])
                if eod is not None and hm >= eod:
                    if self.repo.set_status(int(c["id"]), "EXPIRED", f"EOD {c['eod']} passed without a breakout"):
                        write_audit_log(f"[LVB][EXPIRED] #{c['id']} {c['instrument']} — EOD {c['eod']}")
        except Exception as e:
            self._alert_once(f"house{hm // 15}", "REPO_FAIL", f"{e!r}", "error")
        # 3 ── bars
        try:
            armed = self.repo.armed(self.day_s)
        except Exception:
            armed = []
        for inst in sorted({c["instrument"] for c in armed}):
            self._bars_step(inst, now_i)

    def _boot(self, now_i: int) -> None:
        self.booted = True
        try:
            n = self.repo.expire_before(self.day_s)
            if n:
                write_audit_log(f"[LVB][BOOT] {n} condition(s) from earlier days expired")
            r = self.gm.resume(now_i)
            write_audit_log(f"[LVB][BOOT] {self.day_s} armed={len(self.repo.armed(self.day_s))} "
                            f"open={r} mode={self.gm.raw_mode()}")
        except Exception as e:
            self.gm._alert("BOOT_FAIL", f"{e!r} — open trades may be unmanaged; check the Levels page", "critical")

    # ── exits ──
    def _poll_exits(self, now_i: int, hm: int) -> None:
        pos = list(self.gm.pos.values())
        keys = [f"{p.exchange}:{p.symbol}" for p in pos] + \
               sorted({INSTRUMENTS[p.instrument]["index_key"] for p in pos})
        try:
            q = self.feed.ltp(keys)
        except Exception as e:
            self._alert_once(f"quote{now_i // 120}", "QUOTE_FAIL", f"{e!r} — SL/TP blind until quotes resume",
                             "error")
            q = {}
        for inst, spec in INSTRUMENTS.items():
            if spec["index_key"] in q:
                self.spot[inst] = (q[spec["index_key"]], now_i)
        for p in pos:
            ltp = q.get(f"{p.exchange}:{p.symbol}")
            spot = q.get(INSTRUMENTS[p.instrument]["index_key"])
            if self.gm.check(p.cid, ltp, spot):
                self.pending_close.pop(p.cid, None)
                continue
            if p.cid not in self.gm.pos:
                continue
            if p.eod_min is not None and hm >= p.eod_min:
                if self.gm.close(p.cid, "EOD", ltp=ltp, spot=spot) is not None:
                    self.pending_close.pop(p.cid, None)
                    continue
            why = self.pending_close.get(p.cid)
            if why and self.gm.close(p.cid, why, ltp=ltp, spot=spot) is not None:
                self.pending_close.pop(p.cid, None)
        for cid in [c for c in self.pending_close if c not in self.gm.pos]:
            self.pending_close.pop(cid, None)
        if now_i - self._last_marks_save >= MARKS_SAVE_S:
            self._last_marks_save = now_i
            for p in list(self.gm.pos.values()):
                try:
                    self.repo.set_marks(p.cid, p.mark, p.mark_spot)
                except Exception:
                    pass

    def request_close(self, cid: int, reason: str = "MANUAL") -> dict:
        """Manual square-off (API thread). Tries now; if the broker path
        defers (LIVE backoff) the engine retries every poll."""
        p = self.gm.pos.get(int(cid))
        if p is None:
            return {"ok": False, "error": "no open position for this condition"}
        ltp = None
        spot = None
        try:
            q = self.feed.ltp([f"{p.exchange}:{p.symbol}", INSTRUMENTS[p.instrument]["index_key"]])
            ltp = q.get(f"{p.exchange}:{p.symbol}")
            spot = q.get(INSTRUMENTS[p.instrument]["index_key"])
        except Exception:
            pass
        px = self.gm.close(int(cid), reason, ltp=ltp, spot=spot)
        if px is None and int(cid) in self.gm.pos:
            self.pending_close[int(cid)] = reason
            return {"ok": True, "queued": True, "message": "broker deferred the exit — retrying every 2 s"}
        return {"ok": True, "exit_price": px}

    def request_close_all(self, reason: str = "MANUAL") -> dict:
        out = {"closed": 0, "queued": 0}
        for cid in list(self.gm.pos):
            r = self.request_close(cid, reason)
            if r.get("queued"):
                out["queued"] += 1
            elif r.get("ok"):
                out["closed"] += 1
        return out

    # ── bars ──
    def _bars_step(self, inst: str, now_i: int) -> None:
        open_ts = self.ds + SESSION_OPEN_MIN * 60
        k = (now_i - open_ts) // BAR_S                    # bars fully closed so far (if >= 1)
        if k < 1:
            return
        k = min(k, 75)                                   # 09:15 … 15:25 → 75 bars
        expect_ts = open_ts + (k - 1) * BAR_S            # newest bar that should be complete
        close_at = expect_ts + BAR_S
        if self.done_ts.get(inst, -1) >= expect_ts:
            return
        if now_i < close_at + SETTLE_S or now_i - self.last_fetch.get(inst, 0) < FETCH_GAP_S:
            return
        self.last_fetch[inst] = now_i
        try:
            bars = self.feed.bars_5m(inst, now_i)
            self.data_ok[inst] = True
        except Exception as e:
            self.data_ok[inst] = False
            self._alert_once(f"bars{inst}{expect_ts}", "CANDLE_FETCH", f"{inst} 5m candles: {e!r}", "error")
            return
        self.bars[inst] = bars
        if bars:
            self.spot.setdefault(inst, (bars[-1]["close"], bars[-1]["ts"] + BAR_S))
            if self.spot[inst][1] < bars[-1]["ts"] + BAR_S:
                self.spot[inst] = (bars[-1]["close"], bars[-1]["ts"] + BAR_S)
        done = self.done_ts.get(inst, -1)
        for i, b in enumerate(bars):
            if b["ts"] <= done:
                continue
            self._on_bar(inst, bars, i, now_i)
            self.done_ts[inst] = b["ts"]
        if not bars or bars[-1]["ts"] < expect_ts:
            if now_i - close_at >= SPOT_WAIT_S:
                self._alert_once(f"miss{inst}{expect_ts}", "CANDLE_MISSING",
                                 f"{inst} 5m candle {min_to_hm(minute_of(expect_ts))} still missing "
                                 f"{now_i - close_at}s after its close — breakouts are judged when it arrives "
                                 f"(and are not traded if later than {LATE_S}s)", "error")

    def _on_bar(self, inst: str, bars: List[dict], i: int, now_i: int) -> None:
        b = bars[i]
        ref = reference_for(bars, i)
        close = float(b["close"])
        cts = b["ts"] + BAR_S
        late = now_i - cts > LATE_S
        at = min_to_hm(minute_of(cts))
        try:
            conds = [c for c in self.repo.armed(self.day_s) if c["instrument"] == inst]
        except Exception as e:
            self._alert_once(f"armed{cts}", "REPO_FAIL", f"{e!r}", "error")
            return
        for c in conds:
            if not condition_sees_bar(c, b):
                continue
            side = breakout(ref, close, c.get("upper"), c.get("lower"))
            if side is None:
                continue
            cid = int(c["id"])
            lvl = c["upper"] if side == "CE" else c["lower"]
            if late:
                msg = (f"{inst} closed {close:g} {'above' if side == 'CE' else 'below'} {lvl:g} at {at}, judged "
                       f"{now_i - cts}s late (app restart / data delay) — NOT traded; still armed")
                self.gm._alert("LATE_BREAKOUT", f"#{cid} {msg}", "warning")
                self.repo.note(cid, msg)
                continue
            tp = target_spot(side, c.get("upper"), c.get("lower"), float(c.get("tp_pts") or 0))
            if target_already_past(side, close, tp):
                msg = (f"{side} breakout at {at} closed {close:g}, already beyond the spot target {tp:g} — "
                       "not traded; still armed")
                self.gm._alert("TARGET_PAST", f"#{cid} {msg}", "info")
                self.repo.note(cid, msg)
                continue
            write_audit_log(f"[LVB][BREAKOUT] #{cid} {inst} {side}: {at} close {close:g} vs level {lvl:g} "
                            f"(ref {ref:g})")
            self._enter(c, side, b, close, tp, now_i)

    def _enter(self, c: dict, side: str, b: dict, close: float, tp: Optional[float], now_i: int) -> None:
        inst = c["instrument"]
        cid = int(c["id"])
        spec = INSTRUMENTS[inst]
        key = (inst, side, b["ts"])
        if key not in self._chain_cache:
            try:
                expiry, cands = chain_rows(self.feed.rows(self.day), inst, side, close, self.day)
            except Exception as e:
                self.gm._refuse(cid, "CHAIN_FAIL", f"{inst} {side} chain: {e!r}")
                return
            if not cands:
                self.gm._refuse(cid, "CHAIN_EMPTY", f"no listed {inst} {side} contracts near {close:g}")
                return
            try:
                q = self.feed.ltp([f"{x['exchange']}:{x['sym']}" for x in cands] + [spec["index_key"]])
            except Exception as e:
                self.gm._refuse(cid, "QUOTE_FAIL", f"{inst} {side} chain quotes: {e!r}")
                return
            ltps = {x["sym"]: q.get(f"{x['exchange']}:{x['sym']}") or 0 for x in cands}
            self._chain_cache = {key: (expiry, cands, ltps, q.get(spec["index_key"]))}
        expiry, cands, ltps, spot_now = self._chain_cache[key]
        pick = pick_option(cands, ltps, float(c["prem_min"]), float(c["prem_max"]), close)
        if pick is None:
            near = sorted((v for v in ltps.values() if v), key=lambda v: min(abs(v - c["prem_min"]),
                                                                              abs(v - c["prem_max"])))[:1]
            self.gm._refuse(cid, "NO_STRIKE", f"no {inst} {side} ({expiry}) priced ₹{c['prem_min']:g}–"
                            f"{c['prem_max']:g}" + (f" (nearest ₹{near[0]:g})" if near else ""), "warning")
            return
        self.gm.open_position(c, pick, side=side, trigger_bar_ts=int(b["ts"]), trigger_close=close,
                              tp_spot=tp, entry_spot=spot_now or close)

    # ── panel ──
    def status(self) -> dict:
        return {"day": self.day_s, "booted": self.booted, "heartbeat_ts": self.last_poll_ts,
                "spot": {k: {"ltp": v[0], "ts": v[1]} for k, v in self.spot.items()},
                "last_bar": {k: (min_to_hm(minute_of(v[-1]["ts"] + BAR_S)) if v else None)
                             for k, v in self.bars.items()},
                "data_ok": dict(self.data_ok), "pending_close": dict(self.pending_close)}
