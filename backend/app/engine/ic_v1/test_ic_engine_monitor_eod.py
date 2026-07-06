# backend/app/engine/ic_v1/test_ic_engine_monitor_eod.py
#
# ET/MT/ED scenarios for ic_engine, ic_gtt_monitor, ic_v1_live_eod, ic_runtime.
import sys
import types
from datetime import datetime, timedelta, timezone
import pytest

# ── stub app tree (same approach as the group-manager suite) ────────────────
def _mk(n):
    m = types.ModuleType(n); sys.modules[n] = m; return m

for n in ["app", "app.event_bus", "app.config", "app.risk", "app.marketdata",
          "app.db", "app.api", "app.engine", "app.engine.ic_v1", "app.utils",
          "app.execution", "app.jobs"]:
    _mk(n)

_mk("app.event_bus.audit_logger").write_audit_log = lambda *a, **k: None
ALERTS = []
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append(code)

class _Cfg:
    strategy = {"entry_time": "09:18", "exit_time": "15:28",
                "trade_execution_mode": "PAPER"}
_mk("app.config.strategy_loader").load_strategy_config = \
    lambda sid: dict(_Cfg.strategy)
_mk("app.config.global_loader").load_global_config = lambda: {"trade_on": True}

_mg = _mk("app.risk.strategy_max_loss_guard")
_mg.resolve_execution_mode = lambda sid: ("PAPER", False)
_mg.check_strategy_max_loss = lambda sid: False
_mk("app.risk.risk_mtm_guard").is_day_blocked = lambda sid: False

_lts = _mk("app.marketdata.ltp_store")
class LTPStore:
    _d = {}
    @classmethod
    def update(cls, s, v): cls._d[s] = v
    @classmethod
    def get_with_timestamp(cls, s):
        import time
        v = cls._d.get(s)
        return (v, time.time()) if v else None
_lts.LTPStore = LTPStore

_mk("app.utils.market_hours").is_market_open = lambda: True
_mk("app.db.trades_repo").insert_trade = lambda **k: None
sys.modules["app.db.trades_repo"].close_trade = lambda **k: None
_mk("app.db.paper_trades_repo").insert_paper_trade = lambda **k: None
sys.modules["app.db.paper_trades_repo"].close_paper_trade = lambda **k: None
_tg = _mk("app.api.telegram_api")
TG = []
for fn in ["notify_trade_entry", "notify_sl_exit", "notify_tp_exit",
           "notify_manual_exit", "notify_critical"]:
    setattr(_tg, fn, (lambda name: lambda d: TG.append(name))(fn))

import ic_live_core
sys.modules["app.engine.ic_v1.ic_live_core"] = ic_live_core
import ic_selection
sys.modules["app.engine.ic_v1.ic_selection"] = ic_selection
import ic_group_manager
sys.modules["app.engine.ic_v1.ic_group_manager"] = ic_group_manager
import ic_engine as ENG
sys.modules["app.engine.ic_v1.ic_engine"] = ENG
import ic_gtt_monitor as MON
sys.modules["app.engine.ic_v1.ic_gtt_monitor"] = MON
import ic_runtime as RT
sys.modules["app.engine.ic_v1.ic_runtime"] = RT
import ic_v1_live_eod as EOD

from ic_live_core import L_OPEN, L_CLOSED, StrikePick
from ic_selection import ICSelection

IST = timezone(timedelta(minutes=330))
def T(hm, s=0):
    h, m = hm.split(":")
    return datetime(2026, 7, 6, int(h), int(m), s, tzinfo=IST)


# ── ET: engine scheduling logic ─────────────────────────────────────────────
def test_et1_entry_window_states():
    assert ENG.entry_window_state(T("09:17"), "09:18", 120) == "BEFORE"
    assert ENG.entry_window_state(T("09:18"), "09:18", 120) == "IN_WINDOW"
    assert ENG.entry_window_state(T("09:19", 59), "09:18", 120) == "IN_WINDOW"
    assert ENG.entry_window_state(T("09:20", 1), "09:18", 120) == "LATE"


