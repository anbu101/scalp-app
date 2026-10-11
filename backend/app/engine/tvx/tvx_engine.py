# backend/app/engine/tvx/tvx_engine.py
#
# ── TVX_V1_20261007 ── TradingView-alert paper strategy · engine
# ── TVX_HILO_20261007 ── + per-trade high/low of the option and its futures
# ── TVX_MULTI_20261009 ── + instruments: Crude (MCX monthly, ATM from futures)
#    and Nifty (NFO weekly, ATM from the NSE spot); each its own settings,
#    position, alerts and results; one combined quote call per loop.
# ── TVX_OPTS_20261008 ── + No SL / No target (T1 and T2 recorded, exit on the
#    opposite signal), carry overnight (no square-off; not quoted outside MCX
#    hours; forced out on the option's expiry day), next-month roll N trading
#    days before expiry. Settings are stamped on each trade at entry.
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
HILO_FENCE = "TVX_HILO_20261007"   # trade range: option + futures high/low between entry and exit
RANGE_MAX_TRIES = 5
RANGE_RETRY_SEC = 60
RANGE_MIN_GAP_SEC = 5              # at most one candle job (2 historical calls) per 5 s
RANGE_SETTLE_SEC = 120             # wait after exit so Kite has published the last inside minute


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

    # ── TVX_MULTI_20261009 ── file: {"shared": {...}, "instruments": {"CRUDE": {...}, "NIFTY": {...}}}
    # A V1-era flat file is read as shared + CRUDE and rewritten on the next save.
    def _raw(self) -> Dict:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text())
            except Exception:
                raw = {}
        if "instruments" not in raw:                       # legacy flat file
            raw = {"shared": {k: raw[k] for k in core.SHARED_KEYS if k in raw},
                   "instruments": {"CRUDE": {k: raw[k] for k in core.INST_KEYS if k in raw}}}
        raw.setdefault("shared", {})
        raw.setdefault("instruments", {})
        return raw

    @staticmethod
    def _flat(raw: Dict, inst: str) -> Dict:
        return dict(raw["instruments"].get(inst, {}), **raw["shared"])

    def load(self, inst: str = "CRUDE") -> Dict:
        cfg, _ = core.normalize_config(self._flat(self._raw(), inst), inst)
        return cfg

    def load_all(self) -> Dict[str, Dict]:
        raw = self._raw()
        return {i: core.normalize_config(self._flat(raw, i), i)[0] for i in core.INSTRUMENTS}

    def save(self, patch: Dict, inst: str = "CRUDE") -> (Dict, List[str]):
        inst = inst if inst in core.PRESETS else "CRUDE"
        raw = self._raw()
        merged = self._flat(raw, inst)
        for k, v in (patch or {}).items():
            if k in core.DEFAULTS:
                if k == "read_token" and (v is None or str(v).strip() == ""):
                    continue                     # blank = keep the stored token
                merged[k] = v
        cfg, errs = core.normalize_config(merged, inst)
        if errs:
            return cfg, errs
        shared = {k: cfg[k] for k in core.SHARED_KEYS}
        for other in core.INSTRUMENTS:           # shared edits must not break another instrument
            if other != inst:
                _, e2 = core.normalize_config(dict(raw["instruments"].get(other, {}), **shared), other)
                if e2:
                    return cfg, [f"{core.PRESETS[other]['label']}: {x}" for x in e2]
        raw["shared"] = shared
        raw["instruments"][inst] = {k: cfg[k] for k in core.INST_KEYS}
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, indent=2))
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
        self._ins_cache: Dict = {}                   # (exchange, underlying) -> {day, futs, opts}
        self._manual: set = set()                    # instruments with a manual exit requested
        self.marks: Dict[str, Dict] = {}             # inst -> {"opt": quote, "ref": quote}
        self._range_next_at: Dict[int, int] = {}     # trade id -> earliest retry epoch
        self._range_last_run = 0
        self.status: Dict = {
            "running": False, "started_at": None, "last_loop_at": None,
            "last_poll_at": None, "last_poll_ok": None, "last_error": None,
            "last_error_at": None, "polls": 0, "quote_errors": 0, "fence": FENCE,
        }

    # ── marks (per instrument; .mark / .fut_mark = Crude, kept for V1 callers) ──
    def _set_mark(self, inst: str, opt: Optional[Dict], ref: Optional[Dict]) -> None:
        if opt is None and ref is None:
            self.marks.pop(inst, None)
        else:
            self.marks[inst] = {"opt": opt, "ref": ref}

    def mark_of(self, inst: str = "CRUDE") -> Optional[Dict]:
        return (self.marks.get(inst) or {}).get("opt")

    def ref_mark_of(self, inst: str = "CRUDE") -> Optional[Dict]:
        return (self.marks.get(inst) or {}).get("ref")

    @property
    def mark(self) -> Optional[Dict]:
        return self.mark_of("CRUDE")

    @property
    def fut_mark(self) -> Optional[Dict]:
        return self.ref_mark_of("CRUDE")

    # ── small utils ──
    def _err(self, msg: str) -> None:
        self.status["last_error"] = msg
        self.status["last_error_at"] = int(self.clock().timestamp())
        self.log(f"[TVX][WARN] {msg}")

    def request_manual_exit(self, inst: str = "CRUDE") -> bool:
        if self.repo.open_position(inst) is None:
            return False
        self._manual.add(inst)
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
        exch, name = cfg.get("opt_exchange", "MCX"), cfg["underlying"]
        key = (exch, name)
        c = self._ins_cache.get(key)
        if c and c["day"] == today and c["opts"]:
            return c["futs"], c["opts"]
        kite = self.kite_provider()
        if kite is None:
            raise TvxError("no Kite data session")
        rows = [r for r in (kite.instruments(exch) or []) if r.get("name") == name]

        def d(x):
            return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])

        futs = [dict(r, expiry=d(r["expiry"])) for r in rows if r.get("instrument_type") == "FUT"]
        opts = [dict(r, expiry=d(r["expiry"]), strike=float(r["strike"]))
                for r in rows if r.get("instrument_type") in ("CE", "PE")]
        if not opts or (cfg.get("ref") == "FUT" and not futs):
            raise TvxError(f"no {exch} options{'/futures' if cfg.get('ref') == 'FUT' else ''} for {name}")
        self._ins_cache[key] = {"day": today, "futs": futs, "opts": opts}
        self.log(f"[TVX] instruments loaded {exch}:{name}: {len(futs)} FUT, {len(opts)} OPT")
        return futs, opts

    # ── one loop iteration (the unit the tests drive) ──
    def step(self) -> None:
        with self._lock:
            cfgs = self.cfg_store.load_all()
            now = self.clock()
            self.status["last_loop_at"] = int(now.timestamp())
            if any(self._should_poll(cfgs[i], now, self.repo.open_position(i)) for i in core.INSTRUMENTS):
                self._poll_alerts(cfgs, now)
            positions = {i: self.repo.open_position(i) for i in core.INSTRUMENTS}
            # ONE quote call for every open leg + reference (Kite's quote budget is 1/s)
            want = {i: p for i, p in positions.items() if p and self._needs_quote(p, cfgs[i], now)}
            prefetched = None
            if want:
                keys = sorted({k for p in want.values() for k in self._keys(p) if k})
                try:
                    prefetched = self._quote(keys)
                except Exception as e:
                    self._err(f"mark {', '.join(want)}: {e}")
            for i, pos in positions.items():
                if pos:
                    if i in want and prefetched is not None:
                        self._manage(pos, cfgs[i], now, prefetched)
                    elif i not in want:
                        self._manage(pos, cfgs[i], now)       # returns early (carried, market shut)
                else:
                    self._set_mark(i, None, None)
                    self._manual.discard(i)
                    if self.repo.get_pending_exit(i):
                        self.repo.set_pending_exit(None, inst=i)
            self._range_job(cfgs, now)

    def _needs_quote(self, pos: Dict, cfg: Dict, now: dt.datetime) -> bool:
        if cfg.get("carry_overnight") and core.inst_of(pos) not in self._manual \
                and not core.in_session(cfg, now):
            return False
        return True

    def _should_poll(self, cfg: Dict, now: dt.datetime, pos) -> bool:
        if not cfg["relay_url"] or not cfg["read_token"]:
            return False
        if not cfg["enabled"] and pos is None:
            return False
        if cfg.get("carry_overnight"):                            # ── TVX_OPTS_20261008 ──
            return core.in_session(cfg, now, pad_min=10)          # no alerts can arrive overnight
        d = now.date()
        if not core.is_weekday(d):
            return pos is not None
        s = core.session(d, cfg)
        return (s["start"] - dt.timedelta(minutes=5)) <= now <= (s["close"] + dt.timedelta(minutes=10)) \
            or pos is not None

    # ── relay ──
    def _poll_alerts(self, cfgs: Dict, now: dt.datetime) -> None:
        if isinstance(cfgs, dict) and "relay_url" in cfgs:        # a single flat cfg (V1 callers)
            cfgs = {core.inst_of(cfgs): cfgs}
        shared = next(iter(cfgs.values()))
        cursor = self.repo.get_cursor()
        first_run = cursor is None
        since = cursor or 0
        self.status["polls"] += 1
        self.status["last_poll_at"] = int(now.timestamp())
        try:
            for _ in range(20):
                data = self.http_get(f"{shared['relay_url']}/alerts?since={since}&limit={self.PAGE}",
                                     shared["read_token"])
                rows = sorted(data.get("alerts") or [], key=lambda a: int(a["id"]))
                for a in rows:
                    self._on_alert(a, cfgs, now, first_run)
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

    def _on_alert(self, a: Dict, cfgs: Dict, now: dt.datetime, first_run: bool) -> None:
        aid = int(a["id"])
        if self.repo.alert_seen(aid):
            return
        if "relay_url" in cfgs:                                   # a single flat cfg (V1 callers)
            cfgs = {core.inst_of(cfgs): cfgs}
        inst = next((i for i, c in cfgs.items() if core.alert_matches(a, c)
                     and (c.get("enabled") or self.repo.open_position(i))), None)
        if inst is None:
            return                                                # another chart / timeframe
        cfg = cfgs[inst]
        ts = int(now.timestamp())
        age = core.alert_age_sec(a, now)
        if age is None or age > cfg["stale_sec"]:
            if not (first_run and (age is None or age > 3600)):   # don't bury the log in history
                self.repo.record_alert(a, ts, "STALE",
                                       "no receive time" if age is None else f"{age:.0f}s old", inst=inst)
            return
        self.repo.record_alert(a, ts, "PROCESSING", inst=inst)   # at-most-once
        pos = self.repo.open_position(inst)
        pending = self.repo.get_pending_exit(inst)
        block = core.entry_block(cfg, now, exit_pending=pending is not None)
        target = (pos.get("target_sig") if pos else None) or cfg.get("target_signal", "T2")
        actions = core.decide(a.get("sig"), pos["side"] if pos else None, block,
                              target=target)                      # the trade's own target (TVX_OPTS)
        done, notes = [], []
        for kind, arg in actions:
            if kind == "EXIT":
                ok, note = self._exit(pos, arg, now, alert_id=aid)
                done.append("EXIT" if ok else "EXIT_FAILED")
                notes.append(note)
                if not ok:
                    self.repo.set_pending_exit(pos["id"], arg, aid, inst=inst)
                    notes.append("entry skipped: exit pending")
                    break
            elif kind in ("NOTE_T1", "NOTE_T2"):          # ── TVX_T2_20261007 / TVX_OPTS ──
                which = kind[-2:]
                ok, note = self._note_target(pos, which, now)
                done.append(f"{which}_NOTED" if ok else "HOLD")
                notes.append(note)
            elif kind == "ENTER":
                ok, note = self._enter(arg, cfg, now, a)
                done.append(f"ENTER_{arg}" if ok else "ENTER_FAILED")
                notes.append(note)
            else:
                done.append(kind)
                notes.append(arg)
        action = "+".join(done)
        self.repo.record_alert(a, ts, action, "; ".join(n for n in notes if n), inst=inst)
        self.log(f"[TVX][ALERT] {inst} id={aid} sig={a.get('sig')} {a.get('ticker')} {a.get('tf')} "
                 f"→ {action} | {'; '.join(n for n in notes if n)}")

    # ── entry ──
    def _enter(self, side: str, cfg: Dict, now: dt.datetime, a: Dict):
        try:
            futs, opts = self._instruments(cfg, now.date())
            exp = core.pick_option_expiry([o["expiry"] for o in opts], now.date(),
                                          roll_days=int(cfg.get("roll_days", 2)))   # ── TVX_OPTS ──
            if exp is None:
                return False, "no option expiry after today"
            ox = cfg.get("opt_exchange", "MCX")                   # ── TVX_MULTI_20261009 ──
            if cfg.get("ref") == "SPOT":                          # Nifty: ATM from the index spot
                ref_sym, ref_tok, fkey = cfg["ref_symbol"], cfg.get("ref_token"), cfg["ref_key"]
            else:                                                 # Crude: from the option's futures
                fut = core.pick_future(futs, exp)
                if fut is None:
                    return False, f"no futures for option expiry {exp}"
                ref_sym, ref_tok, fkey = fut["tradingsymbol"], fut.get("instrument_token"), \
                    f"{ox}:{fut['tradingsymbol']}"
            chain = {o["strike"]: o for o in opts if o["expiry"] == exp and o["instrument_type"] == side}
            strikes = sorted(chain)
            alert_px = a.get("price")
            cands = core.nearest_strikes(strikes, float(alert_px), 4) if alert_px else []
            q = self._quote([fkey] + [f"{ox}:{chain[k]['tradingsymbol']}" for k in cands])
            fut_px = core.parse_quote(q.get(fkey)).get("ltp")
            if not fut_px:
                return False, f"no {cfg.get('ref_label', 'futures').lower()} price for {ref_sym}"
            atm = core.atm_strike(strikes, fut_px)
            ins = chain[atm]
            okey = f"{ox}:{ins['tradingsymbol']}"
            if okey not in q:
                q.update(self._quote([okey]))
            oq = core.parse_quote(q.get(okey))
            px, src = core.entry_fill(oq)
            if not px:
                return False, f"no price for {ins['tradingsymbol']}"
            units = int(ins.get("lot_size") or 0) if ox == "NFO" else 0   # NFO: Kite's lot is in units
            units = units if units > 1 else int(cfg["units_per_lot"])
            qty = int(cfg["lots"]) * units
            no_sl = bool(cfg.get("no_sl"))                                         # ── TVX_OPTS ──
            sl = None if no_sl else round(max(px - float(cfg["sl_points"]), 0.1), 1)
            t0 = int(now.timestamp())
            o0 = oq.get("ltp") or px
            row = {
                "side": side, "symbol": ins["tradingsymbol"], "token": ins.get("instrument_token"),
                "strike": atm, "expiry": exp.isoformat(), "fut_symbol": ref_sym, "inst": core.inst_of(cfg),
                "fut_px": fut_px, "alert_px": alert_px, "entry_alert_id": int(a["id"]),
                "entry_signal": a.get("sig"), "signal_bar": a.get("bar_time"),
                "entry_time": int(now.timestamp()), "entry_price": px, "entry_src": src,
                "entry_bid": oq.get("bid"), "entry_ask": oq.get("ask"), "entry_ltp": oq.get("ltp"),
                "sl_points": None if no_sl else float(cfg["sl_points"]), "sl_price": sl,
                "lots": int(cfg["lots"]), "units_per_lot": units, "qty": qty,
                "target_sig": "NONE" if cfg.get("no_target") else cfg.get("target_signal", "T2"),
                # ── TVX_HILO_20261007 ── the range starts at the entry quote
                "fut_token": ref_tok,
                "opt_high": o0, "opt_high_ts": t0, "opt_low": o0, "opt_low_ts": t0,
                "fut_high": fut_px, "fut_high_ts": t0, "fut_low": fut_px, "fut_low_ts": t0,
            }
            tid = self.repo.open_trade(row)
            self._notify(cfg, "ENTRY", tid, now)                    # ── TVX_TG_20261008 ──
            self._set_mark(core.inst_of(cfg),
                           {"ltp": oq.get("ltp"), "bid": oq.get("bid"), "ask": oq.get("ask"), "ts": t0},
                           {"ltp": fut_px, "ts": t0})
            rl = "spot" if cfg.get("ref") == "SPOT" else "fut"
            note = (f"BUY {ins['tradingsymbol']} @ {px} ({src}) {'no SL' if sl is None else f'SL {sl}'} · "
                    f"{rl} {fut_px} · {cfg['lots']} lot × {units}")
            self.log(f"[TVX][ENTER] #{tid} {note}")
            return True, note
        except Exception as e:
            self._err(f"entry {side} failed: {e}")
            return False, f"entry failed: {e}"

    # ── TVX_T2_20261007 / TVX_OPTS_20261008 ── target shadow exit while holding ──
    def _note_t1(self, pos: Dict, now: dt.datetime):
        return self._note_target(pos, "T1", now)

    def _note_target(self, pos: Dict, which: str, now: dt.datetime):
        w = which.lower()
        hold = "holding for T2" if pos.get("target_sig") == "T2" else "holding for the opposite signal"
        if pos.get(f"{w}_ts"):
            return False, f"{which} already recorded — {hold}"
        try:
            oq, fq = self._quote_pos(pos)
        except Exception as e:
            self._err(f"{which} note quote failed: {e}")
            return False, f"{which} reached — {hold} (price not recorded: quote failed)"
        self._fold_range(pos, oq, fq, int(now.timestamp()))
        px, src = core.exit_fill(oq)
        if not px:
            return False, f"{which} reached — {hold} (no bid to record)"
        self.repo.mark_target(pos["id"], which, int(now.timestamp()), px, src, fq.get("ltp"))
        cfg = self.cfg_store.load(core.inst_of(pos))
        if cfg.get("notify_t1"):
            self._notify(cfg, which, pos["id"], now)                # ── TVX_TG_20261008 ──
        pts = px - pos["entry_price"]
        note = f"{which} reached — {hold}; selling now would get {px} ({src}) {pts:+.1f} pts"
        self.log(f"[TVX][{which}] #{pos['id']} {note}")
        return True, note

    # ── TVX_TG_20261008 ── queue a Telegram message (sent by TvxNotifier) ──
    def _notify(self, cfg: Dict, kind: str, trade_id: int, now: dt.datetime) -> None:
        if not cfg.get("notify_telegram"):
            return
        try:
            t = self.repo.get_trade(trade_id)
            text = {"ENTRY": lambda: core.msg_entry(t, cfg), "EXIT": lambda: core.msg_exit(t),
                    "T1": lambda: core.msg_target(t, "T1"),
                    "T2": lambda: core.msg_target(t, "T2")}[kind]()
            self.repo.enqueue_notify(kind, trade_id, text, int(now.timestamp()))
        except Exception as e:                      # a message must never break a trade
            self._err(f"telegram {kind} #{trade_id} not queued: {e}")

    # ── exit ──
    def _exit(self, pos: Dict, reason: str, now: dt.datetime, alert_id: Optional[int] = None,
              quote: Optional[Dict] = None, fut_quote: Optional[Dict] = None):
        try:
            if quote is None:
                oq, fq = self._quote_pos(pos)
            else:
                oq, fq = quote, (fut_quote or {})
            px, src = core.exit_fill(oq)
            if not px:
                return False, f"no exit price for {pos['symbol']}"
            self._fold_range(pos, oq, fq, int(now.timestamp()))     # ── TVX_HILO ── exit tick counts
            ch = core.row_charges(pos, px)                         # MCX or NSE rates (TVX_MULTI)
            pts = round(px - pos["entry_price"], 2)
            gross = round(pts * pos["qty"], 2)
            net = round(gross - ch["total"], 2)
            ok = self.repo.close_trade(
                pos["id"], exit_time=int(now.timestamp()), exit_price=px, exit_src=src,
                exit_bid=oq.get("bid"), exit_ask=oq.get("ask"), exit_ltp=oq.get("ltp"),
                exit_reason=reason, exit_alert_id=alert_id, pnl_points=pts, gross=gross,
                charges=ch["total"], net=net, charges_detail=ch, fut_exit=fq.get("ltp"))
            if not ok:
                return False, "trade already closed"
            inst = core.inst_of(pos)
            self._notify(self.cfg_store.load(inst), "EXIT", pos["id"], now)   # ── TVX_TG_20261008 ──
            self.repo.set_pending_exit(None, inst=inst)
            self._manual.discard(inst)
            self._set_mark(inst, None, None)
            note = f"SELL {pos['symbol']} @ {px} ({src}) {reason} · {pts:+.1f} pts · net ₹{net:,.0f}"
            self.log(f"[TVX][EXIT] #{pos['id']} {note}")
            return True, note
        except Exception as e:
            self._err(f"exit {pos.get('symbol')} failed: {e}")
            return False, f"exit failed: {e}"

    # ── open-position management ──
    def _keys(self, pos: Dict):
        """(option key, reference key) — MCX:<opt>/MCX:<fut> for Crude,
        NFO:<opt>/NSE:NIFTY 50 for Nifty (TVX_MULTI_20261009)."""
        pre = core.PRESETS[core.inst_of(pos)]
        okey = f"{pre['opt_exchange']}:{pos['symbol']}"
        if pre["ref"] == "SPOT":
            return okey, pre["ref_key"]
        return okey, (f"{pre['opt_exchange']}:{pos['fut_symbol']}" if pos.get("fut_symbol") else None)

    def _quote_pos(self, pos: Dict, q: Optional[Dict] = None):
        """Option + its reference in ONE quote call (TVX_HILO_20261007); or
        sliced from a quote already fetched for every open leg (TVX_MULTI)."""
        okey, fkey = self._keys(pos)
        if q is None or okey not in q:
            q = self._quote([okey] + ([fkey] if fkey else []))
        return core.parse_quote(q.get(okey)), (core.parse_quote(q.get(fkey)) if fkey else {})

    def _fold_range(self, pos: Dict, oq: Dict, fq: Dict, ts: int) -> None:
        """Fold one live quote into the trade's running range (DB write only
        when something moved). Mutates pos so later folds in the same loop see it."""
        f: Dict = {}
        o, fu = oq.get("ltp"), (fq or {}).get("ltp")
        if o:
            exc = o - pos["entry_price"]
            mfe = round(max(pos.get("mfe_points") or 0.0, exc), 2)
            mae = round(min(pos.get("mae_points") or 0.0, exc), 2)
            if mfe != (pos.get("mfe_points") or 0.0):
                f["mfe_points"] = mfe
            if mae != (pos.get("mae_points") or 0.0):
                f["mae_points"] = mae
            if pos.get("opt_high") is None or o > pos["opt_high"]:
                f["opt_high"], f["opt_high_ts"] = o, ts
            if pos.get("opt_low") is None or o < pos["opt_low"]:
                f["opt_low"], f["opt_low_ts"] = o, ts
        if fu:
            if pos.get("fut_high") is None or fu > pos["fut_high"]:
                f["fut_high"], f["fut_high_ts"] = fu, ts
            if pos.get("fut_low") is None or fu < pos["fut_low"]:
                f["fut_low"], f["fut_low_ts"] = fu, ts
        if f:
            self.repo.update_range(pos["id"], f)
            pos.update(f)

    def _manage(self, pos: Dict, cfg: Dict, now: dt.datetime, q: Optional[Dict] = None) -> None:
        inst = core.inst_of(pos)
        carry = bool(cfg.get("carry_overnight"))                  # ── TVX_OPTS_20261008 ──
        if carry and inst not in self._manual and not core.in_session(cfg, now):
            return                                                 # carried overnight: nothing can move
        try:
            oq, fq = self._quote_pos(pos, q)
        except Exception as e:
            self._err(f"mark {pos['symbol']}: {e}")
            return
        self._set_mark(inst, dict(oq, ts=int(now.timestamp())), dict(fq, ts=int(now.timestamp())) if fq else None)
        self._fold_range(pos, oq, fq, int(now.timestamp()))

        entry_day = dt.datetime.fromtimestamp(pos["entry_time"], IST).date()
        s = core.session(now.date(), cfg)
        pending = self.repo.get_pending_exit(inst)

        reason = None
        if inst in self._manual:
            reason = "MANUAL"
        elif pending and pending.get("trade_id") == pos["id"]:
            reason = pending.get("reason") or "SIGNAL"
        elif (pos.get("expiry") and now >= s["squareoff"]
              and dt.date.fromisoformat(pos["expiry"]) <= now.date()):
            reason = "EXPIRY"                                      # never hold an option into expiry
        elif not carry and entry_day < now.date():
            reason = "EOD_LATE" if core.is_weekday(now.date()) and now >= s["start"] else None
        elif not carry and now >= s["squareoff"]:
            reason = "EOD"
        elif pos.get("sl_price") is not None and core.sl_hit(oq, pos["sl_price"]):
            reason = "SL"
        if reason is None:
            return
        alert_id = pending.get("alert_id") if pending and reason == pending.get("reason") else None
        ok, note = self._exit(pos, reason, now, alert_id=alert_id, quote=oq, fut_quote=fq)
        if not ok and reason in ("SL", "EOD", "EOD_LATE", "MANUAL", "EXPIRY"):
            self._err(f"{reason} exit not filled yet: {note}")

    # ── TVX_HILO_20261007 ── 1-minute candles for closed trades ──
    def _candles(self, kite, token, t0: int, t1: int):
        """Kite 1-minute candles with start in [t0, t1] → [(start_epoch, high, low)]."""
        f = dt.datetime.fromtimestamp(t0, IST).replace(tzinfo=None)
        t = dt.datetime.fromtimestamp(t1, IST).replace(tzinfo=None)
        rows = kite.historical_data(int(token), f, t, "minute") or []
        out = []
        for r in rows:
            d = r.get("date")
            if isinstance(d, str):
                d = dt.datetime.fromisoformat(d)
            if d is None:
                continue
            if d.tzinfo is None:
                d = d.replace(tzinfo=IST)
            ts = int(d.timestamp())
            if t0 <= ts <= t1:                          # never trust the API's edges
                out.append((ts, float(r.get("high") or 0) or None, float(r.get("low") or 0) or None))
        return out

    def _fut_token(self, cfg: Dict, trade: Dict, today: dt.date):
        if trade.get("fut_token"):
            return trade["fut_token"]
        if not trade.get("fut_symbol"):
            return None
        futs, _ = self._instruments(cfg, today)
        m = next((f for f in futs if f["tradingsymbol"] == trade["fut_symbol"]), None)
        return m.get("instrument_token") if m else None

    def _range_job(self, cfgs: Dict, now: dt.datetime) -> None:
        if "relay_url" in cfgs:                                   # a single flat cfg (V1 callers)
            cfgs = {core.inst_of(cfgs): cfgs}
        nowts = int(now.timestamp())
        if nowts - self._range_last_run < RANGE_MIN_GAP_SEC:
            return
        job = self.repo.next_range_job(RANGE_MAX_TRIES, settled_before=nowts - RANGE_SETTLE_SEC)
        if not job or self._range_next_at.get(job["id"], 0) > nowts:
            return
        self._range_last_run = nowts
        legacy = job.get("hilo_status") is None
        if legacy:                                         # closed before the patch: whole span
            span = (core.minute_floor(job["entry_time"]), core.minute_floor(job["exit_time"]))
        else:
            span = core.interior_minutes(job["entry_time"], job["exit_time"])
            if span is None:                               # < 3 minutes: live quotes are the range
                self.repo.update_range(job["id"], {"hilo_status": "DONE", "hilo_src": "LIVE"}, open_only=False)
                return
        try:
            kite = self.kite_provider()
            if kite is None:
                raise TvxError("no Kite data session")
            f: Dict = {}
            if job.get("token"):
                hi, hi_ts, lo, lo_ts = core.candle_extremes(self._candles(kite, job["token"], *span))
                f["opt_high"], f["opt_high_ts"] = core.merge_high(job.get("opt_high"), job.get("opt_high_ts"), hi, hi_ts)
                f["opt_low"], f["opt_low_ts"] = core.merge_low(job.get("opt_low"), job.get("opt_low_ts"), lo, lo_ts)
            cfg = cfgs.get(core.inst_of(job)) or self.cfg_store.load(core.inst_of(job))
            ftok = self._fut_token(cfg, job, now.date())
            if ftok:
                hi, hi_ts, lo, lo_ts = core.candle_extremes(self._candles(kite, ftok, *span))
                f["fut_high"], f["fut_high_ts"] = core.merge_high(job.get("fut_high"), job.get("fut_high_ts"), hi, hi_ts)
                f["fut_low"], f["fut_low_ts"] = core.merge_low(job.get("fut_low"), job.get("fut_low_ts"), lo, lo_ts)
                f["fut_token"] = ftok
            if not legacy and f.get("opt_high") is not None:
                # interior candles are inside the trade, so they may extend MFE/MAE
                f["mfe_points"] = round(max(job.get("mfe_points") or 0.0, f["opt_high"] - job["entry_price"]), 2)
                f["mae_points"] = round(min(job.get("mae_points") or 0.0, f["opt_low"] - job["entry_price"]), 2)
            f["hilo_status"], f["hilo_src"] = "DONE", ("1M" if legacy else "LIVE+1M")
            self.repo.update_range(job["id"], f, open_only=False)
            self._range_next_at.pop(job["id"], None)
            self.log(f"[TVX][RANGE] #{job['id']} {f['hilo_src']} opt {f.get('opt_low')}–{f.get('opt_high')} "
                     f"fut {f.get('fut_low')}–{f.get('fut_high')}")
        except Exception as e:
            tries = int(job.get("hilo_tries") or 0) + 1
            upd = {"hilo_tries": tries}
            if tries >= RANGE_MAX_TRIES:
                upd["hilo_status"] = "FAILED"
                if legacy:
                    upd["hilo_src"] = None
            self.repo.update_range(job["id"], upd, open_only=False)
            self._range_next_at[job["id"]] = nowts + RANGE_RETRY_SEC
            self._err(f"range candles for #{job['id']} failed (try {tries}/{RANGE_MAX_TRIES}): {e}")

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


