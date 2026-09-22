#!/usr/bin/env python3
# revert_ha_cond1_flip.py — remove the HA_V1 "C1 Flip Side" experiment.
#
# Fence removed : HA_COND1_FLIP (delivered 2026-08-13; backtest-falsified —
#                 C1-flip edge was confined to 2020-21/2024 regimes).
# Revert marker : HA_COND1_FLIP_REVERT_20260922  (used only for the .bak names
#                 and the done-file; NO marker is left inside any source file —
#                 the files read as if the fence had never landed).
#
# WHAT IS REMOVED (7 files, exact-inverse of the 2026-08-13 delivery):
#   backend/app/engine/ha_options/ha_tick_engine.py
#       the COND1 → opposite-contract transform in _on_candle_close; the
#       arbitration offer goes back to the SIGNAL contract's symbol/side/ltp/sl.
#       A leftover "cond1_flip_side": true in a saved config becomes inert.
#   backend/app/backtest/ha/backtest_ha_runner.py
#       docstring line, c1flip resolve, 4 flip diag counters, bucket_bars
#       lookup, the candidate-replacement block, the diag print segment.
#   frontend/src/pages/Settings.jsx     DEFAULT_HA_CONFIG key + "C1 Flip Side" toggle
#   frontend/src/pages/Backtest.jsx     chip, state hook, 3 dep-array entries,
#                                       buildConfig emission, form <select>
#   frontend/src/pages/backtest/RunComparison.jsx   diff-matrix row
#   frontend/src/pages/backtest/SweepBuilder.jsx    c1r_flip axis
#   frontend/src/pages/backtest/BacktestQueue.jsx   queue label
#   ~/.scalp-app/strategies/HA_V1.json  the saved "cond1_flip_side" key is
#       dropped (backup written first). Harmless either way after the code
#       revert; removed so the config reads clean.
#
# NOT touched: HA_COND_FILTER (C1/C2/C3 chips), MIN_SL_GATE, HA_COND1_RETRACE,
# HA_COND_WINDOWS, HA_DAILY_CAP, ARB_WINDOW, GLOBAL_ARB_GATE, the desktop
# mirror trees other than a best-effort same-edit copy (they are rsync'd from
# the source trees by build-scalp.sh anyway).
#
# GATES (all before any write): every anchor x1 in every file; py_compile on
# both .py results; pyflakes undefined-name scan (if pyflakes is installed);
# esbuild parse of all 5 JSX results (if esbuild/npx is available); a
# behavioural simulation of the reverted _on_candle_close (5 cases) run in a
# subprocess against the staged engine. Writes are all-or-nothing with
# .bak-HA_COND1_FLIP_REVERT_20260922 backups; a failed post-write verification
# restores every file from its backup.
#
# USAGE (repo root, non-trading hours):
#   python3 revert_ha_cond1_flip.py --check      # dry run
#   python3 revert_ha_cond1_flip.py              # apply
#   python3 revert_ha_cond1_flip.py --allow-dirty   # if git shows M on a target
# Then: ./desktop/build-scalp.sh both   (backend + frontend both change)

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tempfile, json, py_compile

REVERT = "HA_COND1_FLIP_REVERT_20260922"
ROOT = os.path.dirname(os.path.abspath(__file__))
MIRROR_BACKEND = "desktop/src-tauri/backend"     # gitignored copy, patched if present

