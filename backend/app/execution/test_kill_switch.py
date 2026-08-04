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


@pytest.fixture(autouse=True)
def clean():
    CFG.clear(); SAVED.clear(); AUDIT.clear(); ALERTS.clear(); TG.clear()
    DB_OPEN.clear(); IC_GM["IC_V1"] = None; IC_GM["IC_V2"] = None
    for sid in list(KS.KILL_STRATEGIES):
        CFG[sid] = {"trade_execution_mode": "PAPER"}
    yield


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
    res = KS.kill("SCALP_V1")
    assert res["ok"] is False and res["remaining"] == 2
    assert res["mode_flipped"] is False
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE"   # unchanged
    assert TG                                                   # CRITICAL sent
    assert "leg stuck" in res["detail"]


# ── KS3b: unverifiable (-1) treated as stuck ────────────────────────────────
def test_ks3b_unverified_treated_stuck():
    KS.register_adapter("SCALP_V1", _adapter(closed=1, remaining=-1))
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
