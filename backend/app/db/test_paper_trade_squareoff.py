# backend/app/db/test_paper_trade_squareoff.py
#
# OVERNIGHT_EXEMPT (2026-07-29): the generic 15:25 paper sweep must NOT
# close IC_V2's carried overnight legs, must still sweep everything else
# (including legacy NULL-strategy rows), and must stay idempotent.
# Stub convention as the IC suites; real in-memory sqlite behind get_conn.
# Run: python3 test_paper_trade_squareoff.py
import sys
import types
import sqlite3
import pytest


def _mk(n):
    m = types.ModuleType(n); sys.modules[n] = m; return m

for n in ["app", "app.db", "app.event_bus", "app.marketdata"]:
    _mk(n)

AUDIT = []
_mk("app.event_bus.audit_logger").write_audit_log = lambda s: AUDIT.append(s)

_conn = sqlite3.connect(":memory:")
_conn.row_factory = sqlite3.Row
_conn.execute("""
    CREATE TABLE paper_trades (
        paper_trade_id TEXT PRIMARY KEY,
        strategy_name  TEXT,
        symbol         TEXT,
        token          INTEGER,
        entry_price    REAL,
        qty            INTEGER,
        state          TEXT
    )
""")
_mk("app.db.sqlite").get_conn = lambda: _conn

CLOSED = []
def close_paper_trade(*, paper_trade_id, exit_price, exit_reason):
    CLOSED.append((paper_trade_id, exit_price, exit_reason))
    _conn.execute("UPDATE paper_trades SET state='CLOSED' WHERE paper_trade_id=?",
                  (paper_trade_id,))
_mk("app.db.paper_trades_repo").close_paper_trade = close_paper_trade

_mk("app.marketdata.ltp_store").LTPStore = type(
    "LTPStore", (), {"get": staticmethod(lambda sym: 100.0)})

import paper_trade_squareoff as SQ
sys.modules["app.db.paper_trade_squareoff"] = SQ


@pytest.fixture(autouse=True)
def clean():
    _conn.execute("DELETE FROM paper_trades")
    CLOSED.clear(); AUDIT.clear()
    yield


def _row(tid, strat, state="OPEN"):
    _conn.execute(
        "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?)",
        (tid, strat, f"SYM{tid}", 1, 50.0, 65, state))


# ── PS1: IC rows survive the sweep; everything else closes ──────────────────
def test_ps1_ic_exempt_others_swept():
    _row("t1", "BB_V1")
    _row("t2", "IC_V2")          # carried condor leg — MUST survive
    _row("t3", "IC_V2")
    _row("t4", "SCALP_V1")
    SQ.square_off_open_paper_trades()
    closed_ids = {c[0] for c in CLOSED}
    assert closed_ids == {"t1", "t4"}
    states = dict(_conn.execute(
        "SELECT paper_trade_id, state FROM paper_trades"))
    assert states["t2"] == "OPEN" and states["t3"] == "OPEN"
    assert all(c[2] == "EOD_SQUARE_OFF" for c in CLOSED)
    assert any("EXEMPT" in a and "IC_V2" in a and "2 open" in a for a in AUDIT)


# ── PS2: legacy NULL-strategy rows are STILL swept ──────────────────────────
def test_ps2_null_strategy_still_swept():
    _row("t9", None)
    SQ.square_off_open_paper_trades()
    assert [c[0] for c in CLOSED] == ["t9"]


# ── PS3: only-IC-open → clean no-op sweep, exemption logged ─────────────────
def test_ps3_only_ic_open_noop():
    _row("t2", "IC_V2")
    SQ.square_off_open_paper_trades()
    assert CLOSED == []
    assert any("No open trades to square off" in a for a in AUDIT)
    assert any("EXEMPT" in a for a in AUDIT)


# ── PS4: idempotent — second run is a clean no-op ───────────────────────────
def test_ps4_idempotent():
    _row("t1", "BB_V1"); _row("t2", "IC_V2")
    SQ.square_off_open_paper_trades()
    n1 = len(CLOSED)
    SQ.square_off_open_paper_trades()
    assert len(CLOSED) == n1 == 1


# ── PS5: the exemption tuple is exactly the locked set ──────────────────────
def test_ps5_exempt_set_locked():
    # NOTE: this assertion was stale in the pre-split tree (said ("IC_V1",)
    # while the code already exempted TSG_V1 too). Fixed to the true set.
    assert SQ.OVERNIGHT_EXEMPT_STRATEGIES == ("IC_V2", "TSG_V1")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
