# backend/app/backtest/replay/live_replay.py
#
# ── TRADE_REPLAY_LIVE_20261017 ── "Trade Replay" for the Trades page (paper
# + paper-table LIVE rows, /paper_trades) and the Analytics page (live
# history, /trades/history). Admin-only: the route is mounted behind
# _require_admin_ui in api_server.py; the pages render ▶ only for an admin
# ui_level (fail-closed while the licence loads).
#
# HOW
#   * Rows come from the pages' OWN loaders (get_paper_trades /
#     _query_trades), so a key here is exactly the key the page holds:
#     paper_trade_id / trade_id, else "<entry_time>_<symbol>" (the pages'
#     own fallback).
#   * The row's group (explicit group_id, else same strategy + entry minute
#     + expiry) becomes a stand-in "run" handed to replay_core's
#     build_replay_for — the SAME payload builder, chart and overlay code the
#     Backtest replay uses. Nothing is re-derived for live.
#   * Indicators: the strategy's backtest overlay code on the strategy's
#     CURRENT saved Settings config (read without the loader's seed-on-absent
#     write). The config in force at the time of the trade is not recorded;
#     the payload says so.
#
# CORPUS FILL (decided by Anbu 2026-10-17: "Continue" on the stated plan)
#   Missing 1m bars are pulled from Kite and ADDED to backtest.db's
#   backtest_candles_1m, through kite_backfill's data/trade session
#   resolution, its chunk size, throttle and IST stamp conversion:
#     * NIFTY spot: the trade's sessions + WARM_CAL_DAYS before (indicator
#       warm-up). Written under the corpus's EXISTING spot identity (token/
#       symbol of its SPOT rows; 256265/NIFTYSPOT on an empty corpus), so a
#       Kite bar can never duplicate a Dhan bar.
#     * each leg's contract: the sessions it was held.
#   Past sessions are INSERT-OR-IGNORE — an existing corpus row is never
#   modified. Only TODAY's session is upserted (the forming bar moves).
#   A session counts as present when its last bar is ≥ 15:25; past
#   sessions Kite was already asked for are not asked again this process.
#   Kite only knows contracts that have not expired: an expired contract
#   missing from the corpus is reported in the payload's notes, never
#   invented. No Kite session → corpus only, with a note. Never fatal.
#
# ── TRADE_REPLAY_STREAM_20261017 ── LIVE MODE (an open trade, in session)
#   The modal re-asks every LIVE_POLL_MS with live=1. Each answer is a full
#   rebuild by the same replay code, plus:
#     * the FORMING bar of NIFTY spot and of every open leg, built from the
#       app's own in-memory prices (MarketIndicesState / LTPStore — the
#       websocket feed; no broker call) by a 1 s sampler that runs only while
#       a live replay is being polled. Minutes the sampler saw that Kite has
#       not served yet bridge the gap until it does.
#     * those bars live ONLY in a TEMP view on the replay's own read-only
#       connection (replay_core's corpus_hook); they are never written to the
#       corpus. Kite fills write only minutes closed ≥ 3 s ago.
#     * PROVISIONAL indicator values: the strategy's own overlay code runs on
#       closed bars + the forming bar, "as if the price stayed here until the
#       bar closes": the forming minute's close is carried flat to the end of
#       its 15-minute bucket (PAD_TF) inside the TEMP view only, so code that
#       draws completed timeframe bars only (TMA's `complete` filter) still
#       values the forming 5m/15m bar. Everything after the forming minute is
#       then stripped from the payload. The points of each overlay's forming
#       bar are split into a dotted "provisional" segment (they move until
#       that bar closes — the engines decide on the close).
#     * a live price line on each pane and live.legs (LTP, distance to SL/TP).
#
# ── TRADE_REPLAY_WATCH_20261021 ── WATCH MODE (no open position)
#   build_watch(sid): the strategy's live chart with no trade — NIFTY spot for
#   the previous + current session with the strategy's own indicators,
#   forming bar and provisional values while in session, so an entry can be
#   anticipated. Built by the same payload code through a "ghost" leg on the
#   spot series that is stripped from the answer (no legs, markers, levels,
#   lower pane). find_keys(strategy) answers "w|<SID>" when the strategy holds
#   no open position, with its trades before it for Prev.

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.backtest.replay import replay_core as RC

FENCE = "TRADE_REPLAY_LIVE_20261017"
SCOPES = ("trades", "analytics")
WARM_CAL_DAYS = 75              # calendar days of spot before the entry (≈ 50 sessions ≥ DEEP_WARM 40)
COMPLETE_MOD = 15 * 60 + 25     # a session whose last bar is at/after 15:25 is complete
TODAY_REFRESH_S = 30            # today's bars are re-pulled at most this often per instrument
LIVE_GAP_S = 5                  # …but a just-closed minute Kite has not served yet is re-asked after 5 s
CLOSE_GRACE_S = 3               # a minute is written only once it closed this long ago (never a forming bar)
FILL_BUDGET_S = 40.0            # stop asking Kite after this long; replay what is there
KITE_TTL_S = 15 * 60
DUMP_TTL_S = 60 * 60
SPOT_KITE_TOKEN = 256265        # Kite's NIFTY 50 index token
SPOT_FALLBACK_SYMBOL = "NIFTYSPOT"

_SID_ALIASES = {"1M_SCALP": "SCALP_V1", "BB": "BB_V1", "HA": "HA_V1"}
# live Settings key → the backtest key the overlay normaliser reads
_CFG_ALIASES = {"VET_V1": (("signal_tf", "timeframe_minutes"),)}

_FILL_LOCK = threading.Lock()
_CHECKED: Dict[Tuple[str, str], bool] = {}        # (instrument id, ISO day) → Kite already asked
_TODAY_AT: Dict[str, float] = {}                  # instrument id → last today-pull (monotonic)
_KITE = {"k": None, "at": 0.0}
_DUMP = {"by_sym": None, "at": 0.0}

_WEEKLY = re.compile(r"^(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY)(\d{2})([1-9OND])(\d{2})(\d+(?:\.\d+)?)(CE|PE)$")
_MONTHLY = re.compile(r"^(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
                      r"(\d+(?:\.\d+)?)(CE|PE)$")
_MCODE = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "O": 10, "N": 11, "D": 12}


# ─────────────────────────────────────────────────────────────────────
# clock (monkeypatched by the suite)
# ─────────────────────────────────────────────────────────────────────
def _now_ts() -> int:
    return int(time.time())


def _today() -> date:
    return RC.ist_date(_now_ts())


# ─────────────────────────────────────────────────────────────────────
# rows — the pages' own loaders
# ─────────────────────────────────────────────────────────────────────
def canon_sid(s) -> str:
    s = str(s or "").strip().upper()
    s = _SID_ALIASES.get(s, s)
    return s.replace(" ", "_")


