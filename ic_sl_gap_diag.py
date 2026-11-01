#!/usr/bin/env python3
# ic_sl_gap_diag.py — ── IC_SL_GAP_DIAG_20261101 ── ── IC_SL_GAP_DIAG_FIX1_20261101 ── READ-ONLY, writes nothing.
#
# Why this exists instead of the IC "intrabar SL" patch (1 Nov):
#   the IC engine ALREADY checks every short leg's SL on the candle HIGH and
#   fills AT the SL price (ic_v1_engine._eval_candle: SELL hit_sl = high >= sl,
#   sl_fill = sl; BUY → low; TP mirrors). A spike candle stops the short in its
#   own minute and the adjustment arms from that minute. So "backtest checks the
#   close" cannot be what made 28 Oct 12:58 vs 12:51 — something else differs.
#
# This replays IC_V2 for each day EXACTLY as the parity ledger does (same day
# pack, same 09:14 config, same runner) and, for every short leg that stopped
# on either side, prints:
#   * entry / SL / exit of the backtest leg vs the paper leg (app.db)
#   * the leg's 1m bars from the pack around both stop minutes, marking which
#     bar's HIGH first reached the paper SL and which first reached the
#     backtest SL, and any missing minutes
#   * a verdict:
#       A  SL LEVELS DIFFER   (entry price → SL level; the stop check is fine)
#       B  BAR REACHED SL, BACKTEST LATE   (engine/data bug — would be news)
#       C  PAPER STOPPED ON A TICK THE 1m BAR DOES NOT SHOW   (feed vs bar)
#       D  MTC / re-pinned stop involved   (cross-leg timing, not the SL check)
#
#   cd <repo root> && python3 ic_sl_gap_diag.py                 # 28 + 29 Oct
#   cd <repo root> && python3 ic_sl_gap_diag.py 2026-10-28 --config=/path/run_config.json
#   Config: --config file → recorder snapshot → nearest later snapshot → current live config.
#
# Safe while the app runs: backtest.db / recorder / app.db are only read.

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

SID = "IC_V2"
IST = timezone(timedelta(hours=5, minutes=30))
STOP_REASONS = {"SL", "MTC_COST", "MTC_MARKET_OUT", "IV_SL"}
PAD_MIN = 3


def _hm(ts):
    return datetime.fromtimestamp(int(ts), IST).strftime("%H:%M:%S") if ts else "—"


def _hhmm(ts):
    return datetime.fromtimestamp(int(ts), IST).strftime("%H:%M") if ts else "—"


def _minute(ts):
    return int(ts) - int(ts) % 60 if ts else None


def _g(t, k):
    return t.get(k) if isinstance(t, dict) else getattr(t, k, None)


def _strike(sym):
    import re
    m = re.search(r"(\d+)(CE|PE)$", str(sym or ""))
    if not m:
        return None, None
    d = m.group(1)
    for n in (5, 4):           # NIFTY strikes are 4–5 digits at the end
        try:
            k = float(d[-n:])
            if 5000 <= k <= 60000:
                return k, m.group(2)
        except ValueError:
            pass
    return None, m.group(2)


# ── app side ──────────────────────────────────────────────────────────
def paper_legs(app_db, d):
    ds = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    de = ds + 86400
    c = sqlite3.connect(f"file:{app_db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "SELECT symbol, trade_class, trade_direction, entry_time, candle_ts, entry_price, sl_price, "
            "tp_price, exit_time, exit_price, exit_reason, qty, state FROM paper_trades "
            "WHERE strategy_name=? AND ((exit_time>=? AND exit_time<?) OR (state='OPEN' AND entry_time<?)) "
            "ORDER BY entry_time", (SID, ds, de, de)).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        k, ot = _strike(r["symbol"])
        out.append({"symbol": r["symbol"], "leg": r["trade_class"], "side": "SELL" if r["trade_direction"] == "SHORT" else "BUY",
                    "type": ot, "strike": k, "entry_ts": int(r["candle_ts"] or r["entry_time"] or 0),
                    "entry_wall": int(r["entry_time"] or 0), "entry": float(r["entry_price"] or 0),
                    "sl": float(r["sl_price"]) if r["sl_price"] else None,
                    "exit_ts": int(r["exit_time"]) if r["exit_time"] else None,
                    "exit": float(r["exit_price"]) if r["exit_price"] is not None else None,
                    "reason": r["exit_reason"], "qty": int(r["qty"] or 0)})
    return out


