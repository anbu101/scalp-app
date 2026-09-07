#!/usr/bin/env python3
"""
apply_CRYPTO_STFC_20260907.py — STFC (SuperTrend Flip-Confirm) lab on the
BTCUSD perp corpus, inside the Crypto Lab page.

FENCE: CRYPTO_STFC_20260907

Creates
  backend/app/backtest/crypto/crypto_stfc_engine.py     (new)
  frontend/src/pages/CryptoStfcLab.jsx                  (new)
Patches (fenced, anchored, uniqueness-asserted)
  backend/app/backtest/crypto/delta_corpus.py           (+ backfill_perp_range)
  backend/app/api/crypto_lab_routes.py                  (+ perp pull/coverage, stfc run, runs filter, csv)
  frontend/src/pages/CryptoLab.jsx                      (+ lab tab switch)

Safety: fence presence check (abort if applied), py_compile gate before any
write, staged all-or-nothing writes with .bak-FENCE backups, dual-tree deploy
(desktop/src-tauri/{backend,frontend}) when those trees exist, behavioural
simulation suite, esbuild verification of both JSX files.

Run from the repo root:  python3 apply_CRYPTO_STFC_20260907.py
"""
import os, sys, py_compile, shutil, subprocess, tempfile

FENCE = "CRYPTO_STFC_20260907"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
DUAL_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")
DUAL_FRONTEND = os.path.join(ROOT, "desktop", "src-tauri", "frontend")