EDITS = [
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'tick: flip block + offer',
     '        # ── HA_COND1_FLIP BEGIN ── COND1-only opposite-side entry (PAPER +\n        # LIVE). cond1_flip_side: bool, default OFF → this whole block is a\n        # no-op and the entry_* vars below equal the signal values, so COND2 /\n        # COND3 and flip-off COND1 behave byte-identically to before.\n        #\n        # WHAT: a COND1 signal on the selected CE enters the selected PE (and\n        # vice versa) — validated in backtest (C1 standalone -4.14L → flipped\n        # +1.50L; C1flip+C3 net/DD 2.14 vs C2+C3 1.39). The signal contract\'s\n        # risk transfers IN POINTS (its red-low is meaningless on the flipped\n        # contract): flip_sl = flip_ltp − (ltp − red_low). trade_manager.enter\n        # computes TP from the entry_ltp it receives, so the flipped TP is\n        # correct with zero trade-manager changes.\n        #\n        # FAIL-CLOSED: every unmet precondition SKIPS the entry entirely —\n        # NEVER falls back to the signal side (that side is the direction the\n        # data says loses). Skip cases: opposite side not selected, opposite\n        # LTP unavailable, side_mode blocks the flipped side, per-side cap\n        # blocks the flipped side, or the transferred risk >= flipped LTP\n        # (synthetic SL would be <= 0).\n        #\n        # The DB candle annotation below stays on the SIGNAL symbol/side — it\n        # records that the evaluator fired, which is true regardless of which\n        # contract trades. Arbitration election re-checks can_enter on the\n        # entry side (the flipped side) exactly as before.\n        _entry_symbol = symbol\n        _entry_side = side\n        _entry_ltp = float(ltp)\n        _entry_sl = float(signal.sl_price)\n        if bool(cfg.get("cond1_flip_side")) and signal.condition == "COND1":\n            _opp_side = "PE" if side == "CE" else "CE"\n            _fm = cfg.get("trade_side_mode", "BOTH")\n            if _fm != "BOTH" and _fm != _opp_side:\n                write_audit_log(\n                    f"[HA][FLIP_SKIP] {symbol} — side_mode={_fm} blocks "\n                    f"flipped side {_opp_side}; no entry (fail-closed)"\n                )\n                return\n            _fok, _freason = self._signal_engine.can_enter(_opp_side)\n            if not _fok:\n                write_audit_log(\n                    f"[HA][FLIP_SKIP] {symbol} — flipped side {_opp_side}: "\n                    f"{_freason}; no entry (fail-closed)"\n                )\n                return\n            with self._selection_lock:\n                _opp_symbol = (self._selected_pe if side == "CE"\n                               else self._selected_ce)\n            if not _opp_symbol:\n                write_audit_log(\n                    f"[HA][FLIP_SKIP] {symbol} — no selected {_opp_side} to "\n                    f"flip into; no entry (fail-closed)"\n                )\n                return\n            _opp_ltp = LTPStore.get(_opp_symbol)\n            if not _opp_ltp or _opp_ltp <= 0:\n                write_audit_log(\n                    f"[HA][FLIP_SKIP] {symbol} — LTP unavailable for flipped "\n                    f"{_opp_symbol}; no entry (fail-closed)"\n                )\n                return\n            _risk = float(ltp) - float(signal.sl_price)\n            _flip_sl = float(_opp_ltp) - _risk\n            if _flip_sl <= 0:\n                write_audit_log(\n                    f"[HA][FLIP_SKIP] {symbol} — transferred risk "\n                    f"{_risk:.2f} >= flipped LTP {float(_opp_ltp):.2f} "\n                    f"({_opp_symbol}); synthetic SL <= 0; no entry (fail-closed)"\n                )\n                return\n            _entry_symbol = _opp_symbol\n            _entry_side = _opp_side\n            _entry_ltp = float(_opp_ltp)\n            _entry_sl = _flip_sl\n            write_audit_log(\n                f"[HA][FLIP] COND1 {symbol} {side} ltp={float(ltp):.2f} "\n                f"red_low={float(signal.sl_price):.2f} risk={_risk:.2f} → "\n                f"ENTER {_entry_symbol} {_entry_side} ltp={_entry_ltp:.2f} "\n                f"sl={_entry_sl:.2f}"\n            )\n        # ── HA_COND1_FLIP END ──\n\n        write_audit_log(\n            f"[HA][SIGNAL_FIRED] {_entry_symbol} side={_entry_side} "\n            f"cond={signal.condition} sl={_entry_sl:.2f} ltp={_entry_ltp:.2f}"\n        )\n\n        # Annotate DB row with signal (unchanged — records that a signal fired,\n        # independent of whether arbitration ultimately elects this side).\n        try:\n            insert_ha_candle(\n                symbol=symbol,\n                timeframe="1m",\n                ts=ha.ts,\n                ha_open=ha.open,\n                ha_high=ha.high,\n                ha_low=ha.low,\n                ha_close=ha.close,\n                ema20_low=ema_val,\n                is_green=ha.is_green,\n                signal_action=f"ENTER_{side}",\n                signal_reason=signal.condition,\n            )\n        except Exception:\n            pass\n\n        # ── ARB_WINDOW BEGIN ──────────────────────────────────────\n        # GLOBAL single-trade arbitration. Instead of confirm_entry + enter()\n        # synchronously, defer the candidate into a per-minute window. The\n        # winner (highest entry_ltp) is elected when the window timer expires;\n        # only the winner gets confirm_entry()+enter(). This matches the\n        # backtest\'s global one-trade-at-a-time, highest-premium arbitration and\n        # applies in BOTH paper and live. confirm_entry is DEFERRED to election\n        # (per decision) so a dropped side never burns a daily-cap slot.\n        # ── HA_COND1_FLIP ── the offer uses the entry_* vars: identical to the\n        # signal values when the flip is off; the flipped contract when on.\n        self._offer_to_arbitration(\n            bucket_ts=int(ha.ts),\n            side=_entry_side,\n            symbol=_entry_symbol,\n            entry_ltp=_entry_ltp,\n            sl_price=_entry_sl,\n            condition=signal.condition,\n        )\n        # ── ARB_WINDOW END ────────────────────────────────────────\n',
     '        write_audit_log(\n            f"[HA][SIGNAL_FIRED] {symbol} side={side} "\n            f"cond={signal.condition} sl={signal.sl_price:.2f} ltp={ltp:.2f}"\n        )\n\n        # Annotate DB row with signal (unchanged — records that a signal fired,\n        # independent of whether arbitration ultimately elects this side).\n        try:\n            insert_ha_candle(\n                symbol=symbol,\n                timeframe="1m",\n                ts=ha.ts,\n                ha_open=ha.open,\n                ha_high=ha.high,\n                ha_low=ha.low,\n                ha_close=ha.close,\n                ema20_low=ema_val,\n                is_green=ha.is_green,\n                signal_action=f"ENTER_{side}",\n                signal_reason=signal.condition,\n            )\n        except Exception:\n            pass\n\n        # ── ARB_WINDOW BEGIN ──────────────────────────────────────\n        # GLOBAL single-trade arbitration. Instead of confirm_entry + enter()\n        # synchronously, defer the candidate into a per-minute window. The\n        # winner (highest entry_ltp) is elected when the window timer expires;\n        # only the winner gets confirm_entry()+enter(). This matches the\n        # backtest\'s global one-trade-at-a-time, highest-premium arbitration and\n        # applies in BOTH paper and live. confirm_entry is DEFERRED to election\n        # (per decision) so a dropped side never burns a daily-cap slot.\n        self._offer_to_arbitration(\n            bucket_ts=int(ha.ts),\n            side=side,\n            symbol=symbol,\n            entry_ltp=float(ltp),\n            sl_price=float(signal.sl_price),\n            condition=signal.condition,\n        )\n'),
    ('backend/app/backtest/ha/backtest_ha_runner.py', 'runner: docstring',
     "      cond1_flip_side              bool — COND1-ONLY opposite-side experiment:\n                                   a CE signal buys the snapshot's selected PE\n                                   (and vice versa), risk transferred in points\n                                   from the signal contract, TP from the flip\n                                   entry. Composes with cond1_retrace (flip\n                                   first, then the limit arms on the flipped\n                                   contract). Default OFF → legacy behaviour.\n",
     ''),
    ('backend/app/backtest/ha/backtest_ha_runner.py', 'runner: c1flip resolve',
     '    # ── HA_COND1_FLIP BEGIN ── COND1-only OPPOSITE-SIDE entry experiment.\n    # cond1_flip_side: bool. Default OFF → bit-identical legacy behaviour.\n    #\n    # WHAT: when a COND1 signal fires on side X, do NOT trade the signalling\n    # contract — trade the SAME selection snapshot\'s OPPOSITE-side contract\n    # (CE signal → buy the selected PE, and vice versa). The bet inverts from\n    # "the EMA bounce continues" to "the bounce fails".\n    #\n    # MECHANICS (flip happens at CANDIDATE CONSTRUCTION, before arbitration,\n    # so retrace arming / fills / exits all operate on the flipped contract\n    # unchanged — the two features compose):\n    #   * Flip target = opposite-side symbols in the ACTIVE snapshot that have\n    #     a bar in THIS 1m bucket; if several, highest bar-close premium wins,\n    #     symbol tie-break (the arbitration convention). No bar → candidate\n    #     dropped (rej_flip_no_bar).\n    #   * RISK TRANSFERS IN POINTS: risk = signal entry − signal red-low (the\n    #     red-low is a level on the SIGNAL contract and means nothing on the\n    #     flipped one). flip_sl = flip_entry − risk; TP from flip_entry at the\n    #     configured RR (or override points). Both bands share the premium\n    #     selection band, so point-parity is comparable across sides.\n    #   * side_mode and the per-side daily cap are RE-CHECKED against the\n    #     FLIPPED side (the earlier gates validated the signal side).\n    # HONESTY NOTE: with C1 measuring ≈ -0.04R (a paid coin flip), theory says\n    # the flip is ≈ the same coin flip minus charges — this switch exists to\n    # let the DATA say so. It is an experiment knob, not a recommended mode.\n    c1flip = bool(cfg.get("cond1_flip_side"))\n    # ── HA_COND1_FLIP END ──\n',
     ''),
    ('backend/app/backtest/ha/backtest_ha_runner.py', 'runner: diag keys',
     '        # ── HA_COND1_FLIP ── flip funnel (all zero when disabled)\n        "flip_applied": 0, "rej_flip_no_bar": 0,\n        "rej_flip_side_mode": 0, "rej_flip_cap": 0,\n',
     ''),
    ('backend/app/backtest/ha/backtest_ha_runner.py', 'runner: bucket_bars',
     "            # ── HA_COND1_FLIP ── O(1) bar lookup for the flip target within\n            # this same 1m bucket (items is exactly this bucket's bars).\n            bucket_bars = dict(items)\n",
     ''),
    ('backend/app/backtest/ha/backtest_ha_runner.py', 'runner: candidate flip',
     '                # ── HA_COND1_FLIP BEGIN ── COND1-only: replace the candidate\n                # with the snapshot\'s OPPOSITE-side contract. Runs AFTER every\n                # signal-side gate and BEFORE arbitration, so the winner (and\n                # any retrace arming on it) is already the flipped contract.\n                if c1flip and signal.condition == "COND1":\n                    opp_side = "PE" if side == "CE" else "CE"\n                    # side_mode re-check against the FLIPPED side.\n                    if side_mode in ("CE", "PE") and side_mode != opp_side:\n                        _diag["rej_flip_side_mode"] += 1\n                        continue\n                    # per-side cap + in-trade flag re-check for the FLIPPED side.\n                    _fok, _freason = signal_engine.can_enter(opp_side)\n                    if not _fok:\n                        _diag["rej_flip_cap"] += 1\n                        continue\n                    # Flip target: opposite-side snapshot members with a bar in\n                    # THIS bucket. Highest close premium, symbol tie-break (the\n                    # arbitration convention) — deterministic.\n                    _opp_sel = sel_pe if side == "CE" else sel_ce\n                    _best = None\n                    for _os in _opp_sel:\n                        _ob = bucket_bars.get(_os)\n                        if _ob is None or _os not in meta_map:\n                            continue\n                        _key = (float(_ob["close"]), _os)\n                        if _best is None or _key > _best[0]:\n                            _best = (_key, _os, _ob)\n                    if _best is None:\n                        _diag["rej_flip_no_bar"] += 1\n                        continue\n                    _, flip_sym, flip_bar = _best\n                    flip_entry = float(flip_bar["close"])\n                    # RISK TRANSFERS IN POINTS (signal red-low is meaningless\n                    # on the flipped contract). Same risk → MIN_SL_GATE parity\n                    # holds automatically; flip_sl < flip_entry since risk > 0.\n                    flip_sl = flip_entry - risk\n                    flip_tp = (flip_entry + override_pts) if override_on \\\n                        else (flip_entry + risk * rr)\n                    _diag["flip_applied"] += 1\n                    entry_candidates.append((flip_entry, flip_sym, {\n                        "side": opp_side, "strike": meta_map[flip_sym]["strike"],\n                        "entry_ts": snap_end_ts, "entry_price": flip_entry,\n                        "sl": flip_sl, "tp": flip_tp,\n                        "condition": "COND1",\n                    }))\n                    continue\n                # ── HA_COND1_FLIP END ──\n\n',
     ''),
    ('backend/app/backtest/ha/backtest_ha_runner.py', 'runner: diag print',
     '            # ── HA_COND1_FLIP ── (all zero when disabled)\n            f"c1_flip: applied={_diag[\'flip_applied\']} "\n            f"no_bar={_diag[\'rej_flip_no_bar\']} "\n            f"side_mode={_diag[\'rej_flip_side_mode\']} "\n            f"cap={_diag[\'rej_flip_cap\']} | "\n',
     ''),
    ('frontend/src/pages/Settings.jsx', 'settings: default key',
     '  // ── HA_COND1_FLIP ── COND1-only opposite-side entry (backtest-validated).\n  // Deep-merge backfills existing saved configs to FALSE = current behaviour;\n  // the tick engine treats an absent/false key as flip-off (fail-closed).\n  cond1_flip_side:      false,\n',
     ''),
    ('frontend/src/pages/Settings.jsx', 'settings: toggle',
     '\n              {/* ── HA_COND1_FLIP BEGIN ── COND1-only opposite-side entry\n                  (PAPER + LIVE — the transform sits in the shared signal path\n                  in ha_tick_engine, before arbitration). A C1 signal on the\n                  selected CE enters the selected PE and vice versa; risk\n                  transfers in points. C2/C3 entries are never affected. */}\n              <Field label="C1 Flip Side"\n                helper="COND1 signals enter the OPPOSITE side\'s selected contract (CE↔PE). C2/C3 unaffected. Backtest: C1 standalone flips −4.1L → +1.5L; pair with C3.">\n                <div style={{ display: "flex", gap: 8 }}>\n                  {[["0", "Off (signal side)"], ["1", "On (CE↔PE opposite)"]].map(([v, label]) => {\n                    const on = (haConfig.cond1_flip_side ? "1" : "0") === v;\n                    return (\n                      <button key={v} type="button"\n                        onClick={() => updateHA(["cond1_flip_side"], v === "1")}\n                        style={{\n                          padding: "6px 14px", borderRadius: 6, fontSize: 13, fontWeight: 700,\n                          cursor: "pointer",\n                          border: `1px solid ${on ? colors.primary : colors.border.light}`,   /* ── THEME_PHASE2B_20260831 ── */\n                          background: on ? "rgba(59,130,246,0.15)" : "transparent",\n                          color: on ? colors.primary : colors.text.tertiary,\n                        }}>\n                        {label}\n                      </button>\n                    );\n                  })}\n                </div>\n              </Field>\n              {/* ── HA_COND1_FLIP END ── */}\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: chip',
     '  // ── HA_COND1_FLIP ── RUN_PARAMS_DISPLAY tripwire, same contract as retrace.\n  if (cfg.cond1_flip_side) add("C1 flip", "CE↔PE (opposite side)");\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: state',
     '  // ── HA_COND1_FLIP BEGIN ── COND1-only opposite-side experiment (HA_V1\n  // ONLY, same SHARED_EXEC_FIELDS gating as retrace). CE signal buys the\n  // selected PE and vice versa; risk transfers in points. Composes with\n  // retrace (the limit arms on the flipped contract).\n  const [c1FlipSide, setC1FlipSide] = useState(saved.c1FlipSide ?? false);\n  // ── HA_COND1_FLIP END ──\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: dep1',
     '      c1FlipSide,   // ── HA_COND1_FLIP ──\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: dep2',
     '      c1FlipSide,   // ── HA_COND1_FLIP ── stale-closure rule (saveParams)\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: emit',
     '      // ── HA_COND1_FLIP ── HA_V1 ONLY, emitted only when ON (key absent →\n      // runner legacy path; a disabled form never ships a stale flag).\n      if (sid === "HA_V1" && c1FlipSide) haCfg.cond1_flip_side = true;\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: dep3',
     '      c1FlipSide,   // ── HA_COND1_FLIP ── stale-closure rule\n',
     ''),
    ('frontend/src/pages/Backtest.jsx', 'bt: form',
     '                  {/* ── HA_COND1_FLIP BEGIN ── opposite-side experiment: a C1\n                      CE signal buys the selected PE and vice versa. Composes\n                      with retrace (limit arms on the flipped contract). */}\n                  <Field label="C1 flip side">\n                    <select style={inputStyle} value={c1FlipSide ? "1" : "0"} onChange={(e) => setC1FlipSide(e.target.value === "1")}>\n                      <option value="0">Off (signal side)</option>\n                      <option value="1">On (CE↔PE opposite)</option>\n                    </select>\n                  </Field>\n                  {/* ── HA_COND1_FLIP END ── */}\n',
     ''),
    ('frontend/src/pages/backtest/RunComparison.jsx', 'cmp: row',
     '  // ── HA_COND1_FLIP ── a flipped run and a signal-side run must never\n  // compare as identical params in the diff matrix.\n  { key: "c1_flip",          label: "C1 flip side",   get: (r) => (r.config?.cond1_flip_side ? "CE↔PE" : null) },\n',
     ''),
    ('frontend/src/pages/backtest/SweepBuilder.jsx', 'sweep: axis',
     '  // ── HA_COND1_FLIP ── 0/1 axis (IV12keep pattern) — lets flip vs signal-side\n  // A/B run as ONE sweep, alone or crossed with the retrace axes.\n  { key: "c1r_flip", label: "C1 flip side (0/1)", strategies: [HA],\n    hint: "0, 1", parse: _num,\n    apply: (c, v) => { if (v) c.cond1_flip_side = true; else delete c.cond1_flip_side; },\n    fmt: (v) => (v ? "c1flip" : "c1 signal-side") },\n',
     ''),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', 'queue: label',
     '  if (cfg.cond1_flip_side) p.push("c1flip");   // ── HA_COND1_FLIP ──\n',
     ''),
]

