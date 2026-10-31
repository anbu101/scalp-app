# backend/app/engine/emc/emc_engine.py
#
# ── EMC_V1 ENGINE ── live day loop for "Ember". Fence: EMC_V1_LIVE_20261011
#
# Shape: OrbEngine's (own daemon thread, REST only — NO new KiteTicker; the
# fleet is already over Kite's socket budget), with the decision taken by the
# backtest's own EmcDayCore and the indicators re-computed by the backtest's
# own functions (emc_live_core). One tick() per second does, in order:
#
#   1. intraminute exits — while a position is open, quote the held option
#      and let the manager take SL / TP at market (paper: at that LTP);
#   2. EOD — at the square-off minute flatten, then end the session in the
#      core (expire any pending setup);
#   3. decisions — every COMPLETED minute k, strictly in order, never skipped:
#      freeze kite-historical 1m spot candles (exchange-true bars, the source
#      the backtest corpus is built from) → recompute spot crosses → refresh
#      the pending setup's option candles → EmcDayCore.on_minute_close(k, x).
#      Selection reads a fresh weekly-chain snapshot; the entry is a market
#      buy (paper: a fresh LTP) right after minute k closes — the backtest's
#      "next minute open" model.
#
# Fail-closed rules (each one alerts):
#   * short spot warm-up / config the runner rejects / unsupported live knob
#     → the DAY is refused (an adopted open position is still managed);
#   * a restated spot cross → FROZEN (no new setups; position managed by
#     SL / TP / EOD only);
#   * a decision processed more than LATE_S after its minute closed (data
#     stall, restart) → no entry and no selection on that minute;
#   * a pending setup is NOT resurrected after a restart (its chain snapshot
#     is gone) — an open position IS (adopted from paper_trades).

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log
from app.backtest.emc.emc_v1_engine import EmcDayCore, DayTrade, SESSION_OPEN_MIN
from app.engine.emc.emc_live_core import (LiveSpotCrosses, LiveOptionTrack, live_params,
                                          option_warm_from_sessions, day_start_epoch_of, minute_of)
from app.engine.emc.emc_manager import EmcManager

IST = 5 * 3600 + 30 * 60
SPOT_TOKEN = 256265                  # NSE:NIFTY 50
SESSION_CLOSE_MIN_EXCL = 15 * 60 + 30   # ── EMC_WARM_CLIP_20261030 ── last session minute is 15:29
SETTLE_S = 3                         # let the minute's candle publish before reading it
SPOT_WAIT_S = 90                     # give up waiting for one missing spot minute
LATE_S = 60                          # a decision older than this never opens a trade
BOOT_RETRY_S = 60
EXIT_POLL_S = 2                      # SL/TP quote cadence while a position is open (Kite quote budget)


def _naive_ist(ts: int) -> datetime:
    return datetime.utcfromtimestamp(int(ts) + IST)


