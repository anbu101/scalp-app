# backend/app/backtest/util/test_lot_sizes.py
# ── STOCK_LOT_AUTO_20260828 ── behavioural tests. No network, no app imports.
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lot_sizes as L  # noqa: E402

HDR = ("EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,"
       "UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,"
       "LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE")


def row(instr, sym, lot, exp, exch="NSE"):
    return (f"{exch},D,1,NA,{instr},100,{sym},{sym}-x,{sym} x,OPT,NA,"
            f"{lot},{exp},700.0,CE")


MASTER = "\n".join([
    HDR,
    row("OPTSTK", "HDFCBANK", 650, "2026-09-29"),
    row("OPTSTK", "HDFCBANK", 650, "2026-10-27"),
    row("OPTSTK", "RELIANCE", 500, "2026-09-29"),
    # lot revision in flight: near month 250, far month 300
    row("OPTSTK", "DIXON", 250, "2026-09-29"),
    row("OPTSTK", "DIXON", 300, "2026-12-29"),
    # expired rows must never win over live ones
    row("OPTSTK", "RELIANCE", 250, "2024-01-25"),
    row("OPTIDX", "NIFTY", 75, "2026-09-24"),
    row("OPTSTK", "SOMEBSE", 100, "2026-09-29", exch="BSE"),   # wrong exchange
    row("OPTSTK", "BADLOT", 0, "2026-09-29"),                  # lot 0 -> dropped
]) + "\n"

TODAY = date(2026, 8, 28)
fails = []


def ck(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        fails.append(name)


p = L.parse_master_lots(MASTER, today=TODAY)
ck("HDFCBANK resolves to 650", p["lots"].get("HDFCBANK") == 650,
   f"got {p['lots'].get('HDFCBANK')}")
ck("nearest-expiry lot wins over far month", p["lots"].get("DIXON") == 250,
   f"got {p['lots'].get('DIXON')}")
ck("far-month revision surfaced as pending",
   p["detail"]["DIXON"].get("pending", {}).get("lot") == 300)
ck("no false pending when lots agree", "pending" not in p["detail"]["HDFCBANK"])
ck("expired row does not override live", p["lots"].get("RELIANCE") == 500,
   f"got {p['lots'].get('RELIANCE')}")
ck("non-NSE row excluded", "SOMEBSE" not in p["lots"])
ck("lot 0 row excluded", "BADLOT" not in p["lots"])
ck("index lots recorded separately",
   p["index_lots"].get("NIFTY") == 75 and "NIFTY" not in p["lots"])

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    cache = td / "lot_sizes.json"
    L.refresh_cache(master_text=MASTER, cache_path=cache, today=TODAY)
    ck("cache written", cache.exists())

    # index constant must NOT come from the master (sealed strategies)
    lot, src = L.resolve_lot(underlying="NIFTY", is_stock=False, cfg_lot=0,
                             index_lot=65, cache_path=cache)
    ck("index keeps its constant, ignores master 75",
       lot == 65 and src == "index-const", f"got {lot}/{src}")

    lot, src = L.resolve_lot(underlying="HDFCBANK", is_stock=True, cfg_lot=0,
                             index_lot=65, cache_path=cache)
    ck("stock resolves from cache", lot == 650 and src.startswith("scrip-master@"),
       f"got {lot}/{src}")

    lot, src = L.resolve_lot(underlying="HDFCBANK", is_stock=True, cfg_lot=999,
                             index_lot=65, cache_path=cache)
    ck("explicit config overrides everything", lot == 999 and src == "config")

    # corpus meta path
    db = td / "HDFCBANK.db"
    sqlite3.connect(db).close()
    L.write_corpus_meta(str(db), lot_size=650, lot_size_asof="2026-08-28")
    ck("corpus meta round-trips",
       L.read_corpus_meta(str(db), "lot_size") == "650")

    # stale cache + no network: master tier fails, corpus meta must catch it
    c = json.load(open(cache))
    c["fetched_epoch"] = time.time() - 400 * 86400
    json.dump(c, open(cache, "w"))
    lot, src = L.resolve_lot(underlying="HDFCBANK", is_stock=True, cfg_lot=0,
                             index_lot=65, db_path=str(db), cache_path=cache,
                             allow_network=False)
    ck("stale cache falls through to corpus meta",
       lot == 650 and src.startswith("corpus-meta@"), f"got {lot}/{src}")

    # stale cache, no corpus meta -> stale beats abort
    lot, src = L.resolve_lot(underlying="RELIANCE", is_stock=True, cfg_lot=0,
                             index_lot=65, cache_path=cache, allow_network=False)
    ck("stale cache used rather than aborting",
       lot == 500 and "STALE" in src, f"got {lot}/{src}")

    # nothing anywhere -> unresolved, and legacy map still honoured
    lot, src = L.resolve_lot(underlying="NOSUCHCO", is_stock=True, cfg_lot=0,
                             index_lot=65, cache_path=cache, allow_network=False)
    ck("unknown symbol is fail-closed", lot is None and src == "unresolved")
    lot, src = L.resolve_lot(underlying="LEGACY", is_stock=True, cfg_lot=0,
                             index_lot=65, cache_path=cache, allow_network=False,
                             static_map={"LEGACY": 42})
    ck("legacy static map still honoured", lot == 42 and src == "static-map")

    # missing cache entirely + no network
    os.remove(cache)
    lot, src = L.resolve_lot(underlying="HDFCBANK", is_stock=True, cfg_lot=0,
                             index_lot=65, cache_path=cache, allow_network=False)
    ck("no cache, no network -> unresolved", lot is None and src == "unresolved")

    # ── gap scan: synthetic 1:1 bonus ──
    gdb = td / "GAP.db"
    conn = sqlite3.connect(gdb)
    conn.execute("""CREATE TABLE backtest_candles_1m (
        instrument_token INTEGER, ts INTEGER, underlying TEXT,
        tradingsymbol TEXT, instrument_type TEXT, strike REAL, expiry TEXT,
        open REAL, high REAL, low REAL, close REAL, volume INTEGER, oi INTEGER)""")
    base = 1755000000
    for i in range(6):
        px = 2050.0 if i < 3 else 1025.0          # halves at i==3
        conn.execute("INSERT INTO backtest_candles_1m VALUES "
                     "(1,?, 'GAP','GAP','SPOT',0,'',?,?,?,?,0,0)",
                     (base + i * 86400, px, px, px, px))
    conn.commit()
    conn.close()
    hits = L.corpus_gap_scan(str(gdb), "GAP")
    ck("gap scan catches the 50% cut", len(hits) == 1 and hits[0]["pct"] < -49,
       f"got {hits}")
    ck("gap scan clean on flat series",
       L.corpus_gap_scan(str(gdb), "NOSUCH") == [])

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)