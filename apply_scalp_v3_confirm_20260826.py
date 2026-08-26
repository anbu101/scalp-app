#!/usr/bin/env python3
# apply_scalp_v3_confirm_20260826.py
#
# V3-D4 — FIRST-CANDLE ENTRY CONFIRMATION (agreed D4.1-D4.5 + D4.2b).
# PREREQ: PARALLEL + TPMULT + EMA_GATE + SWEEP_AXES patches applied (anchors).
#
# Evidence: on gate+3.5x (run c5504442) the <=2-minute bucket carries -110.3L
# net while everything surviving its first candle is +73.5L net. The first
# candle after entry resolves adversely 58% of the time on the baseline. D4
# skips the knife: signal election creates a PENDING; entry happens one
# candle later, only if the hedge printed no new low (and the signal didn't
# blow through its frozen SL).
#
# SEMANTICS (per the agreed walkthrough):
#   D4.1  election at candle T -> pending {frozen signal SL/TP, hedge chosen
#         at T, hedge's T low recorded}; NO order.
#   D4.2  at T+1: enter iff hedge low >= T low (strictly-below rejects;
#         equality passes). Fill = T+1 hedge close, stamped T+120.
#   D4.2b reject if T+1 signal high >= frozen signal SL (dead-on-arrival).
#   D4.3  new low -> discard, no trade, counted.
#   D4.4  a pending owns nothing. NOTE (proven by ordering): pendings resolve
#         at the top of each minute BEFORE the candidate scan can create a
#         new one, so at most ONE pending is ever live -- the two-confirm tie
#         case cannot arise; slot-busy at confirm time is defensive only.
#         EOD discipline: a confirm whose stamp would land >= 15:15 discards.
#         Stale pendings (T+1 minute absent from the replay) discard --
#         fail closed, no data = no confirmation. Day boundary resets.
#   D4.5  config key entry_confirmation {enabled:true}, omit-when-off ->
#         baseline configs stay byte-identical to 95e70e7e. Surfaces: V3 UI
#         toggle, run-detail chip, queue label token, RunComparison row,
#         SweepBuilder axis, summary stats (serial + parallel-merged).
#
# FILES: backtest_hedge_runner.py (dual-tree) · Backtest.jsx ·
#        BacktestQueue.jsx · RunComparison.jsx · SweepBuilder.jsx (+ mirrors)
#
# ACCEPTANCE: confirmation OFF, gate+3.5x config -> byte-identical to
# c5504442. Then ON at the same config -- the run summary's
# entry_confirmation block reports pendings/confirms/rejections.

import glob
import os
import py_compile
import sys
import tempfile

REPO = os.getcwd()
BACKEND_TREES = ["backend", os.path.join("desktop", "src-tauri", "backend")]
RUNNER = os.path.join("app", "backtest", "runner", "backtest_hedge_runner.py")
FENCE = "SCALP_V3_CONFIRM_20260826"


def fail(msg):
    print(f"\n[ABORT] {msg}\nNothing was written.")
    sys.exit(1)


EC_KEYS = ["pendings_created", "confirmed_entries", "rejected_new_low",
           "rejected_signal_invalid", "discarded_no_data", "discarded_slot_busy"]