ENGINE_SRC = '# ── CRYPTO_STFC_20260907 BEGIN ──\n# backend/app/backtest/crypto/crypto_stfc_engine.py\n#\n# STFC — SuperTrend Flip-Confirm one-candle scalper on the BTCUSD perp corpus\n# (perp_candles_1m in crypto_backtest.db, Delta Exchange India).\n#\n# Rule set (identical to the TradingView Pine reference "ST Flip-Confirm\n# Scalper"; keep the two in lock-step — this module is the parity core):\n#   * SuperTrend(len, mult) on N-minute bars resampled from the 1m corpus.\n#   * Flip on candle C1 → C1 ignored. First later candle whose colour matches\n#     the new trend (green after up-flip / red after down-flip) ARMS the next\n#     candle. The armed candle is traded: enter at its OPEN, exit at its CLOSE.\n#   * One trade per flip; a fresh flip while waiting restarts the setup.\n#   * Stop-loss is walked MINUTE BY MINUTE inside the trade candle using the\n#     1m sub-candles (the Pine version can only see the candle\'s low/high).\n#\n# Costs: spread (USD, once per trade) + taker fee % per side on notional.\n# P/L is in USD per `size_btc` BTC.\n#\n# Grid: tf_list × st_len_list × st_mult_list × sl_list. Signals are computed\n# once per (tf, len, mult); SL variants re-walk the same trade candles.\n# Scoreboard ranking follows the house priority:\n#   years positive ↓ → worst month ↓ → max consecutive losing months ↑ →\n#   positive months ↓ → max drawdown ↑ → net ↓ (secondary).\n#\n# Backtest-scoped: no broker, no order paths.\n\nfrom __future__ import annotations\n\nimport datetime as dt\nimport itertools\nimport json\nimport threading\nimport time\nimport uuid\nfrom dataclasses import dataclass, field, asdict\nfrom typing import Callable, Optional\n\nfrom app.backtest.crypto.delta_corpus import db, IST\n\nSTRATEGY_TAG = "STFC"\nSYMBOL = "BTCUSD"\nMAX_TRADES_STORED = 20000       # per persisted run (primary cell)\n\n\n# ----------------------------------------------------------------------\n# Config\n# ----------------------------------------------------------------------\n@dataclass\nclass StfcConfig:\n    tf_min: int = 3\n    st_len: int = 10\n    st_mult: float = 2.0\n    sl_usd: float = 0.0            # 0 = off\n    spread_usd: float = 0.0\n    fee_pct_side: float = 0.05     # taker fee % per side (Delta ≈ 0.05)\n    size_btc: float = 1.0\n    trade_long: bool = True\n    trade_short: bool = True\n    date_from: str = ""            # YYYY-MM-DD (IST calendar)\n    date_to: str = ""\n    # grid lists — empty ⇒ single cell using the scalar above\n    tf_list: list = field(default_factory=list)\n    st_len_list: list = field(default_factory=list)\n    st_mult_list: list = field(default_factory=list)\n    sl_list: list = field(default_factory=list)\n\n    @classmethod\n    def from_dict(cls, d: dict) -> "StfcConfig":\n        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}\n        cfg = cls(**known)\n        cfg.validate()\n        return cfg\n\n    def validate(self) -> None:\n        if not (1 <= int(self.tf_min) <= 240):\n            raise ValueError("tf_min must be 1..240")\n        if not (2 <= int(self.st_len) <= 200):\n            raise ValueError("st_len must be 2..200")\n        if not (0.1 <= float(self.st_mult) <= 20):\n            raise ValueError("st_mult must be 0.1..20")\n        if self.sl_usd < 0 or self.spread_usd < 0 or self.fee_pct_side < 0:\n            raise ValueError("sl/spread/fee cannot be negative")\n        if self.size_btc <= 0:\n            raise ValueError("size_btc must be > 0")\n        if not (self.trade_long or self.trade_short):\n            raise ValueError("at least one side must be enabled")\n        for name, lst, lo, hi in (("tf_list", self.tf_list, 1, 240),\n                                  ("st_len_list", self.st_len_list, 2, 200),\n                                  ("st_mult_list", self.st_mult_list, 0.1, 20),\n                                  ("sl_list", self.sl_list, 0, 1e9)):\n            for v in lst:\n                if not (lo <= float(v) <= hi):\n                    raise ValueError(f"{name} value {v} out of range")\n        for s in (self.date_from, self.date_to):\n            if s:\n                dt.date.fromisoformat(s)\n        if len(self.cells()) > 400:\n            raise ValueError("grid too large (max 400 cells)")\n\n    def cells(self) -> list:\n        tfs = [int(x) for x in self.tf_list] or [int(self.tf_min)]\n        lens = [int(x) for x in self.st_len_list] or [int(self.st_len)]\n        mults = [float(x) for x in self.st_mult_list] or [float(self.st_mult)]\n        sls = [float(x) for x in self.sl_list] or [float(self.sl_usd)]\n        return [dict(tf_min=a, st_len=b, st_mult=c, sl_usd=d)\n                for a, b, c, d in itertools.product(tfs, lens, mults, sls)]\n\n\n# ----------------------------------------------------------------------\n# Corpus access\n# ----------------------------------------------------------------------\ndef _range_epochs(cfg: StfcConfig) -> tuple:\n    """[start, end) epoch bounds in IST calendar days; open-ended if blank."""\n    start = 0\n    end = 2 ** 40\n    if cfg.date_from:\n        d = dt.date.fromisoformat(cfg.date_from)\n        start = int(dt.datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())\n    if cfg.date_to:\n        d = dt.date.fromisoformat(cfg.date_to) + dt.timedelta(days=1)\n        end = int(dt.datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())\n    return start, end\n\n\ndef load_1m(start: int, end: int, conn=None) -> list:\n    """Ascending list of (ts, o, h, l, c) tuples from the perp corpus."""\n    own = conn is None\n    conn = conn or db()\n    try:\n        rows = conn.execute(\n            "SELECT ts, open, high, low, close FROM perp_candles_1m "\n            "WHERE symbol=? AND ts>=? AND ts<? AND open IS NOT NULL "\n            "ORDER BY ts", (SYMBOL, start, end)).fetchall()\n        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),\n                 float(r[4])) for r in rows]\n    finally:\n        if own:\n            conn.close()\n\n\ndef coverage(date_from: str, date_to: str) -> dict:\n    """Cheap coverage report for a date window (IST days)."""\n    cfg = StfcConfig(date_from=date_from, date_to=date_to)\n    start, end = _range_epochs(cfg)\n    conn = db()\n    try:\n        r = conn.execute(\n            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM perp_candles_1m "\n            "WHERE symbol=? AND ts>=? AND ts<?", (SYMBOL, start, end)).fetchone()\n        n, lo, hi = r[0], r[1], r[2]\n        # per-day row counts → find thin/missing days\n        days = conn.execute(\n            "SELECT (ts + 19800) / 86400 AS d, COUNT(*) FROM perp_candles_1m "\n            "WHERE symbol=? AND ts>=? AND ts<? GROUP BY d",\n            (SYMBOL, start, end)).fetchall()\n    finally:\n        conn.close()\n    have = {int(d): c for d, c in days}\n    thin, missing = [], []\n    if date_from and date_to:\n        d0 = dt.date.fromisoformat(date_from)\n        d1 = dt.date.fromisoformat(date_to)\n        cur = d0\n        while cur <= d1:\n            key = int(dt.datetime(cur.year, cur.month, cur.day,\n                                  tzinfo=IST).timestamp() + 19800) // 86400\n            c = have.get(key, 0)\n            if c == 0:\n                missing.append(cur.isoformat())\n            elif c < 1380:\n                thin.append({"date": cur.isoformat(), "rows": c})\n            cur += dt.timedelta(days=1)\n    return {\n        "rows": n,\n        "from": dt.datetime.fromtimestamp(lo, IST).isoformat() if lo else None,\n        "to": dt.datetime.fromtimestamp(hi, IST).isoformat() if hi else None,\n        "days_with_data": len(have),\n        "missing_days": missing[:60], "missing_count": len(missing),\n        "thin_days": thin[:60], "thin_count": len(thin),\n    }\n\n\n# ----------------------------------------------------------------------\n# Resample 1m → N-minute bars (epoch-aligned buckets, same as TradingView)\n# ----------------------------------------------------------------------\ndef resample(m1: list, tf_min: int) -> list:\n    """Returns list of dicts: ts (bucket start), o,h,l,c, and `subs` = list\n    of the 1m tuples inside the bucket (ascending)."""\n    if tf_min == 1:\n        return [dict(ts=t, o=o, h=h, l=l, c=c, subs=[(t, o, h, l, c)])\n                for (t, o, h, l, c) in m1]\n    width = tf_min * 60\n    out = []\n    cur = None\n    for row in m1:\n        t, o, h, l, c = row\n        b = t - (t % width)\n        if cur is None or cur["ts"] != b:\n            if cur is not None:\n                out.append(cur)\n            cur = dict(ts=b, o=o, h=h, l=l, c=c, subs=[row])\n        else:\n            cur["h"] = max(cur["h"], h)\n            cur["l"] = min(cur["l"], l)\n            cur["c"] = c\n            cur["subs"].append(row)\n    if cur is not None:\n        out.append(cur)\n    return out\n\n\n# ----------------------------------------------------------------------\n# SuperTrend — bit-for-bit port of TradingView\'s ta.supertrend()\n#   ATR = RMA(TR, len) seeded with SMA of the first `len` TRs (ta.rma).\n#   direction: -1 = up-trend (price above line), +1 = down-trend.\n# ----------------------------------------------------------------------\ndef supertrend(bars: list, length: int, mult: float) -> list:\n    """Returns list of (line, direction) per bar; direction None until ATR\n    exists (mirrors Pine na)."""\n    n = len(bars)\n    out = [None] * n\n    tr_sum = 0.0\n    atr_prev = None\n    prev_close = None\n    prev_upper = 0.0\n    prev_lower = 0.0\n    prev_st = None\n    direction = 1\n    for i, b in enumerate(bars):\n        h, l, c = b["h"], b["l"], b["c"]\n        tr = (h - l) if prev_close is None else max(\n            h - l, abs(h - prev_close), abs(l - prev_close))\n        # RMA with SMA seed\n        if i < length - 1:\n            tr_sum += tr\n            atr = None\n        elif i == length - 1:\n            tr_sum += tr\n            atr = tr_sum / length\n        else:\n            atr = (atr_prev * (length - 1) + tr) / length\n        atr_was_na = atr_prev is None\n        if atr is None:\n            out[i] = (None, None)\n            prev_close = c\n            continue\n        src = (h + l) / 2.0\n        upper = src + mult * atr\n        lower = src - mult * atr\n        pc = prev_close if prev_close is not None else c\n        lower = lower if (lower > prev_lower or pc < prev_lower) else prev_lower\n        upper = upper if (upper < prev_upper or pc > prev_upper) else prev_upper\n        if atr_was_na:\n            direction = 1\n        elif prev_st == prev_upper:\n            direction = -1 if c > upper else 1\n        else:\n            direction = 1 if c < lower else -1\n        st = lower if direction == -1 else upper\n        out[i] = (st, direction)\n        prev_upper, prev_lower, prev_st = upper, lower, st\n        atr_prev = atr\n        prev_close = c\n    return out\n\n\n# ----------------------------------------------------------------------\n# Signal engine: flip → confirm → armed candle (index list + direction)\n# ----------------------------------------------------------------------\ndef signals(bars: list, st: list, trade_long: bool, trade_short: bool) -> list:\n    """Returns list of (trade_bar_index, dir, flip_bar_index, confirm_bar_index).\n    State machine is a 1:1 port of the Pine script."""\n    out = []\n    pend = 0\n    armed = False\n    armed_dir = 0\n    flip_i = -1\n    for i, b in enumerate(bars):\n        # 1) execute (the bar armed by the previous bar)\n        if armed:\n            out.append((i, armed_dir, flip_i, i - 1))\n        armed = False\n        armed_dir = 0\n        d = st[i][1]\n        dprev = st[i - 1][1] if i > 0 else None\n        if d is None or dprev is None:\n            continue\n        is_up, was_up = d < 0, dprev < 0\n        flip_up = is_up and not was_up\n        flip_dn = (not is_up) and was_up\n        green = b["c"] > b["o"]\n        red = b["c"] < b["o"]\n        # 2) flip (C1 ignored) / 3) confirmation → arm next bar\n        if flip_up:\n            pend = 1 if trade_long else 0\n            flip_i = i\n        elif flip_dn:\n            pend = -1 if trade_short else 0\n            flip_i = i\n        elif pend == 1 and green:\n            armed, armed_dir, pend = True, 1, 0\n        elif pend == -1 and red:\n            armed, armed_dir, pend = True, -1, 0\n    return out\n\n\n# ----------------------------------------------------------------------\n# Trade execution on the armed candle (1m SL walk + costs)\n# ----------------------------------------------------------------------\ndef execute(bar: dict, direction: int, sl_usd: float, spread_usd: float,\n            fee_pct_side: float, size_btc: float) -> dict:\n    entry = bar["o"]\n    exit_p = bar["c"]\n    exit_ts = bar["subs"][-1][0] if bar["subs"] else bar["ts"]\n    sl_hit = False\n    sl_price = None\n    if sl_usd > 0:\n        sl_price = entry - sl_usd if direction == 1 else entry + sl_usd\n        for (t, o, h, l, c) in bar["subs"]:\n            if (direction == 1 and l <= sl_price) or \\\n               (direction == -1 and h >= sl_price):\n                sl_hit = True\n                exit_p = sl_price\n                exit_ts = t\n                break\n    move = (exit_p - entry) * direction\n    gross = move * size_btc\n    spread = spread_usd * size_btc\n    fees = (entry + exit_p) * size_btc * fee_pct_side / 100.0\n    net = gross - spread - fees\n    return dict(ts=bar["ts"], exit_ts=exit_ts, dir=direction,\n                entry=entry, exit=exit_p, high=bar["h"], low=bar["l"],\n                sl_price=sl_price, sl_hit=sl_hit, move=move,\n                gross=round(gross, 4), spread=round(spread, 4),\n                fees=round(fees, 4), net=round(net, 4))\n\n\n# ----------------------------------------------------------------------\n# Summary / scoreboard\n# ----------------------------------------------------------------------\ndef _ist(ts: int) -> dt.datetime:\n    return dt.datetime.fromtimestamp(ts, IST)\n\n\ndef summarize(trades: list, cell: dict) -> dict:\n    n = len(trades)\n    net = sum(t["net"] for t in trades)\n    gross = sum(t["gross"] for t in trades)\n    spread = sum(t["spread"] for t in trades)\n    fees = sum(t["fees"] for t in trades)\n    wins = [t for t in trades if t["net"] > 0]\n    losses = [t for t in trades if t["net"] <= 0]\n    longs = [t for t in trades if t["dir"] == 1]\n    shorts = [t for t in trades if t["dir"] == -1]\n    eq = peak = 0.0\n    max_dd = 0.0\n    cur_streak = worst_streak = 0\n    for t in trades:\n        eq += t["net"]\n        peak = max(peak, eq)\n        max_dd = max(max_dd, peak - eq)\n        if t["net"] > 0:\n            cur_streak = 0\n        else:\n            cur_streak += 1\n            worst_streak = max(worst_streak, cur_streak)\n    # yearly / monthly / daily buckets (IST calendar)\n    years, months, days = {}, {}, set()\n    for t in trades:\n        d = _ist(t["ts"])\n        years[d.year] = years.get(d.year, 0.0) + t["net"]\n        mk = f"{d.year}-{d.month:02d}"\n        months[mk] = months.get(mk, 0.0) + t["net"]\n        days.add(d.date())\n    month_list = [{"month": k, "net": round(v, 2)} for k, v in sorted(months.items())]\n    consec_neg = cur = 0\n    for m in month_list:\n        cur = cur + 1 if m["net"] < 0 else 0\n        consec_neg = max(consec_neg, cur)\n    year_list = [{"year": y, "net": round(v, 2)} for y, v in sorted(years.items())]\n    sum_win = sum(t["net"] for t in wins)\n    sum_loss = -sum(t["net"] for t in losses)\n    return {\n        "strategy": STRATEGY_TAG, "cell": cell,\n        "trades": n, "wins": len(wins), "losses": len(losses),\n        "win_rate": (len(wins) / n) if n else 0.0,\n        "longs": len(longs), "shorts": len(shorts),\n        "long_net": round(sum(t["net"] for t in longs), 2),\n        "short_net": round(sum(t["net"] for t in shorts), 2),\n        "long_win_rate": (sum(1 for t in longs if t["net"] > 0) / len(longs)) if longs else 0.0,\n        "short_win_rate": (sum(1 for t in shorts if t["net"] > 0) / len(shorts)) if shorts else 0.0,\n        "sl_hits": sum(1 for t in trades if t["sl_hit"]),\n        "gross": round(gross, 2), "spread": round(spread, 2),\n        "fees": round(fees, 2), "net": round(net, 2),\n        "avg_win": round(sum_win / len(wins), 2) if wins else 0.0,\n        "avg_loss": round(sum_loss / len(losses), 2) if losses else 0.0,\n        "profit_factor": round(sum_win / sum_loss, 3) if sum_loss > 0 else None,\n        "best": round(max((t["net"] for t in trades), default=0.0), 2),\n        "worst": round(min((t["net"] for t in trades), default=0.0), 2),\n        "max_dd": round(max_dd, 2),\n        "net_over_dd": round(net / max_dd, 2) if max_dd > 0 else None,\n        "max_consec_losses": worst_streak,\n        "trading_days": len(days),\n        "net_per_day": round(net / len(days), 2) if days else 0.0,\n        "years": year_list,\n        "years_positive": sum(1 for y in year_list if y["net"] > 0),\n        "years_total": len(year_list),\n        "months": month_list,\n        "months_positive": sum(1 for m in month_list if m["net"] > 0),\n        "months_total": len(month_list),\n        "worst_month": round(min((m["net"] for m in month_list), default=0.0), 2),\n        "max_consec_neg_months": consec_neg,\n        # compat keys used by the shared run-history list\n        "net_usd": round(net, 2), "traded": n,\n    }\n\n\ndef score_key(s: dict) -> tuple:\n    """House priority: yearly signs → worst month → consec losing months →\n    positive months → drawdown → net. Larger tuple = better."""\n    return (s["years_positive"] - s["years_total"],      # 0 when all positive\n            s["worst_month"],\n            -s["max_consec_neg_months"],\n            s["months_positive"],\n            -s["max_dd"],\n            s["net"])\n\n\n# ----------------------------------------------------------------------\n# Run\n# ----------------------------------------------------------------------\ndef run_stfc_backtest(cfg: StfcConfig,\n                      progress_cb: Optional[Callable[[dict], None]] = None,\n                      cancel: Optional[threading.Event] = None) -> dict:\n    t0 = time.time()\n    start, end = _range_epochs(cfg)\n    m1 = load_1m(start, end)\n    if len(m1) < 1000:\n        raise ValueError(\n            f"only {len(m1)} 1m candles in range — pull the perp corpus for "\n            f"this window first")\n    cells = cfg.cells()\n    total = len(cells)\n    results = []\n    primary_trades = None\n    done = 0\n    # cache per tf and per (tf,len,mult)\n    bars_cache = {}\n    sig_cache = {}\n    for cell in cells:\n        if cancel is not None and cancel.is_set():\n            break\n        tf = cell["tf_min"]\n        if tf not in bars_cache:\n            bars_cache[tf] = resample(m1, tf)\n        bars = bars_cache[tf]\n        sk = (tf, cell["st_len"], cell["st_mult"])\n        if sk not in sig_cache:\n            st = supertrend(bars, cell["st_len"], cell["st_mult"])\n            sig_cache[sk] = signals(bars, st, cfg.trade_long, cfg.trade_short)\n        trades = [execute(bars[i], d, cell["sl_usd"], cfg.spread_usd,\n                          cfg.fee_pct_side, cfg.size_btc)\n                  for (i, d, _f, _c) in sig_cache[sk]]\n        summ = summarize(trades, cell)\n        results.append(summ)\n        if primary_trades is None:\n            primary_trades = trades\n        done += 1\n        if progress_cb:\n            progress_cb({"phase": "cells", "done": done, "total": total,\n                         "cell": cell, "trades": len(trades)})\n    # rank\n    order = sorted(range(len(results)), key=lambda i: score_key(results[i]),\n                   reverse=True)\n    for rank, i in enumerate(order, 1):\n        results[i]["rank"] = rank\n    best = results[order[0]] if results else None\n    run_id = str(uuid.uuid4())\n    params = asdict(cfg)\n    params["strategy"] = STRATEGY_TAG\n    params["cells"] = total\n    summary = {\n        "strategy": STRATEGY_TAG,\n        "cells": results,\n        "best": best,\n        "primary": results[0] if results else None,\n        "m1_rows": len(m1),\n        "range_from": _ist(m1[0][0]).isoformat(),\n        "range_to": _ist(m1[-1][0]).isoformat(),\n        "elapsed_s": round(time.time() - t0, 1),\n        # compat keys (history list)\n        "net_usd": (results[0]["net"] if results else 0.0),\n        "traded": (results[0]["trades"] if results else 0),\n        "win_rate": (results[0]["win_rate"] if results else 0.0),\n    }\n    stored = (primary_trades or [])[-MAX_TRADES_STORED:]\n    conn = db()\n    try:\n        conn.execute("INSERT INTO lab_runs VALUES(?,?,?,?,?)",\n                     (run_id, int(time.time()), json.dumps(params),\n                      json.dumps(summary), json.dumps(stored)))\n        conn.commit()\n    finally:\n        conn.close()\n    return {"run_id": run_id, "params": params, "summary": summary,\n            "trades": stored}\n\n\ndef list_runs(limit: int = 50, strategy: str = "IC") -> list:\n    """Shared lab_runs table; rows without a strategy key are legacy IC."""\n    conn = db()\n    try:\n        rows = conn.execute(\n            "SELECT run_id, created_at, params_json, summary_json FROM lab_runs "\n            "ORDER BY created_at DESC LIMIT ?", (limit * 4,)).fetchall()\n    finally:\n        conn.close()\n    out = []\n    for r in rows:\n        p = json.loads(r[2])\n        tag = p.get("strategy", "IC")\n        if tag != strategy:\n            continue\n        out.append({"run_id": r[0], "created_at": r[1], "params": p,\n                    "summary": json.loads(r[3])})\n        if len(out) >= limit:\n            break\n    return out\n\n\nCSV_COLS = ["ts_ist", "exit_ts_ist", "dir", "entry", "exit", "high", "low",\n            "sl_price", "sl_hit", "move", "gross", "spread", "fees", "net"]\n\n\ndef trades_for_csv(trades: list) -> list:\n    out = []\n    for t in trades:\n        row = dict(t)\n        row["ts_ist"] = _ist(t["ts"]).strftime("%Y-%m-%d %H:%M")\n        row["exit_ts_ist"] = _ist(t["exit_ts"]).strftime("%Y-%m-%d %H:%M")\n        row["dir"] = "LONG" if t["dir"] == 1 else "SHORT"\n        out.append(row)\n    return out\n# ── CRYPTO_STFC_20260907 END ──\n'
PAGE_SRC = '// frontend/src/pages/CryptoStfcLab.jsx\n//\n// ── CRYPTO_STFC_20260907 ── STFC (SuperTrend Flip-Confirm) lab on the\n// BTCUSD perp corpus. Rendered inside CryptoLab.jsx via the lab tab switch.\n//\n//   * Corpus panel — pull BTCUSD 1m candles for a date window (IST days),\n//     coverage check (missing / thin days), cancel (resumable, day-level).\n//   * Config panel — timeframe, SuperTrend len/mult, SL $, spread $, taker\n//     fee %/side, size (BTC), sides, date range; optional GRID lists for\n//     tf / len / mult / SL (comma separated) → scoreboard.\n//   * Results — scoreboard ranked by the house priority (yearly signs →\n//     worst month → consec losing months → positive months → DD → net),\n//     cell detail with yearly sign strip, monthly grid, equity curve and\n//     trades (primary cell only carries trades), client-side CSV.\n//\n// Backend is the source of truth for jobs/runs (rehydrated on mount).\n// No window.confirm/alert (blocked in Tauri webview).\n\nimport React, { useCallback, useEffect, useMemo, useRef, useState } from "react";\nimport { getApiBase } from "../api/base";\nimport { colors, spacing, typography } from "../tokens";\n\nconst LS_KEY = "crypto_stfc_params_v1";\n\nfunction isoDaysAgo(n) {\n  const d = new Date(Date.now() - n * 86400000);\n  return d.toISOString().slice(0, 10);\n}\n\nconst DEFAULT_PARAMS = {\n  tf_min: 3, st_len: 10, st_mult: 2.0, sl_usd: 0, spread_usd: 0,\n  fee_pct_side: 0.05, size_btc: 1, trade_long: true, trade_short: true,\n  date_from: isoDaysAgo(365), date_to: isoDaysAgo(0),\n  grid_on: false, tf_text: "3,5,15", len_text: "7,10,14",\n  mult_text: "1.5,2,3", sl_text: "0,100,200",\n};\n\nfunction loadParams() {\n  try {\n    const raw = localStorage.getItem(LS_KEY);\n    return raw ? { ...DEFAULT_PARAMS, ...JSON.parse(raw) } : { ...DEFAULT_PARAMS };\n  } catch { return { ...DEFAULT_PARAMS }; }\n}\nfunction saveParams(p) {\n  try { localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch { /* ignore */ }\n}\nfunction parseList(txt, isFloat) {\n  return String(txt || "").split(/[,\\s]+/).map((s) => s.trim()).filter(Boolean)\n    .map((s) => (isFloat ? parseFloat(s) : parseInt(s, 10))).filter((v) => !Number.isNaN(v));\n}\n\n/* ── tiny local UI kit (mirrors CryptoLab.jsx; not exported there) ── */\nfunction Card({ children, style }) {\n  return (\n    <div style={{\n      background: colors.bg.secondary, border: `1px solid ${colors.border.light}`,\n      borderRadius: 8, padding: 16, boxShadow: "0 1px 3px var(--c-shadow)", ...style,\n    }}>{children}</div>\n  );\n}\nfunction Field({ label, children, hint, w }) {\n  return (\n    <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: w || 110 }}>\n      <span style={{ ...typography.label, color: colors.text.muted, fontSize: 11 }}>{label}</span>\n      {children}\n      {hint ? <span style={{ fontSize: 10, color: colors.text.tertiary }}>{hint}</span> : null}\n    </label>\n  );\n}\nconst inputStyle = {\n  padding: "7px 10px", borderRadius: 6, border: `1px solid ${colors.border.light}`,\n  background: colors.bg.secondary, color: colors.text.primary, fontSize: 13,\n  outline: "none", fontFamily: "var(--c-font-ui)",\n};\nfunction Btn({ children, onClick, disabled, danger, primary, small }) {\n  return (\n    <button onClick={onClick} disabled={disabled} style={{\n      padding: small ? "5px 10px" : "8px 16px", borderRadius: 6,\n      fontSize: small ? 12 : 13, fontWeight: 600,\n      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,\n      border: `1px solid ${danger ? colors.danger : primary ? colors.primary : colors.border.light}`,\n      background: danger ? "rgba(239,68,68,0.12)" : primary ? colors.primaryBg : colors.bg.tertiary,\n      color: danger ? colors.danger : primary ? colors.primary : colors.text.primary,\n    }}>{children}</button>\n  );\n}\nfunction StatCard({ label, value, sub, tone }) {\n  const col = tone === "good" ? colors.success : tone === "bad" ? colors.danger\n    : tone === "warn" ? colors.warning : colors.text.primary;\n  return (\n    <div style={{\n      background: colors.bg.tertiary, borderRadius: 8, padding: "10px 14px",\n      border: `1px solid ${colors.border.light}`, minWidth: 130,\n    }}>\n      <div style={{ fontSize: 10, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>\n      <div style={{ fontSize: 18, fontWeight: 700, color: col, marginTop: 2 }}>{value}</div>\n      {sub ? <div style={{ fontSize: 10, color: colors.text.tertiary, marginTop: 2 }}>{sub}</div> : null}\n    </div>\n  );\n}\nfunction usd(v, d = 2) {\n  if (v === null || v === undefined) return "—";\n  const s = v < 0 ? "-" : "";\n  return `${s}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: d })}`;\n}\nconst pnlCol = (v) => (v > 0 ? colors.success : v < 0 ? colors.danger : colors.text.primary);\nconst pct = (v) => `${(100 * (v || 0)).toFixed(1)}%`;\n\nfunction EquityCurve({ trades }) {\n  const pts = useMemo(() => { let eq = 0; return trades.map((t) => { eq += t.net; return eq; }); }, [trades]);\n  if (!pts.length) return null;\n  const W = 860, H = 220, PAD = 34;\n  const min = Math.min(0, ...pts), max = Math.max(0, ...pts);\n  const span = max - min || 1;\n  const x = (i) => PAD + (i / Math.max(1, pts.length - 1)) * (W - 2 * PAD);\n  const y = (v) => H - PAD - ((v - min) / span) * (H - 2 * PAD);\n  const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");\n  const last = pts[pts.length - 1];\n  return (\n    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>\n      <line x1={PAD} y1={y(0)} x2={W - PAD} y2={y(0)} stroke={colors.border.light} strokeDasharray="4 4" />\n      <path d={path} fill="none" stroke={pnlCol(last)} strokeWidth="1.8" />\n      <text x={PAD} y={14} fill={colors.text.muted} fontSize="10">cumulative net USD · primary cell · {trades.length} trades</text>\n      <text x={W - PAD} y={14} fill={pnlCol(last)} fontSize="11" textAnchor="end" fontWeight="700">{usd(last)}</text>\n    </svg>\n  );\n}\n\n/* yearly sign strip */\nfunction YearStrip({ years }) {\n  if (!years?.length) return null;\n  return (\n    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>\n      {years.map((y) => (\n        <div key={y.year} style={{\n          padding: "6px 10px", borderRadius: 6, minWidth: 84, textAlign: "center",\n          background: y.net > 0 ? colors.successBg : colors.dangerBg,\n          border: `1px solid ${y.net > 0 ? colors.success : colors.danger}`,\n        }}>\n          <div style={{ fontSize: 11, color: colors.text.muted }}>{y.year}</div>\n          <div style={{ fontSize: 13, fontWeight: 700, color: pnlCol(y.net) }}>{usd(y.net, 0)}</div>\n        </div>\n      ))}\n    </div>\n  );\n}\n\n/* monthly grid: rows = years, cols = Jan..Dec */\nfunction MonthGrid({ months }) {\n  const byYear = useMemo(() => {\n    const m = {};\n    (months || []).forEach((r) => { const [y, mo] = r.month.split("-"); (m[y] = m[y] || {})[parseInt(mo, 10)] = r.net; });\n    return m;\n  }, [months]);\n  const years = Object.keys(byYear).sort();\n  if (!years.length) return null;\n  const maxAbs = Math.max(1, ...(months || []).map((r) => Math.abs(r.net)));\n  const cell = (v) => {\n    if (v === undefined) return { background: "transparent", color: colors.text.tertiary };\n    const a = Math.min(0.85, 0.15 + 0.7 * (Math.abs(v) / maxAbs));\n    return { background: v >= 0 ? `rgba(34,197,94,${a})` : `rgba(239,68,68,${a})`, color: "#fff" };\n  };\n  return (\n    <table style={{ borderCollapse: "collapse", fontSize: 11, width: "100%" }}>\n      <thead><tr>\n        <th style={{ textAlign: "left", padding: 4, color: colors.text.muted }}>Year</th>\n        {["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map((m) =>\n          <th key={m} style={{ padding: 4, color: colors.text.muted, fontWeight: 500 }}>{m}</th>)}\n        <th style={{ padding: 4, color: colors.text.muted }}>Year</th>\n      </tr></thead>\n      <tbody>{years.map((y) => {\n        const tot = Object.values(byYear[y]).reduce((a, b) => a + b, 0);\n        return (\n          <tr key={y}>\n            <td style={{ padding: 4, fontWeight: 700 }}>{y}</td>\n            {Array.from({ length: 12 }, (_, i) => i + 1).map((mo) => {\n              const v = byYear[y][mo];\n              return <td key={mo} style={{ padding: "5px 4px", textAlign: "center", borderRadius: 3, ...cell(v) }}>\n                {v === undefined ? "·" : usd(v, 0)}</td>;\n            })}\n            <td style={{ padding: 4, textAlign: "right", fontWeight: 700, color: pnlCol(tot) }}>{usd(tot, 0)}</td>\n          </tr>\n        );\n      })}</tbody>\n    </table>\n  );\n}\n\nconst cellLabel = (c) => `${c.tf_min}m · ST(${c.st_len},${c.st_mult}) · SL ${c.sl_usd > 0 ? "$" + c.sl_usd : "off"}`;\n\n/* ─────────────────────────────────────────────────────────────────── */\nexport default function CryptoStfcLab() {\n  const api = getApiBase();\n  const [params, setParams] = useState(loadParams);\n  const [pull, setPull] = useState({ date_from: isoDaysAgo(365), date_to: isoDaysAgo(0), pace_s: 0.35, force: false });\n  const [cJob, setCJob] = useState(null);\n  const [cStats, setCStats] = useState(null);\n  const [cov, setCov] = useState(null);\n  const [rJob, setRJob] = useState(null);\n  const [run, setRun] = useState(null);          // loaded run detail\n  const [selCell, setSelCell] = useState(0);\n  const [runs, setRuns] = useState([]);\n  const [err, setErr] = useState("");\n  const [note, setNote] = useState("");\n  const pollRef = useRef(null);\n  const lastRunId = useRef(null);\n\n  const upd = (k, v) => setParams((p) => { const n = { ...p, [k]: v }; saveParams(n); return n; });\n\n  const refreshRuns = useCallback(async () => {\n    try {\n      const j = await fetch(`${api}/api/backtest/crypto/runs?limit=30&strategy=STFC`).then((r) => r.json());\n      setRuns(j.runs || []);\n    } catch { /* ignore */ }\n  }, [api]);\n\n  const loadRun = useCallback(async (runId) => {\n    try {\n      const r = await fetch(`${api}/api/backtest/crypto/runs/${runId}`);\n      if (!r.ok) throw new Error(`HTTP ${r.status}`);\n      const j = await r.json();\n      setRun(j); setSelCell(0); setErr("");\n    } catch (e) { setErr(`load run: ${e.message}`); }\n  }, [api]);\n\n  const poll = useCallback(async () => {\n    try {\n      const [c, r] = await Promise.all([\n        fetch(`${api}/api/backtest/crypto/corpus/status`).then((x) => x.json()),\n        fetch(`${api}/api/backtest/crypto/run/status`).then((x) => x.json()),\n      ]);\n      setCJob(c.job); setCStats(c.stats); setRJob(r);\n      if (!r.running && r.run_id && r.run_id !== lastRunId.current) {\n        lastRunId.current = r.run_id;\n        if (r.result?.params?.strategy === "STFC") { setRun(r.result); setSelCell(0); refreshRuns(); }\n      }\n      if (r.error) setErr(`run: ${r.error}`);\n      if (c.job?.error) setErr(`corpus: ${c.job.error}`);\n    } catch { /* offline — keep last state */ }\n  }, [api, refreshRuns]);\n\n  useEffect(() => {\n    poll(); refreshRuns();\n    pollRef.current = setInterval(poll, 2000);\n    return () => clearInterval(pollRef.current);\n  }, [poll, refreshRuns]);\n\n  const collecting = !!cJob?.running;\n  const running = !!rJob?.running;\n\n  const checkCoverage = async () => {\n    try {\n      const j = await fetch(`${api}/api/backtest/crypto/perp/coverage?date_from=${pull.date_from}&date_to=${pull.date_to}`).then((r) => r.json());\n      setCov(j); setErr("");\n    } catch (e) { setErr(`coverage: ${e.message}`); }\n  };\n  const startPull = async () => {\n    setErr(""); setNote("");\n    try {\n      const r = await fetch(`${api}/api/backtest/crypto/perp/pull/start`, {\n        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pull) });\n      const j = await r.json();\n      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);\n      poll();\n    } catch (e) { setErr(`pull: ${e.message}`); }\n  };\n  const cancelPull = async () => {\n    try { await fetch(`${api}/api/backtest/crypto/corpus/backfill/cancel`, { method: "POST" }); poll(); }\n    catch (e) { setErr(`cancel: ${e.message}`); }\n  };\n\n  const buildBody = () => {\n    const b = {\n      tf_min: Number(params.tf_min), st_len: Number(params.st_len), st_mult: Number(params.st_mult),\n      sl_usd: Number(params.sl_usd), spread_usd: Number(params.spread_usd),\n      fee_pct_side: Number(params.fee_pct_side), size_btc: Number(params.size_btc),\n      trade_long: !!params.trade_long, trade_short: !!params.trade_short,\n      date_from: params.date_from || "", date_to: params.date_to || "",\n      tf_list: [], st_len_list: [], st_mult_list: [], sl_list: [],\n    };\n    if (params.grid_on) {\n      b.tf_list = parseList(params.tf_text, false);\n      b.st_len_list = parseList(params.len_text, false);\n      b.st_mult_list = parseList(params.mult_text, true);\n      b.sl_list = parseList(params.sl_text, true);\n    }\n    return b;\n  };\n  const gridCells = useMemo(() => {\n    if (!params.grid_on) return 1;\n    const n = (t, f) => Math.max(1, parseList(t, f).length);\n    return n(params.tf_text) * n(params.len_text) * n(params.mult_text, true) * n(params.sl_text, true);\n  }, [params]);\n\n  const startRun = async () => {\n    setErr(""); setNote("");\n    try {\n      const r = await fetch(`${api}/api/backtest/crypto/stfc/run/start`, {\n        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildBody()) });\n      const j = await r.json();\n      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);\n      if (j.corpus_busy) setNote("Corpus pull is running — results are non-authoritative until it finishes.");\n      poll();\n    } catch (e) { setErr(`run: ${e.message}`); }\n  };\n  const cancelRun = async () => {\n    try { await fetch(`${api}/api/backtest/crypto/run/cancel`, { method: "POST" }); poll(); }\n    catch (e) { setErr(`cancel: ${e.message}`); }\n  };\n\n  /* client-side CSV (Backtest.jsx convention — blob anchor, no window.open) */\n  const downloadCsv = () => {\n    if (!run?.trades?.length) return;\n    const cols = ["ts_ist", "exit_ts_ist", "dir", "entry", "exit", "high", "low", "sl_price", "sl_hit", "move", "gross", "spread", "fees", "net"];\n    const ist = (ts) => new Date(ts * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });\n    const rows = run.trades.map((t) => ({ ...t, ts_ist: ist(t.ts), exit_ts_ist: ist(t.exit_ts), dir: t.dir === 1 ? "LONG" : "SHORT" }));\n    const esc = (v) => (v === null || v === undefined ? "" : `"${String(v).replace(/"/g, \'""\')}"`);\n    const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\\n");\n    const blob = new Blob([csv], { type: "text/csv" });\n    const a = document.createElement("a");\n    a.href = URL.createObjectURL(blob); a.download = `stfc_${run.run_id.slice(0, 8)}.csv`;\n    document.body.appendChild(a); a.click(); document.body.removeChild(a);\n    setTimeout(() => URL.revokeObjectURL(a.href), 2000);\n    setNote(`CSV saved: stfc_${run.run_id.slice(0, 8)}.csv (${rows.length} trades)`);\n  };\n\n  const cells = run?.summary?.cells || [];\n  const ranked = useMemo(() => [...cells].sort((a, b) => (a.rank || 0) - (b.rank || 0)), [cells]);\n  const cell = cells[selCell] || null;\n  const isPrimary = selCell === 0;\n\n  return (\n    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>\n      {err ? <Card style={{ borderColor: colors.danger, color: colors.danger, fontSize: 13 }}>{err}</Card> : null}\n      {note ? <Card style={{ borderColor: colors.warning, color: colors.warning, fontSize: 13 }}>{note}</Card> : null}\n\n      {/* ── Corpus: perp window pull ── */}\n      <Card>\n        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>\n          <div style={{ ...typography.h2, fontSize: 15 }}>BTCUSD perp corpus (1m candles)</div>\n          <div style={{ fontSize: 11, color: colors.text.tertiary }}>\n            {cStats && !cStats.error\n              ? `${(cStats.perp_rows || 0).toLocaleString()} rows · ${cStats.perp_from || "—"} → ${cStats.perp_to || "—"} · ${cStats.db_size_mb ?? 0} MB · disk free ${cStats.disk_free_gb ?? "?"} GB`\n              : "stats unavailable"}\n          </div>\n        </div>\n        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>\n          <Field label="From (IST date)">\n            <input type="date" style={inputStyle} value={pull.date_from} onChange={(e) => setPull((f) => ({ ...f, date_from: e.target.value }))} />\n          </Field>\n          <Field label="To (IST date)">\n            <input type="date" style={inputStyle} value={pull.date_to} onChange={(e) => setPull((f) => ({ ...f, date_to: e.target.value }))} />\n          </Field>\n          <Field label="Pace (s/req)" hint="raise on HTTP 429s">\n            <input type="number" step="0.05" min="0.2" max="2" style={inputStyle} value={pull.pace_s} onChange={(e) => setPull((f) => ({ ...f, pace_s: Number(e.target.value) }))} />\n          </Field>\n          <Field label="Re-fetch full days" hint="normally off: complete days are skipped">\n            <select style={inputStyle} value={pull.force ? "1" : "0"} onChange={(e) => setPull((f) => ({ ...f, force: e.target.value === "1" }))}>\n              <option value="0">No (resume)</option><option value="1">Yes (force)</option>\n            </select>\n          </Field>\n          <Btn onClick={checkCoverage} disabled={collecting}>Check coverage</Btn>\n          {!collecting\n            ? <Btn primary onClick={startPull} disabled={running}>Pull window</Btn>\n            : <Btn danger onClick={cancelPull}>Cancel (resumable)</Btn>}\n          <Btn small onClick={() => { const n = { ...pull, date_from: params.date_from, date_to: params.date_to }; setPull(n); }}>\n            ← use backtest range\n          </Btn>\n        </div>\n        {collecting && cJob?.progress ? (\n          <div style={{ marginTop: 10 }}>\n            <div style={{ height: 8, borderRadius: 4, background: colors.bg.tertiary, overflow: "hidden" }}>\n              <div style={{ height: "100%", background: colors.primary, transition: "width 1s linear",\n                width: `${Math.min(100, (100 * (cJob.progress.done || 0)) / Math.max(1, cJob.progress.total || 1)).toFixed(1)}%` }} />\n            </div>\n            <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 4 }}>\n              {cJob.progress.phase} · day {cJob.progress.done}/{cJob.progress.total} · {cJob.progress.current || ""} · {(cJob.progress.rows || 0).toLocaleString()} rows inserted · {cJob.progress.skipped || 0} skipped · {cJob.progress.failed || 0} failed\n            </div>\n          </div>\n        ) : null}\n        {!collecting && cJob?.result?.kind === "perp_pull" ? (\n          <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 8 }}>\n            last pull: {cJob.result.done}/{cJob.result.total} days · {(cJob.result.rows || 0).toLocaleString()} rows · {cJob.result.skipped} skipped · {cJob.result.failed} failed{cJob.result.cancelled ? " · cancelled" : ""}\n          </div>\n        ) : null}\n        {cov ? (\n          <div style={{ fontSize: 12, marginTop: 8, color: colors.text.secondary }}>\n            <b>Coverage</b> {pull.date_from} → {pull.date_to}: {cov.rows?.toLocaleString()} rows · {cov.days_with_data} days with data ·{" "}\n            <span style={{ color: cov.missing_count ? colors.danger : colors.success }}>{cov.missing_count} missing</span> ·{" "}\n            <span style={{ color: cov.thin_count ? colors.warning : colors.success }}>{cov.thin_count} thin (&lt;1380 rows)</span>\n            {cov.missing_count ? <div style={{ fontSize: 11, color: colors.text.tertiary, marginTop: 2 }}>missing: {cov.missing_days.join(", ")}{cov.missing_count > cov.missing_days.length ? " …" : ""}</div> : null}\n          </div>\n        ) : null}\n      </Card>\n\n      {/* ── Config ── */}\n      <Card>\n        <div style={{ ...typography.h2, fontSize: 15, marginBottom: 10 }}>STFC config</div>\n        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>\n          <Field label="Timeframe (min)"><input type="number" min="1" max="240" style={inputStyle} value={params.tf_min} onChange={(e) => upd("tf_min", e.target.value)} /></Field>\n          <Field label="ST length"><input type="number" min="2" max="200" style={inputStyle} value={params.st_len} onChange={(e) => upd("st_len", e.target.value)} /></Field>\n          <Field label="ST factor"><input type="number" step="0.1" min="0.1" style={inputStyle} value={params.st_mult} onChange={(e) => upd("st_mult", e.target.value)} /></Field>\n          <Field label="Stop loss ($)" hint="0 = off · walked on 1m candles"><input type="number" min="0" step="10" style={inputStyle} value={params.sl_usd} onChange={(e) => upd("sl_usd", e.target.value)} /></Field>\n          <Field label="Spread ($/trade)"><input type="number" min="0" step="1" style={inputStyle} value={params.spread_usd} onChange={(e) => upd("spread_usd", e.target.value)} /></Field>\n          <Field label="Taker fee %/side" hint="Delta ≈ 0.05"><input type="number" min="0" step="0.01" style={inputStyle} value={params.fee_pct_side} onChange={(e) => upd("fee_pct_side", e.target.value)} /></Field>\n          <Field label="Size (BTC)" hint="P/L in USD for this size"><input type="number" min="0.001" step="0.001" style={inputStyle} value={params.size_btc} onChange={(e) => upd("size_btc", e.target.value)} /></Field>\n          <Field label="Sides">\n            <select style={inputStyle} value={params.trade_long && params.trade_short ? "both" : params.trade_long ? "long" : "short"}\n              onChange={(e) => { const v = e.target.value; upd("trade_long", v !== "short"); upd("trade_short", v !== "long"); }}>\n              <option value="both">Long + Short</option><option value="long">Long only</option><option value="short">Short only</option>\n            </select>\n          </Field>\n          <Field label="From"><input type="date" style={inputStyle} value={params.date_from} onChange={(e) => upd("date_from", e.target.value)} /></Field>\n          <Field label="To"><input type="date" style={inputStyle} value={params.date_to} onChange={(e) => upd("date_to", e.target.value)} /></Field>\n        </div>\n\n        <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px dashed ${colors.border.light}` }}>\n          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>\n            <input type="checkbox" checked={!!params.grid_on} onChange={(e) => upd("grid_on", e.target.checked)} />\n            <b>Grid sweep</b> <span style={{ color: colors.text.tertiary, fontSize: 11 }}>comma-separated lists; empty list falls back to the single value above · {gridCells} cell{gridCells === 1 ? "" : "s"} (max 400)</span>\n          </label>\n          {params.grid_on ? (\n            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>\n              <Field label="Timeframes" w={150}><input style={inputStyle} value={params.tf_text} onChange={(e) => upd("tf_text", e.target.value)} /></Field>\n              <Field label="ST lengths" w={150}><input style={inputStyle} value={params.len_text} onChange={(e) => upd("len_text", e.target.value)} /></Field>\n              <Field label="ST factors" w={150}><input style={inputStyle} value={params.mult_text} onChange={(e) => upd("mult_text", e.target.value)} /></Field>\n              <Field label="Stop losses ($)" w={150}><input style={inputStyle} value={params.sl_text} onChange={(e) => upd("sl_text", e.target.value)} /></Field>\n            </div>\n          ) : null}\n        </div>\n\n        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12 }}>\n          {!running\n            ? <Btn primary onClick={startRun} disabled={collecting}>Run backtest{gridCells > 1 ? ` (${gridCells} cells)` : ""}</Btn>\n            : <Btn danger onClick={cancelRun}>Cancel run</Btn>}\n          {running && rJob?.progress ? (\n            <span style={{ fontSize: 12, color: colors.text.tertiary }}>\n              cell {rJob.progress.done}/{rJob.progress.total} · {rJob.progress.cell ? cellLabel(rJob.progress.cell) : ""} · {rJob.progress.trades} trades\n            </span>\n          ) : null}\n          {collecting ? <span style={{ fontSize: 12, color: colors.warning }}>corpus pull running — wait for it before running</span> : null}\n        </div>\n      </Card>\n\n      {/* ── Scoreboard (grid runs) ── */}\n      {run && cells.length > 1 ? (\n        <Card>\n          <div style={{ ...typography.h2, fontSize: 15, marginBottom: 6 }}>Scoreboard · {cells.length} cells</div>\n          <div style={{ fontSize: 11, color: colors.text.tertiary, marginBottom: 8 }}>\n            ranked: yearly signs → worst month → consec. losing months → positive months → drawdown → net. Click a row for detail. Only cell #1 (first in grid order) carries trades/equity.\n          </div>\n          <div style={{ overflowX: "auto" }}>\n            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>\n              <thead><tr style={{ color: colors.text.muted }}>\n                {["#", "Cell", "Trades", "Win%", "Net", "Gross", "Costs", "MaxDD", "Net/DD", "Yrs +", "Worst mo", "Neg mo run", "Mo +", "SL hits"].map((h) =>\n                  <th key={h} style={{ textAlign: h === "Cell" ? "left" : "right", padding: "4px 6px", borderBottom: `1px solid ${colors.border.light}`, whiteSpace: "nowrap" }}>{h}</th>)}\n              </tr></thead>\n              <tbody>{ranked.map((c) => {\n                const idx = cells.indexOf(c);\n                const sel = idx === selCell;\n                const td = (v, col, left) => <td style={{ padding: "4px 6px", textAlign: left ? "left" : "right", color: col || colors.text.primary, whiteSpace: "nowrap" }}>{v}</td>;\n                return (\n                  <tr key={idx} onClick={() => setSelCell(idx)} style={{ cursor: "pointer", background: sel ? colors.primaryBg : "transparent", borderBottom: `1px solid ${colors.border.light}` }}>\n                    {td(c.rank)}{td(cellLabel(c.cell) + (idx === 0 ? "  ★" : ""), null, true)}{td(c.trades)}{td(pct(c.win_rate))}\n                    {td(usd(c.net, 0), pnlCol(c.net))}{td(usd(c.gross, 0), pnlCol(c.gross))}{td(usd(-(c.spread + c.fees), 0), colors.warning)}\n                    {td(usd(-c.max_dd, 0), colors.danger)}{td(c.net_over_dd ?? "—")}\n                    {td(`${c.years_positive}/${c.years_total}`, c.years_positive === c.years_total ? colors.success : colors.danger)}\n                    {td(usd(c.worst_month, 0), pnlCol(c.worst_month))}{td(c.max_consec_neg_months)}\n                    {td(`${c.months_positive}/${c.months_total}`)}{td(c.sl_hits)}\n                  </tr>\n                );\n              })}</tbody>\n            </table>\n          </div>\n        </Card>\n      ) : null}\n\n      {/* ── Cell detail ── */}\n      {run && cell ? (\n        <Card>\n          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>\n            <div>\n              <div style={{ ...typography.h2, fontSize: 15 }}>{cellLabel(cell.cell)}{cells.length > 1 ? ` · rank ${cell.rank}/${cells.length}` : ""}</div>\n              <div style={{ fontSize: 11, color: colors.text.tertiary }}>\n                {run.summary.range_from?.slice(0, 10)} → {run.summary.range_to?.slice(0, 10)} · {cell.trading_days} trading days · spread ${run.params.spread_usd} · fee {run.params.fee_pct_side}%/side · size {run.params.size_btc} BTC · {run.summary.m1_rows?.toLocaleString()} 1m rows · {run.summary.elapsed_s}s\n              </div>\n            </div>\n            <div style={{ display: "flex", gap: 6 }}>\n              {isPrimary && run.trades?.length ? <Btn small onClick={downloadCsv}>CSV ({run.trades.length})</Btn> : null}\n            </div>\n          </div>\n\n          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>\n            <StatCard label="Net" value={usd(cell.net)} sub={`${usd(cell.net_per_day)} / day`} tone={cell.net >= 0 ? "good" : "bad"} />\n            <StatCard label="Gross" value={usd(cell.gross)} sub={`spread ${usd(-cell.spread)} · fees ${usd(-cell.fees)}`} tone={cell.gross >= 0 ? "good" : "bad"} />\n            <StatCard label="Trades" value={cell.trades} sub={`L ${cell.longs} · S ${cell.shorts}`} />\n            <StatCard label="Win rate" value={pct(cell.win_rate)} sub={`L ${pct(cell.long_win_rate)} · S ${pct(cell.short_win_rate)}`} tone={cell.win_rate >= 0.5 ? "good" : "warn"} />\n            <StatCard label="Max DD" value={usd(-cell.max_dd)} sub={`net/DD ${cell.net_over_dd ?? "—"} · PF ${cell.profit_factor ?? "—"}`} tone="bad" />\n            <StatCard label="Avg win / loss" value={`${usd(cell.avg_win)} / ${usd(-cell.avg_loss)}`} sub={`best ${usd(cell.best)} · worst ${usd(cell.worst)}`} />\n            <StatCard label="SL hits" value={cell.sl_hits} sub={cell.trades ? pct(cell.sl_hits / cell.trades) : ""} tone={cell.sl_hits ? "warn" : undefined} />\n            <StatCard label="Months +" value={`${cell.months_positive}/${cell.months_total}`} sub={`worst ${usd(cell.worst_month, 0)} · neg run ${cell.max_consec_neg_months}`} tone={cell.months_positive > cell.months_total / 2 ? "good" : "bad"} />\n            <StatCard label="Years +" value={`${cell.years_positive}/${cell.years_total}`} sub={`max consec. losses ${cell.max_consec_losses}`} tone={cell.years_positive === cell.years_total ? "good" : "bad"} />\n            <StatCard label="Long / Short net" value={`${usd(cell.long_net, 0)} / ${usd(cell.short_net, 0)}`} />\n          </div>\n\n          <div style={{ marginTop: 14 }}><YearStrip years={cell.years} /></div>\n          <div style={{ marginTop: 12 }}><MonthGrid months={cell.months} /></div>\n\n          {isPrimary && run.trades?.length ? (\n            <>\n              <div style={{ marginTop: 14 }}><EquityCurve trades={run.trades} /></div>\n              <div style={{ marginTop: 10, fontSize: 11, color: colors.text.tertiary }}>last {Math.min(200, run.trades.length)} trades (IST)</div>\n              <div style={{ overflowX: "auto", maxHeight: 360, overflowY: "auto" }}>\n                <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}>\n                  <thead><tr style={{ color: colors.text.muted }}>\n                    {["Time", "Dir", "Entry", "Exit", "Low", "High", "SL", "Move", "Costs", "Net"].map((h) =>\n                      <th key={h} style={{ textAlign: "right", padding: "3px 6px", borderBottom: `1px solid ${colors.border.light}` }}>{h}</th>)}\n                  </tr></thead>\n                  <tbody>{run.trades.slice(-200).reverse().map((t) => (\n                    <tr key={t.ts} style={{ borderBottom: `1px solid ${colors.border.light}` }}>\n                      <td style={{ padding: "3px 6px", textAlign: "right", whiteSpace: "nowrap" }}>{new Date(t.ts * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right", color: t.dir === 1 ? colors.success : colors.danger, fontWeight: 600 }}>{t.dir === 1 ? "L" : "S"}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.entry.toFixed(1)}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.exit.toFixed(1)}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.low.toFixed(1)}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{t.high.toFixed(1)}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right", color: t.sl_hit ? colors.warning : colors.text.tertiary }}>{t.sl_hit ? "HIT" : "—"}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right", color: pnlCol(t.move) }}>{t.move.toFixed(1)}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right", color: colors.warning }}>{usd(-(t.spread + t.fees))}</td>\n                      <td style={{ padding: "3px 6px", textAlign: "right", color: pnlCol(t.net), fontWeight: 600 }}>{usd(t.net)}</td>\n                    </tr>\n                  ))}</tbody>\n                </table>\n              </div>\n            </>\n          ) : null}\n        </Card>\n      ) : null}\n\n      {/* ── History ── */}\n      <Card>\n        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>\n          <div style={{ ...typography.h2, fontSize: 15 }}>STFC run history</div>\n          <Btn small onClick={refreshRuns}>Refresh</Btn>\n        </div>\n        {!runs.length ? <div style={{ fontSize: 12, color: colors.text.tertiary }}>no runs yet</div> : (\n          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>\n            {runs.map((r) => {\n              const p = r.summary?.primary || {};\n              const b = r.summary?.best || {};\n              return (\n                <div key={r.run_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12,\n                  padding: "6px 10px", borderRadius: 6, background: run?.run_id === r.run_id ? colors.primaryBg : colors.bg.tertiary }}>\n                  <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>\n                    <span style={{ color: colors.text.tertiary }}>{new Date(r.created_at * 1000).toLocaleString("en-IN", { hour12: false })}</span>\n                    <span>{r.params.cells > 1 ? `grid ${r.params.cells} cells` : cellLabel(p.cell || {})}</span>\n                    <span style={{ color: colors.text.tertiary }}>{r.params.date_from || "…"} → {r.params.date_to || "…"}</span>\n                    <span style={{ fontWeight: 700, color: pnlCol(p.net) }}>{usd(p.net, 0)}</span>\n                    <span style={{ color: colors.text.tertiary }}>{p.trades} trades · win {pct(p.win_rate)} · yrs {p.years_positive}/{p.years_total}</span>\n                    {r.params.cells > 1 && b.cell ? <span style={{ color: colors.text.tertiary }}>best: {cellLabel(b.cell)} {usd(b.net, 0)}</span> : null}\n                  </div>\n                  <Btn small onClick={() => loadRun(r.run_id)}>Load</Btn>\n                </div>\n              );\n            })}\n          </div>\n        )}\n      </Card>\n    </div>\n  );\n}\n'

