#!/usr/bin/env python3
# apply_scalp_v5_live_eod_settings_20260826.py
#
# SCALP_V5 — configurable LIVE/PAPER EOD square-off time
# fence: SCALP_V5_LIVE_EOD_SETTINGS_20260826
#
# WHY THIS IS NOT JUST A SETTINGS FIELD: the live square-off is an APScheduler
# cron registered at hour=15, minute=25 in api_server.py. A config value alone
# would be written and never read — the field would silently do nothing. So
# this fence has two halves:
#
#   1. BACKEND — a per-minute WATCHDOG over 15:00–15:29 (`scalpv5_eod_tick`)
#      that reads `eod_squareoff_time` from SCALP_V5 config and fires the
#      EXISTING scalpv5_live_eod_job the first minute at/after that time, with
#      a per-day latch so it runs ONCE. The original 15:25 cron is left in
#      place as an untouched BACKSTOP: if the config is missing, unparseable,
#      or the watchdog itself fails, the current behaviour still happens. The
#      job is idempotent (a second call finds nothing open), so belt AND
#      braces is safe here — which is the right bias for a square-off.
#      Config changes take effect the SAME day, no restart.
#
#   2. SETTINGS UI — "EOD Square-off" field in the SCALP_V5 Trading Sessions
#      group, plus the key in DEFAULT_SCALP_V5_CONFIG and the backend default.
#
# DEFAULT "15:25" preserves today's live behaviour exactly. The backtest grid
# says 15:15 is better on net, drawdown AND worst year — set it in Settings
# once this ships, and the backtest/live pair finally agree.
#
# HOUSE RULE: <input type="time"> is unreliable under Tauri/WebKit → text
# input canonicalised on blur; unparseable input reverts to "15:25" (the
# backstop time) rather than an empty value that would disable the watchdog
# silently. Backend clamps to the 15:00–15:29 watchdog window and refuses
# anything outside it (audited), so a typo can never park the square-off
# outside market hours.
#
# DEPLOYMENT CLASS: live-shared path (scheduler + job + config). NON-TRADING
# DAY ship, and note today is a trading day. Dual-tree; rebuild via
# ./desktop/build-scalp.sh after verifying the diff.
#
# Idempotent. Run from the repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V5_LIVE_EOD_SETTINGS_20260826"
ROOT = Path(__file__).resolve().parent
JOB_REL = "app/jobs/scalpv5_live_eod.py"
API_REL = "app/api_server.py"
LD_REL = "app/config/strategy_loader.py"
SET_JSX = ROOT / "frontend" / "src" / "pages" / "Settings.jsx"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / JOB_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab):
    c = t.count(o)
    if c != 1:
        _die(f"anchor '{lab}' matched {c} times (want 1) — NOTHING written")
    return t.replace(o, n, 1)


# ═══ 1. the watchdog, appended to the existing job module ═════════════════

JOB_OLD = '''    # Reset the V5-local MTM re-entry latch for the next session (always; safe
    # even if the manager was unavailable).
    try:
        from app.engine.scalpv5.scalpv5_manager import reset_v5_risk_latch
        reset_v5_risk_latch()
    except Exception as e:
        write_audit_log(f"[V5][EOD][ERROR] risk-latch reset failed: {e!r}")'''

