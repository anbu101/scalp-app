#!/usr/bin/env python3
"""
apply_ORB_REPLAY_RETRY_20260911.py — FENCE: ORB_REPLAY_RETRY_20260911

Every restart after 09:15 → "ORB window incomplete — day refused" one second
after boot (2026-09-11 12:53:01, and each rebuild this week).

Cause 1 — replay ran before the broker was ready. _roll_day() arms the day
on the engine thread's FIRST iteration, immediately at boot, and calls
_warm_replay(); the Kite session is restored a few seconds later, so
_kite() is None, the replay writes "no kite — core cold" and returns — and
_roll_day keys on the date, so it never tries again. The first live bar
then reaches compute_orb with a one-bar prefix → refused. (A live bar can
only complete on a minute roll, which is why the alert sits at :01 — the
minute that started before the boot.)

Cause 2 — replay actions were discarded. _warm_replay() called
day.process(bar) and dropped the returned actions: a DAY_REFUSED during
replay was silent (audit said "warm-replayed N bars; levels=None/None"),
and a replayed past SIGNAL left the core's pending_side set with nobody to
abandon it — blocking every later signal that day.

Fix (engine only):
  * _roll_day marks the day REPLAY-PENDING when armed late. While pending,
    the loop does NOT fold live spot; every iteration calls
    _replay_if_pending(now), which retries as soon as _kite() is available
    (throttled to one historical call per 30 s once the broker IS up, so a
    historical-API error can't hammer Kite). One warning alert if still
    pending 3 minutes after arming.
  * _warm_replay handles the actions it used to drop: LEVELS → audit;
    DAY_REFUSED / FROZEN → day_stats + alert (visible); SIGNAL → the past
    signal is abandoned (on_entry_abandoned) and audited, never traded.
    adopt_resumed_position() then re-grafts an open row exactly as before.
  * Replay succeeds only when the prefix actually reached the current
    minute; a historical call that returns nothing keeps the day pending.

Known limit (documented, not solved here): a restart after a trade that
already CLOSED today rebuilds the core with day_trades = 0, so the per-day
budget could allow one extra trade. Separate item.

Safety: fence check, py_compile gate, engine-level simulation on the
patched module (boot with no kite → pending, no refusal; kite appears →
replay, levels set, no refusal, replayed signal abandoned; historical error
→ still pending + throttled; short history → refusal now VISIBLE; 09:15 arm
→ no replay; resume with position still adopts), .bak backup, dual-tree
mirror. Backend-only → rebuild backend.

Run from the repo root:  python3 apply_ORB_REPLAY_RETRY_20260911.py
"""
import importlib.util, os, py_compile, shutil, sys, tempfile, types
from datetime import datetime, timedelta, timezone

FENCE = "ORB_REPLAY_RETRY_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = "app/engine/orb/orb_engine.py"
P = os.path.join(BACKEND, REL)
if not os.path.isfile(P):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


src = read(P)
if FENCE in src:
    die(f"{FENCE} already present — nothing to do")
if "ORB_LATEBOOT" not in src:
    die("FLEET_EOD_ALERT_FIX_20260908 (ORB_LATEBOOT) not applied — apply it first")

# ── state ────────────────────────────────────────────────────────────────
src = sub1(src,
    '        self._resumed_pending = False\n'
    '\n'
    '    def start(self):\n',
    '        self._resumed_pending = False\n'
    '        # ── ORB_REPLAY_RETRY_20260911 ── late-armed day waiting for its\n'
    '        # kite-historical prefix; live spot is NOT folded while pending.\n'
    '        self._replay_pending = False\n'
    '        self._replay_armed_at: Optional[datetime] = None\n'
    '        self._replay_last_try: float = 0.0\n'
    '        self._replay_warned = False\n'
    '\n'
    '    def start(self):\n',
    "state fields")