# ---------------------------------------------------------------- patches
# Patch fragments consumed by the apply script (kept separate for review).

CORPUS_APPEND = r'''

# ── CRYPTO_STFC_20260907 BEGIN ── date-window perp puller
# UI-configurable window (IST calendar dates). Resumable: a day that already
# has >= FULL_DAY_ROWS candles is skipped, so re-pulls/extensions are cheap.
# Transport errors are retried (bounded) and never counted as "done".
FULL_DAY_ROWS = 1380            # 1440 minus tolerable exchange gaps
PERP_MAX_RETRY = 3


def _day_epochs_ist(date_from: str, date_to: str):
    d0 = dt.date.fromisoformat(date_from)
    d1 = dt.date.fromisoformat(date_to)
    if d1 < d0:
        d0, d1 = d1, d0
    cur = d0
    while cur <= d1:
        s = int(dt.datetime(cur.year, cur.month, cur.day, tzinfo=IST).timestamp())
        yield cur.isoformat(), s, s + 86400
        cur += dt.timedelta(days=1)


def backfill_perp_range(date_from: str, date_to: str,
                        progress_cb: Optional[Callable[[dict], None]] = None,
                        cancel: Optional[threading.Event] = None,
                        pace_s: float = DEFAULT_PACE_S,
                        force: bool = False) -> dict:
    """Pull BTCUSD 1m candles for every IST day in [date_from, date_to]."""
    conn = db()
    try:
        now = int(time.time())
        days = list(_day_epochs_ist(date_from, date_to))
        total = len(days)
        done = skipped = failed = inserted = 0
        for label, s, e in days:
            if cancel is not None and cancel.is_set():
                break
            if s > now:
                skipped += 1
                done += 1
                continue
            if not force:
                have = conn.execute(
                    "SELECT COUNT(*) FROM perp_candles_1m WHERE symbol='BTCUSD' "
                    "AND ts>=? AND ts<?", (s, e)).fetchone()[0]
                if have >= FULL_DAY_ROWS:
                    skipped += 1
                    done += 1
                    if progress_cb and done % 5 == 0:
                        progress_cb({"phase": "perp", "done": done, "total": total,
                                     "current": label, "rows": inserted,
                                     "skipped": skipped, "failed": failed})
                    continue
            rows = None
            for _attempt in range(PERP_MAX_RETRY):
                rows = get_candles("BTCUSD", s, min(e, now), pace_s=pace_s)
                if rows is not None:
                    break
                time.sleep(1.0)
            if rows is None:
                failed += 1
            else:
                conn.executemany(
                    "INSERT OR IGNORE INTO perp_candles_1m "
                    "VALUES('BTCUSD',?,?,?,?,?,?)",
                    [(c["time"], c.get("open"), c.get("high"), c.get("low"),
                      c.get("close"), c.get("volume")) for c in rows])
                conn.commit()
                inserted += len(rows)
            done += 1
            if progress_cb and done % 2 == 0:
                progress_cb({"phase": "perp", "done": done, "total": total,
                             "current": label, "rows": inserted,
                             "skipped": skipped, "failed": failed})
        return {"done": done, "total": total, "rows": inserted,
                "skipped": skipped, "failed": failed}
    finally:
        conn.close()
# ── CRYPTO_STFC_20260907 END ──
'''