class SpyGM:
    def __init__(self):
        self.squared = 0; self.opened = False; self.ticks = []
    def has_open_group(self): return self.opened
    def force_square_off_all(self, reason): self.squared += 1; self.opened = False; return 4
    def current_group(self): return None
    def enter_day(self, sel, mode): self.opened = True; return True
    def leg_runtime(self, lid): return {}
    def on_tick(self, t, p): self.ticks.append((t, p))
    def is_paper(self): return True

class SpyBroker:
    def is_ready(self): return True
    def get_data_kite(self): return None


def test_et2_late_wake_skips_day():
    gm = SpyGM(); e = ENG.ICEngine(gm, SpyBroker())
    ALERTS.clear()
    e._step(T("11:00"))
    assert "IC_LATE_SKIP" in ALERTS and not gm.opened
    # and only alerts once
    ALERTS.clear()
    e._step(T("11:00", 30))
    assert "IC_LATE_SKIP" not in ALERTS


def test_et3_eod_backstop_fires_every_iteration_past_exit():
    gm = SpyGM(); gm.opened = True
    e = ENG.ICEngine(gm, SpyBroker())
    e._attempt_date = "2026-07-06"
    e._step(T("15:28"))
    assert gm.squared == 1
    gm.opened = True                     # simulate something reopened/stuck
    e._step(T("15:29"))
    assert gm.squared == 2               # continuous, not one-shot


def test_et4_in_window_attempts_once():
    gm = SpyGM(); e = ENG.ICEngine(gm, SpyBroker())
    calls = []
    e._attempt_entry = lambda cfg: calls.append(1)
    e._step(T("09:18", 5))
    e._step(T("09:18", 30))
    assert len(calls) == 1               # once per day


def test_et5_off_mode_no_entry():
    _Cfg.strategy = dict(_Cfg.strategy, trade_execution_mode="OFF")
    gm = SpyGM(); e = ENG.ICEngine(gm, SpyBroker())
    e._attempt_entry(_Cfg.strategy)
    assert not gm.opened
    _Cfg.strategy = dict(_Cfg.strategy, trade_execution_mode="PAPER")


# ── MT: GTT monitor doctrine ────────────────────────────────────────────────
class Leg:
    def __init__(self):
        self.leg_id = "L1"; self.symbol = "N24150CE"; self.is_short = True
        self.state = L_OPEN; self.sl = 119.49; self.tp = None
        self.entry_price = 84.15

class MonGM:
    def __init__(self, gids):
        self.leg = Leg(); self.gids = gids; self.handoffs = []
        self._paper = False
        class C:
            def __init__(s, leg): s._leg = leg
            def open_legs(s): return [s._leg] if s._leg.state == L_OPEN else []
        self._core = C(self.leg)
    def current_group(self): return self._core
    def is_paper(self): return self._paper
    def leg_runtime(self, lid): return {"gtt_ids": self.gids}
    def on_backstop_leg_exit(self, *, leg_id, exit_price, reason):
        self.handoffs.append((leg_id, exit_price, reason))
        self.leg.state = L_CLOSED

class MonExec:
    def __init__(self):
        self.gtts = []; self.fail_fetch = False
        self.order_fill = {}; self.orders = []; self.positions = []
    def get_gtts(self):
        if self.fail_fetch: raise RuntimeError("network")
        return self.gtts
    def get_order_fill(self, oid): return self.order_fill.get(oid, {})
    def get_orders(self): return self.orders
    def get_open_positions(self): return self.positions


def test_mt1_fetch_fail_never_closes():
    gm = MonGM(["501"]); ex = MonExec(); ex.fail_fetch = True
    MON.ICGTTMonitor(ex, gm)._sweep()
    assert gm.handoffs == [] and gm.leg.state == L_OPEN


