#!/usr/bin/env python3
"""
probe_delta_corpus.py — Delta Exchange historical-data feasibility probe
========================================================================
Purpose: decide whether an IC-style BTC options backtest corpus is buildable
from Delta Exchange public candle APIs (India entity vs Global entity), and
how deep 1m retention goes for EXPIRED option contracts.

Read-only. No auth. No order endpoints touched. Safe to run any time.

Usage:
    python3 probe_delta_corpus.py

Output: per-era, per-base verdict table + raw evidence lines.
"""

import sys
import time
import json
import datetime as dt

try:
    import requests
except ImportError:
    print("ERROR: `pip3 install requests` first.")
    sys.exit(1)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
BASES = {
    "india":  "https://api.india.delta.exchange",
    "global": "https://api.delta.exchange",
}

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
EXPIRY_HM_IST = (17, 30)          # daily options expire 17:30 IST
CONTRACT_LIFE_H = 48              # D1/D2 chain => ~48h of life
PACE_S = 0.35                     # be polite; ~3 req/s
TIMEOUT = 15

# Eras to test for retention depth (months back from today)
RETENTION_ERAS = [
    ("recent",  0),    # taken from products API, not guessed
    ("6m_ago",  6),
    ("1y_ago", 12),
    ("2y_ago", 24),
    ("3y_ago", 36),
]

# Plausible BTC strike grids on Delta over the years
STRIKE_GRIDS = [200, 400, 500, 1000, 2000]

session = requests.Session()
session.headers.update({"User-Agent": "corpus-feasibility-probe/1.0"})


def get(base: str, path: str, params: dict | None = None):
    """GET with pacing; returns (json_or_None, note)."""
    url = base + path
    try:
        r = session.get(url, params=params or {}, timeout=TIMEOUT)
        time.sleep(PACE_S)
        # Surface rate-limit headers once if present
        rl = {k: v for k, v in r.headers.items() if "RATE" in k.upper()}
        note = f"HTTP {r.status_code}" + (f" rl={rl}" if rl else "")
        if r.status_code != 200:
            return None, note + f" body={r.text[:200]}"
        return r.json(), note
    except Exception as e:
        return None, f"EXC {e!r}"


# ----------------------------------------------------------------------
# Symbol / time helpers
# ----------------------------------------------------------------------
def parse_expiry_from_symbol(symbol: str) -> dt.datetime | None:
    """C-BTC-116000-050826 -> aware datetime 2026-08-05 17:30 IST."""
    try:
        ddmmyy = symbol.rsplit("-", 1)[1]
        d, m, y = int(ddmmyy[0:2]), int(ddmmyy[2:4]), 2000 + int(ddmmyy[4:6])
        return dt.datetime(y, m, d, *EXPIRY_HM_IST, tzinfo=IST)
    except Exception:
        return None


def life_window_epochs(expiry_ist: dt.datetime) -> tuple[int, int]:
    end = int(expiry_ist.timestamp())
    start = end - CONTRACT_LIFE_H * 3600
    return start, end


def make_symbol(side: str, strike: int, expiry_date: dt.date) -> str:
    return f"{side}-BTC-{strike}-{expiry_date.strftime('%d%m%y')}"


# ----------------------------------------------------------------------
# Candle fetch + summarize
# ----------------------------------------------------------------------
def candle_count(base: str, symbol: str, start: int, end: int,
                 resolution: str = "1m") -> tuple[int, str]:
    js, note = get(base, "/v2/history/candles", {
        "symbol": symbol, "resolution": resolution,
        "start": start, "end": end,
    })
    if js is None:
        return -1, note
    rows = js.get("result") or []
    if rows:
        ts = sorted(r["time"] for r in rows if "time" in r)
        span = ""
        if ts:
            f = dt.datetime.fromtimestamp(ts[0], UTC).strftime("%Y-%m-%d %H:%M")
            l = dt.datetime.fromtimestamp(ts[-1], UTC).strftime("%Y-%m-%d %H:%M")
            span = f" span={f}..{l}Z"
        return len(rows), note + span
    return 0, note


def probe_option_symbol(base: str, symbol: str) -> dict:
    """Probe LTP + MARK 1m candles over the contract's real life window."""
    exp = parse_expiry_from_symbol(symbol)
    if not exp:
        return {"symbol": symbol, "error": "unparseable expiry"}
    start, end = life_window_epochs(exp)
    ltp_n, ltp_note = candle_count(base, symbol, start, end)
    mark_n, mark_note = candle_count(base, "MARK:" + symbol, start, end)
    return {
        "symbol": symbol, "expiry_ist": exp.strftime("%Y-%m-%d %H:%M"),
        "ltp_1m": ltp_n, "ltp_note": ltp_note,
        "mark_1m": mark_n, "mark_note": mark_note,
    }


# ----------------------------------------------------------------------
# Step 1: recent expired BTC symbols straight from products API
# ----------------------------------------------------------------------
def recent_expired_btc_symbols(base: str, want: int = 3, max_pages: int = 8):
    syms, after = [], None
    for _ in range(max_pages):
        params = {"contract_types": "call_options,put_options",
                  "states": "expired", "page_size": 100}
        if after:
            params["after"] = after
        js, note = get(base, "/v2/products", params)
        if js is None:
            print(f"  [products] {base} -> {note}")
            break
        for p in js.get("result", []):
            s = p.get("symbol", "")
            if "-BTC-" in s:
                syms.append(s)
                if len(syms) >= want:
                    return syms
        after = (js.get("meta") or {}).get("after")
        if not after:
            break
    return syms


