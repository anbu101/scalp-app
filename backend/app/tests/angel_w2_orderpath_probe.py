#!/usr/bin/env python3
"""
angel_w2_orderpath_probe.py
===========================
W2 LIVE PROBE — verifies the ASSUMED Angel endpoints (design doc §3.2)
with ONE tiny real trade, and observes GTT trigger-fire semantics.

  T1  getRMS            funds/balance shape (for the D9 balance pill)
  T2  getPosition       baseline read (field names for POS_DECOMP)
  T3  placeOrder        BUY 1 lot far-OTM NIFTY CE (protected LIMIT)
  T4  order polling     order book + individual status -> fill/avg-price
  T5  getPosition       post-fill row (day buy qty/price equivalents?)
  T6  GTT OCO           protect the REAL position, verify via ruleDetails
  T7  cancel GTT        cancel-verified (real-position variant)
  T8  getTradeBook      trade rows shape
  T9  EXIT              default: GTT trigger-fire exit (observes the full
                        trigger -> order -> fill chain); falls back to a
                        direct SELL if the trigger doesn't fire in time.
                        --no-trigger-test skips straight to direct SELL.
  T10 getRMS            margin delta after round-trip

REAL MONEY, SMALL: one lot of a ~1000-OTM weekly NIFTY CE. Round-trip
cost = spread + charges (typically well under Rs.150 total; the premium
itself comes back on exit). The script shows the exact premium outlay
and refuses to arm above --max-cost (default Rs.400 premium).

SAFETY RAILS
  - Requires market hours (09:20-15:20 IST); --force to override window.
  - Requires typing exactly "ARM LIVE".
  - Any failure after a fill triggers AUTO-FLATTEN in a finally block;
    if flatten cannot be confirmed, the script prints LOUD manual
    cleanup instructions (position + any GTT rule ids).
  - Credentials/PIN/TOTP/tokens are never printed.

RUN (from the machine whose IP is registered on the SmartAPI app):
  pip3 install requests pyotp
  python3 angel_w2_orderpath_probe.py            # full run incl. trigger test
  python3 angel_w2_orderpath_probe.py --no-trigger-test
  Options: --offset 1000  --strike N  --max-cost 400  --trigger-wait 90

SAVE THE FULL TERMINAL OUTPUT — the raw JSON shapes captured here are
what the W3 executor wiring is specified against.
"""

import argparse
import datetime as dt
import getpass
import json
import sys
import time

try:
    import requests
    import pyotp
except ImportError:
    sys.exit("Missing deps. Run:  pip3 install requests pyotp")

BASE = "https://apiconnect.angelone.in"
EP_LOGIN       = BASE + "/rest/auth/angelbroking/user/v1/loginByPassword"
EP_LTP         = BASE + "/rest/secure/angelbroking/order/v1/getLtpData"
EP_PLACE       = BASE + "/rest/secure/angelbroking/order/v1/placeOrder"
EP_CANCEL      = BASE + "/rest/secure/angelbroking/order/v1/cancelOrder"
EP_ORDERBOOK   = BASE + "/rest/secure/angelbroking/order/v1/getOrderBook"
EP_TRADEBOOK   = BASE + "/rest/secure/angelbroking/order/v1/getTradeBook"
EP_POSITION    = BASE + "/rest/secure/angelbroking/order/v1/getPosition"
EP_RMS         = BASE + "/rest/secure/angelbroking/user/v1/getRMS"
EP_ORDER_DET   = BASE + "/rest/secure/angelbroking/order/v1/details/"  # +uniqueorderid
EP_GTT_CREATE  = BASE + "/rest/secure/angelbroking/gtt/v1/createRule"
EP_GTT_CANCEL  = BASE + "/rest/secure/angelbroking/gtt/v1/cancelRule"
EP_GTT_DETAILS = BASE + "/rest/secure/angelbroking/gtt/v1/ruleDetails"
SCRIP_MASTER   = ("https://margincalculator.angelbroking.com"
                  "/OpenAPI_File/files/OpenAPIScripMaster.json")

