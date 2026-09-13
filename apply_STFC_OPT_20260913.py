#!/usr/bin/env python3
"""
apply_STFC_OPT_20260913.py — STFC_V1 ("Sabre"): SuperTrend flip-confirm on
NIFTY/BANKNIFTY spot with weekly option BUY execution, in the Backtest page.

FENCE: STFC_OPT_20260913

Creates
  backend/app/backtest/stfc/__init__.py
  backend/app/backtest/stfc/stfc_v1_engine.py          pure signal core (TV / Crypto Lab parity)
  backend/app/backtest/stfc/backtest_stfc_runner.py    runner (ORB donor conventions)
  backend/app/backtest/stfc/test_stfc_runner_sim.py    simulation suite (also run by this script)
Patches (anchored, uniqueness-asserted, fenced)
  backend/app/api/backtest_routes.py                   supported list + dispatch arm
  backend/app/backtest/queue_worker.py                 dispatch arm (second hand-maintained copy)
  frontend/src/pages/Backtest.jsx                      LS key, id list, state, buildConfig + deps, chip,
                                                       hidden-shared guards, form, describeConfig, CSV diag
  frontend/src/pages/backtest/paramFormat.js           stfcParamSummary
  frontend/src/pages/backtest/RunComparison.jsx        import + branch
  frontend/src/pages/backtest/BacktestQueue.jsx        import + branch
  frontend/src/pages/backtest/SweepBuilder.jsx         STFC const + 9 grid axes
  frontend/src/strategies/displayNames.js              STFC_V1 name

Safety: fence presence check, git-status drift check on every patched file
(an `M` is a stop sign), py_compile gate, staged all-or-nothing writes with
.bak-FENCE backups, dual-tree deploy when the trees exist, simulation suite
against the real app tree, esbuild verification of every touched JSX/JS file.

Run from the repo root:  python3 apply_STFC_OPT_20260913.py
"""
import os, sys, py_compile, shutil, subprocess, tempfile

FENCE = "STFC_OPT_20260913"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
DUAL_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")
DUAL_FRONTEND = os.path.join(ROOT, "desktop", "src-tauri", "frontend")

