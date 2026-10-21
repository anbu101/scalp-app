# backend/app/backtest/replay/test_live_replay.py
#
# ── TRADE_REPLAY_LIVE_20261017 ── behavioural suite for Trade Replay on the
# Trades and Analytics pages.
#
# A throw-away HOME gets the REAL app.db migrations; rows are written into
# the REAL tables (paper_trades, vet_trades, tma2_trades, trades) and read
# back through the pages' OWN loaders. A synthetic corpus is built twice:
# SRC (what "Kite" knows) and the target corpus with holes (what the app
# has). A fake Kite serves SRC; the suite checks that the fill adds exactly
# the missing bars, never modifies an existing past row, upserts only today,
# reports expired contracts and a missing session, groups legs like the
# pages do, draws overlays on the saved Settings config, and that the route
# is admin-only on the REAL api_server app (driven as ASGI — no TestClient).
# ── TRADE_REPLAY_STREAM_20261017 ── + live mode: the forming bar from a
# sampled in-memory feed (never written), provisional indicator segments on
# the forming bar of each overlay's timeframe, the Kite bridge, live price
# lines and SL/TP distances, staleness, session gating, the sampler TTL, and
# the Dashboard lookup (find_keys + /api/replay/find).
#
#   cd backend && PYTHONPATH=$PWD python3 app/backtest/replay/test_live_replay.py

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

FAILS = []
PASSES = [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:300]}]")


TMP = tempfile.mkdtemp(prefix="live_replay_")
os.environ["HOME"] = TMP                     # app paths derive from Path.home() at import
IST = timezone(timedelta(hours=5, minutes=30))


def ts_of(d, hh, mm, ss=0):
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST).timestamp())


from pathlib import Path                                   # noqa: E402
import app.backtest.repo.backtest_repo as BR               # noqa: E402
import app.backtest.data.candle_source as CS               # noqa: E402
DB = os.path.join(TMP, "backtest.db")
SRC = os.path.join(TMP, "src.db")
BR._db_path = lambda: Path(DB)
CS._backtest_db_path = lambda: Path(DB)
import app.backtest.replay.replay_core as RC               # noqa: E402
RC.runs_db_path = lambda: DB
import app.backtest.replay.live_replay as LR               # noqa: E402
import app.backtest.backfill.kite_backfill as KB           # noqa: E402
KB.THROTTLE_S = 0.0
from app.backtest.replay.replay_synth import build         # noqa: E402
from app.db.sqlite import get_conn                         # noqa: E402
from app.db.migrations.runner import run_migrations        # noqa: E402
from app.event_bus.audit_logger import audit_muted         # noqa: E402

print("── 0. app.db (real migrations) + corpora")
APP = get_conn()
with audit_muted():
    run_migrations(APP)
check("app.db migrated in the throw-away HOME", str(TMP) in str(Path.home()))
build(SRC, date(2025, 1, 1), date(2025, 2, 28))
shutil.copy(SRC, DB)
D24, D25, D10 = date(2025, 2, 24), date(2025, 2, 25), date(2025, 2, 10)
src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row
exp = src.execute("SELECT expiry FROM backtest_candles_1m WHERE instrument_type='CE' AND ts>=? AND ts<? LIMIT 1",
                  (ts_of(D24, 9, 15), ts_of(D24, 9, 16))).fetchone()[0]
spot0915 = src.execute("SELECT open FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts=?",
                       (ts_of(D24, 9, 16),)).fetchone()[0]
atm = int(round(spot0915 / 50) * 50)
yy, mc = int(exp[2:4]), {"1": "1", "2": "2"}.get(str(int(exp[5:7])), str(int(exp[5:7])))
dd = exp[8:10]
S = lambda k, t: f"NIFTY{yy}{mc}{dd}{k}{t}"            # noqa: E731
SYMS = {"ce": S(atm + 150, "CE"), "pe": S(atm - 150, "PE"), "cew": S(atm + 300, "CE"), "pew": S(atm - 300, "PE"),
        "atm_ce": S(atm, "CE"), "atm_pe": S(atm, "PE"), "exp": S(atm + 250, "CE")}
have = {r[0] for r in src.execute("SELECT DISTINCT tradingsymbol FROM backtest_candles_1m WHERE expiry=?", (exp,))}
check("synthetic chain carries every symbol the suite trades", all(s in have for s in SYMS.values()),
      [s for s in SYMS.values() if s not in have])
TOK = {r[0]: r[1] for r in src.execute("SELECT DISTINCT tradingsymbol, instrument_token FROM backtest_candles_1m")}