NIFTY_TOKEN = "26000"
TICK = 0.05
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Cleanup ledger for the finally block
STATE = {"jwt": None, "api_key": None, "public_ip": "127.0.0.1",
         "sym": None, "token": None, "lot": 0,
         "holding_qty": 0, "gtt_ids": [], "open_order": None}


def tick_round(px: float) -> float:
    return max(TICK, round(round(px / TICK) * TICK, 2))


def detect_public_ip() -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            ip = requests.get(url, timeout=10).text.strip()
            if ip and len(ip) <= 45:
                return ip
        except Exception:
            continue
    return "127.0.0.1"


def hdrs(with_auth: bool = True) -> dict:
    h = {
        "Content-Type": "application/json", "Accept": "application/json",
        "X-UserType": "USER", "X-SourceID": "WEB",
        "X-ClientLocalIP": STATE["public_ip"],
        "X-ClientPublicIP": STATE["public_ip"],
        "X-MACAddress": "aa:bb:cc:dd:ee:ff",
        "X-PrivateKey": STATE["api_key"],
    }
    if with_auth and STATE["jwt"]:
        h["Authorization"] = "Bearer " + STATE["jwt"]
    return h


def _log_body(label: str, body: dict, redact_login: bool = False):
    safe = body
    if redact_login and isinstance(body.get("data"), dict):
        safe = dict(body)
        safe["data"] = {k: ("***" if "oken" in k else v)
                        for k, v in body["data"].items()}
    print(f"<< [{label}]", json.dumps(safe, indent=2)[:5000])


def post(url: str, payload: dict, label: str, redact_req: bool = False,
         redact_resp: bool = False) -> dict:
    print(f"\n--- {label} ---")
    if not redact_req:
        print(">>", json.dumps(payload, indent=2))
    r = requests.post(url, headers=hdrs(with_auth=(url != EP_LOGIN)),
                      data=json.dumps(payload), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:2000]}
    print("<< HTTP", r.status_code)
    _log_body(label, body, redact_resp)
    return body


def get(url: str, label: str) -> dict:
    print(f"\n--- {label} (GET) ---")
    r = requests.get(url, headers=hdrs(), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:2000]}
    print("<< HTTP", r.status_code)
    _log_body(label, body)
    return body


def ok(body: dict) -> bool:
    return bool(body) and body.get("status") is True


def fatal_ip_check(body: dict, op: str):
    if str(body.get("errorCode") or body.get("errorcode")) == "AG7002":
        sys.exit(f"\nFATAL [{op}]: AG7002 — this machine's IP "
                 f"({STATE['public_ip']}) is not registered on the app.")


# ----------------------------------------------------------------------
# Instrument
# ----------------------------------------------------------------------

def pick_instrument(offset: int, strike_override):
    print("\nDownloading scrip master...")
    rows = requests.get(SCRIP_MASTER, timeout=120).json()
    print(f"{len(rows)} instruments")

    if strike_override:
        strike = strike_override
    else:
        b = post(EP_LTP, {"exchange": "NSE", "tradingsymbol": "Nifty 50",
                          "symboltoken": NIFTY_TOKEN}, "NIFTY SPOT")
        if not ok(b):
            fatal_ip_check(b, "SPOT")
            sys.exit("Spot LTP failed.")
        spot = float(b["data"]["ltp"])
        strike = int(round((spot + offset) / 50.0) * 50)
        print(f"spot={spot} -> strike {strike}")

    today = dt.datetime.now(tz=IST).date()
    cands = []
    for row in rows:
        if (row.get("exch_seg") == "NFO"
                and row.get("instrumenttype") == "OPTIDX"
                and row.get("name") == "NIFTY"
                and str(row.get("symbol", "")).endswith("CE")):
            try:
                k = int(float(row.get("strike", "0")) / 100)
                exp = dt.datetime.strptime(row.get("expiry", ""),
                                           "%d%b%Y").date()
            except Exception:
                continue
            if k == strike and exp >= today:
                cands.append((exp, row))
    if not cands:
        sys.exit(f"No NIFTY {strike} CE found; try --strike.")
    cands.sort(key=lambda t: t[0])
    exp, row = cands[0]
    lot = int(float(row.get("lotsize", 65)))
    print(f"Instrument: {row['symbol']} token={row['token']} "
          f"expiry={exp} lot={lot}")
    return row["symbol"], str(row["token"]), lot


