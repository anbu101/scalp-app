# backend/app/execution/test_kill_switch_adapters.py
# ── KILL_ADAPTER_TESTS_20261126 ── (TD-074)
#
# The KILL button is the emergency stop. Before this suite no test reached the
# per-strategy adapters (TMA_V1/V2, VET, BB, HA, SCALP_V1/V3/V5), the
# read-only verifiers or the exact report kill() hands the UI, so a renamed
# import or attribute would only have shown up when someone pressed KILL.
#
#   TC-EXEC-0260  every name the kill switch imports exists in the real code
#   TC-EXEC-0261…0266  adapters per strategy, each on a fake manager
#   TC-EXEC-0267  verifiers per strategy (read only, -1 = cannot tell)
#   TC-EXEC-0268…0276  kill(): report, audit, alerts, CRITICAL, flip, settle, watch
#
# Same stub-injection convention as test_kill_switch.py (no strategy engine is
# imported). Run: python3 test_kill_switch_adapters.py
import ast
import sys
import time
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]          # backend/app (real sources)


def _mk(n):
    m = types.ModuleType(n)
    sys.modules[n] = m
    return m


for _n in ["app", "app.event_bus", "app.config", "app.db", "app.api", "app.jobs",
           "app.core", "app.engine", "app.execution",
           "app.engine.bb_options", "app.engine.bb_v2", "app.engine.ha_options",
           "app.engine.scalp_v3", "app.engine.scalpv5", "app.engine.ic",
           "app.engine.tma", "app.engine.tma2", "app.engine.vet", "app.engine.brk",
           "app.engine.orb", "app.engine.emc", "app.engine.tsg", "app.engine.lvb",
           "app.engine.gc"]:
    _mk(_n)

AUDIT, ALERTS, TG, SAVED = [], [], [], []
CFG = {}
CFG_RAISE = set()
SAVE_RAISE = set()
DB_OPEN = {}
DB_RAISE = set()
OPEN = {"v3": 0, "v5": 0}
MGR = {}                       # SCALP_V3 / SCALP_V5 / TMA_V1 / TMA_V2 / VET_V1 managers
IC_GM = {}
SQ = {"closed": 0, "after": 0, "calls": 0}

_mk("app.event_bus.audit_logger").write_audit_log = AUDIT.append
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append((code, message, k))


def _load(sid):
    if sid in CFG_RAISE:
        raise OSError("cfg unreadable")
    c = CFG.get(sid)
    return None if c is None else dict(c)


def _save(sid, cfg):
    if sid in SAVE_RAISE:
        raise OSError("disk full")
    SAVED.append((sid, dict(cfg)))
    CFG[sid] = dict(cfg)


_scl = _mk("app.config.strategy_loader")
_scl.load_strategy_config = _load
_scl.save_strategy_config = _save


def _db(sid):
    if sid in DB_RAISE:
        raise RuntimeError("db locked")
    return [{}] * DB_OPEN.get(sid, 0)


_mk("app.db.trades_repo").get_open_trades_for_strategy = _db
_mk("app.api.telegram_api").notify_critical = TG.append


def _squareoff():
    SQ["calls"] += 1
    DB_OPEN["SCALP_V1"] = SQ["after"]
    return SQ["closed"]


_mk("app.jobs.scalp_live_eod")._squareoff_live_trades = _squareoff
_mk("app.core.engine_registry").BB_ENGINE_REGISTRY = []
_mk("app.core.ha_engine_registry").HA_ENGINE_REGISTRY = []