# ── _roll_day: mark pending, first attempt ───────────────────────────────
src = sub1(src,
    '        _hm = now.hour * 60 + now.minute\n'
    '        if self.gm.pos is not None or _hm > SESSION_OPEN_MIN:\n'
    '            self._warm_replay(now)\n',
    '        _hm = now.hour * 60 + now.minute\n'
    '        # ── ORB_REPLAY_RETRY_20260911 ── the first attempt usually runs\n'
    '        # before the broker session is restored (engine thread starts at\n'
    '        # boot); stay PENDING and retry from the loop until it succeeds.\n'
    '        self._replay_pending = False\n'
    '        self._replay_armed_at = None\n'
    '        self._replay_last_try = 0.0\n'
    '        self._replay_warned = False\n'
    '        if self.gm.pos is not None or _hm > SESSION_OPEN_MIN:\n'
    '            self._replay_pending = True\n'
    '            self._replay_armed_at = now\n'
    '            self._replay_if_pending(now)\n'
    '\n'
    '    def _replay_if_pending(self, now: datetime) -> bool:\n'
    '        """── ORB_REPLAY_RETRY_20260911 ── True while the day still waits\n'
    '        for its replayed prefix (caller must not fold live spot)."""\n'
    '        if not self._replay_pending:\n'
    '            return False\n'
    '        kite = self._kite()\n'
    '        if kite is None:\n'
    '            if not self._replay_warned and self._replay_armed_at is not None \\\n'
    '                    and (now - self._replay_armed_at) >= timedelta(minutes=3):\n'
    '                self._replay_warned = True\n'
    '                self.gm._alert("REPLAY_WAIT", "ORB waiting for the broker "\n'
    '                               "session to rebuild today\'s bars — no live "\n'
    '                               "bars are being built until it does")\n'
    '            return True\n'
    '        if time.time() - self._replay_last_try < 30.0:\n'
    '            return True                     # historical error backoff\n'
    '        self._replay_last_try = time.time()\n'
    '        self._warm_replay(now)\n'
    '        return self._replay_pending\n',
    "roll_day pending")

# ── _warm_replay: handle actions, set pending=False only on success ──────
src = sub1(src,
    '        kite = self._kite()\n'
    '        day = self.gm.day\n'
    '        if kite is None or day is None:\n'
    '            write_audit_log("[ORB][RESUME] no kite — core cold; exits still "\n'
    '                            "guarded by EOD backstop")\n'
    '            return\n'
    '        try:\n'
    '            spot_token = 256265                      # NIFTY 50 index token\n'
    '            frm = now.replace(hour=9, minute=15, second=0, microsecond=0)\n'
    '            candles = kite.historical_data(spot_token, frm, now, "minute") or []\n'
    '            for c in candles:\n'
    '                ts = int(c["date"].timestamp())\n'
    '                ts -= ts % 60\n'
    '                if ts // 60 * 60 + 60 > int(now.timestamp()):\n'
    '                    break                            # forming bar — skip\n'
    '                day.process(OrbBar(ts, float(c["open"]), float(c["high"]),\n'
    '                                   float(c["low"]), float(c["close"])))\n'
    '            self.gm.adopt_resumed_position()\n'
    '            self._resumed_pending = False\n'
    '            write_audit_log(f"[ORB][RESUME] warm-replayed {len(candles)} bars"\n'
    '                            f"; levels={day.orb_high}/{day.orb_low}")\n'
    '        except Exception as e:\n'
    '            write_audit_log(f"[ORB][RESUME][FAIL] {e!r}")\n',
    '        kite = self._kite()\n'
    '        day = self.gm.day\n'
    '        if kite is None or day is None:\n'
    '            write_audit_log("[ORB][RESUME] no kite yet — replay pending "\n'
    '                            "(retried every poll; live bars not folded)")\n'
    '            return\n'
    '        try:\n'
    '            spot_token = 256265                      # NIFTY 50 index token\n'
    '            frm = now.replace(hour=9, minute=15, second=0, microsecond=0)\n'
    '            candles = kite.historical_data(spot_token, frm, now, "minute") or []\n'
    '            fed = 0\n'
    '            for c in candles:\n'
    '                ts = int(c["date"].timestamp())\n'
    '                ts -= ts % 60\n'
    '                if ts // 60 * 60 + 60 > int(now.timestamp()):\n'
    '                    break                            # forming bar — skip\n'
    '                acts = day.process(OrbBar(ts, float(c["open"]), float(c["high"]),\n'
    '                                          float(c["low"]), float(c["close"])))\n'
    '                fed += 1\n'
    '                # ── ORB_REPLAY_RETRY_20260911 ── actions were dropped here\n'
    '                for a in acts:\n'
    '                    if a[0] == "LEVELS":\n'
    '                        write_audit_log(f"[ORB][LEVELS] high={a[1]} low={a[2]} (replay)")\n'
    '                    elif a[0] == "DAY_REFUSED":\n'
    '                        self.gm.day_stats["refused"] = a[1]\n'
    '                        self.gm._alert("DAY_REFUSED", a[1] + " (during replay)")\n'
    '                    elif a[0] == "FROZEN":\n'
    '                        self.gm.day_stats["frozen"] = a[1]\n'
    '                        self.gm._alert("FROZEN", f"{a[1]} (during replay)", "critical")\n'
    '                    elif a[0] == "SIGNAL":\n'
    '                        # a signal from BEFORE the restart: never trade it\n'
    '                        # now; release the slot so later signals can fire\n'
    '                        day.on_entry_abandoned()\n'
    '                        write_audit_log(f"[ORB][RESUME] past {a[1]} signal at "\n'
    '                                        f"{datetime.fromtimestamp(a[2], IST).strftime(\'%H:%M\')} "\n'
    '                                        f"skipped (replay)")\n'
    '            if fed == 0:\n'
    '                write_audit_log("[ORB][RESUME] historical returned no completed "\n'
    '                                "bars — replay still pending")\n'
    '                return\n'
    '            self.gm.adopt_resumed_position()\n'
    '            self._resumed_pending = False\n'
    '            self._replay_pending = False\n'
    '            write_audit_log(f"[ORB][RESUME] warm-replayed {fed} bars"\n'
    '                            f"; levels={day.orb_high}/{day.orb_low}"\n'
    '                            f"{\' — REFUSED\' if day.refused else \'\'}")\n'
    '        except Exception as e:\n'
    '            write_audit_log(f"[ORB][RESUME][FAIL] {e!r} — replay still pending")\n',
    "warm_replay body")