ROUTES_IMPORT_OLD = "from app.backtest.crypto import crypto_ic_engine as engine\n"
ROUTES_IMPORT_NEW = ("from app.backtest.crypto import crypto_ic_engine as engine\n"
                     "from app.backtest.crypto import crypto_stfc_engine as stfc   # ── CRYPTO_STFC_20260907 ──\n")

# /runs list: strategy filter so the two labs don't see each other's runs
ROUTES_RUNS_OLD = '''@crypto_router.get("/runs")
def runs_list(limit: int = 50):
    return {"runs": engine.list_runs(max(1, min(200, limit)))}
'''
ROUTES_RUNS_NEW = '''@crypto_router.get("/runs")
def runs_list(limit: int = 50, strategy: str = "IC"):
    # ── CRYPTO_STFC_20260907 ── shared lab_runs table; filter by strategy tag
    return {"runs": stfc.list_runs(max(1, min(200, limit)), strategy=strategy)}
'''

# CSV: STFC runs have their own columns
ROUTES_CSV_OLD = '''    r = engine.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    cols = ["expiry", "date", "weekday", "side", "spot",'''
ROUTES_CSV_NEW = '''    r = engine.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r["params"].get("strategy") == stfc.STRATEGY_TAG:      # ── CRYPTO_STFC_20260907 ──
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=stfc.CSV_COLS, extrasaction="ignore",
                           restval="")
        w.writeheader()
        w.writerows(stfc.trades_for_csv(r["trades"]))
        return PlainTextResponse(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="stfc_{run_id}.csv"'})
    cols = ["expiry", "date", "weekday", "side", "spot",'''