RUNNER_EDITS = [
    # -- run-scope state + enabled flag --
    (
        "    side_mode = cfg.get(\"trade_side_mode\", \"BOTH\").upper()\n"
        "    hedge_sl_pts = _hedge_sl_points(cfg)\n",
        "    side_mode = cfg.get(\"trade_side_mode\", \"BOTH\").upper()\n"
        "    hedge_sl_pts = _hedge_sl_points(cfg)\n"
        "\n"
        "    # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 D4 first-candle entry confirmation.\n"
        "    # Off/absent = today's immediate-entry path, bit-identical.\n"
        "    _ec_cfg = cfg.get(\"entry_confirmation\") or {}\n"
        "    _conf_enabled = (bool(_ec_cfg.get(\"enabled\"))\n"
        "                     if isinstance(_ec_cfg, dict) else bool(_ec_cfg))\n"
        "    _ec_stats = {\"pendings_created\": 0, \"confirmed_entries\": 0,\n"
        "                 \"rejected_new_low\": 0, \"rejected_signal_invalid\": 0,\n"
        "                 \"discarded_no_data\": 0, \"discarded_slot_busy\": 0}\n"
        "    _pending = None\n",
    ),
    # -- day reset --
    (
        "        # \u2500\u2500 V3_TRADE_COUNT_LIMITS \u2500\u2500 new IST day: reset trade counters.\n"
        "        _tc_day_total = 0\n"
        "        _tc_day_side = {\"CE\": 0, \"PE\": 0}\n",
        "        # \u2500\u2500 V3_TRADE_COUNT_LIMITS \u2500\u2500 new IST day: reset trade counters.\n"
        "        _tc_day_total = 0\n"
        "        _tc_day_side = {\"CE\": 0, \"PE\": 0}\n"
        "        _pending = None   # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 pendings are intra-day\n",
    ),
    # -- resolve pending at the top of each minute, before the scan --
    (
        "            if _day_blocked or (_month_key in _month_blocked):\n"
        "                continue\n"
        "            entry_candidates = []  # (entry_price, signal_symbol, ctx, c, signal)\n",
        "            if _day_blocked or (_month_key in _month_blocked):\n"
        "                continue\n"
        "            # \u2500\u2500 SCALP_V3_CONFIRM_20260826 BEGIN: D4.2/2b/3 \u2014 resolve the pending\n"
        "            # BEFORE the candidate scan (resolve-before-create ordering is what\n"
        "            # guarantees at most one live pending). Fail closed on any gap:\n"
        "            # stale minute, missing hedge candle, or a post-15:15 stamp.\n"
        "            if _pending is not None and ts >= _pending[\"t\"] + 60:\n"
        "                _p, _pending = _pending, None\n"
        "                _hctx = ctxs.get(_p[\"hedge_sym\"])\n"
        "                _hc = _hctx.by_ts.get(ts) if _hctx is not None else None\n"
        "                _sctx = ctxs.get(_p[\"signal_sym\"])\n"
        "                _sc = _sctx.by_ts.get(ts) if _sctx is not None else None\n"
        "                if ts > _p[\"t\"] + 60 or _hc is None:\n"
        "                    _ec_stats[\"discarded_no_data\"] += 1\n"
        "                elif ts + 60 >= eod_close_ts:\n"
        "                    _ec_stats[\"discarded_no_data\"] += 1\n"
        "                elif _sc is not None and _sc.high >= _p[\"signal_sl\"]:\n"
        "                    _ec_stats[\"rejected_signal_invalid\"] += 1   # D4.2b\n"
        "                elif _hc.low < _p[\"hedge_low_t\"]:\n"
        "                    _ec_stats[\"rejected_new_low\"] += 1          # D4.3\n"
        "                elif book.any_open():\n"
        "                    _ec_stats[\"discarded_slot_busy\"] += 1       # defensive\n"
        "                else:\n"
        "                    hedge_entry = round(_hc.close, 2)\n"
        "                    hedge_sl = round(hedge_entry - hedge_sl_pts, 2)\n"
        "                    book.open_position(HedgePosition(\n"
        "                        signal_symbol=_p[\"signal_sym\"], signal_token=0,\n"
        "                        signal_side=_p[\"signal_side\"],\n"
        "                        signal_entry_price=_p[\"entry_ref\"],\n"
        "                        signal_sl=_p[\"signal_sl\"], signal_tp=_p[\"signal_tp\"],\n"
        "                        signal_candle_ts=ts,\n"
        "                        hedge_symbol=_p[\"hedge_sym\"], hedge_token=0,\n"
        "                        hedge_side=_p[\"hedge_side\"],\n"
        "                        hedge_entry_ts=ts + 60, hedge_entry_price=hedge_entry,\n"
        "                        hedge_sl=hedge_sl, qty=qty,\n"
        "                        condition=_p[\"diag\"]))\n"
        "                    _ec_stats[\"confirmed_entries\"] += 1\n"
        "                    _tc_day_total += 1\n"
        "                    _tc_day_side[_p[\"hedge_side\"]] += 1\n"
        "            # \u2500\u2500 SCALP_V3_CONFIRM_20260826 END \u2500\u2500\n"
        "            entry_candidates = []  # (entry_price, signal_symbol, ctx, c, signal)\n",
    ),
    # -- election: create pending instead of entering when enabled --
    (
        "                if hedge is None:\n"
        "                    continue  # no hedge available \u2192 skip (per spec)\n"
        "\n"
        "                hedge_entry = round(hedge[\"close\"], 2)\n",
        "                if hedge is None:\n"
        "                    continue  # no hedge available \u2192 skip (per spec)\n"
        "\n"
        "                # \u2500\u2500 SCALP_V3_CONFIRM_20260826 BEGIN: D4.1 \u2014 pending, not order.\n"
        "                # Signal SL/TP and the hedge choice FREEZE at T; the hedge's T\n"
        "                # low is the bar the next candle must not undercut.\n"
        "                if _conf_enabled:\n"
        "                    _hc_t = ctxs[hedge[\"symbol\"]].by_ts.get(ts)\n"
        "                    _pending = {\n"
        "                        \"t\": ts, \"signal_sym\": sig_sym,\n"
        "                        \"signal_side\": signal_side,\n"
        "                        \"entry_ref\": signal.entry_price,\n"
        "                        \"signal_sl\": signal.sl, \"signal_tp\": signal.tp,\n"
        "                        \"hedge_sym\": hedge[\"symbol\"],\n"
        "                        \"hedge_side\": hedge[\"side\"],\n"
        "                        \"hedge_low_t\": (_hc_t.low if _hc_t is not None\n"
        "                                        else float(hedge[\"close\"])),\n"
        "                        \"diag\": diag,\n"
        "                    }\n"
        "                    _ec_stats[\"pendings_created\"] += 1\n"
        "                    continue\n"
        "                # \u2500\u2500 SCALP_V3_CONFIRM_20260826 END (immediate entry below) \u2500\u2500\n"
        "\n"
        "                hedge_entry = round(hedge[\"close\"], 2)\n",
    ),
    # -- serial summary --
    (
        "    summary[\"summary\"][\"trade_count_limits\"] = {\n"
        "        \"max_trades_per_day\": _tc_max_day,\n"
        "        \"max_trades_per_side_per_day\": _tc_max_side,\n"
        "        **_tc_stats,\n"
        "    }\n",
        "    summary[\"summary\"][\"trade_count_limits\"] = {\n"
        "        \"max_trades_per_day\": _tc_max_day,\n"
        "        \"max_trades_per_side_per_day\": _tc_max_side,\n"
        "        **_tc_stats,\n"
        "    }\n"
        "    # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 surface D4 activity in the summary\n"
        "    summary[\"summary\"][\"entry_confirmation\"] = {\"enabled\": _conf_enabled,\n"
        "                                                **_ec_stats}\n",
    ),
    # -- parallel: worker returns the stats --
    (
        "    return {\"trades\": out[\"trades\"],\n"
        "            \"coverage\": out.get(\"coverage\") or {},\n"
        "            \"risk_limits\": _s.get(\"risk_limits\") or {},\n"
        "            \"trade_count_limits\": _s.get(\"trade_count_limits\") or {}}\n",
        "    return {\"trades\": out[\"trades\"],\n"
        "            \"coverage\": out.get(\"coverage\") or {},\n"
        "            \"risk_limits\": _s.get(\"risk_limits\") or {},\n"
        "            \"trade_count_limits\": _s.get(\"trade_count_limits\") or {},\n"
        "            \"entry_confirmation\": _s.get(\"entry_confirmation\") or {}}   # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n",
    ),
    # -- parallel: parent merge init --
    (
        "            _tc_m = {\"max_trades_per_day\": max(0, int(cfg.get(\"max_trades_per_day\") or 0)),\n"
        "                     \"max_trades_per_side_per_day\": max(0, int(cfg.get(\"max_trades_per_side_per_day\") or 0)),\n"
        "                     \"entries_blocked_day_cap\": 0, \"entries_blocked_side_cap\": 0}\n",
        "            _tc_m = {\"max_trades_per_day\": max(0, int(cfg.get(\"max_trades_per_day\") or 0)),\n"
        "                     \"max_trades_per_side_per_day\": max(0, int(cfg.get(\"max_trades_per_side_per_day\") or 0)),\n"
        "                     \"entries_blocked_day_cap\": 0, \"entries_blocked_side_cap\": 0}\n"
        "            # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 D4 stats merged across chunks\n"
        "            _ec_m = {\"pendings_created\": 0, \"confirmed_entries\": 0,\n"
        "                     \"rejected_new_low\": 0, \"rejected_signal_invalid\": 0,\n"
        "                     \"discarded_no_data\": 0, \"discarded_slot_busy\": 0}\n",
    ),
    # -- parallel: per-chunk accumulate --
    (
        "                        _tc_m[\"entries_blocked_day_cap\"] += int(_t.get(\"entries_blocked_day_cap\", 0) or 0)\n"
        "                        _tc_m[\"entries_blocked_side_cap\"] += int(_t.get(\"entries_blocked_side_cap\", 0) or 0)\n",
        "                        _tc_m[\"entries_blocked_day_cap\"] += int(_t.get(\"entries_blocked_day_cap\", 0) or 0)\n"
        "                        _tc_m[\"entries_blocked_side_cap\"] += int(_t.get(\"entries_blocked_side_cap\", 0) or 0)\n"
        "                        _e = _out.get(\"entry_confirmation\") or {}   # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n"
        "                        for _k in _ec_m:\n"
        "                            _ec_m[_k] += int(_e.get(_k, 0) or 0)\n",
    ),
    # -- parallel: attach to merged summary --
    (
        "            summary[\"summary\"][\"trade_count_limits\"] = _tc_m\n",
        "            summary[\"summary\"][\"trade_count_limits\"] = _tc_m\n"
        "            # \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n"
        "            _pec = cfg.get(\"entry_confirmation\") or {}\n"
        "            summary[\"summary\"][\"entry_confirmation\"] = {\n"
        "                \"enabled\": (bool(_pec.get(\"enabled\"))\n"
        "                            if isinstance(_pec, dict) else bool(_pec)),\n"
        "                **_ec_m}\n",
    ),
]

