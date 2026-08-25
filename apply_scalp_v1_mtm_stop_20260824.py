#!/usr/bin/env python3
# apply_scalp_v1_mtm_stop_20260824.py
#
# DAILY MAX MTM LOSS — fence: SCALP_V1_MTM_STOP_20260824  (backtest-only)
#
# Config (SCALP_V1): "daily_max_mtm_loss": 0     (rupees; 0 = off)
#
# Semantics (checked at every candle close on the open position, AFTER that
# candle's SL/TP resolution — intra-candle SL/TP fires first at its level,
# matching live tick ordering):
#   day MTM = realized GROSS of trades closed today
#           + (entry − close) × qty of the open position
#   breach (day MTM ≤ −limit) → open position force-closed at candle close,
#   exit reason "MTM", and ALL further entries halted for the day. A breach
#   on realized alone (no open position) also halts entries. Gross, not net:
#   comparable to the broker's live MTM figure, which excludes charges.
#
# FILES: backtest_runner.py + strategy_loader.py (+ desktop tree if present),
# Backtest.jsx (field/chip), Queue token, Comparison row, Sweep axis.
# PREREQ fences through SCALP_V1_PARALLEL_20260823 in the runner and
# SCALP_V1_FRESH_ENTRY_20260824 in the UI. Idempotent. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_MTM_STOP_20260824"
ROOT = Path(__file__).resolve().parent
RN_REL = "app/backtest/runner/backtest_runner.py"
LD_REL = "app/config/strategy_loader.py"
SRC = ROOT / "frontend" / "src"
BT_JSX = SRC / "pages" / "Backtest.jsx"
QU_JSX = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC_JSX = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW_JSX = SRC / "pages" / "backtest" / "SweepBuilder.jsx"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / RN_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, old, new, label):
    n = t.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return t.replace(old, new, 1)


# ── R1: config parse (after the sizing/spread parse block) ─────────────────
R1_OLD = "    # ── SCALP_V1_ENTRY_SIZING_20260823 END: config ──"
R1_NEW = '''    # ── SCALP_V1_ENTRY_SIZING_20260823 END: config ──

    # ── SCALP_V1_MTM_STOP_20260824: daily max MTM loss (rupees; 0 = off) ──
    try:
        mtm_limit = float(cfg.get("daily_max_mtm_loss", 0) or 0)
        if mtm_limit < 0:
            mtm_limit = abs(mtm_limit)   # tolerate "-50000" style input
    except (TypeError, ValueError):
        mtm_limit = 0.0'''

# ── R2: per-day state ──────────────────────────────────────────────────────
R2_OLD = """        # ── SCALP_V1_BT_FILTERS_20260823: per-day state (D2, D4) ──
        day_entries = 0"""
R2_NEW = """        # ── SCALP_V1_BT_FILTERS_20260823: per-day state (D2, D4) ──
        day_entries = 0
        day_realized = 0.0        # ── SCALP_V1_MTM_STOP_20260824 ── gross of today's closed trades
        day_mtm_halted = False    #    breach latch: no further entries today"""

# ── R3: capture realized gross at the three in-loop close sites, and run
#        the MTM check after SL/TP resolution ──────────────────────────────
R3_OLD = """                    if ts >= eod_close_ts:
                        # Candle STARTS at/after 15:15 — live already squared
                        # off at 15:15:00. This branch only fires when the
                        # 15:14 candle was missing from the corpus; fill at
                        # this candle's OPEN as the closest proxy for the
                        # 15:15:00 market price. No SL/TP resolution: that
                        # price action post-dates the live square-off.
                        book.close_position(sym, exit_ts=eod_close_ts,
                                            exit_price=c.open,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        continue"""
R3_NEW = """                    if ts >= eod_close_ts:
                        # Candle STARTS at/after 15:15 — live already squared
                        # off at 15:15:00. This branch only fires when the
                        # 15:14 candle was missing from the corpus; fill at
                        # this candle's OPEN as the closest proxy for the
                        # 15:15:00 market price. No SL/TP resolution: that
                        # price action post-dates the live square-off.
                        _ct = book.close_position(sym, exit_ts=eod_close_ts,
                                            exit_price=c.open,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──
                        continue"""

R4_OLD = """                    if fr.exited:
                        # stamp exit at candle CLOSE (ts+60) to match live labelling
                        book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=fr.exit_price,
                                            exit_reason=fr.exit_reason,
                                            ambiguous_fill=fr.ambiguous)"""
R4_NEW = """                    if fr.exited:
                        # stamp exit at candle CLOSE (ts+60) to match live labelling
                        _ct = book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=fr.exit_price,
                                            exit_reason=fr.exit_reason,
                                            ambiguous_fill=fr.ambiguous)
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──"""

R5_OLD = """                    elif ts + 60 >= eod_close_ts:
                        book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)"""