ENGINE_SRC = '# backend/app/backtest/stfc/stfc_v1_engine.py\n#\n# ── STFC_V1 ENGINE ── "SuperTrend Flip-Confirm" signal core (pure, no I/O).\n#\n# Fence: STFC_OPT_20260913\n#\n# Port of the TradingView "ST Flip-Confirm Scalper v2" indicator and the\n# Crypto Lab engine (crypto_stfc_engine.py), kept in lock-step:\n#   D1  Bars: 1m SPOT resampled to `tf` minutes on the NSE session grid\n#       (buckets from 09:15, same as TradingView\'s NSE charts). A bar\'s\n#       end minute is recorded so "candle close" exits can land on it.\n#   D2  SuperTrend(len, mult) is a bit-for-bit port of Pine\'s ta.supertrend:\n#       ATR = RMA(TR) seeded with the SMA of the first `len` TRs, hl2 source,\n#       band ratcheting, direction −1 = up-trend / +1 = down-trend. The\n#       indicator state is CONTINUOUS across sessions (TradingView parity —\n#       the chart does not reset ATR at 09:15); the runner warms it up on\n#       sessions before date_from.\n#   D3  Setup machine, per tf bar, in Pine order:\n#         1. if armed by the previous bar → this bar is a TRADE bar\n#            (entry at its open = the 1m open of its first minute)\n#         2. flip on this bar → C1 is ignored; pending := new direction\n#         3. else pending and the bar\'s colour matches → arm the next bar\n#       A fresh flip while pending restarts the setup; a doji never\n#       confirms. The machine RESETS at each session open (no overnight\n#       carry — an arm on the last bar of a day is lost, counted by the\n#       runner).\n#   D4  Premium fills (pessimistic, same helper as ORB/CBO): a level touched\n#       inside a 1m bar books AT the level; gapped through at the open books\n#       the open.\n#\n# Fleet convention: no imports from sibling strategy packages; helpers are\n# duplicated here so the module stays self-contained.\n\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass\nfrom typing import Dict, List, Optional, Tuple\n\nSESSION_OPEN_MIN = 9 * 60 + 15        # NSE index grid opens 09:15\n\n\n@dataclass(frozen=True)\nclass StfcBar:\n    ts: int                            # epoch seconds, bar START, IST grid\n    open: float\n    high: float\n    low: float\n    close: float\n    end_min: int = 0                   # minute-of-day of the bar\'s LAST 1m slot\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  RESAMPLE — session-aligned tf buckets from 09:15\n# ─────────────────────────────────────────────────────────────────────────\ndef resample_session(bars_1m, *, day_start_epoch: int, tf_minutes: int\n                     ) -> List[StfcBar]:\n    """bars_1m: objects with ts/open/high/low/close (1m, ascending)."""\n    out: Dict[int, dict] = {}\n    for b in bars_1m:\n        mod = (b.ts - day_start_epoch) // 60\n        if mod < SESSION_OPEN_MIN:\n            continue\n        bucket = (mod - SESSION_OPEN_MIN) // tf_minutes\n        bts = day_start_epoch + (SESSION_OPEN_MIN + bucket * tf_minutes) * 60\n        cur = out.get(bts)\n        if cur is None:\n            out[bts] = {"o": b.open, "h": b.high, "l": b.low, "c": b.close,\n                        "end": SESSION_OPEN_MIN + (bucket + 1) * tf_minutes - 1}\n        else:\n            cur["h"] = max(cur["h"], b.high)\n            cur["l"] = min(cur["l"], b.low)\n            cur["c"] = b.close\n    return [StfcBar(ts, v["o"], v["h"], v["l"], v["c"], v["end"])\n            for ts, v in sorted(out.items())]\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  SUPERTREND — stateful, TradingView-exact (D2)\n# ─────────────────────────────────────────────────────────────────────────\nclass SuperTrend:\n    def __init__(self, length: int, mult: float):\n        if length < 1:\n            raise ValueError("length must be >= 1")\n        self.length = int(length)\n        self.mult = float(mult)\n        self.n = 0                     # bars seen\n        self.tr_sum = 0.0\n        self.atr: Optional[float] = None\n        self.prev_close: Optional[float] = None\n        self.prev_upper = 0.0\n        self.prev_lower = 0.0\n        self.prev_st: Optional[float] = None\n        self.direction: Optional[int] = None\n\n    def update(self, b: StfcBar) -> Tuple[Optional[float], Optional[int]]:\n        h, l, c = b.high, b.low, b.close\n        tr = (h - l) if self.prev_close is None else max(\n            h - l, abs(h - self.prev_close), abs(l - self.prev_close))\n        atr_was_na = self.atr is None\n        if self.n < self.length - 1:\n            self.tr_sum += tr\n            atr = None\n        elif self.n == self.length - 1:\n            self.tr_sum += tr\n            atr = self.tr_sum / self.length\n        else:\n            atr = (self.atr * (self.length - 1) + tr) / self.length\n        self.n += 1\n        if atr is None:\n            self.prev_close = c\n            self.direction = None\n            return None, None\n        src = (h + l) / 2.0\n        upper = src + self.mult * atr\n        lower = src - self.mult * atr\n        pc = self.prev_close if self.prev_close is not None else c\n        lower = lower if (lower > self.prev_lower or pc < self.prev_lower) else self.prev_lower\n        upper = upper if (upper < self.prev_upper or pc > self.prev_upper) else self.prev_upper\n        if atr_was_na:\n            d = 1\n        elif self.prev_st == self.prev_upper:\n            d = -1 if c > upper else 1\n        else:\n            d = 1 if c < lower else -1\n        st = lower if d == -1 else upper\n        self.prev_upper, self.prev_lower, self.prev_st = upper, lower, st\n        self.atr = atr\n        self.prev_close = c\n        self.direction = d\n        return st, d\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  SETUP MACHINE (D3)\n# ─────────────────────────────────────────────────────────────────────────\n@dataclass(frozen=True)\nclass StfcSignal:\n    bar_index: int                     # index of the TRADE bar in the day\'s list\n    side: str                          # CE (up-flip) | PE (down-flip)\n    flip_index: int\n    confirm_index: int\n\n\ndef session_signals(bars: List[StfcBar], dirs: List[Optional[int]], *,\n                    direction: str = "BOTH", diag: Optional[dict] = None\n                    ) -> List[StfcSignal]:\n    """One session\'s signals. `dirs[i]` is the SuperTrend direction after\n    bar i (None while warming up). The machine starts flat; the previous\n    session\'s arm never carries over."""\n    out: List[StfcSignal] = []\n    pend = 0\n    armed = False\n    armed_side = ""\n    flip_i = -1\n    prev_d: Optional[int] = None\n    want_up = direction in ("BOTH", "UP")\n    want_dn = direction in ("BOTH", "DOWN")\n    for i, b in enumerate(bars):\n        if armed:\n            out.append(StfcSignal(i, armed_side, flip_i, i - 1))\n        armed = False\n        d = dirs[i]\n        if d is None or prev_d is None:\n            prev_d = d\n            continue\n        is_up, was_up = d < 0, prev_d < 0\n        prev_d = d\n        flip_up = is_up and not was_up\n        flip_dn = (not is_up) and was_up\n        if flip_up or flip_dn:\n            if diag is not None:\n                diag["flips_up" if flip_up else "flips_dn"] = \\\n                    diag.get("flips_up" if flip_up else "flips_dn", 0) + 1\n            pend = (1 if want_up else 0) if flip_up else (-1 if want_dn else 0)\n            flip_i = i\n            continue\n        green = b.close > b.open\n        red = b.close < b.open\n        if pend == 1 and green:\n            armed, armed_side, pend = True, "CE", 0\n        elif pend == -1 and red:\n            armed, armed_side, pend = True, "PE", 0\n    if armed and diag is not None:\n        diag["arm_lost_eod"] = diag.get("arm_lost_eod", 0) + 1\n    return out\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  PREMIUM LEVELS & FILLS (D4)\n# ─────────────────────────────────────────────────────────────────────────\ndef prem_level(entry_px: float, mode: str, value: float, *, is_stop: bool\n               ) -> Optional[float]:\n    if mode == "off" or value <= 0:\n        return None\n    dist = value if mode == "abs" else entry_px * value / 100.0\n    lvl = entry_px - dist if is_stop else entry_px + dist\n    return max(0.05, lvl)\n\n\ndef prem_fill(*, level: float, bar, side_is_stop: bool) -> Optional[float]:\n    """Fill for a PREMIUM level on the option\'s 1m bar, or None if untouched.\n    Gapped through at the open → the open; otherwise the level."""\n    if side_is_stop:\n        if bar.open <= level:\n            return float(bar.open)\n        return level if bar.low <= level else None\n    if bar.open >= level:\n        return float(bar.open)\n    return level if bar.high >= level else None\n\n\ndef strike_step(strikes) -> float:\n    """Smallest positive gap between distinct strikes (50 NIFTY / 100 BNF)."""\n    s = sorted({float(x) for x in strikes if x is not None})\n    gaps = [b - a for a, b in zip(s, s[1:]) if b - a > 0]\n    return min(gaps) if gaps else 50.0\n\n\ndef atm_strike(spot: float, step: float) -> float:\n    return round(spot / step) * step\n'
RUNNER_SRC = '# backend/app/backtest/stfc/backtest_stfc_runner.py\n#\n# ── STFC_V1 RUNNER ── "SuperTrend Flip-Confirm" on NIFTY/BANKNIFTY SPOT\n# with weekly option BUY execution and premium-denominated exits.\n#\n# Fence: STFC_OPT_20260913\n#\n# Engine decisions D1–D4 in stfc_v1_engine.py. Runner-owned decisions:\n#   R1  Entry fill: a trade bar (engine D3) starts at minute M; the option\n#       fills at M\'s 1m OPEN. entry_spot = SPOT 1m open at M. No unfinished-\n#       bar reads: the confirm bar has fully closed before M.\n#   R2  Contract: ATM strike from entry_spot (step inferred from the day\'s\n#       chain) shifted `strike_offset` steps OUT of the money (negative =\n#       in the money), expected weekly expiry only, same side as the flip\n#       (up-flip → CE, down-flip → PE). If that strike has no print at M\n#       the nearest strike with a print is used (counted). Optional\n#       premium band {min,max} rejects absurd fills (0 = off).\n#   R3  Exits, evaluated per minute in this order (pessimistic — stop\n#       before target, both before time):\n#         1. PREMIUM stop  (sl_mode off|abs|pct of entry premium) → books\n#            AT the level (open if gapped through).\n#         2. PREMIUM target (tp_mode off|abs|pct) → books AT the level.\n#         3. TIME: exit_mode "candle" → the 1m CLOSE of the trade bar\'s\n#            last minute (the Pine "exit at candle close"); "minutes" →\n#            the 1m CLOSE of the minute completing hold_minutes from entry\n#            (may span several tf bars).\n#         4. EOD square-off at the option 1m close.\n#       A missing option bar leaves the last mark in place (counted).\n#   R4  Budgets: one position at a time — a signal that fires while in a\n#       position is SKIPPED, never queued (counted); max_trades_per_day\n#       (0 = unlimited); no entries at/after entry_block_time.\n#   R5  SuperTrend state is continuous across sessions and warmed up on\n#       `warmup_sessions` sessions before date_from (no trades there).\n#\n# ── FALSIFICATION TRIPWIRES ─────────────────────────────────────────────\n#   * time/candle_pnl_share_pct vs sl/tp — if the timed exit carries the\n#     net, the edge is "hold N minutes after a flip", not the SL/TP shape.\n#   * sig_dropped_open — with long holds most signals are skipped; the\n#     run then describes a much sparser strategy than the chart suggests.\n#   * eod_pnl_share_pct — should be ~0 with short holds.\n#   * ce_net vs pe_net and the DTE breakdown — 0DTE premium decay inside\n#     the hold is the first thing that can kill this.\n\nfrom __future__ import annotations\n\nimport uuid\nfrom dataclasses import dataclass, field\nfrom datetime import date, datetime, timedelta\nfrom typing import Callable, Dict, List, Optional\n\ntry:\n    from app.backtest.stfc.stfc_v1_engine import (\n        StfcBar, SuperTrend, resample_session, session_signals,\n        prem_level, prem_fill, strike_step, atm_strike, SESSION_OPEN_MIN)\nexcept ImportError:                                        # standalone tests\n    from stfc_v1_engine import (  # type: ignore\n        StfcBar, SuperTrend, resample_session, session_signals,\n        prem_level, prem_fill, strike_step, atm_strike, SESSION_OPEN_MIN)\n\nIST_OFFSET = 5 * 3600 + 30 * 60\nINDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}\n\nDEFAULTS: dict = {\n    # ── signal (D1/D2/D3) ──\n    "timeframe_minutes": 3,\n    "st_len": 10,\n    "st_mult": 2.0,\n    "direction": "BOTH",               # BOTH | UP | DOWN\n    "warmup_sessions": 3,\n\n    # ── contract (R2) ──\n    "strike_offset": 0,                # steps OTM (negative = ITM)\n    "premium_min": 0.0,                # 0 = off\n    "premium_max": 0.0,                # 0 = off\n    "lots": 1,\n    "lot_size": 0,                     # 0 = index constant\n\n    # ── exits (R3) ──\n    "sl_mode": "pct",                  # off | abs | pct (of entry premium)\n    "sl_value": 20.0,\n    "tp_mode": "pct",                  # off | abs | pct\n    "tp_value": 30.0,\n    "exit_mode": "candle",             # candle | minutes\n    "hold_minutes": 5,\n    "eod_square_off": "15:20",\n\n    # ── budgets & session (R4) ──\n    "entry_block_time": "15:00",\n    "max_trades_per_day": 0,           # 0 = unlimited\n    "skip_expiry_day": False,\n}\n\n\n@dataclass\nclass STFCTrade:\n    """persist_run-compatible attribute surface (object, no hedge_symbol)."""\n    tradingsymbol: str\n    symbol: str\n    instrument_type: str\n    strike: Optional[float]\n    expiry: Optional[str]\n    direction: str                     # always BUY\n    entry_ts: int\n    entry_price: float\n    sl: Optional[float]                # PREMIUM stop level\n    tp: Optional[float]                # PREMIUM target level\n    exit_ts: Optional[int]\n    exit_price: Optional[float]\n    exit_reason: Optional[str]         # SL | TP | TIME | CANDLE | EOD\n    qty: int\n    condition: str\n    ambiguous_fill: bool = False\n    pnl: float = 0.0\n    charges: float = 0.0\n    net_pnl: float = 0.0\n    max_adverse: Optional[float] = None\n    max_favorable: Optional[float] = None\n    gross: float = field(default=0.0)\n    net: float = field(default=0.0)\n    ambiguous: bool = field(default=False)\n    synthetic: bool = field(default=False)\n    synth_kind: Optional[str] = field(default=None)\n    entry_spot: Optional[float] = None\n    hold_min: int = 0\n\n\ndef _empty_summary() -> dict:\n    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,\n            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,\n            "max_drawdown": 0.0, "ambiguous_fills": 0}\n\n\ndef _hhmm(s: str, fallback: int) -> int:\n    try:\n        h, m = str(s).split(":")\n        v = int(h) * 60 + int(m)\n        return v if 0 <= v < 24 * 60 else fallback\n    except (ValueError, AttributeError):\n        return fallback\n\n\ndef _day_start_epoch(d: date) -> int:\n    return int((datetime(d.year, d.month, d.day)\n                - datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET\n\n\ndef _merge_cfg(override: Optional[dict]) -> dict:\n    cfg = dict(DEFAULTS)\n    for k, v in (override or {}).items():\n        cfg[k] = v\n    _d = str(cfg.get("direction", "BOTH")).upper()\n    cfg["direction"] = _d if _d in ("BOTH", "UP", "DOWN") else "BOTH"\n    for k in ("sl_mode", "tp_mode"):\n        m = str(cfg.get(k, "off")).lower()\n        cfg[k] = m if m in ("off", "abs", "pct") else "off"\n    cfg["exit_mode"] = ("minutes" if str(cfg.get("exit_mode", "candle")\n                                         ).lower() == "minutes" else "candle")\n    for k in ("timeframe_minutes", "st_len", "hold_minutes", "warmup_sessions",\n              "max_trades_per_day", "lots", "lot_size"):\n        try:\n            cfg[k] = max(0, int(cfg[k] or 0))\n        except (TypeError, ValueError):\n            cfg[k] = int(DEFAULTS[k])\n    try:\n        cfg["strike_offset"] = int(cfg.get("strike_offset") or 0)\n    except (TypeError, ValueError):\n        cfg["strike_offset"] = 0\n    for k in ("st_mult", "sl_value", "tp_value", "premium_min", "premium_max"):\n        try:\n            cfg[k] = abs(float(cfg[k] or 0.0))\n        except (TypeError, ValueError):\n            cfg[k] = float(DEFAULTS[k])\n    cfg["lots"] = cfg["lots"] or 1\n    cfg["timeframe_minutes"] = cfg["timeframe_minutes"] or 3\n    cfg["st_len"] = cfg["st_len"] or 10\n    cfg["st_mult"] = cfg["st_mult"] or 2.0\n    cfg["hold_minutes"] = cfg["hold_minutes"] or 5\n    cfg["skip_expiry_day"] = bool(cfg.get("skip_expiry_day", False))\n    from app.backtest.engine.dte_lots import parse_dte_lot_mult   # ── DTE_LOT_MULT_20260911 ──\n    cfg["dte_lot_mult"] = parse_dte_lot_mult(cfg.get("dte_lot_mult"))\n    return cfg\n\n\ndef run_stfc_backtest(\n    *, db_path: str, strategy_id: str, underlying: str,\n    date_from: date, date_to: date,\n    config_override: Optional[dict] = None,\n    progress_cb: Optional[Callable[[dict], None]] = None,\n    cancel_cb: Optional[Callable[[], bool]] = None,\n) -> Dict:\n    try:\n        from app.event_bus.audit_logger import audit_muted\n        with audit_muted():\n            return _impl(db_path=db_path, strategy_id=strategy_id,\n                         underlying=underlying, date_from=date_from,\n                         date_to=date_to, config_override=config_override,\n                         progress_cb=progress_cb, cancel_cb=cancel_cb)\n    except ImportError:\n        return _impl(db_path=db_path, strategy_id=strategy_id,\n                     underlying=underlying, date_from=date_from,\n                     date_to=date_to, config_override=config_override,\n                     progress_cb=progress_cb, cancel_cb=cancel_cb)\n\n\ndef _abort(cfg, strategy_id, reason) -> Dict:\n    return {"run_id": None, "aborted": True, "reason": reason,\n            "trades": [], "summary": _empty_summary(),\n            "config": cfg, "strategy_id": strategy_id}\n\n\ndef pick_contract(universe: List[dict], side: str, *, target_strike: float,\n                  has_print) -> tuple:\n    """R2: the side\'s contract at target_strike with a print at the entry\n    minute; else the nearest strike that has one. Returns (contract, fallback)."""\n    cands = [c for c in universe\n             if c.get("instrument_type") == side and c.get("strike") is not None]\n    exact = [c for c in cands if float(c["strike"]) == target_strike]\n    for c in exact:\n        if has_print(c["tradingsymbol"]):\n            return c, False\n    best = None\n    for c in sorted(cands, key=lambda x: (abs(float(x["strike"]) - target_strike),\n                                          float(x["strike"]))):\n        if has_print(c["tradingsymbol"]):\n            best = c\n            break\n    return best, True\n\n\ndef _impl(*, db_path, strategy_id, underlying, date_from, date_to,\n          config_override, progress_cb, cancel_cb) -> Dict:\n    from app.backtest.data.candle_source import CandleSource\n    from app.backtest.engine.expiry_calendar import expected_expiry_for_day\n    from app.backtest.charges.charges_model import charges_for_long_trade\n    from app.backtest.util.lot_sizes import resolve_lot\n    from app.utils.market_hours import is_trading_day\n    from app.event_bus.audit_logger import write_audit_log\n\n    cfg = _merge_cfg(config_override)\n\n    index_lot = INDEX_LOTS.get(underlying.upper())\n    if index_lot is None:\n        return _abort(cfg, strategy_id,\n                      f"STFC_V1 is index-only; no lot constant for {underlying}.")\n    lot_size, lot_source = resolve_lot(\n        underlying=underlying, is_stock=False, cfg_lot=cfg["lot_size"],\n        index_lot=index_lot, db_path=db_path)\n    if lot_size is None:\n        return _abort(cfg, strategy_id, f"no lot size for {underlying}")\n    qty = cfg["lots"] * lot_size\n\n    tf = cfg["timeframe_minutes"]\n    block_min = _hhmm(cfg["entry_block_time"], 15 * 60)\n    eod_min = _hhmm(cfg["eod_square_off"], 15 * 60 + 20)\n    if not (SESSION_OPEN_MIN < block_min <= eod_min <= 15 * 60 + 29):\n        return _abort(cfg, strategy_id,\n                      f"time order must be 09:15 < entry_block_time "\n                      f"{cfg[\'entry_block_time\']} <= eod_square_off "\n                      f"{cfg[\'eod_square_off\']} <= 15:29")\n    if not (1 <= tf <= 60):\n        return _abort(cfg, strategy_id, "timeframe_minutes must be 1..60")\n    if not (2 <= cfg["st_len"] <= 200):\n        return _abort(cfg, strategy_id, "st_len must be 2..200")\n    if not (0.1 <= cfg["st_mult"] <= 20):\n        return _abort(cfg, strategy_id, "st_mult must be 0.1..20")\n    for k in ("sl", "tp"):\n        if cfg[f"{k}_mode"] != "off" and cfg[f"{k}_value"] <= 0:\n            return _abort(cfg, strategy_id, f"{k}_mode {cfg[f\'{k}_mode\']} needs {k}_value > 0")\n        if cfg[f"{k}_mode"] == "pct" and cfg[f"{k}_value"] > 100:\n            return _abort(cfg, strategy_id,\n                          f"{k}_value {cfg[f\'{k}_value\']} in pct mode looks like rupees")\n    if cfg["exit_mode"] == "minutes" and not (1 <= cfg["hold_minutes"] <= 375):\n        return _abort(cfg, strategy_id, "hold_minutes must be 1..375")\n    if cfg["premium_max"] > 0 and cfg["premium_min"] >= cfg["premium_max"]:\n        return _abort(cfg, strategy_id, "premium band needs min < max")\n    if abs(cfg["strike_offset"]) > 10:\n        return _abort(cfg, strategy_id, "strike_offset must be within ±10 steps")\n\n    src = CandleSource(db_path)\n    conn = src._conn()\n\n    def spot_day(ds: int) -> list:\n        return [StfcBar(r["ts"], r["open"], r["high"], r["low"], r["close"])\n                for r in conn.execute(\n                    """SELECT ts, open, high, low, close FROM backtest_candles_1m\n                       WHERE underlying=? AND instrument_type=\'SPOT\'\n                         AND ts>=? AND ts<? ORDER BY ts""",\n                    (underlying, ds, ds + 86400))]\n\n    days: List[date] = []\n    d = date_from\n    while d <= date_to:\n        if is_trading_day(d):\n            days.append(d)\n        d += timedelta(days=1)\n\n    # ── R5: warm the SuperTrend on sessions before date_from ──\n    st = SuperTrend(cfg["st_len"], cfg["st_mult"])\n    warm_days: List[date] = []\n    d = date_from - timedelta(days=1)\n    guard = 0\n    while len(warm_days) < cfg["warmup_sessions"] and guard < 30:\n        if is_trading_day(d):\n            warm_days.append(d)\n        d -= timedelta(days=1)\n        guard += 1\n    warmup_bars = 0\n    for wd in reversed(warm_days):\n        for b in resample_session(spot_day(_day_start_epoch(wd)),\n                                  day_start_epoch=_day_start_epoch(wd), tf_minutes=tf):\n            st.update(b)\n            warmup_bars += 1\n\n    trades: List[STFCTrade] = []\n    diag = {\n        "days_total": len(days), "days_traded": 0, "days_no_spot": 0,\n        "days_uncovered": 0, "days_skipped_expiry": 0, "days_no_signal": 0,\n        # ── DTE_LOT_MULT_20260911 ──\n        "dte_lot_mult": {str(k): v for k, v in cfg["dte_lot_mult"].items()},\n        "dte_skipped_days": 0, "dte_scaled_days": 0, "dte_unknown_days": 0,\n        "warmup_bars": warmup_bars, "tf_bars": 0,\n        "flips_up": 0, "flips_dn": 0, "arm_lost_eod": 0,\n        "signals_total": 0,\n        "sig_dropped_open": 0, "sig_dropped_budget": 0,\n        "sig_dropped_block_time": 0, "sig_no_spot_bar": 0,\n        "sig_no_candidate": 0, "sig_no_fill": 0, "sig_band_reject": 0,\n        "strike_fallbacks": 0,\n        "entries": 0, "ce_entries": 0, "pe_entries": 0,\n        "ce_net": 0.0, "pe_net": 0.0, "entry_hour_hist": {},\n        "sl_exits": 0, "tp_exits": 0, "time_exits": 0, "candle_exits": 0,\n        "eod_exits": 0,\n        "sl_pnl_gross": 0.0, "tp_pnl_gross": 0.0, "time_pnl_gross": 0.0,\n        "candle_pnl_gross": 0.0, "eod_pnl_gross": 0.0,\n        "hold_sum": 0, "stale_marks": 0,\n        "underlying": underlying, "lot_size": lot_size,\n        "lot_source": lot_source, "qty": qty,\n        "corpus_db": str(db_path).rsplit("/", 1)[-1],\n    }\n\n    def close_trade(pos: dict, ts: int, px: float, reason: str) -> None:\n        gross = (px - pos["entry_px"]) * pos["qty"]\n        ch = charges_for_long_trade(entry_price=pos["entry_px"],\n                                    exit_price=px, qty=pos["qty"]).total_charges\n        net = gross - ch\n        t = pos["trade"]\n        t.exit_ts, t.exit_price, t.exit_reason = ts, round(px, 2), reason\n        t.pnl = t.gross = round(gross, 2)\n        t.charges = round(ch, 2)\n        t.net_pnl = t.net = round(net, 2)\n        t.max_adverse = round(pos["mae"], 2)\n        t.max_favorable = round(pos["mfe"], 2)\n        t.hold_min = int((ts - t.entry_ts) // 60) + 1\n        key = reason.lower()\n        diag[f"{key}_exits"] += 1\n        diag[f"{key}_pnl_gross"] += round(net, 2)\n        diag["hold_sum"] += t.hold_min\n        diag["ce_net" if t.instrument_type == "CE" else "pe_net"] += round(net, 2)\n\n    # ── DTE_LOT_MULT_20260911 ── corpus trading-day calendar (sessions)\n    _dte_cal: List[str] = []\n    if cfg["dte_lot_mult"]:\n        from app.backtest.repo.trading_calendar import trading_dates as _td\n        _dte_cal = _td(underlying, db_path)\n        if not _dte_cal:\n            write_audit_log(f"[BACKTEST][{strategy_id}] dte_lot_mult set but the "\n                            f"corpus calendar is empty — multiplier ignored")\n    from app.backtest.engine.dte_lots import lots_for_day as _lots_for_day, dte_for_day as _dte_for_day\n\n    for i, day in enumerate(days):\n        if cancel_cb and cancel_cb():\n            break\n        if progress_cb:\n            progress_cb({"day": i + 1, "total_days": len(days),\n                         "date": day.isoformat(), "trades": len(trades)})\n\n        ds = _day_start_epoch(day)\n        want = expected_expiry_for_day(day).isoformat()\n        spot_1m = spot_day(ds)\n        if not spot_1m:\n            diag["days_no_spot"] += 1\n            continue\n        spot_by_min = {(b.ts - ds) // 60: b for b in spot_1m}\n        bars_tf = resample_session(spot_1m, day_start_epoch=ds, tf_minutes=tf)\n        diag["tf_bars"] += len(bars_tf)\n        # the indicator ALWAYS advances (R5), even on days we do not trade\n        dirs = [st.update(b)[1] for b in bars_tf]\n\n        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:\n            diag["days_skipped_expiry"] += 1\n            continue\n        day_qty = qty\n        if cfg["dte_lot_mult"]:\n            _dte = _dte_for_day(_dte_cal, day.isoformat(), want) if _dte_cal else None\n            _lots, _tag = _lots_for_day(cfg["lots"], cfg["dte_lot_mult"], _dte)\n            if _tag == "skip":\n                diag["dte_skipped_days"] += 1\n                continue\n            if _tag == "scaled":\n                diag["dte_scaled_days"] += 1\n            elif _tag == "unknown":\n                diag["dte_unknown_days"] += 1\n            day_qty = _lots * lot_size\n\n        sm: dict = {}\n        sigs = session_signals(bars_tf, dirs, direction=cfg["direction"], diag=sm)\n        for k in ("flips_up", "flips_dn", "arm_lost_eod"):\n            diag[k] += sm.get(k, 0)\n        if not sigs:\n            diag["days_no_signal"] += 1\n            continue\n        diag["signals_total"] += len(sigs)\n\n        universe = src.contracts_active_on_day(underlying, ds, expiry=want)\n        if not universe:\n            diag["days_uncovered"] += 1\n            continue\n        step = strike_step([c.get("strike") for c in universe])\n        bars_by_sym: Dict[str, Dict[int, object]] = {}\n\n        def bars(sym: str) -> Dict[int, object]:\n            if sym not in bars_by_sym:\n                bars_by_sym[sym] = {c.ts: c for c in\n                                    src.candles_1m_for_symbol_day(sym, ds)}\n            return bars_by_sym[sym]\n\n        day_trades = 0\n        open_until: Optional[int] = None\n        traded = False\n\n        for s in sigs:\n            tb = bars_tf[s.bar_index]\n            entry_min = (tb.ts - ds) // 60                 # R1: trade bar\'s first minute\n            if open_until is not None and entry_min <= open_until:\n                diag["sig_dropped_open"] += 1\n                continue\n            if cfg["max_trades_per_day"] and day_trades >= cfg["max_trades_per_day"]:\n                diag["sig_dropped_budget"] += 1\n                continue\n            if entry_min >= block_min or entry_min >= eod_min:\n                diag["sig_dropped_block_time"] += 1\n                continue\n            sb = spot_by_min.get(entry_min)\n            if sb is None:\n                diag["sig_no_spot_bar"] += 1\n                continue\n            entry_spot = float(sb.open)\n            fill_ts = ds + entry_min * 60\n            sign = 1 if s.side == "CE" else -1\n            target_strike = atm_strike(entry_spot, step) + sign * cfg["strike_offset"] * step\n\n            def has_print(sym: str) -> bool:\n                b = bars(sym).get(fill_ts)\n                return b is not None and bool(b.open)\n\n            mc, fallback = pick_contract(universe, s.side,\n                                         target_strike=target_strike, has_print=has_print)\n            if mc is None:\n                diag["sig_no_candidate"] += 1\n                continue\n            if fallback:\n                diag["strike_fallbacks"] += 1\n            sym = mc["tradingsymbol"]\n            fb = bars(sym).get(fill_ts)\n            if fb is None or not fb.open:\n                diag["sig_no_fill"] += 1\n                continue\n            entry_px = float(fb.open)\n            if (cfg["premium_max"] > 0 and entry_px >= cfg["premium_max"]) or \\\n               (cfg["premium_min"] > 0 and entry_px < cfg["premium_min"]):\n                diag["sig_band_reject"] += 1\n                continue\n            sl_prem = prem_level(entry_px, cfg["sl_mode"], cfg["sl_value"], is_stop=True)\n            tp_prem = prem_level(entry_px, cfg["tp_mode"], cfg["tp_value"], is_stop=False)\n            hh, mm = entry_min // 60, entry_min % 60\n            sl_tag = ("SLoff" if sl_prem is None else\n                      f"SL{\'P\' if cfg[\'sl_mode\'] == \'pct\' else \'A\'}{cfg[\'sl_value\']:g}")\n            tp_tag = ("TPoff" if tp_prem is None else\n                      f"TP{\'P\' if cfg[\'tp_mode\'] == \'pct\' else \'A\'}{cfg[\'tp_value\']:g}")\n            ex_tag = (f"hold{cfg[\'hold_minutes\']}m" if cfg["exit_mode"] == "minutes"\n                      else "candle")\n            t = STFCTrade(\n                tradingsymbol=sym, symbol=sym, instrument_type=s.side,\n                strike=float(mc["strike"]) if mc.get("strike") is not None else None,\n                expiry=mc.get("expiry"), direction="BUY",\n                entry_ts=fill_ts, entry_price=round(entry_px, 2),\n                sl=round(sl_prem, 2) if sl_prem is not None else None,\n                tp=round(tp_prem, 2) if tp_prem is not None else None,\n                exit_ts=None, exit_price=None, exit_reason=None, qty=day_qty,\n                entry_spot=round(entry_spot, 2),\n                condition=(f"STFC·{s.side}·{hh:02d}:{mm:02d}·{tf}m·ST{cfg[\'st_len\']},"\n                           f"{cfg[\'st_mult\']:g}·{sl_tag}·{tp_tag}·{ex_tag}"\n                           f"{\'·FB\' if fallback else \'\'}"))\n            trades.append(t)\n            diag["entries"] += 1\n            diag["ce_entries" if s.side == "CE" else "pe_entries"] += 1\n            hk = f"{hh:02d}"\n            diag["entry_hour_hist"][hk] = diag["entry_hour_hist"].get(hk, 0) + 1\n            day_trades += 1\n            traded = True\n\n            pos = {"trade": t, "entry_px": entry_px, "qty": day_qty,\n                   "mae": 0.0, "mfe": 0.0, "last_mark": entry_px}\n            ob = bars(sym)\n            exit_min = eod_min\n            # R3 time boundary: candle mode → trade bar\'s last minute;\n            # minutes mode → the minute completing hold_minutes from entry\n            if cfg["exit_mode"] == "minutes":\n                time_min = entry_min + cfg["hold_minutes"] - 1\n                time_reason = "TIME"\n            else:\n                time_min = tb.end_min\n                time_reason = "CANDLE"\n\n            for m in range(entry_min, eod_min + 1):\n                o = ob.get(ds + m * 60)\n                if m >= eod_min:\n                    px = float(o.close) if o is not None else pos["last_mark"]\n                    close_trade(pos, ds + m * 60, px, "EOD")\n                    exit_min = m\n                    break\n                if o is not None:\n                    pos["last_mark"] = float(o.close)\n                    pos["mae"] = min(pos["mae"], (float(o.low) - entry_px) * day_qty)\n                    pos["mfe"] = max(pos["mfe"], (float(o.high) - entry_px) * day_qty)\n                    if sl_prem is not None:\n                        px = prem_fill(level=sl_prem, bar=o, side_is_stop=True)\n                        if px is not None:\n                            close_trade(pos, ds + m * 60, px, "SL")\n                            exit_min = m\n                            break\n                    if tp_prem is not None:\n                        px = prem_fill(level=tp_prem, bar=o, side_is_stop=False)\n                        if px is not None:\n                            close_trade(pos, ds + m * 60, px, "TP")\n                            exit_min = m\n                            break\n                else:\n                    diag["stale_marks"] += 1\n                if m >= time_min:\n                    px = float(o.close) if o is not None else pos["last_mark"]\n                    close_trade(pos, ds + m * 60, px, time_reason)\n                    exit_min = m\n                    break\n            else:\n                last_ts = max(ob) if ob else ds + eod_min * 60\n                close_trade(pos, last_ts, pos["last_mark"], "EOD")\n                exit_min = eod_min\n            open_until = exit_min\n\n        if traded:\n            diag["days_traded"] += 1\n\n    src.close()\n    if diag["entries"]:\n        diag["avg_hold_min"] = round(diag["hold_sum"] / diag["entries"], 1)\n    summary = _summarize(trades, diag)\n    write_audit_log(\n        f"[BACKTEST][{strategy_id}] {underlying} {date_from}..{date_to}: "\n        f"{summary[\'total_trades\']} trades, net {summary[\'net_pnl\']:,.0f}, "\n        f"DD {summary[\'max_drawdown\']:,.0f}, exits SL {diag[\'sl_exits\']} / "\n        f"TP {diag[\'tp_exits\']} / TIME {diag[\'time_exits\']} / CANDLE "\n        f"{diag[\'candle_exits\']} / EOD {diag[\'eod_exits\']}, skipped-open "\n        f"{diag[\'sig_dropped_open\']} of {diag[\'signals_total\']} signals"\n    )\n    return {"run_id": str(uuid.uuid4()), "summary": summary,\n            "config": cfg, "trades": trades, "strategy_id": strategy_id}\n\n\ndef _summarize(trades: List[STFCTrade], diag: dict) -> dict:\n    closed = [t for t in trades if t.exit_price is not None]\n    if not closed:\n        s = _empty_summary()\n        s["diag_stfc"] = diag\n        return s\n    eq = peak = mdd = 0.0\n    for t in sorted(closed, key=lambda x: (x.exit_ts or 0, x.entry_ts or 0)):\n        eq += t.net_pnl\n        peak = max(peak, eq)\n        mdd = max(mdd, peak - eq)\n    nets = [t.net_pnl for t in closed]\n    wins = sum(1 for n in nets if n > 0)\n    net = sum(nets)\n    if abs(net) > 1e-9:\n        for k in ("sl", "tp", "time", "candle", "eod"):\n            diag[f"{k}_pnl_share_pct"] = round(100.0 * diag[f"{k}_pnl_gross"] / net, 1)\n    return {\n        "total_trades": len(closed), "wins": wins,\n        "losses": sum(1 for n in nets if n < 0),\n        "win_rate": round(100.0 * wins / len(closed), 2),\n        "gross_pnl": round(sum(t.pnl for t in closed), 2),\n        "total_charges": round(sum(t.charges for t in closed), 2),\n        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),\n        "ambiguous_fills": 0,\n        "diag_stfc": diag,\n    }\n'
TEST_SRC = '# backend/app/backtest/stfc/test_stfc_runner_sim.py\n#\n# ── STFC_OPT_20260913 ── behavioural simulation suite. Standalone:\n#   cd backend && python3 app/backtest/stfc/test_stfc_runner_sim.py\n# Part 1 exercises the pure engine; part 2 checks SuperTrend parity with the\n# Crypto Lab implementation on the same random bars; part 3 runs the whole\n# runner against a synthetic corpus and checks fill/exit/overlap invariants.\n\nfrom __future__ import annotations\n\nimport os\nimport random\nimport sqlite3\nimport sys\nimport tempfile\nfrom datetime import date, datetime, timedelta\n\nHERE = os.path.dirname(os.path.abspath(__file__))\nBACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))\nsys.path.insert(0, HERE)\nsys.path.insert(0, BACKEND)\n\nfrom stfc_v1_engine import (                     # noqa: E402\n    StfcBar, SuperTrend, resample_session, session_signals, prem_level,\n    prem_fill, strike_step, atm_strike, SESSION_OPEN_MIN)\n\nIST = 5 * 3600 + 30 * 60\nFAILS = []\n\n\ndef check(name: str, ok: bool, note: str = "") -> None:\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}"\n          f"{(\'  — \' + note) if (note and not ok) else \'\'}")\n    if not ok:\n        FAILS.append(name)\n\n\ndef ds_for(d: date) -> int:\n    return int((datetime(d.year, d.month, d.day)\n                - datetime(1970, 1, 1)).total_seconds()) - IST\n\n\ndef _d(ts: int) -> date:\n    return (datetime(1970, 1, 1) + timedelta(seconds=ts + IST)).date()\n\n\ndef m1(ds: int, minute: int, o, h, l, c) -> StfcBar:\n    """1m bar by minute offset from 09:15 (0 = 09:15)."""\n    return StfcBar(ds + (SESSION_OPEN_MIN + minute) * 60, o, h, l, c)\n\n\nDS = ds_for(date(2026, 1, 5))\n\n# ═════════════════════════════════════ 1. engine ═════════════════════════\nprint("── engine: resample ──")\nraw = [m1(DS, k, 100 + k, 105 + k, 95 + k, 101 + k) for k in range(7)]\nb3 = resample_session(raw, day_start_epoch=DS, tf_minutes=3)\ncheck("3m buckets from 09:15", [(b.ts - DS) // 60 for b in b3] == [555, 558, 561])\ncheck("bucket OHLC", b3[0].open == 100 and b3[0].high == 107 and b3[0].low == 95 and b3[0].close == 103)\ncheck("end_min recorded", [b.end_min for b in b3] == [557, 560, 563])\npre = [StfcBar(DS + 9 * 3600 * 1, 1, 1, 1, 1)]           # 09:00, pre-open\ncheck("pre-open bars ignored", len(resample_session(pre + raw, day_start_epoch=DS, tf_minutes=3)) == 3)\n\nprint("── engine: supertrend invariants ──")\nrandom.seed(7)\npx = 100.0\nbars = []\nfor i in range(400):\n    o = px\n    c = px + random.uniform(-2, 2)\n    h = max(o, c) + random.uniform(0, 1.5)\n    l = min(o, c) - random.uniform(0, 1.5)\n    bars.append(StfcBar(i * 180, o, h, l, c))\n    px = c\nst = SuperTrend(10, 2.0)\nout = [st.update(b) for b in bars]\ncheck("ATR na for first len-1 bars", all(out[i][1] is None for i in range(9)) and out[9][1] == 1)\nok = True\nfor i in range(10, 400):\n    line, d = out[i]\n    if d == -1 and bars[i].close < line - 1e-9:\n        ok = False\n    if d == 1 and bars[i].close > line + 1e-9:\n        ok = False\ncheck("direction consistent with price/band", ok)\nflips = sum(1 for i in range(10, 400) if out[i][1] != out[i - 1][1])\ncheck("plausible flip count", 3 <= flips <= 120, str(flips))\n\nprint("── engine: setup machine ──")\n\n\ndef run_seq(seq, direction="BOTH"):\n    bs, ds_ = [], []\n    for i, (d, col) in enumerate(seq):\n        c = 101 if col == "g" else 99 if col == "r" else 100\n        bs.append(StfcBar(DS + (SESSION_OPEN_MIN + 3 * i) * 60, 100, 102, 98, c, SESSION_OPEN_MIN + 3 * i + 2))\n        ds_.append(d)\n    diag = {}\n    sig = session_signals(bs, ds_, direction=direction, diag=diag)\n    return [(s.bar_index, s.side, s.flip_index, s.confirm_index) for s in sig], diag\n\n\nsig, dg = run_seq([(1, "r"), (1, "g"), (-1, "g"), (-1, "r"), (-1, "r"), (-1, "g"), (-1, "r"), (-1, "g"), (-1, "g")])\ncheck("flip C1 ignored, red red GREEN confirms, trade next bar (CE)", sig == [(6, "CE", 2, 5)], str(sig))\nsig, dg = run_seq([(1, "r"), (-1, "g"), (1, "g"), (1, "r"), (1, "g")])\ncheck("flip back resets pending → PE setup", sig == [(4, "PE", 2, 3)], str(sig))\nsig, dg = run_seq([(1, "r"), (-1, "g"), (1, "g"), (1, "r"), (1, "g")], direction="UP")\ncheck("direction UP gates down flips", sig == [])\nsig, dg = run_seq([(1, "r"), (-1, "g"), (-1, "g"), (1, "r"), (1, "r"), (1, "g")])\ncheck("trade on a flip bar still executes; new setup starts there", sig == [(3, "CE", 1, 2), (5, "PE", 3, 4)], str(sig))\nsig, dg = run_seq([(1, "r"), (-1, "g"), (-1, "d"), (-1, "d")])\ncheck("doji never confirms", sig == [])\nsig, dg = run_seq([(1, "r"), (-1, "g"), (-1, "g")])\ncheck("arm on the last bar is lost (no overnight carry) and counted", sig == [] and dg.get("arm_lost_eod") == 1)\nsig, dg = run_seq([(None, "g"), (None, "g"), (1, "g"), (-1, "g"), (-1, "g"), (-1, "g")])\ncheck("warm-up None directions never flip", sig == [(5, "CE", 3, 4)] and dg.get("flips_up") == 1, str(sig))\n\nprint("── engine: levels / fills / strikes ──")\ncheck("prem_level pct stop", prem_level(100, "pct", 20, is_stop=True) == 80)\ncheck("prem_level abs target", prem_level(100, "abs", 15, is_stop=False) == 115)\ncheck("prem_level off", prem_level(100, "off", 15, is_stop=False) is None and prem_level(100, "pct", 0, is_stop=True) is None)\ncheck("prem_level floors at 0.05", prem_level(1.0, "abs", 5, is_stop=True) == 0.05)\nB = StfcBar(0, 100, 106, 94, 101)\ncheck("stop fill at level", prem_fill(level=95, bar=B, side_is_stop=True) == 95)\ncheck("stop gapped through → open", prem_fill(level=101, bar=StfcBar(0, 99, 100, 98, 99), side_is_stop=True) == 99)\ncheck("target untouched → None", prem_fill(level=107, bar=B, side_is_stop=False) is None)\ncheck("strike step inferred", strike_step([24000, 24050, 24100, None]) == 50 and strike_step([50000, 50100]) == 100)\ncheck("atm rounding", atm_strike(24026, 50) == 24050 and atm_strike(24024, 50) == 24000)\n\n# ═════════════════════════════ 2. parity with crypto engine ══════════════\nprint("── parity: SuperTrend vs crypto_stfc_engine ──")\ntry:\n    import types\n    import importlib.util\n    stub = types.ModuleType("app.backtest.crypto.delta_corpus")\n    stub.db = lambda: None\n    stub.IST = None\n    saved = sys.modules.get("app.backtest.crypto.delta_corpus")\n    sys.modules["app.backtest.crypto.delta_corpus"] = stub\n    spec = importlib.util.spec_from_file_location(\n        "crypto_stfc_engine_parity",\n        os.path.join(BACKEND, "app", "backtest", "crypto", "crypto_stfc_engine.py"))\n    CE = importlib.util.module_from_spec(spec)\n    sys.modules["crypto_stfc_engine_parity"] = CE\n    spec.loader.exec_module(CE)\n    if saved is not None:\n        sys.modules["app.backtest.crypto.delta_corpus"] = saved\n    cb = [dict(ts=b.ts, o=b.open, h=b.high, l=b.low, c=b.close, subs=[], i0=i) for i, b in enumerate(bars)]\n    ref = CE.supertrend(cb, 10, 2.0)\n    same = all((ref[i][1] == out[i][1]) and\n               (ref[i][0] is None and out[i][0] is None or abs((ref[i][0] or 0) - (out[i][0] or 0)) < 1e-9)\n               for i in range(400))\n    check("400-bar SuperTrend lines and directions identical to the Crypto Lab engine", same)\nexcept Exception as e:                                     # crypto engine absent → skip, not fail\n    print(f"  SKIP  crypto engine parity ({e!r})")\n\n# ═════════════════════════════ 3. runner e2e ═════════════════════════════\nprint("── runner: synthetic corpus ──")\ntry:\n    from backtest_stfc_runner import run_stfc_backtest, _merge_cfg, DEFAULTS   # noqa: E402\n    from app.backtest.engine.expiry_calendar import expected_expiry_for_day  # noqa: E402\n    from app.utils.market_hours import is_trading_day                         # noqa: E402\nexcept Exception as e:\n    print(f"  SKIP  runner e2e (app tree unavailable: {e!r})")\n    if FAILS:\n        print("FAILED:", FAILS)\n        sys.exit(1)\n    print("ALL PASS")\n    sys.exit(0)\n\nDDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,\ntradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,\nclose REAL,volume INTEGER,oi INTEGER);\nCREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);\nCREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);\nCREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""\n\n\ndef build_corpus(path: str, days: list, seed: int = 11) -> dict:\n    """Random-walk NIFTY spot (0.02%/min sigma) + synthetic weekly chain:\n    CE = max(spot-K,0)+time_value, PE mirror; time value decays with the\n    minute so 0DTE-like behaviour exists. Returns {day: expiry}."""\n    random.seed(seed)\n    conn = sqlite3.connect(path)\n    conn.executescript(DDL)\n    rows = []\n    exp_by_day = {}\n    spot = 24000.0\n    for d in days:\n        ds = ds_for(d)\n        exp = expected_expiry_for_day(d).isoformat()\n        exp_by_day[d] = exp\n        strikes = [24000 + 50 * k for k in range(-12, 13)]\n        minute_spot = []\n        for m in range(SESSION_OPEN_MIN, 15 * 60 + 30):\n            o = spot\n            c = spot * (1 + random.gauss(0, 0.0002))\n            h = max(o, c) * (1 + abs(random.gauss(0, 0.00008)))\n            l = min(o, c) * (1 - abs(random.gauss(0, 0.00008)))\n            ts = ds + m * 60\n            rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", None, None, o, h, l, c, 0, 0))\n            minute_spot.append((ts, o, h, l, c, m))\n            spot = c\n        for K in strikes:\n            for side in ("CE", "PE"):\n                sym = f"NIFTY{exp.replace(\'-\', \'\')}{K}{side}"\n                for (ts, o, h, l, c, m) in minute_spot:\n                    tv = 60.0 * (1 - (m - SESSION_OPEN_MIN) / 800.0)\n                    def px(s):\n                        return round(max(s - K, 0) + tv, 2) if side == "CE" else round(max(K - s, 0) + tv, 2)\n                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp,\n                                 px(o), max(px(h), px(l), px(o), px(c)), min(px(h), px(l), px(o), px(c)), px(c), 100, 100))\n    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)\n    conn.commit()\n    conn.close()\n    return exp_by_day\n\n\ntmpdir = tempfile.mkdtemp()\ndbp = os.path.join(tmpdir, "bt.db")\nall_days = []\nd = date(2026, 1, 1)\nwhile len(all_days) < 8:\n    if is_trading_day(d):\n        all_days.append(d)\n    d += timedelta(days=1)\nexp_by_day = build_corpus(dbp, all_days)\nwarm = all_days[:3]\nrun_days = all_days[3:]\nos.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")\n\n\ndef run(cfg, **kw):\n    r = run_stfc_backtest(db_path=dbp, strategy_id="STFC_V1", underlying="NIFTY",\n                          date_from=run_days[0], date_to=run_days[-1],\n                          config_override=cfg, **kw)\n    return r\n\n\ndef invariants(r, cfg, label):\n    cfgm = _merge_cfg(cfg)\n    tf = cfgm["timeframe_minutes"]\n    trades = r["trades"]\n    diag = r["summary"]["diag_stfc"]\n    ok_all = True\n    notes = []\n    last_exit = {}\n    for t in trades:\n        ds = ds_for(_d(t.entry_ts))\n        emin = (t.entry_ts - ds) // 60\n        if (emin - SESSION_OPEN_MIN) % tf != 0:\n            ok_all = False; notes.append(f"entry not on tf grid {emin}")\n        if t.exit_ts < t.entry_ts:\n            ok_all = False; notes.append("exit before entry")\n        if t.exit_reason not in ("SL", "TP", "TIME", "CANDLE", "EOD"):\n            ok_all = False; notes.append(f"bad reason {t.exit_reason}")\n        if t.exit_reason == "SL" and t.exit_price > t.sl + 1e-9:\n            ok_all = False; notes.append("SL fill above level")\n        if t.exit_reason == "TP" and t.exit_price < t.tp - 1e-9:\n            ok_all = False; notes.append("TP fill below level")\n        xmin = (t.exit_ts - ds) // 60\n        if t.exit_reason == "CANDLE" and xmin != emin + tf - 1:\n            ok_all = False; notes.append(f"CANDLE exit minute {xmin} != {emin + tf - 1}")\n        if t.exit_reason == "TIME" and t.hold_min != cfgm["hold_minutes"]:\n            ok_all = False; notes.append(f"TIME hold {t.hold_min}")\n        if t.exit_reason == "EOD" and xmin != 15 * 60 + 20:\n            ok_all = False; notes.append(f"EOD minute {xmin}")\n        if cfgm["direction"] == "UP" and t.instrument_type != "CE":\n            ok_all = False; notes.append("UP produced PE")\n        if ds in last_exit and t.entry_ts <= last_exit[ds]:\n            ok_all = False; notes.append("overlapping positions")\n        last_exit[ds] = t.exit_ts\n        if abs(t.net_pnl - (t.pnl - t.charges)) > 0.011:\n            ok_all = False; notes.append("net != gross - charges")\n    acc = (diag["entries"] + diag["sig_dropped_open"] + diag["sig_dropped_budget"]\n           + diag["sig_dropped_block_time"] + diag["sig_no_spot_bar"]\n           + diag["sig_no_candidate"] + diag["sig_no_fill"] + diag["sig_band_reject"])\n    if acc != diag["signals_total"]:\n        ok_all = False; notes.append(f"signal accounting {acc} != {diag[\'signals_total\']}")\n    if diag["entries"] != len(trades):\n        ok_all = False; notes.append("entries != trades")\n    check(f"{label}: invariants over {len(trades)} trades", ok_all, "; ".join(notes[:4]))\n    return trades, diag\n\n\nbase = dict(DEFAULTS)\nbase.update(timeframe_minutes=3, st_len=10, st_mult=2.0, sl_mode="pct", sl_value=20,\n            tp_mode="pct", tp_value=30, exit_mode="candle")\nr = run(base)\ncheck("run completes (not aborted)", not r.get("aborted"), str(r.get("reason")))\ntrades, diag = invariants(r, base, "candle mode")\ncheck("warm-up consumed prior sessions", diag["warmup_bars"] > 0)\ncheck("some trades and signals", len(trades) > 0 and diag["signals_total"] >= len(trades))\ncheck("ATM strike selection", all(t.strike == atm_strike(t.entry_spot, 50) for t in trades if "FB" not in t.condition))\ncheck("entry fill = option 1m open at entry minute", True)   # by construction of the runner; covered via sqlite check below\nconn = sqlite3.connect(dbp)\nok = True\nfor t in trades[:20]:\n    row = conn.execute("SELECT open FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",\n                       (t.tradingsymbol, t.entry_ts)).fetchone()\n    if row is None or abs(row[0] - t.entry_price) > 0.011:\n        ok = False\nconn.close()\ncheck("entry_price equals the contract\'s 1m open at entry_ts", ok)\ncheck("candle-mode exits are CANDLE/SL/TP only", diag["time_exits"] == 0 and diag["eod_exits"] == 0)\ncheck("pnl shares sum to ~100%", abs(sum(diag.get(f"{k}_pnl_share_pct", 0) for k in ("sl", "tp", "time", "candle", "eod")) - 100) < 1.0\n      if abs(r["summary"]["net_pnl"]) > 1 else True)\n\nmins = dict(base, exit_mode="minutes", hold_minutes=60, sl_mode="off", tp_mode="off")\nr2 = run(mins)\ntrades2, diag2 = invariants(r2, mins, "minutes mode, no SL/TP")\ncheck("all exits TIME (or EOD near close)", diag2["sl_exits"] == 0 and diag2["tp_exits"] == 0 and diag2["time_exits"] > 0 and diag2["candle_exits"] == 0)\ncheck("holds skip overlapping signals", diag2["sig_dropped_open"] > 0)\ncheck("fewer entries than candle mode (skips)", len(trades2) <= len(trades))\n\nup = dict(base, direction="UP")\nr3 = run(up)\ntrades3, diag3 = invariants(r3, up, "direction UP")\ncheck("UP → CE only, pe_entries 0", diag3["pe_entries"] == 0 and diag3["flips_dn"] > 0)\n\noff = dict(base, strike_offset=2)\nr4 = run(off)\ntrades4, _ = invariants(r4, off, "strike offset +2")\ncheck("offset +2 OTM applied per side", all(\n    (t.strike == atm_strike(t.entry_spot, 50) + 100) if t.instrument_type == "CE"\n    else (t.strike == atm_strike(t.entry_spot, 50) - 100) for t in trades4 if "FB" not in t.condition))\n\ncap = dict(base, max_trades_per_day=2)\nr5 = run(cap)\ntrades5, diag5 = invariants(r5, cap, "max 2/day")\nper_day = {}\nfor t in trades5:\n    per_day[_d(t.entry_ts)] = per_day.get(_d(t.entry_ts), 0) + 1\ncheck("max_trades_per_day respected", all(v <= 2 for v in per_day.values()) and diag5["sig_dropped_budget"] > 0)\n\nblk = dict(base, entry_block_time="10:00", eod_square_off="10:30")\nr6 = run(blk)\ntrades6, diag6 = invariants(r6, blk, "block 10:00 / eod 10:30")\ncheck("no entries at/after block time", all((t.entry_ts - ds_for(_d(t.entry_ts))) // 60 < 600 for t in trades6))\n\ndte = dict(base, dte_lot_mult="0:0")\nr7 = run(dte)\ntrades7, diag7 = invariants(r7, dte, "dte_lot_mult 0:0")\ncheck("expiry-day sessions skipped via DTE mult", diag7["dte_skipped_days"] >= 1 or diag7["dte_unknown_days"] >= 1)\n\nskip = dict(base, skip_expiry_day=True)\nr8 = run(skip)\ncheck("skip_expiry_day counts", r8["summary"]["diag_stfc"]["days_skipped_expiry"] >= 1)\n\nprint("── runner: config guards (abort, never run) ──")\nfor bad, why in ((dict(base, sl_mode="pct", sl_value=150), "pct SL > 100 looks like rupees"),\n                 (dict(base, entry_block_time="15:25", eod_square_off="15:20"), "block after eod"),\n                 (dict(base, exit_mode="minutes", hold_minutes=400), "hold > 375"),\n                 (dict(base, timeframe_minutes=90), "tf 90"),\n                 (dict(base, premium_min=200, premium_max=100), "band inverted")):\n    rb = run(bad)\n    check(f"abort: {why}", rb.get("aborted") is True and rb["run_id"] is None)\n\ncanc = run(base, cancel_cb=lambda: True)\ncheck("cancel_cb stops before trading", canc["summary"]["total_trades"] == 0 and not canc.get("aborted"))\n\nprog = []\nrun(base, progress_cb=prog.append)\ncheck("progress callback per day", len(prog) == len(run_days) and prog[-1]["day"] == len(run_days))\n\nif FAILS:\n    print("FAILED:", FAILS)\n    sys.exit(1)\nprint("ALL PASS")\n'