RESIDUE = ("HA_COND1_FLIP", "cond1_flip_side", "c1FlipSide", "c1flip", "bucket_bars",
           "_entry_symbol", "_entry_side", "_entry_ltp", "_entry_sl")
PY_FILES = [p for p in {e[0] for e in EDITS} if p.endswith(".py")]
JSX_FILES = [p for p in {e[0] for e in EDITS} if p.endswith(".jsx")]

SIM_TEST = r"""'''Behavioural test of ha_tick_engine._on_candle_close signal path after the
HA_COND1_FLIP revert. Runs against a checkout root passed as argv[1].
Stubs every app.* dependency with fakes; drives the real evaluator/signal
engine/HA indicator code. Asserts:
  T1 COND1 signal offers the SIGNAL contract (no flip) with signal ltp/sl
  T2 COND2 / COND3 still offer normally
  T3 MIN_SL_GATE skips a sub-floor signal (unchanged)
  T4 HA_COND_FILTER skips a disabled condition (unchanged)
  T5 a config that still carries cond1_flip_side:true is IGNORED (no flip)
'''
import sys, types, importlib
ROOT = sys.argv[1]
sys.path.insert(0, ROOT + "/backend")

LOG = []
CFG = {"session": {"primary": {"start": "00:00", "end": "23:59"}},
       "trade_side_mode": "BOTH", "min_sl_points": 0, "entry_conditions": [],
       "max_trades_per_side": 10}
def stub(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m
for pkg in ["app", "app.risk", "app.indicators", "app.engine", "app.engine.ha_options",
            "app.marketdata", "app.event_bus", "app.config", "app.core", "app.db"]:
    if pkg not in sys.modules:
        m = types.ModuleType(pkg); m.__path__ = []; sys.modules[pkg] = m
sys.modules["app"].__path__ = [ROOT + "/backend/app"]
sys.modules["app.indicators"].__path__ = [ROOT + "/backend/app/indicators"]
sys.modules["app.engine.ha_options"].__path__ = [ROOT + "/backend/app/engine/ha_options"]
stub("app.risk.risk_mtm_guard", mtm_breach_ha=lambda **k: None, is_day_blocked=lambda s: False,
     has_open_positions_ha=lambda m, t: False)
class _LTP:
    _d = {}
    @classmethod
    def update(c, s, v): c._d[s] = v
    @classmethod
    def get(c, s): return c._d.get(s)
stub("app.marketdata.ltp_store", LTPStore=_LTP)
stub("app.event_bus.audit_logger", write_audit_log=lambda m: LOG.append(m))
stub("app.marketdata.ws_registry", get_ws_engines=lambda: [])
stub("app.config.strategy_loader", load_strategy_config=lambda s: dict(CFG),
     load_strategy_config_ex=lambda s: (dict(CFG), False))
stub("app.config.global_loader", load_global_config=lambda: {"trade_on": True})
stub("app.core.ha_engine_registry", HA_ENGINE_REGISTRY=[])
stub("app.db.ha_candles_repo", init_table=lambda: None, insert_ha_candle=lambda **k: None,
     fetch_recent_ha_candles=lambda **k: [])
# trade manager stub: only what the engine constructor + signal path touches
class _TM:
    def __init__(self, **k): self._live = {}
    def attach_engine(self, e): pass
    def is_off(self): return False
    def _has_open_trade(self): return False
stub("app.engine.ha_options.ha_trade_manager", HATradeManager=_TM)

eng_mod = importlib.import_module("app.engine.ha_options.ha_tick_engine")
from app.indicators.heikin_ashi import HACandle

OFFERS = []
def make_engine():
    e = eng_mod.HAOptionsTickEngine(executor=None, config=dict(CFG), trade_mode="PAPER")
    e._offer_to_arbitration = lambda **k: OFFERS.append(k)
    e._selected_ce, e._selected_pe = "NIFTY_CE", "NIFTY_PE"
    for s, t in (("NIFTY_CE", 1), ("NIFTY_PE", 2)):
        e._states[s] = eng_mod.SymbolState(s, t)
    return e

def feed(e, sym, seq, ema=100.0):
    '''seq: list of (o,h,l,c) already IN HA space; we bypass the converter by
    pushing HACandles directly into the evaluator via _on_candle_close.'''
    st = e._states[sym]; st.ema_low_value = ema
    ts = 1_700_000_000
    for i, (o, h, l, c) in enumerate(seq):
        ha = HACandle(ts=ts + 60 * i, open=o, high=h, low=l, close=c)
        st.last_ha = ha
        e._on_candle_close(sym, ha, st)

def reset():
    OFFERS.clear(); LOG.clear(); _LTP._d.clear()

# COND1: N-1 RED touching EMA(100), N GREEN.  red low = 98 (SL)
COND1 = [(110, 112, 108, 109), (109, 109, 98, 99), (99, 106, 99, 105)]
# COND2: N GREEN touching EMA, N-1 RED (red low 95 → SL 95)
COND2 = [(110, 112, 108, 109), (109, 110, 101, 102), (102, 104, 99, 103)]
# COND3: N2 RED, N1 GREEN not touching, N GREEN touching. red low 97
COND3 = [(110, 111, 97, 98), (98, 105, 102, 104), (104, 106, 99, 105)]

# T1
reset(); CFG.update(min_sl_points=0, entry_conditions=[]); e = make_engine()
_LTP.update("NIFTY_CE", 105.0); _LTP.update("NIFTY_PE", 140.0)
feed(e, "NIFTY_CE", COND1)
assert len(OFFERS) == 1, OFFERS
o = OFFERS[0]
assert o["symbol"] == "NIFTY_CE" and o["side"] == "CE" and o["entry_ltp"] == 105.0 \
    and o["sl_price"] == 98.0 and o["condition"] == "COND1", o
assert any("[HA][SIGNAL_FIRED] NIFTY_CE side=CE cond=COND1 sl=98.00 ltp=105.00" in m for m in LOG)
assert not any("FLIP" in m for m in LOG)
print("T1 PASS  COND1 offers the signal contract itself (no flip, no FLIP log)")

# T2
reset(); e = make_engine(); _LTP.update("NIFTY_PE", 103.0)
feed(e, "NIFTY_PE", COND2)
assert OFFERS and OFFERS[-1]["condition"] == "COND2" and OFFERS[-1]["symbol"] == "NIFTY_PE" \
    and OFFERS[-1]["sl_price"] == 101.0, OFFERS
reset(); e = make_engine(); _LTP.update("NIFTY_CE", 105.0)
feed(e, "NIFTY_CE", COND3)
assert OFFERS and OFFERS[-1]["condition"] == "COND3" and OFFERS[-1]["sl_price"] == 97.0, OFFERS
print("T2 PASS  COND2 / COND3 offer normally")

# T3 min SL gate: risk = 105-98 = 7 < 18 → skip
reset(); CFG.update(min_sl_points=18); e = make_engine(); _LTP.update("NIFTY_CE", 105.0)
feed(e, "NIFTY_CE", COND1)
assert not OFFERS and any("MIN SL 18.00" in m for m in LOG), LOG[-3:]
CFG.update(min_sl_points=0)
print("T3 PASS  MIN_SL_GATE still skips sub-floor signals")

# T4 cond filter: only COND2/COND3 enabled → COND1 skipped
reset(); CFG.update(entry_conditions=["COND2", "COND3"]); e = make_engine(); _LTP.update("NIFTY_CE", 105.0)
feed(e, "NIFTY_CE", COND1)
assert not OFFERS and any("condition COND1 not in enabled {COND2,COND3}" in m for m in LOG), LOG[-3:]
CFG.update(entry_conditions=[])
print("T4 PASS  HA_COND_FILTER still skips disabled conditions")

# T5 stale key in a saved config is inert
reset(); CFG["cond1_flip_side"] = True; e = make_engine()
_LTP.update("NIFTY_CE", 105.0); _LTP.update("NIFTY_PE", 140.0)
feed(e, "NIFTY_CE", COND1)
assert len(OFFERS) == 1 and OFFERS[0]["symbol"] == "NIFTY_CE" and OFFERS[0]["entry_ltp"] == 105.0, OFFERS
assert not any("FLIP" in m for m in LOG)
del CFG["cond1_flip_side"]
print("T5 PASS  a leftover cond1_flip_side:true in a saved config is ignored")
print("5/5 PASS")
"""