# ───────────────────────── Telegram outbox sender (TVX_TG_20261008) ─────────────────────────

def _relay_post(url: str, token: str, payload: Dict, timeout: float = 12.0):
    import requests
    r = requests.post(url, json=payload, timeout=timeout,
                      headers={"Authorization": f"Bearer {token}", "User-Agent": "scalp-terminal-tvx/1"})
    if r.status_code != 200:
        raise RuntimeError(f"relay HTTP {r.status_code} {r.text[:160]}")
    return True


class TvxNotifier:
    """Own thread: drains tvx_notify in order. A slow or failing Telegram never
    delays the trading loop. Backoff 5 s → 15 s → 60 s → 120 s…; a message
    older than MAX_AGE is dropped instead of arriving late and misleading."""
    BACKOFF = (5, 15, 60, 120, 300)
    MAX_AGE = 30 * 60

    def __init__(self, repo: TvxRepo, cfg_store: TvxConfigStore,
                 http_post: Callable = _relay_post,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(IST),
                 log: Callable[[str], None] = _default_log, sleep: Callable[[float], None] = time.sleep):
        self.repo, self.cfg_store, self.http_post = repo, cfg_store, http_post
        self.clock, self.log, self.sleep = clock, log, sleep
        self.status = {"running": False, "last_ok_at": None, "last_error": None, "last_error_at": None}

    def step(self) -> bool:
        """Try to send the head of the queue. True when something was sent."""
        cfg = self.cfg_store.load()
        if not cfg["relay_url"] or not cfg["read_token"]:
            return False
        job = self.repo.notify_head()
        now = int(self.clock().timestamp())
        if not job or job["next_at"] > now:
            return False
        if now - job["created_at"] > self.MAX_AGE:
            self.repo.notify_drop(job["id"], f"dropped: still unsent after {self.MAX_AGE // 60} min")
            self.log(f"[TVX][TG] dropped {job['kind']} #{job['trade_id']} (stale)")
            return False
        try:
            self.http_post(f"{cfg['relay_url']}/notify", cfg["read_token"], {"text": job["text"]})
            self.repo.notify_sent(job["id"], now)
            self.status["last_ok_at"] = now
            return True
        except Exception as e:
            wait = self.BACKOFF[min(job["tries"], len(self.BACKOFF) - 1)]
            self.repo.notify_retry(job["id"], str(e), now + wait)
            self.status["last_error"], self.status["last_error_at"] = str(e)[:300], now
            self.log(f"[TVX][TG][WARN] {job['kind']} #{job['trade_id']} not sent ({e}); retry in {wait}s")
            return False

    def run_forever(self, stop: Optional[threading.Event] = None) -> None:
        self.status["running"] = True
        while not (stop and stop.is_set()):
            try:
                sent = self.step()
            except Exception as e:
                self.status["last_error"] = f"notifier error: {e}"
                sent = False
            self.sleep(0.2 if sent else 1.0)
        self.status["running"] = False


_NOTIFIER: Optional[TvxNotifier] = None


def get_notifier() -> Optional[TvxNotifier]:
    return _NOTIFIER


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
        global _NOTIFIER                                            # ── TVX_TG_20261008 ──
        _NOTIFIER = TvxNotifier(eng.repo, eng.cfg_store)
        threading.Thread(target=_NOTIFIER.run_forever, name="tvx_v1_tg", daemon=True).start()
        return eng
