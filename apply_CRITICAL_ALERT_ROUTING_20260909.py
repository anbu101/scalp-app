#!/usr/bin/env python3
"""
apply_CRITICAL_ALERT_ROUTING_20260909.py — FENCE: CRITICAL_ALERT_ROUTING_20260909

Rule (fleet-wide, from 2026-09-09):
  1. A CRITICAL alert for a strategy running in PAPER never goes to Telegram.
  2. EVERY critical alert lands in the in-app bell, paper or live, so a user
     with no Telegram configured still sees it (today's Scala skew alert went
     to the phone and never appeared in-app).

Why centrally: notify_critical() has 55 call sites in 19 files. Some pair it
with record_alert (IC, TMA, TSG…), others only page Telegram (GTT monitors,
kill switch, EOD safety). Patching each site is the surface that breeds a
missed one; every alert already funnels through notify_critical with a
strategy_id, so the gate and the mirror live there.

What:
  app/event_bus/inapp_events.py
    - record_event() stamps the last in-app ALERT time per strategy_id.
    - recent_alert_within(strategy_id, seconds) reads it.
  app/api/telegram_api.py  notify_critical()
    - mode: alert_data["mode"] if the caller passes one, else the strategy's
      trade_execution_mode from config. Read failure → "LIVE" (loud, never
      silent). No strategy_id → system-wide → always Telegram (relay/DB
      emergencies must reach the phone).
    - in-app mirror: record_alert("CRITICAL", …) unless an in-app alert for
      the same strategy fired within the last 3 s — paired call sites always
      record_alert immediately before notify_critical, so they don't ring
      twice; unpaired sites now ring once instead of never.
    - Telegram fan-out only when mode != PAPER. Audit line either way.

Safety: fence check, py_compile gate, behavioural tests on the patched modules
(paper → in-app only; live → in-app + Telegram; paired site → one bell;
unpaired → one bell; system-wide → Telegram; config read failure → loud),
.bak backups, staged write, dual-tree mirror. Backend-only → rebuild.

Run from the repo root:  python3 apply_CRITICAL_ALERT_ROUTING_20260909.py
"""
import importlib.util, os, py_compile, shutil, sys, tempfile, time

