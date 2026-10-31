# backend/app/engine/ic/test_ic_engine_monitor_eod.py
#
# ET/MT/ED scenarios for ic_engine, ic_gtt_monitor, ic_live_eod, ic_runtime.
import sys
import types
from datetime import datetime, timedelta, timezone
import pytest

# ── stub app tree (same approach as the group-manager suite) ────────────────
def _mk(n):
    m = types.ModuleType(n); sys.modules[n] = m; return m

for n in ["app", "app.event_bus", "app.config", "app.risk", "app.marketdata",
          "app.db", "app.api", "app.engine", "app.engine.ic", "app.utils",
          "app.execution", "app.jobs"]:
    _mk(n)

_mk("app.event_bus.audit_logger").write_audit_log = lambda *a, **k: None
ALERTS = []
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append(code)

class _Cfg:
    BASE = {"entry_time": "09:18", "exit_time": "15:28",
            "trade_execution_mode": "PAPER", "exit_mode": "EOD"}
    strategy = dict(BASE)
_scl = _mk("app.config.strategy_loader")
_scl.load_strategy_config = lambda sid: dict(_Cfg.strategy)
# pre-existing stub gap: the engine imports the _ex loader at module level
_scl.load_strategy_config_ex = lambda sid: (dict(_Cfg.strategy), False)
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
           "notify_manual_exit", "notify_critical",
           "notify_group_entry"]:          # ── GROUP_ENTRY ──
    setattr(_tg, fn, (lambda name: lambda d: TG.append(name))(fn))

import os
from pathlib import Path
# jobs file lives in app/jobs — make the flat import work from this dir
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "jobs"))

import ic_live_core
sys.modules["app.engine.ic.ic_live_core"] = ic_live_core
import ic_carry_store
sys.modules["app.engine.ic.ic_carry_store"] = ic_carry_store
# never touch the real ~/.scalp-app from this suite
import tempfile as _tf
_carry_tmp = Path(_tf.mkdtemp(prefix="ic_test_state_"))
# ── IC_SPLIT ── paths derive from STATE_DIR per strategy_id now.
ic_carry_store.STATE_DIR = _carry_tmp
TEST_SID = "IC_V2"
import ic_selection
sys.modules["app.engine.ic.ic_selection"] = ic_selection
import ic_group_manager
sys.modules["app.engine.ic.ic_group_manager"] = ic_group_manager
import ic_engine as ENG
sys.modules["app.engine.ic.ic_engine"] = ENG
import ic_gtt_monitor as MON
sys.modules["app.engine.ic.ic_gtt_monitor"] = MON
import ic_runtime as RT
sys.modules["app.engine.ic.ic_runtime"] = RT
import ic_live_eod as EOD
sys.modules["app.jobs.ic_live_eod"] = EOD


# ── IC_SPLIT ── the jobs iterate RT.IC_REGISTRY; tests install a gm here.
def _install_gm(gm, sid=TEST_SID):
    RT.IC_REGISTRY.clear()
    if gm is not None:
        _rt = RT._ICRuntime()
        _rt.manager = gm
        RT.IC_REGISTRY[sid] = _rt

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
        self.strategy_id = TEST_SID    # ── IC_SPLIT ── engine reads this
        self.squared = 0; self.opened = False; self.ticks = []
        # ── IC_V2 surface ──
        self.carried = False
        self.expiry_squared = 0; self.morning_calls = 0
        self.morning_remaining = 0
        self.committed = False; self.premarket_calls = 0
        self.premarket_ok = True; self.holds = []
        self.due_calls = 0
    def has_open_group(self): return self.opened
    def force_square_off_all(self, reason): self.squared += 1; self.opened = False; return 4
    def current_group(self): return None
    def enter_day(self, sel, mode): self.opened = True; return True
    def leg_runtime(self, lid): return {}
    def on_tick(self, t, p): self.ticks.append((t, p))
    def is_paper(self): return True
    # ── IC_V2 ──
    def attach_chain_provider(self, fn): self.chain_provider = fn
    def process_due(self, ts=None): self.due_calls += 1
    def has_carried_open(self): return self.carried
    def set_carry_hold(self, h): self.holds.append(h)
    def premarket_cancel_gtts(self):
        self.premarket_calls += 1; return self.premarket_ok
    def morning_square_off(self):
        self.morning_calls += 1
        if self.morning_remaining == 0:
            self.carried = False; self.opened = False
        return self.morning_remaining
    def expiry_square_off(self, today): self.expiry_squared += 1; return 0
    def carry_committed(self): return self.committed
    def commit_carry(self, mode): self.committed = True; return True
    def carry_entry_date(self): return getattr(self, "carry_ed", None)
    def restore_session_payload(self, payload, adopt_as_carry=False):
        self.session_restores = getattr(self, "session_restores", [])
        self.session_restores.append(adopt_as_carry)
        if adopt_as_carry:
            self.carried = True
        self.opened = True
        return True
    def restore_carry_payload(self, payload):
        self.carry_restores = getattr(self, "carry_restores", 0) + 1
        self.carried = True; self.opened = True
        return True

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
    _Cfg.strategy = dict(_Cfg.BASE, exit_mode="EOD")   # legacy branch
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
        self.is_adjust = False    # IC_V2: monitor also watches ·ADJ longs