def option_ltp() -> float:
    b = post(EP_LTP, {"exchange": "NFO", "tradingsymbol": STATE["sym"],
                      "symboltoken": STATE["token"]}, "OPTION LTP")
    if not ok(b):
        fatal_ip_check(b, "LTP")
        raise RuntimeError("option LTP failed")
    return float(b["data"]["ltp"])


# ----------------------------------------------------------------------
# Orders
# ----------------------------------------------------------------------

def place_order(txn: str, limit_px: float, label: str) -> dict:
    body = post(EP_PLACE, {
        "variety": "NORMAL", "tradingsymbol": STATE["sym"],
        "symboltoken": STATE["token"], "transactiontype": txn,
        "exchange": "NFO", "ordertype": "LIMIT",
        "producttype": "CARRYFORWARD", "duration": "DAY",
        "price": limit_px, "squareoff": "0", "stoploss": "0",
        "quantity": str(STATE["lot"]),
    }, label)
    fatal_ip_check(body, label)
    if not ok(body):
        raise RuntimeError(f"{label} rejected: {body.get('message')}")
    data = body.get("data") or {}
    return {"orderid": str(data.get("orderid")),
            "uniqueorderid": data.get("uniqueorderid")}


def find_order_row(orderid: str):
    b = get(EP_ORDERBOOK, f"ORDER BOOK (find {orderid})")
    for row in (b.get("data") or []) if ok(b) else []:
        if str(row.get("orderid")) == orderid:
            return row
    return None


def poll_fill(order: dict, timeout_s: int = 45):
    """Returns (status, avgprice, filledqty, raw_row)."""
    if order.get("uniqueorderid"):
        get(EP_ORDER_DET + str(order["uniqueorderid"]),
            "INDIVIDUAL ORDER STATUS")  # shape capture; book poll decides
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        row = find_order_row(order["orderid"])
        if row:
            st = str(row.get("status", "")).lower()
            if st in ("complete", "rejected", "cancelled"):
                return (st, float(row.get("averageprice") or 0),
                        int(row.get("filledshares")
                            or row.get("filledquantity") or 0), row)
        time.sleep(2)
    row = find_order_row(order["orderid"])
    return ("timeout", 0.0, 0, row)


def cancel_order(orderid: str):
    post(EP_CANCEL, {"variety": "NORMAL", "orderid": orderid},
         f"CANCEL ORDER {orderid}")


def net_qty_for_symbol() -> int:
    b = get(EP_POSITION, "POSITIONS")
    if not ok(b):
        return -999999  # unknown
    for row in (b.get("data") or []):
        if str(row.get("tradingsymbol")) == STATE["sym"]:
            for key in ("netqty", "netquantity", "net_quantity"):
                if key in row:
                    try:
                        return int(float(row[key]))
                    except Exception:
                        pass
    return 0


def flatten(reason: str) -> bool:
    """SELL out whatever we hold. True when confirmed flat."""
    qty = STATE["holding_qty"]
    if qty <= 0:
        return True
    print(f"\n### FLATTEN ({reason}) — selling {qty} {STATE['sym']}")
    try:
        ltp = option_ltp()
    except Exception:
        ltp = 0.0
    px = tick_round(ltp * 0.95) if ltp else TICK  # marketable limit
    try:
        o = place_order("SELL", px, "EXIT SELL")
        st, avg, fq, _ = poll_fill(o, timeout_s=60)
        print(f"exit status={st} avg={avg} filled={fq}")
        if st == "timeout":
            cancel_order(o["orderid"])
            # one retry deeper through the book
            o = place_order("SELL", tick_round(max(TICK, px * 0.8)),
                            "EXIT SELL RETRY")
            st, avg, fq, _ = poll_fill(o, timeout_s=60)
            print(f"retry status={st} avg={avg} filled={fq}")
    except Exception as e:
        print(f"exit order error: {e}")
    nq = net_qty_for_symbol()
    if nq == 0:
        STATE["holding_qty"] = 0
        print("FLAT CONFIRMED via positions.")
        return True
    print(f"!! NOT CONFIRMED FLAT (netqty={nq})")
    return False


