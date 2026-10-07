#!/usr/bin/env python3
# mcx_probe.py — read-only. Can Scalp App's Kite data session see MCX crude
# options, and what do the contracts look like?  Answers the questions the
# TradingView-alert paper strategy (TVX) depends on BEFORE any code is built.
#
# Places no orders, writes nothing, opens no WebSocket. Makes 4 REST calls:
# profile, instruments("MCX"), one quote for the futures, one quote for the
# ATM CE/PE pair. Run it after 15:30 IST to stay clear of the fleet's quote budget.
#
# USAGE (from the repo root, with the same python you run the suite with):
#   cd ~/dev/scalp-app/backend && python3 ../mcx_probe.py
#   python3 ../mcx_probe.py --name CRUDEOILM        # mini crude instead
#
# Paste the whole output back into the chat.

from __future__ import annotations
import argparse, datetime as dt, sys, os
from collections import Counter, defaultdict

sys.path.insert(0, os.getcwd())          # run from backend/ so `app` imports

ap = argparse.ArgumentParser()
ap.add_argument("--name", default="CRUDEOIL", help="MCX underlying name (CRUDEOIL / CRUDEOILM)")
args = ap.parse_args()

from app.brokers.zerodha_manager import ZerodhaManager   # noqa: E402

zm = ZerodhaManager()
if not zm.refresh():
    sys.exit("ZerodhaManager.refresh() failed — log in to Kite in the app first, then re-run.")
kite = zm.get_data_kite()
if kite is None:
    sys.exit("No data Kite session (data token missing/expired). Log in via the app and re-run.")

def hr(t): print(f"\n=== {t} " + "=" * max(0, 60 - len(t)))

# 1. Which exchanges is the data account enabled for?
hr("1. profile")
try:
    p = kite.profile()
    print("user_id   :", p.get("user_id"))
    print("exchanges :", p.get("exchanges"))
    print("products  :", p.get("products"))
except Exception as e:
    print("profile() failed:", e)

# 2. MCX instrument dump for the underlying
hr(f"2. instruments(MCX) name={args.name}")
try:
    inst = kite.instruments("MCX")
except Exception as e:
    sys.exit(f"instruments('MCX') failed: {e}")
rows = [r for r in inst if r.get("name") == args.name]
print("MCX rows total:", len(inst), "| rows for", args.name, ":", len(rows))
print("segments:", dict(Counter(r.get("segment") for r in rows)))
if not rows:
    names = sorted({r.get("name") for r in inst if "CRUDE" in str(r.get("name"))})
    sys.exit(f"No rows for {args.name}. Crude-like names present: {names}")

today = dt.date.today()
futs = sorted([r for r in rows if r.get("instrument_type") == "FUT" and r["expiry"] >= today],
              key=lambda r: r["expiry"])
opts = [r for r in rows if r.get("instrument_type") in ("CE", "PE") and r["expiry"] >= today]

print("\nFutures (upcoming):")
for r in futs[:3]:
    print(f"  {r['tradingsymbol']:<22} expiry {r['expiry']}  lot_size {r['lot_size']}  tick {r['tick_size']}  token {r['instrument_token']}")

by_exp = defaultdict(list)
for r in opts:
    by_exp[r["expiry"]].append(r)
print("\nOption expiries (upcoming):")
for e in sorted(by_exp)[:3]:
    strikes = sorted({float(r["strike"]) for r in by_exp[e]})
    steps = Counter(round(b - a, 2) for a, b in zip(strikes, strikes[1:]))
    lots = Counter(r["lot_size"] for r in by_exp[e])
    print(f"  {e}  strikes {len(strikes)} ({strikes[0]:.0f}–{strikes[-1]:.0f})  "
          f"step(s) {dict(steps.most_common(3))}  lot_size {dict(lots)}  "
          f"days_to_expiry {(e - today).days}")

if not futs or not by_exp:
    sys.exit("Missing futures or options — cannot quote.")

# 3. Front futures quote (the ATM reference)
hr("3. front futures quote")
fut = futs[0]
fkey = f"MCX:{fut['tradingsymbol']}"
q = kite.quote([fkey]).get(fkey, {})
fut_ltp = float(q.get("last_price") or 0)
print(f"{fkey}  ltp {fut_ltp}  last_trade_time {q.get('last_trade_time')}  "
      f"volume {q.get('volume')}  oi {q.get('oi')}")
if fut_ltp <= 0:
    sys.exit("Futures LTP is 0 — no MCX market-data entitlement on this key, or market closed long ago.")

# 4. ATM CE/PE on the nearest option expiry
hr("4. nearest-expiry ATM CE/PE quote")
exp0 = sorted(by_exp)[0]
strikes0 = sorted({float(r["strike"]) for r in by_exp[exp0]})
atm = min(strikes0, key=lambda k: abs(k - fut_ltp))
pair = {r["instrument_type"]: r for r in by_exp[exp0] if float(r["strike"]) == atm}
keys = [f"MCX:{pair[s]['tradingsymbol']}" for s in ("CE", "PE") if s in pair]
qq = kite.quote(keys)
print(f"expiry {exp0}  futures {fut_ltp}  ATM strike {atm:.0f}")
for k in keys:
    d = qq.get(k, {})
    dep = d.get("depth") or {}
    bid = (dep.get("buy") or [{}])[0].get("price")
    ask = (dep.get("sell") or [{}])[0].get("price")
    print(f"  {k:<32} ltp {d.get('last_price')}  bid {bid}  ask {ask}  "
          f"last_trade {d.get('last_trade_time')}  vol {d.get('volume')}  oi {d.get('oi')}")
    if k.endswith("CE") or k.endswith("PE"):
        r = pair[k[-2:]]
        print(f"    lot_size {r['lot_size']}  tick {r['tick_size']}  token {r['instrument_token']}")

hr("done")
print("Paste everything above into the chat.")