# ── backtest side (the ledger's replay, raw trades kept) ──────────────
def replay_raw(d, d_from):
    from app.parity import parity_core as PC
    from app.backtest import queue_worker as QW
    from app.backtest.repo import trading_calendar as TC
    cfg, notes, src = resolve_config(d_from)
    if cfg is None:
        return None, None, None, notes
    notes.append(f"config source: {src}")
    cfg, tn = PC.to_runner_config(SID, cfg, d_from)
    notes += tn
    pack, pinfo = PC.build_pack(d, d_from=d_from)
    tok = QW.DB_OVERRIDE.set(pack)
    ctok = TC.CAL_OVERRIDE.set(PC.session_calendar(d_from, d))
    try:
        res = QW._dispatch_run(strategy_id=SID, underlying="NIFTY", df=d_from, dt=d, config=cfg,
                               progress_cb=lambda *a, **k: None, cancel_cb=lambda: False)
    finally:
        TC.CAL_OVERRIDE.reset(ctok)
        QW.DB_OVERRIDE.reset(tok)
    if (res or {}).get("aborted"):
        notes.append(f"runner aborted: {res.get('reason')}")
    bt = []
    for t in (res or {}).get("trades") or []:
        sym = _g(t, "tradingsymbol") or _g(t, "symbol")
        k, ot = _strike(sym)
        bt.append({"symbol": sym, "leg": _g(t, "condition"), "side": str(_g(t, "direction") or "").upper(),
                   "type": _g(t, "instrument_type") or ot, "strike": _g(t, "strike") or k,
                   "entry_ts": int(_g(t, "entry_ts") or 0), "entry": float(_g(t, "entry_price") or 0),
                   "sl": _g(t, "sl"), "tp": _g(t, "tp"), "exit_ts": _g(t, "exit_ts"),
                   "exit": _g(t, "exit_price"), "reason": _g(t, "exit_reason"),
                   "synthetic": str(sym or "").upper().startswith("SYN")})
    return bt, pack, cfg, notes


CONFIG_FILE = None          # --config=path.json (a Backtest run's config, or a saved live config)


def resolve_config(d_from):
    """── IC_SL_GAP_DIAG_FIX1_20261101 ── the recorder's 09:14 snapshot only exists
    from PARITY_CAPTURE (29 Oct). Order: --config file → snapshot for d_from →
    the nearest LATER snapshot → the current live config (load_strategy_config)."""
    import json
    from app.parity import parity_core as PC
    if CONFIG_FILE:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg = cfg.get("config", cfg) if isinstance(cfg, dict) else cfg
        return cfg, [], f"--config {CONFIG_FILE}"
    cfg, notes = PC.config_for(d_from, SID)
    if cfg is not None:
        return cfg, notes, f"recorder 09:14 snapshot of {d_from}"
    notes = [f"no 09:14 snapshot for {d_from} (the recorder captures configs from 29 Oct)"]
    try:
        from app.recorder import rec_db as RD
        c = RD.connect()
        try:
            r = c.execute("SELECT day, config_json FROM session_config WHERE strategy_id=? AND day>? ORDER BY day LIMIT 1",
                          (SID, d_from.isoformat())).fetchone()
        finally:
            c.close()
        if r:
            return json.loads(r[1]), notes, f"recorder 09:14 snapshot of {r[0]} (nearest later day)"
    except Exception as e:
        notes.append(f"recorder read failed: {type(e).__name__}: {e}")
    try:
        from app.config.strategy_loader import load_strategy_config
        cfg = load_strategy_config(SID) or {}
        if cfg:
            return cfg, notes + ["!! the CURRENT live config — if IC_V2 Settings were saved after this day it is not "
                                 "the config that traded"], "current live config (strategy_loader)"
    except Exception as e:
        notes.append(f"live config read failed: {type(e).__name__}: {e}")
    return None, notes + ["no config found — pass --config=path.json"], None


def bars(pack, sym, lo, hi):
    c = sqlite3.connect(f"file:{pack}?mode=ro", uri=True)
    try:
        return {int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in c.execute(
            "SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts>=? AND ts<=? "
            "ORDER BY ts", (sym, lo, hi))}
    finally:
        c.close()


def first_touch(bmap, level, frm, to, side="SELL"):
    if level is None:
        return None
    for ts in sorted(bmap):
        if frm <= ts <= to:
            h, lo = bmap[ts][1], bmap[ts][2]
            if (h >= level) if side == "SELL" else (lo <= level):
                return ts
    return None