# holes in the app's corpus: spot from 17 Feb on, the traded contracts on the
# trade days, and 10 Feb's spot afternoon (a partial past day) with one
# tampered morning bar that the fill must never touch.
tgt = sqlite3.connect(DB)
tgt.execute("DELETE FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=?", (ts_of(date(2025, 2, 17), 0, 0),))
for s in SYMS.values():
    tgt.execute("DELETE FROM backtest_candles_1m WHERE tradingsymbol=? AND ts>=?", (s, ts_of(D24, 0, 0)))
tgt.execute("DELETE FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts>=? AND ts<?",
            (ts_of(D10, 12, 0), ts_of(D10, 23, 0)))
TAMPER_TS = ts_of(D10, 10, 0)
tgt.execute("UPDATE backtest_candles_1m SET close=11111.11 WHERE instrument_type='SPOT' AND ts=?", (TAMPER_TS,))
tgt.commit()
tgt.close()


# ── fake Kite + dump + clock ──
class FakeKite:
    def __init__(self):
        self.calls = []
        self.bump = {}                       # (token, ts) → close override (today's forming bar moves)
        self.cut = None                      # serve nothing at/after this ts (today so far)

    def historical_data(self, instrument_token, from_date, to_date, interval, oi=False):
        self.calls.append((instrument_token, from_date[:10], to_date[:10]))
        tok = 1 if instrument_token == LR.SPOT_KITE_TOKEN else int(instrument_token)
        lo = int(datetime.fromisoformat(from_date).replace(tzinfo=IST).timestamp())
        hi = int(datetime.fromisoformat(to_date).replace(tzinfo=IST).timestamp())
        rows = src.execute("SELECT ts, open, high, low, close, volume, oi FROM backtest_candles_1m "
                           "WHERE instrument_token=? AND ts>=? AND ts<=? ORDER BY ts", (tok, lo, hi)).fetchall()
        out = []
        for r in rows:
            if self.cut and r["ts"] >= self.cut:
                continue
            out.append({"date": datetime.fromtimestamp(r["ts"], IST), "open": r["open"], "high": r["high"],
                        "low": r["low"], "close": self.bump.get((tok, r["ts"]), r["close"]),
                        "volume": r["volume"], "oi": r["oi"]})
        return out


KITE = FakeKite()
LR._kite = lambda: KITE
DUMP = {s: {"token": t, "underlying": "NIFTY", "instrument_type": s[-2:], "strike": float(s[len(f"NIFTY{yy}{mc}{dd}"):-2]),
            "expiry": exp} for s, t in TOK.items() if s.startswith("NIFTY") and s != SYMS["exp"] and s[-2:] in ("CE", "PE")}
LR._dump_by_sym = lambda: DUMP
NOW = [ts_of(date(2025, 3, 10), 12, 0)]
LR._now_ts = lambda: NOW[0]


def reset_fill_memory():
    LR._CHECKED.clear()
    LR._TODAY_AT.clear()


# ── rows in the REAL tables ──
def paper_row(pid, sid, sym, side, dirn, ent_ts, ent, ex_ts=None, ex=None, mode="PAPER", qty=65, sl=0.0, tp=0.0, token=None):
    APP.execute(
        "INSERT INTO paper_trades (paper_trade_id, strategy_name, trade_mode, symbol, token, side, entry_time, entry_price, "
        "candle_ts, sl_price, tp_price, rr, lots, lot_size, qty, exit_time, exit_price, exit_reason, pnl_points, pnl_value, "
        "total_charges, net_pnl, state, created_at, trade_direction) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, sid, mode, sym, TOK.get(sym, 0) if token is None else token, side, ent_ts, ent, ent_ts, sl, tp, 0, qty // 65, 65, qty, ex_ts, ex,
         "EOD" if ex_ts else None, None, None, None, None, "CLOSED" if ex_ts else "OPEN", ent_ts, dirn))


def px(sym, ts, col="close"):
    r = src.execute(f"SELECT {col} FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?", (sym, ts)).fetchone()
    return float(r[0])


E24, X24 = ts_of(D24, 9, 16, 5), ts_of(D24, 15, 15, 2)
for i, (k, side, dirn) in enumerate((("ce", "CE", "SHORT"), ("pe", "PE", "SHORT"), ("cew", "CE", "LONG"), ("pew", "PE", "LONG"))):
    s = SYMS[k]
    paper_row(f"TSG-{i + 1}", "TSG_V1", s, side, dirn, E24, px(s, ts_of(D24, 9, 16)), X24, px(s, ts_of(D24, 15, 15)))