JOB_NEW = '''    # Reset the V5-local MTM re-entry latch for the next session (always; safe
    # even if the manager was unavailable).
    try:
        from app.engine.scalpv5.scalpv5_manager import reset_v5_risk_latch
        reset_v5_risk_latch()
    except Exception as e:
        write_audit_log(f"[V5][EOD][ERROR] risk-latch reset failed: {e!r}")


# ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ──────────────────────────────────────
# Config-driven square-off time. The 15:25 cron above stays registered as an
# untouched BACKSTOP; this watchdog runs every minute 15:00–15:29 and fires
# the same job the first minute at/after the configured time. Belt AND braces
# is deliberate for a square-off, and scalpv5_live_eod_job is idempotent — a
# second call simply finds nothing open.
_V5_EOD_WATCHDOG_DEFAULT = "15:25"      # == the legacy cron slot
_V5_EOD_WINDOW = (15 * 60, 15 * 60 + 29)   # minutes-from-midnight IST
_v5_eod_fired_on = {"day": None}           # per-day latch: fire ONCE


def _v5_eod_target_minute() -> int:
    """Configured square-off as minutes-from-IST-midnight, clamped to the
    watchdog window. Anything missing/unparseable/out-of-window falls back to
    the legacy 15:25 slot (audited) — a typo must never park the square-off
    outside market hours, and must never disable it."""
    raw = _V5_EOD_WATCHDOG_DEFAULT
    try:
        from app.config.strategy_loader import load_strategy_config
        raw = str((load_strategy_config("SCALP_V5") or {}).get(
            "eod_squareoff_time", _V5_EOD_WATCHDOG_DEFAULT) or
            _V5_EOD_WATCHDOG_DEFAULT).strip()
    except Exception as e:
        write_audit_log(f"[V5][EOD][WATCHDOG] config read failed ({e!r}) — "
                        f"using {_V5_EOD_WATCHDOG_DEFAULT}")
    try:
        hh, mm = raw.split(":")
        mins = int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        write_audit_log(f"[V5][EOD][WATCHDOG] unparseable eod_squareoff_time "
                        f"{raw!r} — using {_V5_EOD_WATCHDOG_DEFAULT}")
        return 15 * 60 + 25
    if not (_V5_EOD_WINDOW[0] <= mins <= _V5_EOD_WINDOW[1]):
        write_audit_log(f"[V5][EOD][WATCHDOG] eod_squareoff_time {raw!r} is "
                        f"outside the 15:00–15:29 watchdog window — using "
                        f"{_V5_EOD_WATCHDOG_DEFAULT}")
        return 15 * 60 + 25
    return mins


def scalpv5_eod_tick():
    """Per-minute 15:00–15:29 watchdog. No-op until the configured minute."""
    from datetime import datetime, timedelta, timezone
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        return
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    today = now.date().isoformat()
    if _v5_eod_fired_on["day"] == today:
        return
    if (now.hour * 60 + now.minute) < _v5_eod_target_minute():
        return
    _v5_eod_fired_on["day"] = today          # latch BEFORE running: a raising
    write_audit_log(                         # job must not re-fire every minute
        f"[V5][EOD][WATCHDOG] firing square-off at {now:%H:%M} IST "
        f"(configured {_v5_eod_target_minute() // 60:02d}:"
        f"{_v5_eod_target_minute() % 60:02d})")
    scalpv5_live_eod_job()'''

# ═══ 2. scheduler registration (backstop kept, watchdog added) ════════════

API_OLD = '''        # ── SCALP_V5 BEGIN ──
        scheduler.add_job(
            scalpv5_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalpv5_live_eod_squareoff", replace_existing=True,
        )
        # ── SCALP_V5 END ──'''
API_NEW = '''        # ── SCALP_V5 BEGIN ──
        scheduler.add_job(
            scalpv5_live_eod_job, trigger="cron", hour=15, minute=25,
            id="scalpv5_live_eod_squareoff", replace_existing=True,
        )
        # ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ── config-driven square-off.
        # The cron above is left registered as an unconditional BACKSTOP; this
        # watchdog fires the same (idempotent) job at the Settings-configured
        # minute, so changing the time needs no restart. If the watchdog dies,
        # 15:25 still happens — the failure mode is "squared off later", never
        # "carried overnight".
        scheduler.add_job(
            scalpv5_eod_tick, trigger="cron", hour=15, minute="0-29",
            id="scalpv5_eod_watchdog", replace_existing=True,
        )
        # ── SCALP_V5 END ──'''

API_IMP_OLD = "from app.jobs.scalpv5_live_eod import scalpv5_live_eod_job     # ← NEW (SCALP_V5)"
API_IMP_NEW = ("from app.jobs.scalpv5_live_eod import (scalpv5_live_eod_job,     # ← NEW (SCALP_V5)\n"
               "                                       scalpv5_eod_tick)   # ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ──")

# ═══ 3. backend default ═══════════════════════════════════════════════════

LD_OLD = '''    "SCALP_V5": {
        "trade_execution_mode": "PAPER",'''
LD_NEW = '''    "SCALP_V5": {
        "trade_execution_mode": "PAPER",

        # ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ── live/paper square-off time
        # (IST, 15:00–15:29). "15:25" == the legacy cron slot. The backtest
        # grid favours "15:15" on net, drawdown AND worst year.
        "eod_squareoff_time": "15:25",'''

# ═══ 4. Settings UI ═══════════════════════════════════════════════════════

S1_OLD = '''const DEFAULT_SCALP_V5_CONFIG = {
  trade_execution_mode: "PAPER",'''
S1_NEW = '''const DEFAULT_SCALP_V5_CONFIG = {
  trade_execution_mode: "PAPER",
  eod_squareoff_time:   "15:25",   // ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ──'''