R5_NEW = '''                    elif ts + 60 >= eod_close_ts:
                        _ct = book.close_position(sym, exit_ts=ts + 60,
                                            exit_price=c.close,
                                            exit_reason="EOD",
                                            ambiguous_fill=False)
                        day_realized += _ct.pnl   # ── SCALP_V1_MTM_STOP_20260824 ──
                    # ── SCALP_V1_MTM_STOP_20260824: MTM check AFTER this
                    # candle's SL/TP resolution (intra-candle exits fire first
                    # at their levels, like live ticks). Breach → force-close
                    # at candle close, reason MTM, and halt the day's entries.
                    if mtm_limit > 0 and not day_mtm_halted:
                        _op = book.get_open_for_symbol(sym)
                        if _op is not None:
                            _unreal = (_op.entry_price - c.close) * _op.qty
                            if day_realized + _unreal <= -mtm_limit:
                                _ct = book.close_position(sym, exit_ts=ts + 60,
                                                    exit_price=c.close,
                                                    exit_reason="MTM",
                                                    ambiguous_fill=False)
                                day_realized += _ct.pnl
                                day_mtm_halted = True
                        elif day_realized <= -mtm_limit:
                            day_mtm_halted = True'''

# ── R6: election guard — halted day takes no entries ───────────────────────
R6_OLD = "            _cap_blocked = (max_trades_day is None or"
R6_NEW = """            # ── SCALP_V1_MTM_STOP_20260824 ── halted day: no entries
            _mtm_blocked = mtm_limit > 0 and (day_mtm_halted or
                                              day_realized <= -mtm_limit)
            _cap_blocked = _mtm_blocked or (max_trades_day is None or"""

# ── loader ─────────────────────────────────────────────────────────────────
L1_OLD = '''        "require_fresh_entry": False,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''
L1_NEW = '''        "require_fresh_entry": False,
        # ── SCALP_V1_MTM_STOP_20260824 ── daily MTM loss stop, rupees
        # (realized gross + open unrealized). 0 = off. Breach closes the
        # open position (reason MTM) and halts entries for the day.
        "daily_max_mtm_loss": 0,
        # ── SCALP_V1_BT_FILTERS_20260823 END ──'''

# ── UI ─────────────────────────────────────────────────────────────────────
J1_OLD = "  const [v1FreshEntry, setV1FreshEntry] = useState(saved.v1FreshEntry ?? false);"
J1_NEW = """  const [v1FreshEntry, setV1FreshEntry] = useState(saved.v1FreshEntry ?? false);
  // ── SCALP_V1_MTM_STOP_20260824 ──
  const [v1MtmLoss, setV1MtmLoss] = useState(saved.v1MtmLoss ?? 0);"""

J2_OLD = "      v1FreshEntry });   // ── SCALP_V1_FRESH_ENTRY_20260824 ──"
J2_NEW = "      v1FreshEntry,   // ── SCALP_V1_FRESH_ENTRY_20260824 ──\n      v1MtmLoss });   // ── SCALP_V1_MTM_STOP_20260824 ──"

J3_OLD = "      v1FreshEntry]);   // ── SCALP_V1_FRESH_ENTRY_20260824 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"
J3_NEW = "      v1FreshEntry,   // ── SCALP_V1_FRESH_ENTRY_20260824 ──\n      v1MtmLoss]);   // ── SCALP_V1_MTM_STOP_20260824 ── stale-closure rule: saveParams reads it, so it lands here in the SAME commit"

J4_OLD = """      if (v1FreshEntry) cfg.require_fresh_entry = true;   // ── SCALP_V1_FRESH_ENTRY_20260824 ── omit-when-off
    }"""
J4_NEW = """      if (v1FreshEntry) cfg.require_fresh_entry = true;   // ── SCALP_V1_FRESH_ENTRY_20260824 ── omit-when-off
      if (Number(v1MtmLoss) > 0) cfg.daily_max_mtm_loss = Number(v1MtmLoss);   // ── SCALP_V1_MTM_STOP_20260824 ──
    }"""

J5_OLD = "      v1FreshEntry]);   // ── SCALP_V1_FRESH_ENTRY_20260824 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"
J5_NEW = "      v1FreshEntry,   // ── SCALP_V1_FRESH_ENTRY_20260824 ──\n      v1MtmLoss]);   // ── SCALP_V1_MTM_STOP_20260824 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"

J6_OLD = """              <Field label="Fresh entry">
                <select style={inputStyle} value={v1FreshEntry ? "1" : "0"} onChange={(e) => setV1FreshEntry(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>"""
J6_NEW = """              <Field label="Fresh entry">
                <select style={inputStyle} value={v1FreshEntry ? "1" : "0"} onChange={(e) => setV1FreshEntry(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>
              {/* ── SCALP_V1_MTM_STOP_20260824 ── ₹; 0 = off */}
              <Field label="Daily MTM stop ₹"><input type="number" min="0" step="5000" style={inputStyle} value={v1MtmLoss} onChange={(e) => setV1MtmLoss(e.target.value)} /></Field>"""

