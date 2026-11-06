# backend/app/engine/ic/test_ic2_shadow_carry.py
#
# IC2_SHADOW_CARRY_20261106 — the boot orphan sweep must never neutral-close a
# PAPER_LIVE twin ('SHD:…') whose live row is owned/open. Includes the
# 2026-11-06 repro end-to-end through the REAL trading/shadow_book.py.
# Run: python3 test_ic2_shadow_carry.py   (or pytest)
import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent                      # backend/app


def _mk(n):
    m = types.ModuleType(n); sys.modules[n] = m; return m


for n in ["app", "app.db", "app.event_bus", "app.api", "app.engine",
          "app.engine.ic", "app.trading", "app.risk"]:
    if n not in sys.modules:
        _mk(n)

AUDIT = []
_mk("app.event_bus.audit_logger").write_audit_log = lambda s: AUDIT.append(s)
ALERTS = []
_mk("app.event_bus.inapp_events").record_alert = \
    lambda code, message, **k: ALERTS.append((code, message))
_mk("app.api.telegram_api").notify_critical = lambda d: None

_conn = sqlite3.connect(":memory:", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_mk("app.db.sqlite").get_conn = lambda: _conn

CLOSED = []


def close_paper_trade(*, paper_trade_id, exit_price, exit_reason,
                      trade_direction=None):
    row = _conn.execute("SELECT entry_price, qty, trade_direction FROM "
                        "paper_trades WHERE paper_trade_id=? AND state='OPEN'",
                        (paper_trade_id,)).fetchone()
    if not row:
        AUDIT.append(f"[DB][PAPER][SKIP] CLOSE IGNORED trade_id={paper_trade_id}")
        return
    e, q, d = row
    g = (e - exit_price) * q if (trade_direction or d) == "SHORT" else (exit_price - e) * q
    CLOSED.append((paper_trade_id, exit_price, exit_reason))
    _conn.execute("UPDATE paper_trades SET state='CLOSED', exit_price=?, "
                  "exit_reason=?, pnl_value=? WHERE paper_trade_id=?",
                  (exit_price, exit_reason, g, paper_trade_id))
    _conn.commit()


_mk("app.db.paper_trades_repo").close_paper_trade = close_paper_trade
_mk("app.db.trades_repo").get_open_trades_for_strategy = lambda sid: [
    dict(r) for r in _conn.execute(
        "SELECT trade_id, symbol FROM trades WHERE strategy_id=? "
        "AND exit_time IS NULL", (sid,)).fetchall()]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


OR = _load("app.engine.ic.ic_orphan_reconcile", HERE / "ic_orphan_reconcile.py")
SB = _load("app.trading.shadow_book", APP / "trading" / "shadow_book.py")


class _Leg:
    def __init__(self, lid): self.leg_id = lid


class _Core:
    def __init__(self, lids): self.legs = {l: _Leg(l) for l in lids}


class FakeGM:
    strategy_id = "IC_V2"

    def __init__(self, owned):           # {leg_id: live trade_id}
        self._core = _Core(list(owned)); self._owned = owned

    def current_group(self): return self._core
    def leg_runtime(self, lid): return {"db_id": self._owned.get(lid)}


def _reset(with_links=True, with_trades=True):
    for t in ("paper_trades", "trades", "shadow_links"):
        _conn.execute(f"DROP TABLE IF EXISTS {t}")
    _conn.execute("""CREATE TABLE paper_trades (paper_trade_id TEXT PRIMARY KEY,
        strategy_name TEXT, symbol TEXT, entry_price REAL, qty INTEGER,
        trade_direction TEXT, state TEXT, exit_price REAL, exit_reason TEXT,
        pnl_value REAL)""")
    if with_trades:
        _conn.execute("""CREATE TABLE trades (trade_id TEXT PRIMARY KEY,
            strategy_id TEXT, symbol TEXT, exit_time INTEGER)""")
    if with_links:
        _conn.execute("""CREATE TABLE shadow_links (live_ref TEXT PRIMARY KEY,
            shadow_id TEXT NOT NULL, strategy_id TEXT NOT NULL, symbol TEXT,
            created_at INTEGER)""")
    _conn.commit()
    SB._schema_ok = False
    CLOSED.clear(); AUDIT.clear(); ALERTS.clear()


def _live(tid, sym, open_=True):
    _conn.execute("INSERT INTO trades VALUES (?,?,?,?)",
                  (tid, "IC_V2", sym, None if open_ else 1793900000))


def _twin(sid, sym, entry, qty=195, d="LONG", live_ref=None):
    _conn.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?, 'OPEN', "
                  "NULL, NULL, NULL)", (sid, "IC_V2", sym, entry, qty, d))
    if live_ref:
        _conn.execute("INSERT INTO shadow_links VALUES (?,?,?,?,0)",
                      (live_ref, sid, "IC_V2", sym))
    _conn.commit()


