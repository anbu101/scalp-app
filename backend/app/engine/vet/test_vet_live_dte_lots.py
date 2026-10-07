# backend/app/engine/vet/test_vet_live_dte_lots.py
#
# ── VET_DTE_LIVE_20261005 ── per-DTE lots / day skip on the VET_V1 paper/live
# manager. Drives the REAL VetManager (stubbed chain / quotes / executor, real
# VetRepo on a temp DB) and the REAL app.engine.dte_live resolver, with only
# live_dte() pinned so each scenario sees a known DTE.
#
# The decisive check is OFF ≡ ORIGINAL: with the apply script's backup
# (vet_manager.py.bak-VET_DTE_LIVE_20261005) next to the manager, the original
# manager and the patched one (knob unset) must send the SAME orders with the
# SAME quantities and write the SAME rows, P&L and charges.
#
# Run from backend/:  python app/engine/vet/test_vet_live_dte_lots.py

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3]))          # backend/

from app.engine import dte_live as DL                                    # noqa: E402
from app.engine.vet import vet_manager as NEWM                           # noqa: E402
from app.engine.vet.vet_common import VetRepo                            # noqa: E402
from app.engine.vet.vet_live_core import ENTER, FLIP                     # noqa: E402

FENCE = "VET_DTE_LIVE_20261005"
LOT = 65
SPOT = 24000.0
OK = [0]
FAILS = []
TMP = tempfile.mkdtemp(prefix="vet_dte_live_")
_N = [0]


def check(name, cond):
    if cond:
        OK[0] += 1
        print("ok  ", name)
    else:
        FAILS.append(name)
        print("FAIL", name)


def chain(side, ts):
    out = []
    for k in range(23000, 25050, 50):
        px = max(0.05, 180 - abs(k - SPOT) * 0.35)
        out.append({"tradingsymbol": f"NIFTY26OCT{k}{side}", "token": 1000 + k,
                    "strike": float(k), "expiry": "2026-10-06",
                    "instrument_type": side, "ltp": round(px, 2)})
    return out


