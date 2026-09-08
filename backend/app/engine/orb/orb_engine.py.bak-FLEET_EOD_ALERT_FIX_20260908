# backend/app/engine/orb/orb_engine.py
#
# ── ORB_V1 ENGINE ── live day loop. Fence: ORB_LIVE_20260903
#
# Clone of BrkEngine's shape: own thread, direct kite quotes (the Part-2b
# note in brk_engine — direct quotes sidestep the ChainStore aligned-ts
# scar entirely). Responsibilities: build 1m SPOT bars from polled quotes,
# feed each COMPLETED bar to OrbLiveDay (the parity core), select the
# option on a SIGNAL from the weekly-chain snapshot, and run the three
# engine exits (premium TP at 1m closes, spot-close SL from the core,
# 13:00 EOD). Mid-day restart: warm-replays today's completed bars from
# kite historical 1m candles, then re-adopts the open row.

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.engine.orb.orb_manager import OrbManager, STRATEGY_ID
from app.engine.orb.orb_live_core import OrbLiveDay
from app.backtest.orb.orb_v1_engine import OrbBar, SESSION_OPEN_MIN
from app.backtest.orb.backtest_orb_runner import pick_candidate

IST = timezone(timedelta(hours=5, minutes=30))
IDLE_POLL_S = 2.0
OPEN_POLL_S = 1.0
SPOT_KEY = "NSE:NIFTY 50"


def now_ist() -> datetime:
    return datetime.now(IST)