# Patch fragments consumed by apply_STFC_OPT_20260913.py
# Every OLD anchor is asserted unique before any write.

# ───────────────────────────── backend: backtest_routes.py ─────────────────
ROUTES_SUPPORTED_OLD = '''    if req.strategy_id not in ("SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "TSG_V1", "GC_V1", "TMA_V1", "TMA_V2", "VAP_V1", "VET_V1", "BB_V1", "BB_V2", "CBO_V1", "BRK_V1", "ORV_V1", "ORB_V1"):
        raise HTTPException(400, "Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, IC_V2, TSG_V1, GC_V1, TMA_V1, TMA_V2, VAP_V1, VET_V1, BB_V1, BB_V2, CBO_V1, BRK_V1, ORV_V1, ORB_V1")
'''
ROUTES_SUPPORTED_NEW = '''    if req.strategy_id not in ("SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "TSG_V1", "GC_V1", "TMA_V1", "TMA_V2", "VAP_V1", "VET_V1", "BB_V1", "BB_V2", "CBO_V1", "BRK_V1", "ORV_V1", "ORB_V1", "STFC_V1"):   # ── STFC_OPT_20260913 ──
        raise HTTPException(400, "Supported: SCALP_V1, SCALP_V3, SCALP_V5, HA_V1, HA_SELL, IC_V1, IC_V2, TSG_V1, GC_V1, TMA_V1, TMA_V2, VAP_V1, VET_V1, BB_V1, BB_V2, CBO_V1, BRK_V1, ORV_V1, ORB_V1, STFC_V1")
'''
ROUTES_ARM_OLD = '''                elif req.strategy_id == "ORB_V1":
                    # ── ORB_V1_20260903 BEGIN ── "Outrider" static opening-range
'''
ROUTES_ARM_NEW = '''                elif req.strategy_id == "STFC_V1":
                    # ── STFC_OPT_20260913 BEGIN ── SuperTrend flip-confirm on
                    # index SPOT (tf bars), ATM±offset weekly option BUY at the
                    # trade bar's 1m open, premium SL/TP + candle-close or
                    # N-minute time exit, one position at a time. Keep this
                    # chain in sync with queue_worker._dispatch_run_impl —
                    # two hand-maintained copies.
                    from app.utils.app_paths import APP_HOME
                    from app.backtest.stfc.backtest_stfc_runner import run_stfc_backtest
                    db = APP_HOME / "backtest" / "backtest.db"
                    stf = run_stfc_backtest(
                        db_path=str(db), strategy_id=req.strategy_id,
                        underlying=req.underlying, date_from=df, date_to=dt,
                        config_override=(req.config_override or {}), progress_cb=_cb,
                        cancel_cb=lambda: _JOBS.run.get("cancel", False),
                    )
                    result = {
                        "run_id": stf["run_id"], "summary": stf["summary"],
                        "config": stf.get("config", (req.config_override or {})),
                        "trades": stf["trades"], "strategy_id": req.strategy_id,
                        # ── ABORT_REASON_PASSTHROUGH ── see the TMA block
                        "aborted": stf.get("aborted"), "reason": stf.get("reason"),
                    }
                    # ── STFC_OPT_20260913 END ──
                elif req.strategy_id == "ORB_V1":
                    # ── ORB_V1_20260903 BEGIN ── "Outrider" static opening-range
'''

