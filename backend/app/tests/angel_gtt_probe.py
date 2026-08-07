#!/usr/bin/env python3
"""
angel_gtt_probe.py
==================
LIVE PROBE: Does Angel One SmartAPI support server-side GTT OCO on NFO
index options (CARRYFORWARD product), in BOTH shapes we need?

  TEST A  SELL-side OCO  (protects a hypothetical LONG  — BB/HA/SCALP shape)
  TEST B  BUY-side  OCO  (protects a hypothetical SHORT — PST/IC shape)
  TEST C  Plain single-trigger GTT (control: distinguishes "OCO unsupported"
          from "GTT broken in general")

WHAT THIS SCRIPT DOES / DOES NOT DO
  - Places ONLY GTT rules (no regular orders, no positions, no margin block).
  - Trigger prices are set deliberately unreachable relative to LTP.
  - Every rule it creates is CANCELLED and the cancel is VERIFIED via
    ruleDetails before the script exits. Any rule it cannot cancel is
    listed in a loud warning at the end — delete those manually in the
    Angel app under Orders -> GTT.
  - STRONGLY RECOMMENDED: run OUTSIDE market hours (after 15:40 IST or on
    a weekend). GTT rules only evaluate on live ticks, so off-market there
    is zero possibility of a trigger firing before the cancel lands.

PREREQS (one-time, on the Angel account being probed)
  1. F&O (NFO) segment must be active on the account.
  2. Create a SmartAPI app at https://smartapi.angelone.in  -> get API KEY.
  3. Enable TOTP for SmartAPI at https://smartapi.angelone.in/enable-totp
     -> save the TOTP SECRET (the base32 string shown during enrolment).

USAGE
  pip3 install requests pyotp
  python3 angel_gtt_probe.py

  The script prompts for: API key, client code, PIN, TOTP secret.
  Nothing is written to disk; credentials live only in process memory.

  Optional flags:
    --offset N      strike distance above spot for the probe CE (default 1000)
    --strike N      force an exact strike (skips spot lookup)
    --skip-buy      skip TEST B
    --skip-control  skip TEST C
"""

import argparse
import datetime as dt
import json
import sys
import time
import uuid

try:
    import requests
    import pyotp
except ImportError:
    sys.exit("Missing deps. Run:  pip3 install requests pyotp")

# ----------------------------------------------------------------------
# Endpoints & constants
# ----------------------------------------------------------------------
BASE = "https://apiconnect.angelone.in"
EP_LOGIN        = BASE + "/rest/auth/angelbroking/user/v1/loginByPassword"
EP_LTP          = BASE + "/rest/secure/angelbroking/order/v1/getLtpData"
EP_GTT_CREATE   = BASE + "/rest/secure/angelbroking/gtt/v1/createRule"
EP_GTT_CANCEL   = BASE + "/rest/secure/angelbroking/gtt/v1/cancelRule"
EP_GTT_DETAILS  = BASE + "/rest/secure/angelbroking/gtt/v1/ruleDetails"
EP_GTT_LIST     = BASE + "/rest/secure/angelbroking/gtt/v1/ruleList"
SCRIP_MASTER    = ("https://margincalculator.angelbroking.com"
                   "/OpenAPI_File/files/OpenAPIScripMaster.json")

NIFTY_INDEX_TOKEN = "26000"       # NSE "Nifty 50" index token in SmartAPI
NIFTY_INDEX_SYM   = "Nifty 50"
TICK              = 0.05

created_rule_ids = []             # (rule_id, symboltoken) for cleanup


