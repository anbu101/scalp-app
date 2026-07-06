# backend/app/engine/ic_v1/ic_engine.py
#
# IC_V1 — Engine (entry scheduler + price watcher + EOD backstop)
# ============================================================================
# IC_V1 has NO signal pipeline: no candles, no indicators, no selection loop.
# One scheduled entry per day, then watch 2–4 symbols until exit. So this
# engine is deliberately NOT a clone of the V1/V2 tick-engine stack:
#
#   PRICE FEED = REST POLL, NOT A 7th WEBSOCKET.
#   Zerodha caps concurrent WS connections per api_key; V1/V2/V3/V4/V5/BB
#   already own theirs. IC watches at most 4 symbols with a 42% SL — a
#   4-second REST kite.ltp() poll is more than sufficient granularity, is
#   immune to the WS-zombie failure mode (house learning), and REST is
#   already the doctrine-primary price source for exit decisions. Broker-side
#   GTTs (D4) remain the true first line of protection regardless of what
#   this loop does.
#
# DAILY STATE MACHINE (evaluated every IDLE_POLL_S seconds, IST fixed +05:30):
#   1. now >= exit_time and group open       → force_square_off_all("EOD")
#      (continuous backstop — runs EVERY iteration after exit_time, the
#       APScheduler-silent-death mitigation; ic_live_eod is the scheduled
#       primary, this is the always-on second layer)
#   2. entry window [entry_time, entry_time+grace] and not yet attempted
#      today                                  → _attempt_entry()
#      LATE-ENTRY GUARD: waking > grace past entry_time (app started late,
#      machine slept) SKIPS the day — the live analog of the backtest's 180s
#      candle-staleness guard. One attempt per day, success or skip; NO_ENTRY
#      outcomes are final (spec: no re-entry).
#   3. group open                             → REST-poll LTPs → LTPStore +
#      group_manager.on_tick()
# ============================================================================

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert
from app.config.strategy_loader import load_strategy_config
from app.risk.strategy_max_loss_guard import resolve_execution_mode
from app.marketdata.ltp_store import LTPStore

from app.engine.ic_v1.ic_selection import snapshot_weekly_chain, select_ic_strikes
from app.engine.ic_v1.ic_group_manager import ICGroupManager, DEFAULT_LEGS, STRATEGY_ID

IST = timezone(timedelta(minutes=330))    # house rule: fixed offset, no pytz

IDLE_POLL_S    = 5
OPEN_POLL_S    = 4
ENTRY_GRACE_S  = 120                      # config-overridable: entry_late_grace_s


def now_ist() -> datetime:
    return datetime.now(IST)


def hm_to_dt(hm: str, ref: datetime) -> datetime:
    """'09:18' → today's aware datetime at 09:18:00 IST."""
    h, m = hm.strip().split(":")
    return ref.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def entry_window_state(now: datetime, entry_hm: str, grace_s: int) -> str:
    """Pure: 'BEFORE' | 'IN_WINDOW' | 'LATE' relative to the entry instant."""
    entry_dt = hm_to_dt(entry_hm, now)
    if now < entry_dt:
        return "BEFORE"
    if (now - entry_dt).total_seconds() <= grace_s:
        return "IN_WINDOW"
    return "LATE"