# ----------------------------------------------------------------------
# Step 2: historical spot via perp candles (also = perp retention probe)
# ----------------------------------------------------------------------
def spot_close_on(base: str, day: dt.date) -> float | None:
    start = int(dt.datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    end = start + 86400
    js, _ = get(base, "/v2/history/candles", {
        "symbol": "BTCUSD", "resolution": "1d", "start": start, "end": end})
    rows = (js or {}).get("result") or []
    if rows:
        try:
            return float(rows[0]["close"])
        except Exception:
            return None
    return None


def candidate_strikes(spot: float) -> list[int]:
    """Nearest-to-ATM candidates across plausible grids, deduped, ATM-first."""
    out = []
    for g in STRIKE_GRIDS:
        atm = int(round(spot / g) * g)
        for k in (atm, atm + g, atm - g):
            if k > 0 and k not in out:
                out.append(k)
    return out


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    today = dt.datetime.now(IST).date()
    verdict_rows = []

    print("=" * 72)
    print("DELTA EXCHANGE CORPUS FEASIBILITY PROBE")
    print(f"run at {dt.datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    print("=" * 72)

    for base_name, base in BASES.items():
        print(f"\n########## BASE: {base_name}  ({base}) ##########")

        # ---- perp 1m retention: binary-ish sample at each era ----
        print("\n[perp retention] BTCUSD 1m availability:")
        for label, months in RETENTION_ERAS[1:]:
            day = today - dt.timedelta(days=30 * months)
            start = int(dt.datetime(day.year, day.month, day.day,
                                    tzinfo=UTC).timestamp())
            n, note = candle_count(base, "BTCUSD", start, start + 3600)
            print(f"  {label:7s} {day}  1m_rows(1h window)={n}  {note}")

        # ---- era: recent (real symbols from products API) ----
        print("\n[recent expired options] from /v2/products states=expired:")
        recent_syms = recent_expired_btc_symbols(base)
        if not recent_syms:
            print("  no BTC symbols found in first pages (see notes above)")
        best_recent = 0
        for s in recent_syms:
            r = probe_option_symbol(base, s)
            print(f"  {r['symbol']:26s} exp={r.get('expiry_ist','?')} "
                  f"LTP={r.get('ltp_1m','?')} MARK={r.get('mark_1m','?')}")
            print(f"      ltp:  {r.get('ltp_note','')}")
            print(f"      mark: {r.get('mark_note','')}")
            best_recent = max(best_recent,
                              r.get("ltp_1m", 0), r.get("mark_1m", 0))
        verdict_rows.append((base_name, "recent", best_recent))

        # ---- eras: guessed symbols anchored on historical spot ----
        for label, months in RETENTION_ERAS[1:]:
            day = today - dt.timedelta(days=30 * months)
            print(f"\n[{label}] target expiry date {day}:")
            spot = spot_close_on(base, day)
            if spot is None:
                print("  no perp daily close -> cannot anchor strikes "
                      "(perp retention itself ends before this era?)")
                verdict_rows.append((base_name, label, -1))
                continue
            print(f"  spot close ~ {spot:,.0f}")
            best = 0
            hit_sym = None
            for strike in candidate_strikes(spot):
                sym = make_symbol("C", strike, day)
                r = probe_option_symbol(base, sym)
                n = max(r.get("ltp_1m", 0), r.get("mark_1m", 0))
                if n > 0:
                    best, hit_sym = n, sym
                    print(f"  HIT  {sym}: LTP={r['ltp_1m']} MARK={r['mark_1m']}")
                    break
            if not hit_sym:
                print(f"  no candle data on any of "
                      f"{len(candidate_strikes(spot))} candidate strikes "
                      f"(strike-miss OR retention gap — see verdict notes)")
            verdict_rows.append((base_name, label, best))

    # ---- verdict table ----
    print("\n" + "=" * 72)
    print("VERDICT  (max 1m rows found per era; -1 = perp itself missing)")
    print("=" * 72)
    print(f"{'base':8s} {'era':8s} {'rows':>6s}  meaning")
    for base_name, label, n in verdict_rows:
        if n > 100:
            meaning = "USABLE — corpus buildable for this era"
        elif n > 0:
            meaning = "PARTIAL — data exists but sparse"
        elif n == 0:
            meaning = "EMPTY — retention gap or all strike guesses missed"
        else:
            meaning = "NO PERP — era predates this base's data entirely"
        print(f"{base_name:8s} {label:8s} {n:6d}  {meaning}")

    print("\nInterpretation guide:")
    print(" * recent USABLE + old eras EMPTY on india, but USABLE on global")
    print("   -> build corpus from GLOBAL candles, trade later on INDIA.")
    print(" * EMPTY on both for old eras but recent USABLE")
    print("   -> accumulate-forward mode: start daily corpus capture now.")
    print(" * EMPTY everywhere incl. recent -> candles not served for")
    print("   expired options at all; corpus must come from live capture.")


if __name__ == "__main__":
    main()