ROUTES_TAIL_OLD = "# ── CRYPTO_LAB END ──"
ROUTES_TAIL_NEW = r'''# ── CRYPTO_LAB END ──


# ── CRYPTO_STFC_20260907 BEGIN ── STFC (SuperTrend flip-confirm) on BTC perp
# Shares the corpus job slot and the run job slot with the options lab: one
# corpus job and one backtest run at a time, lab-wide.
class PerpPullRequest(BaseModel):
    date_from: str
    date_to: str
    pace_s: float = 0.35
    force: bool = False


class StfcRunRequest(BaseModel):
    tf_min: int = 3
    st_len: int = 10
    st_mult: float = 2.0
    sl_usd: float = 0.0
    spread_usd: float = 0.0
    fee_pct_side: float = 0.05
    size_btc: float = 1.0
    trade_long: bool = True
    trade_short: bool = True
    date_from: str = ""
    date_to: str = ""
    tf_list: list[int] = []
    st_len_list: list[int] = []
    st_mult_list: list[float] = []
    sl_list: list[float] = []


@crypto_router.get("/perp/coverage")
def perp_coverage(date_from: str = "", date_to: str = ""):
    try:
        return stfc.coverage(date_from, date_to)
    except ValueError as e:
        raise HTTPException(400, str(e))


@crypto_router.post("/perp/pull/start")
def perp_pull_start(req: PerpPullRequest):
    try:
        d0 = __import__("datetime").date.fromisoformat(req.date_from)
        d1 = __import__("datetime").date.fromisoformat(req.date_to)
    except ValueError:
        raise HTTPException(400, "dates must be YYYY-MM-DD")
    if (max(d0, d1) - min(d0, d1)).days > 366 * 6:
        raise HTTPException(400, "window too large (max 6 years per pull)")
    if not (0.2 <= req.pace_s <= 2.0):
        raise HTTPException(400, "pace_s must be 0.2..2.0")
    with _JOBS.lock:
        if _JOBS.corpus["running"]:
            raise HTTPException(409, "corpus job already running")
        if _JOBS.run["running"]:
            raise HTTPException(409, "a backtest run is active — wait for it")
        _JOBS.corpus_cancel = threading.Event()
        cancel = _JOBS.corpus_cancel
        _JOBS.corpus.update(running=True, progress=None, result=None,
                            error=None, started_at=time.time())

    def _cb(p):
        with _JOBS.lock:
            _JOBS.corpus["progress"] = p

    def _worker():
        try:
            res = corpus.backfill_perp_range(
                req.date_from, req.date_to, progress_cb=_cb, cancel=cancel,
                pace_s=req.pace_s, force=req.force)
            res["cancelled"] = cancel.is_set()
            res["kind"] = "perp_pull"
            with _JOBS.lock:
                _JOBS.corpus.update(running=False, result=res)
        except Exception as e:
            with _JOBS.lock:
                _JOBS.corpus.update(running=False, error=repr(e))

    threading.Thread(target=_worker, name="crypto-perp-pull",
                     daemon=True).start()
    return {"ok": True}


@crypto_router.post("/stfc/run/start")
def stfc_run_start(req: StfcRunRequest):
    try:
        cfg = stfc.StfcConfig.from_dict(req.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _JOBS.lock:
        if _JOBS.run["running"]:
            raise HTTPException(409, "a lab run is already active")
        corpus_busy = _JOBS.corpus["running"]
        _JOBS.run_cancel = threading.Event()
        cancel = _JOBS.run_cancel
        _JOBS.run.update(running=True, progress=None, result=None,
                         error=None, started_at=time.time(), run_id=None)

    def _cb(p):
        with _JOBS.lock:
            _JOBS.run["progress"] = p

    def _worker():
        try:
            res = stfc.run_stfc_backtest(cfg, progress_cb=_cb, cancel=cancel)
            res["corpus_busy_during_run"] = corpus_busy
            res["cancelled"] = cancel.is_set()
            with _JOBS.lock:
                _JOBS.run.update(running=False, result=res,
                                 run_id=res["run_id"])
        except Exception as e:
            with _JOBS.lock:
                _JOBS.run.update(running=False, error=repr(e))

    threading.Thread(target=_worker, name="crypto-stfc-run",
                     daemon=True).start()
    return {"ok": True, "corpus_busy": corpus_busy, "cells": len(cfg.cells())}
# ── CRYPTO_STFC_20260907 END ──'''