def test_mt2_triggered_with_fill_hands_off_sl():
    gm = MonGM(["501"]); ex = MonExec()
    ex.gtts = [{"id": "501", "status": "triggered",
                "orders": [{"result": {"order_result": {"order_id": "OX"}}}]}]
    ex.order_fill = {"OX": {"status": "COMPLETE", "avg_price": 119.6}}
    MON.ICGTTMonitor(ex, gm)._sweep()
    assert gm.handoffs == [("L1", 119.6, "SL")]


def test_mt3_gtt_race_retries_then_broker_exit():
    gm = MonGM(["501"]); ex = MonExec()
    ex.gtts = [{"id": "501", "status": "triggered", "orders": [{}]}]  # no result yet
    LTPStore.update("N24150CE", 121.0)
    mon = MON.ICGTTMonitor(ex, gm)
    mon._sweep(); mon._sweep()
    assert gm.handoffs == []                       # still confirming
    mon._sweep()                                   # retry cap hit
    assert gm.handoffs == [("L1", 121.0, "BROKER_EXIT")]


def test_mt4_missing_but_position_open_alerts_naked_no_close():
    gm = MonGM(["501"]); ex = MonExec()
    ex.positions = [{"tradingsymbol": "N24150CE", "quantity": -1560}]
    crit = []
    # bind on the LIVE module object — other suites may have re-stubbed it
    sys.modules["app.api.telegram_api"].notify_critical = lambda d: crit.append(d)
    mon = MON.ICGTTMonitor(ex, gm)
    for _ in range(3):
        mon._sweep()
    assert gm.handoffs == [] and gm.leg.state == L_OPEN
    assert len(crit) == 1


def test_mt5_missing_and_position_gone_broker_exit():
    gm = MonGM(["501"]); ex = MonExec()
    ex.orders = [{"tradingsymbol": "N24150CE", "transaction_type": "BUY",
                  "status": "COMPLETE", "average_price": 118.0, "order_id": "OZ"}]
    mon = MON.ICGTTMonitor(ex, gm)
    for _ in range(3):
        mon._sweep()
    assert gm.handoffs == [("L1", 118.0, "BROKER_EXIT")]


def test_mt6_paper_group_untouched():
    gm = MonGM(["501"]); gm._paper = True
    ex = MonExec(); ex.fail_fetch = True           # would raise if touched
    MON.ICGTTMonitor(ex, gm)._sweep()
    assert gm.handoffs == []


# ── ED: EOD job wait + misfire ──────────────────────────────────────────────
def test_ed1_waits_until_exit_then_squares():
    gm = SpyGM(); gm.opened = True
    RT._MANAGER = gm
    clock = {"now": T("15:25")}
    def now_fn(): return clock["now"]
    def sleep_fn(s): clock["now"] = clock["now"] + timedelta(seconds=s)
    EOD.ic_v1_live_eod_job(sleep_fn=sleep_fn, now_fn=now_fn)
    assert gm.squared == 1
    assert clock["now"] >= T("15:28")


def test_ed2_misfire_squares_immediately():
    gm = SpyGM(); gm.opened = True
    RT._MANAGER = gm
    calls = []
    EOD.ic_v1_live_eod_job(sleep_fn=lambda s: calls.append(s),
                           now_fn=lambda: T("15:40"))
    assert gm.squared == 1 and calls == []          # no waiting


def test_ed3_no_manager_noop():
    RT._MANAGER = None
    EOD.ic_v1_live_eod_job(sleep_fn=lambda s: None, now_fn=lambda: T("15:40"))
    # nothing raised → pass


# ── RT: runtime bootstrap smoke ─────────────────────────────────────────────
def test_rt1_runtime_builds_singletons_without_executor():
    import asyncio
    RT._MANAGER = RT._ENGINE = RT._MONITOR = None
    # ZerodhaOrderExecutor import will fail (stubbed app.execution is empty)
    async def run():
        task = asyncio.get_event_loop().create_task(RT.ic_v1_runtime(SpyBroker()))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.get_event_loop().run_until_complete(run())
    assert RT.get_ic_manager() is not None
    assert RT.get_ic_engine() is not None
    RT.get_ic_engine().stop()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))