# a different strategy in the same minute must not join the TSG group
paper_row("ORB-1", "ORB_V1", SYMS["atm_ce"], "CE", "LONG", E24 + 20, px(SYMS["atm_ce"], ts_of(D24, 9, 16)),
          ts_of(D24, 10, 40), px(SYMS["atm_ce"], ts_of(D24, 10, 40)), sl=40.0, tp=160.0)
# an expired contract (not in the dump) that the corpus lacks
paper_row("ORB-EXP", "ORB_V1", SYMS["exp"], "CE", "LONG", ts_of(D24, 11, 0), px(SYMS["exp"], ts_of(D24, 11, 0)),
          ts_of(D24, 12, 0), px(SYMS["exp"], ts_of(D24, 12, 0)), token=0)
# BRK LIVE row in the generic paper table → shows on Analytics too
paper_row("BRK-L1", "BRK_V1", SYMS["atm_pe"], "PE", "LONG", ts_of(D24, 13, 5), px(SYMS["atm_pe"], ts_of(D24, 13, 5)),
          ts_of(D24, 14, 0), px(SYMS["atm_pe"], ts_of(D24, 14, 0)), mode="LIVE", sl=30.0)

# VET paper spread (vet_trades)
for role, k, dirn in (("MAIN", "atm_pe", "SHORT"), ("HEDGE", "pew", "LONG")):
    s = SYMS[k]
    APP.execute("INSERT INTO vet_trades (group_id, leg_role, mode, direction, tradingsymbol, token, instrument_type, strike, "
                "expiry, qty, lots, lot_size, entry_ts, entry_price, status, exit_ts, exit_price, exit_reason, pnl, charges, net_pnl) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("VG1", role, "PAPER", dirn, s, TOK[s], s[-2:], 0, exp, 650, 10, 65, ts_of(D24, 10, 5), px(s, ts_of(D24, 10, 5)),
                 "CLOSED", ts_of(D24, 14, 30), px(s, ts_of(D24, 14, 30)), "XOVER", 0, 0, 0))

# IC_V2 live condor in `trades` with group_id: 4 legs at 09:20 + an adjustment leg at 11:00
IC_LEGS = (("L1", "ce", "SHORT"), ("L2", "pe", "SHORT"), ("L3", "cew", "LONG"), ("L4", "pew", "LONG"))
for tc, k, dirn in IC_LEGS:
    s = SYMS[k]
    APP.execute("INSERT INTO trades (trade_id, strategy_id, slot, symbol, token, entry_time, entry_price, qty, buy_order_id, "
                "sl_price, tp_price, tp_mode, state, exit_time, exit_price, exit_reason, trade_direction, group_id, trade_class) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"IC-{tc}", "IC_V2", s[-2:], s, TOK[s], ts_of(D24, 9, 20), px(s, ts_of(D24, 9, 20)), 650, "o", 0, 0, "MANUAL",
                 "CLOSED", ts_of(D24, 15, 10), px(s, ts_of(D24, 15, 10)), "EOD", dirn, "ICG1", tc))
s = SYMS["atm_ce"]
APP.execute("INSERT INTO trades (trade_id, strategy_id, slot, symbol, token, entry_time, entry_price, qty, buy_order_id, "
            "sl_price, tp_price, tp_mode, state, exit_time, exit_price, exit_reason, trade_direction, group_id, trade_class) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("IC-ADJ", "IC_V2", "CE", s, TOK[s], ts_of(D24, 11, 0), px(s, ts_of(D24, 11, 0)), 650, "o", 0, 0, "MANUAL",
             "CLOSED", ts_of(D24, 15, 10), px(s, ts_of(D24, 15, 10)), "ADJ", "SHORT", "ICG1", "ADJ1"))

# TMA_V2 paper spread entered 24 Feb, still OPEN (used with "today" = 25 Feb)
for dirn, k in (("SELL", "atm_ce"), ("BUY", "cew")):
    s = SYMS[k]
    APP.execute("INSERT INTO tma2_trades (group_id, mode, direction, tradingsymbol, token, instrument_type, strike, expiry, qty, "
                "entry_ts, entry_price, sl, tp, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("TG1", "PAPER", dirn, s, TOK[s], s[-2:], 0, exp, 650, ts_of(D24, 10, 30), px(s, ts_of(D24, 10, 30)),
                 (60.0 if dirn == "SELL" else None), (10.0 if dirn == "SELL" else None), "OPEN"))

# VET saved Settings (sealed trend_len 36; live tf key is signal_tf)
from app.config import strategy_loader as SL               # noqa: E402
(SL.STRATEGY_DIR / "VET_V1.json").write_text(json.dumps({"trend_len": 36, "signal_tf": 5, "ema_fast1": 10, "ema_fast2": 20}))