LAB_IMPORT_OLD = 'import { colors, spacing, typography } from "../tokens";\n'
LAB_IMPORT_NEW = ('import { colors, spacing, typography } from "../tokens";\n'
                  'import CryptoStfcLab from "./CryptoStfcLab";   // ── CRYPTO_STFC_20260907 ──\n')
LAB_STATE_OLD = 'export default function CryptoLab() {\n  const api = getApiBase();\n'
LAB_STATE_NEW = ('export default function CryptoLab() {\n'
                 '  // ── CRYPTO_STFC_20260907 ── lab tab: "options" (IC/strangle) | "stfc" (BTC perp)\n'
                 '  const [lab, setLab] = useState(() => { try { return localStorage.getItem("crypto_lab_tab") || "options"; } catch { return "options"; } });\n'
                 '  const pickLab = (v) => { setLab(v); try { localStorage.setItem("crypto_lab_tab", v); } catch { /* ignore */ } };\n'
                 '  const api = getApiBase();\n')
LAB_HEAD_OLD = (
    '        <h1 style={{ ...typography.h1, margin: 0 }}>\U0001FA99 Crypto Options Lab</h1>\n'
    '        <div style={{ fontSize: 12, color: colors.text.tertiary, marginTop: 4 }}>\n'
    '          BTC daily options (Delta Exchange India) \u00b7 mark-price backtests \u00b7 research only \u2014\n'
    '          fee model is an UNVERIFIED assumption; mark-price fills flatter live results.\n'
    '        </div>\n'
    '      </div>\n'
    '\n'
    '      {err ? <Card style={{ borderColor: colors.danger')