def row_key(r: dict, scope: str) -> str:
    rid = r.get("paper_trade_id") if scope == "trades" else r.get("trade_id")
    if rid is not None and rid != "":
        return str(rid)
    sym = r.get("symbol") if scope == "trades" else (r.get("symbol") or r.get("tradingsymbol"))
    return f"{r.get('entry_time')}_{sym}"


def _rows_trades() -> List[dict]:
    from app.api.paper_trades_routes import get_paper_trades
    d = get_paper_trades() or {}
    return list(d.get("open") or []) + list(d.get("closed") or [])


def _rows_analytics() -> List[dict]:
    from app.api.trade_history_routes import _query_trades
    return list(_query_trades(None, None, None) or [])


def load_rows(scope: str) -> List[dict]:
    return _rows_trades() if scope == "trades" else _rows_analytics()


def _sid_of(r: dict, scope: str) -> str:
    return canon_sid(r.get("strategy_name") if scope == "trades" else r.get("strategy_id"))


def _num(v) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# contract meta: corpus → today's instruments dump → symbol pattern
# ─────────────────────────────────────────────────────────────────────
def _dump_by_sym() -> Dict[str, dict]:
    now = time.monotonic()
    if _DUMP["by_sym"] is not None and now - _DUMP["at"] < DUMP_TTL_S:
        return _DUMP["by_sym"]
    out: Dict[str, dict] = {}
    try:
        from app.fetcher.zerodha_instruments import load_instruments_df
        df = load_instruments_df()
        if df is not None and not df.empty and "tradingsymbol" in df.columns:
            if "segment" in df.columns:
                df = df[df["segment"].astype(str).str.startswith("NFO")]
            for r in df.to_dict("records"):
                exp = r.get("expiry")
                out[str(r.get("tradingsymbol"))] = {
                    "token": int(r["instrument_token"]) if r.get("instrument_token") == r.get("instrument_token") else None,
                    "underlying": str(r.get("name") or "").upper() or None,
                    "instrument_type": r.get("instrument_type"),
                    "strike": float(r.get("strike") or 0.0),
                    "expiry": exp.isoformat() if hasattr(exp, "isoformat") else (str(exp) if exp else ""),
                }
    except Exception:
        out = {}
    _DUMP["by_sym"], _DUMP["at"] = out, now
    return out


def _parse_symbol(sym: str) -> dict:
    s = (sym or "").replace(" ", "").upper()
    m = _WEEKLY.match(s)
    if m:
        u, yy, mc, dd, k, typ = m.groups()
        try:
            exp = date(2000 + int(yy), _MCODE[mc], int(dd)).isoformat()
        except ValueError:
            exp = ""
        return {"underlying": u, "instrument_type": typ, "strike": float(k), "expiry": exp}
    m = _MONTHLY.match(s)
    if m:
        u, yy, mon, k, typ = m.groups()
        return {"underlying": u, "instrument_type": typ, "strike": float(k), "expiry": f"{2000 + int(yy)}-{mon}"}
    return {"underlying": None, "instrument_type": None, "strike": 0.0, "expiry": ""}


class _Meta:
    """Per-request memo of contract meta."""

    def __init__(self, corpus_path: str):
        self.path = corpus_path
        self.memo: Dict[str, dict] = {}

    def __call__(self, sym: str, row_token=None) -> dict:
        if sym in self.memo:
            return self.memo[sym]
        out = {"token": None, "underlying": None, "instrument_type": None, "strike": 0.0, "expiry": "",
               "in_corpus": False}
        try:
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
            try:
                r = c.execute("SELECT underlying, instrument_type, strike, expiry FROM backtest_candles_1m "
                              "WHERE tradingsymbol=? LIMIT 1", (sym,)).fetchone()
            finally:
                c.close()
            if r:
                out.update({"underlying": r[0], "instrument_type": r[1], "strike": float(r[2] or 0),
                            "expiry": r[3] or "", "in_corpus": True})
        except Exception:
            pass
        d = _dump_by_sym().get(sym)
        if d:
            out["token"] = d.get("token")
            for k in ("underlying", "instrument_type", "expiry"):
                if not out.get(k) and d.get(k):
                    out[k] = d[k]
            if not out.get("strike"):
                out["strike"] = d.get("strike") or 0.0
        if out["token"] is None and row_token not in (None, "", 0):
            try:
                out["token"] = int(row_token)
            except (TypeError, ValueError):
                pass
        p = _parse_symbol(sym)
        for k in ("underlying", "instrument_type", "expiry"):
            if not out.get(k) and p.get(k):
                out[k] = p[k]
        if not out.get("strike"):
            out["strike"] = p.get("strike") or 0.0
        self.memo[sym] = out
        return out


# ─────────────────────────────────────────────────────────────────────
# row → backtest-trade shape
# ─────────────────────────────────────────────────────────────────────
def to_trade(r: dict, scope: str, meta: _Meta) -> Optional[dict]:
    sym = r.get("tradingsymbol") or r.get("symbol")
    entry_ts, entry = _num(r.get("entry_time")), _num(r.get("entry_price"))
    if not sym or entry_ts is None or entry is None:
        return None
    sym = str(sym).replace(" ", "").upper()
    mt = meta(sym, r.get("token"))
    short = str(r.get("trade_direction") or "").upper() in ("SHORT", "SELL")
    qty = int(_num(r.get("qty")) or 0)
    exit_ts, exit_px = _num(r.get("exit_time")), _num(r.get("exit_price"))
    closed = exit_ts is not None and exit_px is not None
    gross = charges = None
    if closed:
        gross = round(((entry - exit_px) if short else (exit_px - entry)) * qty, 2)
        stored = _num(r.get("total_charges"))
        if stored is None:
            stored = _num(r.get("charges"))
        if stored is None:
            try:
                from app.backtest.charges.charges_model import calculate_option_charges
                stored = calculate_option_charges(entry_price=entry, exit_price=exit_px, qty=qty,
                                                  direction="SHORT" if short else "LONG").total_charges
            except Exception:
                stored = 0.0
        charges = round(stored, 2)
    sl, tp = _num(r.get("sl_price")), _num(r.get("tp_price"))
    tc = r.get("trade_class")
    cond = tc if (isinstance(tc, str) and tc and not tc.startswith("GTT:") and len(tc) <= 16) else None
    return {
        "key": row_key(r, scope), "group_id": r.get("group_id") or None,
        "tradingsymbol": sym, "instrument_type": mt.get("instrument_type") or r.get("side") or r.get("slot"),
        "strike": mt.get("strike"), "expiry": mt.get("expiry") or "",
        "underlying": mt.get("underlying"), "token": mt.get("token"),
        "direction": "SHORT" if short else "LONG", "qty": qty,
        "entry_ts": int(entry_ts), "entry_price": entry,
        "exit_ts": int(exit_ts) if closed else None, "exit_price": exit_px if closed else None,
        "exit_reason": r.get("exit_reason"),
        "sl": sl if sl else None, "tp": tp if tp else None,          # TSG/IC store 0 = no per-leg level
        "pnl": gross, "charges": charges,
        "net_pnl": (round(gross - charges, 2) if closed else None),
        "condition": cond, "synthetic": 0,
    }


