# backend/app/risk/live_sizer.py
#
# ── LIVE_SIZER_20261024 ── portfolio-level compounding for the LIVE account,
# SUGGEST-ONLY. The live twin of Backtest → Portfolio "Shared equity"
# (PF_SHARED_EQUITY_20261024 + FIX1): the SAME rule, on the real account.
#
#   equity(d) = account start
#               + NET realised of the member strategies' LIVE legs that
#                 closed on/after the start date and BEFORE day d
#               + deposits − withdrawals − tax paid (adjustments dated < d)
#   s(d)      = equity(d) ÷ sizing capital            (capped by Max total lots)
#   lots_i(d) = max(floor ? start_i : 1, ⌊start_i × s(d)⌋), capped by max_i
#
# "Sizing capital" is the starting capital the plan was backtested with
# (₹12L); the account can start lower (₹10L) — lots then stay at the start
# lots until the account has grown past the first threshold (₹16L for a
# 3-lot strategy). No DD guard: rejected by the backtest (24 Oct runs).
#
# Suggest-only by construction: this module NEVER writes a strategy config
# and places no order. It reads each member's config for the CURRENT lots,
# reads LIVE legs through the dashboard's own reader
# (fleet_today_routes._legs_for — parity with the Trades page), and records:
#   live_sizer_config     one row: the plan (JSON)
#   live_sizer_ledger     every counted LIVE leg (idempotent upsert) — the
#                         readers only return the newest ROW_LIMIT legs, so
#                         the ledger is what makes "realised since start"
#                         exact over months (ingest runs twice a day)
#   live_sizer_adjust     deposits / withdrawals / tax paid
#   live_sizer_snapshots  one row per day: the morning suggestion
# All tables live in app.db and are created lazily (margin_log precedent).
# Never raises out of compute(); problems come back in `warnings`.

from __future__ import annotations

import json
import math
import sqlite3
import time
from typing import Callable, Dict, List, Optional

FENCE = "LIVE_SIZER_20261024"
IST_OFF = 5 * 3600 + 30 * 60
SUPPORTED = ["IC_V2", "TMA_V2", "VET_V1", "IC_V1", "TSG_V1", "TMA_V1"]
DEFAULT_CONFIG = {
    "enabled": True,
    "start_date": "",                 # go-live day (YYYY-MM-DD); legs closed before it never count
    "account_start": 1000000,         # ₹ in the account on the start date
    "sizing_capital": 1200000,        # ₹ the plan was backtested with
    "floor_start": True,
    "max_total": 0,                   # 0 = no account cap
    "members": {
        "IC_V2": {"start": 3, "max": 15},
        "TMA_V2": {"start": 3, "max": 15},
        "VET_V1": {"start": 2, "max": 10},
    },
}
KINDS = {"DEPOSIT": 1, "WITHDRAW": -1, "TAX": -1}

_DDL = """
CREATE TABLE IF NOT EXISTS live_sizer_config (id INTEGER PRIMARY KEY CHECK (id = 1), json TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS live_sizer_ledger (
    leg_key TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, exit_ts INTEGER NOT NULL,
    gross REAL NOT NULL, charges REAL NOT NULL, net REAL NOT NULL, approx INTEGER NOT NULL DEFAULT 0,
    symbol TEXT, counted_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_live_sizer_ledger_sid ON live_sizer_ledger(strategy_id, exit_ts);
CREATE TABLE IF NOT EXISTS live_sizer_adjust (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, kind TEXT NOT NULL,
    amount REAL NOT NULL, note TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS live_sizer_snapshots (day TEXT PRIMARY KEY, ts INTEGER NOT NULL, json TEXT NOT NULL);
"""


# ───────────────────────────── seams (tests patch these) ─────────────────────
def _db_path() -> str:
    from app.db.sqlite import DB_PATH
    return str(DB_PATH)


def _legs_reader(sid: str, warns: list) -> List[dict]:
    from app.api.fleet_today_routes import _legs_for
    return _legs_for(sid, warns)


