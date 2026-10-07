# backend/app/engine/tvx/tvx_core.py
#
# ── TVX_V1_20261007 ── TradingView-alert paper strategy · pure logic
# ============================================================================
# Everything here is side-effect free so it can be tested on the real clock
# without a broker, a relay or a database:
#   * config defaults + validation
#   * MCX session clock (close 23:30, 23:55 while US DST is on)
#   * contract choice: nearest option expiry AFTER today, ATM from the futures
#     of the same contract month
#   * the signal → action table (Anbu, 2026-10-07):
#       BUY  → exit an open PE, buy ATM CE      (repeat BUY while long CE: hold)
#       SELL → exit an open CE, buy ATM PE      (repeat SELL while long PE: hold)
#       T1   → exit whatever is open
#       T2 / CT → logged, no action (T1 already exited)
#   * fills: buy at ASK, sell at BID (LTP only when that side of the book is
#     empty, and the row says so)
#   * Zerodha MCX option charges
# ============================================================================

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

DEFAULTS: Dict = {
    "enabled": False,
    "relay_url": "",            # https://tv-relay.<sub>.workers.dev
    "read_token": "",           # relay READ_TOKEN
    "src": "OB",                # indicator tag in the alert template
    "ticker": "CRUDEOIL1!",     # TradingView {{ticker}} the alerts are on
    "tf": "15",                 # TradingView {{interval}}
    "underlying": "CRUDEOIL",   # Kite instruments() name for the options
    "units_per_lot": 100,       # CRUDEOIL = 100 bbl (Kite MCX lot_size is 1)
    "lots": 1,
    "sl_points": 30.0,          # on option premium
    "stale_sec": 90,            # alerts older than this are never traded
    "poll_sec": 2.0,
    "entry_start": "09:00",
    "squareoff_buffer_min": 10, # flatten this many minutes before MCX close
}

ACTIONABLE = ("BUY", "SELL", "T1")


# ───────────────────────── config ─────────────────────────

def _hhmm(s: str) -> Optional[dt.time]:
    try:
        h, m = str(s).strip().split(":")
        return dt.time(int(h), int(m))
    except Exception:
        return None


def normalize_config(raw: Optional[Dict]) -> Tuple[Dict, List[str]]:
    """Defaults + coercion. Returns (cfg, errors); a cfg with errors must not
    be saved."""
    cfg = dict(DEFAULTS)
    errs: List[str] = []
    raw = raw or {}
    for k in DEFAULTS:
        if k in raw and raw[k] is not None:
            cfg[k] = raw[k]

    def num(key, kind, lo, hi):
        try:
            v = kind(cfg[key])
        except Exception:
            errs.append(f"{key}: not a number")
            return
        if not (lo <= v <= hi):
            errs.append(f"{key}: must be between {lo} and {hi}")
        cfg[key] = v

    cfg["enabled"] = bool(cfg["enabled"]) if not isinstance(cfg["enabled"], str) \
        else cfg["enabled"].strip().lower() in ("1", "true", "yes", "on")
    for k in ("relay_url", "read_token", "src", "ticker", "tf", "underlying", "entry_start"):
        cfg[k] = str(cfg[k] or "").strip()
    cfg["relay_url"] = cfg["relay_url"].rstrip("/")
    cfg["underlying"] = cfg["underlying"].upper()

    num("units_per_lot", int, 1, 100000)
    num("lots", int, 1, 50)
    num("sl_points", float, 0.5, 5000)
    num("stale_sec", int, 10, 3600)
    num("poll_sec", float, 1.0, 30.0)
    num("squareoff_buffer_min", int, 0, 120)
    if _hhmm(cfg["entry_start"]) is None:
        errs.append("entry_start: use HH:MM")
    for k in ("src", "ticker", "tf", "underlying"):
        if not cfg[k]:
            errs.append(f"{k}: required")
    if cfg["relay_url"] and not cfg["relay_url"].startswith("https://"):
        errs.append("relay_url: must start with https://")
    if cfg["enabled"] and (not cfg["relay_url"] or not cfg["read_token"]):
        errs.append("relay_url and read_token are required to enable")
    return cfg, errs


def public_config(cfg: Dict) -> Dict:
    """Config for the UI: the token is never sent back, only whether it is set."""
    out = {k: v for k, v in cfg.items() if k != "read_token"}
    out["read_token_set"] = bool(cfg.get("read_token"))
    return out


# ───────────────────────── MCX session clock ─────────────────────────

def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)        # first Sunday
    return d + dt.timedelta(weeks=n - 1)


def us_dst_active(d: dt.date) -> bool:
    """US DST runs from the 2nd Sunday of March to the 1st Sunday of November.
    MCX trading days strictly between those Sundays use the DST close."""
    return _nth_sunday(d.year, 3, 2) < d < _nth_sunday(d.year, 11, 1)


def mcx_close(d: dt.date) -> dt.time:
    return dt.time(23, 55) if us_dst_active(d) else dt.time(23, 30)


def session(d: dt.date, cfg: Dict) -> Dict[str, dt.datetime]:
    start_t = _hhmm(cfg.get("entry_start", "09:00")) or dt.time(9, 0)
    close = dt.datetime.combine(d, mcx_close(d), IST)
    return {
        "start": dt.datetime.combine(d, start_t, IST),
        "squareoff": close - dt.timedelta(minutes=int(cfg.get("squareoff_buffer_min", 10))),
        "close": close,
    }


def is_weekday(d: dt.date) -> bool:
    return d.weekday() < 5