# ── one pair ──────────────────────────────────────────────────────────
def analyse(pack, b, a, ds):
    sym = (b or a)["symbol"]
    print(f"\n  ── {sym}   backtest {b['leg'] if b else '—'}  ·  paper {a['leg'] if a else '—'}")
    if b:
        print(f"     backtest  entry {_hhmm(b['entry_ts'])} @ {b['entry']:.2f}   SL {b['sl']}   exit {_hhmm(b['exit_ts'])} @ {b['exit']}  {b['reason']}")
    if a:
        print(f"     paper     entry {_hhmm(a['entry_ts'])} @ {a['entry']:.2f} (booked {_hm(a['entry_wall'])})   SL {a['sl']}   "
              f"exit {_hm(a['exit_ts'])} @ {a['exit']}  {a['reason']}")
    if b and a and b["symbol"] != a["symbol"]:
        print("     !! different contracts — the strike pick already differs; bars below are the backtest contract's")
    if b and b.get("synthetic"):
        print("     backtest leg is synthetic (SYN-) — no bars; nothing to compare")
        return "SYN"
    ent = min(x["entry_ts"] for x in (b, a) if x)
    ex = [x["exit_ts"] for x in (b, a) if x and x.get("exit_ts")]
    if not ex:
        return "OPEN"
    lo, hi = _minute(min(ex)) - PAD_MIN * 60, _minute(max(ex)) + 60
    bm = bars(pack, sym, ent - 60, hi + 60)
    watch_from = ent + 60
    t_app = first_touch(bm, a["sl"] if a else None, watch_from, hi + 60)
    t_bt = first_touch(bm, b["sl"] if b else None, watch_from, hi + 60)
    pre = [bm[t][1] for t in bm if watch_from <= t < lo]
    if pre:
        mx = max(pre)
        print(f"     highest bar high {_hhmm(watch_from)}–{_hhmm(lo - 60)}: {mx:.2f}")
    print(f"     {'min':>5}  {'open':>8} {'high':>8} {'low':>8} {'close':>8}   ≥paperSL ≥btSL")
    t = lo
    missing = []
    while t <= hi:
        r = bm.get(t)
        tag = ("  ← paper exit" if a and _minute(a.get("exit_ts")) == t else "") + \
              ("  ← backtest exit" if b and _minute(b.get("exit_ts")) == t else "")
        if r is None:
            missing.append(t)
            print(f"     {_hhmm(t):>5}  {'(no bar)':>35}{tag}")
        else:
            o, h, l, cl = r
            fa = "   ✓   " if a and a["sl"] and h >= a["sl"] else "       "
            fb = "  ✓" if b and b["sl"] and h >= b["sl"] else "   "
            print(f"     {_hhmm(t):>5}  {o:8.2f} {h:8.2f} {l:8.2f} {cl:8.2f}   {fa}{fb}{tag}")
        t += 60
    miss_all = [x for x in range(watch_from, hi + 1, 60) if x not in bm and x < (ds + (15 * 60 + 30) * 60)]
    if miss_all:
        print(f"     bars missing between entry and stop: {len(miss_all)} ({', '.join(_hhmm(x) for x in miss_all[:12])}"
              f"{' …' if len(miss_all) > 12 else ''})")

    # ── verdict
    reasons = {str(x.get("reason") or "") for x in (b, a) if x}
    if (b and "MTC" in str(b.get("leg") or "")) or any(r.startswith("MTC") for r in reasons):
        print("     VERDICT D — a moved-to-cost stop is involved: the timing is MTC re-pin vs live's "
              "mtc_activation_decision, not the SL check. Compare the partner leg's stop minute first.")
        return "D"
    if a and b and a["sl"] and b["sl"] and abs(float(a["sl"]) - float(b["sl"])) > 0.05:
        print(f"     VERDICT A — SL LEVELS DIFFER: backtest {float(b['sl']):.2f} (from entry {b['entry']:.2f}) vs paper "
              f"{float(a['sl']):.2f} (from entry {a['entry']:.2f}).")
        print(f"       first bar whose high reached the paper SL: {_hhmm(t_app)}   (paper exited {_hhmm(_minute(a['exit_ts']))})")
        print(f"       first bar whose high reached the backtest SL: {_hhmm(t_bt)}   (backtest exited {_hhmm(b['exit_ts'])})")
        if t_bt == b["exit_ts"]:
            print("       → the backtest stopped on the FIRST bar that touched its own SL: the stop check is intrabar and correct;"
                  " the gap is the entry price (fill convention), not the SL timing.")
        return "A"
    if b and t_bt is not None and b.get("exit_ts") and t_bt < int(b["exit_ts"]):
        print(f"     VERDICT B — a bar's high reached the SL at {_hhmm(t_bt)} but the backtest exited at {_hhmm(b['exit_ts'])}."
              + (" Missing bars in between explain nothing here — the bar existed." if t_bt in bm else ""))
        return "B"
    if a and b and a.get("exit_ts") and b.get("exit_ts") and _minute(a["exit_ts"]) < int(b["exit_ts"]):
        if a["exit_ts"] and t_app is not None and t_app <= _minute(a["exit_ts"]):
            print("     VERDICT B? — the paper minute's bar reached the SL but the backtest did not stop there; inspect the rows above.")
            return "B"
        hh = max([bm[x][1] for x in bm if watch_from <= x <= _minute(a["exit_ts"])] or [0])
        print(f"     VERDICT C — paper stopped at {_hm(a['exit_ts'])} on a tick ≥ {a['sl']}, but no 1m bar up to that minute "
              f"has a high that reaches it (highest {hh:.2f}). The recorded bar and the live tick feed disagree "
              f"— a data question (Kite historical bar vs WebSocket ticks), not runner logic. Paper booked {a['exit']}.")
        return "C"
    print("     same SL, same minute — no gap on this leg")
    return "SAME"