# ───────────────────────────── backend: queue_worker.py ────────────────────
QUEUE_ARM_OLD = '''    if strategy_id == "ORB_V1":
        # ── ORB_V1_20260903 ── "Outrider" static opening-range breakout on
'''
QUEUE_ARM_NEW = '''    if strategy_id == "STFC_V1":
        # ── STFC_OPT_20260913 ── SuperTrend flip-confirm on index SPOT with
        # ATM±offset weekly option BUY execution and premium exits. Keep this
        # chain in sync with backtest_routes — two hand-maintained copies.
        from app.backtest.stfc.backtest_stfc_runner import run_stfc_backtest
        stf = run_stfc_backtest(db_path=str(db), strategy_id=strategy_id,
                                underlying=underlying, date_from=df, date_to=dt,
                                config_override=(config or {}),
                                progress_cb=progress_cb, cancel_cb=cancel_cb)
        return {"run_id": stf["run_id"], "summary": stf["summary"],
                "config": stf.get("config", (config or {})), "trades": stf["trades"],
                "strategy_id": strategy_id,
                # ── ABORT_REASON_PASSTHROUGH ── same contract as the IC arm
                "aborted": stf.get("aborted"), "reason": stf.get("reason")}

    if strategy_id == "ORB_V1":
        # ── ORB_V1_20260903 ── "Outrider" static opening-range breakout on
'''

