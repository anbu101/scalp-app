# backend/app/config/test_ic_split_invariants.py
#
# ── IC_SPLIT (2026-08-04) ── STRUCTURAL GUARDS for the IC_V1/IC_V2 split.
# These are cheap, dependency-free assertions over the declarative surfaces
# (defaults, registry, whitelist, kill list, exemption tuple). They exist
# because the split's failure modes are all SILENT: an IC_V1 that quietly
# inherits carry semantics, or an IC_V2 dropped from the kill switch, does
# not raise — it just trades wrong. Run with:
#
#     cd backend/app/config && python3 -m pytest test_ic_split_invariants.py -q
#
# (Flat invocation, matching the house convention for stubbed suites.)

import ast
import re
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]


# ── stub the app tree so strategy_loader imports without pytz/kite ─────────
def _mk(n):
    m = types.ModuleType(n)
    sys.modules[n] = m
    return m


for _n in ["app", "app.event_bus", "app.config"]:
    if _n not in sys.modules:
        _mk(_n)
sys.modules["app.event_bus"].__path__ = []
_mk("app.event_bus.audit_logger").write_audit_log = lambda *a, **k: None

sys.path.insert(0, str(APP.parent))


def _load_module_dict(relpath: str, name: str):
    """Parse a config module and eval ONLY its top-level literal dicts —
    no imports executed, so this works in any environment."""
    src = (APP / relpath).read_text()
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            out = ast.literal_eval(node.value)
    return out


DEFAULTS = _load_module_dict("config/strategy_loader.py",
                             "DEFAULT_STRATEGY_CONFIGS")
LOTS_PATHS = _load_module_dict("config/lots_whitelist.py", "LOTS_PATHS")
REGISTRY = _load_module_dict("strategy/strategy_registry.py", "STRATEGIES")


# ── IS1: both instances exist everywhere they must ────────────────────────
def test_is1_both_ic_ids_present_in_every_surface():
    for sid in ("IC_V1", "IC_V2"):
        assert sid in DEFAULTS,   f"{sid} missing from DEFAULT_STRATEGY_CONFIGS"
        assert sid in LOTS_PATHS, f"{sid} missing from LOTS_PATHS"
        assert sid in REGISTRY,   f"{sid} missing from STRATEGIES registry"


# ── IS2: IC_V1 is the LEGACY condor — the two V2 switches are OFF ─────────
def test_is2_ic_v1_is_legacy_eod_no_carry_no_adjust():
    v1 = DEFAULTS["IC_V1"]
    assert v1["exit_mode"] == "EOD", \
        "IC_V1 must square off daily — NEXT_OPEN would carry overnight"
    assert v1["adjust_on_sl"] is False
    assert v1["adjust_only"] is False
    # no adjustment block at all: nothing for a stray read to pick up
    assert "adjust" not in v1
    # carry-only keys must be absent (their presence implies carry intent)
    assert "next_open_time" not in v1
    assert "expiry_exit_time" not in v1


# ── IS3: IC_V2 keeps the pre-split (backtest-validated) semantics ─────────
def test_is3_ic_v2_keeps_carry_and_adjustments():
    v2 = DEFAULTS["IC_V2"]
    assert v2["exit_mode"] == "NEXT_OPEN"
    assert v2["adjust_on_sl"] is True
    assert v2["next_open_time"] == "09:16"
    assert set(v2["adjust"].keys()) == {"L1", "L2"}


# ── IS4: both ship OFF — deploying the split trades nothing by itself ─────
def test_is4_both_ship_off():
    for sid in ("IC_V1", "IC_V2"):
        assert DEFAULTS[sid]["trade_execution_mode"] == "OFF"


# ── IS5: identical leg TEMPLATE (the split is semantics, not structure) ───
def test_is5_leg_templates_identical():
    v1, v2 = DEFAULTS["IC_V1"]["legs"], DEFAULTS["IC_V2"]["legs"]
    assert [l["id"] for l in v1] == ["L1", "L2", "L3", "L4"]
    assert v1 == v2, "leg templates must match — only exit/adjust semantics differ"


# ── IS6: IC_V1 lots whitelist must NOT expose adjust.* paths ──────────────
def test_is6_lots_whitelist_scoped_per_instance():
    assert not any(p.startswith("adjust.") for p in LOTS_PATHS["IC_V1"]), \
        "IC_V1 has no adjustment legs — adjust.* lots paths would be dead writes"
    assert any(p.startswith("adjust.") for p in LOTS_PATHS["IC_V2"])


# ── IS7: kill switch covers BOTH instances ───────────────────────────────
def test_is7_kill_switch_registers_both():
    src = (APP / "execution/kill_switch.py").read_text()
    kl = re.search(r"KILL_STRATEGIES = \[(.*?)\]", src, re.S).group(1)
    assert '"IC_V1"' in kl and '"IC_V2"' in kl
    # per-instance adapters, not one shared closure
    assert '_kill_ic("IC_V1")' in src and '_kill_ic("IC_V2")' in src


# ── IS8: the 15:25 paper sweep exempts ONLY the carrying instance ─────────
def test_is8_overnight_exemption_excludes_ic_v1():
    src = (APP / "db/paper_trade_squareoff.py").read_text()
    tup = re.search(r"OVERNIGHT_EXEMPT_STRATEGIES = \((.*?)\)", src, re.S).group(1)
    assert '"IC_V2"' in tup, "the carrying instance must stay exempt"
    assert '"IC_V1"' not in tup, \
        "IC_V1 squares off daily — exempting it would strand open paper rows"


# ── IS9: no module-level strategy identity left in the shared engine ──────
def test_is9_engine_has_no_hardcoded_identity():
    for f in ("ic_group_manager.py", "ic_engine.py", "ic_gtt_monitor.py",
              "ic_orphan_reconcile.py", "ic_carry_store.py"):
        src = (APP / "engine/ic" / f).read_text()
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "STRATEGY_ID":
                        pytest.fail(f"{f} still defines a module-level "
                                    f"STRATEGY_ID — identity must be per-instance")


# ── IS10: state-file paths are strategy-scoped (no shared latch/carry) ────
def test_is10_state_paths_are_per_strategy():
    gm = (APP / "engine/ic/ic_group_manager.py").read_text()
    cs = (APP / "engine/ic/ic_carry_store.py").read_text()
    assert 'f"{strategy_id}_day_latch.json"' in gm
    assert 'f"{strategy_id}_carry.json"' in cs
    assert 'f"{strategy_id}_session.json"' in cs
    # the old shared constants must be gone
    for bad in ('"IC_V1_day_latch.json"', '"IC_V1_carry.json"',
                '"IC_V1_session.json"'):
        assert bad not in gm and bad not in cs


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
