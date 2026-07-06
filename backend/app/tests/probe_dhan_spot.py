#!/usr/bin/env python3
# probe_dhan_spot.py — PROOF STEP for PST_V1 (pivot/SMA/SuperTrend strategy).
#
# Verifies that Dhan's /v2/charts/intraday endpoint can serve NIFTY 50 INDEX
# 1-minute candles across the eras the backtest corpus needs, BEFORE any
# backfill or strategy code is written. Standalone on purpose: stdlib only,
# no app imports, run it anywhere.
#
# Docs facts this probe validates empirically (DhanHQ v2, checked 2026-07-06):
#   * intraday minute data: last 5 YEARS max  → the 2021-02 window below is
#     EXPECTED TO FAIL; if it succeeds, even better (docs conservative)
#   * max 90 days per request (we use ~2-week windows)
#   * timestamps are EPOCH seconds → we print IST conversions to verify
#   * no rate limit on minute timeframe (polite 1s sleep anyway)
#
# Usage:
#   export DHAN_ACCESS_TOKEN='<your token from Dhan web>'
#   python3 probe_dhan_spot.py
# Optional overrides (if securityId 13 turns out wrong for your account):
#   python3 probe_dhan_spot.py --security-id 13 --segment IDX_I

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
URL = "https://api.dhan.co/v2/charts/intraday"


def _ssl_context() -> ssl.SSLContext:
    """macOS python.org/pyenv builds don't see the system keychain — use
    certifi's CA bundle when present (it is, in this repo's env: requests
    depends on it). NEVER disable verification: this request carries the
    broker access token."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

# Test windows: (label, from, to, expectation)
WINDOWS = [
    ("recent  2026", "2026-06-22 09:15:00", "2026-07-03 15:30:00", "PASS expected"),
    ("mid     2023", "2023-08-01 09:15:00", "2023-08-14 15:30:00", "PASS expected"),
    ("old-edge 2021H2", "2021-08-02 09:15:00", "2021-08-13 15:30:00", "PASS expected (just inside 5y)"),
    ("beyond  2021H1", "2021-02-01 09:15:00", "2021-02-12 15:30:00", "FAIL expected (beyond 5y) — corpus gap Jan-Jun 2021"),
]

# plausible NIFTY spot ranges per era — human sanity anchor, printed not asserted
ERA_HINT = {"2026": "~23,000-26,500", "2023": "~19,000-20,500", "2021": "~14,500-18,500"}


def fetch(token: str, sec_id: str, segment: str, dfrom: str, dto: str):
    payload = {
        "securityId": sec_id, "exchangeSegment": segment, "instrument": "INDEX",
        "interval": "1", "oi": False, "fromDate": dfrom, "toDate": dto,
    }
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "access-token": token})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()[:300]
        except Exception:
            body = ""
        return e.code, {"error": body or str(e.reason)}
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            return 0, {"error": "SSL certs unavailable even via certifi — run: "
                                "python3 -m pip install certifi  (or the macOS "
                                "'Install Certificates.command' for your Python)"}
        return 0, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def analyze(label: str, data: dict):
    ts = data.get("timestamp") or []
    o, h, l, c = (data.get(k) or [] for k in ("open", "high", "low", "close"))
    if not ts or not c:
        print(f"    x no candles in response (keys: {sorted(data.keys())})")
        return False
    n = len(ts)
    days = {}
    for t in ts:
        d = datetime.fromtimestamp(t, IST).date()
        days[d] = days.get(d, 0) + 1
    per_day = sorted(days.values())
    first = datetime.fromtimestamp(ts[0], IST)
    last = datetime.fromtimestamp(ts[-1], IST)
    era = str(first.year)
    print(f"    OK {n} candles | {len(days)} trading days | candles/day min {per_day[0]} max {per_day[-1]} (expect ~375)")
    print(f"       first: {first:%Y-%m-%d %H:%M IST} (raw epoch {ts[0]}) -- MUST read 09:15/09:16 IST, "
          f"else timestamp semantics differ")
    print(f"       last : {last:%Y-%m-%d %H:%M IST}")
    print(f"       first candle OHLC: {o[0]:.2f}/{h[0]:.2f}/{l[0]:.2f}/{c[0]:.2f}  "
          f"(era sanity: NIFTY {era} {ERA_HINT.get(era, '?')})")
    ok = True
    if per_day[0] < 360:
        print(f"       WARN thinnest day has {per_day[0]} candles -- check intraday gaps before trusting")
        ok = False
    hh = first.hour * 60 + first.minute
    if not (9 * 60 + 14 <= hh <= 9 * 60 + 17):
        print("       WARN first candle is not 09:15±1 IST -- timestamp semantics need investigation")
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--security-id", default="13", help="Dhan securityId for NIFTY 50 index (default 13)")
    ap.add_argument("--segment", default="IDX_I", help="exchange segment for indices (default IDX_I)")
    ap.add_argument("--token", default=os.environ.get("DHAN_ACCESS_TOKEN", ""))
    args = ap.parse_args()
    if not args.token:
        sys.exit("Set DHAN_ACCESS_TOKEN env var (or pass --token). Token from Dhan web -> DhanHQ APIs.")

    print(f"Probing {URL}  securityId={args.security_id}  segment={args.segment}\n")
    results = []
    for label, dfrom, dto, expect in WINDOWS:
        print(f"[{label}]  {dfrom[:10]} -> {dto[:10]}   ({expect})")
        status, data = fetch(args.token, args.security_id, args.segment, dfrom, dto)
        if status == 200 and not data.get("error"):
            ok = analyze(label, data)
            results.append((label, "OK" if ok else "OK-with-warnings"))
        else:
            print(f"    x HTTP {status}: {str(data.get('error'))[:200]}")
            if status == 401:
                print("      -> token invalid/expired: regenerate in Dhan web and retry")
            results.append((label, f"FAIL ({status})"))
        time.sleep(1.0)
        print()

    print("=" * 62)
    print("VERDICT")
    for label, res in results:
        print(f"  {label:18s} {res}")
    print("""
Interpretation:
  * recent+mid+old-edge OK, beyond FAIL  -> exactly as documented. GREEN
    LIGHT: build the index backfill; PST_V1 corpus starts 2021-07 (or we
    fill Jan-Jun 2021 from Kite historical later).
  * everything OK incl. 2021H1           -> even better; full-corpus spot.
  * empty candles with HTTP 200          -> securityId/segment mismatch:
    check the IDX row for NIFTY in Dhan's api-scrip-master.csv and pass
    --security-id accordingly.
  * timezone warning                     -> paste the raw epoch line back
    to the chat; the backfill must normalize before any pivot math.""")


if __name__ == "__main__":
    main()