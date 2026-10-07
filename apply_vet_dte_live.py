#!/usr/bin/env python3
# apply_vet_dte_live.py — fence VET_DTE_LIVE_20261005
#
# Adds the "DTE × lots (0 = skip day)" knob to VET_V1 PAPER / LIVE /
# PAPER+LIVE — the same key and resolver BRK_V1 / ORB_V1 use live
# (app.engine.dte_live.resolve_day_lots, DTE_LOT_MULT_LIVE_20260911).
#
#   * vet_manager.py: day_lots / day_skip resolved once per session by
#     apply_day_lots(); skip → no NEW entries (an open or carried position is
#     still managed to its exit; a FLIP degrades to exit-only), in-app
#     DTE_SKIP notice (info). Scaled → every entry order and DB row at the
#     day's lots (wing matches the main leg). Exits, P&L and charges now use
#     the LEG's own stored qty, so a carried position always closes exactly
#     what it opened, whatever today's lots are.
#   * vet_selection_loop.py: manager.apply_day_lots(today) at arm time.
#   * strategy_loader.py: VET_V1 default "dte_lot_mult": {} (off).
#   * Settings.jsx: field under Lots on the VET_V1 card (DteMultInput).
#   * New test: backend/app/engine/vet/test_vet_live_dte_lots.py, plus the
#     existing VET manager / core / signal-engine / state-route suites.
#
# Live DTE = trading sessions from today to this week's expiry (weekday + NSE
# holiday list) — identical to BRK/ORB. VET live trades only the front weekly
# and never rolls, so this equals the backtest knob's contract DTE for every
# live entry (backtest DTE 5 = rolls / post-roll entries, which live never
# takes). Settings changes apply from the next session (read at arm).
#
# Usage: python3 apply_vet_dte_live.py [--repo PATH] [--allow-dirty]
#                                      [--skip-tests] [--skip-esbuild]
# Run ONLY this script — it carries every payload itself.

from __future__ import annotations

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "VET_DTE_LIVE_20261005"

MGR = "backend/app/engine/vet/vet_manager.py"
LOOP = "backend/app/engine/vet/vet_selection_loop.py"
LOADER = "backend/app/config/strategy_loader.py"
SETTINGS = "frontend/src/pages/Settings.jsx"
TEST = "backend/app/engine/vet/test_vet_live_dte_lots.py"

MIRRORS = {"backend/": "desktop/src-tauri/backend/",
           "frontend/": "desktop/src-tauri/frontend/"}

PREREQS = {
    MGR: ["FLEET_MODES_20260923", "FLEET_EOD_ALERT_FIX_20260908"],
    LOOP: ["FLEET_MODES_20260923"],
    SETTINGS: ["DTE_LOT_MULT_LIVE_20260911", "function DteMultInput(", "const DTE_HELP ="],
    LOADER: ["DTE_LOT_MULT_LIVE_20260911"],
    "backend/app/engine/dte_live.py": ["def resolve_day_lots", "def live_dte"],
    "backend/app/event_bus/inapp_events.py": ["def record_alert("],
}

EXTRA_SUITES = [  # (cwd-relative-to-repo, script) — existing suites re-run after the write
    ("backend/app/engine/vet", "test_vet_manager.py"),
    ("backend/app/engine/vet", "test_vet_live_core.py"),
    ("backend/app/engine/vet", "test_vet_live_signal_engine.py"),
    ("backend", "app/api/test_vet_state_route.py"),
]

EDITS = []