def _config_reader(sid: str) -> dict:
    from app.config.strategy_loader import load_strategy_config
    return load_strategy_config(sid) or {}


def _now() -> int:
    return int(time.time())


# ───────────────────────────── clock helpers ────────────────────────────────
def day_of(ts: int) -> str:
    g = time.gmtime(int(ts) + IST_OFF)
    return f"{g.tm_year:04d}-{g.tm_mon:02d}-{g.tm_mday:02d}"


def day_start(day: str) -> int:
    y, m, d = (int(x) for x in day.split("-"))
    import calendar
    return calendar.timegm((y, m, d, 0, 0, 0)) - IST_OFF


def _valid_day(s) -> bool:
    try:
        y, m, d = str(s).split("-")
        return len(y) == 4 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31
    except Exception:
        return False


# ───────────────────────────── the rule (pure) ──────────────────────────────
def suggest_lots(equity: float, cfg: dict) -> Dict[str, int]:
    """Mirror of portfolioSizing.applySharedEquity's daily rule (no guard)."""
    cap = float(cfg.get("sizing_capital") or 0)
    mem = cfg.get("members") or {}
    W = sum(int(m.get("start") or 0) for m in mem.values())
    s = max(0.0, float(equity) / cap) if cap > 0 else 0.0
    mt = int(cfg.get("max_total") or 0)
    if mt > 0 and W > 0:
        s = min(s, mt / W)
    out = {}
    for sid, m in mem.items():
        w = int(m.get("start") or 0)
        n = max(w if cfg.get("floor_start") else 1, int(math.floor(w * s + 1e-9)))
        mx = int(m.get("max") or 0)
        if mx > 0:
            n = min(n, mx)
        out[sid] = n
    return out


def next_threshold(sid: str, cfg: dict, current: int) -> Optional[float]:
    """Account equity at which `sid` would trade current+1 lots (None at its cap)."""
    m = (cfg.get("members") or {}).get(sid) or {}
    w, mx = int(m.get("start") or 0), int(m.get("max") or 0)
    cap = float(cfg.get("sizing_capital") or 0)
    if w <= 0 or cap <= 0 or (mx and current >= mx):
        return None
    mt = int(cfg.get("max_total") or 0)
    W = sum(int(x.get("start") or 0) for x in (cfg.get("members") or {}).values())
    if mt > 0 and W > 0 and (current + 1) > math.floor(w * mt / W + 1e-9):
        return None
    return math.ceil((current + 1) * cap / w)


def lots_of(cfg: dict) -> Optional[int]:
    """Primary lots of a strategy config — same candidates as the frontend's
    baseLotsOf (largest leg; the ratio applies to every leg)."""
    if not isinstance(cfg, dict):
        return None
    c = [(cfg.get("quantity") or {}).get("lots"), cfg.get("lots"),
         ((cfg.get("s1") or {}).get("main") or {}).get("lots"), ((cfg.get("s1") or {}).get("hedge") or {}).get("lots"),
         ((cfg.get("c1") or {}).get("sell") or {}).get("lots"), ((cfg.get("c1") or {}).get("buy") or {}).get("lots"),
         ((cfg.get("v1") or {}).get("main") or {}).get("lots"), ((cfg.get("v1") or {}).get("hedge") or {}).get("lots")]
    for leg in cfg.get("legs") or []:
        if isinstance(leg, dict):
            c.append(leg.get("lots"))
    nums = []
    for v in c:
        try:
            f = float(v)
            if f > 0:
                nums.append(int(round(f)))
        except (TypeError, ValueError):
            pass
    return max(nums) if nums else None