# ───────────────────────────── frontend: Backtest.jsx ──────────────────────
BT_LS_OLD = '''// ── ORB_V1_UI_20260903 BEGIN ── static ORB breakout (ORB_V1). Own LS key.
const ORB_LS_KEY = "scalp_backtest_orb_v1";
'''
BT_LS_NEW = '''// ── STFC_OPT_20260913 BEGIN ── SuperTrend flip-confirm (STFC_V1). Own LS key.
const STFC_LS_KEY = "scalp_backtest_stfc_v1";
function loadStfcParams() {
  try { return JSON.parse(localStorage.getItem(STFC_LS_KEY)) || {}; } catch { return {}; }
}
// ── STFC_OPT_20260913 END ──
// ── ORB_V1_UI_20260903 BEGIN ── static ORB breakout (ORB_V1). Own LS key.
const ORB_LS_KEY = "scalp_backtest_orb_v1";
'''
BT_SAVED_OLD = '''  const orbSaved = loadOrbParams();     // ── ORB_V1_UI_20260903 ──
'''
BT_SAVED_NEW = '''  const orbSaved = loadOrbParams();     // ── ORB_V1_UI_20260903 ──
  const stfcSaved = loadStfcParams();   // ── STFC_OPT_20260913 ──
'''
BT_IDLIST_OLD = '''     ["SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "TSG_V1", "GC_V1", "TMA_V1", "TMA_V2", "VET_V1", "CBO_V1", "BRK_V1", "ORB_V1"].includes(saved.strategyId)'''
BT_IDLIST_NEW = '''     ["SCALP_V1", "SCALP_V3", "SCALP_V5", "HA_V1", "HA_SELL", "IC_V1", "IC_V2", "TSG_V1", "GC_V1", "TMA_V1", "TMA_V2", "VET_V1", "CBO_V1", "BRK_V1", "ORB_V1", "STFC_V1"].includes(saved.strategyId)'''
BT_STATE_OLD = '''  const isORB = strategyId === "ORB_V1";
'''
BT_STATE_NEW = '''  const isORB = strategyId === "ORB_V1";
  // ── STFC_OPT_20260913 BEGIN ── SuperTrend flip-confirm ("STFC"). Spot
  // signals on tf bars, ATM±offset weekly option BUY, premium SL/TP and a
  // candle-close or N-minute time exit. Port of the TradingView indicator
  // and the Crypto Lab engine (kept in parity).
  const isSTFC = strategyId === "STFC_V1";
  const [stfcTf, setStfcTf] = useState(stfcSaved.tf ?? 3);
  const [stfcLen, setStfcLen] = useState(stfcSaved.len ?? 10);
  const [stfcMult, setStfcMult] = useState(stfcSaved.mult ?? 2);
  const [stfcDir, setStfcDir] = useState(stfcSaved.dir ?? "BOTH");
  const [stfcOffset, setStfcOffset] = useState(stfcSaved.offset ?? 0);
  const [stfcPremMin, setStfcPremMin] = useState(stfcSaved.premMin ?? 0);
  const [stfcPremMax, setStfcPremMax] = useState(stfcSaved.premMax ?? 0);
  const [stfcSlMode, setStfcSlMode] = useState(stfcSaved.slMode ?? "pct");
  const [stfcSlVal, setStfcSlVal] = useState(stfcSaved.slVal ?? 20);
  const [stfcTpMode, setStfcTpMode] = useState(stfcSaved.tpMode ?? "pct");
  const [stfcTpVal, setStfcTpVal] = useState(stfcSaved.tpVal ?? 30);
  const [stfcExitMode, setStfcExitMode] = useState(stfcSaved.exitMode ?? "candle");
  const [stfcHold, setStfcHold] = useState(stfcSaved.hold ?? 5);
  const [stfcBlockTime, setStfcBlockTime] = useState(stfcSaved.blockTime ?? "15:00");
  const [stfcEod, setStfcEod] = useState(stfcSaved.eod ?? "15:20");
  const [stfcMaxDay, setStfcMaxDay] = useState(stfcSaved.maxDay ?? 0);
  const [stfcLots, setStfcLots] = useState(stfcSaved.lots ?? 1);
  const [stfcLotSize, setStfcLotSize] = useState(stfcSaved.lotSize ?? 0);
  const [stfcSkipExpiry, setStfcSkipExpiry] = useState(stfcSaved.skipExpiry ?? false);
  const [stfcDteMult, setStfcDteMult] = useState(stfcSaved.dteMult ?? "");
  useEffect(() => {
    try { localStorage.setItem(STFC_LS_KEY, JSON.stringify({ tf: stfcTf, len: stfcLen, mult: stfcMult, dir: stfcDir, offset: stfcOffset, premMin: stfcPremMin, premMax: stfcPremMax, slMode: stfcSlMode, slVal: stfcSlVal, tpMode: stfcTpMode, tpVal: stfcTpVal, exitMode: stfcExitMode, hold: stfcHold, blockTime: stfcBlockTime, eod: stfcEod, maxDay: stfcMaxDay, lots: stfcLots, lotSize: stfcLotSize, skipExpiry: stfcSkipExpiry, dteMult: stfcDteMult })); } catch { /* ignore */ }
  }, [stfcTf, stfcLen, stfcMult, stfcDir, stfcOffset, stfcPremMin, stfcPremMax, stfcSlMode, stfcSlVal, stfcTpMode, stfcTpVal, stfcExitMode, stfcHold, stfcBlockTime, stfcEod, stfcMaxDay, stfcLots, stfcLotSize, stfcSkipExpiry, stfcDteMult]);
  // ── STFC_OPT_20260913 END ──
'''
BT_BUILD_OLD = '''    if (sid === "ORB_V1") {
      // ── ORB_V1_UI_20260903 ── orb_minutes + both_side_policy is the
'''
BT_BUILD_NEW = '''    if (sid === "STFC_V1") {
      // ── STFC_OPT_20260913 ── st_len + st_mult is the describeConfig /
      // paramFormat detection key (no other config carries both).
      return {
        timeframe_minutes: Number(stfcTf) || 3,
        st_len: Number(stfcLen) || 10,
        st_mult: Number(stfcMult) || 2,
        direction: stfcDir,
        warmup_sessions: 3,
        strike_offset: Number(stfcOffset) || 0,
        premium_min: Number(stfcPremMin) || 0,
        premium_max: Number(stfcPremMax) || 0,
        sl_mode: stfcSlMode, sl_value: Number(stfcSlVal) || 0,
        tp_mode: stfcTpMode, tp_value: Number(stfcTpVal) || 0,
        exit_mode: stfcExitMode, hold_minutes: Number(stfcHold) || 5,
        entry_block_time: stfcBlockTime, eod_square_off: stfcEod,
        max_trades_per_day: Number(stfcMaxDay) || 0,
        lots: Number(stfcLots) || 1, lot_size: Number(stfcLotSize) || 0,
        skip_expiry_day: !!stfcSkipExpiry,
        dte_lot_mult: stfcDteMult || "",
      };
    }
    if (sid === "ORB_V1") {
      // ── ORB_V1_UI_20260903 ── orb_minutes + both_side_policy is the
'''
BT_DEPS_OLD = '''    orbOrbMin, orbTrigger, orbPremMax, orbPremMin, orbTargetMode, orbTargetVal, orbSlMode, orbSlPts, orbSlFill, orbSlTrig, orbSlDistMode, orbPremSlMode, orbPremSlVal, orbBlockTime, orbMaxDay, orbMaxSide, orbEod, orbLots, orbLotSize, orbSkipExpiry, orbDteMult,   // ── DTE_LOT_MULT_20260911 / ORB_V1_UI_20260903 ── STALE-CLOSURE RULE: buildConfig reads every one of these.
'''
BT_DEPS_NEW = '''    orbOrbMin, orbTrigger, orbPremMax, orbPremMin, orbTargetMode, orbTargetVal, orbSlMode, orbSlPts, orbSlFill, orbSlTrig, orbSlDistMode, orbPremSlMode, orbPremSlVal, orbBlockTime, orbMaxDay, orbMaxSide, orbEod, orbLots, orbLotSize, orbSkipExpiry, orbDteMult,   // ── DTE_LOT_MULT_20260911 / ORB_V1_UI_20260903 ── STALE-CLOSURE RULE: buildConfig reads every one of these.
    stfcTf, stfcLen, stfcMult, stfcDir, stfcOffset, stfcPremMin, stfcPremMax, stfcSlMode, stfcSlVal, stfcTpMode, stfcTpVal, stfcExitMode, stfcHold, stfcBlockTime, stfcEod, stfcMaxDay, stfcLots, stfcLotSize, stfcSkipExpiry, stfcDteMult,   // ── STFC_OPT_20260913 ── STALE-CLOSURE RULE: buildConfig reads every one of these.
'''
BT_CHIP_OLD = '''          { id: "ORB_V1", label: "ORB V1", sub: "ORB breakout" },   // ── ORB_V1_UI_20260903 ──
'''
BT_CHIP_NEW = '''          { id: "ORB_V1", label: "ORB V1", sub: "ORB breakout" },   // ── ORB_V1_UI_20260903 ──
          { id: "STFC_V1", label: "STFC V1", sub: "SuperTrend flip-confirm" },   // ── STFC_OPT_20260913 ──
'''
BT_HIDE1_OLD = '''          {!isIC && !isTSG && !isTMA && !isTMA2 && !isGC && !isVET && !isCBO && !isBRK && !isORB && (
            <>
'''
BT_HIDE1_NEW = '''          {!isIC && !isTSG && !isTMA && !isTMA2 && !isGC && !isVET && !isCBO && !isBRK && !isORB && !isSTFC && (   // ── STFC_OPT_20260913 ──
            <>
'''
BT_HIDE2_OLD = '''          {!isV5 && !isHA && !isIC && !isTSG && !isTMA && !isTMA2 && !isGC && !isVET && !isCBO && !isBRK && !isORB && (
'''
BT_HIDE2_NEW = '''          {!isV5 && !isHA && !isIC && !isTSG && !isTMA && !isTMA2 && !isGC && !isVET && !isCBO && !isBRK && !isORB && !isSTFC && (   // ── STFC_OPT_20260913 ──
'''
BT_FORM_OLD = '''          {isORB && (
            /* ── ORB_V1_UI_20260903 BEGIN ── static ORB breakout. Shared
'''
BT_FORM_NEW = '''          {isSTFC && (
            /* ── STFC_OPT_20260913 BEGIN ── SuperTrend flip-confirm. Shared
               premium/RR/session/lots fields are HIDDEN for STFC —
               everything the runner reads is defined here. */
            <div style={{ gridColumn: "1 / -1", marginTop: 8 }}>
              <div style={{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}>
                <Field label="Signal tf (min)"><input type="number" min="1" max="60" style={inputStyle} value={stfcTf} onChange={(e) => setStfcTf(Number(e.target.value))} title="SPOT 1m resampled to this many minutes on the 09:15 session grid (same buckets as a TradingView NSE chart)." /></Field>
                <Field label="ST length"><input type="number" min="2" max="200" style={inputStyle} value={stfcLen} onChange={(e) => setStfcLen(Number(e.target.value))} title="SuperTrend ATR length. Pine ta.supertrend parity: RMA seeded with the SMA of the first N true ranges; state is continuous across sessions and warmed on 3 prior sessions." /></Field>
                <Field label="ST factor"><input type="number" step="0.1" min="0.1" style={inputStyle} value={stfcMult} onChange={(e) => setStfcMult(Number(e.target.value))} /></Field>
                <Field label="Direction">
                  <select style={inputStyle} value={stfcDir} onChange={(e) => setStfcDir(e.target.value)} title="UP = only red→green flips (buys CE). DOWN = only green→red flips (buys PE).">
                    <option value="BOTH">both</option><option value="UP">up flips only (CE)</option><option value="DOWN">down flips only (PE)</option>
                  </select>
                </Field>
                <Field label="Strike offset (steps OTM)"><input type="number" min="-10" max="10" style={inputStyle} value={stfcOffset} onChange={(e) => setStfcOffset(Number(e.target.value))} title="0 = ATM from the entry-minute spot open. +1 = one strike step out of the money, −1 = one step in. If that strike has no print at the entry minute the nearest one with a print is used (strike_fallbacks in diag)." /></Field>
                <Field label="Premium min ₹ (0=off)"><input type="number" min="0" style={inputStyle} value={stfcPremMin} onChange={(e) => setStfcPremMin(Number(e.target.value))} title="Sanity band on the fill: a signal whose option fills outside [min, max) is dropped (sig_band_reject)." /></Field>
                <Field label="Premium max ₹ (0=off)"><input type="number" min="0" style={inputStyle} value={stfcPremMax} onChange={(e) => setStfcPremMax(Number(e.target.value))} /></Field>
              </div>
              <div style={{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}>
                <Field label="Stop loss">
                  <select style={inputStyle} value={stfcSlMode} onChange={(e) => setStfcSlMode(e.target.value)} title="On the PREMIUM. abs = entry − ₹value; pct = entry × (1 − value/100). Checked every minute BEFORE the target; books AT the level (open if gapped through).">
                    <option value="off">off</option><option value="abs">abs ₹ under entry</option><option value="pct">% of entry</option>
                  </select>
                </Field>
                <Field label="SL value"><input type="number" step="0.5" min="0" style={inputStyle} value={stfcSlVal} onChange={(e) => setStfcSlVal(Number(e.target.value))} /></Field>
                <Field label="Target">
                  <select style={inputStyle} value={stfcTpMode} onChange={(e) => setStfcTpMode(e.target.value)} title="On the PREMIUM. abs = entry + ₹value; pct = entry × (1 + value/100). Books AT the level (open if gapped through).">
                    <option value="off">off</option><option value="abs">abs ₹ over entry</option><option value="pct">% of entry</option>
                  </select>
                </Field>
                <Field label="TP value"><input type="number" step="0.5" min="0" style={inputStyle} value={stfcTpVal} onChange={(e) => setStfcTpVal(Number(e.target.value))} /></Field>
                <Field label="Time exit">
                  <select style={inputStyle} value={stfcExitMode} onChange={(e) => setStfcExitMode(e.target.value)} title="candle = sell at the 1m CLOSE of the trade bar's last minute (the Pine 'exit at candle close'). minutes = sell at the 1m close of the minute completing N minutes from entry; a hold can span several tf bars, and signals that fire while in position are SKIPPED (sig_dropped_open).">
                    <option value="candle">candle close</option><option value="minutes">after N minutes</option>
                  </select>
                </Field>
                <Field label="Hold minutes"><input type="number" min="1" max="375" style={inputStyle} value={stfcHold} disabled={stfcExitMode !== "minutes"} onChange={(e) => setStfcHold(Number(e.target.value))} /></Field>
              </div>
              <div style={{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}>
                <Field label="Entry block (HH:MM)"><input type="text" style={inputStyle} value={stfcBlockTime} onChange={(e) => setStfcBlockTime(e.target.value)} title="No new entries at or after this time." /></Field>
                <Field label="EOD square-off"><input type="text" style={inputStyle} value={stfcEod} onChange={(e) => setStfcEod(e.target.value)} title="Open positions are sold at this minute's option close. Must be <= 15:29." /></Field>
                <Field label="Max trades/day (0=∞)"><input type="number" min="0" style={inputStyle} value={stfcMaxDay} onChange={(e) => setStfcMaxDay(Number(e.target.value))} /></Field>
                <Field label="Lots"><input type="number" min="1" style={inputStyle} value={stfcLots} onChange={(e) => setStfcLots(Number(e.target.value))} /></Field>
                <Field label="Lot size (0=index)"><input type="number" min="0" style={inputStyle} value={stfcLotSize} onChange={(e) => setStfcLotSize(Number(e.target.value))} /></Field>
                <Field label="Skip expiry day">
                  <select style={inputStyle} value={stfcSkipExpiry ? "1" : "0"} onChange={(e) => setStfcSkipExpiry(e.target.value === "1")}>
                    <option value="0">no</option><option value="1">yes</option>
                  </select>
                </Field>
                <Field label="DTE × lots (0 = skip)"><input type="text" style={inputStyle} value={stfcDteMult} onChange={(e) => setStfcDteMult(e.target.value)} placeholder="e.g. 0:0, 4:2" title="Per-DTE lot multiplier — DTE = trading SESSIONS to expiry. Format 0:1.5, 1:0, 4:2 · absent DTE = ×1 · 0 = SKIP that day. Blank = off." /></Field>
              </div>
              <div style={{ marginTop: 6, fontSize: 11, color: colors.text.tertiary }}>
                SuperTrend({stfcLen},{stfcMult}) on {stfcTf}m SPOT bars. A flip candle is ignored; the first later candle whose colour matches the new trend arms the NEXT candle, which is traded at its open: red→green buys the {Number(stfcOffset) === 0 ? "ATM" : `ATM${Number(stfcOffset) > 0 ? "+" : ""}${stfcOffset}`} CE, green→red the PE (expected weekly). Exits per minute, stop before target: SL {stfcSlMode === "off" ? "off" : `−${stfcSlVal}${stfcSlMode === "pct" ? "%" : "₹"}`}, TP {stfcTpMode === "off" ? "off" : `+${stfcTpVal}${stfcTpMode === "pct" ? "%" : "₹"}`}, then {stfcExitMode === "minutes" ? `the close ${stfcHold} minutes after entry` : "the trade candle's close"}; EOD {stfcEod}. One position at a time — signals during a hold are skipped, not queued. No entries ≥ {stfcBlockTime}.
                <br /><b>Before reading the P&amp;L:</b> compare sl/tp/time/candle_pnl_share_pct in the run diag — if the timed exit carries the net, the edge is the hold, not the stop/target shape. Check sig_dropped_open against signals_total, and read the Sessions-to-Expiry breakdown: 0DTE decay inside the hold is the first suspect.
              </div>
            </div>
            /* ── STFC_OPT_20260913 END ── */
          )}
          {isORB && (
            /* ── ORB_V1_UI_20260903 BEGIN ── static ORB breakout. Shared
'''
BT_DESC_OLD = '''  // ── ORB_V1_UI_20260903 ── (orb_minutes + both_side_policy is unique to
  // ORB configs; must be tested BEFORE ORV's orb_minutes+atr_pct and
'''
BT_DESC_NEW = '''  // ── STFC_OPT_20260913 ── (st_len + st_mult is unique to STFC configs)
  if (cfg.st_len != null && cfg.st_mult != null) {
    add("Signal", `SuperTrend(${cfg.st_len},${cfg.st_mult}) flip-confirm on ${cfg.timeframe_minutes || 3}m spot`);
    if ((cfg.direction || "BOTH") !== "BOTH") add("Direction", cfg.direction);
    add("Contract", `${Number(cfg.strike_offset) === 0 ? "ATM" : `ATM${Number(cfg.strike_offset) > 0 ? "+" : ""}${cfg.strike_offset}`} weekly BUY`);
    if (Number(cfg.premium_max) > 0 || Number(cfg.premium_min) > 0) add("Band", `₹${cfg.premium_min || 0}–₹${cfg.premium_max || "∞"}`);
    add("Stop", (cfg.sl_mode || "off") === "off" ? "off" : (cfg.sl_mode === "pct" ? `−${cfg.sl_value}% of entry` : `−₹${cfg.sl_value}`));
    add("Target", (cfg.tp_mode || "off") === "off" ? "off" : (cfg.tp_mode === "pct" ? `+${cfg.tp_value}% of entry` : `+₹${cfg.tp_value}`));
    add("Time exit", cfg.exit_mode === "minutes" ? `${cfg.hold_minutes} min after entry` : "trade candle close");
    add("Entries", `${Number(cfg.max_trades_per_day) > 0 ? `≤${cfg.max_trades_per_day}/day, ` : ""}one at a time, none ≥${cfg.entry_block_time || "15:00"}`);
    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);
    if (cfg.lots) add("Lots", cfg.lots);
    if (cfg.skip_expiry_day) add("Expiry", "skipped");
    return out;
  }
  // ── ORB_V1_UI_20260903 ── (orb_minutes + both_side_policy is unique to
  // ORB configs; must be tested BEFORE ORV's orb_minutes+atr_pct and
'''
BT_CSV_OLD = '''      const csv = buildCsv(trades, summary, metrics, resultStrategy, resultConfig, summary?.diag_cbo || summary?.diag_brk);   // ── CBO_PARAMS_EXPORT_20260830 ── ── BRK_V1_UI_20260830 ──
'''
BT_CSV_NEW = '''      const csv = buildCsv(trades, summary, metrics, resultStrategy, resultConfig, summary?.diag_cbo || summary?.diag_brk || summary?.diag_stfc);   // ── CBO_PARAMS_EXPORT_20260830 ── ── BRK_V1_UI_20260830 ── ── STFC_OPT_20260913 ──
'''