BACKTEST_JSX_EDITS = [
    (
        "  const [v3EmaMinSlope, setV3EmaMinSlope] = useState(saved.v3EmaMinSlope ?? 1);\n",
        "  const [v3EmaMinSlope, setV3EmaMinSlope] = useState(saved.v3EmaMinSlope ?? 1);\n"
        "  const [v3Confirm, setV3Confirm] = useState(saved.v3Confirm ?? false);   // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n",
        1,
    ),
    (
        "      if (v3EmaGate) cfg.ema_gate = { enabled: true, period: Number(v3EmaPeriod) || 89, slope_lookback: Number(v3EmaLookback) || 30, min_slope_pts: Number(v3EmaMinSlope) || 0 };\n",
        "      if (v3EmaGate) cfg.ema_gate = { enabled: true, period: Number(v3EmaPeriod) || 89, slope_lookback: Number(v3EmaLookback) || 30, min_slope_pts: Number(v3EmaMinSlope) || 0 };\n"
        "      // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 omit-when-off: baselines stay byte-identical.\n"
        "      if (v3Confirm) cfg.entry_confirmation = { enabled: true };\n",
        1,
    ),
    (
        "                  <Field label=\"Min Slope Pts\"><input type=\"number\" min=\"0\" step=\"0.5\" style={inputStyle} value={v3EmaMinSlope} onChange={(e) => setV3EmaMinSlope(e.target.value)} /></Field>\n"
        "                </>\n"
        "              )}\n",
        "                  <Field label=\"Min Slope Pts\"><input type=\"number\" min=\"0\" step=\"0.5\" style={inputStyle} value={v3EmaMinSlope} onChange={(e) => setV3EmaMinSlope(e.target.value)} /></Field>\n"
        "                </>\n"
        "              )}\n"
        "              {/* \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 D4: signal candle creates a\n"
        "                  pending; entry next candle only if the hedge made no new low\n"
        "                  and the signal didn't cross its frozen SL. */}\n"
        "              <Field label=\"Entry Confirm\">\n"
        "                <select style={inputStyle} value={v3Confirm ? \"1\" : \"0\"} onChange={(e) => setV3Confirm(e.target.value === \"1\")}>\n"
        "                  <option value=\"0\">Off</option>\n"
        "                  <option value=\"1\">On (1 candle)</option>\n"
        "                </select>\n"
        "              </Field>\n",
        1,
    ),
    (
        "      v3EmaGate, v3EmaPeriod, v3EmaLookback, v3EmaMinSlope,   // \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500\n",
        "      v3EmaGate, v3EmaPeriod, v3EmaLookback, v3EmaMinSlope,   // \u2500\u2500 SCALP_V3_EMA_GATE_20260826 \u2500\u2500\n"
        "      v3Confirm,   // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n",
        3,
    ),
    # run-detail chip (RUN_PARAMS_DISPLAY tripwire)
    (
        "  if (cfg.require_fresh_entry) add(\"Fresh entry\", \"on\");   // \u2500\u2500 SCALP_V1_FRESH_ENTRY_20260824 \u2500\u2500 RUN_PARAMS_DISPLAY tripwire\n",
        "  if (cfg.require_fresh_entry) add(\"Fresh entry\", \"on\");   // \u2500\u2500 SCALP_V1_FRESH_ENTRY_20260824 \u2500\u2500 RUN_PARAMS_DISPLAY tripwire\n"
        "  if (cfg.entry_confirmation?.enabled) add(\"Entry confirm\", \"1 candle\");   // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n",
        1,
    ),
]