LAB_HEAD_NEW = (
    '        <h1 style={{ ...typography.h1, margin: 0 }}>\U0001FA99 Crypto Lab</h1>\n'
    '        <div style={{ fontSize: 12, color: colors.text.tertiary, marginTop: 4 }}>\n'
    '          {lab === "stfc"\n'
    '            ? "BTCUSD perp (Delta Exchange India) \u00b7 SuperTrend flip-confirm one-candle scalper \u00b7 1m-resolution SL \u00b7 research only"\n'
    '            : "BTC daily options (Delta Exchange India) \u00b7 mark-price backtests \u00b7 research only \u2014 fee model is an UNVERIFIED assumption; mark-price fills flatter live results."}\n'
    '        </div>\n'
    '        {/* \u2500\u2500 CRYPTO_STFC_20260907 \u2500\u2500 lab tabs */}\n'
    '        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>\n'
    '          {[["options", "Options \u00b7 IC / Strangle"], ["stfc", "STFC \u00b7 BTC perp"]].map(([k, label]) => (\n'
    '            <button key={k} onClick={() => pickLab(k)} style={{\n'
    '              padding: "6px 14px", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer",\n'
    '              border: `1px solid ${lab === k ? colors.primary : colors.border.light}`,\n'
    '              background: lab === k ? colors.primaryBg : colors.bg.tertiary,\n'
    '              color: lab === k ? colors.primary : colors.text.secondary,\n'
    '            }}>{label}</button>\n'
    '          ))}\n'
    '        </div>\n'
    '      </div>\n'
    '\n'
    '      {lab === "stfc" ? <CryptoStfcLab /> : (<>\n'
    '      {err ? <Card style={{ borderColor: colors.danger')
LAB_TAIL_OLD = (
    '        )}\n'
    '      </Card>\n'
    '    </div>\n'
    '  );\n'
    '}\n'
    '// \u2500\u2500 THEME_PHASE2B_20260831')
LAB_TAIL_NEW = (
    '        )}\n'
    '      </Card>\n'
    '      </>)}{/* \u2500\u2500 CRYPTO_STFC_20260907 \u2500\u2500 end options lab */}\n'
    '    </div>\n'
    '  );\n'
    '}\n'
    '// \u2500\u2500 THEME_PHASE2B_20260831')


def die(msg):
    print("ABORT:", msg); sys.exit(1)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def replace_once(src, old, new, label):
    n = src.count(old)
    if n != 1:
        die(f"anchor '{label}' count={n} (expected 1)")
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


# ---------------------------------------------------------------- prerequisites
P_CORPUS = os.path.join(BACKEND, "app", "backtest", "crypto", "delta_corpus.py")
P_ROUTES = os.path.join(BACKEND, "app", "api", "crypto_lab_routes.py")
P_ENGINE = os.path.join(BACKEND, "app", "backtest", "crypto", "crypto_stfc_engine.py")
P_LAB = os.path.join(FRONTEND, "src", "pages", "CryptoLab.jsx")
P_PAGE = os.path.join(FRONTEND, "src", "pages", "CryptoStfcLab.jsx")

for p in (P_CORPUS, P_ROUTES, P_LAB):
    if not os.path.exists(p):
        die(f"missing prerequisite {p} — run from the repo root with the Crypto Lab applied")
    if FENCE in read(p):
        die(f"{FENCE} already present in {p}")
if os.path.exists(P_ENGINE) or os.path.exists(P_PAGE):
    die("engine/page file already exists — fence not found but files present; inspect manually")