class EmcFeed:
    """All broker data access (injectable for tests)."""

    def __init__(self, kite_fn: Callable, chain_fn: Optional[Callable] = None):
        self.kite_fn = kite_fn
        self.chain_fn = chain_fn

    def kite(self):
        try:
            return self.kite_fn()
        except Exception:
            return None

    def _hist(self, token: int, frm: datetime, to: datetime, interval: str = "minute") -> List[dict]:
        k = self.kite()
        if k is None:
            raise RuntimeError("data kite unavailable")
        rows = k.historical_data(instrument_token=int(token), from_date=frm, to_date=to, interval=interval) or []
        out = []
        for r in rows:
            d = r["date"]
            ts = int(d.timestamp()) if hasattr(d, "timestamp") else int(d)
            out.append({"ts": ts - ts % 60, "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"])})
        out.sort(key=lambda c: c["ts"])
        return out

    def prior_days(self, today: date, n: int) -> List[date]:
        k = self.kite()
        if k is None:
            return []
        rows = k.historical_data(instrument_token=SPOT_TOKEN, from_date=today - timedelta(days=30),
                                 to_date=today - timedelta(days=1), interval="day") or []
        days = sorted({(r["date"].date() if hasattr(r["date"], "date") else r["date"]) for r in rows})
        days = [d for d in days if d < today]
        return days[-n:] if n else days

    def sessions(self, token: int, days: List[date]) -> List[Tuple[int, List[dict]]]:
        """[(day_start_epoch, candles)] for exactly `days` (one call)."""
        if not days:
            return []
        frm = datetime(days[0].year, days[0].month, days[0].day, 9, 0)
        to = datetime(days[-1].year, days[-1].month, days[-1].day, 15, 45)
        rows = self._hist(token, frm, to)
        want = {int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST for d in days}
        by: Dict[int, List[dict]] = {}
        for c in rows:
            ds = day_start_epoch_of(c["ts"])
            # ── EMC_WARM_CLIP_20261030 ── session minutes only (09:15–15:29),
            # as the corpus / recorder hold them. Kite also returns prints
            # after 15:29 for options; resample_session clips only the pre-
            # open side, so each warm session came out 77 five-minute bars
            # instead of 75 ("option warm bars 154 from 2 session(s)", 30 Oct)
            # — a different EMA than the backtest's and an option trigger one
            # minute off (Ember 11:07 live vs 11:06 replayed).
            if ds in want and SESSION_OPEN_MIN <= minute_of(c["ts"]) < SESSION_CLOSE_MIN_EXCL:
                by.setdefault(ds, []).append(c)
        return [(ds, by[ds]) for ds in sorted(by)]

    def today_completed(self, token: int, now: int) -> List[dict]:
        ds = day_start_epoch_of(now)
        frm = _naive_ist(ds + SESSION_OPEN_MIN * 60)
        rows = self._hist(token, frm, _naive_ist(now))
        return [c for c in rows if c["ts"] + 60 <= now and day_start_epoch_of(c["ts"]) == ds]

    def chain(self):
        if self.chain_fn is not None:
            return self.chain_fn()
        from app.engine.ic.ic_selection import snapshot_weekly_chain
        k = self.kite()
        if k is None:
            return None, [], {}
        return snapshot_weekly_chain(k, getattr(k, "api_key", None), getattr(k, "access_token", None))

    def quote(self, symbols: List[str]) -> Dict[str, float]:
        k = self.kite()
        if k is None or not symbols:
            return {}
        q = k.quote([f"NFO:{s}" for s in symbols]) or {}
        out = {s: float((q.get(f"NFO:{s}") or {}).get("last_price") or 0) for s in symbols}
        try:
            from app.marketdata.ltp_store import LTPStore
            for s, v in out.items():
                if v > 0:
                    LTPStore.update(s, v)
        except Exception:
            pass
        return out


