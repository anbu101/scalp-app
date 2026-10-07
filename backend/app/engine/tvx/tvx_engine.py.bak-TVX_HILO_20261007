# backend/app/engine/tvx/tvx_engine.py
#
# ── TVX_V1_20261007 ── TradingView-alert paper strategy · engine
# ============================================================================
# One daemon thread, one loop, every poll_sec (default 2 s):
#   1. poll the relay  GET {relay_url}/alerts?since=<cursor>   (Bearer READ_TOKEN)
#      → each matching alert goes through tvx_core.decide()
#   2. if a position is open: ONE kite.quote() for that option →
#      excursion tracking, LTP ≤ SL → exit at BID, square-off time → exit
#
# Isolation (the reason this is its own module):
#   * own tables (tvx_*), never paper_trades — NSE square-off / MTM guard /
#     Trades page never see an MCX row
#   * REST quotes on the existing data session; NO new Kite WebSocket (the
#     key is already over Kite's soft socket limit)
#   * PAPER ONLY — this module has no order path at all
#
# Safety rules:
#   * an alert is processed at most once (row written BEFORE acting, cursor
#     persisted after) — a crash mid-alert can lose an action, never repeat it
#   * alerts older than stale_sec are recorded, never traded (app was asleep)
#   * an ENTER never follows a failed EXIT (no double position); the failed
#     exit is persisted as pending and retried every loop, and it blocks entries
#   * disabled with a position open = manage it to its normal exit, no entries
#   * a position from a previous day (app was closed at square-off) is closed
#     at the first quote of the next session, marked EOD_LATE
# ============================================================================

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from app.engine.tvx import tvx_core as core
    from app.engine.tvx.tvx_repo import TvxRepo
except ImportError:                                     # pragma: no cover
    import tvx_core as core                              # type: ignore
    from tvx_repo import TvxRepo                         # type: ignore

IST = core.IST
FENCE = "TVX_V1_20261007"


def _default_log(msg: str) -> None:
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(msg)
    except Exception:                                    # pragma: no cover
        print(msg)


# ───────────────────────── config store ─────────────────────────

class TvxConfigStore:
    def __init__(self, path=None):
        if path is None:
            from app.utils.app_paths import CONFIG_DIR, ensure_app_dirs
            ensure_app_dirs()
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            path = CONFIG_DIR / "tvx_v1.json"
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> Dict:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text())
            except Exception:
                raw = {}
        cfg, _ = core.normalize_config(raw)
        return cfg

    def save(self, patch: Dict) -> (Dict, List[str]):
        cur = self.load()
        merged = dict(cur)
        for k, v in (patch or {}).items():
            if k in core.DEFAULTS:
                if k == "read_token" and (v is None or str(v).strip() == ""):
                    continue                     # blank = keep the stored token
                merged[k] = v
        cfg, errs = core.normalize_config(merged)
        if errs:
            return cfg, errs
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, indent=2))
            tmp.replace(self.path)
        return cfg, []


# ───────────────────────── relay client ─────────────────────────

def _relay_get(url: str, token: str, timeout: float = 5.0) -> Dict:
    import requests
    r = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                   "User-Agent": "scalp-terminal-tvx/1"}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"relay HTTP {r.status_code}")
    return r.json()


class TvxError(Exception):
    pass


# ───────────────────────── engine ─────────────────────────

