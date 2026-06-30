#!/usr/bin/env python3
"""
extract_preentry.py  —  SCALP_V5 pre-entry market-state extractor

PURPOSE
  For each trade, look at the option's OWN 1-min candles in the window BEFORE
  entry and compute market-state features available AT the moment of entry:

    atr_pre      Average True Range over the N candles before entry (volatility
                 regime). High ATR = noisy/choppy; low ATR = orderly.
    atr_pct      atr_pre as % of entry price (scale-free volatility).
    mom_pre      premium velocity: (entry_price - price_Kcandles_ago), the recent
                 push into the entry (pre-entry momentum).
    range_pre    high-low range over the pre-entry window / entry price (%).
    n_pre        how many pre-entry candles were actually found (data quality).

  These are PROXIES (option premium, not index spot — spot isn't in the DB).
  Rough but free: they capture "is this option moving cleanly or chopping" at
  entry, which is what an entry-quality filter would gate on.

SAFETY
  READ-ONLY (sqlite mode=ro). No writes, no app imports, stdlib only.

USAGE
  python3 extract_preentry.py \
    --db /Users/anbu/.scalp-app/backtest/backtest.db \
    --trades /Users/anbu/Downloads/2023.csv \
    --out /Users/anbu/Downloads/2023_preentry.csv \
    --year 2023 \
    --candle-table backtest_candles_1m \
    --col-symbol tradingsymbol --col-ts ts \
    --col-open open --col-high high --col-low low --col-close close \
    --ts-unit s --pre-min 30
  (--pre-min 30 => look back 30 minutes of 1-min candles before entry.)
"""
import argparse, csv, os, sqlite3, sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

def open_ro(db):
    if not os.path.exists(db): sys.exit(f"ERROR: DB not found: {db}")
    con = sqlite3.connect(f"file:{os.path.abspath(db)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con

def parse_ts(s, year):
    s=s.strip().strip('"'); dm,hm=s.split(","); d,mo=dm.strip().split("/"); h,mi=hm.strip().split(":")
    return datetime(year,int(mo),int(d),int(h),int(mi),tzinfo=IST)

def load_trades(path, year):
    rows=list(csv.reader(open(path,newline="")))
    ti=next((i for i,r in enumerate(rows) if r and r[0]=="TRADES"),None)
    if ti is None: sys.exit("ERROR: no TRADES block.")
    out=[]
    for r in rows[ti+2:]:
        if not r or len(r)<12: continue
        sym,ets,entry,sl,tp,xts,exit_,reason,gross,charges,net,amb=r[:12]
        try: e=parse_ts(ets,year)
        except: continue
        out.append(dict(symbol=sym.strip(), entry=float(entry), reason=reason.strip(),
                        net=float(net), entry_dt=e))
    return out

def to_db_ts(dt,u):
    if u=="s": return int(dt.timestamp())
    if u=="ms": return int(dt.timestamp()*1000)
    if u=="iso": return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    raise ValueError(u)

def fetch_pre(con, table, cs, ct, co, ch, cl, cc, symbol, entry_dt, pre_min, unit):
    cur=con.cursor()
    lo=to_db_ts(entry_dt - timedelta(minutes=pre_min+1), unit)
    hi=to_db_ts(entry_dt, unit)
    q=(f'SELECT "{ct}" AS ts,"{co}" AS o,"{ch}" AS h,"{cl}" AS l,"{cc}" AS c '
       f'FROM "{table}" WHERE "{cs}"=? AND "{ct}">=? AND "{ct}"<=? ORDER BY "{ct}" ASC')
    try: rows=cur.execute(q,(symbol,lo,hi)).fetchall()
    except sqlite3.DatabaseError as e: return None,str(e)
    out=[]
    for r in rows:
        ts=r["ts"]
        if unit=="s": dt=datetime.fromtimestamp(int(ts),IST)
        elif unit=="ms": dt=datetime.fromtimestamp(int(ts)/1000,IST)
        else: dt=datetime.strptime(str(ts)[:19],"%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        out.append((dt,float(r["o"]),float(r["h"]),float(r["l"]),float(r["c"])))
    return out,None

def compute(trade, candles):
    res=dict(atr_pre="",atr_pct="",mom_pre="",range_pre="",n_pre=len(candles))
    if len(candles)<2: return res
    # ATR: mean true range over candles
    trs=[]
    for i in range(1,len(candles)):
        _,_,h,l,_=candles[i]
        prev_close=candles[i-1][4]
        tr=max(h-l, abs(h-prev_close), abs(l-prev_close))
        trs.append(tr)
    atr=sum(trs)/len(trs) if trs else 0
    entry=trade["entry"]
    res["atr_pre"]=round(atr,3)
    res["atr_pct"]=round(100*atr/entry,3) if entry else ""
    # pre-entry momentum: entry vs close ~K candles ago (use earliest available in window)
    first_close=candles[0][4]
    res["mom_pre"]=round(entry-first_close,2)
    # range over window
    hi=max(c[2] for c in candles); lo=min(c[3] for c in candles)
    res["range_pre"]=round(100*(hi-lo)/entry,3) if entry else ""
    return res

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",required=True); ap.add_argument("--trades",required=True)
    ap.add_argument("--out",required=True); ap.add_argument("--year",type=int,required=True)
    ap.add_argument("--candle-table",required=True); ap.add_argument("--col-symbol",required=True)
    ap.add_argument("--col-ts",required=True); ap.add_argument("--col-open",required=True)
    ap.add_argument("--col-high",required=True); ap.add_argument("--col-low",required=True)
    ap.add_argument("--col-close",required=True); ap.add_argument("--ts-unit",default="s",choices=["s","ms","iso"])
    ap.add_argument("--pre-min",type=int,default=30); ap.add_argument("--limit",type=int,default=0)
    a=ap.parse_args()
    con=open_ro(a.db)
    trades=load_trades(a.trades,a.year)
    if a.limit: trades=trades[:a.limit]
    print(f"Loaded {len(trades)} trades (year {a.year}).")
    probe,perr=fetch_pre(con,a.candle_table,a.col_symbol,a.col_ts,a.col_open,a.col_high,a.col_low,a.col_close,
                         trades[0]["symbol"],trades[0]["entry_dt"],a.pre_min,a.ts_unit)
    if perr: sys.exit(f"ERROR: {perr}")
    if not probe: print("WARNING: 0 pre-entry candles for first trade — check flags / pre-min.")
    cols=["symbol","entry","reason","net","n_pre","atr_pre","atr_pct","mom_pre","range_pre"]
    nm=0
    with open(a.out,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
        for i,t in enumerate(trades):
            cs,err=fetch_pre(con,a.candle_table,a.col_symbol,a.col_ts,a.col_open,a.col_high,a.col_low,a.col_close,
                             t["symbol"],t["entry_dt"],a.pre_min,a.ts_unit)
            if err: sys.exit(f"ERROR trade {i}: {err}")
            if cs: nm+=1
            row=dict(symbol=t["symbol"],entry=t["entry"],reason=t["reason"],net=t["net"])
            row.update(compute(t,cs or []))
            w.writerow(row)
            if (i+1)%200==0: print(f"  ...{i+1}/{len(trades)} ({nm} matched)")
    print(f"\nDone. {nm}/{len(trades)} had pre-entry candles. Wrote {a.out}")
    if nm==0: print("NOTE: nothing matched — schema/format mismatch, not a real result.")
    else: print("\nNext: upload the *_preentry.csv files and I'll test ATR/momentum entry filters\nwith the 50/50 monster-survival lens.")

if __name__=="__main__":
    main()