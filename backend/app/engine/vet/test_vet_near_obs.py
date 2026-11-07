# backend/app/engine/vet/test_vet_near_obs.py
#
# ── VET_NEAR_OBS_20261106 ── Velvet observability + parity near-threshold
# ============================================================================
#   A. signal engine: bar margins on the 2026-11-06 09:25 bar (1.16 pts),
#      the freeze is logged ONCE, "not valid yet" logged once
#   B. tick engine: every socket line carries the strategy's own tag
#      (VET_TICK), TMA2 keeps TMA2_TICK; spot-only capture for VET
#   C. selection loop, REAL blocks executed against fakes, on the REAL epoch
#      clock: per-bar line (NEAR, FLIP CE, holding), freeze alert once,
#      restart check (alert only on a side mismatch), feed watch (never
#      connected → one alert after the grace, back → info, no spot candle
#      by 09:18, mid-session restart grace) + wiring order in the loop
#   D. parity: dated settings note over the replayed span, hedge re-class
#      after open pairing, exit missed, near-threshold tagging + verdict +
#      line, the replay's own bar index, the live candle from the capture
#
# Runs standalone:  cd backend && PYTHONPATH=$PWD python3 app/engine/vet/test_vet_near_obs.py
# ============================================================================
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import textwrap
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

TMP = tempfile.mkdtemp(prefix="vet_near_obs_")
os.environ["HOME"] = TMP                       # APP_HOME → a throw-away ~/.scalp-app
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, BACKEND)

FAILS, OK = [], [0]
IST = timezone(timedelta(hours=5, minutes=30))
LOG = []


def check(name, cond, detail=""):
    if cond:
        OK[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:400]}]")


import app.event_bus.audit_logger as AL        # noqa: E402
AL.write_audit_log = lambda m, *a, **k: LOG.append(str(m))

from app.utils.app_paths import APP_HOME       # noqa: E402
check("throw-away HOME in force", str(APP_HOME).startswith(TMP), APP_HOME)


def T(y, mo, d, hh, mm, ss=0):
    return int(datetime(y, mo, d, hh, mm, ss, tzinfo=IST).timestamp())


# ── A. signal engine ─────────────────────────────────────────────────
print("\n── A. signal engine ──")
from app.engine.vet import vet_live_signal_engine as SE   # noqa: E402

bar = SimpleNamespace(open=24488.80, close=24502.85)
st = SimpleNamespace(ema_f1=100.0, ema_f2=102.72, ch_top=24537.45, ch_bot=24504.01, sma_t=24520.73)
m = SE.bar_margins(bar, st)
check("09:25 bar: bearish test met by 1.16 pts (close vs channel bottom)", m["to_down"] == 1.16, m)
check("…bullish test missed by a wide margin", m["to_up"] < -40, m)
check("…EMA gap −2.72", m["ema_gap"] == -2.72, m)


def minutes(day, start_hhmm, n, px0, step):
    h, mi = start_hhmm
    t0 = T(day.year, day.month, day.day, h, mi)
    out, px = [], px0
    for i in range(n):
        o = px
        px = px + step(i)
        out.append({"ts": t0 + 60 * i, "open": o, "high": max(o, px) + 1, "low": min(o, px) - 1, "close": px})
    return out


days = []
x = date(2026, 10, 20)
while len(days) < 11:
    if x.weekday() < 5:
        days.append(x)
    x += timedelta(days=1)
warm = []
for dd in days[:10]:
    warm += minutes(dd, (9, 15), 375, 24000.0, lambda i: 0.5 if i % 7 else -1.0)
cfg = {"trend_len": 36, "range_len": 0.618, "ema_fast1": 10, "ema_fast2": 20, "signal_tf": 5,
       "leg_action": "SELL", "warmup_sessions": 10}
eng = SE.VetLiveSignalEngine(cfg, warmup_1m=warm)
today = minutes(days[10], (9, 15), 30, 24200.0, lambda i: 0.3)
sigs = [s for s in (eng.on_minute(c) for c in today) if s]
check("completed bars carry the new fields (open, channel, margins, prev condition)",
      sigs and all(k in sigs[-1] for k in ("open", "close", "ch_top", "ch_bot", "ema_gap", "to_up", "to_down",
                                           "prev_condition")), sigs[-1] if sigs else None)
