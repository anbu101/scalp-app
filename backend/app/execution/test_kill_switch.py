# backend/app/execution/test_kill_switch.py
#
# KS scenarios for the kill-switch orchestrator. Same stub-injection
# convention as the IC suites; adapters are replaced via register_adapter
# so no strategy engine is imported. Run: python3 test_kill_switch.py
import sys
import types
import pytest


def _mk(n):
    m = types.ModuleType(n); sys.modules[n] = m; return m

for n in ["app", "app.event_bus", "app.config", "app.db", "app.api",
          "app.engine", "app.engine.ic", "app.execution"]:
    _mk(n)

AUDIT = []
_mk("app.event_bus.audit_logger").write_audit_log = lambda s: AUDIT.append(s)
ALERTS = []
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append((code, message))

CFG = {}
SAVED = []
_scl = _mk("app.config.strategy_loader")
_scl.load_strategy_config = lambda sid: dict(CFG.get(sid) or {})
def _save(sid, cfg): SAVED.append((sid, dict(cfg))); CFG[sid] = dict(cfg)
_scl.save_strategy_config = _save

DB_OPEN = {}
_mk("app.db.trades_repo").get_open_trades_for_strategy = \
    lambda sid: [{}] * DB_OPEN.get(sid, 0)

TG = []
_mk("app.api.telegram_api").notify_critical = lambda d: TG.append(d)

# ── IC_SPLIT ── get_ic_manager takes the strategy id; the stub keys per sid.
IC_GM = {"IC_V1": None, "IC_V2": None}
_mk("app.engine.ic.ic_runtime").get_ic_manager = lambda sid: IC_GM.get(sid)

import kill_switch as KS
sys.modules["app.execution.kill_switch"] = KS


# ── KILL_PAPER_FLIP_20261123 ── fake clock (settle / watch never really
# sleep) and a spawn recorder (the background watch is run by hand)
CLOCK = [1000.0]
SPAWNED = []


@pytest.fixture(autouse=True)
def clean():
    CFG.clear(); SAVED.clear(); AUDIT.clear(); ALERTS.clear(); TG.clear()
    DB_OPEN.clear(); IC_GM["IC_V1"] = None; IC_GM["IC_V2"] = None
    for sid in list(KS.KILL_STRATEGIES):
        CFG[sid] = {"trade_execution_mode": "PAPER"}
    CLOCK[0] = 1000.0; SPAWNED.clear()
    KS._clock = lambda: CLOCK[0]
    KS._sleep = lambda s: CLOCK.__setitem__(0, CLOCK[0] + s)
    KS._spawn = SPAWNED.append
    adapters, verifiers = dict(KS._ADAPTERS), dict(KS._VERIFIERS)
    yield
    KS._ADAPTERS.clear(); KS._ADAPTERS.update(adapters)
    KS._VERIFIERS.clear(); KS._VERIFIERS.update(verifiers)


def _adapter(closed=1, remaining=0, detail=None):
    return lambda: {"closed": closed, "remaining": remaining,
                    "detail": detail or []}


# ── KS1: LIVE-only gating ───────────────────────────────────────────────────
def test_ks1_not_live_rejected():
    KS.register_adapter("SCALP_V1", _adapter())
    CFG["SCALP_V1"] = {"trade_execution_mode": "PAPER"}
    res = KS.kill("SCALP_V1")
    assert res == {"ok": False, "error": "NOT_LIVE",
                   "strategy_id": "SCALP_V1", "mode": "PAPER"}
    assert SAVED == []                                 # never touches config


# ── KS2: clean kill → flat verified → mode flips PAPER, LAST ────────────────
def test_ks2_clean_kill_flips_mode():
    KS.register_adapter("SCALP_V1", _adapter(closed=2, remaining=0))
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE", "keep": 42}
    res = KS.kill("SCALP_V1")
    assert res["ok"] is True and res["closed"] == 2 and res["mode_flipped"]
    assert SAVED and SAVED[-1][0] == "SCALP_V1"
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "PAPER"
    assert CFG["SCALP_V1"]["keep"] == 42               # rest of cfg preserved


# ── KS3: stuck kill → NO mode flip, CRITICAL fired ──────────────────────────
def test_ks3_stuck_no_flip():
    KS.register_adapter("SCALP_V1",
                        _adapter(closed=1, remaining=2, detail=["leg stuck"]))
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE"}
    DB_OPEN["SCALP_V1"] = 2              # KILL_PAPER_FLIP: the verifier agrees
    res = KS.kill("SCALP_V1")
    assert res["ok"] is False and res["remaining"] == 2
    assert res["mode_flipped"] is False
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE"   # unchanged
    assert TG                                                   # CRITICAL sent
    assert "leg stuck" in res["detail"]