class MonGM:
    def __init__(self, gids):
        self.strategy_id = TEST_SID    # ── IC_SPLIT ── monitor reads this
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
    def escalate_unfilled_gtt(self, *, leg_id):        # IC_V2 gap escalation
        self.escalations = getattr(self, "escalations", [])
        self.escalations.append(leg_id)
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
    def cancel_order(self, oid):
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append(oid)


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
    _Cfg.strategy = dict(_Cfg.BASE, exit_mode="EOD")
    gm = SpyGM(); gm.opened = True
    _install_gm(gm)
    clock = {"now": T("15:25")}
    def now_fn(): return clock["now"]
    def sleep_fn(s): clock["now"] = clock["now"] + timedelta(seconds=s)
    EOD.ic_live_eod_job(sleep_fn=sleep_fn, now_fn=now_fn)
    assert gm.squared == 1
    assert clock["now"] >= T("15:28")


def test_ed2_misfire_squares_immediately():
    _Cfg.strategy = dict(_Cfg.BASE, exit_mode="EOD")
    gm = SpyGM(); gm.opened = True
    _install_gm(gm)
    calls = []
    EOD.ic_live_eod_job(sleep_fn=lambda s: calls.append(s),
                           now_fn=lambda: T("15:40"))
    assert gm.squared == 1 and calls == []          # no waiting


def test_ed3_no_manager_noop():
    _install_gm(None)
    EOD.ic_live_eod_job(sleep_fn=lambda s: None, now_fn=lambda: T("15:40"))
    # nothing raised → pass


# ── RT: runtime bootstrap smoke ─────────────────────────────────────────────
def test_rt1_runtime_builds_singletons_without_executor():
    import asyncio
    RT.IC_REGISTRY.clear()
    # ZerodhaOrderExecutor import will fail (stubbed app.execution is empty)
    async def run():
        # asyncio.run + create_task: kills the 3.12 get_event_loop
        # DeprecationWarning (pre-existing cosmetic, fixed 2026-07-26)
        task = asyncio.create_task(RT.ic_runtime(SpyBroker(), TEST_SID))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(run())
    assert RT.get_ic_manager(TEST_SID) is not None
    assert RT.get_ic_engine(TEST_SID) is not None
    RT.get_ic_engine(TEST_SID).stop()



# ════════════════════════════════════════════════════════════════════════
# IC_V2 (2026-07-26) — carry-morning machine, NEXT_OPEN backstops, jobs,
# gap escalation
# ════════════════════════════════════════════════════════════════════════

def _v2cfg(**kw):
    d = dict(_Cfg.BASE, exit_mode="NEXT_OPEN", next_open_time="09:16",
             expiry_exit_time="15:28")
    d.update(kw)
    return d


# ── ET6: pre-market GTT teardown + first-candle hold (no on_tick) ───────────
def test_et6_carry_morning_premarket_and_hold():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True; gm.carried = True
    e = ENG.ICEngine(gm, SpyBroker())
    e._step(T("09:00"))                       # pre-market → teardown attempted
    assert gm.premarket_calls == 1
    assert e._premarket_clear_date is not None
    e._step(T("09:15", 30))                   # first candle → HOLD, no exits
    assert gm.holds and gm.holds[-1] is True
    assert gm.morning_calls == 0              # NOTHING exits before 09:16
    assert gm.squared == 0