class QExec:
    """Records (kind, symbol, qty) — quantity is the point of this suite."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def place_buy(self, symbol, token, qty):
        self.calls.append(("BUY", symbol, qty))
        return None if "BUY" in self.fail_on else f"OB-{len(self.calls)}"

    def place_sell_entry(self, symbol, token, qty):
        self.calls.append(("SELL", symbol, qty))
        return None if "SELL" in self.fail_on else f"OS-{len(self.calls)}"

    def place_market_sell(self, symbol, qty):
        self.calls.append(("CLOSE_LONG", symbol, qty))
        return f"OC-{len(self.calls)}"

    def place_buy_exit(self, symbol, qty, reason):
        self.calls.append(("CLOSE_SHORT", symbol, qty))
        return f"OX-{len(self.calls)}"


def newdb():
    _N[0] += 1
    return os.path.join(TMP, f"v{_N[0]}.db")


def mk(mod, extra=None, db=None, execu=None, quote=150.0):
    cfg = {"leg_action": "BUY", "atm_offset": -1, "eod_square": True,
           "quantity": {"lots": 10, "lot_size": LOT}, "_spot": SPOT}
    cfg.update(extra or {})
    repo = VetRepo(db or newdb())
    repo.ensure_schema()
    return mod.VetManager(cfg, repo=repo, chain_fn=chain,
                          quote_fn=lambda sym: quote, executor=execu,
                          mode="LIVE" if execu is not None else "PAPER")


def db_rows(m):
    c = sqlite3.connect(m.repo.db_path)
    c.row_factory = sqlite3.Row
    out = []
    for r in c.execute("SELECT * FROM vet_trades ORDER BY id"):
        d = dict(r)
        for k in ("id", "group_id", "created_at", "updated_at"):
            d.pop(k, None)
        out.append(d)
    c.close()
    return out


SELLW = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 3.0}


def scenario(mod, kind):
    """→ (orders, rows, results) for one deterministic lifecycle."""
    ex = QExec(fail_on={"SELL"} if kind == "fail_main" else None)
    extra = {} if kind in ("buy", "flip") else SELLW
    m = mk(mod, extra, execu=ex, quote=140.0)
    res = []
    if kind == "buy":
        res.append(bool(m.open_position("CE", ts=1000, bar_ts=1000, condition=1)))
        res.append(m.close_position("SIGNAL_EXIT", ts=2000))
    elif kind == "flip":
        m.on_decision({"action": ENTER, "side": "CE", "bar_ts": 10, "condition": 1}, ts=10)
        r = m.on_decision({"action": FLIP, "side": "PE", "reason": "FLIP",
                           "bar_ts": 20, "condition": -1}, ts=20)
        res.append((r["flip"], r["opened"], r["closed"]["gross"], r["closed"]["net"]))
        res.append(m.kill(ts=30))
    elif kind == "sell":
        res.append(bool(m.open_position("PE", ts=1000, bar_ts=1000, condition=1)))
        res.append(m.close_position("EOD", ts=2000))
    elif kind == "fail_main":
        res.append(m.open_position("PE", ts=1000, bar_ts=1000, condition=1))
    for r in res:
        if isinstance(r, dict):
            r.pop("group_id", None)
    return ex.calls, db_rows(m), res


def main():
    # ── 1. OFF ≡ ORIGINAL ────────────────────────────────────────────────
    bak = HERE.parent / f"vet_manager.py.bak-{FENCE}"
    if bak.exists():
        tmp = HERE.parent / "_vet_mgr_orig_for_dte_test.py"
        tmp.write_text(bak.read_text())
        name = "app.engine.vet._vet_mgr_orig_for_dte_test"
        try:
            spec = importlib.util.spec_from_file_location(name, tmp)
            OLDM = importlib.util.module_from_spec(spec)
            sys.modules[name] = OLDM
            spec.loader.exec_module(OLDM)
            for kind in ("buy", "flip", "sell", "fail_main"):
                a, b = scenario(OLDM, kind), scenario(NEWM, kind)
                check(f"OFF ≡ ORIGINAL orders+qty [{kind}]", a[0] == b[0] and len(a[0]) > 0)
                check(f"OFF ≡ ORIGINAL DB rows [{kind}]", a[1] == b[1])
                check(f"OFF ≡ ORIGINAL results/P&L [{kind}]", a[2] == b[2])
        finally:
            sys.modules.pop(name, None)
            tmp.unlink(missing_ok=True)
    else:
        print(f"note: {bak.name} not found — OFF≡ORIGINAL skipped (the apply script runs it)")

    orig_live_dte = DL.live_dte
    try:
        # ── 2. knob unset → resolver says base, nothing changes ──────────
        DL.live_dte = lambda d: 2
        m = mk(NEWM)
        tag = m.apply_day_lots(date(2026, 10, 8))
        check("unset knob → tag base, day_lots None, not skipped",
              tag == "base" and m.day_lots is None and not m.day_skip and m.entry_qty == 10 * LOT)

        # ── 3. scaled day: 2DTE ×2 — every order and row at 20 lots ──────
        ex = QExec()
        m = mk(NEWM, dict(SELLW, dte_lot_mult={"2": 2}), execu=ex, quote=1.0)
        tag = m.apply_day_lots(date(2026, 10, 8))
        check("2:2 on a 2DTE day → scaled, 20 lots", tag == "scaled" and m.day_lots == 20
              and m.entry_qty == 20 * LOT and m.state()["day_lots"] == 20 and m.state()["dte"] == 2)
        p = m.open_position("PE", ts=1000, bar_ts=1000, condition=1)
        check("scaled: wing BUY then short SELL, both 1300 qty",
              p is not None and [(k, q) for k, _, q in ex.calls] == [("BUY", 1300), ("SELL", 1300)])
        rows = db_rows(m)
        check("scaled: both DB legs stamped qty 1300 / lots 20",
              len(rows) == 2 and all(r["qty"] == 1300 and r["lots"] == 20 for r in rows))
        ex.calls.clear()
        out = m.close_position("EOD", ts=2000)
        check("scaled: exit closes SHORT then wing, both 1300 qty",
              [(k, q) for k, _, q in ex.calls] == [("CLOSE_SHORT", 1300), ("CLOSE_LONG", 1300)])
        # same trade at base lots → gross must be exactly half
        exb = QExec()
        mb = mk(NEWM, SELLW, execu=exb, quote=1.0)
        mb.open_position("PE", ts=1000, bar_ts=1000, condition=1)
        ob = mb.close_position("EOD", ts=2000)
        check("scaled: gross P&L exactly 2× the base-lot trade", abs(out["gross"] - 2 * ob["gross"]) < 0.01
              and out["gross"] != 0)

        # ── 4. failed main after the wing filled → wing sold back at 1300 ─
        exf = QExec(fail_on={"SELL"})
        mf = mk(NEWM, dict(SELLW, dte_lot_mult="2:2"), execu=exf)
        mf.apply_day_lots(date(2026, 10, 8))
        check("scaled: failed short unwinds the wing at the SAME scaled qty",
              mf.open_position("PE", ts=1000, bar_ts=1000, condition=1) is None
              and [(k, q) for k, _, q in exf.calls] == [("BUY", 1300), ("SELL", 1300), ("CLOSE_LONG", 1300)])

        # ── 5. skip day: no entries, open positions still managed ────────
        alerts = []
        import app.event_bus.inapp_events as IE
        orig_ra = IE.record_alert
        IE.record_alert = lambda *a, **k: alerts.append((a, k))
        try:
            DL.live_dte = lambda d: 0
            ex = QExec()
            m = mk(NEWM, {"dte_lot_mult": {"0": 0}}, execu=ex)
            m.open_position("CE", ts=10, bar_ts=10, condition=1)      # carried / opened before the roll
            check("pre-skip position opened at base qty", m.pos is not None and ex.calls[-1][2] == 650)
            tag = m.apply_day_lots(date(2026, 10, 6))
        finally:
            IE.record_alert = orig_ra
        check("0:0 on 0DTE → skip, one in-app DTE_SKIP notice",
              tag == "skip" and m.day_skip and len(alerts) == 1 and alerts[0][0][0] == "DTE_SKIP"
              and alerts[0][1].get("strategy_id") == "VET_V1")
        ex.calls.clear()
        r = m.on_decision({"action": FLIP, "side": "PE", "reason": "FLIP",
                           "bar_ts": 20, "condition": -1}, ts=20)
        check("skip: a FLIP still CLOSES the open position but does not reopen",
              r["closed"] is not None and not r["opened"] and m.pos is None
              and [(k, q) for k, _, q in ex.calls] == [("CLOSE_LONG", 650)])
        ex.calls.clear()
        check("skip: a fresh ENTER is refused and sends no order",
              m.on_decision({"action": ENTER, "side": "CE", "bar_ts": 30, "condition": 1}, ts=30) is None
              and m.pos is None and ex.calls == [])

        # ── 6. carried position keeps ITS qty across a config/day change ──
        DL.live_dte = lambda d: 3
        db = newdb()
        m1 = mk(NEWM, {"eod_square": False, "dte_lot_mult": {"3": 2}}, db=db)
        m1.apply_day_lots(date(2026, 10, 7))
        m1.open_position("CE", ts=10, bar_ts=10, condition=1)
        ex = QExec()
        m2 = mk(NEWM, {"eod_square": False}, db=db, execu=ex)     # next day: knob cleared, 10 lots
        m2.mode = "PAPER"
        m2.resume_from_db()
        m2.mode = "LIVE"
        m2.apply_day_lots(date(2026, 10, 8))
        out = m2.expiry_exit(ts=99)
        check("resumed 20-lot carry closes 1300 qty even though today's lots are 10",
              len(ex.calls) == 1 and ex.calls[0][0] == "CLOSE_LONG" and ex.calls[0][2] == 1300
              and m2.entry_qty == 650)
        row = [r for r in db_rows(m2) if r["leg_role"] == "MAIN"][0]
        check("...and books its P&L on 1300 qty",
              out is not None and abs(row["pnl"] - (150.0 - row["entry_price"]) * 1300) < 0.02)

        # ── 7. calendar unavailable → fail-open at base lots ─────────────
        DL.live_dte = lambda d: None
        m = mk(NEWM, {"dte_lot_mult": {"0": 0, "2": 3}})
        tag = m.apply_day_lots(date(2026, 10, 8))
        check("unknown DTE → base lots, not skipped",
              tag == "unknown" and m.day_lots is None and not m.day_skip and m.entry_qty == 650)

        # ── 8. resolver failure never blocks the manager ─────────────────
        def _boom(d):
            raise RuntimeError("calendar down")
        DL.live_dte = _boom
        m = mk(NEWM, {"dte_lot_mult": {"0": 0}})
        check("resolver exception → base lots, entries still possible",
              m.apply_day_lots(date(2026, 10, 6)) in ("unknown", "base") and not m.day_skip
              and m.open_position("CE", ts=1, bar_ts=1, condition=1) is not None)
    finally:
        DL.live_dte = orig_live_dte

    # ── 9. real calendar: expiry day itself is 0DTE ──────────────────────
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    exp = expected_expiry_for_day(date(2026, 10, 7))
    check(f"real live_dte({exp}) on its own expiry day = 0", DL.live_dte(exp) == 0)
    check("real live_dte the day before expiry ≥ 1",
          (DL.live_dte(exp - timedelta(days=1)) or 0) >= 1 or (exp - timedelta(days=1)).weekday() >= 5)

    # ── 10. the loop wires it at arm time ────────────────────────────────
    src = (HERE.parent / "vet_selection_loop.py").read_text()
    i_mgr = src.find("manager = VetManager(")
    i_dte = src.find("manager.apply_day_lots(today)")
    i_tick = src.find("tick = TMA2TickEngine(")
    check("selection loop applies the day's lots after the manager exists and before ticks start",
          0 < i_mgr < i_dte < i_tick)


if __name__ == "__main__":
    main()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} VET_V1 LIVE DTE-LOTS CHECKS PASSED")