def corpus_rows(where, params):
    c = sqlite3.connect(DB)
    try:
        return c.execute(f"SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE {where} ORDER BY ts", params).fetchall()
    finally:
        c.close()


def src_rows(where, params):
    return [tuple(r) for r in src.execute(f"SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE {where} ORDER BY ts",
                                         params).fetchall()]


def rep(scope, key, **kw):
    with audit_muted():
        return LR.build_live_replay(scope, key, **kw)


print("── 1. Trades page: TSG strangle (paper_trades) + Kite fill")
SPOT_W = "instrument_type='SPOT' AND ts>=? AND ts<?"
check("before: the corpus has no spot bars for 24 Feb", not corpus_rows(SPOT_W, (ts_of(D24, 0, 0), ts_of(D24, 23, 0))))
r = rep("trades", "TSG-2")
check("replay ok", r.get("ok"), r)
g = r.get("group") or {}
check("group = the four TSG legs (ORB in the same minute excluded)", sorted(g.get("ids") or []) == ["TSG-1", "TSG-2", "TSG-3", "TSG-4"], g.get("ids"))
check("trade_id echoes the page key", r.get("trade_id") == "TSG-2")
check("strategy is TSG_V1, scope trades, source live", (r.get("strategy_id"), r.get("scope"), r.get("source")) == ("TSG_V1", "trades", "live"))
check("basket MTM view offered for the strangle", any(v["key"] == "basket" for v in r.get("views") or []))
check("8 entry/exit markers", len(r.get("markers") or []) == 8, len(r.get("markers") or []))
spot = corpus_rows(SPOT_W, (ts_of(D24, 0, 0), ts_of(D24, 23, 0)))
check("after: 24 Feb spot bars added = Kite's bars, exactly", [tuple(x) for x in spot] == src_rows("instrument_token=1 AND ts>=? AND ts<?", (ts_of(D24, 0, 0), ts_of(D24, 23, 0))), len(spot))
check("spot written under the corpus's existing spot identity (no duplicate SPOT token)",
      {x[0] for x in sqlite3.connect(DB).execute("SELECT DISTINCT instrument_token FROM backtest_candles_1m WHERE instrument_type='SPOT'")} == {1})
leg = corpus_rows("tradingsymbol=? AND ts>=? AND ts<?", (SYMS["ce"], ts_of(D24, 0, 0), ts_of(D24, 23, 0)))
check("leg bars added for the trade day", [tuple(x) for x in leg] == src_rows("tradingsymbol=? AND ts>=? AND ts<?", (SYMS["ce"], ts_of(D24, 0, 0), ts_of(D24, 23, 0))), len(leg))
check("payload spot pane has the trade day's bars", len(r["spot"]["bars"]) >= 370, len(r["spot"]["bars"]))
check("notes say what was added from Kite", any("added from Kite" in n for n in r.get("notes") or []), r.get("notes"))
check("net = Σ(gross − charges) of the legs", abs(g["net"] - sum(l["net"] for l in g["legs"])) < 0.05)
check("payload is JSON-serialisable", bool(json.dumps(r)))

print("── 2. past rows are never modified; partial past day completed")
tam = corpus_rows("instrument_type='SPOT' AND ts=?", (TAMPER_TS,))
check("tampered 10 Feb 10:00 bar untouched (INSERT OR IGNORE)", tam and abs(tam[0][4] - 11111.11) < 1e-6, tam)
aft = corpus_rows(SPOT_W, (ts_of(D10, 12, 0), ts_of(D10, 23, 0)))
check("10 Feb afternoon (warm-up window) filled", len(aft) == len(src_rows("instrument_token=1 AND ts>=? AND ts<?", (ts_of(D10, 12, 0), ts_of(D10, 23, 0)))) and len(aft) > 100, len(aft))

print("── 3. second open: nothing asked again")
n0 = len(KITE.calls)
r = rep("trades", "TSG-1")
check("replay ok again", r.get("ok"))
check("no Kite call on a second open (complete / already asked)", len(KITE.calls) == n0, KITE.calls[n0:])

print("── 4. VET paper spread (vet_trades) — overlays on the saved Settings config")
r = rep("trades", "vet_trades:1")
check("VET replay ok", r.get("ok"), r)
check("main + wing grouped", sorted(r["group"]["ids"]) == ["vet_trades:1", "vet_trades:2"], r["group"]["ids"])
check("indicator builder ran (no overlay failure note)", not any("overlay unavailable" in n for n in r["notes"]), r["notes"])
check("drawn with Settings trend_len 36 (not the backtest default)", "SMA36" in (r.get("config_brief") or ""), r.get("config_brief"))
check("spot pane carries the VET channel overlays", any(o.get("id") == "channel" for o in r["spot"]["overlays"]))
check("parity detail says Settings-config", "Settings" in (r["parity"].get("detail") or ""), r["parity"])
check("note warns the config may differ from the trade's", any("current Settings" in n for n in r["notes"]), r["notes"])
check("SHORT main leg is short in the payload", any(l["short"] for l in r["group"]["legs"]))