# ── ET7: 09:16 → morning square-off retry loop, entry blocked until flat ────
def test_et7_morning_close_retry_and_entry_gate():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True; gm.carried = True
    gm.morning_remaining = 2                  # broker down: legs stay open
    e = ENG.ICEngine(gm, SpyBroker())
    e._step(T("09:16"))
    e._step(T("09:16", 30))
    assert gm.morning_calls == 2              # continuous retry
    assert "IC_MORNING_STUCK" in ALERTS
    # entry window opens while book not flat → attempt NOT consumed (D8)
    calls = []
    e._attempt_entry = lambda cfg: calls.append(1)
    e._step(T("09:18", 10))
    assert calls == [] and e._attempt_date is None
    # broker recovers → close completes, then the entry attempt fires
    gm.morning_remaining = 0
    e._step(T("09:18", 40))                   # closes carry this iteration
    e._step(T("09:18", 50))
    assert calls == [1]


# ── ET8: NEXT_OPEN session end — expiry backstop + carry commit ─────────────
def test_et8_next_open_session_end():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True
    e = ENG.ICEngine(gm, SpyBroker())
    e._attempt_date = "2026-07-06"
    e._step(T("15:28"))                       # expiry backstop (scoped) fires
    assert gm.expiry_squared == 1 and gm.squared == 0   # NOT a full square-off
    assert not gm.committed                   # not yet — session still on
    e._step(T("15:31"))
    assert not gm.committed                   # CAS_2026: commit moved to
                                              # 15:40:30 — 15:31 is too early
    e._step(T("15:41"))
    assert gm.committed                       # carry committed post-15:40:30
    e._step(T("15:42"))
    assert gm.committed                       # idempotent (carry_committed gate)


# ── MT7: triggered-but-unfilled + position OPEN → escalation (gap defence) ──
def test_mt7_triggered_unfilled_escalates_market_out():
    gm = MonGM(["501"]); ex = MonExec()
    ex.gtts = [{"id": "501", "status": "triggered",
                "orders": [{"result": {"order_result": {"order_id": "OX"}}}]}]
    ex.order_fill = {"OX": {"status": "OPEN", "avg_price": 0.0}}   # resting limit
    ex.positions = [{"tradingsymbol": "N24150CE", "quantity": -1560}]
    mon = MON.ICGTTMonitor(ex, gm)
    for _ in range(3):
        mon._sweep()
    assert getattr(gm, "escalations", []) == ["L1"]
    assert gm.handoffs == []                  # escalation path, not a handoff
    assert "OX" in getattr(ex, "cancelled", [])   # stale limit cancelled first


# ── ED4: EOD job in NEXT_OPEN mode → expiry-scoped, never full square-off ───
def test_ed4_eod_job_next_open_scoped():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True
    _install_gm(gm)
    EOD.ic_live_eod_job(sleep_fn=lambda s: None, now_fn=lambda: T("15:40"))
    assert gm.expiry_squared == 1 and gm.squared == 0


# ── MO1: morning job — teardown, wait to 09:16, close ───────────────────────
def test_mo1_morning_job_full_cycle():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True; gm.carried = True
    _install_gm(gm)
    clock = {"now": T("09:08")}
    def now_fn(): return clock["now"]
    def sleep_fn(s): clock["now"] = clock["now"] + timedelta(seconds=s)
    EOD.ic_morning_job(sleep_fn=sleep_fn, now_fn=now_fn)
    assert gm.premarket_calls >= 1
    assert gm.morning_calls == 1 and not gm.carried
    assert clock["now"] >= T("09:16")


def test_mo2_morning_job_noop_without_carry():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM()
    _install_gm(gm)
    EOD.ic_morning_job(sleep_fn=lambda s: None, now_fn=lambda: T("09:08"))
    assert gm.premarket_calls == 0 and gm.morning_calls == 0



# ── ET9: transient entry failure RETRIES inside grace; FINAL consumes ───────
def test_et9_entry_retry_contract():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM()
    e = ENG.ICEngine(gm, SpyBroker())
    results = ["RETRY", "RETRY", "FINAL"]
    calls = []
    e._attempt_entry = lambda cfg: (calls.append(1), results.pop(0))[1]
    e._step(T("09:18", 5))
    e._step(T("09:18", 20))
    assert len(calls) == 2 and e._attempt_date is None    # NOT consumed
    assert "IC_ENTRY_RETRY" in ALERTS                     # once-per-day alert
    e._step(T("09:18", 40))
    assert len(calls) == 3 and e._attempt_date is not None  # FINAL consumed
    e._step(T("09:18", 55))
    assert len(calls) == 3                                  # no re-attempts