class ICEngine:

    def __init__(self, group_manager: ICGroupManager, broker_manager):
        self.gm = group_manager
        self.broker = broker_manager
        self._running = False
        self._attempt_date: Optional[str] = None   # in-memory once-per-day guard
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="ICV1Engine")
        self._thread.start()
        write_audit_log("[IC][ENGINE] started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                slept = self._step(now_ist())
            except Exception as e:
                write_audit_log(f"[IC][ENGINE][STEP_ERR] {repr(e)}")
                slept = IDLE_POLL_S
            time.sleep(slept)

    # ------------------------------------------------------------------
    def _cfg(self) -> dict:
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception:
            return {}

    def _step(self, now: datetime) -> float:
        """One scheduler iteration. Returns seconds to sleep."""
        cfg      = self._cfg()
        entry_hm = cfg.get("entry_time", "09:18")
        exit_hm  = cfg.get("exit_time", "15:28")
        grace    = int(cfg.get("entry_late_grace_s", ENTRY_GRACE_S))

        # 1) continuous EOD backstop — before anything else, every iteration
        if now >= hm_to_dt(exit_hm, now) and self.gm.has_open_group():
            write_audit_log("[IC][ENGINE] EOD backstop firing")
            self.gm.force_square_off_all(reason="EOD")
            return IDLE_POLL_S

        # 2) entry window
        today = now.strftime("%Y-%m-%d")
        if self._attempt_date != today:
            state = entry_window_state(now, entry_hm, grace)
            if state == "IN_WINDOW":
                self._attempt_date = today
                self._attempt_entry(cfg)
                return IDLE_POLL_S
            if state == "LATE":
                self._attempt_date = today
                write_audit_log(f"[IC][ENGINE] woke LATE (> {grace}s past "
                                f"{entry_hm}) — skipping day (late-entry guard)")
                record_alert("IC_LATE_SKIP",
                             f"IC_V1: engine past entry window ({entry_hm} "
                             f"+{grace}s) — no entry today",
                             severity="warning", strategy_id=STRATEGY_ID)
                return IDLE_POLL_S

        # 3) open-group price watch
        if self.gm.has_open_group():
            self._poll_ltps()
            return OPEN_POLL_S

        return IDLE_POLL_S

    # ------------------------------------------------------------------
    def _attempt_entry(self, cfg: dict):
        raw_mode = (cfg.get("trade_execution_mode") or "PAPER").strip().upper()
        if raw_mode == "OFF":
            write_audit_log("[IC][ENGINE] mode=OFF — no entry today")
            return

        mode, degraded = resolve_execution_mode(STRATEGY_ID)
        if degraded:
            record_alert("IC_MODE_DEGRADED",
                         "IC_V1: config unreadable — forced PAPER this session",
                         severity="error", strategy_id=STRATEGY_ID)

        # market-day sanity (fail open: gates + selection still protect)
        try:
            from app.utils.market_hours import is_market_open
            if not is_market_open():
                write_audit_log("[IC][ENGINE] market closed — no entry")
                return
        except Exception:
            pass

        try:
            if not self.broker.is_ready():
                write_audit_log("[IC][ENGINE] broker not ready at entry — skip day")
                record_alert("IC_NO_ENTRY", "IC_V1: broker not ready at entry time",
                             severity="error", strategy_id=STRATEGY_ID)
                return
            kite_data = self.broker.get_data_kite()
            api_key, access_token = self._data_creds()
        except Exception as e:
            write_audit_log(f"[IC][ENGINE] broker access failed: {e} — skip day")
            return

        expiry, rows, ltps = snapshot_weekly_chain(kite_data, api_key, access_token)
        if expiry is None:
            record_alert("IC_NO_ENTRY", "IC_V1: chain snapshot failed — no entry",
                         severity="error", strategy_id=STRATEGY_ID)
            return

        from app.engine.ic_v1.ic_selection import build_chain_candidates
        legs_cfg = cfg.get("legs") or DEFAULT_LEGS
        ce, pe, tokens = build_chain_candidates(rows, ltps)
        selection = select_ic_strikes(legs_cfg, ce, pe, tokens, expiry)

        opened = self.gm.enter_day(selection, mode=mode)
        write_audit_log(f"[IC][ENGINE] entry attempt mode={mode} opened={opened}")

    def _data_creds(self):
        """api_key/access_token for instruments freshness check; best-effort."""
        try:
            k = self.broker.get_data_kite()
            return getattr(k, "api_key", None), getattr(k, "access_token", None)
        except Exception:
            return None, None

    # ------------------------------------------------------------------
    def _poll_ltps(self):
        core = self.gm.current_group()
        if core is None:
            return
        legs = core.open_legs()
        if not legs:
            return
        symbols = {}
        for leg in legs:
            rt = self.gm.leg_runtime(leg.leg_id)
            symbols[f"NFO:{leg.symbol}"] = (rt.get("token", 0), leg.symbol)
        try:
            kite = self.broker.get_data_kite()
            data = kite.ltp(list(symbols.keys()))
        except Exception as e:
            write_audit_log(f"[IC][ENGINE][LTP_POLL_ERR] {e}")
            return
        for key, (token, sym) in symbols.items():
            row = data.get(key) or {}
            ltp = float(row.get("last_price") or 0.0)
            if ltp <= 0:
                continue
            try:
                LTPStore.update(sym, ltp)
            except Exception:
                pass
            self.gm.on_tick(token, ltp)