def find_group(rows: List[dict], scope: str, key: str, meta: _Meta) -> Tuple[Optional[str], List[dict]]:
    """→ (strategy_id, normalised legs of the key's group) — explicit
    group_id first, else same strategy + entry minute + expiry (the
    backtest replay's grouping)."""
    hit = next((r for r in rows if row_key(r, scope) == key), None)
    if hit is None:
        return None, []
    sid = _sid_of(hit, scope)
    me = to_trade(hit, scope, meta)
    if me is None:
        return sid, []
    same = [r for r in rows if _sid_of(r, scope) == sid]
    if me["group_id"]:
        cand = [r for r in same if (r.get("group_id") or None) == me["group_id"]]
    else:
        m0 = me["entry_ts"] // 60
        cand = [r for r in same if not r.get("group_id") and _num(r.get("entry_time")) is not None
                and int(_num(r.get("entry_time"))) // 60 == m0]
    legs = []
    for r in cand:
        t = to_trade(r, scope, meta)
        if t is None:
            continue
        if not me["group_id"] and t["expiry"] != me["expiry"]:
            continue
        legs.append(t)
    if not any(t["key"] == key for t in legs):
        legs.append(me)
    legs.sort(key=lambda t: (t["entry_ts"], t["key"]))
    for i, t in enumerate(legs, start=1):
        t["id"] = i
    return sid, legs


def live_cfg(sid: str) -> Tuple[dict, Optional[str]]:
    """The strategy's saved Settings config merged over its default — read
    WITHOUT strategy_loader's seed-on-absent write."""
    try:
        from app.config import strategy_loader as SL
        cfg = deepcopy(SL.DEFAULT_STRATEGY_CONFIGS.get(sid, {}))
        p = SL._get_strategy_path(sid)
        src = "defaults"
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                SL.deep_update(cfg, json.load(f) or {})
            src = "settings"
    except Exception as e:
        return {}, f"config unreadable ({type(e).__name__}) — backtest defaults"
    for a, b in _CFG_ALIASES.get(sid, ()):
        if a in cfg and b not in cfg:
            cfg[b] = cfg[a]
    return cfg, (None if src == "settings" else "no saved Settings config — backtest defaults")


# ─────────────────────────────────────────────────────────────────────
# Kite fill
# ─────────────────────────────────────────────────────────────────────
def _kite():
    now = time.monotonic()
    if _KITE["k"] is not None and now - _KITE["at"] < KITE_TTL_S:
        return _KITE["k"]
    try:
        from app.backtest.backfill.kite_backfill import _resolve_kite
        k = _resolve_kite()
    except Exception:
        k = None
    _KITE["k"], _KITE["at"] = k, now
    return k


def _sessions(d0: date, d1: date) -> List[date]:
    try:
        from app.utils.market_hours import is_trading_day
    except Exception:          # pragma: no cover
        is_trading_day = None
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            try:
                ok = is_trading_day(d) if is_trading_day else True
            except Exception:
                ok = True
            if ok:
                out.append(d)
        d += timedelta(days=1)
    return out


def _runs(days: List[date], gap: int = 4) -> List[Tuple[date, date]]:
    out: List[List[date]] = []
    for d in sorted(days):
        if out and (d - out[-1][1]).days <= gap:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a, b) for a, b in out]


def _day_stats(c, where: str, params: tuple, lo: int, hi: int) -> Dict[date, int]:
    rows = c.execute(
        "SELECT date(ts,'unixepoch','+5 hours','+30 minutes') d, MAX(ts) FROM backtest_candles_1m "
        f"WHERE {where} AND ts>=? AND ts<? GROUP BY d", params + (lo, hi)).fetchall()
    return {date.fromisoformat(r[0]): RC.mod_of(int(r[1])) for r in rows if r[0]}


def _needed(stats: Dict[date, int], days: List[date], ident: str, today: date) -> List[date]:
    now = time.monotonic()
    out = []
    for d in days:
        if d > today:
            continue
        if d == today:
            since = now - _TODAY_AT.get(ident, -1e9)
            nts = _now_ts()                                   # ── TRADE_REPLAY_STREAM_20261017 ──
            last_closed = RC.mod_of(nts - CLOSE_GRACE_S) - 1
            gap = stats.get(d, -1) < min(last_closed, RC.SESSION_CLOSE_MIN - 1)
            if since >= TODAY_REFRESH_S or (gap and since >= LIVE_GAP_S):
                out.append(d)
            continue
        if stats.get(d, -1) >= COMPLETE_MOD:
            continue
        if _CHECKED.get((ident, d.isoformat())):
            continue
        out.append(d)
    return out


def _kite_rows(kite, token: int, lo: date, hi: date) -> Tuple[list, Optional[str]]:
    from app.backtest.backfill import kite_backfill as KB
    rows: list = []
    for clo, chi in KB._date_chunks(lo, hi, KB.CHUNK_DAYS):
        attempt = 0
        while True:
            try:
                rows.extend(kite.historical_data(instrument_token=int(token),
                                                 from_date=clo.strftime("%Y-%m-%d 00:00:00"),
                                                 to_date=chi.strftime("%Y-%m-%d 23:59:59"),
                                                 interval=KB.INTERVAL, oi=True) or [])
                time.sleep(KB.THROTTLE_S)
                break
            except Exception as e:
                msg = str(e)
                attempt += 1
                if attempt > 2:
                    return rows, f"{type(e).__name__}: {msg[:100]}"
                rate = ("Too many requests" in msg) or ("429" in msg)
                time.sleep((KB.BACKOFF_BASE_S ** attempt) if rate else KB.THROTTLE_S * 2)
    return rows, None


_INS = ("INSERT OR IGNORE INTO backtest_candles_1m (instrument_token, ts, underlying, tradingsymbol, "
        "instrument_type, strike, expiry, open, high, low, close, volume, oi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")
_UPS = _INS.replace("INSERT OR IGNORE", "INSERT") + (
    " ON CONFLICT(instrument_token, ts) DO UPDATE SET open=excluded.open, high=excluded.high, "
    "low=excluded.low, close=excluded.close, volume=excluded.volume, oi=excluded.oi")