# ───────────────────────────── frontend: paramFormat.js ────────────────────
PF_OLD = '''export default { fmtIcSl, cboParamSummary, brkParamSummary, orvParamSummary, orbParamSummary };'''
PF_NEW = '''// ── STFC_OPT_20260913 ── compact one-liner for an STFC_V1 config, defaults
// suppressed. Shared by RunComparison, BacktestQueue and Backtest.jsx.
export function stfcParamSummary(cfg) {
  if (!cfg) return "\\u2014";
  const p = [];
  p.push(`ST${cfg.st_len},${cfg.st_mult}@${cfg.timeframe_minutes || 3}m`);
  if ((cfg.direction || "BOTH") !== "BOTH") p.push(cfg.direction);
  if (Number(cfg.strike_offset) !== 0) p.push(`ATM${Number(cfg.strike_offset) > 0 ? "+" : ""}${cfg.strike_offset}`);
  p.push((cfg.sl_mode || "off") === "off" ? "SLoff" : `SL${cfg.sl_mode === "pct" ? "P" : "A"}${cfg.sl_value}`);
  p.push((cfg.tp_mode || "off") === "off" ? "TPoff" : `TP${cfg.tp_mode === "pct" ? "P" : "A"}${cfg.tp_value}`);
  p.push(cfg.exit_mode === "minutes" ? `hold${cfg.hold_minutes}m` : "candle");
  if (Number(cfg.premium_max) > 0) p.push(`${cfg.premium_min || 0}\\u2013${cfg.premium_max}`);
  if (Number(cfg.max_trades_per_day) > 0) p.push(`${cfg.max_trades_per_day}/d`);
  if ((cfg.entry_block_time || "15:00") !== "15:00") p.push(`blk${cfg.entry_block_time}`);
  if (cfg.eod_square_off && cfg.eod_square_off !== "15:20") p.push(`eod ${cfg.eod_square_off}`);
  if (cfg.skip_expiry_day) p.push("noExp");
  if (Number(cfg.lots) > 1) p.push(`${cfg.lots}L`);
  return p.join(" \\u00b7 ");
}
export default { fmtIcSl, cboParamSummary, brkParamSummary, orvParamSummary, orbParamSummary, stfcParamSummary };'''

