#!/usr/bin/env python3
# apply_ws_selfheal.py — fence WS_SELFHEAL_20261021
# ============================================================================
# INCIDENT 2026-10-21: TMA, TMA2 and VET — the three sockets on the TRADE API
# key — connected at 08:30 (day-cycle arm), received the flat pre-market
# NIFTY snapshot (22888.95) for 08:30–08:35, then went silent with NO
# close / error / reconnect event: a zombie TCP flow. Their coordinators
# alerted at 09:16:01, but nothing healed them — unlike ZerodhaTickEngine and
# SharedTickFeed (data key, connect at 09:15, own sendClose watchdog), the
# TMA/TMA2 tick engines had no self-heal. All three ran blind into the day.
#
# WHAT THIS CHANGES (backend/app and, if present, desktop/src-tauri/backend/app)
#   tma/tma_tick_engine.py, tma2/tma2_tick_engine.py   (VET uses TMA2's class)
#     (a) WebSocket connect deferred to 09:13 IST — a fresh socket at the
#         open, never one idling 45 min through the pre-open unproven.
#     (b) spot-silence SELF-HEAL: spot session (09:15–15:15, weekday,
#         non-holiday) + connected + no NIFTY tick for 120 s (baseline never
#         earlier than today's 09:15) → ws.sendClose(1000) so KiteTicker's
#         auto-reconnect heals it (NOT kws.close(): that calls stop_retry()).
#         Cooldown 90 s, back-off to 30 min after 3 fruitless drops.
#     (c) Telegram system alerts: reconnect forced (warning), feed back
#         (info), 3 reconnects failed → restart the app (error).
#     (d) on_close now flips a connected flag; optional log_tag/alert_label
#         so VET's instance logs as [VET_TICK] instead of [TMA2_TICK].
#   tma/tma_selection_loop.py, tma2/tma2_selection_loop.py
#     pass notify into the tick engine; coordinator sends "spot candles
#     back" after its own 3-minute alert (the old alert never closed).
#   vet/vet_selection_loop.py
#     pass notify/log_tag/alert_label; watchdog gap measured from the bar
#     END and never from before 09:15 (the 2461 s figure counted pre-market);
#     "spot candles back" message.
#
# Run from the repo root (the folder holding backend/):
#     python3 apply_ws_selfheal.py
# Idempotent: aborts cleanly if the fence is already present. Staged
# all-or-nothing writes, .bak-WS_SELFHEAL_20261021 backups, py_compile gate,
# behavioural tests on the real epoch clock before anything is written.
# ============================================================================

from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import tempfile
import types

FENCE = "WS_SELFHEAL_20261021"
TREES = ["backend/app", "desktop/src-tauri/backend/app"]

F_TMA_TICK = "engine/tma/tma_tick_engine.py"
F_TMA2_TICK = "engine/tma2/tma2_tick_engine.py"
F_TMA_LOOP = "engine/tma/tma_selection_loop.py"
F_TMA2_LOOP = "engine/tma2/tma2_selection_loop.py"
F_VET_LOOP = "engine/vet/vet_selection_loop.py"
FILES = [F_TMA_TICK, F_TMA2_TICK, F_TMA_LOOP, F_TMA2_LOOP, F_VET_LOOP]

# parent fences this builds on (working-copy drift guard)
PREREQ = {
    F_TMA_TICK: "TMA_TICK_LTP_PUBLISH_20261015",
    F_TMA2_TICK: "TMA_TICK_LTP_PUBLISH_20261015",
    F_TMA_LOOP: "STRATEGY_INDEPENDENCE_20261015",
    F_TMA2_LOOP: "STRATEGY_INDEPENDENCE_20261015",
    F_VET_LOOP: "STRATEGY_INDEPENDENCE_20261015",
}