QUEUE_JSX_EDITS = [
    (
        "  if (cfg.require_fresh_entry) p.push(\"fresh\");   // \u2500\u2500 SCALP_V1_FRESH_ENTRY_20260824 \u2500\u2500\n",
        "  if (cfg.require_fresh_entry) p.push(\"fresh\");   // \u2500\u2500 SCALP_V1_FRESH_ENTRY_20260824 \u2500\u2500\n"
        "  if (cfg.entry_confirmation?.enabled) p.push(\"confirm\");   // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n",
        1,
    ),
]

RUNCOMP_JSX_EDITS = [
    (
        "  { key: \"tp_mult\",          label: \"TP multiplier\",  get: (r) => (Number(r.config?.tp_multiplier) > 0 && Number(r.config?.tp_multiplier) !== 1 ? `${r.config.tp_multiplier}\u00d7` : null) },\n",
        "  { key: \"tp_mult\",          label: \"TP multiplier\",  get: (r) => (Number(r.config?.tp_multiplier) > 0 && Number(r.config?.tp_multiplier) !== 1 ? `${r.config.tp_multiplier}\u00d7` : null) },\n"
        "  { key: \"v3_confirm\",       label: \"Entry confirm\",  get: (r) => (r.config?.entry_confirmation?.enabled ? \"1 candle\" : null) },   // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500\n",
        1,
    ),
]