def pair(bt, app):
    out, used = [], set()
    for b in bt:
        if b["side"] != "SELL":
            continue
        m = next((i for i, a in enumerate(app) if i not in used and a["side"] == "SELL" and a["symbol"] == b["symbol"]
                  and abs(a["entry_ts"] - b["entry_ts"]) <= 15 * 60), None)
        if m is None:
            m = next((i for i, a in enumerate(app) if i not in used and a["side"] == "SELL" and a["type"] == b["type"]
                      and abs(a["entry_ts"] - b["entry_ts"]) <= 15 * 60), None)
        if m is not None:
            used.add(m)
        out.append((b, app[m] if m is not None else None))
    out += [(None, a) for i, a in enumerate(app) if i not in used and a["side"] == "SELL"]
    return out


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    be = os.path.join(root, "backend")
    if not os.path.isdir(os.path.join(be, "app")):
        print("run from the scalp-app repo root (backend/app not found next to this script)")
        sys.exit(1)
    sys.path.insert(0, be)
    global CONFIG_FILE
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    CONFIG_FILE = next((x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--config=")), None)
    days = [date.fromisoformat(x) for x in args] or [date(2026, 10, 28), date(2026, 10, 29)]
    from app.utils.app_paths import DB_PATH
    app_db = os.environ.get("SCALP_APP_DB") or str(DB_PATH)
    print(f"── {SID} SL gap diagnostic (read-only) · app.db = {app_db}")
    verdicts = []
    for d in days:
        ds = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
        app = paper_legs(app_db, d)
        first = min([datetime.fromtimestamp(a["entry_ts"], IST).date() for a in app if a["entry_ts"]] + [d])
        d_from = max(first, d - timedelta(days=7))
        print(f"\n════ {d}  (replayed from {d_from}) — paper legs: {len(app)}")
        bt, pack, cfg, notes = replay_raw(d, d_from)
        for n in notes:
            print(f"  note: {n}")
        if bt is None:
            continue
        try:
            for l in (cfg.get("legs") or []):
                if str(l.get("action", "")).upper() == "SELL":
                    print(f"  config {l.get('id')}: SL {l.get('sl_val')} {l.get('sl_mode', 'pct')}  TP {l.get('tp_val')} {l.get('tp_mode', 'pct')}"
                          f"  cap {l.get('premium_max')}")
            print(f"  config adjust_on_sl={cfg.get('adjust_on_sl')}  adjust={cfg.get('adjust')}  entry {cfg.get('entry_time')}  exit_mode {cfg.get('exit_mode')}")
            de = ds + 86400
            bt_day = [b for b in bt if b.get("exit_ts") and ds <= int(b["exit_ts"]) < de]
            app_day = [a for a in app if a.get("exit_ts") and ds <= a["exit_ts"] < de]
            hit = [(b, a) for b, a in pair(bt_day, app_day)
                   if (b and str(b.get("reason")) in STOP_REASONS) or (a and str(a.get("reason")) in STOP_REASONS)]
            if not hit:
                print("  no short leg stopped on either side this day")
            for b, a in hit:
                verdicts.append((d, (b or a)["symbol"], analyse(pack, b, a, ds)))
        finally:
            try:
                os.unlink(pack)
            except Exception:
                pass
    print("\n════ summary")
    for d, s, v in verdicts:
        print(f"  {d}  {s:<22} {v}")
    print("\nPaste this whole output back into the chat.")


if __name__ == "__main__":
    main()