# ── loop: don't fold live spot while pending ─────────────────────────────
src = sub1(src,
    '                self._roll_day(now)\n'
    '                ltp = self._spot_ltp()\n',
    '                self._roll_day(now)\n'
    '                if self._replay_if_pending(now):     # ── ORB_REPLAY_RETRY_20260911 ──\n'
    '                    time.sleep(IDLE_POLL_S)\n'
    '                    continue\n'
    '                ltp = self._spot_ltp()\n',
    "loop gate")

# IST constant for the audit line (module already has timezone imported)
if "\nIST = " not in src:
    src = sub1(src,
        'IDLE_POLL_S = 2.0\n',
        'IDLE_POLL_S = 2.0\n'
        'IST = timezone(timedelta(hours=5, minutes=30))   # ── ORB_REPLAY_RETRY_20260911 ──\n',
        "IST const")

# ── gates ────────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmpf = os.path.join(TMP, "orb_engine.py")
with open(tmpf, "w", encoding="utf-8") as f:
    f.write(src)
try:
    py_compile.compile(tmpf, doraise=True)
except py_compile.PyCompileError as e:
    die(f"py_compile failed: {e}")
print("py_compile gate: OK")

sys.path.insert(0, BACKEND)
spec = importlib.util.spec_from_file_location("app.engine.orb.orb_engine", tmpf)
ORB = importlib.util.module_from_spec(spec); sys.modules[spec.name] = ORB; spec.loader.exec_module(ORB)
from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS
from app.backtest.orb.orb_v1_engine import OrbBar
audit = []; ORB.write_audit_log = lambda s: audit.append(s)
ORB.time.sleep = lambda s: None
IST = ORB.IST
CFG = dict(DEFAULT_STRATEGY_CONFIGS["ORB_V1"])

class _GM:
    def __init__(self):
        self.day = None; self.pos = None; self.day_stats = {}; self.alerts = []
    def cfg(self): return CFG
    def _alert(self, code, msg, sev="warning"): self.alerts.append((code, msg))
    def adopt_resumed_position(self):
        if self.pos is not None and self.day is not None:
            self.day.pending_side = self.pos["side"]
            self.adopted = True
    def open_trade(self, **k): return True
class _Kite:
    def __init__(self, bars, raise_exc=None): self.bars, self.raise_exc, self.calls = bars, raise_exc, 0
    def historical_data(self, tok, frm, to, iv):
        self.calls += 1
        if self.raise_exc: raise self.raise_exc
        return [b for b in self.bars if b["date"] >= frm and b["date"] < to]
class _Broker:
    def __init__(self): self.kite = None
    def get_data_kite(self): return self.kite
def bars(day, frm_min, to_min, base=24500.0, spike_at=None):
    out = []
    for m in range(frm_min, to_min):
        d = day.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        px = base + (m - frm_min) * 0.2
        h, l = px + 3, px - 3
        if spike_at is not None and m == spike_at:
            px, h = base + 400, base + 405   # breakout above the ORB → SIGNAL
        out.append({"date": d, "open": px, "high": h, "low": l, "close": px})
    return out
def at(day, h, m, s=0): return day.replace(hour=h, minute=m, second=s, microsecond=0)

DAY = datetime(2026, 9, 11, tzinfo=IST)
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)