class EmcEngine:
    def __init__(self, manager: EmcManager, broker_manager=None, *, feed: Optional[EmcFeed] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 trading_day_fn: Optional[Callable[[date], bool]] = None,
                 expected_expiry_fn: Optional[Callable[[date], date]] = None):
        self.gm = manager
        self.broker = broker_manager
        self.feed = feed or EmcFeed(lambda: broker_manager.get_data_kite())
        self.clock, self.sleep = clock, sleep
        self.trading_day_fn = trading_day_fn
        self.expected_expiry_fn = expected_expiry_fn
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.gm.on_closed = self._on_manager_close
        self._reset_day(None)
        self.last_poll_ts: Optional[int] = None

    # ── day state ──
    def _reset_day(self, day: Optional[date]):
        self.day = day
        self.ds: Optional[int] = None
        self.ready = False
        self.refused: Optional[str] = None
        self.params = None
        self.mcfg: dict = {}
        self.spot: Optional[LiveSpotCrosses] = None
        self.core: Optional[EmcDayCore] = None
        self.diag: dict = {}
        self.next_k = SESSION_OPEN_MIN
        self.finished = False
        self.prior_days: List[date] = []
        self.tracks: Dict[str, LiveOptionTrack] = {}
        self.chain_cache: Dict[int, tuple] = {}
        self.meta: Dict[str, dict] = {}
        self.pending_exit: Optional[str] = None
        self.boot_attempts = 0
        self.boot_last_try = 0.0
        self.boot_alerted = False
        self.alerted: set = set()
        self.now_i = 0
        self.last_cross: Optional[Tuple[int, int]] = None
        self.dte: Optional[int] = None
        self.dte_tag = "base"
        self._last_exit_poll = 0

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

    def _expected_expiry(self, d: date) -> date:
        if self.expected_expiry_fn is not None:
            return self.expected_expiry_fn(d)
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
        return expected_expiry_for_day(d)

    # ── thread ──
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="emc_v1_engine", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        write_audit_log("[EMC][ENGINE] loop up")
        while self._running:
            try:
                self.tick(self.clock())
            except Exception as e:
                write_audit_log(f"[EMC][ENGINE][ERR] {e!r}")
            self.sleep(1.0)
        write_audit_log("[EMC][ENGINE] loop down")

    # ── one step ──
    def tick(self, now: float) -> None:
        now_i = int(now)
        self.now_i = now_i
        self.last_poll_ts = now_i
        d = _naive_ist(now_i).date()
        hm = minute_of(now_i)
        if not self._is_trading_day(d) or hm < 9 * 60 + 5 or hm > 15 * 60 + 31:
            return
        if self.day != d:
            self._reset_day(d)
            self.ds = day_start_epoch_of(now_i)
        if not self.ready:
            self._try_boot(now_i)
        # 1 ── intraminute exits (also retries a deferred XOVER)
        if self.gm.pos is not None and now_i - self._last_exit_poll >= EXIT_POLL_S:
            self._last_exit_poll = now_i
            if self.pending_exit and self.gm.close_trade(reason=self.pending_exit,
                                                       ltp=self._ltp(self.gm.pos.symbol)) is not None:
                self.pending_exit = None
            elif self.gm.pos is not None:
                self.gm.check_exits(self._ltp(self.gm.pos.symbol))
        # 2 ── EOD (also on a refused day that adopted an open position)
        eod_min = self.params.eod_min if self.params is not None else 15 * 60 + 15
        if hm >= eod_min:
            if self.gm.pos is not None:
                self.gm.close_trade(reason="EOD", ltp=self._ltp(self.gm.pos.symbol))
            if self.gm.pos is None and not self.finished and self.core is not None:
                self.core.finish(None)
                self.finished = True
                write_audit_log(f"[EMC][DAY] done — entries {self.core.day_entries} losses "
                                f"{self.core.day_losses} diag {self.diag}")
            return
        if not self.ready:
            return
        # 3 ── decisions, completed minutes in order
        last_done = hm - 1
        while self.next_k <= min(last_done, self.params.eod_min - 1):
            if now_i < self._boundary(self.next_k) + SETTLE_S:
                break
            if not self._process_minute(self.next_k, now_i):
                break
            self.next_k += 1

    def _boundary(self, k: int) -> int:
        """Epoch at which minute k has closed."""
        return self.ds + (k + 1) * 60

    def _ltp(self, sym: str) -> Optional[float]:
        try:
            return self.feed.quote([sym]).get(sym) or None
        except Exception:
            return None

    # ── boot: config, warm-up, restart restore, backfill ──
    def _try_boot(self, now_i: int) -> None:
        if self.refused:
            return
        if self.boot_attempts and now_i - self.boot_last_try < BOOT_RETRY_S:
            return
        self.boot_attempts += 1
        self.boot_last_try = now_i
        hm = minute_of(now_i)
        cfg = self.gm.cfg() or {}
        if not cfg:
            return self._refuse("CFG_MISSING", "strategy config empty — loader path broken?", "critical")
        p, m, bad = live_params(cfg)
        if bad:
            return self._refuse("CFG_REFUSED", bad, "critical")
        # DTE × lots / skip-expiry — read once per session (ORB/VET convention)
        try:
            from app.engine.dte_live import resolve_day_lots
            lots, self.dte, self.dte_tag = resolve_day_lots(m, int(m.get("lots") or 1), self.day, "[EMC]")
        except Exception:
            lots, self.dte, self.dte_tag = int(m.get("lots") or 1), None, "unknown"
        if self.dte_tag == "skip":
            return self._refuse("DTE_SKIP", f"today is {self.dte}DTE — no EMC trading (dte_lot_mult)", "info")
        self.gm.day_lots = int(lots) if self.dte_tag == "scaled" else None
        if m.get("skip_expiry_day") and self._expected_expiry(self.day) == self.day:
            return self._refuse("EXPIRY_SKIP", "expiry day — skip_expiry_day is ON", "info")
        try:
            if not self.prior_days:
                self.prior_days = self.feed.prior_days(self.day, 12)
            need = int(m["warmup_sessions"])
            warm_days = self.prior_days[-need:]
            warm = self.feed.sessions(SPOT_TOKEN, warm_days)
        except Exception as e:
            warm, need = [], int(m.get("warmup_sessions") or 5)
            write_audit_log(f"[EMC][BOOT] warm-up fetch failed: {e!r}")
        spot = LiveSpotCrosses(fast=m["ema_fast"], slow=m["ema_slow"], tf=m["spot_tf"],
                               warm_sessions_1m=[c for _, c in warm], need_sessions=need,
                               first_candle_cross=m["first_candle_cross"])
        if not spot.warmup_ok():
            msg = f"spot warm-up {len(spot.warm_sessions)}/{need} sessions (attempt {self.boot_attempts})"
            write_audit_log(f"[EMC][BOOT] {msg} — retrying every {BOOT_RETRY_S}s")
            if hm >= 9 * 60 + 20 and not self.boot_alerted:
                self.boot_alerted = True
                self.gm._alert("WARMUP", f"{msg} at 09:20 — EMC is blind until it loads", "error")
            if hm >= 15 * 60:
                self._refuse("NO_WARMUP", "no spot warm-up by 15:00 — not trading today", "error")
            return
        self.params, self.mcfg, self.spot = p, m, spot
        self.diag = {}
        self.core = EmcDayCore(p=p, quotes_at=self._quotes_at, track_of=self._track_of, spot_at=self._spot_at,
                               net_of=self._net_of, fill_entry=self._fill_entry, fill_exit=self._fill_exit,
                               diag=self.diag)
        self._restore(now_i)
        # backfill today's completed minutes WITHOUT decisions
        try:
            for c in self.feed.today_completed(SPOT_TOKEN, now_i):
                self.spot.add(c)
        except Exception as e:
            write_audit_log(f"[EMC][BOOT] backfill failed: {e!r} — decisions start as minutes arrive")
        if self.spot.today:
            self.spot.recompute()                      # seeds the prefix guard
            self.next_k = (self.spot.last_minute() or SESSION_OPEN_MIN) + 1
            write_audit_log(f"[EMC][BOOT] backfilled {len(self.spot.today)} spot minutes — decisions resume at "
                            f"{self.next_k // 60:02d}:{self.next_k % 60:02d} (earlier crosses are NOT traded)")
        else:
            self.next_k = SESSION_OPEN_MIN
        self.ready = True
        write_audit_log(f"[EMC][BOOT] armed: mode={self.gm.mode()} lots={self.gm.qty()[0]} ({self.dte_tag}"
                        f"{'' if self.dte is None else f', {self.dte}DTE'}) EMA{m['ema_fast']}/{m['ema_slow']} "
                        f"band ₹{m['sel_prem_min']:g}-{m['sel_prem_max']:g} SL {m['sl_pts']:g} TP {m['tp_pts']:g} "
                        f"cutoff {m['entry_cutoff']} EOD {m['eod_square_off']} loss-cap {m['max_losses_per_day']} "
                        f"no-arm {m.get('arm_skip_from') or '-'}–{m.get('arm_skip_to') or '-'}")

    def _refuse(self, code: str, msg: str, severity: str) -> None:
        self.refused = f"{code}: {msg}"
        self.gm.day_stats["refused"] = self.refused
        self.gm._alert(code, f"day refused — {msg}", severity)
        # an open position from before still needs managing
        try:
            if self.gm.pos is None:
                for r in self.gm.open_rows_any_day():
                    if day_start_epoch_of(int(r.get("candle_ts") or 0)) == day_start_epoch_of(self.now_i):
                        self.gm.resume_row(r)
                        break
        except Exception:
            pass

    def _restore(self, now_i: int) -> None:
        """Restart: stale prior-day rows, today's counters, today's open row."""
        ds = self.ds
        try:
            for r in self.gm.open_rows_any_day():
                if day_start_epoch_of(int(r.get("candle_ts") or 0)) != ds:
                    self.gm.mark_stale(r, "intraday strategy, crash before EOD")
            rows = self.gm.today_rows(ds)
        except Exception as e:
            self.gm._alert("RESUME_FAIL", f"{e!r} — day budget starts at 0", "error")
            return
        losses = 0
        open_row = None
        for r in rows:
            if r.get("exit_price") is None:
                open_row = r
                continue
            if self.gm.net_of(r["entry_price"], r["exit_price"], int(r["qty"])) < 0:
                losses += 1
        c = self.core
        c.day_entries = len(rows)
        c.day_losses = losses
        if c.p.max_losses and losses >= c.p.max_losses:
            c.halted = True
        self.gm.day_stats["entries"] = len(rows)
        if open_row is not None and self.gm.pos is None:
            self.gm.resume_row(open_row)
        if self.gm.pos is not None:
            pos = self.gm.pos
            t = DayTrade(side=pos.side, sym=pos.symbol, strike=None, sel_min=minute_of(pos.decision_ts or now_i),
                         sel_px=pos.entry_px, entry_min=minute_of(pos.entry_ts or now_i), entry_px=pos.entry_px,
                         sl=pos.sl, tp=pos.tp)
            c.adopt_position(t, check_from=minute_of(now_i))
        if rows:
            write_audit_log(f"[EMC][RESUME] today: {len(rows)} entries, {losses} losing closed"
                            f"{', HALTED' if c.halted else ''}{', 1 open' if self.gm.pos else ''} "
                            "(a pending setup is never resurrected)")

    # ── per minute ──
    def _process_minute(self, k: int, now_i: int) -> bool:
        sp = self.spot
        if (sp.last_minute() or -1) < k:
            try:
                for c in self.feed.today_completed(SPOT_TOKEN, now_i):
                    sp.add(c)
            except Exception as e:
                # ── OFF_QUIET_20261030 ── one REST hiccup is not actionable;
                # SPOT_MISSING below alerts if the minute really stays absent.
                write_audit_log(f"[EMC][SPOT_FETCH] {e!r} — will retry; SPOT_MISSING alerts if data stays late")
            if (sp.last_minute() or -1) < k:
                if now_i - self._boundary(k) < SPOT_WAIT_S:
                    return False                      # not published yet — wait
                self._alert_once(f"spotmiss{k}", "SPOT_MISSING",
                                 f"no spot candle for {k // 60:02d}:{k % 60:02d} after {SPOT_WAIT_S}s — "
                                 "decisions are delayed until data resumes", "error")
                return False
        crosses = sp.recompute()
        if sp.guard.frozen:
            self._alert_once("frozen", "FROZEN", f"{sp.guard.reason} — no new setups today; an open position "
                             "is managed by SL / TP / EOD only", "critical")
            return True
        x = crosses.get(k, 0)
        if x:
            self.last_cross = (k, x)
            write_audit_log(f"[EMC][CROSS] {'UP' if x > 0 else 'DOWN'} on the {k // 60:02d}:{k % 60:02d} close"
                            f"{' (first candle / overnight gap)' if sp.gap_cross_min == k else ''}")
        c = self.core
        if c.setup is not None:
            self._feed_track(c.setup["track"], c.setup["c"]["sym"], now_i)
        c.on_minute_close(k, x)
        return True

    # ── core callbacks ──
    def _late(self, k: int) -> bool:
        return self.now_i - self._boundary(k) > LATE_S

    def _quotes_at(self, k: int, side: str) -> List[dict]:
        if self._late(k):
            self._alert_once(f"latesel{k}", "LATE_DECISION",
                             f"{side} cross on {k // 60:02d}:{k % 60:02d} processed "
                             f"{self.now_i - self._boundary(k)}s late — no selection", "warning")
            return []
        if k not in self.chain_cache:
            try:
                self.chain_cache = {k: self.feed.chain()}
            except Exception as e:
                self.gm._alert("CHAIN_FAIL", f"{e!r} — setup forfeited", "error")
                return []
        expiry, rows, ltp = self.chain_cache[k]
        if not rows:
            self.gm._alert("CHAIN_FAIL", "empty weekly chain snapshot — setup forfeited", "error")
            return []
        want = self._expected_expiry(self.day)
        if expiry is not None and expiry != want:
            self._alert_once("expirymm", "EXPIRY_MISMATCH",
                             f"nearest listed expiry {expiry} ≠ calendar {want} — using the listed one", "warning")
        out = []
        for r in rows:
            sym = str(r.get("tradingsymbol") or "")
            if (r.get("instrument_type") or ("CE" if sym.endswith("CE") else "PE")) != side:
                continue
            px = float((ltp or {}).get(sym) or 0)
            if px <= 0 or r.get("strike") is None:
                continue
            self.meta[sym] = {"token": int(r.get("instrument_token") or r.get("token") or 0),
                              "strike": float(r["strike"]), "expiry": expiry}
            out.append({"sym": sym, "strike": float(r["strike"]), "price": px})
        return out

    def _track_of(self, c: dict) -> LiveOptionTrack:
        sym = c["sym"]
        tr = self.tracks.get(sym)
        if tr is None:
            meta = self.meta.get(sym, {})
            token, expiry = meta.get("token"), meta.get("expiry")
            m = self.mcfg
            n = int(m.get("opt_warmup_sessions") or 0)
            # runner R3 parity: only sessions on which this contract was the
            # expected weekly (the corpus holds front-week contracts only)
            days = [d for d in self.prior_days if expiry is None or self._expected_expiry(d) == expiry][-n:] if n else []
            warm = []
            try:
                warm = option_warm_from_sessions(self.feed.sessions(token, days), m["opt_tf"]) if days else []
            except Exception as e:
                write_audit_log(f"[EMC][OPT_WARM] {sym}: {e!r} — EMAs start cold")
            tr = LiveOptionTrack(fast=m["ema_fast"], slow=m["ema_slow"], tf=m["opt_tf"], ds=self.ds, warm_bars=warm)
            self.tracks[sym] = tr
            self.diag["opt_tracks"] = self.diag.get("opt_tracks", 0) + 1
            if not warm:
                self.diag["opt_tracks_cold"] = self.diag.get("opt_tracks_cold", 0) + 1
            write_audit_log(f"[EMC][SETUP] {sym} ₹{c['price']:g} selected — option warm bars {len(warm)} "
                            f"from {len(days)} session(s)")
        self._feed_track(tr, sym, self.now_i)
        return tr

    def _feed_track(self, tr: LiveOptionTrack, sym: str, now_i: int) -> None:
        token = (self.meta.get(sym) or {}).get("token")
        if not token:
            return
        try:
            for cndl in self.feed.today_completed(token, now_i):
                tr.add(cndl)
        except Exception as e:
            self._alert_once(f"optfetch{sym}{now_i // 300}", "OPT_FETCH", f"{sym}: {e!r}", "warning")

    def _spot_at(self, k: int) -> Optional[float]:
        for b in reversed(self.spot.today if self.spot else []):
            if minute_of(b.ts) <= k:
                return float(b.close)
        return None

    def _net_of(self, entry: float, exit_px: float) -> float:
        g = self.gm
        q = g.pos.qty if g.pos is not None else getattr(g, "last_closed_qty", None)
        return g.net_of(entry, exit_px, qty=q)

    def _fill_entry(self, sym: str, k: int):
        if self._late(k):
            self._alert_once(f"lateentry{k}", "LATE_DECISION", f"{sym}: trigger on {k // 60:02d}:{k % 60:02d} "
                             f"processed {self.now_i - self._boundary(k)}s late — entry forfeited", "warning")
            return None, k
        setup = self.core.setup
        meta = self.meta.get(sym) or {}
        ltp = self._ltp(sym)
        m = self.mcfg
        sk = int(setup["k"])
        wait_txt = " (already aligned)" if k == sk else f", waited {k - sk}m"
        note = f"armed {sk // 60:02d}:{sk % 60:02d} at ₹{float(setup['c']['price']):g}{wait_txt}"
        px = self.gm.open_trade(symbol=sym, token=meta.get("token") or 0, side=setup["side"], ltp=ltp,
                                entry_spot=self._spot_at(k), decision_ts=self._boundary(k),
                                sl_pts=float(m["sl_pts"]), tp_pts=float(m["tp_pts"]), note=note)
        if px is None:
            return None, k
        return px, k + 1

    def _fill_exit(self, sym: str, k: int, last):
        px = self.gm.close_trade(reason="XOVER", ltp=self._ltp(sym), notify_core=False)
        if px is None and self.gm.pos is not None:
            self.pending_exit = "XOVER"                 # LIVE backoff — retried every tick
            return None, k
        return px, k + 1

    def _on_manager_close(self, ts: int, px: float, reason: str) -> None:
        if self.core is None or self.core.pos is None:
            return
        k = min(minute_of(ts), self.params.eod_min) if self.params else minute_of(ts)
        self.core.external_close(k, px, reason)

    # ── panel ──
    def phase(self) -> str:
        if self.day is None:
            return "PRE_OPEN"
        if self.refused and self.gm.pos is None:
            return "REFUSED"
        if not self.ready:
            return "BOOTING"
        if self.spot and self.spot.guard.frozen:
            return "FROZEN"
        if self.gm.pos is not None:
            return "IN_POSITION"
        if self.finished:
            return "DONE"
        hm = minute_of(self.now_i)
        if self.core and self.core.halted:
            return "HALTED"
        if self.core and self.core.setup is not None:
            return "SETUP_PENDING"
        if hm >= self.params.block_min:
            return "NO_NEW_ENTRIES"
        return "WAITING_CROSS"