def cancel_gtt_verified(rule_id: str) -> bool:
    det = post(EP_GTT_DETAILS, {"id": rule_id}, f"GTT DETAILS {rule_id}")
    tok = (det.get("data") or {}).get("symboltoken", STATE["token"])
    post(EP_GTT_CANCEL, {"id": rule_id, "symboltoken": tok,
                         "exchange": "NFO"}, f"GTT CANCEL {rule_id}")
    for _ in range(4):
        det = post(EP_GTT_DETAILS, {"id": rule_id},
                   f"GTT VERIFY {rule_id}")
        if "CANCEL" in str((det.get("data") or {}).get("status", "")).upper():
            return True
        time.sleep(0.6)
    return False


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, default=1000)
    ap.add_argument("--strike", type=int, default=None)
    ap.add_argument("--max-cost", type=float, default=400.0,
                    help="max premium outlay in Rs. (default 400)")
    ap.add_argument("--trigger-wait", type=int, default=90,
                    help="seconds to wait for GTT trigger-fire exit")
    ap.add_argument("--no-trigger-test", action="store_true",
                    help="skip T9 trigger-fire; exit with a direct SELL")
    ap.add_argument("--force", action="store_true",
                    help="override the market-hours window check")
    args = ap.parse_args()

    now = dt.datetime.now(tz=IST)
    in_window = (now.weekday() < 5
                 and dt.time(9, 20) <= now.time() <= dt.time(15, 20))
    if not in_window and not args.force:
        sys.exit("Refusing: outside 09:20-15:20 IST market window "
                 "(orders need live market). Use --force only if you "
                 "know today is a trading day.")

    STATE["public_ip"] = detect_public_ip()
    print(f"Detected public IP: {STATE['public_ip']} "
          f"(must match the SmartAPI app registration)")

    STATE["api_key"] = input("SmartAPI API key: ").strip()
    client = input("Angel client code: ").strip()
    pin = getpass.getpass("PIN: ").strip()
    totp_secret = getpass.getpass("TOTP secret (base32): ").strip()

    body = post(EP_LOGIN, {"clientcode": client, "password": pin,
                           "totp": pyotp.TOTP(totp_secret).now(),
                           "state": "w2probe"},
                "LOGIN", redact_req=True, redact_resp=True)
    if not ok(body):
        sys.exit(f"LOGIN FAILED: {body.get('message')}")
    STATE["jwt"] = body["data"]["jwtToken"]
    print("Login OK.")

    STATE["sym"], STATE["token"], STATE["lot"] = pick_instrument(
        args.offset, args.strike)

    ltp = option_ltp()
    outlay = ltp * STATE["lot"]
    if ltp < 0.30:
        sys.exit(f"LTP {ltp} too low for clean fills — rerun with a "
                 f"smaller --offset (e.g. 700).")
    if outlay > args.max_cost:
        sys.exit(f"Premium outlay Rs.{outlay:.0f} exceeds --max-cost "
                 f"{args.max_cost:.0f}. Increase --offset or --max-cost.")

    print("\n" + "=" * 70)
    print("W2 ORDER-PATH PROBE — REAL MONEY (small)")
    print(f"  BUY 1 lot ({STATE['lot']}) {STATE['sym']} @ ~{ltp}")
    print(f"  Premium outlay ~Rs.{outlay:.0f} (recovered on exit; net "
          f"cost = spread + charges)")
    print(f"  Exit: {'GTT trigger-fire (fallback direct SELL)' if not args.no_trigger_test else 'direct SELL'}")
    print("=" * 70)
    if input("Type ARM LIVE to proceed: ").strip() != "ARM LIVE":
        sys.exit("Aborted. Nothing was placed.")

    results = {}
    try:
        # T1 funds baseline
        b = get(EP_RMS, "T1 getRMS (before)")
        results["T1_getRMS"] = "PASS" if ok(b) else "FAIL"

        # T2 positions baseline
        b = get(EP_POSITION, "T2 getPosition (before)")
        results["T2_getPosition"] = "PASS" if ok(b) else "FAIL"

        # T3 entry
        entry_px = tick_round(ltp * 1.05)
        order = place_order("BUY", entry_px, "T3 placeOrder BUY")
        STATE["open_order"] = order
        results["T3_placeOrder"] = "PASS"

        # T4 fill polling
        st, avg, fq, row = poll_fill(order)
        print(f"\nT4 result: status={st} avg={avg} filled={fq}")
        if st == "timeout":
            cancel_order(order["orderid"])
            results["T4_fill_poll"] = "FAIL (no fill in 45s — limit missed; nothing held)"
            print("Entry never filled; aborting remaining tests cleanly.")
            return
        if st != "complete":
            results["T4_fill_poll"] = f"FAIL (status={st})"
            print("Entry not complete; aborting remaining tests.")
            return
        STATE["open_order"] = None
        STATE["holding_qty"] = fq or STATE["lot"]
        results["T4_fill_poll"] = f"PASS (avg={avg})"

        # T5 positions with a real row — capture field names verbatim
        b = get(EP_POSITION, "T5 getPosition (holding)")
        held = ok(b) and any(str(r.get("tradingsymbol")) == STATE["sym"]
                             for r in (b.get("data") or []))
        results["T5_position_row"] = "PASS" if held else "FAIL (row missing)"

        # T6 GTT OCO on the REAL position (unreachable triggers)
        far_up, far_dn = tick_round(ltp * 4), tick_round(ltp * 0.25)
        gb = post(EP_GTT_CREATE, {
            "tradingsymbol": STATE["sym"], "symboltoken": STATE["token"],
            "exchange": "NFO", "producttype": "CARRYFORWARD",
            "transactiontype": "SELL", "qty": STATE["holding_qty"],
            "disclosedqty": 0, "timeperiod": 1,
            "triggerprice": far_up, "price": tick_round(far_up * 0.997),
            "gttType": "OCO",
            "stoplosstriggerprice": far_dn,
            "stoplossprice": tick_round(far_dn * 0.995),
        }, "T6 GTT OCO (real position)")
        if ok(gb):
            rid = str(gb["data"]["id"])
            STATE["gtt_ids"].append(rid)
            results["T6_gtt_on_position"] = "PASS"
            # T7 cancel-verified
            if cancel_gtt_verified(rid):
                STATE["gtt_ids"].remove(rid)
                results["T7_gtt_cancel_verified"] = "PASS"
            else:
                results["T7_gtt_cancel_verified"] = f"FAIL (rule {rid} ARMED?)"
        else:
            results["T6_gtt_on_position"] = f"FAIL ({gb.get('message')})"
            results["T7_gtt_cancel_verified"] = "SKIP"

        # T8 trade book
        b = get(EP_TRADEBOOK, "T8 getTradeBook")
        results["T8_tradebook"] = "PASS" if ok(b) else "FAIL"

        # T9 exit
        if not args.no_trigger_test:
            cur = option_ltp()
            trig = tick_round(cur * 1.01)   # a whisker above LTP
            sell_limit = tick_round(cur * 0.95)  # marketable when fired
            tb = post(EP_GTT_CREATE, {
                "tradingsymbol": STATE["sym"], "symboltoken": STATE["token"],
                "exchange": "NFO", "producttype": "CARRYFORWARD",
                "transactiontype": "SELL", "qty": STATE["holding_qty"],
                "disclosedqty": 0, "timeperiod": 1,
                "triggerprice": trig, "price": sell_limit,
            }, "T9 GTT trigger-fire exit (GENERIC SELL)")
            fired = False
            if ok(tb):
                rid = str(tb["data"]["id"])
                STATE["gtt_ids"].append(rid)
                print(f"Waiting up to {args.trigger_wait}s for LTP to "
                      f"cross {trig} and fire rule {rid}...")
                t0 = time.time()
                while time.time() - t0 < args.trigger_wait:
                    det = post(EP_GTT_DETAILS, {"id": rid},
                               f"T9 rule status")
                    stt = str((det.get("data") or {})
                              .get("status", "")).upper()
                    if stt not in ("NEW", "ACTIVE"):
                        print(f"Rule left pending state: {stt}")
                        fired = True
                        break
                    if net_qty_for_symbol() == 0:
                        fired = True
                        break
                    time.sleep(6)
                if fired:
                    time.sleep(3)
                    get(EP_ORDERBOOK, "T9 ORDER BOOK after trigger "
                                      "(capture fired-order shape)")
                    if net_qty_for_symbol() == 0:
                        STATE["holding_qty"] = 0
                        STATE["gtt_ids"].remove(rid)
                        results["T9_trigger_fire_exit"] = "PASS"
                    else:
                        results["T9_trigger_fire_exit"] = \
                            "PARTIAL (fired but not flat — flatten follows)"
                else:
                    print("Trigger did not fire in time; cancelling and "
                          "falling back to direct SELL.")
                    if cancel_gtt_verified(rid):
                        STATE["gtt_ids"].remove(rid)
                    results["T9_trigger_fire_exit"] = \
                        "INCONCLUSIVE (no cross in window; direct exit used)"
            else:
                results["T9_trigger_fire_exit"] = \
                    f"FAIL create ({tb.get('message')})"
        # direct exit if still holding
        if STATE["holding_qty"] > 0:
            okf = flatten("planned exit")
            results["T9_direct_exit"] = "PASS" if okf else "FAIL"

        # T10 funds after
        b = get(EP_RMS, "T10 getRMS (after)")
        results["T10_getRMS_after"] = "PASS" if ok(b) else "FAIL"

    finally:
        # ---------------- SAFETY NET ----------------
        leftover_msgs = []
        for rid in list(STATE["gtt_ids"]):
            try:
                if cancel_gtt_verified(rid):
                    STATE["gtt_ids"].remove(rid)
            except Exception:
                pass
        if STATE["open_order"]:
            try:
                cancel_order(STATE["open_order"]["orderid"])
            except Exception:
                leftover_msgs.append(
                    f"open order {STATE['open_order']} may be pending")
        if STATE["holding_qty"] > 0:
            if not flatten("SAFETY NET"):
                leftover_msgs.append(
                    f"POSITION {STATE['sym']} x{STATE['holding_qty']} "
                    f"still open — SQUARE OFF MANUALLY in Angel app NOW")
        if STATE["gtt_ids"]:
            leftover_msgs.append(
                f"GTT rules still armed: {STATE['gtt_ids']} — delete in "
                f"Angel app (Orders -> GTT)")

        print("\n" + "=" * 70)
        print("W2 PROBE SUMMARY")
        print("=" * 70)
        for k, v in results.items():
            print(f"{k:26s} {v}")
        if leftover_msgs:
            print("\n" + "!" * 70)
            for m in leftover_msgs:
                print("!!", m)
            print("!" * 70)
        else:
            print("\nAccount clean: flat, no armed rules, no open orders.")
        print("\nSend the FULL terminal output back for the W3 wiring spec.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted — running safety net...")
        try:
            for rid in list(STATE["gtt_ids"]):
                cancel_gtt_verified(rid)
            if STATE["holding_qty"] > 0:
                flatten("interrupt")
        finally:
            if STATE["holding_qty"] > 0 or STATE["gtt_ids"]:
                print(f"!! MANUAL CLEANUP NEEDED: pos={STATE['holding_qty']} "
                      f"{STATE['sym']} gtts={STATE['gtt_ids']}")