# ---------------------------------------------------------------- stage
corpus_new = read(P_CORPUS) + CORPUS_APPEND
routes_new = read(P_ROUTES)
routes_new = replace_once(routes_new, ROUTES_IMPORT_OLD, ROUTES_IMPORT_NEW, "routes import")
routes_new = replace_once(routes_new, ROUTES_RUNS_OLD, ROUTES_RUNS_NEW, "routes /runs")
routes_new = replace_once(routes_new, ROUTES_CSV_OLD, ROUTES_CSV_NEW, "routes csv")
routes_new = replace_once(routes_new, ROUTES_TAIL_OLD, ROUTES_TAIL_NEW, "routes tail")
lab_new = read(P_LAB)
lab_new = replace_once(lab_new, LAB_IMPORT_OLD, LAB_IMPORT_NEW, "lab import")
lab_new = replace_once(lab_new, LAB_STATE_OLD, LAB_STATE_NEW, "lab state")
lab_new = replace_once(lab_new, LAB_HEAD_OLD, LAB_HEAD_NEW, "lab header")
lab_new = replace_once(lab_new, LAB_TAIL_OLD, LAB_TAIL_NEW, "lab tail")

compile_gate(corpus_new, "delta_corpus.py")
compile_gate(routes_new, "crypto_lab_routes.py")
compile_gate(ENGINE_SRC, "crypto_stfc_engine.py")
print("py_compile gate: OK (3 files)")


# ---------------------------------------------------------------- simulation suite (engine, no DB)
def simulate():
    import types, datetime as dt, importlib.util, random
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30)); UTC = dt.timezone.utc
    stub = types.ModuleType("app.backtest.crypto.delta_corpus")
    stub.db = lambda: (_ for _ in ()).throw(RuntimeError("db not used in sim"))
    stub.IST = IST; stub.UTC = UTC
    for name in ("app", "app.backtest", "app.backtest.crypto"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["app.backtest.crypto.delta_corpus"] = stub
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(ENGINE_SRC); tmp = f.name
    spec = importlib.util.spec_from_file_location("stfc_sim", tmp)
    E = importlib.util.module_from_spec(spec); sys.modules["stfc_sim"] = E; spec.loader.exec_module(E)
    os.unlink(tmp)

    def bar(ts, o, h, l, c, subs=None):
        return dict(ts=ts, o=o, h=h, l=l, c=c, subs=subs or [(ts, o, h, l, c)])

    # 1) resample: 1m rows into 3m buckets, epoch aligned, OHLC correct
    m1 = [(180 * 3 + i * 60, 100 + i, 105 + i, 95 + i, 101 + i) for i in range(7)]
    b = E.resample(m1, 3)
    assert [x["ts"] for x in b] == [540, 720, 900], b
    assert b[0]["o"] == 100 and b[0]["h"] == 107 and b[0]["l"] == 95 and b[0]["c"] == 103
    assert len(b[0]["subs"]) == 3 and len(b[2]["subs"]) == 1
    print("  sim 1 resample: OK")

    # 2) supertrend: ATR na for first len-1 bars; direction consistent with price/band
    random.seed(7)
    px = 100.0; bars = []
    for i in range(400):
        o = px; c = px + random.uniform(-2, 2)
        h = max(o, c) + random.uniform(0, 1.5); l = min(o, c) - random.uniform(0, 1.5)
        bars.append(bar(i * 180, o, h, l, c)); px = c
    st = E.supertrend(bars, 10, 2.0)
    assert all(st[i][1] is None for i in range(9)) and st[9][1] == 1, "ATR seed / first direction"
    for i in range(10, 400):
        line, d = st[i]
        assert d in (1, -1)
        if d == -1: assert bars[i]["c"] >= line - 1e-9, f"uptrend but close below line at {i}"
        else: assert bars[i]["c"] <= line + 1e-9, f"downtrend but close above line at {i}"
    flips = sum(1 for i in range(10, 400) if st[i][1] != st[i - 1][1])
    assert 3 <= flips <= 120, flips
    print(f"  sim 2 supertrend: OK ({flips} flips in 400 bars)")

    # 3) state machine on scripted direction sequences (bypass ST math)
    def run_seq(seq, L=True, S=True):
        bs, ss = [], []
        for i, (d, col) in enumerate(seq):
            bs.append(bar(i * 180, 100, 102, 98, 101 if col == "g" else 99)); ss.append((0.0, d))
        return E.signals(bs, ss, L, S)
    # down,down,UP(C1),red,red,green(C4) -> trade bar 6 long
    sig = run_seq([(1, "r"), (1, "g"), (-1, "g"), (-1, "r"), (-1, "r"), (-1, "g"), (-1, "r"), (-1, "g"), (-1, "g")])
    assert sig == [(6, 1, 2, 5)], f"expected trade at bar 6 long, got {sig}"
    # C1 ignored even if green; flip back cancels pending and restarts as short
    seq = [(1, "r"), (-1, "g"), (1, "g"), (1, "r"), (1, "g")]
    assert run_seq(seq) == [(4, -1, 2, 3)], run_seq(seq)
    assert run_seq(seq, True, False) == []
    # trade on the flip bar still executes; new setup starts on that bar
    seq = [(1, "r"), (-1, "g"), (-1, "g"), (1, "r"), (1, "r"), (1, "g")]
    assert run_seq(seq) == [(3, 1, 1, 2), (5, -1, 3, 4)], run_seq(seq)
    # doji never confirms
    seq = [(1, "r"), (-1, "g"), (-1, "d"), (-1, "d")]
    bs, ss = [], []
    for i, (d, col) in enumerate(seq):
        bs.append(bar(i * 180, 100, 102, 98, 100)); ss.append((0.0, d))
    assert E.signals(bs, ss, True, True) == []
    print("  sim 3 state machine: OK")

    # 4) execution: SL walked on 1m subs; SL hit mid-candle; costs
    subs = [(0, 100, 101, 99.5, 100.5), (60, 100.5, 101, 96, 97), (120, 97, 103, 96.5, 102.5)]
    tb = bar(0, 100, 103, 96, 102.5, subs)
    t = E.execute(tb, 1, sl_usd=3, spread_usd=1, fee_pct_side=0.05, size_btc=2)
    assert t["sl_hit"] and t["exit"] == 97 and t["exit_ts"] == 60, t
    assert abs(t["gross"] - (-3 * 2)) < 1e-9 and abs(t["spread"] - 2) < 1e-9
    assert abs(t["fees"] - (100 + 97) * 2 * 0.0005) < 1e-9
    assert abs(t["net"] - (t["gross"] - t["spread"] - t["fees"])) < 1e-9
    t = E.execute(tb, 1, sl_usd=0, spread_usd=0, fee_pct_side=0, size_btc=1)
    assert not t["sl_hit"] and t["exit"] == 102.5 and abs(t["net"] - 2.5) < 1e-9
    t = E.execute(tb, -1, sl_usd=2.5, spread_usd=0, fee_pct_side=0, size_btc=1)
    assert t["sl_hit"] and t["exit"] == 102.5 and t["exit_ts"] == 120, t
    t = E.execute(tb, -1, sl_usd=0, spread_usd=0, fee_pct_side=0, size_btc=1)
    assert abs(t["net"] - (-2.5)) < 1e-9
    print("  sim 4 execution/SL/costs: OK")

    # 5) summary buckets + scoreboard ordering
    def mk(ts, net):
        return dict(ts=ts, exit_ts=ts, dir=1, entry=1, exit=1, high=1, low=1, sl_price=None,
                    sl_hit=False, move=net, gross=net, spread=0, fees=0, net=net)
    def ts(y, m, d=1):
        return int(dt.datetime(y, m, d, 10, 0, tzinfo=IST).timestamp())
    trades = [mk(ts(2024, 1), 10), mk(ts(2024, 2), -4), mk(ts(2024, 3), -3), mk(ts(2025, 1), 5), mk(ts(2025, 2), 2)]
    s = E.summarize(trades, dict(tf_min=3, st_len=10, st_mult=2, sl_usd=0))
    assert s["years_positive"] == 2 and s["years_total"] == 2 and s["net"] == 10
    assert s["worst_month"] == -4 and s["max_consec_neg_months"] == 2 and s["months_positive"] == 3
    assert s["max_dd"] == 7 and s["max_consec_losses"] == 2 and s["trading_days"] == 5
    a = dict(s); a.update(years_positive=1, worst_month=-1, max_consec_neg_months=0, months_positive=5, max_dd=1, net=999)
    assert E.score_key(s) > E.score_key(a), "all-years-positive must outrank a losing year despite larger net"
    print("  sim 5 summary/scoreboard: OK")

    # 6) config grid + validation
    cfg = E.StfcConfig.from_dict(dict(tf_list=[3, 5], st_len_list=[10], st_mult_list=[2, 3], sl_list=[0, 100]))
    assert len(cfg.cells()) == 8
    try:
        E.StfcConfig.from_dict(dict(trade_long=False, trade_short=False)); die("validation missed")
    except ValueError:
        pass
    print("  sim 6 config/grid: OK")


print("simulation suite:")
simulate()

# ---------------------------------------------------------------- write (all-or-nothing)
writes = [(P_CORPUS, corpus_new, True), (P_ROUTES, routes_new, True),
          (P_LAB, lab_new, True), (P_ENGINE, ENGINE_SRC, False), (P_PAGE, PAGE_SRC, False)]
for p, _, bak in writes:
    if bak:
        shutil.copy2(p, p + f".bak-{FENCE}")
for p, text, _ in writes:
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
print("primary tree written (5 files, 3 backups)")


# ---------------------------------------------------------------- dual trees
def mirror(src_root, dst_root, rel):
    dst = os.path.join(dst_root, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(os.path.join(src_root, rel), dst)


if os.path.isdir(DUAL_BACKEND):
    for rel in ("app/backtest/crypto/delta_corpus.py", "app/api/crypto_lab_routes.py",
                "app/backtest/crypto/crypto_stfc_engine.py"):
        mirror(BACKEND, DUAL_BACKEND, rel)
    print("dual backend tree synced")
else:
    print("dual backend tree absent (build script rsyncs it) — skipped")
if os.path.isdir(DUAL_FRONTEND):
    for rel in ("src/pages/CryptoLab.jsx", "src/pages/CryptoStfcLab.jsx"):
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
    for p in (P_LAB, P_PAGE):
        r = subprocess.run([esb, p, "--loader:.jsx=jsx", "--jsx=automatic", "--outfile=/dev/null"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr); die(f"esbuild failed for {p}")
    print("esbuild: OK (CryptoLab.jsx, CryptoStfcLab.jsx)")
else:
    print("esbuild not found — run `npm run build` in frontend/ to verify")

print(f"\nDONE — {FENCE} applied. Rebuild: ./desktop/build-scalp.sh both")