# ── KS3b: unverifiable (-1) treated as stuck ────────────────────────────────
def test_ks3b_unverified_treated_stuck():
    KS.register_adapter("SCALP_V1", _adapter(closed=1, remaining=-1),
                        verify=lambda: -1)  # KILL_PAPER_FLIP: still can't tell
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("SCALP_V1")
    assert res["ok"] is False and res["mode_flipped"] is False
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE"


# ── KS4: adapter exception → error report, no flip ──────────────────────────
def test_ks4_adapter_exception():
    def boom(): raise RuntimeError("BROKER_GONE")
    KS.register_adapter("SCALP_V1", boom)
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("SCALP_V1")
    assert res["ok"] is False and "ADAPTER_ERROR" in res["error"]
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE"
    assert TG


# ── KS5: IC special eligibility — live group under non-LIVE config ──────────
def test_ks5_ic_live_group_override():
    class GM:
        def has_open_group(self): return True
        def is_paper(self): return False
        def kill_all(self):
            return {"ok": True, "closed": 4, "remaining": 0, "stuck_gtts": []}
    IC_GM["IC_V1"] = GM()
    CFG["IC_V1"] = {"trade_execution_mode": "PAPER"}   # config already flipped
    e = KS.eligibility()["IC_V1"]
    assert e["eligible"] and "LIVE group open" in e["reason"]
    res = KS.kill("IC_V1")
    assert res["ok"] is True and res["closed"] == 4
    assert CFG["IC_V1"]["trade_execution_mode"] == "PAPER"