FENCE = "CRITICAL_ALERT_ROUTING_20260909"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"inapp": "app/event_bus/inapp_events.py", "tg": "app/api/telegram_api.py"}
PATH = {k: os.path.join(BACKEND, v) for k, v in REL.items()}
if not os.path.isfile(PATH["tg"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


for k, p in PATH.items():
    if FENCE in read(p):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")

# ── inapp_events: last-alert stamp + reader ────────────────────────────
ia = read(PATH["inapp"])
ia = sub1(ia,
    '_lock = threading.Lock()\n_events: List[Dict] = []\n',
    '_lock = threading.Lock()\n_events: List[Dict] = []\n'
    '# ── CRITICAL_ALERT_ROUTING_20260909 ── last in-app ALERT time per strategy\n'
    '# ("" = system-wide). Lets notify_critical mirror to the bell without\n'
    '# double-ringing call sites that already record_alert() first.\n'
    '_last_alert_ts: Dict[str, float] = {}\n'
    '\n'
    '\n'
    'def recent_alert_within(strategy_id: str, seconds: float) -> bool:\n'
    '    """True if an in-app ALERT for strategy_id was recorded < seconds ago."""\n'
    '    try:\n'
    '        with _lock:\n'
    '            t = _last_alert_ts.get(strategy_id or "")\n'
    '        return t is not None and (time.time() - t) < seconds\n'
    '    except Exception:\n'
    '        return False\n',
    "inapp globals")
ia = sub1(ia,
    '            _next_id += 1\n'
    '            _events.append(evt)\n',
    '            _next_id += 1\n'
    '            _events.append(evt)\n'
    '            if evt["event_type"] == EVENT_ALERT:        # ── CRITICAL_ALERT_ROUTING_20260909 ──\n'
    '                _last_alert_ts[evt["strategy_id"]] = evt["ts"]\n',
    "inapp stamp")

# ── telegram_api.notify_critical ───────────────────────────────────────
tg = read(PATH["tg"])
tg = sub1(tg,
    'from app.event_bus.inapp_events import (\n'
    '    record_event,\n',
    'from app.event_bus.audit_logger import write_audit_log   # ── CRITICAL_ALERT_ROUTING_20260909 ──\n'
    'from app.event_bus.inapp_events import (\n'
    '    record_event,\n',
    "telegram_api audit import")
tg = sub1(tg,
    '    severity = alert_data.get("severity", "error")\n'
    '    emoji = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "🚨")\n'
    '    strategy_id = alert_data.get("strategy_id")  # may be None\n'
    '\n'
    '    strat_line = f"\\nStrategy: {codename(strategy_id)}" if strategy_id else ""\n',
    '    severity = alert_data.get("severity", "error")\n'
    '    emoji = {"error": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "🚨")\n'
    '    strategy_id = alert_data.get("strategy_id")  # may be None\n'
    '\n'
    '    # ── CRITICAL_ALERT_ROUTING_20260909 BEGIN ─────────────────────────\n'
    '    # (1) resolve mode: caller override → strategy config → LIVE on any\n'
    '    #     read failure (an alert path must fail LOUD, never silent).\n'
    '    #     No strategy_id = system-wide = always LIVE.\n'
    '    _mode = str(alert_data.get("mode") or "").upper()\n'
    '    if not _mode:\n'
    '        if strategy_id:\n'
    '            try:\n'
    '                from app.config.strategy_loader import load_strategy_config\n'
    '                _mode = str((load_strategy_config(strategy_id) or {})\n'
    '                            .get("trade_execution_mode") or "LIVE").upper()\n'
    '            except Exception as _me:\n'
    '                write_audit_log(f"[TELEGRAM][CRITICAL] mode read failed for "\n'
    '                                f"{strategy_id} ({_me!r}) — treating as LIVE")\n'
    '                _mode = "LIVE"\n'
    '        else:\n'
    '            _mode = "LIVE"\n'
    '    # (2) in-app mirror — every critical alert reaches the bell, paper or\n'
    '    #     live, Telegram configured or not. Skipped only when the caller\n'
    '    #     just recorded its own in-app alert (paired sites: IC/TMA/TSG…).\n'
    '    try:\n'
    '        from app.event_bus.inapp_events import record_alert, recent_alert_within\n'
    '        if not recent_alert_within(strategy_id or "", 3.0):\n'
    '            record_alert("CRITICAL", str(alert_data.get("message", "")),\n'
    '                         severity=severity if severity in ("error", "warning", "info") else "error",\n'
    '                         strategy_id=strategy_id or "", mode=_mode.lower())\n'
    '    except Exception as _ie:\n'
    '        write_audit_log(f"[TELEGRAM][CRITICAL][INAPP_FAIL] {_ie!r}")\n'
    '    # (3) PAPER never pages the phone.\n'
    '    if _mode == "PAPER":\n'
    '        write_audit_log(f"[TELEGRAM][CRITICAL] {strategy_id} is PAPER — in-app "\n'
    '                        f"only, Telegram suppressed")\n'
    '        return\n'
    '    # ── CRITICAL_ALERT_ROUTING_20260909 END ───────────────────────────\n'
    '\n'
    '    strat_line = f"\\nStrategy: {codename(strategy_id)}" if strategy_id else ""\n',
    "notify_critical body")
if "write_audit_log" not in tg.split("def notify_critical")[0]:
    die("telegram_api has no write_audit_log import above notify_critical")

NEW = {"inapp": ia, "tg": tg}

# ── py_compile gate ────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
TMPF = {}
for k, text in NEW.items():
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    TMPF[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK")

# ── behavioural tests ──────────────────────────────────────────────────
sys.path.insert(0, BACKEND)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m
    spec.loader.exec_module(m); return m
IA = load("app.event_bus.inapp_events", TMPF["inapp"])
TG = load("app.api.telegram_api", TMPF["tg"])
import app.config.strategy_loader as SL

sent, audit = [], []
TG._fanout = lambda key, msg, **k: sent.append((key, msg, k.get("strategy_id")))
TG.write_audit_log = lambda s: audit.append(s)
MODES = {}
SL.load_strategy_config = lambda sid: {"trade_execution_mode": MODES[sid]} if sid in MODES else (_ for _ in ()).throw(KeyError(sid))
def bell(sid=None):
    return [e for e in IA._events if e["event_type"] == IA.EVENT_ALERT and (sid is None or e["strategy_id"] == sid)]
def reset():
    sent.clear(); IA._events.clear(); IA._last_alert_ts.clear()

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)

reset(); MODES.update({"SCALP_V1": "PAPER", "TSG_V1": "LIVE", "IC_V1": "PAPER"})
TG.notify_critical({"message": "skew gate error", "severity": "error", "strategy_id": "SCALP_V1"})
b = bell("SCALP_V1")
check("paper strategy → in-app bell (mode=paper), NO Telegram",
      len(b) == 1 and b[0]["mode"] == "paper" and b[0]["code"] == "CRITICAL" and not sent, f"bell={len(b)} sent={sent}")
check("suppression audited", any("PAPER" in a and "suppressed" in a for a in audit))

reset()
TG.notify_critical({"message": "entry failed at L2", "severity": "error", "strategy_id": "TSG_V1"})
check("live strategy → in-app bell AND Telegram", len(bell("TSG_V1")) == 1 and len(sent) == 1 and sent[0][2] == "TSG_V1")
check("telegram text still carries codename + message", "Tigris" in sent[0][1] and "entry failed at L2" in sent[0][1], sent[0][1][:80])

reset()   # paired site: record_alert then notify_critical (IC/TMA/TSG pattern)
IA.record_alert("IC_UNWOUND", "group unwound", severity="error", strategy_id="TSG_V1", mode="live")
TG.notify_critical({"message": "entry FAILED at L3; unwound", "severity": "error", "strategy_id": "TSG_V1"})
check("paired site → ONE bell entry (no double ring)", len(bell("TSG_V1")) == 1 and len(sent) == 1, f"bell={len(bell('TSG_V1'))}")

reset()   # unpaired site (GTT monitor pattern) → now rings once
TG.notify_critical({"message": "GTT placement failed", "severity": "error", "strategy_id": "TSG_V1"})
check("unpaired site → one bell entry (was zero)", len(bell("TSG_V1")) == 1)

reset()   # dedupe is per strategy, not global
IA.record_alert("X", "other strategy's alert", severity="error", strategy_id="IC_V1", mode="paper")
TG.notify_critical({"message": "tsg thing", "severity": "error", "strategy_id": "TSG_V1"})
check("dedupe window is per-strategy", len(bell("TSG_V1")) == 1)

reset()   # stale window → mirror again
IA.record_alert("X", "old", severity="error", strategy_id="TSG_V1", mode="live")
IA._last_alert_ts["TSG_V1"] -= 10
TG.notify_critical({"message": "new", "severity": "error", "strategy_id": "TSG_V1"})
check("alert >3s after last in-app alert → mirrored", len(bell("TSG_V1")) == 2)

reset()   # system-wide (no strategy_id) → always Telegram + bell
TG.notify_critical({"message": "relay down", "severity": "error"})
check("system-wide → Telegram + bell", len(sent) == 1 and len(bell("")) == 1 and bell("")[0]["mode"] == "live")

reset()   # config read failure → LOUD
TG.notify_critical({"message": "x", "severity": "error", "strategy_id": "UNKNOWN_V9"})
check("unknown strategy / config read failure → treated as LIVE (Telegram sent)", len(sent) == 1 and len(bell("UNKNOWN_V9")) == 1)
check("read failure audited", any("mode read failed" in a for a in audit))

reset()   # caller override wins over config
TG.notify_critical({"message": "x", "severity": "error", "strategy_id": "SCALP_V1", "mode": "live"})
check("explicit mode='live' overrides PAPER config", len(sent) == 1)

reset()   # OFF is not PAPER → still loud
MODES["HA_V1"] = "OFF"
TG.notify_critical({"message": "x", "severity": "error", "strategy_id": "HA_V1"})
check("OFF strategy alert still pages (only PAPER is silenced)", len(sent) == 1)

reset()   # in-app failure never blocks Telegram
IA.record_alert = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bell broken"))
TG.notify_critical({"message": "x", "severity": "error", "strategy_id": "TSG_V1"})
check("in-app failure → audited, Telegram still sent", len(sent) == 1 and any("INAPP_FAIL" in a for a in audit))

if FAILS:
    die(f"tests failed: {FAILS}")
print("behavioural tests: OK")

# ── write ───────────────────────────────────────────────────────────────
for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
for k, p in PATH.items():
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 2 files (+ .bak backups)")
if os.path.isdir(DUAL):
    for k, rel in REL.items():
        shutil.copy2(PATH[k], os.path.join(DUAL, rel))
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