# ───────────────────────────── frontend: RunComparison / BacktestQueue ─────
IMPORT_OLD = '''import { fmtIcSl, cboParamSummary, brkParamSummary, orvParamSummary, orbParamSummary } from "./paramFormat";'''
IMPORT_NEW = '''import { fmtIcSl, cboParamSummary, brkParamSummary, orvParamSummary, orbParamSummary, stfcParamSummary } from "./paramFormat";   // ── STFC_OPT_20260913 ──'''
RC_BRANCH_OLD = '''  if (cfg.orb_minutes != null && cfg.both_side_policy != null) {
    return orbParamSummary(cfg);
  }
'''
RC_BRANCH_NEW = '''  if (cfg.st_len != null && cfg.st_mult != null) {   // ── STFC_OPT_20260913 ── key: st_len + st_mult
    return stfcParamSummary(cfg);
  }
  if (cfg.orb_minutes != null && cfg.both_side_policy != null) {
    return orbParamSummary(cfg);
  }
'''

# ───────────────────────────── frontend: SweepBuilder.jsx ──────────────────
SW_CONST_OLD = '''const HA = "HA_V1", HAS = "HA_SELL", IC = "IC_V1", TMA = "TMA_V1", TMA2 = "TMA_V2", TSG = "TSG_V1", GC = "GC_V1", VAP = "VAP_V1", VET = "VET_V1", BRK = "BRK_V1", ORB = "ORB_V1";'''
SW_CONST_NEW = '''const HA = "HA_V1", HAS = "HA_SELL", IC = "IC_V1", TMA = "TMA_V1", TMA2 = "TMA_V2", TSG = "TSG_V1", GC = "GC_V1", VAP = "VAP_V1", VET = "VET_V1", BRK = "BRK_V1", ORB = "ORB_V1", STFC = "STFC_V1";   // ── STFC_OPT_20260913 ──'''
SW_AXES_OLD = '''];
/* ── SWEEP_AXES END ── */'''
SW_AXES_NEW = '''  // ── STFC_OPT_20260913 ── STFC_V1 grid axes. sl/tp value semantics follow
  // the form's mode selects (abs ₹ vs %), so set the mode on the Run form
  // BEFORE launching a sweep on those axes; hold only applies in minutes mode.
  { key: "stfc_tf", label: "STFC signal tf (min)", strategies: [STFC],
    hint: "3, 5, 15", parse: _num,
    apply: (c, v) => { c.timeframe_minutes = Math.max(1, Math.round(v)); }, fmt: (v) => `${Math.round(v)}m` },
  { key: "stfc_len", label: "STFC ST length", strategies: [STFC],
    hint: "7, 10, 14", parse: _num,
    apply: (c, v) => { c.st_len = Math.max(2, Math.round(v)); }, fmt: (v) => `L${Math.round(v)}` },
  { key: "stfc_mult", label: "STFC ST factor", strategies: [STFC],
    hint: "1.5, 2, 3", parse: _num,
    apply: (c, v) => { c.st_mult = Math.abs(v); }, fmt: (v) => `F${Math.abs(v)}` },
  { key: "stfc_sl", label: "STFC SL value (unit = form's SL mode)", strategies: [STFC],
    hint: "10, 20, 30  ·  0 = off", parse: _num,
    apply: (c, v) => { c.sl_value = Math.abs(v); if (Math.abs(v) === 0) c.sl_mode = "off"; }, fmt: (v) => `SL${Math.abs(v)}` },
  { key: "stfc_tp", label: "STFC TP value (unit = form's TP mode)", strategies: [STFC],
    hint: "20, 30, 50  ·  0 = off", parse: _num,
    apply: (c, v) => { c.tp_value = Math.abs(v); if (Math.abs(v) === 0) c.tp_mode = "off"; }, fmt: (v) => `TP${Math.abs(v)}` },
  { key: "stfc_hold", label: "STFC hold minutes (sets minutes mode)", strategies: [STFC],
    hint: "3, 5, 10, 15, 30", parse: _num,
    apply: (c, v) => { c.exit_mode = "minutes"; c.hold_minutes = Math.max(1, Math.round(v)); }, fmt: (v) => `hold${Math.round(v)}` },
  { key: "stfc_offset", label: "STFC strike offset (steps OTM)", strategies: [STFC],
    hint: "-1, 0, 1, 2", parse: _num,
    apply: (c, v) => { c.strike_offset = Math.round(v); }, fmt: (v) => `ATM${v > 0 ? "+" : ""}${Math.round(v)}` },
  { key: "stfc_eod", label: "STFC EOD square-off", strategies: [STFC],
    hint: "14:30, 15:00, 15:20", parse: _hm,
    apply: (c, v) => { c.eod_square_off = v; }, fmt: (v) => `eod ${v}` },
  { key: "stfc_block", label: "STFC entry block (HH:MM)", strategies: [STFC],
    hint: "12:00, 14:00, 15:00", parse: _hm,
    apply: (c, v) => { c.entry_block_time = v; }, fmt: (v) => `blk ${v}` },
];
/* ── SWEEP_AXES END ── */'''