class BBOptionsTickEngine:
    def __init__(self, boom=False):
        self.calls, self.boom = 0, boom

    def eod_squareoff(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("engine down")


class BBOptionsTickEngineV2:                    # a separate class in the app, not a subclass
    __init__ = BBOptionsTickEngine.__init__
    eod_squareoff = BBOptionsTickEngine.eod_squareoff


class HAEngine(BBOptionsTickEngine):
    pass


_BB2 = "app.engine.bb_v2.bb_tick_engine_v2"
_mk("app.engine.bb_options.bb_tick_engine").BBOptionsTickEngine = BBOptionsTickEngine
_mk(_BB2).BBOptionsTickEngineV2 = BBOptionsTickEngineV2
_BB2_MOD = sys.modules[_BB2]
_mk("app.engine.scalp_v3.scalp_v3_selection_loop").get_manager = lambda: MGR.get("SCALP_V3")
_mk("app.db.scalp_v3_repo").get_all_open_v3_trades = lambda: [{}] * OPEN["v3"]
_mk("app.engine.scalpv5.scalpv5_selection_loop").get_manager = lambda: MGR.get("SCALP_V5")
_mk("app.db.scalpv5_repo").get_all_open_v5_trades = lambda: [{}] * OPEN["v5"]
_mk("app.engine.ic.ic_runtime").get_ic_manager = lambda sid: IC_GM.get(sid)
_mk("app.engine.tma.tma_selection_loop").get_manager = lambda: MGR.get("TMA_V1")
_mk("app.engine.tma2.tma2_selection_loop").get_manager = lambda: MGR.get("TMA_V2")
_mk("app.engine.vet.vet_selection_loop").get_manager = lambda: MGR.get("VET_V1")
RUNTIMES = {"BRK_V1": "app.engine.brk.brk_runtime", "ORB_V1": "app.engine.orb.orb_runtime",
            "EMC_V1": "app.engine.emc.emc_runtime", "TSG_V1": "app.engine.tsg.tsg_runtime",
            "LVB_V1": "app.engine.lvb.lvb_runtime", "GC_V1": "app.engine.gc.gc_runtime"}
for _sid, _m in RUNTIMES.items():
    _mk(_m)

import kill_switch as KS  # noqa: E402

sys.modules["app.execution.kill_switch"] = KS
REAL_SPAWN = KS._spawn
CLOCK = [1000.0]
SPAWNED = []


def _set_runtime(sid, mgr):
    attr = "_manager" if sid == "GC_V1" else "_MANAGER"
    setattr(sys.modules[RUNTIMES[sid]], attr, mgr)


@pytest.fixture(autouse=True)
def clean():
    for x in (AUDIT, ALERTS, TG, SAVED, SPAWNED):
        x.clear()
    for d in (CFG, DB_OPEN, MGR, IC_GM):
        d.clear()
    CFG_RAISE.clear(); SAVE_RAISE.clear(); DB_RAISE.clear()
    OPEN.update(v3=0, v5=0)
    SQ.update(closed=0, after=0, calls=0)
    sys.modules["app.core.engine_registry"].BB_ENGINE_REGISTRY.clear()
    sys.modules["app.core.ha_engine_registry"].HA_ENGINE_REGISTRY.clear()
    sys.modules[_BB2] = _BB2_MOD
    for sid in RUNTIMES:
        _set_runtime(sid, None)
    for sid in list(KS.KILL_STRATEGIES):
        CFG[sid] = {"trade_execution_mode": "PAPER"}
    CLOCK[0] = 1000.0
    KS._clock = lambda: CLOCK[0]
    KS._sleep = lambda s: CLOCK.__setitem__(0, CLOCK[0] + s)
    KS._spawn = SPAWNED.append
    adapters, verifiers, ids = dict(KS._ADAPTERS), dict(KS._VERIFIERS), list(KS.KILL_STRATEGIES)
    yield
    KS._ADAPTERS.clear(); KS._ADAPTERS.update(adapters)
    KS._VERIFIERS.clear(); KS._VERIFIERS.update(verifiers)
    KS.KILL_STRATEGIES[:] = ids


def _live(sid):
    CFG[sid] = {"trade_execution_mode": "LIVE"}


def _pressed(sid):
    return ("KILL_SWITCH", f"{sid}: KILL pressed — closing all live exposure and switching to PAPER.",
            {"severity": "error", "strategy_id": sid, "mode": "live"})


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0260 — the real code still has every name the KILL path calls
# (read from source: nothing heavy is imported, a rename fails here, not live)
# ════════════════════════════════════════════════════════════════════════════
CONTRACT = [
    # (file under backend/app, top-level names it must define)
    ("jobs/scalp_live_eod.py", ["_squareoff_live_trades"]),
    ("core/engine_registry.py", ["BB_ENGINE_REGISTRY"]),
    ("core/ha_engine_registry.py", ["HA_ENGINE_REGISTRY"]),
    ("engine/bb_options/bb_tick_engine.py", ["BBOptionsTickEngine"]),
    ("engine/bb_v2/bb_tick_engine_v2.py", ["BBOptionsTickEngineV2"]),
    ("engine/scalp_v3/scalp_v3_selection_loop.py", ["get_manager"]),
    ("db/scalp_v3_repo.py", ["get_all_open_v3_trades"]),
    ("engine/scalpv5/scalpv5_selection_loop.py", ["get_manager"]),
    ("db/scalpv5_repo.py", ["get_all_open_v5_trades"]),
    ("engine/ic/ic_runtime.py", ["get_ic_manager"]),
    ("engine/tma/tma_selection_loop.py", ["get_manager"]),
    ("engine/tma2/tma2_selection_loop.py", ["get_manager"]),
    ("engine/vet/vet_selection_loop.py", ["get_manager"]),
    ("db/trades_repo.py", ["get_open_trades_for_strategy"]),
    ("api/telegram_api.py", ["notify_critical"]),
    ("engine/brk/brk_runtime.py", ["_MANAGER"]),
    ("engine/orb/orb_runtime.py", ["_MANAGER"]),
    ("engine/emc/emc_runtime.py", ["_MANAGER"]),
    ("engine/tsg/tsg_runtime.py", ["_MANAGER"]),
    ("engine/lvb/lvb_runtime.py", ["_MANAGER"]),
    ("engine/gc/gc_runtime.py", ["_manager"]),
]
METHODS = [
    # (file, class, methods it must define, self.<attr> it must set)
    ("engine/tma/tma_trade_manager.py", "TMATradeManager", ["kill_close"], ["group"]),
    ("engine/tma2/tma2_trade_manager.py", "TMA2TradeManager", ["kill_close"], ["group"]),
    ("engine/vet/vet_manager.py", "VetManager", ["kill"], ["pos"]),
    ("engine/ic/ic_group_manager.py", "ICGroupManager",
     ["kill_all", "has_open_group", "is_paper"], ["_core"]),
    ("engine/ic/ic_live_core.py", None, ["open_legs"], []),
    ("engine/scalp_v3/scalp_v3_manager.py", None, ["eod_squareoff"], []),
    ("engine/scalpv5/scalpv5_manager.py", None, ["eod_squareoff"], []),
    ("engine/bb_options/bb_tick_engine.py", "BBOptionsTickEngine", ["eod_squareoff"], []),
    ("engine/bb_v2/bb_tick_engine_v2.py", "BBOptionsTickEngineV2", ["eod_squareoff"], []),
    ("engine/ha_options/ha_tick_engine.py", None, ["eod_squareoff"], []),
    ("engine/tsg/tsg_manager.py", None, [], ["_core"]),
    ("engine/tsg/tsg_live_core.py", None, ["open_ids"], []),
    ("engine/brk/brk_manager.py", None, [], ["pos"]),
    ("engine/orb/orb_manager.py", None, [], ["pos"]),
    ("engine/emc/emc_manager.py", None, [], ["pos"]),
    ("engine/lvb/lvb_manager.py", None, [], ["pos"]),
    ("engine/gc/gc_manager.py", None, [], ["position"]),
]


def _tree(rel):
    p = APP / rel
    assert p.exists(), f"{rel} is gone — the KILL adapter that imports it would fail when pressed"
    return ast.parse(p.read_text(encoding="utf-8"))


def _top_names(tree):
    out = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


@pytest.mark.parametrize("rel,names", CONTRACT, ids=[c[0] for c in CONTRACT])
def test_0260_imported_names_exist(rel, names):
    missing = [n for n in names if n not in _top_names(_tree(rel))]
    assert missing == [], f"{rel} no longer defines {missing}"


@pytest.mark.parametrize("rel,cls,methods,attrs", METHODS, ids=[m[0] for m in METHODS])
def test_0260_manager_methods_exist(rel, cls, methods, attrs):
    tree = _tree(rel)
    scope = tree
    if cls:
        found = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls]
        assert found, f"{rel} has no class {cls}"
        scope = found[0]
    defs = {n.name for n in ast.walk(scope) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    sets = {t.attr for n in ast.walk(scope) if isinstance(n, (ast.Assign, ast.AnnAssign))
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"}
    assert [m for m in methods if m not in defs] == [], f"{rel} lost a method KILL calls"
    assert [a for a in attrs if a not in sets] == [], f"{rel} no longer sets an attribute KILL reads"


def test_0260_kill_list_is_the_fleet():
    assert KS.KILL_STRATEGIES[:15] == [
        "SCALP_V1", "BB_V1", "BB_V2", "HA_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "IC_V2",
        "TMA_V1", "TMA_V2", "VET_V1", "ORB_V1", "EMC_V1", "BRK_V1", "TSG_V1"]
    assert set(KS._ADAPTERS) >= {"SCALP_V1", "BB_V1", "BB_V2", "HA_V1", "SCALP_V3", "SCALP_V5",
                                 "IC_V1", "IC_V2", "TMA_V1", "TMA_V2", "VET_V1"}
    assert all(sid in KS._LOCKS for sid in KS.KILL_STRATEGIES)


# ════════════════════════════════════════════════════════════════════════════
# adapters
# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0261 — SCALP_V1 reuses the EOD square-off, verifies on the trades table
def test_0261_scalp_v1_closes_and_flips():
    SQ.update(closed=3, after=0)
    DB_OPEN["SCALP_V1"] = 3
    _live("SCALP_V1")
    assert KS._kill_scalp_v1() == {"closed": 3, "remaining": 0, "detail": []}
    DB_OPEN["SCALP_V1"] = 3
    res = KS.kill("SCALP_V1")
    assert res == {"ok": True, "strategy_id": "SCALP_V1", "closed": 3, "remaining": 0,
                   "mode_flipped": True, "detail": []}
    assert SQ["calls"] == 2 and CFG["SCALP_V1"]["trade_execution_mode"] == "PAPER"


def test_0261_scalp_v1_row_left_open_is_remaining():
    SQ.update(closed=1, after=2)
    assert KS._kill_scalp_v1() == {"closed": 1, "remaining": 2, "detail": []}
    DB_RAISE.add("SCALP_V1")
    assert KS._kill_scalp_v1()["remaining"] == -1
    assert "[KILL][SCALP_V1][DB_CHECK_ERR] RuntimeError('db locked')" in AUDIT


# TC-EXEC-0262 — BB_V1 / BB_V2 square off only their own engines
def test_0262_bb_engines_filtered_by_class():
    reg = sys.modules["app.core.engine_registry"].BB_ENGINE_REGISTRY
    v1, v2 = BBOptionsTickEngine(), BBOptionsTickEngineV2()
    reg[:] = [v2, v1]
    assert KS._kill_bb("BB_V2") == {"closed": 1, "remaining": 0,
                                    "detail": [f"engine id={id(v2)} squareoff ok"]}
    assert (v1.calls, v2.calls) == (0, 1)
    DB_OPEN["BB_V1"] = 2
    assert KS._kill_bb("BB_V1") == {"closed": 1, "remaining": 2,
                                    "detail": [f"engine id={id(v1)} squareoff ok"]}
    assert (v1.calls, v2.calls) == (1, 1)
    reg[:] = []
    assert KS._kill_bb("BB_V2") == {"closed": 0, "remaining": 0, "detail": []}
    reg[:] = [v1, v2]
    DB_OPEN["BB_V1"] = 0
    _live("BB_V2")
    assert KS.kill("BB_V2")["ok"] is True and (v1.calls, v2.calls) == (1, 2)


def test_0262_bb_engine_error_does_not_stop_the_others():
    reg = sys.modules["app.core.engine_registry"].BB_ENGINE_REGISTRY
    bad, good = BBOptionsTickEngine(boom=True), BBOptionsTickEngine()
    reg[:] = [bad, good]
    r = KS._kill_bb("BB_V1")
    assert good.calls == 1 and r["closed"] == 2
    assert r["detail"] == [f"engine id={id(bad)} ERROR RuntimeError('engine down')",
                           f"engine id={id(good)} squareoff ok"]
    assert f"[KILL][BB_V1][ENGINE_ERR] id={id(bad)} RuntimeError('engine down')" in AUDIT


def test_0262_bb_v2_not_deployed_v1_still_works():
    reg = sys.modules["app.core.engine_registry"].BB_ENGINE_REGISTRY
    v1 = BBOptionsTickEngine()
    reg[:] = [v1]
    sys.modules[_BB2] = None                     # import of V2 fails
    assert KS._kill_bb("BB_V1")["closed"] == 1 and v1.calls == 1
    assert KS._kill_bb("BB_V2")["closed"] == 0 and v1.calls == 1
    _live("BB_V1")
    assert KS.kill("BB_V1")["mode_flipped"] is True


# TC-EXEC-0263 — HA_V1 squares off every registered engine
def test_0263_ha_engines():
    reg = sys.modules["app.core.ha_engine_registry"].HA_ENGINE_REGISTRY
    bad, good = HAEngine(boom=True), HAEngine()
    reg[:] = [bad, good]
    DB_OPEN["HA_V1"] = 1
    r = KS._kill_ha()
    assert r == {"closed": 2, "remaining": 1,
                 "detail": [f"engine id={id(bad)} ERROR RuntimeError('engine down')",
                            f"engine id={id(good)} squareoff ok"]}
    assert f"[KILL][HA_V1][ENGINE_ERR] id={id(bad)} RuntimeError('engine down')" in AUDIT
    DB_OPEN["HA_V1"] = 0
    _live("HA_V1")
    assert KS.kill("HA_V1")["ok"] is True


# TC-EXEC-0264 — SCALP_V3 / SCALP_V5: manager not running → DB says what is left
class _SqMgr:
    def __init__(self, key, n):
        self.key, self.n, self.calls = key, n, 0

    def eod_squareoff(self):
        self.calls += 1
        OPEN[self.key] = 0
        return self.n


@pytest.mark.parametrize("sid,key,fn", [("SCALP_V3", "v3", "_kill_scalp_v3"),
                                         ("SCALP_V5", "v5", "_kill_scalp_v5")])
def test_0264_scalp_v3_v5(sid, key, fn):
    OPEN[key] = 2
    assert getattr(KS, fn)() == {"closed": 0, "remaining": 2, "detail": ["manager not running"]}
    m = _SqMgr(key, 2)
    MGR[sid] = m
    assert getattr(KS, fn)() == {"closed": 2, "remaining": 0, "detail": []}
    OPEN[key] = 1
    m.n = 1
    _live(sid)
    res = KS.kill(sid)
    assert res["ok"] is True and res["closed"] == 1 and m.calls == 2
    assert KS._verify(sid) == 0
    OPEN[key] = 4
    assert KS._verify(sid) == 4


# TC-EXEC-0265 — IC: manager missing, stuck GTTs, DB disagrees, odd kill_all result
class _GM:
    def __init__(self, res, open_group=True, paper=False, legs=None):
        self.res, self.open, self.paper = res, open_group, paper
        if legs is not None:
            self._core = types.SimpleNamespace(open_legs=lambda: list(legs))

    def kill_all(self):
        return self.res

    def has_open_group(self):
        return self.open

    def is_paper(self):
        return self.paper


def test_0265_ic_adapter_shapes():
    DB_OPEN["IC_V1"] = 3
    assert KS._kill_ic("IC_V1") == {"closed": 0, "remaining": 3, "detail": ["manager not running"]}
    IC_GM["IC_V1"] = _GM({"closed": 4, "remaining": 0,
                          "stuck_gtts": [{"gtt_id": "901", "symbol": "N24150CE", "leg_id": "L1"}]})
    DB_OPEN["IC_V1"] = 2
    r = KS._kill_ic("IC_V1")
    assert r == {"closed": 4, "remaining": 2, "no_settle": True,
                 "detail": ["GTT 901 on N24150CE (L1) STILL ARMED — delete in Kite, then retry",
                            "2 open IC row(s) still in trades DB"]}
    IC_GM["IC_V1"] = _GM({})                          # says nothing
    DB_OPEN["IC_V1"] = 0
    assert KS._kill_ic("IC_V1") == {"closed": 0, "remaining": -1, "detail": [], "no_settle": False}
    IC_GM["IC_V1"] = _GM({"closed": 3, "remaining": 0, "stuck_gtts": []})
    DB_OPEN["IC_V1"] = 1                              # one row the manager forgot
    assert KS._kill_ic("IC_V1") == {"closed": 3, "remaining": 1, "no_settle": False,
                                    "detail": ["1 open IC row(s) still in trades DB"]}
    IC_GM["IC_V1"] = _GM({"closed": 1, "remaining": 5, "stuck_gtts": None})
    DB_OPEN["IC_V1"] = 1
    assert KS._kill_ic("IC_V1")["remaining"] == 5     # the larger count wins


# TC-EXEC-0266 — TMA_V1 / TMA_V2 / VET_V1: one group, kill_close / kill on the manager
class _GroupMgr:
    def __init__(self, attr, opened=True, closes=True, boom=False):
        self.attr, self.closes, self.boom, self.ts = attr, closes, boom, []
        setattr(self, attr, {"id": 1} if opened else None)

    def _do(self, ts):
        self.ts.append(ts)
        if self.boom:
            raise RuntimeError("broker gone")
        if self.closes:
            setattr(self, self.attr, None)

    def kill_close(self, ts):
        self._do(ts)

    def kill(self, ts):
        self._do(ts)


GROUP = [("TMA_V1", "_kill_tma", "group", "kill_close"),
         ("TMA_V2", "_kill_tma2", "group", "kill_close"),
         ("VET_V1", "_kill_vet", "pos", "kill")]


@pytest.mark.parametrize("sid,fn,attr,call", GROUP, ids=[g[0] for g in GROUP])
def test_0266_group_adapters(sid, fn, attr, call):
    run = getattr(KS, fn)
    assert run() == {"closed": 0, "remaining": 0, "detail": ["manager not running"]}
    m = _GroupMgr(attr)
    MGR[sid] = m
    before = int(time.time())
    want_detail = ["manager frozen against re-entry"] if sid == "VET_V1" else []
    assert run() == {"closed": 1, "remaining": 0, "detail": want_detail}
    assert len(m.ts) == 1 and isinstance(m.ts[0], int) and before <= m.ts[0] <= int(time.time())
    # already flat: nothing closed, nothing claimed
    assert run() == {"closed": 0, "remaining": 0, "detail": []}
    # still open after the call → stuck, says what to do
    MGR[sid] = _GroupMgr(attr, closes=False)
    r = run()
    assert r["closed"] == 0 and r["remaining"] == 1
    if sid == "VET_V1":
        assert r["detail"] == ["manager frozen against re-entry"]
    else:
        assert r["detail"] == ["group still open — forced exit is retrying its GTT cancel "
                               "(armed-GTT double-fire guard); retry KILL"]
    # the manager raises → MGR_ERR audit, remaining = what it had
    MGR[sid] = _GroupMgr(attr, boom=True)
    r = run()
    assert r == {"closed": 0, "remaining": 1,
                 "detail": [f"{call} ERROR RuntimeError('broker gone')"]}
    assert f"[KILL][{sid}][MGR_ERR] RuntimeError('broker gone')" in AUDIT
    MGR[sid] = _GroupMgr(attr, opened=False, boom=True)
    assert run()["remaining"] == 0
    # a manager that never set the attribute (half-built): read as flat, no crash
    bare = _GroupMgr(attr, opened=False, closes=False)
    delattr(bare, attr)
    MGR[sid] = bare
    assert run() == {"closed": 0, "remaining": 0, "detail": []}
    assert KS._verify(sid) == 0


@pytest.mark.parametrize("sid,fn,attr,call", GROUP, ids=[g[0] for g in GROUP])
def test_0266_group_kill_end_to_end(sid, fn, attr, call):
    MGR[sid] = _GroupMgr(attr)
    _live(sid)
    res = KS.kill(sid)
    assert res["ok"] is True and res["closed"] == 1 and res["mode_flipped"] is True
    assert CFG[sid]["trade_execution_mode"] == "PAPER"
    # a group that stays open: the verifier sees it, mode stays LIVE, a watch starts
    MGR[sid] = _GroupMgr(attr, closes=False)
    _live(sid)
    res = KS.kill(sid)
    assert res["ok"] is False and res["remaining"] == 1 and res["watching"] is True
    assert CFG[sid]["trade_execution_mode"] == "LIVE" and len(SPAWNED) == 1


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0267 — verifiers: read only, the strategy's own source of truth
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("sid", ["SCALP_V1", "BB_V1", "BB_V2", "HA_V1"])
def test_0267_trades_table_verifiers(sid):
    assert KS._verify(sid) == 0
    DB_OPEN[sid] = 2
    assert KS._verify(sid) == 2
    DB_RAISE.add(sid)
    assert KS._verify(sid) == -1


@pytest.mark.parametrize("sid", ["IC_V1", "IC_V2"])
def test_0267_ic_verifier(sid):
    assert KS._verify(sid) == 0                                   # no manager, DB empty
    DB_OPEN[sid] = 1
    assert KS._verify(sid) == 1
    IC_GM[sid] = _GM({}, legs=["L1", "L2", "L3"])
    assert KS._verify(sid) == 3                                   # max(legs, db)
    IC_GM[sid] = _GM({}, open_group=True)                         # no _core
    DB_OPEN[sid] = 0
    assert KS._verify(sid) == 1
    IC_GM[sid] = _GM({}, open_group=False)
    assert KS._verify(sid) == 0
    gm = _GM({}, open_group=True)
    gm._core = object()                                           # a core with no open_legs()
    IC_GM[sid] = gm
    assert KS._verify(sid) == 1
    DB_RAISE.add(sid)
    IC_GM[sid] = _GM({}, legs=["L1"])
    assert KS._verify(sid) == -1                                  # DB unreadable → cannot tell
    assert KS._VERIFIERS[sid]() == -1


@pytest.mark.parametrize("sid,attr", [("TMA_V1", "group"), ("TMA_V2", "group"), ("VET_V1", "pos")])
def test_0267_selection_verifiers(sid, attr):
    assert KS._verify(sid) == 0
    MGR[sid] = _GroupMgr(attr)
    assert KS._verify(sid) == 1
    MGR[sid] = _GroupMgr(attr, opened=False)
    assert KS._verify(sid) == 0


@pytest.mark.parametrize("sid", ["BRK_V1", "ORB_V1", "EMC_V1"])
def test_0267_pos_verifiers(sid):
    assert KS._verify(sid) == 0
    delattr(sys.modules[RUNTIMES[sid]], "_MANAGER")                # runtime never armed
    assert KS._verify(sid) == 0
    _set_runtime(sid, types.SimpleNamespace(pos=None))
    assert KS._verify(sid) == 0
    _set_runtime(sid, types.SimpleNamespace(pos={"leg": 1}))
    assert KS._verify(sid) == 1


def test_0267_tsg_lvb_gc_verifiers():
    for sid in ("TSG_V1", "LVB_V1", "GC_V1"):
        assert KS._verify(sid) == 0
    _set_runtime("TSG_V1", types.SimpleNamespace(_core=None))
    assert KS._verify("TSG_V1") == 0
    _set_runtime("TSG_V1", types.SimpleNamespace(_core=types.SimpleNamespace(open_ids=lambda: ["a", "b"])))
    assert KS._verify("TSG_V1") == 2
    _set_runtime("LVB_V1", types.SimpleNamespace(pos={"CE": 1, "PE": 2}))
    assert KS._verify("LVB_V1") == 2
    _set_runtime("GC_V1", types.SimpleNamespace(position=[1, 2, 3]))
    assert KS._verify("GC_V1") == 3
    _set_runtime("GC_V1", types.SimpleNamespace(position=None))
    assert KS._verify("GC_V1") == 0


def test_0267_verify_cannot_tell_is_minus_one():
    assert KS._verify("NOPE_V1") == -1
    KS._VERIFIERS["X_V1"] = lambda: -7
    assert KS._verify("X_V1") == -1
    KS._VERIFIERS["X_V1"] = lambda: 1 / 0
    assert KS._verify("X_V1") == -1
    assert "[KILL][X_V1][VERIFY_ERR] ZeroDivisionError('division by zero')" in AUDIT
    KS._VERIFIERS["X_V1"] = lambda: "4"
    assert KS._verify("X_V1") == 4


# ════════════════════════════════════════════════════════════════════════════
# kill() — what the UI, the audit log, alerts and Telegram get
# ════════════════════════════════════════════════════════════════════════════
def _adapter(closed=1, remaining=0, detail=None):
    return lambda: {"closed": closed, "remaining": remaining, "detail": list(detail or [])}


# TC-EXEC-0268 — refusals: unknown, not live, in flight; lower case accepted
def test_0268_refusals():
    assert KS.kill(None) == {"ok": False, "error": "UNKNOWN_STRATEGY", "strategy_id": ""}
    assert KS.kill("nope") == {"ok": False, "error": "UNKNOWN_STRATEGY", "strategy_id": "NOPE"}
    assert KS.kill("tma_v1") == {"ok": False, "error": "NOT_LIVE", "strategy_id": "TMA_V1",
                                 "mode": "PAPER"}
    CFG.pop("TMA_V1")
    assert KS.kill("TMA_V1")["mode"] == "OFF"
    assert AUDIT == [] and ALERTS == [] and SAVED == []
    KS.register_adapter("SCALP_V1", _adapter())
    CFG["SCALP_V1"] = {"trade_execution_mode": "paper_live"}       # PAPER_LIVE is live
    assert KS.kill("scalp_v1")["ok"] is True


def test_0268_lock_released_after_every_outcome():
    _live("SCALP_V1")

    def boom():
        raise RuntimeError("x")
    KS.register_adapter("SCALP_V1", boom)
    KS.kill("SCALP_V1")
    assert not KS._LOCKS["SCALP_V1"].locked()
    KS.register_adapter("SCALP_V1", _adapter(remaining=2), verify=lambda: 2)
    KS.kill("SCALP_V1")
    assert not KS._LOCKS["SCALP_V1"].locked()
    KS.register_adapter("SCALP_V1", _adapter())
    assert KS.kill("SCALP_V1")["ok"] is True and not KS._LOCKS["SCALP_V1"].locked()


# TC-EXEC-0269 — clean kill: audit, alerts, the report
def test_0269_clean_kill_trail():
    KS.register_adapter("SCALP_V1", _adapter(closed=2))
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE", "lots": 3}
    gen = KS._WATCH_GEN.get("SCALP_V1", 0)
    res = KS.kill("SCALP_V1")
    assert res == {"ok": True, "strategy_id": "SCALP_V1", "closed": 2, "remaining": 0,
                   "mode_flipped": True, "detail": []}
    assert KS._WATCH_GEN["SCALP_V1"] == gen + 1
    assert CFG["SCALP_V1"] == {"trade_execution_mode": "PAPER", "lots": 3}
    assert AUDIT == ["[KILL][SCALP_V1] ═══ KILL SWITCH PRESSED ═══",
                     "[KILL][SCALP_V1] mode LIVE → PAPER",
                     "[KILL][SCALP_V1] complete closed=2 mode_flipped=True"]
    assert ALERTS == [_pressed("SCALP_V1"),
                      ("KILL_SWITCH", "SCALP_V1: KILL complete — 2 closed, flat, mode → PAPER",
                       {"severity": "info", "strategy_id": "SCALP_V1"})]
    assert TG == [] and SPAWNED == []


# TC-EXEC-0270 — flat but the PAPER flip fails: told loudly, ok stays True
def test_0270_flat_but_flip_failed():
    KS.register_adapter("SCALP_V1", _adapter(closed=1))
    _live("SCALP_V1")
    SAVE_RAISE.add("SCALP_V1")
    res = KS.kill("SCALP_V1")
    assert res == {"ok": True, "strategy_id": "SCALP_V1", "closed": 1, "remaining": 0,
                   "mode_flipped": False,
                   "detail": ["positions flat but MODE FLIP FAILED — flip to PAPER manually in Settings"]}
    assert "[KILL][SCALP_V1][MODE_FLIP_FAIL] OSError('disk full')" in AUDIT
    assert AUDIT[-1] == "[KILL][SCALP_V1] complete closed=1 mode_flipped=False"
    assert TG == [{"message": "SCALP_V1 KILL: flat, but the PAPER mode flip failed — set it manually.",
                   "severity": "error"}]
    assert ALERTS[-1] == ("KILL_SWITCH", "SCALP_V1: KILL complete — 1 closed, flat, MODE FLIP FAILED",
                          {"severity": "error", "strategy_id": "SCALP_V1"})
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE"


# TC-EXEC-0271 — adapter raises: CRITICAL, no flip, exact error
def test_0271_adapter_error():
    def boom():
        raise RuntimeError("BROKER_GONE")
    KS.register_adapter("SCALP_V1", boom)
    _live("SCALP_V1")
    res = KS.kill("SCALP_V1")
    assert res == {"ok": False, "error": "ADAPTER_ERROR: RuntimeError('BROKER_GONE')",
                   "strategy_id": "SCALP_V1"}
    assert AUDIT == ["[KILL][SCALP_V1] ═══ KILL SWITCH PRESSED ═══",
                     "[KILL][SCALP_V1][ADAPTER_ERR] RuntimeError('BROKER_GONE')"]
    assert TG == [{"message": "SCALP_V1 KILL FAILED with RuntimeError('BROKER_GONE') — verify "
                              "positions in Kite NOW.", "severity": "error"}]
    assert ALERTS == [_pressed("SCALP_V1")] and SAVED == []


# TC-EXEC-0272 — stuck: no flip, a watch, CRITICAL + alert with the count
def test_0272_stuck_trail():
    KS.register_adapter("SCALP_V1", _adapter(closed=1, remaining=2, detail=["leg stuck"]),
                        verify=lambda: 2)
    _live("SCALP_V1")
    res = KS.kill("SCALP_V1")
    watch = "watching: mode switches to PAPER by itself once flat (up to 30 min)"
    assert res == {"ok": False, "strategy_id": "SCALP_V1", "closed": 1, "remaining": 2,
                   "mode_flipped": False, "detail": ["leg stuck", watch], "watching": True}
    assert len(SPAWNED) == 1
    assert AUDIT[-1] == (f"[KILL][SCALP_V1][STUCK] closed=1 remaining=2 waited=30.0s "
                         f"watching=True detail={['leg stuck', watch]}")
    assert TG == [{"message": "SCALP_V1 KILL INCOMPLETE — 2 position(s) still open. Mode NOT "
                              "flipped yet (it flips by itself once flat). Check Kite, then retry KILL.",
                   "severity": "error"}]
    assert ALERTS[-1] == ("KILL_SWITCH", "SCALP_V1: KILL INCOMPLETE — 2 still open; mode NOT "
                                         "flipped. Retry after checking Kite.",
                          {"severity": "error", "strategy_id": "SCALP_V1"})
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE" and SAVED == []


def test_0272_unverified_without_verifier_no_watch():
    KS.register_adapter("SCALP_V4", _adapter(closed=0, remaining=-1))
    KS._VERIFIERS.pop("SCALP_V4", None)
    _live("SCALP_V4")
    res = KS.kill("SCALP_V4")
    assert res["remaining"] == -1 and res["watching"] is False and res["detail"] == []
    assert SPAWNED == [] and CLOCK[0] == 1000.0                    # nothing to poll
    assert TG[-1]["message"] == ("SCALP_V4 KILL INCOMPLETE — unverified position(s) still open. "
                                 "Mode NOT flipped. Check Kite, then retry KILL.")
    assert "unverified still open" in ALERTS[-1][1]


def test_0272_ic_stuck_gtts_no_wait_in_trail():
    IC_GM["IC_V1"] = _GM({"closed": 0, "remaining": 4,
                          "stuck_gtts": [{"gtt_id": "9", "symbol": "X", "leg_id": "L1"}]})
    _live("IC_V1")
    res = KS.kill("IC_V1")
    assert res["remaining"] == 4 and CLOCK[0] == 1000.0
    assert AUDIT[-1].startswith("[KILL][IC_V1][STUCK] closed=0 remaining=4 waited=0.0s watching=True")


# TC-EXEC-0273 — settle: an exit still filling lands inside the wait → flip
def test_0273_settle_then_flip():
    left = [1]

    def _s(sec):
        CLOCK[0] += sec
        if CLOCK[0] - 1000 >= 4:
            left[0] = 0
    KS._sleep = _s
    KS.register_adapter("SCALP_V1", _adapter(closed=2, remaining=1), verify=lambda: left[0])
    _live("SCALP_V1")
    res = KS.kill("SCALP_V1")
    assert res["ok"] is True and res["mode_flipped"] is True
    assert res["detail"] == ["last exit filled 4s after the close orders — flat"]
    assert "[KILL][SCALP_V1] flat after settling 4.0s" in AUDIT
    assert CLOCK[0] == 1004.0                                       # polled every 2 s


def test_0273_settle_gives_up_at_settle_secs():
    KS._VERIFIERS["SCALP_V1"] = lambda: 1
    assert KS._settle("SCALP_V1", 1) == (1, KS.SETTLE_SECS)
    assert CLOCK[0] == 1000.0 + KS.SETTLE_SECS
    assert KS._settle("NOPE_V1", 3) == (3, 0.0)                     # no verifier: no wait
    KS._VERIFIERS["SCALP_V1"] = lambda: 0
    assert KS._settle("SCALP_V1", 1) == (0, 0.0)


def test_0273_result_without_remaining_is_verified():
    KS.register_adapter("BRK_V1", lambda: 2, verify=lambda: 0)
    _live("BRK_V1")
    res = KS.kill("BRK_V1")
    assert res["ok"] is True and res["closed"] == 2 and CLOCK[0] == 1000.0
    assert res["detail"] == []                                      # flat at once: no settle line


# TC-EXEC-0274 — the background watch: flips once flat, or says why not
def _stuck_then(flat_after_polls, flip_fails=False):
    n = [0]

    def v():
        n[0] += 1
        return 0 if n[0] > flat_after_polls else 1
    KS.register_adapter("SCALP_V1", _adapter(closed=1, remaining=1), verify=v)
    _live("SCALP_V1")
    SPAWNED.clear()
    KS.kill("SCALP_V1")
    assert len(SPAWNED) == 1
    if flip_fails:
        SAVE_RAISE.add("SCALP_V1")
    TG.clear(); ALERTS.clear()


def test_0274_watch_flips_when_flat():
    _stuck_then(flat_after_polls=20)
    t0 = CLOCK[0]
    SPAWNED[0]()
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "PAPER"
    assert "[KILL][SCALP_V1][WATCH] flat now — mode_flipped=True" in AUDIT
    assert ALERTS == [("KILL_SWITCH", "SCALP_V1: now flat — mode → PAPER",
                       {"severity": "info", "strategy_id": "SCALP_V1"})]
    assert TG == [] and (CLOCK[0] - t0) % KS.WATCH_POLL == 0


def test_0274_watch_flat_but_flip_failed():
    _stuck_then(flat_after_polls=17, flip_fails=True)
    SPAWNED[0]()
    assert "[KILL][SCALP_V1][WATCH] flat now — mode_flipped=False" in AUDIT
    assert ALERTS == [("KILL_SWITCH", "SCALP_V1: now flat — MODE FLIP FAILED, set PAPER in Settings",
                       {"severity": "error", "strategy_id": "SCALP_V1"})]
    assert TG == [{"message": "SCALP_V1 KILL: flat, but the PAPER mode flip failed — set it manually.",
                   "severity": "error"}]


def test_0274_watch_gives_up():
    _stuck_then(flat_after_polls=10 ** 9)
    t0 = CLOCK[0]
    SPAWNED[0]()
    assert CFG["SCALP_V1"]["trade_execution_mode"] == "LIVE"
    assert AUDIT[-1] == "[KILL][SCALP_V1][WATCH] still not flat after 30 min — mode left as it is"
    assert TG == [{"message": "SCALP_V1 KILL: still not flat after 30 min — check Kite, then retry KILL.",
                   "severity": "error"}]
    assert CLOCK[0] - t0 == KS.WATCH_SECS                            # stops at the deadline


def test_0274_watch_stops_on_mode_change_or_new_kill():
    _stuck_then(flat_after_polls=10 ** 9)
    CFG["SCALP_V1"]["trade_execution_mode"] = "PAPER_LIVE"          # still live: keeps going
    KS._WATCH_GEN["SCALP_V1"] += 1                                  # a newer KILL
    t0 = CLOCK[0]
    SPAWNED[0]()
    assert CLOCK[0] - t0 == KS.WATCH_POLL and not any("[WATCH]" in a for a in AUDIT)
    _stuck_then(flat_after_polls=10 ** 9)
    CFG["SCALP_V1"]["trade_execution_mode"] = "OFF"
    SPAWNED[0]()
    assert AUDIT[-1] == "[KILL][SCALP_V1][WATCH] mode is now OFF — stopped watching"
    assert TG == []


# TC-EXEC-0275 — config read / write helpers and the result contract
def test_0275_mode_and_flip_helpers():
    CFG["SCALP_V1"] = {"trade_execution_mode": "live"}
    assert KS._mode("SCALP_V1") == "LIVE"
    CFG["SCALP_V1"] = {}
    assert KS._mode("SCALP_V1") == "OFF"
    CFG.pop("SCALP_V1")
    assert KS._mode("SCALP_V1") == "OFF"
    CFG_RAISE.add("SCALP_V1")
    assert KS._mode("SCALP_V1") == "OFF"
    assert "[KILL][SCALP_V1][CFG_READ_ERR] OSError('cfg unreadable')" in AUDIT
    CFG_RAISE.clear()
    CFG["SCALP_V1"] = {"trade_execution_mode": "LIVE", "lots": 2}
    assert KS._flip_to_paper("SCALP_V1") is True
    assert SAVED == [("SCALP_V1", {"trade_execution_mode": "PAPER", "lots": 2})]
    CFG.pop("SCALP_V1")
    assert KS._flip_to_paper("SCALP_V1") is True
    assert AUDIT[-1] == "[KILL][SCALP_V1] mode None → PAPER"
    sys.modules["app.api.telegram_api"].notify_critical = lambda d: 1 / 0
    try:
        KS._notify_critical("x")                                    # never raises
    finally:
        sys.modules["app.api.telegram_api"].notify_critical = TG.append


def test_0275_normalise_contract():
    n = KS._normalise
    assert n(True) == {"closed": 1, "remaining": None, "detail": [], "no_settle": False}
    assert n(None) == {"closed": 0, "remaining": None, "no_settle": False,
                       "detail": ["adapter returned NoneType"]}
    assert n("done")["detail"] == ["adapter returned str"]
    assert n({"closed": 2, "remaining": "3", "detail": [1, "b"], "no_settle": 1}) == \
        {"closed": 2, "remaining": 3, "detail": ["1", "b"], "no_settle": True}
    assert n({"closed": None, "flattened_legs": None}) == \
        {"closed": 0, "remaining": None, "detail": [], "no_settle": False}
    assert n({"closed": ("a",), "flat": False})["remaining"] is None
    assert n({"closed": {"a", "b"}, "flat": True, "remaining": 2})["remaining"] == 2
    assert n({"closed": 0, "remaining": 0})["remaining"] == 0


# TC-EXEC-0276 — eligibility and the extension point
def test_0276_eligibility():
    _live("TMA_V1")
    e = KS.eligibility()
    assert e["TMA_V1"] == {"eligible": True, "mode": "LIVE", "reason": "LIVE mode", "in_flight": False}
    assert e["TMA_V2"] == {"eligible": False, "mode": "PAPER", "reason": "", "in_flight": False}
    assert set(e) == set(KS.KILL_STRATEGIES)
    IC_GM["IC_V1"] = _GM({}, paper=True)                            # a paper group: not killable
    assert KS.eligibility()["IC_V1"]["eligible"] is False

    class Boom:
        def has_open_group(self):
            raise RuntimeError("x")
    IC_GM["IC_V1"] = Boom()
    assert KS.eligibility()["IC_V1"]["eligible"] is False
    IC_GM["IC_V1"] = _GM({}, open_group=False)
    assert KS.eligibility()["IC_V1"]["eligible"] is False
    IC_GM["TMA_V2"] = _GM({})                                       # the override is IC only
    assert KS.eligibility()["TMA_V2"]["eligible"] is False


def test_0276_register_adapter_keeps_builtin_verifier():
    builtin = KS._VERIFIERS["TMA_V1"]
    KS.register_adapter("TMA_V1", _adapter())
    assert KS._VERIFIERS["TMA_V1"] is builtin and KS.KILL_STRATEGIES.count("TMA_V1") == 1
    KS.register_adapter("NEW_V1", _adapter(), verify=lambda: 0)
    assert KS.KILL_STRATEGIES[-1] == "NEW_V1" and not KS._LOCKS["NEW_V1"].locked()
    assert KS._verify("NEW_V1") == 0


def test_0276_spawn_runs_a_daemon_thread(monkeypatch):
    seen = []

    class T:
        def __init__(self, **k):
            seen.append(k)

        def start(self):
            seen[-1]["target"]()
    hit = []
    monkeypatch.setattr(KS.threading, "Thread", T)
    REAL_SPAWN(lambda: hit.append(1))
    assert hit == [1] and seen[0]["daemon"] is True and seen[0]["name"] == "kill-watch"


if __name__ == "__main__":
    # pytest in a child, from this folder (as scripts/run_suites.py runs it): run in-process
    # after the stubs above, pytest could not import this file under its package name
    import os
    import subprocess
    _here = os.path.dirname(os.path.abspath(__file__))
    _env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(APP.parent), _here] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              os.path.basename(__file__)], cwd=_here, env=_env))