# ── vet_manager.py ───────────────────────────────────────────────────────────
EDITS.append((MGR,
"""# around.
# ============================================================================
""",
"""# around.
#
# ── VET_DTE_LIVE_20261005 ── per-DTE lots / day skip (dte_lot_mult, the
# BRK/ORB live key and resolver). apply_day_lots() runs once per session at
# arm: skip → no NEW entries (open/carried positions are still managed; a
# FLIP degrades to exit-only); scaled → entry orders + rows at the day's
# lots. Exits, P&L and charges use the LEG's stored qty, so a carried
# position always closes exactly what it opened.
# ============================================================================
"""))
EDITS.append((MGR,
"""        self.entries_gate: Optional[Callable[[], bool]] = None
""",
"""        self.entries_gate: Optional[Callable[[], bool]] = None
        # ── VET_DTE_LIVE_20261005 ── set by apply_day_lots(); None/False = base
        self.day_lots: Optional[int] = None
        self.day_skip: bool = False
        self.day_dte: Optional[int] = None
        self.day_dte_tag: str = "base"
"""))
EDITS.append((MGR,
"""    @property
    def qty(self) -> int:
        q = self.cfg.get("quantity") or {}
        return int(q.get("lots", 10)) * int(q.get("lot_size", 65))
""",
"""    @property
    def qty(self) -> int:
        q = self.cfg.get("quantity") or {}
        return int(q.get("lots", 10)) * int(q.get("lot_size", 65))

    # ── VET_DTE_LIVE_20261005 ── sizing for NEW entries / existing legs
    @property
    def entry_qty(self) -> int:
        if self.day_lots:
            q = self.cfg.get("quantity") or {}
            return int(self.day_lots) * int(q.get("lot_size", 65))
        return self.qty

    @property
    def entry_lots(self):
        if self.day_lots:
            return int(self.day_lots)
        return (self.cfg.get("quantity") or {}).get("lots")

    def _leg_qty(self, leg: Optional[Dict]) -> int:
        try:
            q = int((leg or {}).get("qty") or 0)
        except (TypeError, ValueError):
            q = 0
        return q if q > 0 else self.qty

    def apply_day_lots(self, day=None) -> str:
        \"\"\"Resolve today's lots from cfg['dte_lot_mult'] (BRK/ORB resolver).
        Never raises; any failure leaves base lots and entries allowed.\"\"\"
        self.day_lots, self.day_skip = None, False
        self.day_dte, self.day_dte_tag = None, "base"
        try:
            from app.engine.dte_live import resolve_day_lots
            base = int((self.cfg.get("quantity") or {}).get("lots", 10) or 10)
            lots, dte, tag = resolve_day_lots(self.cfg, base, day, "[VET]")
        except Exception as e:
            write_audit_log(f"[VET][DTE] resolve failed ({e!r}) — base lots")
            return "base"
        self.day_dte, self.day_dte_tag = dte, tag
        if tag == "scaled":
            self.day_lots = int(lots)
        elif tag == "skip":
            self.day_skip = True
            msg = (f"VET_V1: today is {dte}DTE — no new entries "
                   f"(DTE × lots = 0). Open positions are still managed.")
            write_audit_log(f"[VET][DTE_SKIP] {msg}")
            try:
                from app.event_bus.inapp_events import record_alert
                record_alert("DTE_SKIP", msg, severity="info",
                             strategy_id="VET_V1", mode=self.mode.lower())
            except Exception:
                pass
        return tag
"""))
# order helpers take an explicit qty (None = the old self.qty)
for old, new in (
    ("    def _buy(self, sym: str, token: int) -> Optional[str]:\n",
     "    def _buy(self, sym: str, token: int, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──\n"),
    ("            return self.executor.place_buy(sym, token, self.qty)\n",
     "            return self.executor.place_buy(sym, token, self.qty if qty is None else int(qty))\n"),
    ("    def _sell_entry(self, sym: str, token: int) -> Optional[str]:\n",
     "    def _sell_entry(self, sym: str, token: int, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──\n"),
    ("            return self.executor.place_sell_entry(sym, token, self.qty)\n",
     "            return self.executor.place_sell_entry(sym, token, self.qty if qty is None else int(qty))\n"),
    ("    def _close_long(self, sym: str) -> Optional[str]:\n",
     "    def _close_long(self, sym: str, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──\n"),
    ("            return self.executor.place_market_sell(sym, self.qty)\n",
     "            return self.executor.place_market_sell(sym, self.qty if qty is None else int(qty))\n"),
    ("    def _close_short(self, sym: str, reason: str) -> Optional[str]:\n",
     "    def _close_short(self, sym: str, reason: str, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──\n"),
    ("            return self.executor.place_buy_exit(sym, self.qty, reason)\n",
     "            return self.executor.place_buy_exit(sym, self.qty if qty is None else int(qty), reason)\n"),
):
    EDITS.append((MGR, old, new))
