# backend/app/db/test_paper_charges_direction.py — fence PAPER_STT_SIDE_20261121
#
# DEF-075: close_paper_trade() called calculate_option_charges() without the
# trade's direction, so every SHORT paper row paid STT (0.15%) on the BUY-BACK
# price instead of the SELL (entry) price. Real close_paper_trade against an
# in-memory sqlite table; the expected numbers come from the backtest's own
# charges_model, so paper and backtest charges must agree to the paisa.
#   TC-PCHG-0001  SHORT row closed at a loss: STT on the entry (sell) premium
#   TC-PCHG-0002  SHORT row closed at a profit: STT on the entry (sell) premium
#   TC-PCHG-0003  LONG row: STT stays on the exit (sell) premium — unchanged
#   TC-PCHG-0004  caller override trade_direction="SHORT" on a row stored LONG
#                 is charged as a SHORT; net = gross − charges
#   TC-PCHG-0005  paper SHORT total charges == backtest charges_model to the paisa
#   TC-PCHG-0006  the 27-Oct TSG 22700PE case (sold 102.00, bought back 5.10,
#                 qty 650): STT 99.45, not 4.97
# Run: cd backend/app/db && python3 -m pytest -q test_paper_charges_direction.py
import sqlite3
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_conn = sqlite3.connect(":memory:", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("""
    CREATE TABLE paper_trades (
        paper_trade_id TEXT PRIMARY KEY, strategy_name TEXT, symbol TEXT,
        entry_price REAL, qty INTEGER, trade_direction TEXT, state TEXT,
        exit_time INTEGER, exit_price REAL, exit_reason TEXT, pnl_points REAL,
        pnl_value REAL, brokerage REAL, stt REAL, exchange_charges REAL,
        sebi_charges REAL, stamp_duty REAL, gst REAL, total_charges REAL,
        net_pnl REAL)
""")

_sq = types.ModuleType("app.db.sqlite")
_sq.get_conn = lambda: _conn
sys.modules["app.db.sqlite"] = _sq
_al = types.ModuleType("app.event_bus.audit_logger")
_al.write_audit_log = lambda s: None
sys.modules["app.event_bus.audit_logger"] = _al

from app.db import paper_trades_repo as R                     # noqa: E402
from app.backtest.charges.charges_model import calculate_option_charges as BT  # noqa: E402

R._shadow = None
QTY = 650


@pytest.fixture(autouse=True)
def clean():
    _conn.execute("DELETE FROM paper_trades")
    yield


def _open(tid, entry, direction):
    _conn.execute("INSERT INTO paper_trades (paper_trade_id, strategy_name, symbol, entry_price, qty, "
                  "trade_direction, state) VALUES (?,?,?,?,?,?, 'OPEN')",
                  (tid, "TSG_V1", "NIFTYTEST", entry, QTY, direction))


def _close(tid, exit_px, **kw):
    R.close_paper_trade(paper_trade_id=tid, exit_price=exit_px, exit_reason="TEST", **kw)
    return dict(_conn.execute("SELECT * FROM paper_trades WHERE paper_trade_id=?", (tid,)).fetchone())


def test_tc_pchg_0001_short_loss_stt_on_sell():
    _open("a", 118.40, "SHORT")
    r = _close("a", 253.50)
    assert r["stt"] == pytest.approx(0.0015 * 118.40 * QTY, abs=0.01)
    assert r["pnl_value"] == pytest.approx((118.40 - 253.50) * QTY)


def test_tc_pchg_0002_short_profit_stt_on_sell():
    _open("b", 106.00, "SHORT")
    r = _close("b", 57.50)
    assert r["stt"] == pytest.approx(0.0015 * 106.00 * QTY, abs=0.01)


def test_tc_pchg_0003_long_unchanged():
    _open("c", 6.90, "LONG")
    r = _close("c", 12.20)
    assert r["stt"] == pytest.approx(0.0015 * 12.20 * QTY, abs=0.01)
    assert r["pnl_value"] == pytest.approx((12.20 - 6.90) * QTY)


def test_tc_pchg_0004_override_short_and_net():
    _open("d", 100.0, "LONG")
    r = _close("d", 80.0, trade_direction="SHORT")
    assert r["stt"] == pytest.approx(0.0015 * 100.0 * QTY, abs=0.01)
    assert r["net_pnl"] == pytest.approx(r["pnl_value"] - r["total_charges"], abs=0.01)


def test_tc_pchg_0005_matches_backtest_model():
    for i, (e, x) in enumerate([(118.40, 253.50), (102.0, 5.10), (89.25, 89.25), (7.0, 0.4)]):
        _open(f"e{i}", e, "SHORT")
        r = _close(f"e{i}", x)
        bt = BT(entry_price=e, exit_price=x, qty=QTY, direction="SHORT")
        assert r["total_charges"] == pytest.approx(bt.total_charges, abs=0.01), (e, x)
        assert r["stt"] == pytest.approx(bt.stt, abs=0.01), (e, x)


def test_tc_pchg_0006_27oct_tsg_case():
    _open("f", 102.00, "SHORT")
    r = _close("f", 5.10)
    assert r["stt"] == pytest.approx(99.45, abs=0.01)
    assert r["stt"] != pytest.approx(4.97, abs=0.01)