# ── KS6: IC stuck GTTs propagate to detail, no flip ─────────────────────────
def test_ks6_ic_stuck_gtts_detail():
    class GM:
        def has_open_group(self): return True
        def is_paper(self): return False
        def kill_all(self):
            return {"ok": False, "closed": 0, "remaining": 4,
                    "stuck_gtts": [{"leg_id": "L1", "symbol": "N24150CE",
                                    "gtt_id": "901"}]}
    IC_GM["IC_V1"] = GM()
    CFG["IC_V1"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("IC_V1")
    assert res["ok"] is False and res["mode_flipped"] is False
    assert any("901" in d for d in res["detail"])
    assert CFG["IC_V1"]["trade_execution_mode"] == "LIVE"


# ── KS7: unknown strategy ───────────────────────────────────────────────────
def test_ks7_unknown():
    assert KS.kill("SCALP_V9")["error"] == "UNKNOWN_STRATEGY"


# ── KS8: in-flight lock ─────────────────────────────────────────────────────
def test_ks8_in_flight():
    KS.register_adapter("SCALP_V1", _adapter())
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE"}
    KS._LOCKS["SCALP_V1"].acquire()
    try:
        res = KS.kill("SCALP_V1")
        assert res == {"ok": False, "error": "IN_FLIGHT",
                       "strategy_id": "SCALP_V1"}
        assert KS.eligibility()["SCALP_V1"]["in_flight"] is True
    finally:
        KS._LOCKS["SCALP_V1"].release()


# ── KS9: register_adapter extension point (SCALP_V2/V4 local add) ───────────
def test_ks9_register_extension():
    KS.register_adapter("SCALP_V4", _adapter(closed=1, remaining=0))
    CFG["SCALP_V4"] = {"trade_execution_mode": "LIVE"}
    assert "SCALP_V4" in KS.KILL_STRATEGIES
    res = KS.kill("SCALP_V4")
    assert res["ok"] is True
    assert CFG["SCALP_V4"]["trade_execution_mode"] == "PAPER"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── KS5b (IC_SPLIT): IC_V2 has the same live-group override, per instance ───
def test_ks5b_ic_v2_live_group_override_isolated():
    class GM:
        def has_open_group(self): return True
        def is_paper(self): return False
        def kill_all(self):
            return {"ok": True, "closed": 4, "remaining": 0, "stuck_gtts": []}
    IC_GM["IC_V2"] = GM()                               # only V2 has a group
    CFG["IC_V2"] = {"trade_execution_mode": "PAPER"}
    CFG["IC_V1"] = {"trade_execution_mode": "PAPER"}
    elig = KS.eligibility()
    assert elig["IC_V2"]["eligible"] and "LIVE group open" in elig["IC_V2"]["reason"]
    assert not elig["IC_V1"]["eligible"]                # sibling NOT dragged in
    res = KS.kill("IC_V2")
    assert res["ok"] is True and res["closed"] == 4


# ════════════════════════════════════════════════════════════════════════════
# KILL_PAPER_FLIP_20261123 (DEF-095, DEF-096)
# ════════════════════════════════════════════════════════════════════════════
class _Core:
    def __init__(self, legs): self.legs = list(legs)
    def open_legs(self): return list(self.legs)


class _ClosingGM:
    """IC_V2 on 20 Nov: kill_all returns with one leg still filling; the leg
    closes `close_after` seconds later."""
    def __init__(self, close_after):
        self._core = _Core(["L4"]); self.t_close = CLOCK[0] + close_after
    def has_open_group(self): return bool(self._core.open_legs())
    def is_paper(self): return False
    def kill_all(self):
        return {"ok": True, "closed": 2, "remaining": 1, "stuck_gtts": []}
    def tick(self):
        if CLOCK[0] >= self.t_close: self._core.legs = []


def _ticking(gm):
    """Advance the fake clock AND let the in-flight leg land."""
    def _s(sec):
        CLOCK[0] += sec; gm.tick()
    return _s


# TC-EXEC-0244
def test_ks10_ic_exit_in_flight_settles_then_flips():
    gm = _ClosingGM(close_after=12); IC_GM["IC_V2"] = gm
    KS._sleep = _ticking(gm)
    CFG["IC_V2"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("IC_V2")
    assert res["ok"] is True and res["mode_flipped"] is True
    assert CFG["IC_V2"]["trade_execution_mode"] == "PAPER"
    assert any("filled" in d for d in res["detail"])
    assert TG == [] and SPAWNED == []                   # no false alarm, no watch
    assert 12 <= CLOCK[0] - 1000 <= KS.SETTLE_SECS


# TC-EXEC-0245
def test_ks11_still_open_after_settle_watch_flips_later():
    gm = _ClosingGM(close_after=300); IC_GM["IC_V2"] = gm
    KS._sleep = _ticking(gm)
    CFG["IC_V2"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("IC_V2")
    assert res["ok"] is False and res["mode_flipped"] is False
    assert res["watching"] is True and len(SPAWNED) == 1
    assert CFG["IC_V2"]["trade_execution_mode"] == "LIVE"   # not yet
    assert any("watching" in d for d in res["detail"])
    SPAWNED[0]()                                        # run the watch
    assert CFG["IC_V2"]["trade_execution_mode"] == "PAPER"
    assert any("now flat" in m and "PAPER" in m for _, m in ALERTS)


# TC-EXEC-0246
def test_ks12_watch_stops_when_mode_changed_by_hand():
    gm = _ClosingGM(close_after=300); IC_GM["IC_V2"] = gm
    CFG["IC_V2"] = {"trade_execution_mode": "LIVE"}
    def _s(sec):
        CLOCK[0] += sec; gm.tick()
        if CLOCK[0] > 1100: CFG["IC_V2"]["trade_execution_mode"] = "OFF"
    KS._sleep = _s
    KS.kill("IC_V2")
    n_saved = len(SAVED)
    SPAWNED[0]()
    assert CFG["IC_V2"]["trade_execution_mode"] == "OFF"   # never overwritten
    assert len(SAVED) == n_saved
    assert any("stopped watching" in a for a in AUDIT)


# TC-EXEC-0247
def test_ks13_new_kill_ends_old_watch():
    gm = _ClosingGM(close_after=300); IC_GM["IC_V2"] = gm
    CFG["IC_V2"] = {"trade_execution_mode": "LIVE"}
    KS.kill("IC_V2")
    old = SPAWNED[0]
    KS._WATCH_GEN["IC_V2"] += 1                         # what a new KILL does
    gm._core.legs = []
    old()
    assert CFG["IC_V2"]["trade_execution_mode"] == "LIVE"   # old watch left it


# TC-EXEC-0248
def test_ks14_watch_gives_up_after_watch_secs():
    gm = _ClosingGM(close_after=10 ** 9); IC_GM["IC_V2"] = gm
    CFG["IC_V2"] = {"trade_execution_mode": "LIVE"}
    KS.kill("IC_V2")
    TG.clear()
    SPAWNED[0]()
    assert CFG["IC_V2"]["trade_execution_mode"] == "LIVE"
    assert TG and "still not flat" in TG[-1]["message"]


# TC-EXEC-0249
@pytest.mark.parametrize("sid", ["BRK_V1", "ORB_V1"])
def test_ks15_int_result_brk_orb_flip(sid):
    KS.register_adapter(sid, lambda: 1, verify=lambda: 0)   # kill_all() -> int
    CFG[sid] = {"trade_execution_mode": "LIVE"}
    res = KS.kill(sid)
    assert res["ok"] is True and res["closed"] == 1 and res["mode_flipped"]
    assert CFG[sid]["trade_execution_mode"] == "PAPER"


# TC-EXEC-0250
def test_ks16_int_result_still_open_is_stuck_not_crash():
    KS.register_adapter("BRK_V1", lambda: 0, verify=lambda: 1)
    CFG["BRK_V1"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("BRK_V1")
    assert res["ok"] is False and res["remaining"] == 1
    assert CFG["BRK_V1"]["trade_execution_mode"] == "LIVE"


# TC-EXEC-0251
def test_ks17_tsg_shape_flat_flips():
    KS.register_adapter("TSG_V1", lambda: {"ok": True, "flat": True,
                                           "closed": ["L1", "L2"]})
    CFG["TSG_V1"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("TSG_V1")
    assert res["ok"] is True and res["closed"] == 2
    assert CFG["TSG_V1"]["trade_execution_mode"] == "PAPER"


# TC-EXEC-0252
def test_ks18_tsg_shape_not_flat_uses_verifier():
    left = [1]
    KS.register_adapter("TSG_V1", lambda: {"ok": False, "flat": False,
                                           "closed": ["L1"]},
                        verify=lambda: left[0])
    CFG["TSG_V1"] = {"trade_execution_mode": "LIVE"}
    KS._sleep = lambda sec: (CLOCK.__setitem__(0, CLOCK[0] + sec),
                             left.__setitem__(0, 0))
    res = KS.kill("TSG_V1")
    assert res["ok"] is True and res["closed"] == 1
    assert CFG["TSG_V1"]["trade_execution_mode"] == "PAPER"


# TC-EXEC-0253
def test_ks19_gc_shape_flips():
    KS.register_adapter("GC_V1", lambda: {"ok": True, "flattened_legs": 2},
                        verify=lambda: 0)
    CFG["GC_V1"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("GC_V1")
    assert res["ok"] is True and res["closed"] == 2
    assert CFG["GC_V1"]["trade_execution_mode"] == "PAPER"


# TC-EXEC-0254
def test_ks20_ic_stuck_gtts_do_not_wait():
    class GM:
        def has_open_group(self): return True
        def is_paper(self): return False
        def kill_all(self):
            return {"ok": False, "closed": 0, "remaining": 4,
                    "stuck_gtts": [{"leg_id": "L1", "symbol": "X", "gtt_id": "9"}]}
    IC_GM["IC_V1"] = GM()
    CFG["IC_V1"] = {"trade_execution_mode": "LIVE"}
    KS.kill("IC_V1")
    assert CLOCK[0] == 1000.0                           # answered at once


# TC-EXEC-0255
@pytest.mark.parametrize("raw", [None, "done", 3.5, ["x"]])
def test_ks21_odd_result_never_raises(raw):
    KS.register_adapter("SCALP_V4", lambda: raw)
    KS._VERIFIERS.pop("SCALP_V4", None)
    CFG["SCALP_V4"] = {"trade_execution_mode": "LIVE"}
    res = KS.kill("SCALP_V4")
    assert res["ok"] is False and res["remaining"] == -1
    assert CFG["SCALP_V4"]["trade_execution_mode"] == "LIVE"


# TC-EXEC-0256
def test_ks22_normalise_shapes():
    n = KS._normalise
    assert n(2) == {"closed": 2, "remaining": None, "detail": [], "no_settle": False}
    assert n({"closed": ["a", "b"], "flat": True})["remaining"] == 0
    assert n({"closed": "x", "remaining": "y", "detail": "one"}) == \
        {"closed": 0, "remaining": None, "detail": ["one"], "no_settle": False}
    assert n({"flattened_legs": 3})["closed"] == 3


# TC-EXEC-0257
def test_ks23_every_kill_strategy_has_a_verifier():
    missing = [s for s in KS.KILL_STRATEGIES
               if s not in KS._VERIFIERS and not s.endswith("_PROBE")
               and s not in ("SCALP_V4",)]
    assert missing == []
    for sid in ("GC_V1", "LVB_V1"):                     # registered at boot
        assert sid in KS._VERIFIERS