S2_OLD = '''                <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                  <TimeRange
                    startValue={scalpV5Config.session.secondary.start}
                    endValue={scalpV5Config.session.secondary.end}
                    disabled={!scalpV5Config.session.secondary.enabled}
                    onStartChange={(e) => updateScalpV5(["session", "secondary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV5(["session", "secondary", "end"],   e.target.value)} />
                </Field>
              </Group>'''
S2_NEW = '''                <Field label="Secondary Times" helper="Active only when secondary is enabled" indent>
                  <TimeRange
                    startValue={scalpV5Config.session.secondary.start}
                    endValue={scalpV5Config.session.secondary.end}
                    disabled={!scalpV5Config.session.secondary.enabled}
                    onStartChange={(e) => updateScalpV5(["session", "secondary", "start"], e.target.value)}
                    onEndChange={(e)   => updateScalpV5(["session", "secondary", "end"],   e.target.value)} />
                </Field>
                {/* ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ── text input, not
                    type="time" (unreliable under Tauri/WebKit); canonicalised
                    on blur, unparseable reverts to the 15:25 backstop. */}
                <Field label="EOD Square-off" helper="IST, 15:00–15:29. Open positions are closed at this time. Backtest favours 15:15.">
                  <Input type="text" value={scalpV5Config.eod_squareoff_time ?? "15:25"}
                    onChange={(e) => updateScalpV5(["eod_squareoff_time"], e.target.value)}
                    onBlur={(e) => updateScalpV5(["eod_squareoff_time"], canonEodHm(e.target.value))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </Group>'''

S3_OLD = "const DEFAULT_SCALP_V5_CONFIG = {"
S3_NEW = '''// ── SCALP_V5_LIVE_EOD_SETTINGS_20260826 ── "1515" / "15.15" / " 15:15 " all
// canonicalise to "15:15"; anything unparseable or outside the watchdog
// window reverts to "15:25" (the backstop), never to an empty value.
function canonEodHm(raw) {
  const s = String(raw ?? "").trim();
  const m = s.match(/^(\\d{1,2})\\s*[:.]?\\s*(\\d{2})$/);
  if (!m) return "15:25";
  const h = Number(m[1]), mi = Number(m[2]);
  const mins = h * 60 + mi;
  if (!(h >= 0 && h <= 23 && mi >= 0 && mi <= 59)) return "15:25";
  if (mins < 15 * 60 || mins > 15 * 60 + 29) return "15:25";
  return `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
}

const DEFAULT_SCALP_V5_CONFIG = {'''


def main():
    if not (ROOT / "backend" / JOB_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        jp, ap, lp = tree / JOB_REL, tree / API_REL, tree / LD_REL
        jt, at, lt = jp.read_text(), ap.read_text(), lp.read_text()
        for p, t in ((jp, jt), (ap, at), (lp, lt)):
            if FENCE in t:
                _die(f"fence {FENCE} already present in {p} — already applied")
        jt = _ro(jt, JOB_OLD, JOB_NEW, f"{tree.name}:JOB")
        at = _ro(at, API_IMP_OLD, API_IMP_NEW, f"{tree.name}:API_IMPORT")
        at = _ro(at, API_OLD, API_NEW, f"{tree.name}:API_CRON")
        lt = _ro(lt, LD_OLD, LD_NEW, f"{tree.name}:LOADER")
        staged += [(jp, jt), (ap, at), (lp, lt)]
    st = SET_JSX.read_text()
    if FENCE in st:
        _die(f"fence {FENCE} already present in Settings.jsx")
    st = _ro(st, S3_OLD, S3_NEW, "Settings:HELPER")
    st = _ro(st, S1_OLD, S1_NEW, "Settings:DEFAULT")
    st = _ro(st, S2_OLD, S2_NEW, "Settings:FIELD")
    staged.append((SET_JSX, st))
    for p, t in staged:
        if p.suffix == ".py":
            try:
                compile(t, str(p), "exec")
            except SyntaxError as e:
                _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied.")
    print()
    print("DEPLOYMENT: live-shared path (scheduler + EOD job + config).")
    print("NON-TRADING-DAY ship. Verify the dual-tree diff, then rebuild.")
    print()
    print("AFTER DEPLOY — Settings > Scalp V5 > Trading Sessions:")
    print("  EOD Square-off : 15:15   (backtest-favoured; 15:25 = legacy)")
    print("  Primary Session: 10:15 to 14:30   (the sealed grid cell)")
    print()
    print("FIRST-SESSION ACCEPTANCE: the audit log must show a single")
    print("  [V5][EOD][WATCHDOG] firing square-off at 15:15 IST")
    print("line, and the 15:25 backstop must then report nothing open.")


if __name__ == "__main__":
    main()