class TvxEngine:
    PAGE = 200

    def __init__(self, repo: TvxRepo, cfg_store: TvxConfigStore,
                 kite_provider: Callable[[], object],
                 http_get: Callable[[str, str], Dict] = _relay_get,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(IST),
                 log: Callable[[str], None] = _default_log,
                 sleep: Callable[[float], None] = time.sleep):
        self.repo, self.cfg_store = repo, cfg_store
        self.kite_provider, self.http_get = kite_provider, http_get
        self.clock, self.log, self.sleep = clock, log, sleep
        self._lock = threading.RLock()
        self._ins_cache: Dict = {"day": None, "name": None, "futs": [], "opts": []}
        self._manual_exit = False
        self.mark: Optional[Dict] = None
        self.status: Dict = {
            "running": False, "started_at": None, "last_loop_at": None,
            "last_poll_at": None, "last_poll_ok": None, "last_error": None,
            "last_error_at": None, "polls": 0, "quote_errors": 0, "fence": FENCE,
        }

    # ── small utils ──
    def _err(self, msg: str) -> None:
        self.status["last_error"] = msg
        self.status["last_error_at"] = int(self.clock().timestamp())
        self.log(f"[TVX][WARN] {msg}")

    def request_manual_exit(self) -> bool:
        if self.repo.open_position() is None:
            return False
        self._manual_exit = True
        return True

    def _quote(self, keys: List[str]) -> Dict:
        kite = self.kite_provider()
        if kite is None:
            raise TvxError("no Kite data session")
        last = None
        for attempt in range(3):
            try:
                return kite.quote(keys) or {}
            except Exception as e:                       # rate limit / network
                last = e
                self.sleep(1.0 + attempt)
        self.status["quote_errors"] += 1
        raise TvxError(f"quote failed: {last}")

    def _instruments(self, cfg: Dict, today: dt.date):
        c = self._ins_cache
        if c["day"] == today and c["name"] == cfg["underlying"] and c["opts"]:
            return c["futs"], c["opts"]
        kite = self.kite_provider()
        if kite is None:
            raise TvxError("no Kite data session")
        rows = [r for r in (kite.instruments("MCX") or []) if r.get("name") == cfg["underlying"]]

        def d(x):
            return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])

        futs = [dict(r, expiry=d(r["expiry"])) for r in rows if r.get("instrument_type") == "FUT"]
        opts = [dict(r, expiry=d(r["expiry"]), strike=float(r["strike"]))
                for r in rows if r.get("instrument_type") in ("CE", "PE")]
        if not futs or not opts:
            raise TvxError(f"no MCX futures/options for {cfg['underlying']}")
        self._ins_cache = {"day": today, "name": cfg["underlying"], "futs": futs, "opts": opts}
        self.log(f"[TVX] instruments loaded {cfg['underlying']}: {len(futs)} FUT, {len(opts)} OPT")
        return futs, opts

    # ── one loop iteration (the unit the tests drive) ──
    def step(self) -> None:
        with self._lock:
            cfg = self.cfg_store.load()
            now = self.clock()
            self.status["last_loop_at"] = int(now.timestamp())
            pos = self.repo.open_position()
            if self._should_poll(cfg, now, pos):
                self._poll_alerts(cfg, now)
            pos = self.repo.open_position()
            if pos:
                self._manage(pos, cfg, now)
            else:
                self.mark = None
                self._manual_exit = False
                if self.repo.get_pending_exit():
                    self.repo.set_pending_exit(None)

    def _should_poll(self, cfg: Dict, now: dt.datetime, pos) -> bool:
        if not cfg["relay_url"] or not cfg["read_token"]:
            return False
        if not cfg["enabled"] and pos is None:
            return False
        d = now.date()
        if not core.is_weekday(d):
            return pos is not None
        s = core.session(d, cfg)
        return (s["start"] - dt.timedelta(minutes=5)) <= now <= (s["close"] + dt.timedelta(minutes=10)) \
            or pos is not None

    # ── relay ──
    def _poll_alerts(self, cfg: Dict, now: dt.datetime) -> None:
        cursor = self.repo.get_cursor()
        first_run = cursor is None
        since = cursor or 0
        self.status["polls"] += 1
        self.status["last_poll_at"] = int(now.timestamp())
        try:
            for _ in range(20):
                data = self.http_get(f"{cfg['relay_url']}/alerts?since={since}&limit={self.PAGE}",
                                     cfg["read_token"])
                rows = sorted(data.get("alerts") or [], key=lambda a: int(a["id"]))
                for a in rows:
                    self._on_alert(a, cfg, now, first_run)
                    self.repo.set_cursor(int(a["id"]))
                    since = int(a["id"])
                if len(rows) < self.PAGE:
                    break
            if first_run and self.repo.get_cursor() is None:
                self.repo.set_cursor(0)
            self.status["last_poll_ok"] = True
        except Exception as e:
            self.status["last_poll_ok"] = False
            self._err(f"relay poll failed: {e}")

    def _on_alert(self, a: Dict, cfg: Dict, now: dt.datetime, first_run: bool) -> None:
        aid = int(a["id"])
        if self.repo.alert_seen(aid) or not core.alert_matches(a, cfg):
            return
        ts = int(now.timestamp())
        age = core.alert_age_sec(a, now)
        if age is None or age > cfg["stale_sec"]:
            if not (first_run and (age is None or age > 3600)):   # don't bury the log in history
                self.repo.record_alert(a, ts, "STALE",
                                       "no receive time" if age is None else f"{age:.0f}s old")
            return
        self.repo.record_alert(a, ts, "PROCESSING")              # at-most-once
        pos = self.repo.open_position()
        pending = self.repo.get_pending_exit()
        block = core.entry_block(cfg, now, exit_pending=pending is not None)
        actions = core.decide(a.get("sig"), pos["side"] if pos else None, block)
        done, notes = [], []
        for kind, arg in actions:
            if kind == "EXIT":
                ok, note = self._exit(pos, arg, now, alert_id=aid)
                done.append("EXIT" if ok else "EXIT_FAILED")
                notes.append(note)
                if not ok:
                    self.repo.set_pending_exit(pos["id"], arg, aid)
                    notes.append("entry skipped: exit pending")
                    break
            elif kind == "ENTER":
                ok, note = self._enter(arg, cfg, now, a)
                done.append(f"ENTER_{arg}" if ok else "ENTER_FAILED")
                notes.append(note)
            else:
                done.append(kind)
                notes.append(arg)
        action = "+".join(done)
        self.repo.record_alert(a, ts, action, "; ".join(n for n in notes if n))
        self.log(f"[TVX][ALERT] id={aid} sig={a.get('sig')} {a.get('ticker')} {a.get('tf')} "
                 f"→ {action} | {'; '.join(n for n in notes if n)}")

    # ── entry ──
    def _enter(self, side: str, cfg: Dict, now: dt.datetime, a: Dict):
        try:
            futs, opts = self._instruments(cfg, now.date())
            exp = core.pick_option_expiry([o["expiry"] for o in opts], now.date())
            if exp is None:
                return False, "no option expiry after today"
            fut = core.pick_future(futs, exp)
            if fut is None:
                return False, f"no futures for option expiry {exp}"
            chain = {o["strike"]: o for o in opts if o["expiry"] == exp and o["instrument_type"] == side}
            strikes = sorted(chain)
            fkey = f"MCX:{fut['tradingsymbol']}"
            alert_px = a.get("price")
            cands = core.nearest_strikes(strikes, float(alert_px), 4) if alert_px else []
            q = self._quote([fkey] + [f"MCX:{chain[k]['tradingsymbol']}" for k in cands])
            fut_px = core.parse_quote(q.get(fkey)).get("ltp")
            if not fut_px:
                return False, f"no futures price for {fut['tradingsymbol']}"
            atm = core.atm_strike(strikes, fut_px)
            ins = chain[atm]
            okey = f"MCX:{ins['tradingsymbol']}"
            if okey not in q:
                q.update(self._quote([okey]))
            oq = core.parse_quote(q.get(okey))
            px, src = core.entry_fill(oq)
            if not px:
                return False, f"no price for {ins['tradingsymbol']}"
            qty = int(cfg["lots"]) * int(cfg["units_per_lot"])
            sl = round(max(px - float(cfg["sl_points"]), 0.1), 1)
            row = {
                "side": side, "symbol": ins["tradingsymbol"], "token": ins.get("instrument_token"),
                "strike": atm, "expiry": exp.isoformat(), "fut_symbol": fut["tradingsymbol"],
                "fut_px": fut_px, "alert_px": alert_px, "entry_alert_id": int(a["id"]),
                "entry_signal": a.get("sig"), "signal_bar": a.get("bar_time"),
                "entry_time": int(now.timestamp()), "entry_price": px, "entry_src": src,
                "entry_bid": oq.get("bid"), "entry_ask": oq.get("ask"), "entry_ltp": oq.get("ltp"),
                "sl_points": float(cfg["sl_points"]), "sl_price": sl, "lots": int(cfg["lots"]),
                "units_per_lot": int(cfg["units_per_lot"]), "qty": qty,
            }
            tid = self.repo.open_trade(row)
            self.mark = {"ltp": oq.get("ltp"), "bid": oq.get("bid"), "ask": oq.get("ask"),
                         "ts": int(now.timestamp())}
            note = (f"BUY {ins['tradingsymbol']} @ {px} ({src}) SL {sl} · fut {fut_px} · "
                    f"{cfg['lots']} lot × {cfg['units_per_lot']}")
            self.log(f"[TVX][ENTER] #{tid} {note}")
            return True, note
        except Exception as e:
            self._err(f"entry {side} failed: {e}")
            return False, f"entry failed: {e}"

    # ── exit ──
    def _exit(self, pos: Dict, reason: str, now: dt.datetime, alert_id: Optional[int] = None,
              quote: Optional[Dict] = None):
        try:
            oq = quote if quote is not None else core.parse_quote(
                self._quote([f"MCX:{pos['symbol']}"]).get(f"MCX:{pos['symbol']}"))
            px, src = core.exit_fill(oq)
            if not px:
                return False, f"no exit price for {pos['symbol']}"
            ch = core.mcx_option_charges(pos["entry_price"], px, pos["qty"])
            pts = round(px - pos["entry_price"], 2)
            gross = round(pts * pos["qty"], 2)
            net = round(gross - ch["total"], 2)
            ok = self.repo.close_trade(
                pos["id"], exit_time=int(now.timestamp()), exit_price=px, exit_src=src,
                exit_bid=oq.get("bid"), exit_ask=oq.get("ask"), exit_ltp=oq.get("ltp"),
                exit_reason=reason, exit_alert_id=alert_id, pnl_points=pts, gross=gross,
                charges=ch["total"], net=net, charges_detail=ch)
            if not ok:
                return False, "trade already closed"
            self.repo.set_pending_exit(None)
            self._manual_exit = False
            self.mark = None
            note = f"SELL {pos['symbol']} @ {px} ({src}) {reason} · {pts:+.1f} pts · net ₹{net:,.0f}"
            self.log(f"[TVX][EXIT] #{pos['id']} {note}")
            return True, note
        except Exception as e:
            self._err(f"exit {pos.get('symbol')} failed: {e}")
            return False, f"exit failed: {e}"

    # ── open-position management ──
    def _manage(self, pos: Dict, cfg: Dict, now: dt.datetime) -> None:
        key = f"MCX:{pos['symbol']}"
        try:
            oq = core.parse_quote(self._quote([key]).get(key))
        except Exception as e:
            self._err(f"mark {pos['symbol']}: {e}")
            return
        self.mark = dict(oq, ts=int(now.timestamp()))

        if oq.get("ltp"):
            exc = oq["ltp"] - pos["entry_price"]
            mfe = max(pos.get("mfe_points") or 0.0, exc)
            mae = min(pos.get("mae_points") or 0.0, exc)
            if mfe != (pos.get("mfe_points") or 0.0) or mae != (pos.get("mae_points") or 0.0):
                self.repo.update_excursion(pos["id"], round(mfe, 2), round(mae, 2))

        entry_day = dt.datetime.fromtimestamp(pos["entry_time"], IST).date()
        s = core.session(now.date(), cfg)
        pending = self.repo.get_pending_exit()

        reason = None
        if self._manual_exit:
            reason = "MANUAL"
        elif pending and pending.get("trade_id") == pos["id"]:
            reason = pending.get("reason") or "SIGNAL"
        elif entry_day < now.date():
            reason = "EOD_LATE" if core.is_weekday(now.date()) and now >= s["start"] else None
        elif now >= s["squareoff"]:
            reason = "EOD"
        elif core.sl_hit(oq, pos["sl_price"]):
            reason = "SL"
        if reason is None:
            return
        alert_id = pending.get("alert_id") if pending and reason == pending.get("reason") else None
        ok, note = self._exit(pos, reason, now, alert_id=alert_id, quote=oq)
        if not ok and reason in ("SL", "EOD", "EOD_LATE", "MANUAL"):
            self._err(f"{reason} exit not filled yet: {note}")

    # ── thread ──
    def run_forever(self, stop: Optional[threading.Event] = None) -> None:
        self.status["running"] = True
        self.status["started_at"] = int(self.clock().timestamp())
        self.log(f"[TVX] engine thread started ({FENCE})")
        while not (stop and stop.is_set()):
            try:
                self.step()
            except Exception as e:
                self._err(f"loop error: {e}")
            try:
                poll = float(self.cfg_store.load().get("poll_sec") or 2.0)
            except Exception:
                poll = 2.0
            self.sleep(poll)
        self.status["running"] = False


# ───────────────────────── singleton / launch ─────────────────────────

_ENGINE: Optional[TvxEngine] = None
_START_LOCK = threading.Lock()


def get_engine() -> Optional[TvxEngine]:
    return _ENGINE


def get_repo_and_store():
    """Routes need these even if the thread never started."""
    if _ENGINE is not None:
        return _ENGINE.repo, _ENGINE.cfg_store
    return TvxRepo(), TvxConfigStore()


def start_tvx(zerodha_manager) -> TvxEngine:
    global _ENGINE
    with _START_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        eng = TvxEngine(TvxRepo(), TvxConfigStore(), kite_provider=zerodha_manager.get_data_kite)
        threading.Thread(target=eng.run_forever, name="tvx_v1", daemon=True).start()
        _ENGINE = eng
        return eng