def die(msg: str) -> None:
    print(f"\n[{FENCE}] ABORT: {msg}\nNothing was written.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Payloads
# ─────────────────────────────────────────────────────────────────────────────

def tick_engine_edits(tag: str, label: str):
    """(old, new) pairs for one tick engine. tag = TMA_TICK / TMA2_TICK."""
    edits = []

    # 1) constructor signature
    edits.append((
        "                 capture_dir: Optional[str] = None):\n",
        "                 capture_dir: Optional[str] = None,\n"
        "                 notify: Optional[Callable] = None,       # ── WS_SELFHEAL_20261021 ──\n"
        "                 log_tag: Optional[str] = None,\n"
        "                 alert_label: Optional[str] = None):\n",
    ))

    # 2) constructor state
    edits.append((
        "        self.last_spot_candle_ts: int = 0     # watchdog input\n",
        "        self.last_spot_candle_ts: int = 0     # watchdog input\n"
        "        # ── WS_SELFHEAL_20261021 ── zombie-socket self-heal state\n"
        f"        self._tag = log_tag or \"{tag}\"\n"
        f"        self._label = alert_label or \"{label}\"\n"
        "        self._notify_cb = notify\n"
        "        self._ws_connected = False\n"
        "        self._last_spot_tick_ts = 0.0       # wall clock of the last NIFTY tick\n"
        "        self._last_connect_ts = 0.0\n"
        "        self._last_wd_action_ts = 0.0\n"
        "        self._wd_strikes = 0\n"
        "        self._heal_pending = False\n"
        "        self.heal_count = 0\n",
    ))

    # 3) on_close lambda → method (flips the connected flag)
    edits.append((
        "        self._kws.on_close = lambda ws, code, reason: write_audit_log(\n"
        f"            f\"[{tag}][WS] closed code={{code}} reason={{reason}}\")\n",
        "        self._kws.on_close = self._on_ws_close     # ── WS_SELFHEAL_20261021 ──\n",
    ))

    # 4) start(): deferred connect + watchdog thread
    edits.append((
        "        self._kws.connect(threaded=True)\n"
        "        threading.Thread(target=self._boundary_timer, daemon=True,\n"
        "                         name=\"tma-minute-boundary\").start()\n",
        "        # ── WS_SELFHEAL_20261021 ── connect deferred to 09:13 IST + self-heal\n"
        "        threading.Thread(target=self._deferred_connect, daemon=True,\n"
        "                         name=f\"{self._tag.lower()}-connect\").start()\n"
        "        threading.Thread(target=self._boundary_timer, daemon=True,\n"
        "                         name=\"tma-minute-boundary\").start()\n"
        "        threading.Thread(target=self._ws_watchdog, daemon=True,\n"
        "                         name=f\"{self._tag.lower()}-ws-watchdog\").start()\n",
    ))

    # 5) on_connect bookkeeping
    edits.append((
        "    def _on_connect(self, ws, response):\n"
        "        tokens = list(self._builders.keys())\n",
        "    def _on_connect(self, ws, response):\n"
        "        self._ws_connected = True                 # ── WS_SELFHEAL_20261021 ──\n"
        "        self._last_connect_ts = time.time()\n"
        "        tokens = list(self._builders.keys())\n",
    ))

    # 6) spot tick timestamp (wall clock)
    edits.append((
        "                done = self._builders[tok].add_tick(float(ltp), epoch)\n",
        "                if tok == self._spot_token:\n"
        "                    self._last_spot_tick_ts = time.time()   # ── WS_SELFHEAL_20261021 ──\n"
        "                done = self._builders[tok].add_tick(float(ltp), epoch)\n",
    ))

    # 7) the self-heal block, inserted before the boundary timer
    edits.append((
        "    # ── the minute boundary: finalize quiet builders, drive coordinator ──\n",
        SELFHEAL_METHODS +
        "    # ── the minute boundary: finalize quiet builders, drive coordinator ──\n",
    ))
    return edits


SELFHEAL_METHODS = '''    # ── WS_SELFHEAL_20261021 BEGIN ───────────────────────────────────────
    # 2026-10-21: the trade-key sockets (TMA, TMA2, VET) connected at 08:30,
    # took the flat pre-market snapshot until 08:35, then went silent with no
    # close/error event. The coordinator alerted at 09:16:01 but nothing
    # healed it. (a) never open the socket long before the first real tick;
    # (b) spot silence in the spot session → drop the protocol so KiteTicker
    # auto-reconnects. sendClose, NOT kws.close() (stop_retry kills the
    # auto-reconnect). Fully wrapped — cannot reach the candle path.
    WD_SILENT_S = 120
    WD_POLL_S = 30
    WD_COOLDOWN_S = 90
    WD_BACKOFF_S = 1800
    CONNECT_AT_MIN = 9 * 60 + 13

    @staticmethod
    def _ist_min(now: float) -> int:
        return int((now + IST) % 86400) // 60

    @staticmethod
    def _session_open_ts(now: float) -> int:
        day = (int(now + IST) // 86400) * 86400 - IST
        return day + (9 * 60 + 15) * 60

    @classmethod
    def _spot_session_at(cls, now: float) -> bool:
        """09:15–15:15 IST, weekday, not an NSE holiday — the window in which
        a silent NIFTY index is a fault (CAS_NOTE: after 15:15 it is not)."""
        ist = datetime.utcfromtimestamp(now + IST)
        if ist.weekday() >= 5:
            return False
        try:
            from app.utils.market_hours import is_nse_holiday
            if is_nse_holiday(ist.date()):
                return False
        except Exception:
            pass
        m = cls._ist_min(now)
        return (9 * 60 + 15) <= m < (15 * 60 + 15)

    def _connect_due(self, now: float) -> bool:
        return self._ist_min(now) >= self.CONNECT_AT_MIN

    def _deferred_connect(self):
        try:
            announced = False
            while not self._stop and not self._connect_due(time.time()):
                if not announced:
                    m = self._ist_min(time.time())
                    write_audit_log(
                        f"[{self._tag}][WS] connect deferred to 09:13 IST (now "
                        f"{m // 60:02d}:{m % 60:02d}) — fresh socket at the open, "
                        f"not one idling unproven through the pre-open")
                    announced = True
                time.sleep(5)
            if self._stop:
                return
            self._kws.connect(threaded=True)
        except Exception as e:
            write_audit_log(f"[{self._tag}][WS][FATAL] connect raised: {e!r}")

    def _on_ws_close(self, ws, code, reason):
        self._ws_connected = False
        write_audit_log(f"[{self._tag}][WS] closed code={code} reason={reason}")

    def _wd_notify(self, msg: str, severity: str) -> None:
        if not self._notify_cb:
            return
        try:
            self._notify_cb(msg, severity)
        except Exception:
            pass

    def _wd_check(self, now: float) -> str:
        """One watchdog decision. Returns a status string (tests read it)."""
        if self._heal_pending and self._last_spot_tick_ts > self._last_wd_action_ts:
            took = int(self._last_spot_tick_ts - self._last_wd_action_ts)
            self._heal_pending = False
            self._wd_strikes = 0
            self.heal_count += 1
            write_audit_log(f"[{self._tag}][WS][HEALED] NIFTY ticks back {took}s "
                            f"after the watchdog reconnect")
            self._wd_notify(f"{self._label}: spot feed back {took}s after an "
                            f"automatic WebSocket reconnect", "info")
        if self._stop:
            return "stopped"
        if not self._ws_connected:
            return "not_connected"
        if not self._spot_session_at(now):
            return "off_session"
        base = max(self._last_spot_tick_ts, self._last_connect_ts,
                   float(self._session_open_ts(now)))
        silent = now - base
        if silent < self.WD_SILENT_S:
            return "ok"
        if now - self._last_wd_action_ts < self.WD_COOLDOWN_S:
            return "cooldown"
        if self._wd_strikes >= 3 and silent < self.WD_BACKOFF_S:
            return "backoff"
        self._wd_strikes += 1
        self._last_wd_action_ts = now
        self._heal_pending = True
        write_audit_log(f"[{self._tag}][WS][WATCHDOG] connected but no NIFTY tick "
                        f"for {int(silent)}s in the spot session — forcing "
                        f"reconnect (strike {self._wd_strikes})")
        if self._wd_strikes == 1:
            self._wd_notify(f"{self._label}: no NIFTY tick for {int(silent)}s — "
                            f"forcing a WebSocket reconnect (auto-heal)", "warning")
        elif self._wd_strikes == 3:
            self._wd_notify(f"{self._label}: 3 automatic reconnects did not bring "
                            f"the spot feed back — restart the app", "error")
        ws = getattr(self._kws, "ws", None)
        if ws is None:
            write_audit_log(f"[{self._tag}][WS][WATCHDOG] no protocol object — skipped")
            return "no_protocol"
        ws.sendClose(1000, "tick-watchdog")
        return "dropped"

    def _ws_watchdog(self):
        while not self._stop:
            try:
                time.sleep(self.WD_POLL_S)
                self._wd_check(time.time())
            except Exception as e:
                try:
                    write_audit_log(f"[{self._tag}][WS][WATCHDOG] loop error: {e!r}")
                except Exception:
                    pass
    # ── WS_SELFHEAL_20261021 END ─────────────────────────────────────────

'''


def coordinator_recovery_edit(label: str):
    return (
        "        if spot_candle is not None:\n"
        "            self.last_spot_seen = time.time()\n"
        "            self._spot_seen_count += 1\n"
        "            self._warned_quiet = False\n",
        "        if spot_candle is not None:\n"
        "            # ── WS_SELFHEAL_20261021 ── close the loop on our own alert\n"
        "            if self._warned_quiet:\n"
        "                _gap = int(time.time() - self.last_spot_seen) if self.last_spot_seen else -1\n"
        f"                write_audit_log(f\"[{label}][WATCHDOG] spot candles back after \"\n"
        "                                f\"{_gap}s — feed recovered\")\n"
        "                if self.notify:\n"
        "                    try:\n"
        f"                        self.notify(f\"{label}: spot candles back after {{_gap}}s \"\n"
        "                                    f\"— feed recovered\", severity=\"info\")\n"
        "                    except Exception:\n"
        "                        pass\n"
        "            self.last_spot_seen = time.time()\n"
        "            self._spot_seen_count += 1\n"
        "            self._warned_quiet = False\n",
    )


EDITS = {
    F_TMA_TICK: tick_engine_edits("TMA_TICK", "TMA"),
    F_TMA2_TICK: tick_engine_edits("TMA2_TICK", "TMA2"),
    F_TMA_LOOP: [
        coordinator_recovery_edit("TMA"),
        ("    engine = TMATickEngine(zerodha_manager, instruments_df,\n"
         "                           coord.on_minute, capture_dir=capture_dir)\n",
         "    engine = TMATickEngine(zerodha_manager, instruments_df,\n"
         "                           coord.on_minute, capture_dir=capture_dir,\n"
         "                           notify=notify)   # ── WS_SELFHEAL_20261021 ──\n"),
    ],
    F_TMA2_LOOP: [
        coordinator_recovery_edit("TMA2"),
        ("    engine = TMA2TickEngine(zerodha_manager, instruments_df,\n"
         "                           coord.on_minute, capture_dir=capture_dir)\n",
         "    engine = TMA2TickEngine(zerodha_manager, instruments_df,\n"
         "                           coord.on_minute, capture_dir=capture_dir,\n"
         "                           notify=notify)   # ── WS_SELFHEAL_20261021 ──\n"),
    ],
    F_VET_LOOP: [
        ("    tick = TMA2TickEngine(zerodha_manager, instruments_df, on_minute,\n"
         "                          capture_dir=None)\n",
         "    tick = TMA2TickEngine(zerodha_manager, instruments_df, on_minute,\n"
         "                          capture_dir=None,\n"
         "                          notify=notify, log_tag=\"VET_TICK\",   # ── WS_SELFHEAL_20261021 ──\n"
         "                          alert_label=\"VET_V1\")\n"),
        ("            gap = int(time.time()) - tick.last_spot_candle_ts\n",
         "            # ── WS_SELFHEAL_20261021 ── measure from the bar END, never\n"
         "            # from before 09:15 (the 2026-10-21 alert counted 41 min of\n"
         "            # pre-market as \"silence\")\n"
         "            _now_i = int(time.time())\n"
         "            _open = ((_now_i + IST) // 86400) * 86400 - IST + (9 * 60 + 15) * 60\n"
         "            gap = _now_i - max(int(tick.last_spot_candle_ts) + 60, _open)\n"),
        ("            elif gap <= 90:\n"
         "                silent_alerted = False\n",
         "            elif gap <= 90:\n"
         "                if silent_alerted:   # ── WS_SELFHEAL_20261021 ── close the loop\n"
         "                    write_audit_log(\"[VET] WATCHDOG: spot candles back — \"\n"
         "                                    \"feed recovered\")\n"
         "                    if notify:\n"
         "                        try:\n"
         "                            notify(\"VET_V1: spot candles back — feed recovered\",\n"
         "                                   \"info\")\n"
         "                        except Exception:\n"
         "                            pass\n"
         "                silent_alerted = False\n"),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural tests (real epoch clock — TSG_BANK_FIX1 rule)
# ─────────────────────────────────────────────────────────────────────────────

def ist_epoch(y, mo, d, h, mi, s=0.0) -> float:
    from datetime import datetime as _dt, timezone, timedelta
    return _dt(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp() + s


def run_tests(patched_tick_src: str, cls_name: str, vet_src: str) -> None:
    logs = []
    mod = types.ModuleType("ws_selfheal_under_test")
    exec(compile(patched_tick_src, "<patched tick engine>", "exec"), mod.__dict__)
    mod.write_audit_log = lambda m: logs.append(m)
    Engine = getattr(mod, cls_name)

    class FakeWS:
        def __init__(self):
            self.closes = 0

        def sendClose(self, code, reason):
            self.closes += 1

    class FakeKWS:
        def __init__(self):
            self.ws = FakeWS()

    sent = []

    def mk():
        e = Engine(None, None, lambda *a: None, capture_dir=None,
                   notify=lambda m, s: sent.append((s, m)))
        e._kws = FakeKWS()
        return e

    fails = []

    def check(name, cond):
        print(f"   {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            fails.append(name)

    WED = (2026, 10, 21)          # Wednesday — today's incident day
    SAT = (2026, 10, 24)

    # T1 deferred connect gate
    e = mk()
    check("T1a 08:30 IST → connect NOT due", not e._connect_due(ist_epoch(*WED, 8, 30)))
    check("T1b 09:12:59 → not due", not e._connect_due(ist_epoch(*WED, 9, 12, 59)))
    check("T1c 09:13:00 → due", e._connect_due(ist_epoch(*WED, 9, 13)))
    check("T1d mid-session restart 11:40 → due at once", e._connect_due(ist_epoch(*WED, 11, 40)))

    # T2 open: socket up 09:13 with snapshot tick, index quiet till 09:15 → NO drop
    e = mk()
    e._on_connect(types.SimpleNamespace(subscribe=lambda t: None,
                                        set_mode=lambda m, t: None,
                                        MODE_LTP="ltp"), None)
    check("T2 _on_connect flips connected flag", e._ws_connected is True)
    e._last_connect_ts = ist_epoch(*WED, 9, 13)
    e._last_spot_tick_ts = ist_epoch(*WED, 9, 13, 1)
    check("T2a 09:14:30 pre-session → off_session", e._wd_check(ist_epoch(*WED, 9, 14, 30)) == "off_session")
    check("T2b 09:16:30 (90s since 09:15 baseline) → ok, no false drop",
          e._wd_check(ist_epoch(*WED, 9, 16, 30)) == "ok")
    check("T2c no sendClose yet", e._kws.ws.closes == 0)

    # T3 today's zombie: nothing since 08:35, socket "connected" → must DROP (fail-closed opens)
    e = mk()
    sent.clear()
    e._ws_connected = True
    e._last_connect_ts = ist_epoch(*WED, 8, 30, 11)
    e._last_spot_tick_ts = ist_epoch(*WED, 8, 35, 40)
    check("T3a 09:16:01 → ok (61s of real session silence only)",
          e._wd_check(ist_epoch(*WED, 9, 16, 1)) == "ok")
    r = e._wd_check(ist_epoch(*WED, 9, 17, 1))
    check("T3b 09:17:01 → dropped", r == "dropped")
    check("T3c sendClose called once", e._kws.ws.closes == 1)
    check("T3d warning alert sent", any(s == "warning" for s, _ in sent))
    check("T3e +30s → cooldown", e._wd_check(ist_epoch(*WED, 9, 17, 31)) == "cooldown")

    # T4 heal: on_close → reconnect → ticks flow → HEALED + info alert + strikes reset
    e._on_ws_close(None, 1000, "tick-watchdog")
    check("T4a after close → not_connected", e._wd_check(ist_epoch(*WED, 9, 17, 2)) == "not_connected")
    e._ws_connected = True
    e._last_connect_ts = ist_epoch(*WED, 9, 17, 4)
    e._last_spot_tick_ts = ist_epoch(*WED, 9, 17, 5)
    r = e._wd_check(ist_epoch(*WED, 9, 17, 35))
    check("T4b heal logged, status ok", r == "ok" and e.heal_count == 1 and e._wd_strikes == 0)
    check("T4c info alert sent", any(s == "info" for s, _ in sent))
    check("T4d HEALED audit line", any("[HEALED]" in m for m in logs))

    # T5 ticks flowing → never drops
    e = mk()
    e._ws_connected = True
    t = ist_epoch(*WED, 11, 0)
    e._last_connect_ts = ist_epoch(*WED, 9, 13)
    e._last_spot_tick_ts = t - 5
    check("T5 live ticks 5s ago at 11:00 → ok", e._wd_check(t) == "ok")

    # T6 three fruitless drops → back-off + error alert
    e = mk()
    sent.clear()
    e._ws_connected = True
    t = ist_epoch(*WED, 10, 0)
    e._last_connect_ts = t - 500
    results = []
    for k in range(3):
        results.append(e._wd_check(t))
        e._last_connect_ts = t + 5          # reconnect succeeds, no ticks
        t += 130
    check("T6a three drops", results == ["dropped"] * 3)
    check("T6b 4th within 30 min → backoff", e._wd_check(t) == "backoff")
    check("T6c error alert on 3rd strike", any(s == "error" for s, _ in sent))

    # T7 off-session windows
    e = mk()
    e._ws_connected = True
    check("T7a 15:20 (index auction/CAS) → off_session", e._wd_check(ist_epoch(*WED, 15, 20)) == "off_session")
    check("T7b Saturday 11:00 → off_session", e._wd_check(ist_epoch(*SAT, 11, 0)) == "off_session")

    # T8 stop()
    e = mk()
    e._ws_connected = True
    e._stop = True
    check("T8 stopped engine never acts", e._wd_check(ist_epoch(*WED, 11, 0)) == "stopped")

    # T9 on_close/on_connect wiring present in the patched source
    check("T9a on_close bound to _on_ws_close", "self._kws.on_close = self._on_ws_close" in patched_tick_src)
    check("T9b connect goes through _deferred_connect only",
          patched_tick_src.count("self._kws.connect(threaded=True)") == 1
          and "target=self._deferred_connect" in patched_tick_src)

    # T10 VET gap formula (exec the exact patched lines)
    IST = 5 * 3600 + 30 * 60

    def vet_gap(now_i, last_ts):
        _open = ((now_i + IST) // 86400) * 86400 - IST + (9 * 60 + 15) * 60
        return now_i - max(int(last_ts) + 60, _open)

    check("T10 VET patched formula present", "max(int(tick.last_spot_candle_ts) + 60, _open)" in vet_src)
    g = vet_gap(int(ist_epoch(*WED, 9, 16, 1)), int(ist_epoch(*WED, 8, 35)))
    check(f"T10a today 09:16:01, last bar 08:35 → gap {g}s (not 2461), no alert", g == 61)
    g = vet_gap(int(ist_epoch(*WED, 9, 18, 1)), int(ist_epoch(*WED, 8, 35)))
    check(f"T10b real silence still caught: 09:18:01 → gap {g}s > 180", g > 180)
    g = vet_gap(int(ist_epoch(*WED, 9, 16, 0)), int(ist_epoch(*WED, 9, 13)))
    check(f"T10c 09:13 snapshot bar, poll at 09:16:00 → gap {g}s, no alert", g <= 180)
    g = vet_gap(int(ist_epoch(*WED, 11, 0, 30)), int(ist_epoch(*WED, 10, 59)))
    check(f"T10d healthy mid-session → gap {g}s ≤ 90 (re-arm)", g <= 90)

    if fails:
        die(f"{len(fails)} behavioural test(s) failed: {fails}")


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.path.isdir("backend/app"):
        die("run from the repo root (the folder that contains backend/)")

    trees = [t for t in TREES if os.path.isdir(t)]
    print(f"[{FENCE}] trees: {', '.join(trees)}")
    if "desktop/src-tauri/backend/app" not in trees:
        print("   note: desktop/src-tauri/backend/app not present — backend tree only")

    staged = {}          # path -> (old_text, new_text)
    applied_count = 0
    for tree in trees:
        for rel in FILES:
            path = os.path.join(tree, rel)
            if not os.path.isfile(path):
                die(f"missing file {path}")
            src = open(path, encoding="utf-8").read()
            if FENCE in src:
                applied_count += 1
                continue
            if PREREQ[rel] not in src:
                die(f"{path}: parent fence {PREREQ[rel]} not found — wrong working copy?")
            new = src
            for i, (old, rep) in enumerate(EDITS[rel], 1):
                n = new.count(old)
                if n != 1:
                    die(f"{path}: edit #{i} anchor found {n} times (need exactly 1)")
                new = new.replace(old, rep, 1)
            staged[path] = (src, new)

    total = len(trees) * len(FILES)
    if applied_count == total:
        print(f"[{FENCE}] already applied everywhere — nothing to do.")
        return
    # working-copy drift guard (git-tracked backend files only)
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--"] +
                             [f"backend/app/{f}" for f in FILES],
                             capture_output=True, text=True, timeout=20).stdout
        dirty = [ln for ln in out.splitlines() if ln.strip()]
        if dirty:
            die("uncommitted changes on files this script edits:\n   " +
                "\n   ".join(dirty) + "\n   commit/push or revert them first")
    except FileNotFoundError:
        print("   note: git not found — skipping working-copy check")

    if applied_count:
        die(f"mixed state: fence present in {applied_count}/{total} files — "
            f"restore the .bak-{FENCE} files or finish by hand")

    # py_compile gate
    for path, (_, new) in staged.items():
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
            tf.write(new)
            tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"{path} does not compile after patching:\n{e}")
        finally:
            os.unlink(tmp)
    print(f"[{FENCE}] py_compile OK on {len(staged)} file(s)")

    # behavioural tests on both engine classes
    print(f"[{FENCE}] behavioural tests — TMATickEngine")
    run_tests(staged[f"backend/app/{F_TMA_TICK}"][1], "TMATickEngine",
              staged[f"backend/app/{F_VET_LOOP}"][1])
    print(f"[{FENCE}] behavioural tests — TMA2TickEngine (TMA2 + VET)")
    run_tests(staged[f"backend/app/{F_TMA2_TICK}"][1], "TMA2TickEngine",
              staged[f"backend/app/{F_VET_LOOP}"][1])

    # all-or-nothing write: backups first, then files
    for path, (old, _) in staged.items():
        bak = f"{path}.bak-{FENCE}"
        with open(bak, "w", encoding="utf-8") as f:
            f.write(old)
    for path, (_, new) in staged.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"   wrote {path}")

    print(f"\n[{FENCE}] DONE — {len(staged)} file(s) patched, backups *.bak-{FENCE}.")
    print("Restart the app. Expected in today's log on the next arm:")
    print("   [TMA_TICK][WS] connect deferred to 09:13 IST ...")
    print("   [TMA_TICK][WS] connected — 123 tokens LTP mode   (at ~09:13)")
    print("   [VET_TICK][WS] ...   (VET now logs under its own tag)")


if __name__ == "__main__":
    main()