def detect_public_ip() -> str:
    """Best-effort public IP detection; must match the Primary Static IP
    registered on the SmartAPI app, so we send the real one in headers."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            ip = requests.get(url, timeout=10).text.strip()
            if ip and len(ip) <= 45:
                return ip
        except Exception:
            continue
    return "127.0.0.1"


PUBLIC_IP = detect_public_ip()
print(f"Detected public IP: {PUBLIC_IP}  "
      f"(must match the Primary Static IP registered on the SmartAPI app)")


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def tick_round(px: float) -> float:
    return max(TICK, round(round(px / TICK) * TICK, 2))


def hdrs(api_key: str, jwt: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": PUBLIC_IP,
        "X-ClientPublicIP": PUBLIC_IP,
        "X-MACAddress": "aa:bb:cc:dd:ee:ff",
        "X-PrivateKey": api_key,
    }
    if jwt:
        h["Authorization"] = "Bearer " + jwt
    return h


def post(url: str, api_key: str, jwt: str | None, payload: dict,
         label: str) -> dict:
    """POST with full request/response logging. Returns parsed JSON."""
    print(f"\n--- {label} ---")
    print(">>", json.dumps(payload, indent=2))
    r = requests.post(url, headers=hdrs(api_key, jwt),
                      data=json.dumps(payload), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:2000]}
    print("<< HTTP", r.status_code)
    print("<<", json.dumps(body, indent=2)[:4000])
    return body


def ok(body: dict) -> bool:
    return bool(body) and (body.get("status") is True
                           or str(body.get("status")).lower() == "true")


# ----------------------------------------------------------------------
# Instrument resolution
# ----------------------------------------------------------------------
def load_scrip_master() -> list:
    print("\nDownloading Angel scrip master (few MB, one-time)...")
    r = requests.get(SCRIP_MASTER, timeout=120)
    r.raise_for_status()
    data = r.json()
    print(f"Scrip master loaded: {len(data)} instruments")
    return data


def parse_expiry(s: str) -> dt.date | None:
    # Angel format like "28AUG2025"
    try:
        return dt.datetime.strptime(s, "%d%b%Y").date()
    except Exception:
        return None


def pick_probe_option(master: list, strike: int) -> dict:
    """Nearest-expiry NIFTY CE at the given strike."""
    today = dt.date.today()
    cands = []
    for row in master:
        if (row.get("exch_seg") == "NFO"
                and row.get("instrumenttype") == "OPTIDX"
                and row.get("name") == "NIFTY"
                and str(row.get("symbol", "")).endswith("CE")):
            try:
                k = int(float(row.get("strike", "0")) / 100)  # strike in paise
            except Exception:
                continue
            if k != strike:
                continue
            exp = parse_expiry(row.get("expiry", ""))
            if exp and exp >= today:
                cands.append((exp, row))
    if not cands:
        raise RuntimeError(
            f"No NIFTY {strike} CE found in scrip master. "
            f"Re-run with --strike set to a strike that exists (multiple of 50)."
        )
    cands.sort(key=lambda t: t[0])
    exp, row = cands[0]
    print(f"\nProbe instrument: {row['symbol']}  token={row['token']}  "
          f"expiry={exp}  lot={row.get('lotsize')}")
    return row


def get_ltp(api_key: str, jwt: str, exchange: str, tradingsymbol: str,
            token: str) -> float:
    body = post(EP_LTP, api_key, jwt,
                {"exchange": exchange, "tradingsymbol": tradingsymbol,
                 "symboltoken": token},
                f"LTP {tradingsymbol}")
    if not ok(body):
        raise RuntimeError(f"LTP fetch failed for {tradingsymbol}: {body}")
    return float(body["data"]["ltp"])


# ----------------------------------------------------------------------
# GTT operations
# ----------------------------------------------------------------------
def gtt_create(api_key: str, jwt: str, payload: dict, label: str):
    """
    Try createRule. Field casing for the OCO extension is not consistent in
    Angel's docs vs forum ("gttType" vs "gtttype"), so on a create failure
    with the first casing we retry once with the alternate.
    Returns (rule_id | None, body_of_successful_call | last_body).
    """
    body = post(EP_GTT_CREATE, api_key, jwt, payload, label)
    if not ok(body) and "gttType" in payload:
        alt = dict(payload)
        alt["gtttype"] = alt.pop("gttType")
        body = post(EP_GTT_CREATE, api_key, jwt, alt,
                    label + " (retry: lowercase gtttype)")
    if ok(body):
        data = body.get("data") or {}
        rid = str(data.get("id") or data.get("rule_id") or data)
        return rid, body
    return None, body


def gtt_details(api_key: str, jwt: str, rule_id: str, label: str) -> dict:
    return post(EP_GTT_DETAILS, api_key, jwt, {"id": rule_id}, label)


def gtt_cancel_verified(api_key: str, jwt: str, rule_id: str,
                        symboltoken: str) -> bool:
    body = post(EP_GTT_CANCEL, api_key, jwt,
                {"id": rule_id, "symboltoken": symboltoken,
                 "exchange": "NFO"},
                f"CANCEL rule {rule_id}")
    if not ok(body):
        return False
    time.sleep(1.0)
    det = gtt_details(api_key, jwt, rule_id, f"VERIFY CANCEL {rule_id}")
    status = str((det.get("data") or {}).get("status", "")).upper()
    print(f"Post-cancel status for rule {rule_id}: {status!r}")
    return "CANCEL" in status


def looks_like_oco(details_body: dict) -> tuple[bool, str]:
    """
    Heuristic: rule details must echo BOTH legs. We accept if any stoploss
    field is present and non-null, or gttType reads OCO.
    """
    data = details_body.get("data") or {}
    blob = json.dumps(data).lower()
    if '"oco"' in blob:
        return True, "gttType=OCO echoed in rule details"
    for key in ("stoplossprice", "stoplosstriggerprice"):
        v = data.get(key)
        if v not in (None, "", 0, "0", "0.0"):
            return True, f"{key}={v} echoed in rule details"
    return False, ("no OCO/stoploss fields in rule details — rule was likely "
                   "accepted as a SINGLE-leg GTT (OCO silently ignored)")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, default=1000,
                    help="strike distance above NIFTY spot (default 1000)")
    ap.add_argument("--strike", type=int, default=None,
                    help="force exact strike, skip spot lookup")
    ap.add_argument("--skip-buy", action="store_true")
    ap.add_argument("--skip-control", action="store_true")
    args = ap.parse_args()

    print("=" * 70)
    print("ANGEL ONE GTT OCO PROBE — NFO OPTIONS, CARRYFORWARD")
    print("=" * 70)
    print("Run this OUTSIDE market hours (after 15:40 IST / weekend).")
    now = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    in_market = (now.weekday() < 5
                 and dt.time(9, 15) <= now.time() <= dt.time(15, 40))
    if in_market:
        print("\n*** WARNING: it appears to be MARKET HOURS in IST right now.")
        print("*** Triggers are unreachable by design, but off-market is safer.")

    import getpass
    api_key     = input("SmartAPI API key: ").strip()
    client_code = input("Angel client code: ").strip()
    pin         = getpass.getpass("PIN: ").strip()
    totp_secret = getpass.getpass("TOTP secret (base32): ").strip()

    # ---- LOGIN ----
    totp = pyotp.TOTP(totp_secret).now()
    body = post(EP_LOGIN, api_key, None,
                {"clientcode": client_code, "password": pin, "totp": totp,
                 "state": "probe"},
                "LOGIN")
    if not ok(body):
        sys.exit("\nLOGIN FAILED — check API key / client code / PIN / TOTP "
                 "secret, and that TOTP is enrolled for SmartAPI.")
    jwt = body["data"]["jwtToken"]
    print("\nLogin OK.")

    # ---- INSTRUMENT ----
    master = load_scrip_master()
    if args.strike:
        strike = args.strike
    else:
        spot = get_ltp(api_key, jwt, "NSE", NIFTY_INDEX_SYM, NIFTY_INDEX_TOKEN)
        strike = int(round((spot + args.offset) / 50.0) * 50)
        print(f"NIFTY spot={spot}  -> probe strike {strike} "
              f"(~{args.offset} OTM)")
    inst = pick_probe_option(master, strike)
    sym, token = inst["symbol"], str(inst["token"])
    lot = int(float(inst.get("lotsize", 65)))

    ltp = get_ltp(api_key, jwt, "NFO", sym, token)
    if ltp < 0.10:
        sys.exit(f"\nLTP of {sym} is {ltp} — too deep OTM to build sane "
                 f"trigger prices. Re-run with a smaller --offset "
                 f"(e.g. --offset 500).")

    # Unreachable trigger design (relative to LTP):
    far_up   = tick_round(ltp * 4.0)
    far_dn   = tick_round(ltp * 0.25)
    print(f"\n{sym}: LTP={ltp}  far_up={far_up}  far_dn={far_dn}  lot={lot}")

    print("\nThe probe will create (and immediately cancel) GTT rules on the")
    print(f"instrument above, qty {lot}, product CARRYFORWARD. No regular")
    print("orders are placed and no positions are opened.")
    if input("Type ARM to proceed: ").strip() != "ARM":
        sys.exit("Aborted by user. Nothing was created.")

    results = {}

    def run_test(name: str, payload: dict, expect_oco: bool):
        rid, cbody = gtt_create(api_key, jwt, payload, f"{name} CREATE")
        if not rid:
            results[name] = ("FAIL",
                             f"createRule rejected: "
                             f"{cbody.get('message')} / "
                             f"{cbody.get('errorcode')}")
            return
        created_rule_ids.append((rid, token))
        det = gtt_details(api_key, jwt, rid, f"{name} DETAILS")
        verdict, note = ("PASS", "rule created")
        if expect_oco:
            is_oco, note = looks_like_oco(det)
            verdict = "PASS" if is_oco else "FAIL"
        cancelled = gtt_cancel_verified(api_key, jwt, rid, token)
        if cancelled:
            created_rule_ids.remove((rid, token))
            note += "; cancel verified"
        else:
            note += f"; !! CANCEL NOT VERIFIED — rule id {rid} may be ARMED"
            if verdict == "PASS":
                verdict = "PASS*"
        results[name] = (verdict, note)

    tag = uuid.uuid4().hex[:6]

    # ---- TEST A: SELL-side OCO (long protection) ----
    run_test(
        "A_SELL_OCO",
        {
            "tradingsymbol": sym, "symboltoken": token, "exchange": "NFO",
            "producttype": "CARRYFORWARD", "transactiontype": "SELL",
            "qty": lot, "disclosedqty": 0, "timeperiod": 1,
            "triggerprice": far_up,                    # target leg
            "price": tick_round(far_up * 0.997),
            "gttType": "OCO",
            "stoplosstriggerprice": far_dn,            # SL leg
            "stoplossprice": tick_round(far_dn * 0.995),
        },
        expect_oco=True,
    )

    # ---- TEST B: BUY-side OCO (short protection) ----
    if not args.skip_buy:
        run_test(
            "B_BUY_OCO",
            {
                "tradingsymbol": sym, "symboltoken": token, "exchange": "NFO",
                "producttype": "CARRYFORWARD", "transactiontype": "BUY",
                "qty": lot, "disclosedqty": 0, "timeperiod": 1,
                "triggerprice": far_dn,                # buy-back target leg
                "price": tick_round(far_dn * 1.003),
                "gttType": "OCO",
                "stoplosstriggerprice": far_up,        # SL leg (price runs up)
                "stoplossprice": tick_round(far_up * 1.005),
            },
            expect_oco=True,
        )

    # ---- TEST C: plain single-leg GTT (control) ----
    if not args.skip_control:
        run_test(
            "C_SINGLE_CONTROL",
            {
                "tradingsymbol": sym, "symboltoken": token, "exchange": "NFO",
                "producttype": "CARRYFORWARD", "transactiontype": "SELL",
                "qty": lot, "disclosedqty": 0, "timeperiod": 1,
                "triggerprice": far_up,
                "price": tick_round(far_up * 0.997),
            },
            expect_oco=False,
        )

    # ---- SUMMARY ----
    print("\n" + "=" * 70)
    print("PROBE SUMMARY")
    print("=" * 70)
    for name, (verdict, note) in results.items():
        print(f"{name:20s} {verdict:6s} {note}")

    print("\nInterpretation:")
    print("  A PASS + B PASS  -> Angel fits our doctrine; proceed with "
          "AngelOneExecutor design.")
    print("  A PASS + B FAIL  -> only long-side protection; short strategies "
          "(PST/IC) cannot go to Angel.")
    print("  A FAIL + C PASS  -> GTT works but OCO unsupported on NFO; "
          "Angel would need client-side OCO emulation (weak fit).")
    print("  A FAIL + C FAIL  -> GTT broken on NFO for this account; check "
          "segment activation, or Angel is a no-go.")

    if created_rule_ids:
        print("\n" + "!" * 70)
        print("!! RULES THAT COULD NOT BE CANCEL-VERIFIED — DELETE MANUALLY")
        print("!! in the Angel app: Orders -> GTT")
        for rid, _tok in created_rule_ids:
            print(f"!!   rule id {rid}  ({sym})")
        print("!" * 70)
    else:
        print("\nAll created rules cancelled and verified. Account is clean.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        if created_rule_ids:
            print("!! Rules possibly left armed — delete manually in the "
                  "Angel app (Orders -> GTT):", created_rule_ids)