check("…and the original fields are unchanged",
      sigs and all(k in sigs[-1] for k in ("bar_ts", "last1m_ts", "condition", "spot", "in_range", "dir_trend",
                                           "warmup_ok")))
LOG.clear()
bt = eng._last_bar_ts
eng.guard.seen[int(bt)] = -eng.guard.seen[int(bt)] if eng.guard.seen[int(bt)] else 1   # force a restatement
more = minutes(days[10], (9, 45), 10, today[-1]["close"], lambda i: 0.3)
for c in more:
    eng.on_minute(c)
fl = [m_ for m_ in LOG if "[VET][SIG][FROZEN]" in m_]
check("a guard freeze is logged — once, with the reason", len(fl) == 1 and "restated" in fl[0], LOG)
check("…and the engine stays frozen (decide = blocked frozen)", eng.decide("PE").get("blocked") == "frozen")
LOG.clear()
e2 = SE.VetLiveSignalEngine(dict(cfg, warmup_sessions=1), warmup_1m=minutes(days[9], (9, 15), 20, 24000.0, lambda i: 0.2))
for c in minutes(days[10], (9, 15), 30, 24200.0, lambda i: 0.3):
    e2.on_minute(c)
iv = [m_ for m_ in LOG if "indicators not valid yet" in m_]
check("too little history → 'not valid yet' logged once (not every bar)", len(iv) == 1, LOG)

# ── B. tick engine ───────────────────────────────────────────────────
print("\n── B. tick engine ──")
from app.engine.tma2 import tma2_tick_engine as TE   # noqa: E402


class WS:
    MODE_LTP = "ltp"

    def subscribe(self, t):
        pass

    def set_mode(self, m, t):
        pass


LOG.clear()
ev = TE.TMA2TickEngine(None, None, lambda *a: None, log_tag="VET_TICK", alert_label="VET_V1")
ev._on_connect(WS(), {})
et = TE.TMA2TickEngine(None, None, lambda *a: None)
et._on_connect(WS(), {})
check("Velvet's connect line carries VET_TICK", any(l.startswith("[VET_TICK][WS] connected") for l in LOG), LOG)
check("TMA2's connect line still carries TMA2_TICK", any(l.startswith("[TMA2_TICK][WS] connected") for l in LOG), LOG)
src_te = open(os.path.join(BACKEND, "app/engine/tma2/tma2_tick_engine.py"), encoding="utf-8").read()
check("no hard-coded [TMA2_TICK] tag left in the engine (only the default)",
      src_te.count("TMA2_TICK") == 1 and 'log_tag or "TMA2_TICK"' in src_te)