# entry: skip gate + day sizing
EDITS.append((MGR,
"""            write_audit_log("[VET][MGR] entry suppressed — strategy OFF (no new entries)")
            return None
""",
"""            write_audit_log("[VET][MGR] entry suppressed — strategy OFF (no new entries)")
            return None
        if self.day_skip:   # ── VET_DTE_LIVE_20261005 ──
            write_audit_log(f"[VET][MGR] entry suppressed — {self.day_dte}DTE "
                            f"skip day (dte_lot_mult)")
            return None
        _eq, _el = self.entry_qty, self.entry_lots   # ── VET_DTE_LIVE_20261005 ──
"""))
EDITS.append((MGR,
"""            wing_oid = self._buy(wing["tradingsymbol"], wing.get("token"))
""",
"""            wing_oid = self._buy(wing["tradingsymbol"], wing.get("token"), _eq)
"""))
EDITS.append((MGR,
"""            main_oid = self._sell_entry(main["tradingsymbol"], main.get("token"))
        else:
            main_oid = self._buy(main["tradingsymbol"], main.get("token"))
""",
"""            main_oid = self._sell_entry(main["tradingsymbol"], main.get("token"), _eq)
        else:
            main_oid = self._buy(main["tradingsymbol"], main.get("token"), _eq)
"""))
EDITS.append((MGR,
"""                self._close_long(wing_row["tradingsymbol"])
""",
"""                self._close_long(wing_row["tradingsymbol"], _eq)
"""))
EDITS.append((MGR,
"""                "expiry": wing_row.get("expiry"), "qty": self.qty,
                "lots": (self.cfg.get("quantity") or {}).get("lots"),
""",
"""                "expiry": wing_row.get("expiry"), "qty": _eq,
                "lots": _el,
"""))
EDITS.append((MGR,
"""            "expiry": main.get("expiry"), "qty": self.qty,
            "lots": (self.cfg.get("quantity") or {}).get("lots"),
""",
"""            "expiry": main.get("expiry"), "qty": _eq,
            "lots": _el,
"""))
# exit: the leg's own qty
EDITS.append((MGR,
"""        mpx = self._mark(main["tradingsymbol"], main["entry_price"])
        if self.is_sell:
            moid = self._close_short(main["tradingsymbol"], reason)
            gross = (float(main["entry_price"]) - mpx) * self.qty
        else:
            moid = self._close_long(main["tradingsymbol"])
            gross = (mpx - float(main["entry_price"])) * self.qty
""",
"""        mpx = self._mark(main["tradingsymbol"], main["entry_price"])
        mq = self._leg_qty(main)   # ── VET_DTE_LIVE_20261005 ── close what was opened
        if self.is_sell:
            moid = self._close_short(main["tradingsymbol"], reason, mq)
            gross = (float(main["entry_price"]) - mpx) * mq
        else:
            moid = self._close_long(main["tradingsymbol"], mq)
            gross = (mpx - float(main["entry_price"])) * mq
"""))
EDITS.append((MGR,
"""        m_ch = self._leg_charges(self.is_sell, main["entry_price"], mpx)
""",
"""        m_ch = self._leg_charges(self.is_sell, main["entry_price"], mpx, mq)
"""))
EDITS.append((MGR,
"""            woid = self._close_long(wing["tradingsymbol"])
            wg = (wpx - float(wing["entry_price"])) * self.qty
""",
"""            wq = self._leg_qty(wing)   # ── VET_DTE_LIVE_20261005 ──
            woid = self._close_long(wing["tradingsymbol"], wq)
            wg = (wpx - float(wing["entry_price"])) * wq
"""))
EDITS.append((MGR,
"""            w_ch = self._leg_charges(False, wing["entry_price"], wpx)""",
"""            w_ch = self._leg_charges(False, wing["entry_price"], wpx, wq)"""))
EDITS.append((MGR,
"""    def _leg_charges(self, is_short: bool, entry: float, exit_px: float) -> float:
""",
"""    def _leg_charges(self, is_short: bool, entry: float, exit_px: float,
                     qty: Optional[int] = None) -> float:   # ── VET_DTE_LIVE_20261005 ──
"""))
EDITS.append((MGR,
"""                    qty=int(self.qty))
""",
"""                    qty=int(self.qty if qty is None else qty))
"""))
EDITS.append((MGR,
"""            "day_entries": self._day_entries,
""",
"""            "day_entries": self._day_entries,
            # ── VET_DTE_LIVE_20261005 ──
            "day_lots": self.day_lots, "dte": self.day_dte,
            "dte_tag": self.day_dte_tag, "entry_qty": self.entry_qty,
"""))