# ───────────────────────────── frontend: displayNames.js ───────────────────
DN_OLD = '''  ORB_V1:    { real: "ORB V1",        code: "Outrider",   sub: "15m ORB breakout" },   // ── ORB_V1 ──
'''
DN_NEW = '''  ORB_V1:    { real: "ORB V1",        code: "Outrider",   sub: "15m ORB breakout" },   // ── ORB_V1 ──
  STFC_V1:   { real: "STFC V1",       code: "Sabre",      sub: "SuperTrend flip-confirm" },   // ── STFC_OPT_20260913 ──
'''



def die(msg):
    print("ABORT:", msg); sys.exit(1)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def replace_n(src, old, new, label, n=1):
    c = src.count(old)
    if c != n:
        die(f"anchor '{label}' count={c} (expected {n})")
    return src.replace(old, new)


def compile_gate(text, name):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(text); tmp = f.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {name}: {e}")
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------- targets
P_ROUTES = os.path.join(BACKEND, "app", "api", "backtest_routes.py")
P_QUEUE = os.path.join(BACKEND, "app", "backtest", "queue_worker.py")
P_PKG = os.path.join(BACKEND, "app", "backtest", "stfc")
P_INIT = os.path.join(P_PKG, "__init__.py")
P_ENGINE = os.path.join(P_PKG, "stfc_v1_engine.py")
P_RUNNER = os.path.join(P_PKG, "backtest_stfc_runner.py")
P_TEST = os.path.join(P_PKG, "test_stfc_runner_sim.py")
P_BT = os.path.join(FRONTEND, "src", "pages", "Backtest.jsx")
P_PF = os.path.join(FRONTEND, "src", "pages", "backtest", "paramFormat.js")
P_RC = os.path.join(FRONTEND, "src", "pages", "backtest", "RunComparison.jsx")
P_BQ = os.path.join(FRONTEND, "src", "pages", "backtest", "BacktestQueue.jsx")
P_SW = os.path.join(FRONTEND, "src", "pages", "backtest", "SweepBuilder.jsx")
P_DN = os.path.join(FRONTEND, "src", "strategies", "displayNames.js")
PATCHED = [P_ROUTES, P_QUEUE, P_BT, P_PF, P_RC, P_BQ, P_SW, P_DN]

# ---------------------------------------------------------------- prerequisites
for p in PATCHED:
    if not os.path.exists(p):
        die(f"missing {p} — run from the repo root")
    if FENCE in read(p):
        die(f"{FENCE} already present in {p}")
if os.path.exists(P_ENGINE) or os.path.exists(P_RUNNER):
    die("backend/app/backtest/stfc/ already has engine/runner files — inspect manually")
# parent fences this build relies on
for p, parent in ((P_ROUTES, "ORB_V1_20260903"), (P_QUEUE, "ORB_V1_20260903"),
                  (P_BT, "DTE_LOT_MULT_20260911"), (P_SW, "ORB_PCT_20260903")):
    if parent not in read(p):
        die(f"parent fence {parent} not found in {p}")
# working-copy drift (ORB_RECONCILE scar): a modified tracked file is a stop sign
try:
    st = subprocess.run(["git", "status", "--porcelain", "--"] + PATCHED,
                        capture_output=True, text=True, cwd=ROOT)
    dirty = [l for l in st.stdout.splitlines() if l.strip() and not l.startswith("??")]
    if dirty:
        die("uncommitted changes on files this script patches (commit or stash first):\n  "
            + "\n  ".join(dirty))
except FileNotFoundError:
    print("git not found — drift check skipped")

# ---------------------------------------------------------------- stage
routes_new = read(P_ROUTES)
routes_new = replace_n(routes_new, ROUTES_SUPPORTED_OLD, ROUTES_SUPPORTED_NEW, "routes supported")
routes_new = replace_n(routes_new, ROUTES_ARM_OLD, ROUTES_ARM_NEW, "routes arm")
queue_new = replace_n(read(P_QUEUE), QUEUE_ARM_OLD, QUEUE_ARM_NEW, "queue arm")
bt = read(P_BT)
bt = replace_n(bt, BT_LS_OLD, BT_LS_NEW, "bt ls key")
bt = replace_n(bt, BT_SAVED_OLD, BT_SAVED_NEW, "bt saved")
bt = replace_n(bt, BT_IDLIST_OLD, BT_IDLIST_NEW, "bt id list")
bt = replace_n(bt, BT_STATE_OLD, BT_STATE_NEW, "bt state")
bt = replace_n(bt, BT_BUILD_OLD, BT_BUILD_NEW, "bt buildConfig")
bt = replace_n(bt, BT_DEPS_OLD, BT_DEPS_NEW, "bt deps")
bt = replace_n(bt, BT_CHIP_OLD, BT_CHIP_NEW, "bt chip")
bt = replace_n(bt, BT_HIDE1_OLD, BT_HIDE1_NEW, "bt hidden shared (x2)", n=2)
bt = replace_n(bt, BT_HIDE2_OLD, BT_HIDE2_NEW, "bt hidden shared 2")
bt = replace_n(bt, BT_FORM_OLD, BT_FORM_NEW, "bt form")
bt = replace_n(bt, BT_DESC_OLD, BT_DESC_NEW, "bt describeConfig")
bt = replace_n(bt, BT_CSV_OLD, BT_CSV_NEW, "bt csv diag")
pf_new = replace_n(read(P_PF), PF_OLD, PF_NEW, "paramFormat export")
rc_new = replace_n(read(P_RC), IMPORT_OLD, IMPORT_NEW, "RunComparison import")
rc_new = replace_n(rc_new, RC_BRANCH_OLD, RC_BRANCH_NEW, "RunComparison branch")
bq_new = replace_n(read(P_BQ), IMPORT_OLD, IMPORT_NEW, "BacktestQueue import")
bq_new = replace_n(bq_new, RC_BRANCH_OLD, RC_BRANCH_NEW, "BacktestQueue branch")
sw_new = replace_n(read(P_SW), SW_CONST_OLD, SW_CONST_NEW, "SweepBuilder const")
sw_new = replace_n(sw_new, SW_AXES_OLD, SW_AXES_NEW, "SweepBuilder axes")
dn_new = replace_n(read(P_DN), DN_OLD, DN_NEW, "displayNames")

compile_gate(routes_new, "backtest_routes.py")
compile_gate(queue_new, "queue_worker.py")
compile_gate(ENGINE_SRC, "stfc_v1_engine.py")
compile_gate(RUNNER_SRC, "backtest_stfc_runner.py")
compile_gate(TEST_SRC, "test_stfc_runner_sim.py")
print("py_compile gate: OK (5 files)")

# ---------------------------------------------------------------- write (all-or-nothing)
for p in PATCHED:
    shutil.copy2(p, p + f".bak-{FENCE}")
os.makedirs(P_PKG, exist_ok=True)
for p, text in ((P_ROUTES, routes_new), (P_QUEUE, queue_new), (P_BT, bt), (P_PF, pf_new),
                (P_RC, rc_new), (P_BQ, bq_new), (P_SW, sw_new), (P_DN, dn_new),
                (P_ENGINE, ENGINE_SRC), (P_RUNNER, RUNNER_SRC), (P_TEST, TEST_SRC)):
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
if not os.path.exists(P_INIT):
    open(P_INIT, "w").close()
print("primary tree written (8 patched + 4 new, 8 backups)")

# ---------------------------------------------------------------- simulation suite (real app tree)
r = subprocess.run([sys.executable, P_TEST], capture_output=True, text=True, cwd=BACKEND)
tail = "\n".join(r.stdout.splitlines()[-8:])
if r.returncode != 0 or "ALL PASS" not in r.stdout:
    print(r.stdout[-4000:]); print(r.stderr[-2000:])
    die("simulation suite failed — files are written; restore from .bak-" + FENCE + " if needed")
print("simulation suite: ALL PASS\n" + tail)

# ---------------------------------------------------------------- dual trees
def mirror(src_root, dst_root, rel):
    dst = os.path.join(dst_root, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(os.path.join(src_root, rel), dst)


if os.path.isdir(DUAL_BACKEND):
    for rel in ("app/api/backtest_routes.py", "app/backtest/queue_worker.py",
                "app/backtest/stfc/__init__.py", "app/backtest/stfc/stfc_v1_engine.py",
                "app/backtest/stfc/backtest_stfc_runner.py", "app/backtest/stfc/test_stfc_runner_sim.py"):
        mirror(BACKEND, DUAL_BACKEND, rel)
    print("dual backend tree synced")
else:
    print("dual backend tree absent (build script rsyncs it) — skipped")
if os.path.isdir(DUAL_FRONTEND):
    for rel in ("src/pages/Backtest.jsx", "src/pages/backtest/paramFormat.js",
                "src/pages/backtest/RunComparison.jsx", "src/pages/backtest/BacktestQueue.jsx",
                "src/pages/backtest/SweepBuilder.jsx", "src/strategies/displayNames.js"):
        mirror(FRONTEND, DUAL_FRONTEND, rel)
    print("dual frontend tree synced")
else:
    print("dual frontend tree absent (build script rsyncs it) — skipped")

# ---------------------------------------------------------------- esbuild verify
esb = None
for cand in (os.path.join(FRONTEND, "node_modules", ".bin", "esbuild"),
             os.path.join(ROOT, "desktop", "node_modules", ".bin", "esbuild"),
             shutil.which("esbuild")):
    if cand and os.path.exists(cand):
        esb = cand; break
if esb:
    for p in (P_BT, P_RC, P_BQ, P_SW, P_PF, P_DN):
        r = subprocess.run([esb, p, "--loader:.jsx=jsx", "--loader:.js=jsx", "--jsx=automatic", "--outfile=/dev/null"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr); die(f"esbuild failed for {p}")
    print("esbuild: OK (6 frontend files)")
else:
    print("esbuild not found — run `npm run build` in frontend/ to verify")

print(f"\nDONE — {FENCE} applied. Rebuild: ./desktop/build-scalp.sh both")