def leg_preview(cfg: dict, current: int, target: int) -> List[dict]:
    """Every lot field of the config and what it would read at `target`
    (each scaled by target ÷ current, rounded, min 1 — the runners' rule)."""
    out: List[dict] = []
    if not current or current <= 0:
        return out

    def add(label, v):
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            return
        if n > 0:
            out.append({"field": label, "now": n, "to": max(1, int(round(n * target / current)))})

    q = cfg.get("quantity") or {}
    if "lots" in q:
        add("quantity.lots", q.get("lots"))
    if "lots" in cfg:
        add("lots", cfg.get("lots"))
    for blk in ("s1", "c1", "v1"):
        for k, sub in (cfg.get(blk) or {}).items():
            if isinstance(sub, dict) and "lots" in sub:
                add(f"{blk}.{k}.lots", sub.get("lots"))
    for leg in cfg.get("legs") or []:
        if isinstance(leg, dict) and "lots" in leg:
            add(f"legs.{leg.get('id', '?')}.lots", leg.get("lots"))
    for k, sub in (cfg.get("adjust") or {}).items():
        if isinstance(sub, dict) and "lots" in sub:
            add(f"adjust.{k}.lots", sub.get("lots"))
    return out


# ───────────────────────────── store ────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return c


def validate_config(raw: dict) -> dict:
    """Normalised copy, or ValueError with the exact reason."""
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k in ("enabled", "floor_start"):
        if k in raw:
            cfg[k] = bool(raw[k])
    if "start_date" in raw:
        sd = str(raw.get("start_date") or "").strip()
        if sd and not _valid_day(sd):
            raise ValueError("start date must be YYYY-MM-DD")
        cfg["start_date"] = sd
    for k in ("account_start", "sizing_capital"):
        if k in raw:
            try:
                v = float(raw[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if not v > 0:
                raise ValueError(f"{k} must be above ₹0")
            cfg[k] = v
    if "max_total" in raw:
        try:
            cfg["max_total"] = max(0, int(float(raw.get("max_total") or 0)))
        except (TypeError, ValueError):
            raise ValueError("max total lots must be a whole number")
    if "members" in raw:
        mem = {}
        for sid, m in (raw.get("members") or {}).items():
            sid = str(sid).upper()
            if sid not in SUPPORTED:
                raise ValueError(f"{sid} is not supported by the sizer")
            try:
                st = int(float((m or {}).get("start") or 0))
                mx = int(float((m or {}).get("max") or 0))
            except (TypeError, ValueError):
                raise ValueError(f"{sid}: start and max lots must be whole numbers")
            if st <= 0:
                raise ValueError(f"{sid}: start lots must be at least 1")
            if mx and mx < st:
                raise ValueError(f"{sid}: max lots ({mx}) is below its start lots ({st})")
            mem[sid] = {"start": st, "max": mx}
        if not mem:
            raise ValueError("pick at least one strategy")
        cfg["members"] = mem
    return cfg


def load_config() -> dict:
    try:
        with _conn() as c:
            r = c.execute("SELECT json FROM live_sizer_config WHERE id=1").fetchone()
        if r:
            return validate_config(json.loads(r["json"]))
    except Exception:
        pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(raw: dict) -> dict:
    cfg = validate_config(raw)
    with _conn() as c:
        c.execute("INSERT INTO live_sizer_config(id, json, updated_at) VALUES (1, ?, ?) "
                  "ON CONFLICT(id) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
                  (json.dumps(cfg), _now()))
    return cfg


def add_adjustment(day: str, kind: str, amount, note: str = "") -> dict:
    kind = str(kind or "").upper()
    if kind not in KINDS:
        raise ValueError("kind must be DEPOSIT, WITHDRAW or TAX")
    if not _valid_day(day):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        amt = abs(float(amount))
    except (TypeError, ValueError):
        raise ValueError("amount must be a number")
    if not amt > 0:
        raise ValueError("amount must be above ₹0")
    with _conn() as c:
        cur = c.execute("INSERT INTO live_sizer_adjust(day, kind, amount, note, created_at) VALUES (?,?,?,?,?)",
                        (day, kind, amt, str(note or "")[:200], _now()))
        return {"id": cur.lastrowid, "day": day, "kind": kind, "amount": amt, "note": str(note or "")[:200]}


def delete_adjustment(adj_id: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM live_sizer_adjust WHERE id=?", (int(adj_id),)).rowcount > 0


def list_adjustments() -> List[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM live_sizer_adjust ORDER BY day, id")]


# ───────────────────────────── ingest ───────────────────────────────────────
def ingest(cfg: Optional[dict] = None, warns: Optional[list] = None) -> int:
    """Upsert every CLOSED LIVE leg of the members exited on/after the start
    date into the ledger. Idempotent. Returns the number of legs seen."""
    cfg = cfg or load_config()
    warns = warns if warns is not None else []
    if not cfg.get("start_date"):
        return 0
    t0 = day_start(cfg["start_date"])
    rows = []
    for sid in cfg.get("members") or {}:
        try:
            legs = _legs_reader(sid, warns) or []
        except Exception as e:
            warns.append(f"{sid}: legs read failed ({e!r})")
            continue
        for l in legs:
            if l.get("book") != "LIVE" or l.get("open") or not l.get("exit_ts"):
                continue
            x = int(l["exit_ts"])
            if x < t0:
                continue
            g, ch = float(l.get("gross") or 0), float(l.get("charges") or 0)
            key = f"{sid}|{l.get('id')}|{int(l.get('entry_ts') or 0)}|{l.get('symbol') or ''}|{x}"
            rows.append((key, sid, x, g, ch, g - ch, 1 if l.get("approx") else 0, l.get("symbol") or "", _now()))
    if rows:
        with _conn() as c:
            c.executemany(
                "INSERT INTO live_sizer_ledger(leg_key, strategy_id, exit_ts, gross, charges, net, approx, symbol, counted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(leg_key) DO UPDATE SET gross=excluded.gross, "
                "charges=excluded.charges, net=excluded.net, approx=excluded.approx", rows)
    return len(rows)


# ───────────────────────────── compute ──────────────────────────────────────
def compute(now: Optional[int] = None, do_ingest: bool = True) -> dict:
    """Today's suggestion with the full equity breakdown. Never raises."""
    now = int(now or _now())
    warns: List[str] = []
    cfg = load_config()
    today = day_of(now)
    out = {"ok": True, "fence": FENCE, "today": today, "config": cfg, "warnings": warns,
           "configured": bool(cfg.get("start_date"))}
    try:
        if do_ingest:
            ingest(cfg, warns)
        t_today = day_start(today)
        t0 = day_start(cfg["start_date"]) if cfg.get("start_date") else None
        per, today_net, approx = {}, {}, 0
        with _conn() as c:
            for sid in cfg["members"]:
                if t0 is None:
                    per[sid], today_net[sid] = 0.0, 0.0
                    continue
                r = c.execute("SELECT COALESCE(SUM(net),0) s, COALESCE(SUM(approx),0) a, COUNT(*) n FROM live_sizer_ledger "
                              "WHERE strategy_id=? AND exit_ts>=? AND exit_ts<?", (sid, t0, t_today)).fetchone()
                per[sid] = float(r["s"])
                approx += int(r["a"])
                r2 = c.execute("SELECT COALESCE(SUM(net),0) s FROM live_sizer_ledger WHERE strategy_id=? AND exit_ts>=?",
                               (sid, t_today)).fetchone()
                today_net[sid] = float(r2["s"])
            adj = [dict(r) for r in c.execute("SELECT * FROM live_sizer_adjust ORDER BY day, id")]
        adj_before = sum(KINDS[a["kind"]] * float(a["amount"]) for a in adj if a["day"] < today)
        realised = sum(per.values())
        equity = float(cfg["account_start"]) + realised + adj_before
        sug = suggest_lots(equity, cfg)
        rows = []
        for sid, m in cfg["members"].items():
            try:
                scfg = _config_reader(sid)
            except Exception as e:
                scfg = {}
                warns.append(f"{sid}: config read failed ({e!r})")
            cur = lots_of(scfg)
            mode = str(scfg.get("trade_execution_mode") or "—").upper()
            tgt = sug[sid]
            rows.append({
                "strategy_id": sid, "mode": mode, "start": m["start"], "max": m["max"] or None,
                "current": cur, "suggested": tgt, "change": (cur is not None and cur != tgt),
                "realised_since_start": round(per[sid], 2), "realised_today": round(today_net[sid], 2),
                "next_at": next_threshold(sid, cfg, tgt), "preview": leg_preview(scfg, cur or 0, tgt) if cur else [],
            })
            if mode not in ("LIVE", "PAPER_LIVE"):
                warns.append(f"{sid} is in {mode} mode — its suggestion is informational until it trades LIVE")
        if approx:
            warns.append(f"{approx} counted leg(s) had no exit price or charges model — their net may be approximate")
        if not cfg.get("start_date"):
            warns.append("Set the start date (your go-live day) — no realised P&L is counted until then")
        out.update({
            "account_start": float(cfg["account_start"]), "realised": round(realised, 2),
            "adjustments_total": round(adj_before, 2), "equity": round(equity, 2),
            "ratio": round(equity / float(cfg["sizing_capital"]), 4),
            "capital_per_lot": round(float(cfg["sizing_capital"]) / max(1, sum(m["start"] for m in cfg["members"].values())), 2),
            "rows": rows, "adjustments": adj, "any_change": any(r["change"] for r in rows),
            "realised_today_total": round(sum(today_net.values()), 2),
        })
    except Exception as e:
        out["ok"] = False
        warns.append(f"compute failed: {e!r}")
    return out


def snapshot(now: Optional[int] = None) -> dict:
    """compute() and store it as today's row (re-runs overwrite the same day)."""
    res = compute(now)
    try:
        with _conn() as c:
            c.execute("INSERT INTO live_sizer_snapshots(day, ts, json) VALUES (?,?,?) "
                      "ON CONFLICT(day) DO UPDATE SET ts=excluded.ts, json=excluded.json",
                      (res["today"], int(now or _now()), json.dumps(res)))
    except Exception as e:
        res.setdefault("warnings", []).append(f"snapshot not stored: {e!r}")
    return res


def history(limit: int = 15) -> List[dict]:
    try:
        with _conn() as c:
            out = []
            for r in c.execute("SELECT day, json FROM live_sizer_snapshots ORDER BY day DESC LIMIT ?", (int(limit),)):
                j = json.loads(r["json"])
                out.append({"day": r["day"], "equity": j.get("equity"),
                            "lots": {x["strategy_id"]: {"current": x["current"], "suggested": x["suggested"]}
                                     for x in j.get("rows") or []}})
            return out
    except Exception:
        return []


# ───────────────────────────── scheduler entry ──────────────────────────────
_ALERTED: Dict[str, bool] = {}


def live_sizer_job(kind: str = "morning") -> Optional[dict]:
    """09:05 (morning) and 15:50 (close) on trading days. Morning: snapshot +
    one bell alert per day when any member's suggestion differs from its
    configured lots. Close: ingest only (keeps the ledger ahead of the
    readers' row window). Never raises."""
    try:
        from app.utils.market_hours import is_trading_day
        if not is_trading_day():
            return None
    except Exception:
        pass
    try:
        cfg = load_config()
        if not cfg.get("enabled"):
            return None
        if kind != "morning":
            ingest(cfg, [])
            return None
        res = snapshot()
        if res.get("any_change") and not _ALERTED.get(res["today"]):
            _ALERTED[res["today"]] = True
            msg = "; ".join(f"{r['strategy_id']} {r['current']} → {r['suggested']} lots"
                            for r in res["rows"] if r["change"])
            try:
                from app.event_bus.inapp_events import record_alert
                record_alert("LIVE_SIZER", f"Live sizer suggests: {msg} (account ₹{res['equity']:,.0f}). "
                             f"Suggest-only — change lots in Settings if you agree.", severity="info")
            except Exception:
                pass
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[LIVE_SIZER] {res.get('today')} equity={res.get('equity')} "
                            + " ".join(f"{r['strategy_id']}:{r['current']}->{r['suggested']}" for r in res.get("rows") or []))
        except Exception:
            pass
        return res
    except Exception:
        return None