def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def apply_edits(src: str, path: str) -> str:
    for p, name, old, new in EDITS:
        if p != path:
            continue
        c = src.count(old)
        if c != 1:
            fail(f"{path}: anchor [{name}] x{c}, expected x1 — the file drifted from "
                 f"GitHub main (2026-09-22); inspect by hand")
        src = src.replace(old, new)
    for tok in RESIDUE:
        if tok in src:
            fail(f"{path}: residue {tok!r} still present after edit")
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry run, write nothing")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="proceed although git shows local edits on a target file")
    a = ap.parse_args()

    targets = sorted({e[0] for e in EDITS})
    done = os.path.join(ROOT, f".{REVERT}.done")
    if os.path.exists(done):
        print(f"  SKIP   {REVERT} already applied ({done} present) — nothing to do")
        return

    # ── read + idempotency ─────────────────────────────────────────────
    srcs = {}
    for t in targets:
        p = os.path.join(ROOT, t)
        if not os.path.exists(p):
            fail(f"{t} not found — run from the scalp-app repo root")
        srcs[t] = open(p, encoding="utf-8").read()
    if not any("HA_COND1_FLIP" in s for s in srcs.values()):
        print("  SKIP   HA_COND1_FLIP not present in any target — already reverted")
        return
    for t, s in srcs.items():
        if "HA_COND1_FLIP" not in s:
            fail(f"{t}: fence absent while other files still carry it — partial state; inspect")

    # ── git drift check (working-copy drift scar, 2026-09-11) ──────────
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--"] + targets, cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        status = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        status = ""
    if status and not a.allow_dirty:
        fail("uncommitted edits on target files:\n         " + status.replace("\n", "\n         ")
             + "\n         commit them first (preferred) or re-run with --allow-dirty")
    print("  OK     targets present, fence present, tree clean" + (" (dirty allowed)" if status else ""))

    # ── stage ──────────────────────────────────────────────────────────
    staged = {t: apply_edits(s, t) for t, s in srcs.items()}
    print(f"  OK     {len(EDITS)} anchors x1, residue scan clean in {len(targets)} files")

    stage_dir = tempfile.mkdtemp(prefix="ha_flip_revert_")
    for t, s in staged.items():
        sp = os.path.join(stage_dir, t)
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(s)

    # ── py_compile + pyflakes ───────────────────────────────────────────
    for t in PY_FILES:
        try:
            py_compile.compile(os.path.join(stage_dir, t), doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"py_compile {t}: {e}")
    print("  OK     py_compile gate passed")
    try:
        import pyflakes  # noqa
        r = subprocess.run([sys.executable, "-m", "pyflakes"] + [os.path.join(stage_dir, t) for t in PY_FILES],
                           capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() if "undefined name" in l]
        if bad:
            fail("pyflakes undefined names:\n         " + "\n         ".join(bad))
        print("  OK     pyflakes undefined-name gate passed")
    except ImportError:
        print("  WARN   pyflakes not installed — undefined-name gate skipped")

    # ── behavioural simulation against the staged engine ───────────────
    # The sim imports app.indicators / app.engine.ha_options from a checkout
    # root, so build one: staged engine over the real repo for everything else.
    sim_root = os.path.join(stage_dir, "simroot")
    os.makedirs(os.path.join(sim_root, "backend", "app", "engine", "ha_options"), exist_ok=True)
    os.makedirs(os.path.join(sim_root, "backend", "app", "indicators"), exist_ok=True)
    for rel in ("backend/app/indicators/heikin_ashi.py", "backend/app/indicators/ema.py",
                "backend/app/engine/ha_options/ha_signal_engine.py"):
        shutil.copy2(os.path.join(ROOT, rel), os.path.join(sim_root, rel))
    shutil.copy2(os.path.join(stage_dir, "backend/app/engine/ha_options/ha_tick_engine.py"),
                 os.path.join(sim_root, "backend/app/engine/ha_options/ha_tick_engine.py"))
    sim_py = os.path.join(stage_dir, "sim.py")
    with open(sim_py, "w", encoding="utf-8") as f:
        f.write(SIM_TEST)
    r = subprocess.run([sys.executable, sim_py, sim_root], capture_output=True, text=True)
    if r.returncode != 0 or "5/5 PASS" not in r.stdout:
        fail(f"behavioural simulation failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    for line in r.stdout.strip().splitlines():
        print("         " + line)
    print("  OK     behavioural simulation 5/5")

    # ── esbuild parse gate ─────────────────────────────────────────────
    esb, npx = shutil.which("esbuild"), shutil.which("npx")
    cmd = ([esb] if esb else [npx, "--yes", "esbuild"] if npx else None)
    if cmd is None:
        print("  WARN   esbuild unavailable — JSX gate skipped")
    else:
        for t in JSX_FILES:
            g = subprocess.run(cmd + ["--loader:.jsx=jsx", os.path.join(stage_dir, t), "--outfile=" + os.devnull],
                               capture_output=True, text=True, cwd=os.path.join(ROOT, "frontend"))
            if g.returncode != 0:
                fail(f"esbuild gate {t}:\n{g.stderr[-2000:]}")
        print(f"  OK     esbuild JSX gate passed ({len(JSX_FILES)} files)")

    # ── saved config key ───────────────────────────────────────────────
    cfg_path = os.path.join(os.path.expanduser("~"), ".scalp-app", "strategies", "HA_V1.json")
    cfg_has_key = False
    if os.path.exists(cfg_path):
        try:
            cfg_has_key = "cond1_flip_side" in json.load(open(cfg_path, encoding="utf-8"))
        except Exception as e:
            print(f"  WARN   could not parse {cfg_path} ({e}) — config left alone")

    if a.check:
        for t in targets:
            print(f"  WOULD  edit   {t}")
        if cfg_has_key:
            print(f"  WOULD  drop   cond1_flip_side from {cfg_path}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write (all-or-nothing) ─────────────────────────────────────────
    written = []
    try:
        for t in targets:
            p = os.path.join(ROOT, t)
            shutil.copy2(p, p + f".bak-{REVERT}")
            with open(p, "w", encoding="utf-8") as f:
                f.write(staged[t])
            written.append(p)
            print(f"  WROTE  {t}")
        # verify
        for t in targets:
            got = open(os.path.join(ROOT, t), encoding="utf-8").read()
            if got != staged[t] or any(tok in got for tok in RESIDUE):
                raise RuntimeError(f"post-write verification failed on {t}")
    except Exception as e:
        for p in written:
            shutil.copy2(p + f".bak-{REVERT}", p)
        fail(f"{e} — all {len(written)} written files restored from backup")

    # mirror backend (gitignored; rsync'd by build-scalp.sh, but keep it in step)
    for t in PY_FILES:
        mp = os.path.join(ROOT, MIRROR_BACKEND, t[len("backend/"):])
        if os.path.exists(mp):
            ms = open(mp, encoding="utf-8").read()
            if "HA_COND1_FLIP" in ms and all(ms.count(o) == 1 for p, _, o, _ in EDITS if p == t):
                shutil.copy2(mp, mp + f".bak-{REVERT}")
                with open(mp, "w", encoding="utf-8") as f:
                    f.write(apply_edits(ms, t))
                print(f"  WROTE  mirror {MIRROR_BACKEND}/{t[len('backend/'):]}")
            else:
                print(f"  NOTE   mirror {t} not patched (fence absent or anchors differ) — build-scalp.sh re-syncs it")

    if cfg_has_key:
        try:
            shutil.copy2(cfg_path, cfg_path + f".bak-{REVERT}")
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            cfg.pop("cond1_flip_side", None)
            tmp = cfg_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, cfg_path)
            print(f"  WROTE  {cfg_path} (cond1_flip_side removed)")
        except Exception as e:
            print(f"  WARN   config key not removed ({e}) — harmless, the engine ignores it")

    with open(done, "w") as f:
        f.write(REVERT + "\n")
    print()
    print("  DONE   HA_COND1_FLIP removed from 7 files.")
    print("         Rebuild: ./desktop/build-scalp.sh both")
    print("         Settings → HA_V1: the 'C1 Flip Side' row is gone; re-save once so the")
    print("         config round-trips without the key. Backtest → HA_V1: 'C1 flip side'")
    print("         select, sweep axis c1r_flip, compare row and queue label are gone.")
    print(f"         Backups: *.bak-{REVERT}")


if __name__ == "__main__":
    main()