# The 2026-11-06 carry: (leg, symbol, dir, entry, live exit at 09:16)
INCIDENT = [
    ("L2",  "NIFTY26N0924200PE", "SHORT", 96.55, 112.70),
    ("L3",  "NIFTY26N0925700CE", "LONG",  3.55,  2.95),
    ("L4",  "NIFTY26N0922900PE", "LONG",  5.10,  3.25),
    ("L1A", "NIFTY26N0924600CE", "LONG",  85.00, 77.40),
]


def _seed_incident():
    owned = {}
    for lid, sym, d, e, _x in INCIDENT:
        tid = f"live-{lid}"
        _live(tid, sym)
        _twin(f"SHD:{lid}", sym, e, d=d, live_ref=tid)
        owned[lid] = tid
    return FakeGM(owned)


# ── S1: the 2026-11-06 repro — no twin closed at boot ──────────────────────
def test_s1_incident_twins_kept_at_boot():
    _reset(); gm = _seed_incident()
    res = OR.reconcile_orphan_rows(gm)
    assert res == {"paper_closed": 0, "live_flagged": 0}, res
    assert CLOSED == []
    assert sum("TWIN_KEPT" in a for a in AUDIT) == 4
    assert not ALERTS


# ── S2: end-to-end — 09:16 NEXT_OPEN mirror_close books the real P&L ───────
def test_s2_incident_next_open_books_real_pnl():
    _reset(); gm = _seed_incident()
    OR.reconcile_orphan_rows(gm)
    total = 0.0
    for lid, sym, d, e, x in INCIDENT:
        assert SB.mirror_close(live_ref=f"live-{lid}", exit_price=x,
                               exit_reason="NEXT_OPEN") is True
        r = _conn.execute("SELECT state, exit_price, exit_reason, pnl_value "
                          "FROM paper_trades WHERE paper_trade_id=?",
                          (f"SHD:{lid}",)).fetchone()
        assert r["state"] == "CLOSED" and r["exit_price"] == x
        assert r["exit_reason"] == "NEXT_OPEN"
        total += r["pnl_value"]
    assert round(total, 2) == -5109.00, total
    assert not any("SKIP" in a for a in AUDIT)


# ── S3: twin of an OPEN live row outside the group → kept ──────────────────
def test_s3_twin_of_open_live_row_not_in_group_kept():
    _reset()
    _live("stray", "NIFTY26N0924000CE")
    _twin("SHD:s", "NIFTY26N0924000CE", 50.0, live_ref="stray")
    res = OR.reconcile_orphan_rows(FakeGM({}))
    assert CLOSED == [] and res["live_flagged"] == 1


# ── S4: twin whose live row is CLOSED (stale link) → genuine orphan ────────
def test_s4_twin_of_closed_live_row_is_orphan():
    _reset()
    _live("gone", "NIFTY26N0924000CE", open_=False)
    _twin("SHD:g", "NIFTY26N0924000CE", 50.0, live_ref="gone")
    res = OR.reconcile_orphan_rows(FakeGM({}))
    assert res["paper_closed"] == 1 and CLOSED == [("SHD:g", 50.0, "MANUAL")]


# ── S5: unlinked twin — kept only if an open live row holds the contract ───
def test_s5_unlinked_twin():
    _reset()
    _live("t1", "NIFTY26N0924000CE")
    _twin("SHD:u1", "NIFTY26N0924000CE", 50.0)          # contract still live
    _twin("SHD:u2", "NIFTY26N0923000PE", 9.0)           # nothing behind it
    OR.reconcile_orphan_rows(FakeGM({}))
    assert [c[0] for c in CLOSED] == ["SHD:u2"]


# ── S6: lookup error → fail-safe keep (no trades table) ────────────────────
def test_s6_lookup_error_keeps_twin():
    _reset(with_trades=False)
    _twin("SHD:e", "NIFTY26N0924000CE", 50.0, live_ref="x")
    OR.reconcile_orphan_rows(FakeGM({}))
    assert CLOSED == []
    assert any("TWIN_CHECK_ERR" in a for a in AUDIT)


# ── S7: ordinary (non-twin) orphans unchanged — still neutral-closed ───────
def test_s7_plain_orphan_unchanged():
    _reset(with_links=False)
    _twin("plain-orphan", "NIFTY26N0924000CE", 77.10)
    _twin("own1", "NIFTY26N0924100CE", 60.0)
    _live("x", "NIFTY26N0999999CE", open_=False)
    gm = FakeGM({"L1": "own1"})
    res = OR.reconcile_orphan_rows(gm)
    assert CLOSED == [("plain-orphan", 77.10, "MANUAL")]
    assert res["paper_closed"] == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f(); print(f"  ✓ {f.__name__}")
    print(f"ALL {len(fns)} PASSED")