print("── 5. Analytics: IC condor by group_id, BRK LIVE row from the paper table")
r = rep("analytics", "IC-L3")
check("IC replay ok", r.get("ok"), r)
check("condor grouped by group_id incl. the later adjustment leg", sorted(r["group"]["ids"]) == ["IC-ADJ", "IC-L1", "IC-L2", "IC-L3", "IC-L4"], r["group"]["ids"])
check("leg labels carry the trade_class tag", any("L1" in (v.get("label") or "") for v in r["views"]), [v.get("label") for v in r["views"]])
check("IC default view is the basket", r.get("default_view") == "basket")
prim = next(l for l in r["group"]["legs"] if l["id"] == r["group"]["primary_id"])
check("the condor is led by an opening leg, not the 11:00 adjustment", prim["key"] != "IC-ADJ" and prim["entry_ts"] == ts_of(D24, 9, 20), prim)
r = rep("analytics", "BRK-L1")
check("BRK LIVE row found on Analytics", r.get("ok") and r["group"]["ids"] == ["BRK-L1"], r.get("error"))
r = rep("trades", "BRK-L1")
check("…and on the Trades page (it lists paper_trades of every mode)", r.get("ok"))
check("an Analytics key is not looked up on the Trades page", rep("trades", "IC-L1").get("status") == 404)

print("── 6. open position, 'today' = 25 Feb 13:00:05 — upsert only today")
reset_fill_memory()
NOW[0] = ts_of(D25, 13, 0, 5)          # 12:59 closed 5 s ago (≥ CLOSE_GRACE_S) — writable
KITE.cut = ts_of(D25, 13, 0)
r = rep("trades", "tma2_trades:1")
check("open TMA_V2 replay ok", r.get("ok"), r)
check("window runs from the entry day to today", r["window"]["days"][:1] == [D24.isoformat()] and r["window"]["days"][-1] == D25.isoformat(), r["window"]["days"])
check("open legs reported", r["group"]["open_legs"] == 2 and any("still open" in n for n in r["notes"]), r["notes"])
check("no exit markers for open legs", not any(m["kind"] == "exit" for m in r["markers"]))
today_bars = corpus_rows("tradingsymbol=? AND ts>=? AND ts<?", (SYMS["atm_ce"], ts_of(D25, 0, 0), ts_of(D25, 23, 0)))
check("today's bars so far are in the corpus", today_bars and today_bars[-1][0] == ts_of(D25, 12, 59), today_bars[-1:] if today_bars else None)
last_ts = ts_of(D25, 12, 59)
KITE.bump[(TOK[SYMS["atm_ce"]], last_ts)] = 777.77
LR.TODAY_REFRESH_S = 0
r = rep("trades", "tma2_trades:2")
moved = corpus_rows("tradingsymbol=? AND ts=?", (SYMS["atm_ce"], last_ts))
check("today's forming bar is UPDATED on a re-pull (upsert)", moved and abs(moved[0][4] - 777.77) < 1e-6, moved)
LR.TODAY_REFRESH_S = 10 ** 6
n0 = len(KITE.calls)
rep("trades", "tma2_trades:1")
check("today is not re-pulled inside the refresh interval", len(KITE.calls) == n0, KITE.calls[n0:])
KITE.cut = None
NOW[0] = ts_of(date(2025, 3, 10), 12, 0)

print("── 7. expired contract the corpus lacks")
r = rep("trades", "ORB-EXP")
check("replay still ok", r.get("ok"), r)
check("note names the contract and says no Kite token", any(SYMS["exp"] in n and "no Kite token" in n for n in r["notes"]), r["notes"])
check("nothing invented for it", not corpus_rows("tradingsymbol=? AND ts>=?", (SYMS["exp"], ts_of(D24, 0, 0))))
APP.execute("UPDATE paper_trades SET token=? WHERE paper_trade_id='ORB-EXP'", (TOK[SYMS["exp"]],))
reset_fill_memory()
r = rep("trades", "ORB-EXP")
check("a token stored on the row is used when the dump lacks the contract",
      len(corpus_rows("tradingsymbol=? AND ts>=? AND ts<?", (SYMS["exp"], ts_of(D24, 0, 0), ts_of(D24, 23, 0)))) > 300, r["notes"])