def test_et9b_broker_not_ready_is_retry_then_late_consumes():
    _Cfg.strategy = _v2cfg()
    class NotReadyBroker(SpyBroker):
        def is_ready(self): return False
    gm = SpyGM()
    e = ENG.ICEngine(gm, NotReadyBroker())
    e._step(T("09:18", 10))                    # real path → broker not ready
    assert e._attempt_date is None             # transient → not consumed
    assert not gm.opened
    e._step(T("09:21"))                        # past grace → LATE consumes
    assert e._attempt_date is not None
    assert "IC_LATE_SKIP" in ALERTS



# ── ET10: SAME-DAY evening restart must NOT square off the carry ────────────
# (2026-07-30 incident: restart at 15:45 after the 15:30:30 commit closed
#  all carried legs as NEXT_OPEN at 15:46 — the machine was clock-only.)
def test_et10_same_day_restore_holds_carry():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True; gm.carried = True; gm.committed = True
    gm.carry_ed = T("15:46").strftime("%Y-%m-%d")     # entered TODAY
    e = ENG.ICEngine(gm, SpyBroker())
    e._step(T("15:46"))
    e._step(T("16:10"))
    assert gm.morning_calls == 0                      # NO same-day square-off
    assert gm.premarket_calls == 0                    # NO GTT teardown today
    assert gm.holds and gm.holds[-1] is False         # hold released
    assert gm.carried                                 # legs still riding
    assert not gm.squared                             # and no legacy EOD path


def test_et10b_next_day_machine_fires():
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True; gm.carried = True; gm.committed = True
    gm.carry_ed = "2026-07-05"                        # entered a PRIOR day
    e = ENG.ICEngine(gm, SpyBroker())
    e._step(T("09:00"))                               # pre-market teardown
    assert gm.premarket_calls == 1
    e._step(T("09:16", 10))                           # sole exit executor
    assert gm.morning_calls == 1 and not gm.carried


def test_et10c_unknown_entry_date_falls_to_machine():
    # legacy payload without entry_date: closing is the safer failure
    _Cfg.strategy = _v2cfg()
    gm = SpyGM(); gm.opened = True; gm.carried = True
    gm.carry_ed = None
    e = ENG.ICEngine(gm, SpyBroker())
    e._step(T("09:16", 10))
    assert gm.morning_calls == 1



# ── ET12: IC_RESTART boot precedence — session restore paths ────────────────
def test_et12_same_day_session_restored_live():
    _Cfg.strategy = _v2cfg()
    ic_carry_store.clear_carry(TEST_SID); ic_carry_store.clear_session(TEST_SID)
    today = ENG.now_ist().strftime("%Y-%m-%d")
    ic_carry_store.save_session(TEST_SID, {"entry_date": today, "core": {"legs": []},
                                 "paper": True})
    gm = SpyGM()
    e = ENG.ICEngine(gm, SpyBroker())
    e._restore_carry_if_any()
    assert getattr(gm, "session_restores", []) == [False]   # today's group
    ic_carry_store.clear_session(TEST_SID)


def test_et12b_prior_day_session_adopted_as_carry():
    _Cfg.strategy = _v2cfg()
    ic_carry_store.clear_carry(TEST_SID); ic_carry_store.clear_session(TEST_SID)
    yday = (ENG.now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    ic_carry_store.save_session(TEST_SID, {"entry_date": yday,
                                 "core": {"legs": []}, "paper": True})
    gm = SpyGM()
    e = ENG.ICEngine(gm, SpyBroker())
    ALERTS.clear()
    e._restore_carry_if_any()
    assert getattr(gm, "session_restores", []) == [True]    # adopted
    assert gm.carried
    assert "IC_SESSION_ADOPTED" in ALERTS
    ic_carry_store.clear_session(TEST_SID)


def test_et12c_carry_file_wins_over_session():
    _Cfg.strategy = _v2cfg()
    today = ENG.now_ist().strftime("%Y-%m-%d")
    ic_carry_store.save_carry(TEST_SID, {"entry_date": today, "legs": [{}]})
    ic_carry_store.save_session(TEST_SID, {"entry_date": today, "core": {"legs": []},
                                 "paper": True})
    gm = SpyGM()
    e = ENG.ICEngine(gm, SpyBroker())
    e._restore_carry_if_any()
    assert getattr(gm, "carry_restores", 0) == 1
    assert getattr(gm, "session_restores", []) == []        # never consulted
    assert not ic_carry_store.session_exists(TEST_SID)              # superseded+cleared
    ic_carry_store.clear_carry(TEST_SID)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))