# ── vet_selection_loop.py ────────────────────────────────────────────────────
EDITS.append((LOOP,
"""    manager.entries_gate = lambda: _xm.entries_allowed(_xm.strategy_mode(STRATEGY_ID))   # ── FLEET_MODES_20260923 ──
""",
"""    manager.entries_gate = lambda: _xm.entries_allowed(_xm.strategy_mode(STRATEGY_ID))   # ── FLEET_MODES_20260923 ──
    # ── VET_DTE_LIVE_20261005 ── today's lots / skip from dte_lot_mult (read
    # once per session, like the mode); carried positions keep their own qty.
    manager.apply_day_lots(today)
"""))
EDITS.append((LOOP,
"""                    f"eod_square={eod_square} universe={n} expiry={expiry_iso}")
""",
"""                    f"eod_square={eod_square} universe={n} expiry={expiry_iso} "
                    f"dte={manager.day_dte} ({manager.day_dte_tag}, "
                    f"entry qty {manager.entry_qty})")   # ── VET_DTE_LIVE_20261005 ──
"""))

# ── strategy_loader.py ───────────────────────────────────────────────────────
EDITS.append((LOADER,
"""        "quantity": {"lot_size": 65, "lots": 10},
    },
    # ── VET_V1 END ──
""",
"""        "quantity": {"lot_size": 65, "lots": 10},
        # ── VET_DTE_LIVE_20261005 ── per-DTE lots / day skip, off by default.
        # Same key + resolver as BRK/ORB (app.engine.dte_live).
        "dte_lot_mult": {},
    },
    # ── VET_V1 END ──
"""))

# ── Settings.jsx ─────────────────────────────────────────────────────────────
EDITS.append((SETTINGS,
"""  quantity: { lot_size: 65, lots: 10 },
};
// ── VET_V1 END ──
""",
"""  quantity: { lot_size: 65, lots: 10 },
  dte_lot_mult: {},   // ── VET_DTE_LIVE_20261005 ── off
};
// ── VET_V1 END ──
"""))
EDITS.append((SETTINGS,
"""                    onChange={(e) => updateVET(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 90 }} />
                </Field>
""",
"""                    onChange={(e) => updateVET(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 90 }} />
                </Field>
                {/* ── VET_DTE_LIVE_20261005 ── */}
                <Field label="DTE × lots (0 = skip day)" helper={"VET trades the front weekly only and never rolls live, so this is the same DTE the Backtest knob buckets by (backtest DTE 5 = rolls, which live never takes). Skip day = no NEW entries; an open or carried position is still managed to its exit and a FLIP becomes exit-only. Carried positions always close at the size they opened. " + DTE_HELP}>
                  <DteMultInput value={vetConfig.dte_lot_mult} onCommit={(o) => updateVET(["dte_lot_mult"], o)} style={{ maxWidth: 220 }} />
                </Field>
"""))

TEST_PAYLOAD = r'''# backend/app/engine/vet/test_vet_live_dte_lots.py
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
'''


def die(msg: str) -> None:
    print(f"\n✗ {msg}")
    sys.exit(1)


def git_dirty(repo: Path, rel: str) -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=repo,
                             capture_output=True, text=True, timeout=30).stdout
        return bool(out.strip())
    except Exception:
        return False