def _write(c, ident: tuple, rows: list, days: set, today: date) -> int:
    from app.backtest.backfill.kite_backfill import _to_epoch_ist
    tok, und, sym, typ, strike, exp = ident
    ins, ups = [], []
    closed_before = _now_ts() - CLOSE_GRACE_S - 60          # ── TRADE_REPLAY_STREAM_20261017 ── never a forming bar
    for r in rows:
        try:
            ts = _to_epoch_ist(r["date"])
        except Exception:
            continue
        d = RC.ist_date(ts)
        if d not in days or ts > closed_before:
            continue
        rec = (int(tok), ts, und, sym, typ, float(strike or 0.0), exp or "",
               float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
               int(r.get("volume") or 0), int(r.get("oi") or 0))
        (ups if d == today else ins).append(rec)
    if ins:
        c.executemany(_INS, ins)
    if ups:
        c.executemany(_UPS, ups)
    c.commit()
    return len(ins) + len(ups)


def ensure_candles(legs: List[dict], underlying: str, *, prev_sessions: int = 0) -> List[str]:
    """Add whatever Kite has for the group's spot window and legs to the
    corpus. Returns notes for the payload. Never raises."""
    notes: List[str] = []
    try:
        with _FILL_LOCK:
            _fill(legs, underlying, notes, prev_sessions)
    except Exception as e:                         # pragma: no cover - reported, never raised
        notes.append(f"Kite fill stopped: {type(e).__name__}: {str(e)[:120]}")
    return notes


def _fill(legs, underlying, notes, prev_sessions):
    path = RC.runs_db_path()
    today = _today()
    t0 = min(t["entry_ts"] for t in legs)
    t1 = max([t["exit_ts"] or t["entry_ts"] for t in legs] + ([_now_ts()] if any(t["exit_ts"] is None for t in legs) else []))
    d0, d1 = RC.ist_date(t0), min(RC.ist_date(t1), today)
    c = sqlite3.connect(path, timeout=30)
    try:
        c.execute("PRAGMA journal_mode=WAL;")
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backtest_candles_1m'").fetchone():
            notes.append("backtest corpus has no candle table — run Backtest → Backfill once")
            return
        jobs = []          # (ident tuple, fetch token, needed days, label)
        und = (underlying or "NIFTY").upper()
        if und == "NIFTY":
            sp = c.execute("SELECT instrument_token, tradingsymbol FROM backtest_candles_1m "
                           "WHERE underlying='NIFTY' AND instrument_type='SPOT' LIMIT 1").fetchone()
            ident = (int(sp[0]), "NIFTY", sp[1], "SPOT", 0.0, "") if sp else \
                (SPOT_KITE_TOKEN, "NIFTY", SPOT_FALLBACK_SYMBOL, "SPOT", 0.0, "")
            sdays = _sessions(d0 - timedelta(days=WARM_CAL_DAYS + 3 * max(0, prev_sessions)), d1)
            lo, hi = RC.day_start(sdays[0]) if sdays else 0, RC.day_start(d1) + 86400
            st = _day_stats(c, "underlying='NIFTY' AND expiry='' AND instrument_type='SPOT'", (), lo, hi)
            need = _needed(st, sdays, "SPOT:NIFTY", today)
            if need:
                jobs.append((ident, SPOT_KITE_TOKEN, need, "NIFTY spot"))
        else:
            notes.append(f"{und} underlying bars are not filled from Kite — corpus only")
        seen = set()
        for t in legs:
            if t.get("ghost"):                               # ── TRADE_REPLAY_WATCH_20261021 ── spot-only fill
                continue
            sym = t["tradingsymbol"]
            if sym in seen:
                continue
            seen.add(sym)
            a = RC.ist_date(t["entry_ts"])
            b = min(RC.ist_date(t["exit_ts"] or _now_ts()), today)
            days = _sessions(a, b)
            st = _day_stats(c, "tradingsymbol=?", (sym,), RC.day_start(a), RC.day_start(b) + 86400)
            need = _needed(st, days, sym, today)
            if not need:
                continue
            if not t.get("token"):
                notes.append(f"{sym}: not in the corpus for {', '.join(d.isoformat() for d in need[:3])}"
                             f"{'…' if len(need) > 3 else ''} and no Kite token (expired contract) — "
                             "run Backtest → Backfill before expiry to keep these replayable")
                for d in need:
                    if d < today:
                        _CHECKED[(sym, d.isoformat())] = True
                continue
            ident = (int(t["token"]), (t.get("underlying") or und), sym, t.get("instrument_type") or "",
                     t.get("strike") or 0.0, t.get("expiry") or "")
            jobs.append((ident, int(t["token"]), need, sym))
        if not jobs:
            return
        kite = _kite()
        if kite is None:
            notes.append("no Kite session — chart shows what the backtest corpus already has")
            return
        started = time.monotonic()
        added: Dict[str, int] = {}
        for ident, ftok, need, label in jobs:
            idk = "SPOT:NIFTY" if ident[3] == "SPOT" else ident[2]
            for lo_d, hi_d in _runs(need):
                if time.monotonic() - started > FILL_BUDGET_S:
                    notes.append("Kite fill hit its time budget — reopen the replay to continue")
                    return
                rows, err = _kite_rows(kite, ftok, lo_d, hi_d)
                if err and not rows:
                    notes.append(f"Kite: {label} {lo_d}…{hi_d} failed ({err})")
                    if "token" in err.lower() or "permission" in err.lower():
                        _KITE["k"] = None
                    continue
                span = {d for d in need if lo_d <= d <= hi_d}
                n = _write(c, ident, rows, span, today)
                added[label] = added.get(label, 0) + n
                for d in span:
                    if d < today:
                        _CHECKED[(idk, d.isoformat())] = True
                    else:
                        _TODAY_AT[idk] = time.monotonic()
        got = [f"{k} +{v} bars" for k, v in added.items() if v]
        if got:
            notes.append("added from Kite to the backtest corpus: " + ", ".join(got))
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────────────
# ── TRADE_REPLAY_STREAM_20261017 ── live mode
# ─────────────────────────────────────────────────────────────────────
LIVE_POLL_MS = 2000
SAMPLE_S = 1.0
WATCH_TTL_S = 45
STALE_S = 10
PAD_TF = 15                     # carry the forming close to the end of this bucket (covers every tf ≤ 15 that divides it)
SPOT_IDENT = "SPOT:NIFTY"
_TRACKER_THREAD = True          # the suite samples by hand


def _mono() -> float:
    return time.monotonic()


def _read_ltp(ident: str) -> Optional[Tuple[float, Optional[float]]]:
    """(price, epoch of the last print or None) from the app's in-memory feed."""
    try:
        if ident == SPOT_IDENT:
            from app.marketdata.market_indices_state import MarketIndicesState as M
            px = M._ltp.get("NIFTY")
            return (float(px), M._ltp_ws_ts.get("NIFTY")) if px else None
        from app.marketdata.ltp_store import LTPStore
        r = LTPStore.get_with_timestamp(ident)
        return (float(r[0]), float(r[1])) if r and r[0] else None
    except Exception:
        return None