J7_OLD = '  if (cfg.require_fresh_entry) add("Fresh entry", "on");   // ── SCALP_V1_FRESH_ENTRY_20260824 ── RUN_PARAMS_DISPLAY tripwire'
J7_NEW = '''  if (cfg.require_fresh_entry) add("Fresh entry", "on");   // ── SCALP_V1_FRESH_ENTRY_20260824 ── RUN_PARAMS_DISPLAY tripwire
  if (Number(cfg.daily_max_mtm_loss) > 0) add("MTM stop", `₹${cfg.daily_max_mtm_loss}/day`);   // ── SCALP_V1_MTM_STOP_20260824 ──'''

Q1_OLD = '  if (cfg.require_fresh_entry) p.push("fresh");   // ── SCALP_V1_FRESH_ENTRY_20260824 ──'
Q1_NEW = '''  if (cfg.require_fresh_entry) p.push("fresh");   // ── SCALP_V1_FRESH_ENTRY_20260824 ──
  if (Number(cfg.daily_max_mtm_loss) > 0) p.push(`mtm${cfg.daily_max_mtm_loss/1000}k`);   // ── SCALP_V1_MTM_STOP_20260824 ──'''

C1_OLD = '''  { key: "fresh_entry",      label: "Fresh entry",    get: (r) => (r.config?.require_fresh_entry ? "on" : null) },   // ── SCALP_V1_FRESH_ENTRY_20260824 ──'''
C1_NEW = '''  { key: "fresh_entry",      label: "Fresh entry",    get: (r) => (r.config?.require_fresh_entry ? "on" : null) },   // ── SCALP_V1_FRESH_ENTRY_20260824 ──
  { key: "mtm_stop",         label: "Daily MTM stop", get: (r) => (Number(r.config?.daily_max_mtm_loss) > 0 ? `₹${r.config.daily_max_mtm_loss}` : null) },   // ── SCALP_V1_MTM_STOP_20260824 ──'''

W1_OLD = """  { key: "v1_fresh", label: "V1 fresh entry (0/1)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.require_fresh_entry = true; }, fmt: (v) => (v ? "fresh" : "stale-ok") },"""
W1_NEW = """  { key: "v1_fresh", label: "V1 fresh entry (0/1)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.require_fresh_entry = true; }, fmt: (v) => (v ? "fresh" : "stale-ok") },
  // ── SCALP_V1_MTM_STOP_20260824 ── rupees; 0 = off.
  { key: "v1_mtm_stop", label: "V1 daily MTM stop ₹ (0=off)", strategies: [V1],
    hint: "0, 50000, 75000, 100000, 125000", parse: _num,
    apply: (c, v) => { if (v > 0) c.daily_max_mtm_loss = v; }, fmt: (v) => (v > 0 ? `mtm${v/1000}k` : "no mtm") },"""


def main():
    if not (ROOT / "backend" / RN_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        rn_p, ld_p = tree / RN_REL, tree / LD_REL
        rn, ld = rn_p.read_text(), ld_p.read_text()
        if FENCE in rn or FENCE in ld:
            _die(f"fence {FENCE} already present under {tree}")
        if "SCALP_V1_PARALLEL_20260823" not in rn:
            _die(f"prerequisite fences missing in {rn_p}")
        for lab, o, n in [("R1", R1_OLD, R1_NEW), ("R2", R2_OLD, R2_NEW),
                          ("R3", R3_OLD, R3_NEW), ("R4", R4_OLD, R4_NEW),
                          ("R5", R5_OLD, R5_NEW), ("R6", R6_OLD, R6_NEW)]:
            rn = _ro(rn, o, n, f"{tree.name}:{lab}")
        ld = _ro(ld, L1_OLD, L1_NEW, f"{tree.name}:L1")
        staged.append((rn_p, rn))
        staged.append((ld_p, ld))
    for path, edits in [
        (BT_JSX, [("J1", J1_OLD, J1_NEW), ("J2", J2_OLD, J2_NEW), ("J3", J3_OLD, J3_NEW),
                  ("J4", J4_OLD, J4_NEW), ("J5", J5_OLD, J5_NEW), ("J6", J6_OLD, J6_NEW),
                  ("J7", J7_OLD, J7_NEW)]),
        (QU_JSX, [("Q1", Q1_OLD, Q1_NEW)]),
        (RC_JSX, [("C1", C1_OLD, C1_NEW)]),
        (SW_JSX, [("W1", W1_OLD, W1_NEW)]),
    ]:
        t = path.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {path.name}")
        for lab, o, n in edits:
            t = _ro(t, o, n, f"{path.name}:{lab}")
        staged.append((path, t))
    for p, t in staged:
        if p.suffix == ".py":
            try:
                compile(t, str(p), "exec")
            except SyntaxError as e:
                _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied. 0 = off; existing runs unchanged.")
    print("New exit reason 'MTM' will appear in exports; entries halt for the")
    print("rest of a breached day. Sweep axis: 0/50k/75k/100k/125k.")


if __name__ == "__main__":
    main()
