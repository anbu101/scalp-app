# backend/app/engine/ic/test_ic_orphan_reconcile.py
#
# ORPHAN RECONCILER (2026-08-03): unowned open PAPER rows neutral-close at
# boot; OWNED rows are untouched; LIVE rows are FLAGGED, never closed.
# Run: python3 test_ic_orphan_reconcile.py
import sys
import types
import sqlite3
import pytest


def _mk(n):
    m = types.ModuleType(n); sys.modules[n] = m; return m

for n in ["app", "app.db", "app.event_bus", "app.api", "app.engine",
          "app.engine.ic"]:
    _mk(n)

AUDIT = []
_mk("app.event_bus.audit_logger").write_audit_log = lambda s: AUDIT.append(s)
ALERTS = []
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append((code, message))
TG = []
_mk("app.api.telegram_api").notify_critical = lambda d: TG.append(d)

_conn = sqlite3.connect(":memory:")
_conn.row_factory = sqlite3.Row
_conn.execute("""CREATE TABLE paper_trades (
    paper_trade_id TEXT PRIMARY KEY, strategy_name TEXT, symbol TEXT,
    entry_price REAL, state TEXT, exit_price REAL, exit_reason TEXT)""")
_mk("app.db.sqlite").get_conn = lambda: _conn

CLOSED = []
def close_paper_trade(*, paper_trade_id, exit_price, exit_reason):
    CLOSED.append((paper_trade_id, exit_price, exit_reason))
    _conn.execute("UPDATE paper_trades SET state='CLOSED', exit_price=?, "
                  "exit_reason=? WHERE paper_trade_id=?",
                  (exit_price, exit_reason, paper_trade_id))
_mk("app.db.paper_trades_repo").close_paper_trade = close_paper_trade

LIVE_ROWS = []
_mk("app.db.trades_repo").get_open_trades_for_strategy = \
    lambda sid: list(LIVE_ROWS)

import ic_orphan_reconcile as OR
sys.modules["app.engine.ic.ic_orphan_reconcile"] = OR


class FakeLeg:
    def __init__(self, lid): self.leg_id = lid
class FakeCore:
    def __init__(self, lids): self.legs = {l: FakeLeg(l) for l in lids}
class FakeGM:
    strategy_id = "IC_V2"         # ── IC_SPLIT ── reconciler scopes by this
    def __init__(self, owned):    # owned: {leg_id: db_id}
        self._core = FakeCore(list(owned))
        self._owned = owned
    def current_group(self): return self._core
    def leg_runtime(self, lid): return {"db_id": self._owned.get(lid)}


@pytest.fixture(autouse=True)
def clean():
    _conn.execute("DELETE FROM paper_trades")
    CLOSED.clear(); AUDIT.clear(); ALERTS.clear(); TG.clear()
    LIVE_ROWS.clear()
    yield


def _paper(pid, symbol="NIFTYX", entry=70.0, state="OPEN"):
    _conn.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,NULL,NULL)",
                  (pid, "IC_V2", symbol, entry, state))


# ── OR1: orphans neutral-closed; owned rows untouched ───────────────────────
def test_or1_orphans_closed_owned_kept():
    _paper("own1"); _paper("own2")
    _paper("orph1", entry=77.10); _paper("orph2", entry=4.00)
    gm = FakeGM({"L1": "own1", "L2": "own2"})
    res = OR.reconcile_orphan_rows(gm)
    assert res == {"paper_closed": 2, "live_flagged": 0}
    assert {c[0] for c in CLOSED} == {"orph1", "orph2"}
    assert all(c[2] == "MANUAL" for c in CLOSED)
    # neutral close: exit at the stored entry
    assert dict((c[0], c[1]) for c in CLOSED) == {"orph1": 77.10, "orph2": 4.00}
    states = dict(_conn.execute(
        "SELECT paper_trade_id, state FROM paper_trades"))
    assert states["own1"] == "OPEN" and states["own2"] == "OPEN"
    assert any(a[0] == "IC_ORPHAN_RECONCILE" for a in ALERTS)


# ── OR2: no group at all → every open IC paper row is an orphan ─────────────
def test_or2_no_group_all_orphans():
    _paper("a"); _paper("b"); _paper("c"); _paper("d")
    class NoGroupGM:
        strategy_id = "IC_V2"
        def current_group(self): return None
        def leg_runtime(self, lid): return {}
    res = OR.reconcile_orphan_rows(NoGroupGM())
    assert res["paper_closed"] == 4


# ── OR3: LIVE rows are FLAGGED, never closed ────────────────────────────────
def test_or3_live_flag_only():
    LIVE_ROWS.extend([{"trade_id": "t-live-1", "symbol": "NIFTYLIVE"}])
    gm = FakeGM({})
    res = OR.reconcile_orphan_rows(gm)
    assert res["live_flagged"] == 1 and res["paper_closed"] == 0
    assert CLOSED == []                          # nothing touched
    assert any(a[0] == "IC_ORPHAN_LIVE" for a in ALERTS)
    assert TG                                    # CRITICAL fired


# ── OR4: owned live rows are not flagged ────────────────────────────────────
def test_or4_owned_live_not_flagged():
    LIVE_ROWS.extend([{"trade_id": "t-own", "symbol": "NIFTYLIVE"}])
    gm = FakeGM({"L1": "t-own"})
    res = OR.reconcile_orphan_rows(gm)
    assert res["live_flagged"] == 0 and not TG


# ── OR5: clean boot → quiet no-op ───────────────────────────────────────────
def test_or5_clean_noop():
    res = OR.reconcile_orphan_rows(FakeGM({}))
    assert res == {"paper_closed": 0, "live_flagged": 0}
    assert not ALERTS and not TG
    assert any("no orphaned rows" in a for a in AUDIT)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