def find_esbuild(repo: Path):
    for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild"):
        if c.exists():
            return str(c)
    return shutil.which("esbuild")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-esbuild", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    print(f"[{FENCE}] repo: {repo}")

    targets = sorted({f for f, _, _ in EDITS})
    for rel in targets + [TEST]:
        p = repo / rel
        if rel != TEST and not p.exists():
            die(f"missing {rel}")
        if p.exists() and FENCE in p.read_text(encoding="utf-8"):
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
    for rel, marks in PREREQS.items():
        p = repo / rel
        if not p.exists():
            die(f"prerequisite file missing: {rel}")
        txt = p.read_text(encoding="utf-8")
        for mk in marks:
            if mk not in txt:
                die(f"prerequisite '{mk}' not found in {rel}")
    dirty = [rel for rel in targets if git_dirty(repo, rel)]
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) +
            " — commit/stash first or re-run with --allow-dirty")

    staged = {rel: (repo / rel).read_text(encoding="utf-8") for rel in targets}
    original = dict(staged)
    for rel, old, new in EDITS:
        n = staged[rel].count(old)
        if n != 1:
            die(f"anchor matched {n}× (need 1) in {rel}:\n---\n{old[:300]}\n---")
        staged[rel] = staged[rel].replace(old, new, 1)

    with tempfile.TemporaryDirectory() as td:
        for rel in (MGR, LOOP, LOADER):
            tp = Path(td) / Path(rel).name
            tp.write_text(staged[rel], encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for staged {rel}: {e}")
        tt = Path(td) / Path(TEST).name
        tt.write_text(TEST_PAYLOAD, encoding="utf-8")
        try:
            py_compile.compile(str(tt), doraise=True)
        except py_compile.PyCompileError as e:
            die(f"py_compile failed for the test payload: {e}")
        if not a.skip_esbuild:
            esb = find_esbuild(repo)
            if esb:
                tp = Path(td) / "Settings.jsx"
                tp.write_text(staged[SETTINGS], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {SETTINGS}:\n{r.stderr[:2000]}")
                print("  ✓ esbuild parsed staged Settings.jsx")
            else:
                print("  ! esbuild not found — JSX parse gate skipped (npm run build will check)")

    def rollback(reason: str) -> None:
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        (repo / TEST).unlink(missing_ok=True)
        die(reason + " — every file restored to its pre-apply content")

    written = []
    try:
        for rel in targets:
            p = repo / rel
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        for rel in targets:
            (repo / rel).write_text(staged[rel], encoding="utf-8")
            written.append(rel)
        (repo / TEST).write_text(TEST_PAYLOAD, encoding="utf-8")
        written.append(TEST)
    except Exception as e:
        rollback(f"write failed ({e!r})")
    for rel in written:
        print(f"  ✓ wrote {rel}")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        runs = [("backend", "app/engine/vet/test_vet_live_dte_lots.py")] + EXTRA_SUITES
        for cwd, script in runs:
            if not (repo / cwd / script).exists():
                print(f"  ! {script} not found — skipped")
                continue
            r = subprocess.run([sys.executable, script], cwd=repo / cwd,
                               capture_output=True, text=True, env=env)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            bad = r.returncode != 0 or "FAIL" in r.stdout.replace("FAILURES: 0", "")
            if bad:
                print(r.stdout[-4000:])
                print(r.stderr[-3000:])
                rollback(f"suite {script} FAILED")
            print(f"  ✓ {script}: {last}")

    for rel in written:
        for src_pre, dst_pre in MIRRORS.items():
            if rel.startswith(src_pre):
                dst = repo / (dst_pre + rel[len(src_pre):])
                if dst.parent.exists():
                    if dst.exists():
                        shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                    shutil.copy2(repo / rel, dst)
                    print(f"  ✓ mirrored → {dst.relative_to(repo)}")

    print(f"\n✓ {FENCE} applied. Rebuild, then set VET_V1 → DTE × lots in Settings "
          f"(takes effect from the next session the loop arms).")


if __name__ == "__main__":
    main()