cap = os.path.join(TMP, "cap")
ec = TE.TMA2TickEngine(None, None, lambda *a: None, capture_dir=cap, capture_spot_only=True)
ec._spot_token, ec._tok2sym = 256265, {111: "NIFTY26N0924450CE"}
c0 = {"ts": T(2026, 11, 6, 9, 29), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
ec._capture(256265, c0)
ec._capture(111, c0)
lines = open(os.path.join(cap, "2026-11-06.jsonl")).read().splitlines()
check("spot-only capture keeps NIFTY and drops option candles",
      len(lines) == 1 and json.loads(lines[0])["sym"] == "SPOT", lines)
ea = TE.TMA2TickEngine(None, None, lambda *a: None, capture_dir=os.path.join(TMP, "cap2"))
ea._spot_token, ea._tok2sym = 256265, {111: "X"}
ea._capture(256265, c0)
ea._capture(111, c0)
check("default capture (TMA2) still keeps every token",
      len(open(os.path.join(TMP, "cap2", "2026-11-06.jsonl")).read().splitlines()) == 2)

# ── C. selection loop blocks ─────────────────────────────────────────
print("\n── C. selection loop ──")
from app.engine.vet import vet_selection_loop as VL   # noqa: E402
src = open(os.path.join(HERE, "vet_selection_loop.py"), encoding="utf-8").read()


def block(begin_marker):
    b = src.index(begin_marker)
    e_ = src.index("# ── VET_NEAR_OBS_20261106 END ──", b)
    return textwrap.dedent(src[b:e_].split("\n", 1)[1])


ALERTS = []


class Guard:
    frozen = False
    reason = None


class Eng:
    ema_fast1, ema_fast2 = 10, 20
    guard = Guard()
    nxt = {"action": "HOLD", "blocked": None}
    sig = None

    def decide(self, side):
        return dict(self.nxt)

    def latest_signal(self):
        return self.sig


mgr = SimpleNamespace(pos={"side": "PE", "main": {"tradingsymbol": "NIFTY26N0923900PE"}})
ns = {"datetime": datetime, "IST": VL.IST, "HOLD": "HOLD", "Dict": dict, "Optional": object,
      "NEAR_PTS": VL.NEAR_PTS, "engine": Eng(), "manager": mgr, "state": {},
      "write_audit_log": LOG.append, "_alert": lambda m_, s_="warning": ALERTS.append((s_, m_))}
exec(compile(block("# ── VET_NEAR_OBS_20261106 BEGIN ── per-bar log line"), "loop<bar>", "exec"), ns)
sig = {"bar_ts": T(2026, 11, 6, 9, 25), "last1m_ts": T(2026, 11, 6, 9, 29), "condition": -1, "prev_condition": 1,
       "spot": 24502.85, **SE.bar_margins(bar, st)}
LOG.clear()
ns["_bar_log"](sig, {"action": "FLIP", "side": "CE", "blocked": None})
ln = LOG[-1] if LOG else ""
check("per-bar line: time, O/C, channel, EMA gap, condition change",
      ln.startswith("[VET][BAR] 09:25 O 24488.80 C 24502.85 channel 24504.01/24537.45 ema10-20 -2.72 cond +1→-1"), ln)
check("…flags NEAR (1.16 pts) and says what it did and what it holds",
      "to_down +1.16" in ln and " NEAR" in ln and "→ FLIP CE (holding PE NIFTY26N0923900PE)" in ln, ln)
ns["_bar_log"](dict(sig, to_up=-400.0, to_down=-300.0), {"action": "HOLD", "blocked": None})
check("a bar far from both tests is not NEAR; HOLD is logged too", "NEAR" not in LOG[-1] and "→ HOLD (" in LOG[-1], LOG[-1])
ns["_bar_log"](sig, {"action": "HOLD", "blocked": "warmup 8/10 sessions"})
check("a blocked decision is logged with its reason", "→ BLOCKED (warmup 8/10 sessions)" in LOG[-1], LOG[-1])
ns["state"]["freeze_alerted"] = False
ns["engine"].guard.frozen, ns["engine"].guard.reason = True, "bar 1 restated condition 1 -> -1"
ALERTS.clear()
ns["_check_frozen"]()
ns["_check_frozen"]()
check("engine freeze → ONE error alert naming the reason and the held position",
      len(ALERTS) == 1 and ALERTS[0][0] == "error" and "restated" in ALERTS[0][1]
      and "holding PE NIFTY26N0923900PE" in ALERTS[0][1], ALERTS)
ALERTS.clear()
LOG.clear()
ns["engine"].sig = sig
ns["engine"].nxt = {"action": "HOLD", "blocked": None}
ns["_restart_check"](22)
check("restart: rebuilt candles agree with the held side → logged, no alert",
      not ALERTS and any("agree with the position held" in l for l in LOG), (ALERTS, LOG))
ns["engine"].nxt = {"action": "FLIP", "side": "CE", "blocked": None, "bar_ts": T(2026, 11, 6, 11, 0), "condition": -1}
ns["_restart_check"](22)
check("restart: rebuilt candles want the other side → one warning naming both",
      len(ALERTS) == 1 and ALERTS[0][0] == "warning" and "FLIP CE" in ALERTS[0][1]
      and "11:00" in ALERTS[0][1] and "holding PE" in ALERTS[0][1], ALERTS)
ALERTS.clear()
ns["_restart_check"](0)
check("restart check does nothing when nothing was backfilled", not ALERTS)

# feed watch, real epoch clock
fb = block("# ── VET_NEAR_OBS_20261106 BEGIN ── the two watchdogs")
D6 = date(2026, 11, 6)


def feed_ns(start_ts):
    tick = SimpleNamespace(_ws_connected=False, last_spot_candle_ts=0)
    n = {"time": __import__("time"), "IST": VL.IST, "CONN_GRACE_S": VL.CONN_GRACE_S,
         "FIRST_CANDLE_GRACE_S": VL.FIRST_CANDLE_GRACE_S, "tick": tick, "today": D6,
         "write_audit_log": LOG.append, "_alert": lambda m_, s_="warning": ALERTS.append((s_, m_))}
    exec(compile(fb, "loop<feed>", "exec"), n)
    n["fw"]["start"] = start_ts
    return n


ALERTS.clear()
f = feed_ns(T(2026, 11, 6, 8, 43, 56))
f["tick"]._ws_connected = True
f["tick"].last_spot_candle_ts = T(2026, 11, 6, 9, 15)
for hh, mm, ss in ((9, 16, 30), (9, 17, 30), (9, 19, 0)):
    f["_feed_watch"](T(2026, 11, 6, hh, mm, ss))
check("normal morning (connected 09:13, candles flowing) → no alert", not ALERTS, ALERTS)
f = feed_ns(T(2026, 11, 6, 8, 43, 56))
f["_feed_watch"](T(2026, 11, 6, 9, 16, 30))
check("never connected: 09:16:30 still inside the grace → no alert",
      not [a for a in ALERTS if "connection" in a[1]], ALERTS)
f["_feed_watch"](T(2026, 11, 6, 9, 17, 1))
f["_feed_watch"](T(2026, 11, 6, 9, 17, 31))
conn = [a for a in ALERTS if "data connection is not up" in a[1]]
check("…09:17:01 → ONE error alert (60 s after 09:16), not repeated", len(conn) == 1 and conn[0][0] == "error", ALERTS)
f["_feed_watch"](T(2026, 11, 6, 9, 18, 31))
first = [a for a in ALERTS if "no NIFTY spot candle yet" in a[1]]
check("…and by 09:18 with no spot candle → one 'no candle yet' alert", len(first) == 1, ALERTS)
f["tick"]._ws_connected = True
f["_feed_watch"](T(2026, 11, 6, 9, 19, 1))
f["_feed_watch"](T(2026, 11, 6, 9, 19, 31))
back = [a for a in ALERTS if "connection back" in a[1]]
check("connection comes back → one info line", len(back) == 1 and back[0][0] == "info", ALERTS)
f["tick"]._ws_connected = False
f["_feed_watch"](T(2026, 11, 6, 12, 0, 0))
f["_feed_watch"](T(2026, 11, 6, 12, 1, 1))
check("a later drop that stays down 60 s → a new alert (one per outage)",
      len([a for a in ALERTS if "data connection is not up" in a[1]]) == 2, ALERTS)
ALERTS.clear()
f = feed_ns(T(2026, 11, 6, 11, 4, 41))                  # mid-session restart at 11:04:41
f["_feed_watch"](T(2026, 11, 6, 11, 5, 11))
check("restart at 11:04:41, not connected 30 s later → no alert yet", not ALERTS, ALERTS)
f["_feed_watch"](T(2026, 11, 6, 11, 5, 42))
check("…61 s after the restart → the alert fires", any("data connection is not up" in a[1] for a in ALERTS), ALERTS)
f["tick"]._ws_connected = True
f["_feed_watch"](T(2026, 11, 6, 11, 6, 0))
f["_feed_watch"](T(2026, 11, 6, 11, 6, 50))
check("…no-candle alert waits FIRST_CANDLE_GRACE_S after a restart",
      not any("no NIFTY spot candle" in a[1] for a in ALERTS), ALERTS)
f["_feed_watch"](T(2026, 11, 6, 11, 7, 12))
check("…and fires after it when still no candle", any("no NIFTY spot candle" in a[1] for a in ALERTS), ALERTS)

i_dec = src.index('        state["last_decision"] = decision\n')
check("per-bar line written right after the decision, before any gate returns",
      src.index("_bar_log(sig, decision)", i_dec) < src.index("if decision.get(\"blocked\"):", i_dec))
i_on = src.index("        sig = engine.on_minute(spot_candle)\n")
check("freeze check right after each fold", src.index("_check_frozen()", i_on) < src.index("if sig is None:", i_on))
check("Velvet keeps its live spot candles", "capture_dir=_vet_capture_dir()" in src and "capture_spot_only=True" in src)
check("restart check runs after the backfill line", src.index("_restart_check(fed)") > src.index("[VET] backfilled"))
check("feed watch runs inside the armed weekday branch, skipped on holidays",
      "if armed and wd and not _holiday:" in src and src.index("_feed_watch(time.time())") > src.index("if armed and wd and not _holiday:"))
check("new alerts go through notify_critical (bell always; Telegram only when LIVE)",
      "notify_critical(payload)" in src and '_alert_mode["mode"] = str(mode or "PAPER").upper()' in src)
d_ = VL._vet_capture_dir()
check("capture folder is ~/.scalp-app/vet_capture", d_ and d_.startswith(str(APP_HOME)) and d_.endswith("vet_capture"), d_)

# ── D. parity ────────────────────────────────────────────────────────
print("\n── D. parity ──")
from app.parity import parity_core as PC                 # noqa: E402
from app.recorder import rec_db as RD                    # noqa: E402

c = RD.connect()
c.execute("INSERT INTO session_config (day, strategy_id, mode, degraded, config_json, config_hash, captured_at) "
          "VALUES (?,?,?,?,?,?,?)", ("2026-11-04", "VET_V1", "PAPER", 0, json.dumps(cfg), "h", T(2026, 11, 4, 9, 14)))
for ts in (T(2026, 11, 4, 9, 20, 58), T(2026, 11, 4, 9, 21, 26), T(2026, 11, 5, 10, 12)):
    c.execute("INSERT INTO config_changes (ts, strategy_id, mode, config_json, config_hash, source) VALUES (?,?,?,?,?,?)",
              (ts, "VET_V1", "PAPER", json.dumps(cfg), "x", "save"))
c.commit()
c.close()
_, notes = PC.config_for(date(2026, 11, 4), "VET_V1", until=date(2026, 11, 6))
nn = " ".join(notes)
# ── PARITY_POLISH_20261107 ── the date once per day
check("settings note says WHEN, with the date", "on 2026-11-04 at 09:20, 09:21" in nn, notes)
check("…covers the whole replayed span (a 5 Nov save is reported too)", "on 2026-11-05 at 10:12" in nn, notes)
check("…and names whose opening config the replay used", "2026-11-04's opening config" in nn, notes)
_, notes1 = PC.config_for(date(2026, 11, 4), "VET_V1")
check("without `until` only that day's saves are listed", "2026-11-05" not in " ".join(notes1), notes1)


def leg(sym, side, ets, ent, xts=None, ext=None, reason=None, qty=650, book="PAPER", open_=False):
    return {"symbol": sym, "side": side, "type": sym[-2:], "strike": float(sym[-7:-2]), "qty": qty,
            "entry_ts": ets, "exit_ts": xts, "entry": ent, "exit": ext, "reason": reason, "synthetic": False,
            "wing_modelled": False, "book": book, "open": open_}


E4 = T(2026, 11, 4, 13, 14)
bt_closed = [leg("NIFTY26N0923900PE", "SELL", E4, 203.25, T(2026, 11, 6, 9, 29), 27.50, "FLIP"),
             leg("NIFTY26N0924450CE", "SELL", T(2026, 11, 6, 9, 29), 179.60, T(2026, 11, 6, 9, 39), 209.20, "FLIP")]
bt_open = [dict(leg("NIFTY26N0924600PE", "SELL", T(2026, 11, 6, 9, 39), 218.45), open=True)]
app_open = [leg("NIFTY26N0923900PE", "SELL", E4, 203.25, open_=True), leg("NIFTY26N0922400PE", "BUY", E4, 5.0, open_=True)]
rows = PC.match(bt_closed, [])
for r in PC.match(bt_open, app_open):
    r["open"] = True
    rows.append(r)
rows = PC.pair_across_open(rows)
before = sorted(r["status"] for r in rows)
rows = PC.mark_exit_missed(PC.reclass_hedges(rows, bt_closed + bt_open))
after = {(r["bt"] or r["app"])["symbol"][-7:]: r for r in rows}
check("the 6 Nov shape before the fix: the wing read EXTRA", "EXTRA" in before, before)
check("…after: the carried 22400PE wing is HEDGE (paired short 23900PE)", after["22400PE"]["status"] == "HEDGE", after["22400PE"])
check("…23900PE closed by the backtest, still held by the app → exit missed",
      after["23900PE"]["status"] == "MATCHED" and after["23900PE"].get("exit_missed"), after["23900PE"])

D_FROM = date(2026, 11, 4)
idx = {"sell": True, "tf": 5, "bars": {
    T(2026, 11, 6, 9, 29): {"bar_ts": T(2026, 11, 6, 9, 25), "cond": -1, "prev": 1, **SE.bar_margins(bar, st)},
    T(2026, 11, 6, 9, 39): {"bar_ts": T(2026, 11, 6, 9, 35), "cond": 1, "prev": -1, "open": 24549.0, "close": 24552.70,
                            "ch_top": 24544.44, "ch_bot": 24509.41, "ema_gap": 1.67, "to_up": 1.67, "to_down": -43.29}}}
os.makedirs(os.path.join(str(APP_HOME), "vet_capture"), exist_ok=True)
with open(os.path.join(str(APP_HOME), "vet_capture", "2026-11-06.jsonl"), "w") as fh:
    for k, (o, cl) in enumerate(((24489.10, 24480.0), (24480.0, 24492.0), (24492.0, 24511.0), (24511.0, 24512.4),
                                 (24512.4, 24504.30))):
        fh.write(json.dumps({"sym": "SPOT", "ts": T(2026, 11, 6, 9, 25 + k), "open": o, "high": o, "low": o, "close": cl}) + "\n")
rows = PC.tag_vet_near(rows, idx, date(2026, 11, 6))
by = {(r["bt"] or r["app"])["symbol"][-7:]: r for r in rows}
check("missed 24450CE → near-threshold, 1.16 pts, 09:25 bar",
      by["24450CE"].get("near") and by["24450CE"]["near_pts"] == 1.16 and by["24450CE"]["near_bar"] == "09:25", by["24450CE"])
check("exit-missed 23900PE → measured on its exit bar (1.16 pts)", by["23900PE"].get("near") and by["23900PE"]["near_pts"] == 1.16)
check("missed 24600PE (entry 09:39) → near, 1.67 pts (EMA gap decides)", by["24600PE"].get("near") and by["24600PE"]["near_pts"] == 1.67)
check("the hedge row is not tagged", "near" not in by["22400PE"])
check("live candle read from the capture (C 24504.30 — on the other side of 24504.01)",
      by["24450CE"].get("near_live") == {"open": 24489.10, "close": 24504.30, "minutes": 5}, by["24450CE"].get("near_live"))
s = PC.summarise(rows)
check("verdict: 'differs at a near-threshold bar' (every difference is near)",
      s["verdict"] == "differs at a near-threshold bar" and s["near_threshold"] == 3 and s["near_min_pts"] == 1.16, s)
check("exit missed counted; hedges kept out of the app P/L", s["exit_missed"] == 1 and s["hedge"] == 1, s)
res = {"ok": True, "name": "Velvet", "strategy_id": "VET_V1", "book": "PAPER", "summary": s, "rows": rows}
check("daily line: amber ⚠ near-threshold (1.16 pts)", PC.line_for(res) == "Velvet ⚠ near-threshold (1.16 pts)", PC.line_for(res))
far = {T(2026, 11, 6, 9, 29): dict(idx["bars"][T(2026, 11, 6, 9, 29)], to_down=12.0)}
rows2 = PC.tag_vet_near([dict(r, near=None, near_pts=None) for r in rows], {"sell": True, "bars": far}, date(2026, 11, 6))
s2 = PC.summarise(rows2)
check("same rows on a clear bar (12 pts) → 'trades differ', exit missed in the line",
      s2["verdict"] == "trades differ" and "exit missed" in PC.line_for(dict(res, summary=s2)), (s2, PC.line_for(dict(res, summary=s2))))
off = {T(2026, 11, 6, 9, 29) + 30: idx["bars"][T(2026, 11, 6, 9, 29)]}
r3 = PC.tag_vet_near([dict(by["24450CE"], near=None, near_pts=None)], {"sell": True, "bars": {T(2026, 11, 6, 9, 29): off[T(2026, 11, 6, 9, 29) + 30]}}, date(2026, 11, 6))
check("decision minute matched on the real epoch (entry 09:29:00 → the 09:25 bar)", r3[0].get("near_pts") == 1.16, r3[0])
bt_s = dict(bt_closed[1], entry_ts=T(2026, 11, 6, 9, 29, 30))
r4 = PC.tag_vet_near([{"status": "MISSED", "bt": bt_s, "app": None}], idx, date(2026, 11, 6))
check("…an entry stamped 09:29:30 floors to the same decision minute", r4[0].get("near_pts") == 1.16, r4[0])

# replay's own bar index from a pack (runner shape)
pack = os.path.join(TMP, "pack.db")
p = sqlite3.connect(pack)
p.executescript(RD._SCHEMA.split("CREATE TABLE IF NOT EXISTS band")[0])
xs, x = [], date(2026, 10, 19)
while len(xs) < 14:
    if x.weekday() < 5:
        xs.append(x)
    x += timedelta(days=1)
for dd in xs:
    for cdl in minutes(dd, (9, 15), 375, 24000.0 + 20 * xs.index(dd), lambda i: (0.6 if i % 9 else -2.5)):
        p.execute(f"INSERT INTO backtest_candles_1m ({RD.COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (256265, cdl["ts"], "NIFTY", "NIFTYSPOT", "SPOT", 0, "", cdl["open"], cdl["high"], cdl["low"], cdl["close"], 0, 0))
p.commit()
p.close()
vi = PC.vet_bar_index(pack, cfg, xs[-3], xs[-1])
k0 = T(xs[-1].year, xs[-1].month, xs[-1].day, 9, 29)
check("bar index keyed by the decision minute (09:29 for the 09:25 bar)",
      k0 in vi["bars"] and vi["bars"][k0]["bar_ts"] == T(xs[-1].year, xs[-1].month, xs[-1].day, 9, 25))
check("…covers the replayed span only, from d_from", min(vi["bars"]) >= PC.day_start(xs[-3]))
from app.backtest.gc.gc_v1_engine import resample_spot          # noqa: E402
from app.backtest.vet.vet_v1_engine import vet_states           # noqa: E402
con = sqlite3.connect(pack)
allb = []
for dd in xs[-13:]:                                              # 10 warm-up sessions + 3 replayed
    ds = PC.day_start(dd)
    rr = [dict(zip(("ts", "open", "high", "low", "close"), r)) for r in con.execute(
        "SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE ts>=? AND ts<? ORDER BY ts", (ds, ds + 86400))]
    allb += resample_spot(rr, 5, ds + (9 * 60 + 15) * 60)
con.close()
ss = vet_states(allb, ema_fast1=10, ema_fast2=20, trend_len=36, range_len=0.618)
j = next(i for i, b_ in enumerate(allb) if b_.last1m_ts == k0)
check("…its condition and margins equal a direct runner-shape computation",
      vi["bars"][k0]["cond"] == ss[j].condition and vi["bars"][k0]["to_down"] == SE.bar_margins(allb[j], ss[j])["to_down"])

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL VET NEAR-THRESHOLD / OBSERVABILITY CHECKS PASSED")