class _Tracker:
    """1 s sampler of watched instruments' in-memory prices → today's minute
    OHLC. Runs only while a live replay is being polled (watch TTL)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.watch: Dict[str, float] = {}
        self.bars: Dict[str, Dict[int, list]] = {}
        self.last: Dict[str, Tuple[float, Optional[float], int]] = {}
        self.thread = None

    def touch(self, idents: List[str]) -> None:
        exp = _mono() + WATCH_TTL_S
        with self.lock:
            for i in idents:
                self.watch[i] = exp
        self.sample_once()
        if _TRACKER_THREAD and (self.thread is None or not self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, name="replay-live-sampler", daemon=True)
            self.thread.start()

    def sample_once(self) -> None:
        now, mono = _now_ts(), _mono()
        m = now - now % 60
        day0 = RC.day_start(RC.ist_date(now))
        with self.lock:
            for ident in list(self.watch):
                if self.watch[ident] < mono:
                    self.watch.pop(ident, None)
                    self.bars.pop(ident, None)
                    self.last.pop(ident, None)
                    continue
                r = _read_ltp(ident)
                if not r:
                    continue
                px, pts = r
                self.last[ident] = (px, pts, now)
                b = self.bars.setdefault(ident, {})
                cur = b.get(m)
                if cur is None:
                    b[m] = [px, px, px, px]
                else:
                    cur[1] = max(cur[1], px)
                    cur[2] = min(cur[2], px)
                    cur[3] = px
                for k in [k for k in b if k < day0]:
                    b.pop(k, None)

    def _run(self) -> None:
        while True:
            with self.lock:
                alive = bool(self.watch)
            if not alive:
                return
            try:
                self.sample_once()
            except Exception:
                pass
            time.sleep(SAMPLE_S)

    def snapshot(self, ident: str) -> Tuple[Dict[int, list], Optional[Tuple[float, Optional[float], int]]]:
        with self.lock:
            return {k: list(v) for k, v in self.bars.get(ident, {}).items()}, self.last.get(ident)


_TRACKER = _Tracker()


def live_session(now: Optional[int] = None) -> bool:
    now = _now_ts() if now is None else now
    d = RC.ist_date(now)
    if d.weekday() >= 5:
        return False
    try:
        from app.utils.market_hours import is_trading_day
        if not is_trading_day(d):
            return False
    except Exception:
        pass
    return RC.SESSION_OPEN_MIN <= RC.mod_of(now) < RC.SESSION_CLOSE_MIN


def _forming(path: str, idents: Dict[str, dict], now: int) -> Tuple[List[tuple], Dict[str, dict]]:
    """→ (corpus-shaped rows for the TEMP view, per-ident live info). Rows are
    the sampler's minutes AFTER the corpus's last bar of today, the forming
    minute last; corpus bars always win for any minute they already hold."""
    rows: List[tuple] = []
    info: Dict[str, dict] = {}
    cur_m = now - now % 60
    day0 = RC.day_start(RC.ist_date(now))
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        for ident, mt in idents.items():
            if ident == SPOT_IDENT:
                sp = c.execute("SELECT instrument_token, tradingsymbol FROM backtest_candles_1m WHERE "
                               "underlying='NIFTY' AND instrument_type='SPOT' LIMIT 1").fetchone()
                tok, sym = (sp[0], sp[1]) if sp else (SPOT_KITE_TOKEN, SPOT_FALLBACK_SYMBOL)
                last = c.execute("SELECT MAX(ts) FROM backtest_candles_1m WHERE underlying='NIFTY' AND expiry='' "
                                 "AND instrument_type='SPOT' AND ts>=? AND ts<?", (day0, day0 + 86400)).fetchone()[0]
                ident_row = (int(tok), "NIFTY", sym, "SPOT", 0.0, "")
            else:
                last = c.execute("SELECT MAX(ts) FROM backtest_candles_1m WHERE tradingsymbol=? AND ts>=? AND ts<?",
                                 (ident, day0, day0 + 86400)).fetchone()[0]
                tr = c.execute("SELECT instrument_token FROM backtest_candles_1m WHERE tradingsymbol=? LIMIT 1",
                               (ident,)).fetchone()
                tok = tr[0] if tr else (mt.get("token") or 0)
                ident_row = (int(tok), mt.get("underlying") or "NIFTY", ident, mt.get("instrument_type") or "",
                             float(mt.get("strike") or 0.0), mt.get("expiry") or "")
            bars, lt = _TRACKER.snapshot(ident)
            last = int(last) if last is not None else day0 - 60
            if lt and cur_m not in bars:                     # a print exists but none sampled this minute yet
                prev = bars.get(max([k for k in bars if k < cur_m], default=-1))
                o = prev[3] if prev else lt[0]
                bars[cur_m] = [o, max(o, lt[0]), min(o, lt[0]), lt[0]]
            fm = None
            for m in sorted(k for k in bars if last < k <= cur_m and RC.SESSION_OPEN_MIN <= RC.mod_of(k) < RC.SESSION_CLOSE_MIN):
                o, h, l_, cl = bars[m]
                rows.append(ident_row[:1] + (m,) + ident_row[1:] + (o, h, l_, cl, 0, 0))
                fm = (m, cl)
            if fm and fm[0] == cur_m:                        # provisional padding (TEMP view only; stripped later)
                end = min(_bucket(cur_m, PAD_TF) + PAD_TF * 60, day0 + RC.SESSION_CLOSE_MIN * 60)
                for m in range(cur_m + 60, end, 60):
                    rows.append(ident_row[:1] + (m,) + ident_row[1:] + (fm[1], fm[1], fm[1], fm[1], 0, 0))
            age = None
            if lt:
                age = round(now - (lt[1] if lt[1] else lt[2]), 1)
            info[ident] = {"ltp": (lt[0] if lt else None), "age_s": age,
                           "forming": bars.get(cur_m) if cur_m > last else None}
    finally:
        c.close()
    return rows, info


def _hook_for(rows: List[tuple], errs: List[str]):
    def hook(corpus):
        if not rows:
            return
        try:
            c = corpus.c
            c.execute("CREATE TEMP TABLE IF NOT EXISTS rp_forming AS SELECT * FROM main.backtest_candles_1m WHERE 0")
            c.execute("DELETE FROM temp.rp_forming")
            c.executemany("INSERT INTO temp.rp_forming VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            c.execute("CREATE TEMP VIEW IF NOT EXISTS backtest_candles_1m AS "
                      "SELECT * FROM main.backtest_candles_1m UNION ALL SELECT * FROM temp.rp_forming")
        except Exception as e:                               # pragma: no cover - reported, not raised
            errs.append(f"forming bar unavailable: {type(e).__name__}: {e}"[:200])
    return hook


def _bucket(ts: int, tf: int) -> int:
    ds = RC.day_start(RC.ist_date(ts))
    m = RC.mod_of(ts)
    tf = max(1, int(tf or 1))
    return ds + (RC.SESSION_OPEN_MIN + ((m - RC.SESSION_OPEN_MIN) // tf) * tf) * 60


def _split_provisional(ovs: List[dict], forming_ts: int, default_tf: int) -> List[dict]:
    """Each line overlay → its closed part + a 'provisional' part holding the
    points of the still-forming bar (joined to the last closed point)."""
    out = []
    for o in ovs or []:
        if o.get("type") != "line" or not o.get("points"):
            out.append(o)
            continue
        b0 = _bucket(forming_ts, int(o.get("tf") or default_tf or 1))
        closed = [p for p in o["points"] if int(p[0]) < b0]
        prov = [p for p in o["points"] if int(p[0]) >= b0]
        if not prov:
            out.append(o)
            continue
        if closed:
            out.append(dict(o, points=closed))
        out.append(dict(o, points=(closed[-1:] + prov), provisional=True, id=f"{o.get('id') or o.get('label')}:prov"))
    return out


def _live_leg(t: dict, ltp: Optional[float], age) -> dict:
    k = t.get("strike")
    ks = (f"{int(k)}" if float(k).is_integer() else f"{k:g}") if isinstance(k, (int, float)) and k else ""
    out = {"key": t["key"], "symbol": t["tradingsymbol"], "label": f"{ks}{t.get('instrument_type') or ''}" or t["tradingsymbol"],
           "short": t["direction"] == "SHORT", "entry": t["entry_price"],
           "ltp": ltp, "age_s": age, "sl": t.get("sl"), "tp": t.get("tp"), "qty": t.get("qty")}
    if ltp is not None:
        sgn = -1 if out["short"] else 1
        out["pnl"] = round(sgn * (ltp - t["entry_price"]) * (t.get("qty") or 0), 2)
        for k in ("sl", "tp"):
            lv = t.get(k)
            if lv:
                dist = abs(lv - ltp)
                out[f"{k}_dist"] = round(dist, 2)
                out[f"{k}_pct"] = round(dist / ltp * 100, 2) if ltp else None
                span = abs(lv - t["entry_price"])
                # how much of the way from entry to this level price has travelled (≥1 = through it)
                towards = (lv - t["entry_price"]) * (ltp - t["entry_price"]) > 0
                out[f"{k}_used"] = round(min(1.5, (span - dist) / span), 3) if (span and towards) else 0.0
    return out


def _strip_future(out: dict, cut: int) -> None:
    """Drop everything after the forming minute (the provisional padding)."""
    keep = lambda pts: [p for p in pts if int(p[0]) <= cut]
    out["spot"]["bars"] = keep(out["spot"].get("bars") or [])
    out["legs_bars"] = {k: keep(v) for k, v in (out.get("legs_bars") or {}).items()}
    ha = (out.get("premium") or {}).get("ha") or {}
    for k, v in list(ha.items()):
        if isinstance(v, list):
            ha[k] = keep(v)
    if isinstance(out.get("mtm"), dict) and out["mtm"].get("path"):
        out["mtm"]["path"] = keep(out["mtm"]["path"])

    def clip(ovs):
        res = []
        for o in ovs or []:
            o = dict(o)
            if isinstance(o.get("points"), list):
                o["points"] = keep(o["points"])
                if not o["points"] and o.get("type") != "level":
                    continue
            if o.get("type") == "band":
                o["upper"], o["lower"] = keep(o.get("upper") or []), keep(o.get("lower") or [])
            if o.get("type") in ("box", "shade") and o.get("from_ts") is not None and int(o["from_ts"]) > cut:
                continue
            res.append(o)
        return res
    out["spot"]["overlays"] = clip(out["spot"].get("overlays"))
    out["premium"]["overlays"] = {k: clip(v) for k, v in (out["premium"].get("overlays") or {}).items()}
    osc = out.get("osc")
    if isinstance(osc, dict):
        if osc.get("overlays"):
            osc["overlays"] = clip(osc["overlays"])
        if isinstance(osc.get("by_symbol"), dict):
            osc["by_symbol"] = {k: clip(v) for k, v in osc["by_symbol"].items()}


def _apply_live(out: dict, legs: List[dict], info: Dict[str, dict], now: int, errs: List[str]) -> None:
    forming_ts = now - now % 60
    _strip_future(out, forming_ts)
    out["spot"]["overlays"] = _split_provisional(out["spot"].get("overlays"), forming_ts, out["spot"].get("tf") or 1)
    pov = out["premium"].get("overlays") or {}
    out["premium"]["overlays"] = {k: _split_provisional(v, forming_ts, out["premium"].get("tf") or 1) for k, v in pov.items()}
    osc = out.get("osc")
    if isinstance(osc, dict):
        if osc.get("overlays"):
            osc["overlays"] = _split_provisional(osc["overlays"], forming_ts, osc.get("tf") or 1)
        if isinstance(osc.get("by_symbol"), dict):
            osc["by_symbol"] = {k: _split_provisional(v, forming_ts, osc.get("tf") or 1) for k, v in osc["by_symbol"].items()}
    sp = info.get(SPOT_IDENT) or {}
    if sp.get("ltp") is not None:
        # stable legend label; the moving price goes on the axis tag
        out["spot"]["overlays"].append({"type": "level", "label": "live price", "tag": f"live {sp['ltp']:.2f}",
                                        "value": round(sp["ltp"], 2), "color": "primary", "dash": False, "kind": "live"})
    lv_legs = []
    for t in legs:
        if t["exit_ts"] is not None:
            continue
        li = info.get(t["tradingsymbol"]) or {}
        lv_legs.append(_live_leg(t, li.get("ltp"), li.get("age_s")))
        if li.get("ltp") is not None:
            out["premium"]["levels"].setdefault(t["tradingsymbol"], []).append(
                {"type": "level", "label": "LTP", "tag": f"LTP {li['ltp']:.2f}", "value": round(li["ltp"], 2),
                 "color": "primary", "dash": False, "kind": "live"})
    ages = [v.get("age_s") for v in info.values() if v.get("age_s") is not None]
    missing = [t["tradingsymbol"] for t in legs if t["exit_ts"] is None and (info.get(t["tradingsymbol"]) or {}).get("ltp") is None]
    out["live"] = {"active": True, "fence": STREAM_FENCE, "now_ts": now, "forming_ts": forming_ts, "poll_ms": LIVE_POLL_MS,
                   "spot_ltp": sp.get("ltp"), "spot_age_s": sp.get("age_s"), "legs": lv_legs,
                   "max_age_s": (max(ages) if ages else None),
                   "stale": (not ages) or max(ages) > STALE_S or bool(missing), "no_feed": missing}
    if missing:
        out["notes"].insert(0, f"no live price for {', '.join(missing)} — its candle updates when Kite serves the minute")
    out["notes"] = errs + out["notes"]


STREAM_FENCE = "TRADE_REPLAY_STREAM_20261017"


# ─────────────────────────────────────────────────────────────────────
# ── TRADE_REPLAY_STREAM_20261017 ── Dashboard lookup: which page row is this?
# ─────────────────────────────────────────────────────────────────────
def find_keys(strategy: Optional[str] = None, symbol: Optional[str] = None, entry_ts: Optional[int] = None,
              book: Optional[str] = None) -> dict:
    """Composite keys "t|<Trades key>" / "a|<Analytics key>" for a strategy's
    trades (newest open group first), or the row matching symbol+entry time.
    Rows on both pages (paper_trades LIVE rows) are listed once, as Trades."""
    ents, seen = [], set()
    for scope, pre in (("trades", "t"), ("analytics", "a")):
        for r in load_rows(scope):
            sym = str(r.get("tradingsymbol") or r.get("symbol") or "").replace(" ", "").upper()
            et = _num(r.get("entry_time"))
            if et is None:
                continue
            sid = _sid_of(r, scope)
            dk = (sid, sym, int(et))
            if dk in seen:
                continue
            seen.add(dk)
            bk = (str(r.get("trade_mode") or "PAPER").upper() if scope == "trades" else "LIVE")
            ents.append({"k": f"{pre}|{row_key(r, scope)}", "sid": sid, "symbol": sym, "entry_ts": int(et),
                         "open": _num(r.get("exit_time")) is None or str(r.get("state") or "").upper() == "OPEN",
                         "book": bk})
    sid = canon_sid(strategy) if strategy else None
    bk = str(book).upper() if book else None
    pool = [e for e in ents if (sid is None or e["sid"] == sid) and (bk is None or e["book"] == bk)]
    target = None
    if entry_ts is not None:
        sym = str(symbol or "").replace(" ", "").upper()
        near = [e for e in pool if abs(e["entry_ts"] - int(entry_ts)) <= 90]
        near.sort(key=lambda e: (0 if (sym and e["symbol"] == sym) else 1, abs(e["entry_ts"] - int(entry_ts))))
        target = near[0] if near else None
    elif sid is not None:
        # ── TRADE_REPLAY_WATCH_20261021 ── a strategy: its open group, else WATCH
        opens = [e for e in pool if e["open"]]
        if opens:
            target = max(opens, key=lambda e: (e["entry_ts"], e["k"]))
        else:
            nav = sorted((e for e in ents if e["sid"] == sid), key=lambda e: (e["entry_ts"], e["k"]))
            wk = f"w|{sid}"
            return {"ok": True, "key": wk, "strategy_id": sid, "book": bk, "watch": True,
                    "nav": [e["k"] for e in nav][-499:] + [wk]}
    elif pool:
        opens = [e for e in pool if e["open"]]
        target = max(opens or pool, key=lambda e: (e["entry_ts"], e["k"]))
    if target is None and bk is not None:
        # the Dashboard's book label and a row's trade_mode can disagree (a
        # LIVE row kept in paper_trades): retry without the book
        return find_keys(strategy=strategy, symbol=symbol, entry_ts=entry_ts, book=None)
    if target is None:
        return {"ok": False, "error": "no matching trade", "status": 404}
    nav = sorted((e for e in ents if e["sid"] == target["sid"] and e["book"] == target["book"]),
                 key=lambda e: (e["entry_ts"], e["k"]))
    keys = [e["k"] for e in nav][-499:]
    if entry_ts is None and sid is not None:
        keys.append(f"w|{sid}")                              # ── TRADE_REPLAY_WATCH_20261021 ── Next → the live chart
    return {"ok": True, "key": target["k"], "strategy_id": target["sid"], "book": target["book"], "nav": keys}


# ─────────────────────────────────────────────────────────────────────
# ── TRADE_REPLAY_WATCH_20261021 ── watch mode: the strategy's chart, no trade
# ─────────────────────────────────────────────────────────────────────
WATCH_FOCUS_MIN = 150           # default frame: the last 2½ hours


def build_watch(strategy: str, *, fill: bool = True, live: bool = False) -> dict:
    sid = canon_sid(strategy)
    if not sid:
        return {"ok": False, "error": "strategy required", "status": 400}
    cfg, cfg_note = live_cfg(sid)
    underlying = str(cfg.get("underlying") or "NIFTY").upper()
    if underlying not in RC.INDEX_UNDERLYINGS:
        underlying = "NIFTY"
    cfg["underlying"] = underlying
    now, today = _now_ts(), _today()
    in_session = live_session(now)
    try:
        from app.utils.market_hours import is_trading_day
        today_ok = bool(is_trading_day(today))
    except Exception:
        today_ok = today.weekday() < 5
    if today_ok and RC.mod_of(now) >= RC.SESSION_OPEN_MIN:
        D = today
    else:
        prev = _sessions(today - timedelta(days=12), today - timedelta(days=1))
        D = prev[-1] if prev else today
    notes: List[str] = []
    if fill:
        ghost_fill = {"tradingsymbol": "", "ghost": True, "token": None,
                      "entry_ts": RC.day_start(D) + RC.SESSION_OPEN_MIN * 60,
                      "exit_ts": None if D == today else RC.day_start(D) + (RC.SESSION_CLOSE_MIN - 1) * 60}
        notes += ensure_candles([ghost_fill], underlying)
    path = RC.runs_db_path()
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            sp = c.execute("SELECT tradingsymbol FROM backtest_candles_1m WHERE underlying=? AND instrument_type='SPOT' "
                           "LIMIT 1", (underlying,)).fetchone()
            days = [date.fromisoformat(r[0]) for r in c.execute(
                "SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d FROM backtest_candles_1m "
                "WHERE underlying=? AND expiry='' AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY d",
                (underlying, RC.day_start(D) - 20 * 86400, RC.day_start(D) + 86400)).fetchall() if r[0]]
        finally:
            c.close()
    except Exception:
        sp, days = None, []
    spot_sym = sp[0] if sp else None
    live_now = bool(live and in_session and D == today)
    if not spot_sym or (not days and not live_now):
        return {"ok": False, "error": f"no {underlying} spot bars in the backtest corpus to chart", "status": 404}
    if live_now and D not in days:
        days = days + [D]
    D_eff = D if D in days else days[-1]
    prev_days = [d for d in days if d < D_eff]
    first = prev_days[-1] if prev_days else D_eff
    end_ts = now if live_now else RC.day_start(D_eff) + (RC.SESSION_CLOSE_MIN - 1) * 60
    ghost = {"id": 1, "key": "watch", "ghost": True, "tradingsymbol": spot_sym, "instrument_type": "SPOT",
             "strike": 0.0, "expiry": "", "underlying": underlying, "token": None, "direction": "LONG", "qty": 0,
             "entry_ts": RC.day_start(first) + RC.SESSION_OPEN_MIN * 60, "entry_price": 0.0,
             "exit_ts": None, "exit_price": None, "exit_reason": None, "sl": None, "tp": None,
             "pnl": None, "charges": None, "net_pnl": None, "condition": None, "synthetic": 0}
    run = {"run_id": "watch", "strategy_id": sid, "underlying": underlying, "config": cfg,
           "date_from": None, "date_to": None, "trades": [ghost], "groups": [[ghost]], "window_until_ts": end_ts}
    info: Dict[str, dict] = {}
    errs: List[str] = []
    if live_now:
        _TRACKER.touch([SPOT_IDENT])
        f_rows, info = _forming(path, {SPOT_IDENT: {}}, now)
        run["corpus_hook"] = _hook_for(f_rows, errs)
    out = RC.build_replay_for(run, 1)
    if not out.get("ok"):
        return out
    # strip the ghost: no legs, markers, levels or lower pane
    out["legs_bars"] = {}
    prem = out.get("premium") or {}
    out["premium"] = {"tf": prem.get("tf") or 1, "overlays": {}, "levels": {}, "ha": {}}
    out["markers"] = []
    out["views"], out["default_view"] = [], None
    out["mtm"] = None
    if isinstance(out.get("osc"), dict) and not out["osc"].get("overlays"):
        out["osc"] = None
    out["spot"]["overlays"] = [o for o in out["spot"].get("overlays") or [] if o.get("type") != "level"]
    focus = max(RC.day_start(D_eff) + RC.SESSION_OPEN_MIN * 60, end_ts - WATCH_FOCUS_MIN * 60)
    out["group"] = {"ids": [sid], "legs": [], "watch": True, "primary_id": None, "open_legs": 0,
                    "entry_ts": focus, "exit_ts": None if live_now else end_ts, "net": 0.0, "gross": 0.0,
                    "dte": None, "exit_reasons": []}
    out["scope"], out["source"], out["trade_id"] = "watch", "live", f"w|{sid}"
    out["fence_watch"] = "TRADE_REPLAY_WATCH_20261021"
    out["watch"] = {"strategy_id": sid, "day": D_eff.isoformat(), "in_session": in_session,
                    "stale_day": D_eff != D}
    out["nav"] = {"index": 0, "count": 1, "prev_trade_id": None, "next_trade_id": None}
    keep = [n for n in (out.get("notes") or []) if spot_sym not in n and "leg" not in n.lower()]
    extra = ["no open position — watching the strategy's indicators for the next entry"]
    if D_eff != D:
        extra.append(f"no bars for {D.isoformat()} yet — showing {D_eff.isoformat()}")
    if cfg_note:
        extra.append(f"indicators: {cfg_note}")
    out["notes"] = notes + extra + keep
    par = dict(out.get("parity") or {})
    if par.get("tier") not in (None, "none", "levels"):
        par["detail"] = (f"{par.get('detail') or ''} · drawn on today's Settings config").strip(" ·")
    out["parity"] = par
    if not out["spot"]["overlays"] and not out.get("osc"):
        out["notes"].insert(1, f"{sid} decides on the option premium or the clock, not on spot indicators — "
                               "the watch chart shows spot only")
    if live_now:
        _apply_live(out, [], info, now, errs)
    else:
        out["live"] = {"active": False, "open": False, "session": in_session, "watch": True}
    return out


# ─────────────────────────────────────────────────────────────────────
# entry point
# ─────────────────────────────────────────────────────────────────────
def build_live_replay(scope: str, key: str, *, fill: bool = True, live: bool = False) -> dict:
    if scope not in SCOPES:
        return {"ok": False, "error": f"unknown scope {scope!r}", "status": 400}
    rows = load_rows(scope)
    meta = _Meta(RC.runs_db_path())
    sid, legs = find_group(rows, scope, str(key), meta)
    if sid is None:
        return {"ok": False, "error": "trade not found on this page", "status": 404}
    if not legs:
        return {"ok": False, "error": "trade has no entry price/time to replay", "status": 404}
    cfg, cfg_note = live_cfg(sid)
    underlying = str(cfg.get("underlying") or legs[0].get("underlying") or "NIFTY").upper()
    if underlying not in RC.INDEX_UNDERLYINGS:
        underlying = "NIFTY"
    cfg["underlying"] = underlying

    notes: List[str] = []
    if fill:
        try:
            from app.backtest.replay import overlays as OV
            prev_n = int(getattr(OV, "CONTEXT_PREV_SESSIONS", {}).get(sid, 0) or 0)
        except Exception:
            prev_n = 0
        notes += ensure_candles(legs, underlying, prev_sessions=prev_n)

    open_legs = [t for t in legs if t["exit_ts"] is None]
    run = {"run_id": f"live:{scope}", "strategy_id": sid, "underlying": underlying, "config": cfg,
           "date_from": None, "date_to": None, "trades": legs, "groups": [legs],
           "window_until_ts": (_now_ts() if open_legs else None)}
    # ── TRADE_REPLAY_STREAM_20261017 ── live: forming bars into a TEMP view
    now = _now_ts()
    is_live = bool(live and open_legs and live_session(now))
    info: Dict[str, dict] = {}
    errs: List[str] = []
    if is_live:
        idents = {t["tradingsymbol"]: t for t in open_legs}
        if underlying == "NIFTY":
            idents = {SPOT_IDENT: {}, **idents}
        _TRACKER.touch(list(idents))
        f_rows, info = _forming(RC.runs_db_path(), idents, now)
        run["corpus_hook"] = _hook_for(f_rows, errs)
    out = RC.build_replay_for(run, legs[0]["id"])
    if not out.get("ok"):
        return out
    by_id = {t["id"]: t["key"] for t in legs}
    out["fence_live"] = FENCE
    out["scope"] = scope
    out["source"] = "live"
    out["trade_id"] = str(key)
    out["group"]["ids"] = [by_id[i] for i in out["group"]["ids"]]
    out["group"]["open_legs"] = len(open_legs)
    for lv in out["group"]["legs"]:
        lv["key"] = by_id.get(lv["id"])
    out["nav"] = {"index": 0, "count": 1, "prev_trade_id": None, "next_trade_id": None}
    par = dict(out.get("parity") or {})
    if par.get("tier") not in (None, "none", "levels"):
        par["detail"] = (f"{par.get('detail') or ''} · drawn on today's Settings config, "
                         "not necessarily the one in force at the trade").strip(" ·")
    out["parity"] = par
    extra = []
    if cfg_note:
        extra.append(f"indicators: {cfg_note}")
    elif par.get("tier") not in (None, "none", "levels"):
        extra.append("indicators use the strategy's current Settings — if you changed them since this trade, "
                     "the lines may differ from what the engine saw")
    if open_legs:
        extra.append(f"{len(open_legs)} leg{'s' if len(open_legs) > 1 else ''} still open — net shows closed legs "
                     "only; the chart runs to the latest bar")
    out["notes"] = notes + extra + list(out.get("notes") or [])
    if is_live:
        _apply_live(out, legs, info, now, errs)
    else:
        out["live"] = {"active": False, "open": bool(open_legs),
                       "session": live_session(now) if open_legs else None}
    return out