print("── 8. no Kite session")
reset_fill_memory()
c = sqlite3.connect(DB)
c.execute("DELETE FROM backtest_candles_1m WHERE tradingsymbol=? AND ts>=?", (SYMS["atm_ce"], ts_of(D24, 0, 0)))
c.commit()
c.close()
LR._kite = lambda: None
r = rep("trades", "ORB-1")
check("replay ok without Kite", r.get("ok"), r)
check("note says corpus only", any("no Kite session" in n for n in r["notes"]), r["notes"])
LR._kite = lambda: KITE
n0 = len(KITE.calls)
r = rep("trades", "ORB-1", fill=False)
check("fill=False never calls Kite", len(KITE.calls) == n0)

print("── 9. route on the REAL api_server app (ASGI, no TestClient)")
import app.api_server as API                               # noqa: E402
from app.license import license_state                      # noqa: E402
_real_level = license_state.ui_level


def call(path, qs, level):
    license_state.ui_level = lambda: level
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": qs.encode(), "headers": [],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "root_path": ""}
    with audit_muted():
        asyncio.run(API.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


st, body = call("/api/replay/trade", "scope=trades&key=TSG-1&fill=0", "admin")
check("admin → 200 with the payload", st == 200 and json.loads(body).get("ok"), (st, body[:200]))
st, _ = call("/api/replay/trade", "scope=trades&key=TSG-1&fill=0", "standard")
check("non-admin → 403 (router behind _require_admin_ui)", st == 403, st)
st, _ = call("/api/replay/trade", "scope=nope&key=TSG-1", "admin")
check("bad scope → 400", st == 400, st)
st, _ = call("/api/replay/trade", "scope=analytics&key=NOPE", "admin")
check("unknown key → 404", st == 404, st)
st, _ = call("/api/backtest/coverage", "", "standard")
check("backtest wall unchanged (non-admin 403)", st == 403, st)
license_state.ui_level = _real_level

print("── 11. live mode — forming bar + provisional indicators (TRADE_REPLAY_STREAM_20261017)")
LR._TRACKER_THREAD = False
LR.TODAY_REFRESH_S = 30
reset_fill_memory()
LTP = {}
LR._read_ltp = lambda ident: (LTP[ident], NOW[0]) if ident in LTP else None
MONO = [1000.0]
LR._mono = lambda: MONO[0]
CE, CEW = SYMS["atm_ce"], SYMS["cew"]
KITE.cut = None
seq = [(1, 23400.0, 80.0, 20.0), (10, 23410.0, 82.0, 21.0), (20, 23395.0, 79.0, 19.5), (30, 23405.0, 81.0, 20.5)]
for k, (sec, sp_, ce_, cew_) in enumerate(seq):
    NOW[0] = ts_of(D25, 13, 4, sec)
    LTP.update({LR.SPOT_IDENT: sp_, CE: ce_, CEW: cew_})
    if k == 0:
        LR._TRACKER.touch([LR.SPOT_IDENT, CE, CEW])
    else:
        LR._TRACKER.sample_once()
F = ts_of(D25, 13, 4)
r = rep("trades", "tma2_trades:1", live=True)
lv = r.get("live") or {}
check("live replay ok and active in session", r.get("ok") and lv.get("active"), (r.get("error"), lv))
check("forming minute = 13:04", lv.get("forming_ts") == F, lv.get("forming_ts"))
check("leg forming bar = sampled o/h/l/c", r["legs_bars"][CE][-1] == [F, 80.0, 82.0, 79.0, 81.0], r["legs_bars"][CE][-1:])
check("spot forming bar = sampled o/h/l/c", r["spot"]["bars"][-1] == [F, 23400.0, 23410.0, 23395.0, 23405.0], r["spot"]["bars"][-1:])
check("forming bar never written to the corpus", not corpus_rows("tradingsymbol=? AND ts>=?", (CE, F)))
check("Kite fill wrote the closed 13:03 minute", bool(corpus_rows("tradingsymbol=? AND ts=?", (CE, ts_of(D25, 13, 3)))))
check("…and nothing at/after the forming minute even though Kite served it",
      not corpus_rows("instrument_type='SPOT' AND ts>=? AND ts<?", (F, ts_of(D25, 23, 0))))
prov = [o for o in r["spot"]["overlays"] if o.get("provisional")]
b0 = ts_of(D25, 13, 0)        # 5m bucket holding 13:04 (09:15 grid)
check("TMA_V2 spot indicators carry provisional segments", len(prov) >= 1, [o.get("id") for o in r["spot"]["overlays"]])
check("provisional points sit on the forming 5m bar, joined to the last closed point",
      all(int(p[0]) >= b0 for o in prov for p in o["points"][1:]) and all(int(o["points"][0][0]) < b0 for o in prov),
      [o["points"][:3] for o in prov])
closed_lines = [o for o in r["spot"]["overlays"] if o.get("type") == "line" and not o.get("provisional")]
check("closed parts end before the forming bar", all(int(p[0]) < b0 for o in closed_lines for p in o["points"]))
check("provisional and closed parts share a label (one legend chip)",
      {o["label"] for o in prov} <= {o["label"] for o in closed_lines})
live_spot = [o for o in r["spot"]["overlays"] if o.get("kind") == "live"]
check("live price line on the spot pane", live_spot and live_spot[0]["value"] == 23405.0, live_spot)
live_leg = [l for l in r["premium"]["levels"].get(CE, []) if l.get("kind") == "live"]
check("LTP line on the leg's pane", live_leg and live_leg[0]["value"] == 81.0, live_leg)
ll = {x["symbol"]: x for x in lv.get("legs") or []}
check("live.legs: short label strike+type", ll.get(CE, {}).get("label") == CE[-7:], ll.get(CE))
check("live lines keep a stable legend label; the price rides on the axis tag",
      live_spot[0]["label"] == "live price" and live_spot[0]["tag"] == "live 23405.00" and live_leg[0]["label"] == "LTP")
check("live.legs: SL distance and pct", ll.get(CE, {}).get("sl_dist") == 21.0 and abs(ll[CE]["sl_pct"] - 25.93) < 0.01, ll.get(CE))
check("live.legs: short leg P&L at LTP", ll[CE]["pnl"] == round((ll[CE]["entry"] - 81.0) * 650, 2), ll[CE])
check("feed fresh → not stale", lv.get("stale") is False and lv.get("max_age_s") == 0, lv)
check("provisional padding stripped: nothing after the forming minute in the payload",
      all(b[0] <= F for b in r["spot"]["bars"]) and all(b[0] <= F for v in r["legs_bars"].values() for b in v)
      and all(int(p[0]) <= F for o in r["spot"]["overlays"] for p in (o.get("points") or []))
      and all(p[0] <= F for p in ((r.get("mtm") or {}).get("path") or [])))
NOW[0] = ts_of(D25, 13, 5, 5)
LTP.update({LR.SPOT_IDENT: 23401.0, CE: 83.0, CEW: 21.5})
LR._TRACKER.sample_once()
NOW[0] = ts_of(D25, 13, 5, 8)
LR.LIVE_GAP_S = 10 ** 6
r = rep("trades", "tma2_trades:1", live=True)
bars = {b[0]: b for b in r["legs_bars"][CE]}
check("a closed minute Kite has not served yet is bridged by the sampler", bars.get(F) == [F, 80.0, 82.0, 79.0, 81.0], bars.get(F))
check("new forming minute 13:05", r["live"]["forming_ts"] == ts_of(D25, 13, 5) and bars.get(ts_of(D25, 13, 5), [0])[4] == 83.0)
prov = [o for o in r["spot"]["overlays"] if o.get("provisional")]
b5 = ts_of(D25, 13, 5)
check("mid-bucket (13:05 of the 13:05–13:09 bar): TMA's completed-bar code still values the forming 5m bar",
      len(prov) >= 1 and all(int(p[0]) == b5 for o in prov for p in o["points"][1:]), [o["points"][-2:] for o in prov])
check("…and draws nothing past it", all(int(p[0]) <= b5 for o in r["spot"]["overlays"] for p in (o.get("points") or [])))
LR.LIVE_GAP_S = 0
r = rep("trades", "tma2_trades:1", live=True)
bars = {b[0]: b for b in r["legs_bars"][CE]}
src_1304 = src_rows("tradingsymbol=? AND ts=?", (CE, F))[0]
check("once Kite serves the minute, the corpus bar wins", tuple(bars[F]) == src_1304, (bars[F], src_1304))
LR.LIVE_GAP_S = 5
LTP.pop(CEW)
LR._TRACKER.bars.pop(CEW, None)
LR._TRACKER.last.pop(CEW, None)
r = rep("trades", "tma2_trades:1", live=True)
check("a leg without a live price is flagged (stale + note)", CEW in r["live"]["no_feed"] and r["live"]["stale"]
      and any("no live price" in n for n in r["notes"]), r["live"])
r = rep("trades", "tma2_trades:1")
check("live=False → snapshot only (no forming, no provisional)", r["live"]["active"] is False
      and not any(o.get("provisional") for o in r["spot"]["overlays"]))
r = rep("trades", "TSG-1", live=True)
check("closed trade with live=1 → not live", r["live"]["active"] is False and r["live"]["open"] is False)
NOW[0] = ts_of(D25, 15, 45)
r = rep("trades", "tma2_trades:1", live=True)
check("after 15:30 → not live, says market closed", r["live"]["active"] is False and r["live"]["session"] is False)
MONO[0] += 1000
LR._TRACKER.sample_once()
check("sampler drops instruments nobody polls (TTL)", not LR._TRACKER.watch, LR._TRACKER.watch)
NOW[0] = ts_of(date(2025, 3, 10), 12, 0)
KITE.cut = None

print("── 12. Dashboard lookup (find_keys)")
with audit_muted():
    f = LR.find_keys(strategy="TMA_V2")
check("by strategy → its newest open group, Trades-prefixed", f.get("ok") and f["key"] in ("t|tma2_trades:1", "t|tma2_trades:2"), f)
check("nav = the strategy's rows in entry order", f["nav"] == ["t|tma2_trades:1", "t|tma2_trades:2"], f["nav"])
with audit_muted():
    f = LR.find_keys(symbol=SYMS["ce"], entry_ts=E24)
check("by symbol + entry time → that row (TSG leg, not the IC leg 4 min later)", f.get("key") == "t|TSG-1", f)
with audit_muted():
    f = LR.find_keys(strategy="IC_V2")
check("Analytics-only rows are a|-prefixed, book LIVE", f.get("key", "").startswith("a|IC-") and f["book"] == "LIVE", f)
with audit_muted():
    f = LR.find_keys(strategy="BRK_V1")
check("a row on both pages is listed once (as Trades)", f.get("key") == "t|BRK-L1" and f["nav"] == ["t|BRK-L1"], f)
with audit_muted():
    f = LR.find_keys(strategy="TSG_V1", book="LIVE")
check("book mismatch falls back to the strategy's rows", f.get("key", "").startswith("t|TSG-"), f)
with audit_muted():
    f = LR.find_keys(symbol="NOPE", entry_ts=1)
check("nothing near → 404", f.get("status") == 404)

print("── 13. stream routes on the REAL api_server app")
st, body = call("/api/replay/find", "strategy=TSG_V1", "admin")
check("find: admin → 200", st == 200 and json.loads(body)["key"].startswith("t|TSG-"), (st, body[:160]))
st, _ = call("/api/replay/find", "strategy=TSG_V1", "standard")
check("find: non-admin → 403", st == 403, st)
st, body = call("/api/replay/trade", "scope=trades&key=TSG-1&fill=0&live=1", "admin")
check("trade?live=1 accepted (closed trade → live inactive)", st == 200 and json.loads(body)["live"]["active"] is False, (st, body[:160]))
st, _ = call("/api/replay/trade", "scope=trades&key=TSG-1&live=1", "standard")
check("trade?live=1: non-admin → 403", st == 403, st)
license_state.ui_level = _real_level

print("── 10. helpers")
check("row_key: page id first", LR.row_key({"paper_trade_id": 7, "entry_time": 1, "symbol": "X"}, "trades") == "7")
check("row_key: the pages' fallback", LR.row_key({"entry_time": 1700000000, "symbol": "NIFTYX"}, "trades") == "1700000000_NIFTYX")
check("row_key: analytics uses trade_id / tradingsymbol", LR.row_key({"entry_time": 5, "tradingsymbol": "Y"}, "analytics") == "5_Y")
p = LR._parse_symbol("NIFTY25O1425500CE")
check("weekly symbol parsed", (p["expiry"], p["strike"], p["instrument_type"]) == ("2025-10-14", 25500.0, "CE"), p)
p = LR._parse_symbol("NIFTY25OCT25500PE")
check("monthly symbol parsed (month only)", (p["expiry"], p["strike"], p["instrument_type"]) == ("2025-OCT", 25500.0, "PE"), p)
check("canon_sid maps page names", [LR.canon_sid(x) for x in ("SCALP V1", "1M_SCALP", "BB", "HA", "TMA V2")] ==
      ["SCALP_V1", "SCALP_V1", "BB_V1", "HA_V1", "TMA_V2"])
check("live_cfg never seeds a missing strategy file", (LR.live_cfg("ORB_V1")[1] or "").startswith("no saved") and
      not (SL.STRATEGY_DIR / "ORB_V1.json").exists())
check("VET signal_tf aliased to timeframe_minutes", LR.live_cfg("VET_V1")[0].get("timeframe_minutes") == 5)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL TRADE_REPLAY_LIVE CHECKS PASSED")
shutil.rmtree(TMP, ignore_errors=True)