SWEEP_JSX_EDITS = [
    (
        "  { key: \"hedge_sl\", label: \"Hedge SL pts\", strategies: [V3],\n"
        "    hint: \"10, 15, 20, 25\", parse: _num,\n"
        "    apply: (c, v) => { c.hedge_sl_points = v; }, fmt: (v) => `hSL ${v}` },\n",
        "  { key: \"hedge_sl\", label: \"Hedge SL pts\", strategies: [V3],\n"
        "    hint: \"10, 15, 20, 25\", parse: _num,\n"
        "    apply: (c, v) => { c.hedge_sl_points = v; }, fmt: (v) => `hSL ${v}` },\n"
        "  // \u2500\u2500 SCALP_V3_CONFIRM_20260826 \u2500\u2500 D4 on/off axis (0 = off, 1 = on).\n"
        "  { key: \"v3_confirm\", label: \"Entry confirm (0/1)\", strategies: [V3],\n"
        "    hint: \"0, 1\", parse: _num,\n"
        "    apply: (c, v) => { if (v > 0) c.entry_confirmation = { enabled: true }; },\n"
        "    fmt: (v) => (v > 0 ? \"confirm\" : \"no confirm\") },\n",
        1,
    ),
]


def apply_edits(path, edits):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if FENCE in text:
        print(f"[SKIP] fence already present: {os.path.relpath(path, REPO)}")
        return None
    for i, item in enumerate(edits, 1):
        old, new = item[0], item[1]
        want = item[2] if len(item) > 2 else 1
        n = text.count(old)
        if n != want:
            fail(f"anchor #{i} matched {n}x (need exactly {want}) in "
                 f"{os.path.relpath(path, REPO)} — prerequisite patches applied?")
        text = text.replace(old, new)
    return text


