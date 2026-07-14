# backend/app/tests/pst_daily_parity_report.py
#
# ── PST DAILY PARITY REPORT ── (Phase 1, Delivery 3 — D27 closing loop)
#
# End-of-day audit: replays the day's CAPTURED live candles
# (~/.scalp-app/pst_capture/YYYY-MM-DD.jsonl) through the BACKTEST engines
# (build_signals + run_day_short / run_day_hedge, same configs) and diffs
# the result against what the paper managers ACTUALLY recorded in
# pst_sell_trades / pst_hedge_trades. On identical candles any difference
# is a code bug — this is the daily proof that paper == backtest.
#
#   python3 -m app.tests.pst_daily_parity_report 2026-07-15
#
# PASS: "PARITY OK" per strategy — leg-for-leg identical.

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime

IST = 5 * 3600 + 30 * 60


def day_start(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


def load_capture(path):
    spot, chain = [], {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            c = {"ts": int(r["ts"]), "open": r["open"], "high": r["high"],
                 "low": r["low"], "close": r["close"]}
            if r["sym"] == "SPOT":
                spot.append(c)
            else:
                chain.setdefault(r["sym"], {})[c["ts"]] = c
    spot.sort(key=lambda c: c["ts"])
    return spot, chain


def main(day_iso: str):
    home = os.path.expanduser("~/.scalp-app")
    from app.engine.pst.pst_common import canonical_db_path
    db_file = canonical_db_path()          # data/app.db — NOT APP_HOME/app.db
    cap = os.path.join(home, "pst_capture", f"{day_iso}.jsonl")
    if not os.path.exists(cap):
        print(f"no capture file {cap}")
        return 1
    d = date.fromisoformat(day_iso)
    ds = day_start(d)
    spot, chain = load_capture(cap)
    print(f"{day_iso}: {len(spot)} spot candles, {len(chain)} contracts captured")

    from app.backtest.pst.pst_v1_engine import build_signals
    from app.backtest.pst.pst_sell_engine import run_day_short
    from app.backtest.pst.pst_hedge_engine import run_day_hedge
    from app.backtest.ic.ic_v1_engine import select_strike
    from app.config.strategy_loader import load_strategy_config
    from app.engine.pst.pst_live_warmup import fetch_prev_session_spot
    from app.brokers.zerodha_manager import ZerodhaManager

    warm = fetch_prev_session_spot(ZerodhaManager().get_kite(), today=d)
    if warm is None:
        print("warmup unavailable — cannot replay signals")
        return 1
    sig_res = build_signals(spot, ds, warm["prev_hlc"],
                            warmup_sessions=[(warm["spot_1m"], warm["day_start"])])
    sigs = sig_res["signals"]
    print(f"replayed signals: {len(sigs)}")

    fails = 0
    for sid, table, runner in (("PST_SELL", "pst_sell_trades", run_day_short),
                               ("PST_HEDGE", "pst_hedge_trades", run_day_hedge)):
        cfg = load_strategy_config(sid)
        legs = [l for l in (cfg.get("legs") or []) if int(l.get("lots") or 0) > 0]
        if not legs:
            print(f"{sid}: no legs configured — skipped")
            continue
        prem = float(cfg.get("premium_max", 150) or 150)
        eod = ds + 60 * (int(cfg.get("exit_time", "15:25").split(":")[0]) * 60
                         + int(cfg.get("exit_time", "15:25").split(":")[1]))
        side_mode = str(cfg.get("side_mode", "BOTH") or "BOTH")
        mtpd = int(cfg.get("max_trades_per_day", 0) or 0)

        def _pick(side, ts):
            cands = [(s, cs[ts - 60]["close"]) for s, cs in chain.items()
                     if s.endswith(side) and (ts - 60) in cs]
            p = select_strike(cands, prem)
            if p is None or ts not in chain.get(p[0], {}):
                return None
            return p[0]

        if sid == "PST_SELL":
            def sel(side, ts):
                sym = _pick(side, ts)
                if sym is None:
                    return None
                return {"symbol": sym, "entry_price": chain[sym][ts]["close"],
                        "candles": [c for t, c in sorted(chain[sym].items()) if t >= ts + 60]}
            ref = runner(sigs, legs, sel, spot, eod,
                         side_mode=side_mode, max_trades_per_day=mtpd)["trades"]
            refk = sorted((t["tradingsymbol"], t["leg"], int(t["entry_ts"]) + 60,
                           round(t["entry_price"], 2), int(t["exit_ts"]),
                           round(t["exit_price"], 2), t["exit_reason"]) for t in ref)
        else:
            def selp(side, ts):
                ssym = _pick(side, ts)
                other = "PE" if side == "CE" else "CE"
                hsym = _pick(other, ts) if ssym else None
                if ssym is None or hsym is None:
                    return None
                return {"sig_symbol": ssym, "sig_entry": chain[ssym][ts]["close"],
                        "sig_candles": [c for t, c in sorted(chain[ssym].items()) if t >= ts + 60],
                        "held_symbol": hsym, "held_side": other,
                        "held_entry": chain[hsym][ts]["close"],
                        "held_candles": [c for t, c in sorted(chain[hsym].items()) if t >= ts + 60]}
            ref = runner(sigs, legs, selp, spot, eod,
                         side_mode=side_mode, max_trades_per_day=mtpd)["trades"]
            refk = sorted((t["tradingsymbol"], t["leg"], int(t["entry_ts"]) + 60,
                           round(t["entry_price"], 2), int(t["exit_ts"]),
                           round(t["exit_price"], 2), t["exit_reason"]) for t in ref)

        with sqlite3.connect(db_file) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute(
                f"""SELECT * FROM {table} WHERE entry_ts >= ? AND entry_ts < ?
                    ORDER BY entry_ts, leg_id""", (ds, ds + 86400))]
        papk = sorted((r["tradingsymbol"], r["leg_id"], int(r["entry_ts"]),
                       round(r["entry_price"], 2), int(r["exit_ts"] or 0),
                       round(r["exit_price"] or 0, 2), r["exit_reason"]) for r in rows)
        if refk == papk:
            print(f"{sid}: PARITY OK — {len(refk)} legs leg-for-leg identical")
        else:
            fails += 1
            print(f"{sid}: PARITY FAIL — replay {len(refk)} vs paper {len(papk)}")
            for x in sorted(set(refk) - set(papk))[:3]:
                print(f"   only-replay {x}")
            for x in sorted(set(papk) - set(refk))[:3]:
                print(f"   only-paper  {x}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()))