def entry_block(cfg: Dict, now: dt.datetime, exit_pending: bool) -> Optional[str]:
    """None when a new entry is allowed now, else the reason it is not."""
    if not cfg.get("enabled"):
        return "DISABLED"
    if exit_pending:
        return "EXIT_PENDING"
    d = now.astimezone(IST).date()
    if not is_weekday(d):
        return "WEEKEND"
    s = session(d, cfg)
    if now < s["start"]:
        return "BEFORE_SESSION"
    if now >= s["squareoff"]:
        return "AFTER_SQUAREOFF"
    return None


# ───────────────────────── alerts ─────────────────────────

def parse_iso(s) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def alert_matches(a: Dict, cfg: Dict) -> bool:
    return (str(a.get("src") or "").upper() == cfg["src"].upper()
            and str(a.get("ticker") or "").upper() == cfg["ticker"].upper()
            and str(a.get("tf") or "").upper() == cfg["tf"].upper())


def alert_age_sec(a: Dict, now: dt.datetime) -> Optional[float]:
    t = parse_iso(a.get("received_at"))
    return None if t is None else (now - t).total_seconds()


def decide(sig: str, pos_side: Optional[str], block: Optional[str]) -> List[Tuple[str, str]]:
    """The signal table. Returns ordered actions:
       ("EXIT", reason) · ("ENTER", "CE"|"PE") · ("HOLD", why) · ("SKIP", why) · ("NONE", why)
    An EXIT always precedes an ENTER, and the engine must not ENTER if the
    EXIT failed (that would leave two positions)."""
    sig = (sig or "").upper()
    if sig in ("BUY", "SELL"):
        want = "CE" if sig == "BUY" else "PE"
        if pos_side == want:
            return [("HOLD", f"already long {want}")]
        out: List[Tuple[str, str]] = []
        if pos_side is not None:
            out.append(("EXIT", "SIGNAL"))
        out.append(("ENTER", want) if block is None else ("SKIP", f"no entry: {block}"))
        return out
    if sig == "T1":
        return [("EXIT", "T1")] if pos_side else [("NONE", "T1 with no position")]
    return [("NONE", f"{sig or '?'} is informational")]


# ───────────────────────── contract choice ─────────────────────────

def pick_option_expiry(expiries: List[dt.date], today: dt.date) -> Optional[dt.date]:
    """Nearest expiry strictly AFTER today — on expiry day it rolls to next month."""
    later = sorted(e for e in set(expiries) if e > today)
    return later[0] if later else None


def pick_future(futs: List[Dict], opt_expiry: dt.date) -> Optional[Dict]:
    """The futures contract the option is written on: first FUT expiring on or
    after the option expiry (MCX crude options expire a few days before it)."""
    cands = sorted((f for f in futs if f["expiry"] >= opt_expiry), key=lambda f: f["expiry"])
    return cands[0] if cands else None


def atm_strike(strikes: List[float], ref: float) -> Optional[float]:
    if not strikes or not ref:
        return None
    return min(strikes, key=lambda k: (abs(k - ref), k))


def nearest_strikes(strikes: List[float], ref: float, n: int = 4) -> List[float]:
    if not strikes or not ref:
        return []
    return sorted(sorted(strikes, key=lambda k: (abs(k - ref), k))[:n])


# ───────────────────────── quotes / fills ─────────────────────────

def parse_quote(d: Optional[Dict]) -> Dict:
    d = d or {}
    dep = d.get("depth") or {}

    def top(side):
        try:
            px = float(((dep.get(side) or [{}])[0] or {}).get("price") or 0)
        except Exception:
            px = 0.0
        return px if px > 0 else None

    try:
        ltp = float(d.get("last_price") or 0) or None
    except Exception:
        ltp = None
    return {"ltp": ltp, "bid": top("buy"), "ask": top("sell")}


def entry_fill(q: Dict) -> Tuple[Optional[float], str]:
    if q.get("ask"):
        return q["ask"], "ASK"
    if q.get("ltp"):
        return q["ltp"], "LTP"
    return None, "NONE"


def exit_fill(q: Dict) -> Tuple[Optional[float], str]:
    if q.get("bid"):
        return q["bid"], "BID"
    if q.get("ltp"):
        return q["ltp"], "LTP"
    return None, "NONE"


def sl_hit(q: Dict, sl_price: float) -> bool:
    """Trigger on LTP (a stray thin bid must not stop us out); the exit then
    fills at the bid."""
    return bool(q.get("ltp")) and q["ltp"] <= sl_price


# ───────────────────────── charges (Zerodha, MCX options) ─────────────────────────

CHARGES = {
    "brokerage_per_order": 20.0,   # flat per executed order
    "ctt_sell": 0.0005,            # 0.05% of sell-side premium
    "exch": 0.000418,              # MCX txn 0.0418% of premium turnover
    "sebi": 0.000001,              # ₹10 / crore
    "stamp_buy": 0.00003,          # 0.003% of buy-side premium
    "gst": 0.18,                   # on brokerage + exch + sebi
}


def mcx_option_charges(entry_px: float, exit_px: float, qty: int) -> Dict[str, float]:
    buy = entry_px * qty
    sell = exit_px * qty
    turnover = buy + sell
    brokerage = 2 * CHARGES["brokerage_per_order"]
    exch = turnover * CHARGES["exch"]
    sebi = turnover * CHARGES["sebi"]
    out = {
        "brokerage": brokerage,
        "ctt": sell * CHARGES["ctt_sell"],
        "exch": exch,
        "sebi": sebi,
        "stamp": buy * CHARGES["stamp_buy"],
        "gst": (brokerage + exch + sebi) * CHARGES["gst"],
    }
    out = {k: round(v, 2) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out