class OrbEngine:
    def __init__(self, manager: OrbManager, broker_manager):
        self.gm = manager
        self.broker = broker_manager
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._day_key: Optional[str] = None
        self._spot_bar: Optional[dict] = None      # forming 1m bar
        self._chain_meta: Dict[str, dict] = {}     # symbol -> {token, type}
        self._resumed_pending = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="orb-engine")
        self._thread.start()

    def stop(self):
        self._running = False

    # ── quotes ──
    def _kite(self):
        try:
            return self.broker.get_data_kite()
        except Exception:
            return None

    def _spot_ltp(self) -> Optional[float]:
        kite = self._kite()
        if kite is None:
            return None
        try:
            q = kite.quote([SPOT_KEY]) or {}
            return float(q.get(SPOT_KEY, {}).get("last_price") or 0) or None
        except Exception:
            return None

    def _quote_many(self, symbols: List[str]) -> Dict[str, float]:
        kite = self._kite()
        if kite is None or not symbols:
            return {}
        try:
            q = kite.quote([f"NFO:{s}" for s in symbols]) or {}
            return {s: float(q.get(f"NFO:{s}", {}).get("last_price") or 0)
                    for s in symbols}
        except Exception:
            return {}

    def _chain_snapshot(self) -> Dict[str, float]:
        """symbol -> premium for the EXPECTED weekly expiry (LD7); caches
        token/type meta for selection."""
        kite = self._kite()
        if kite is None:
            return {}
        try:
            from app.marketdata.chain_snapshot import snapshot_weekly_chain
            api_key = getattr(kite, "api_key", None)
            access_token = getattr(kite, "access_token", None)
            snap = snapshot_weekly_chain(kite, api_key, access_token) or {}
        except Exception as e:
            write_audit_log(f"[ORB][CHAIN_FAIL] {e!r}")
            return {}
        prints: Dict[str, float] = {}
        for sym, row in snap.items():
            try:
                prints[sym] = float(row.get("last_price") or 0)
                self._chain_meta[sym] = {
                    "token": row.get("instrument_token") or row.get("token"),
                    "instrument_type": row.get("instrument_type")
                    or ("CE" if sym.endswith("CE") else "PE")}
            except Exception:
                continue
        return prints

    # ── day lifecycle ──
    def _day_start_epoch(self, now: datetime) -> int:
        mid = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(mid.timestamp())

    def _roll_day(self, now: datetime):
        key = now.strftime("%Y-%m-%d")
        if key == self._day_key:
            return
        self._day_key = key
        self._spot_bar = None
        cfg = dict(self.gm.cfg())
        self.gm.day = OrbLiveDay(day_start_epoch=self._day_start_epoch(now),
                                 cfg=cfg)
        self.gm.day_stats = {"signals": 0, "entries": 0, "exits": {},
                             "refused": None, "frozen": None}
        write_audit_log(f"[ORB][DAY] armed {key} cfg_target="
                        f"{cfg.get('target_value')}")
        if self.gm.pos is not None:
            self._warm_replay(now)

    def _warm_replay(self, now: datetime):
        """Restart with an open row: rebuild the core from today's completed
        1m bars (kite historical), then re-adopt the position (parity)."""
        kite = self._kite()
        day = self.gm.day
        if kite is None or day is None:
            write_audit_log("[ORB][RESUME] no kite — core cold; exits still "
                            "guarded by EOD backstop")
            return
        try:
            spot_token = 256265                      # NIFTY 50 index token
            frm = now.replace(hour=9, minute=15, second=0, microsecond=0)
            candles = kite.historical_data(spot_token, frm, now, "minute") or []
            for c in candles:
                ts = int(c["date"].timestamp())
                ts -= ts % 60
                if ts // 60 * 60 + 60 > int(now.timestamp()):
                    break                            # forming bar — skip
                day.process(OrbBar(ts, float(c["open"]), float(c["high"]),
                                   float(c["low"]), float(c["close"])))
            self.gm.adopt_resumed_position()
            self._resumed_pending = False
            write_audit_log(f"[ORB][RESUME] warm-replayed {len(candles)} bars"
                            f"; levels={day.orb_high}/{day.orb_low}")
        except Exception as e:
            write_audit_log(f"[ORB][RESUME][FAIL] {e!r}")

    # ── 1m spot bar builder ──
    def _fold_spot(self, ltp: float, now: datetime) -> Optional[OrbBar]:
        """Accumulate the forming minute; return the COMPLETED bar when the
        minute rolls over, else None."""
        mts = int(now.timestamp())
        mts -= mts % 60
        b = self._spot_bar
        if b is None or b["ts"] != mts:
            done = None
            if b is not None and b["ts"] < mts:
                done = OrbBar(b["ts"], b["o"], b["h"], b["l"], b["c"])
            self._spot_bar = {"ts": mts, "o": ltp, "h": ltp, "l": ltp,
                              "c": ltp}
            return done
        b["h"] = max(b["h"], ltp)
        b["l"] = min(b["l"], ltp)
        b["c"] = ltp
        return None

    # ── signal → selection → entry ──
    def _on_signal(self, side: str, sig_ts: int, spot_close: float):
        self.gm.day_stats["signals"] += 1
        cfg = self.gm.cfg()
        prints = self._chain_snapshot()
        sided = {s: p for s, p in prints.items()
                 if self._chain_meta.get(s, {}).get("instrument_type") == side
                 and p > 0}
        sym = pick_candidate(sided, below=float(cfg.get("premium_max") or 200),
                             floor=float(cfg.get("premium_min") or 150))
        if sym is None:
            write_audit_log(f"[ORB][NO_CANDIDATE] {side} band "
                            f"{cfg.get('premium_min')}-{cfg.get('premium_max')}")
            if self.gm.day:
                self.gm.day.on_entry_abandoned()
            return
        self.gm.open_trade(symbol=sym,
                           token=int(self._chain_meta[sym].get("token") or 0),
                           side=side, ltp=sided[sym], entry_spot=spot_close,
                           sig_ts=sig_ts)

    # ── per-completed-minute drive ──
    def _on_completed_bar(self, bar: OrbBar):
        day = self.gm.day
        if day is None:
            return
        acts = day.process(bar)
        for a in acts:
            if a[0] == "LEVELS":
                write_audit_log(f"[ORB][LEVELS] high={a[1]} low={a[2]}")
            elif a[0] == "DAY_REFUSED":
                self.gm.day_stats["refused"] = a[1]
                self.gm._alert("DAY_REFUSED", a[1])
            elif a[0] == "FROZEN":
                self.gm.day_stats["frozen"] = a[1]
                self.gm._alert("FROZEN", f"{a[1]} — flattening", "critical")
                if self.gm.pos is not None:
                    q = self._quote_many([self.gm.pos.symbol])
                    self.gm.close_trade(reason="FROZEN",
                                        ltp=q.get(self.gm.pos.symbol))
            elif a[0] == "SIGNAL":
                self._on_signal(a[1], a[2], bar.close)
            elif a[0] == "STOP_CLOSE_BREACH" and self.gm.pos is not None:
                q = self._quote_many([self.gm.pos.symbol])
                self.gm.close_trade(reason="SL",
                                    ltp=q.get(self.gm.pos.symbol))
            elif a[0] == "EOD_SQUARE_OFF" and self.gm.pos is not None:
                q = self._quote_many([self.gm.pos.symbol])
                self.gm.close_trade(reason="EOD",
                                    ltp=q.get(self.gm.pos.symbol))
        # ── premium TP at 1m closes (LD4 rev B) ──
        pos = self.gm.pos
        if pos is not None and day.position is not None:
            q = self._quote_many([pos.symbol])
            mark = q.get(pos.symbol)
            if mark and mark >= pos.tp_prem:
                # paper books AT the level (backtest convention); live takes
                # the market fill from close_trade's sell.
                px = pos.tp_prem if pos.mode == "PAPER" else mark
                self.gm.close_trade(reason="TP", ltp=px)

    # ── main loop ──
    def _loop(self):
        write_audit_log("[ORB][ENGINE] loop up")
        while self._running:
            try:
                now = now_ist()
                hm = now.hour * 60 + now.minute
                if now.weekday() >= 5 or hm < SESSION_OPEN_MIN or hm > 13 * 60 + 30:
                    time.sleep(10)
                    continue
                try:
                    from app.utils.market_hours import is_trading_day
                    if not is_trading_day(now.date()):
                        time.sleep(60)
                        continue
                except Exception:
                    pass
                if self.mode_off():
                    time.sleep(10)
                    continue
                self._roll_day(now)
                ltp = self._spot_ltp()
                if ltp:
                    done = self._fold_spot(ltp, now)
                    if done is not None:
                        self._on_completed_bar(done)
                time.sleep(OPEN_POLL_S if self.gm.pos else IDLE_POLL_S)
            except Exception as e:
                write_audit_log(f"[ORB][ENGINE][ERR] {e!r}")
                time.sleep(IDLE_POLL_S)
        write_audit_log("[ORB][ENGINE] loop down")

    def mode_off(self) -> bool:
        return self.gm.mode() == "OFF"