def sim_pending_state_machine():
    """Standalone replica of the D4 state machine. Seven scenarios."""
    EOD = 15 * 3600 + 15 * 60

    def run(events):
        """events: list of (ts, kind, payload). kinds:
        'elect'  payload = {sig_sl, hedge_low_t}          (slot assumed free)
        'candle' payload = {hedge_low, sig_high, has_hedge, slot_busy}
        Returns (log, stats)."""
        pending = None
        stats = {k: 0 for k in EC_KEYS}
        log = []
        for ts, kind, pl in events:
            if kind == "candle" and pending is not None and ts >= pending["t"] + 60:
                p, pending = pending, None
                if ts > p["t"] + 60 or not pl.get("has_hedge", True):
                    stats["discarded_no_data"] += 1; log.append((ts, "discard"))
                elif ts + 60 >= EOD:
                    stats["discarded_no_data"] += 1; log.append((ts, "eod_discard"))
                elif pl.get("sig_high", -1) >= p["sig_sl"]:
                    stats["rejected_signal_invalid"] += 1; log.append((ts, "sig_invalid"))
                elif pl["hedge_low"] < p["hedge_low_t"]:
                    stats["rejected_new_low"] += 1; log.append((ts, "new_low"))
                elif pl.get("slot_busy", False):
                    stats["discarded_slot_busy"] += 1; log.append((ts, "slot_busy"))
                else:
                    stats["confirmed_entries"] += 1; log.append((ts, "ENTER"))
            if kind == "elect" and pending is None:
                pending = {"t": ts, "sig_sl": pl["sig_sl"],
                           "hedge_low_t": pl["hedge_low_t"]}
                stats["pendings_created"] += 1
        return log, stats

    T = 10 * 3600 + 42 * 60
    # 1) walkthrough left branch: confirm + enter
    log, st = run([(T, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                   (T + 60, "candle", {"hedge_low": 164.60, "sig_high": 180.0})])
    assert log == [(T + 60, "ENTER")] and st["confirmed_entries"] == 1
    # 2) walkthrough right branch: new low -> reject
    log, st = run([(T, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                   (T + 60, "candle", {"hedge_low": 163.40, "sig_high": 180.0})])
    assert log == [(T + 60, "new_low")]
    # 3) equality passes (strictly-below rejects)
    log, _ = run([(T, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                  (T + 60, "candle", {"hedge_low": 164.10, "sig_high": 180.0})])
    assert log == [(T + 60, "ENTER")]
    # 4) D4.2b: signal blew its frozen SL -> reject even though hedge held
    log, _ = run([(T, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                  (T + 60, "candle", {"hedge_low": 165.0, "sig_high": 187.0})])
    assert log == [(T + 60, "sig_invalid")]
    # 5) stale gap: T+1 minute never replayed -> discard at T+2
    log, _ = run([(T, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                  (T + 120, "candle", {"hedge_low": 170.0, "sig_high": 100.0})])
    assert log == [(T + 120, "discard")]
    # 6) EOD: confirm stamp would land >= 15:15 -> discard
    T2 = 15 * 3600 + 13 * 60
    log, _ = run([(T2, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                  (T2 + 60, "candle", {"hedge_low": 170.0, "sig_high": 100.0})])
    assert log == [(T2 + 60, "eod_discard")]
    # 7) resolve-before-create: reject at T+1, NEW election same minute lands,
    #    then confirms at T+2 -> exactly one live pending at any time
    log, st = run([(T, "elect", {"sig_sl": 186.0, "hedge_low_t": 164.10}),
                   (T + 60, "candle", {"hedge_low": 163.0, "sig_high": 100.0}),
                   (T + 60, "elect", {"sig_sl": 190.0, "hedge_low_t": 160.0}),
                   (T + 120, "candle", {"hedge_low": 161.0, "sig_high": 100.0})])
    assert log == [(T + 60, "new_low"), (T + 120, "ENTER")]
    assert st["pendings_created"] == 2 and st["confirmed_entries"] == 1
    print("[SIM] D4 pending state machine: 7/7 scenarios OK "
          "(confirm/reject/equality/2b/stale/eod/ordering)")


def main():
    sim_pending_state_machine()

    staged = []

    trees = [t for t in BACKEND_TREES if os.path.isdir(os.path.join(REPO, t, "app"))]
    if not trees:
        fail("no backend tree found — run from the scalp-app repo root")
    for tree in trees:
        path = os.path.join(REPO, tree, RUNNER)
        if not os.path.isfile(path):
            fail(f"missing file: {path}")
        text = apply_edits(path, RUNNER_EDITS)
        if text is None:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(text); tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"staged compile failed for {tree}/{RUNNER}:\n{e}")
        finally:
            os.unlink(tmp)
        staged.append((path, text))
        print(f"[OK] staged {tree}/{RUNNER} (compiles)")

    def frontend(relparts, edits):
        main_p = os.path.join(REPO, "frontend", "src", *relparts)
        candidates = [main_p] + sorted(set(
            glob.glob(os.path.join(REPO, "desktop", "**", relparts[-1]),
                      recursive=True)) - {main_p})
        found = [p for p in candidates if os.path.isfile(p)]
        if not found:
            fail(f"{relparts[-1]} not found")
        for p in found:
            t = apply_edits(p, edits)
            if t is not None:
                staged.append((p, t))
                print(f"[OK] staged {os.path.relpath(p, REPO)}")

    frontend(["pages", "Backtest.jsx"], BACKTEST_JSX_EDITS)
    frontend(["pages", "backtest", "BacktestQueue.jsx"], QUEUE_JSX_EDITS)
    frontend(["pages", "backtest", "RunComparison.jsx"], RUNCOMP_JSX_EDITS)
    frontend(["pages", "backtest", "SweepBuilder.jsx"], SWEEP_JSX_EDITS)

    if not staged:
        print("\n[DONE] nothing to do — all fences already present.")
        return

    for path, text in staged:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WROTE] {os.path.relpath(path, REPO)}")

    for tree in trees:
        with open(os.path.join(REPO, tree, RUNNER), "r", encoding="utf-8") as f:
            t = f.read()
        assert t.count("_pending") >= 6
        assert "\"entry_confirmation\"" in t
        assert t.index("resolve the pending") < t.index("D4.1 \u2014 pending, not order")
    print("\n[PASS] all structural asserts hold.")
    print("Syntax checks + rebuild:")
    print("  npx --no-install esbuild frontend/src/pages/Backtest.jsx --loader:.jsx=jsx --outfile=/dev/null")
    print("  (repeat for BacktestQueue.jsx, RunComparison.jsx, SweepBuilder.jsx)")
    print("ACCEPTANCE: confirm OFF at gate+3.5x -> byte-identical to c5504442;")
    print("then ON at the same config, and check entry_confirmation stats in the summary.")


if __name__ == "__main__":
    main()