print("engine simulation")
# 1) boot 12:53:00 with no kite → pending, nothing refused, live folding blocked
b = _Broker(); gm = _GM(); e = ORB.OrbEngine(gm, b)
e._roll_day(at(DAY, 12, 53))
check("boot with no kite → replay pending, no refusal", e._replay_pending and not gm.alerts and gm.day.refused is None)
check("loop gate: still pending → live spot must not be folded", e._replay_if_pending(at(DAY, 12, 53, 2)) is True)
# 2) kite appears at 12:53:05 with full history 09:15→12:52 → replay, levels, no refusal
b.kite = _Kite(bars(DAY, 555, 12 * 60 + 53))
still = e._replay_if_pending(at(DAY, 12, 53, 5))
check("kite up → replay ran, pending cleared", still is False and not e._replay_pending and b.kite.calls == 1)
check("ORB levels set from replay, day NOT refused", gm.day.orb_high is not None and gm.day.refused is None and not any(a[0] == "DAY_REFUSED" for a in gm.alerts), str(gm.alerts))
check("live folding now proceeds", e._replay_if_pending(at(DAY, 12, 53, 7)) is False)
# 3) replay with a past breakout SIGNAL → abandoned, slot free, never traded
b2 = _Broker(); gm2 = _GM(); e2 = ORB.OrbEngine(gm2, b2)
e2._roll_day(at(DAY, 11, 10)); b2.kite = _Kite(bars(DAY, 555, 11 * 60 + 10, spike_at=10 * 60 + 5))
e2._replay_if_pending(at(DAY, 11, 10, 5))
check("replayed past SIGNAL → abandoned (pending_side None), audited", gm2.day.pending_side is None and any("past" in a and "signal" in a and "skipped" in a for a in audit), str([a for a in audit if 'RESUME' in a][-3:]))
# 4) historical raises → still pending, retry throttled to 30 s, no refusal
b3 = _Broker(); gm3 = _GM(); e3 = ORB.OrbEngine(gm3, b3)
b3.kite = _Kite([], raise_exc=RuntimeError("historical api not subscribed"))
e3._roll_day(at(DAY, 12, 53))
check("historical error → still pending, audited", e3._replay_pending and any("still pending" in a for a in audit))
e3._replay_if_pending(at(DAY, 12, 53, 3)); e3._replay_if_pending(at(DAY, 12, 53, 6))
check("retry throttled (one historical call within 30 s)", b3.kite.calls == 1)
e3._replay_last_try = 0.0; e3._replay_if_pending(at(DAY, 12, 54))
check("…and retried after the backoff", b3.kite.calls == 2)
# 5) short history (starts 09:20) → refusal is now VISIBLE, pending cleared (day is decided)
b4 = _Broker(); gm4 = _GM(); e4 = ORB.OrbEngine(gm4, b4)
b4.kite = _Kite(bars(DAY, 560, 12 * 60 + 53)); e4._roll_day(at(DAY, 12, 53))
check("incomplete history → DAY_REFUSED alert raised from replay (was silent)", any(a[0] == "DAY_REFUSED" and "replay" in a[1] for a in gm4.alerts) and not e4._replay_pending, str(gm4.alerts))
# 6) armed at 09:15:04 → no replay, not pending (bars built live)
b5 = _Broker(); gm5 = _GM(); e5 = ORB.OrbEngine(gm5, b5); b5.kite = _Kite(bars(DAY, 555, 556))
e5._roll_day(at(DAY, 9, 15, 4))
check("09:15 arm → no replay, not pending", not e5._replay_pending and b5.kite.calls == 0)
# 7) resume with an open position still adopts after replay
b6 = _Broker(); gm6 = _GM(); gm6.pos = {"side": "CE"}; e6 = ORB.OrbEngine(gm6, b6)
b6.kite = _Kite(bars(DAY, 555, 11 * 60)); e6._roll_day(at(DAY, 11, 0))
check("open-position resume → adopt_resumed_position called after replay", getattr(gm6, "adopted", False) and not e6._replay_pending)
# 8) 3-minute wait with no broker → one warning alert, still pending
b7 = _Broker(); gm7 = _GM(); e7 = ORB.OrbEngine(gm7, b7); e7._roll_day(at(DAY, 12, 53))
e7._replay_if_pending(at(DAY, 12, 55)); e7._replay_if_pending(at(DAY, 12, 56, 30)); e7._replay_if_pending(at(DAY, 12, 57))
check("no broker for 3 min → ONE REPLAY_WAIT warning", sum(1 for a in gm7.alerts if a[0] == "REPLAY_WAIT") == 1 and e7._replay_pending)
if FAILS:
    die(f"engine simulation failed: {FAILS}")
print("engine simulation: OK")

# ── write ────────────────────────────────────────────────────────────────
shutil.copy2(P, P + f".bak-{FENCE}")
with open(P, "w", encoding="utf-8") as f:
    f.write(src)
print(f"written {REL} (+ .bak-{FENCE})")
if os.path.isdir(DUAL):
    dst = os.path.join(DUAL, REL); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(P, dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
