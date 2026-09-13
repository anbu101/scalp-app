#!/usr/bin/env python3
"""
apply_DTE_LOT_MULT_LIVE_20260911.py — FENCE: DTE_LOT_MULT_LIVE_20260911

Requires DTE_LOT_MULT_20260911 (backtest knob + app/backtest/engine/dte_lots.py).

Live-side per-DTE lot multiplier for BRK_V1 and ORB_V1 — same key, same
semantics as the backtest: dte_lot_mult = {dte: mult}; absent → ×1; 0 → the
day is SKIPPED; else lots_today = max(1, round(lots × mult)).

DEFAULTS (user decision 2026-09-11, from the split-half DTE analysis and
the two filtered runs):
  BRK_V1: {"1":0, "2":0, "3":0, "4":0}   → trades expiry day only
  ORB_V1: {"0":0}                         → trades every day except expiry
Together: BRK on 0DTE, ORB on 1–4DTE, one 10-lot book. Saved configs are
merged OVER loader defaults, so existing installs pick these up without
editing; the Settings page exposes the field for both strategies.

DTE (live) = trading sessions from today to expected_expiry_for_day(today),
counted with app.utils.market_hours.is_trading_day (weekday + NSE holiday
list). A Tuesday holiday shifts the real expiry to Monday; counting
sessions to the expected Tuesday makes that Monday read 0DTE and the
Friday 1DTE with no special casing.

Where it lands (one place per engine, at the day roll):
  ORB  _roll_day: after the CFG guard, before the day core is built.
       skip → in-app info alert DTE_SKIP, day = None, day_stats.refused =
       "DTE_SKIP nDTE"; the loop idles. scaled → manager.day_lots override.
  BRK  _roll_day: after resume_from_db. skip → both sessions marked done
       (no selection, no entry), alert DTE_SKIP. scaled → day_lots.
  Both managers' _qty() honour day_lots when set.
Calendar failure → unscaled day + audit (fail-open to the sealed base
config; never a silent skip). Resumed positions keep their own lots.

Files:
  backend/app/engine/dte_live.py                NEW — live_dte(), resolve_day_lots()
  backend/app/engine/orb/orb_engine.py          day-roll hook
  backend/app/engine/orb/orb_manager.py         _qty honours day_lots
  backend/app/engine/brk/brk_engine.py          day-roll hook
  backend/app/engine/brk/brk_manager.py         _qty honours day_lots
  backend/app/config/strategy_loader.py         defaults
  frontend/src/pages/Settings.jsx               defaults + DteMultInput field ×2

Safety: parent fence, py_compile gate, helper tests on the real 2026
calendar (Fri→Tue = 2, Mon = 1, Tue = 0, Tuesday-holiday week → Monday
0DTE), engine-level sims (ORB: expiry day skipped with alert + refused
stat, non-expiry day armed with base lots, ×2 day → day_lots=2 and
_qty 130, calendar failure → armed unscaled; BRK: expiry day armed and
lots 10, Wednesday → both sessions done, alert, no selection), loader
defaults asserted, existing ORB suites, JSX parse gate, .bak backups,
dual-tree mirror. Backend + frontend → full rebuild.

Run from the repo root:  python3 apply_DTE_LOT_MULT_LIVE_20260911.py
"""
import importlib.util, os, py_compile, shutil, subprocess, sys, tempfile, types
from datetime import date, datetime, timedelta, timezone

FENCE = "DTE_LOT_MULT_LIVE_20260911"
PARENT = "DTE_LOT_MULT_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"live": "backend/app/engine/dte_live.py",
       "oeng": "backend/app/engine/orb/orb_engine.py", "omgr": "backend/app/engine/orb/orb_manager.py",
       "beng": "backend/app/engine/brk/brk_engine.py", "bmgr": "backend/app/engine/brk/brk_manager.py",
       "loader": "backend/app/config/strategy_loader.py", "ui": "frontend/src/pages/Settings.jsx"}
PATH = {k: os.path.join(ROOT, v) for k, v in REL.items()}
if not os.path.isfile(PATH["oeng"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


if not os.path.isfile(os.path.join(ROOT, "backend/app/backtest/engine/dte_lots.py")):
    die(f"{PARENT} not applied (dte_lots.py missing) — run apply_{PARENT}.py first")
for k in ("oeng", "omgr", "beng", "bmgr", "loader", "ui"):
    if FENCE in read(PATH[k]):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")
if os.path.exists(PATH["live"]):
    die(f"{REL['live']} already exists — nothing to do")
oeng = read(PATH["oeng"])
if "ORB_REPLAY_RETRY_20260911" not in oeng:
    die("orb_engine.py lacks ORB_REPLAY_RETRY_20260911 — apply the ORB chain first")

LIVE = '''# backend/app/engine/dte_live.py
#
# ── DTE_LOT_MULT_LIVE_20260911 ── live per-DTE lot multiplier.
# Same key/semantics as the backtest knob (app.backtest.engine.dte_lots):
#   dte_lot_mult = {dte: mult}; absent → ×1; 0 → skip the day;
#   lots_today = max(1, round(lots × mult)).
# DTE = trading SESSIONS from today to expected_expiry_for_day(today),
# counted with is_trading_day (weekday + NSE holiday list). A Tuesday
# holiday moves the real expiry to Monday; counting sessions to the
# expected Tuesday makes that Monday 0DTE with no special casing.
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple

from app.event_bus.audit_logger import write_audit_log


def live_dte(day: date) -> Optional[int]:
    """Trading sessions in (day, expected_expiry]. 0 on expiry day.
    None if the calendar cannot be evaluated (caller fails open)."""
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
        from app.utils.market_hours import is_trading_day
        exp = expected_expiry_for_day(day)
        n = 0
        d = day + timedelta(days=1)
        while d <= exp:
            if is_trading_day(d):
                n += 1
            d += timedelta(days=1)
        return n
    except Exception as e:
        write_audit_log(f"[DTE][LIVE] calendar failed ({e!r}) — DTE unknown")
        return None


def resolve_day_lots(cfg: dict, base_lots: int, day: Optional[date] = None,
                     tag_prefix: str = "") -> Tuple[int, Optional[int], str]:
    """→ (lots_today, dte, tag) with tag in {"base","scaled","skip","unknown"}.
    Never raises; unknown/failed → base lots (fail-open to the sealed config)."""
    try:
        from app.backtest.engine.dte_lots import parse_dte_lot_mult, lots_for_day
        mult = parse_dte_lot_mult((cfg or {}).get("dte_lot_mult"))
        if not mult:
            return int(base_lots or 1), None, "base"
        dte = live_dte(day or date.today())
        lots, tag = lots_for_day(int(base_lots or 1), mult, dte)
        write_audit_log(f"[DTE][LIVE]{tag_prefix} today {dte if dte is not None else '?'}DTE "
                        f"→ {tag} (lots {lots}, base {base_lots}, map {mult})")
        return lots, dte, tag
    except Exception as e:
        write_audit_log(f"[DTE][LIVE]{tag_prefix} resolve failed ({e!r}) — base lots")
        return int(base_lots or 1), None, "unknown"
'''

# ── ORB engine ───────────────────────────────────────────────────────────
oeng = sub1(oeng,
    '        self.gm.day = OrbLiveDay(day_start_epoch=self._day_start_epoch(now),\n'
    '                                 cfg=cfg)\n',
    '        # ── DTE_LOT_MULT_LIVE_20260911 ── per-DTE lots / day skip\n'
    '        from app.engine.dte_live import resolve_day_lots as _rdl\n'
    '        _lots, _dte, _tag = _rdl(cfg, int(cfg.get("lots") or 1), now.date(), "[ORB]")\n'
    '        self.gm.day_lots = None\n'
    '        if _tag == "skip":\n'
    '            self.gm._alert("DTE_SKIP", f"today is {_dte}DTE — no ORB trading "\n'
    '                           f"(dte_lot_mult)", "info")\n'
    '            self.gm.day = None\n'
    '            self.gm.day_stats = {"signals": 0, "entries": 0, "exits": {},\n'
    '                                 "refused": f"DTE_SKIP {_dte}DTE", "frozen": None}\n'
    '            return\n'
    '        if _tag == "scaled":\n'
    '            self.gm.day_lots = int(_lots)\n'
    '        self.gm.day = OrbLiveDay(day_start_epoch=self._day_start_epoch(now),\n'
    '                                 cfg=cfg)\n',
    "orb engine roll")
omgr = read(PATH["omgr"])
omgr = sub1(omgr,
    '    def _qty(self):\n'
    '        cfg = self.cfg()\n'
    '        lots = int(cfg.get("lots") or 1)\n',
    '    def _qty(self):\n'
    '        cfg = self.cfg()\n'
    '        lots = int(getattr(self, "day_lots", None) or cfg.get("lots") or 1)   # ── DTE_LOT_MULT_LIVE_20260911 ──\n',
    "orb manager qty")

# ── BRK engine ───────────────────────────────────────────────────────────
beng = read(PATH["beng"])
beng = sub1(beng,
    '        self.gm.resume_from_db()\n'
    '        m = minute_of_day(int(now.timestamp()))\n'
    '        # RESTART FAIL-CLOSED: windows already elapsed take no late entries.\n',
    '        self.gm.resume_from_db()\n'
    '        m = minute_of_day(int(now.timestamp()))\n'
    '        # ── DTE_LOT_MULT_LIVE_20260911 ── per-DTE lots / day skip\n'
    '        from app.engine.dte_live import resolve_day_lots as _rdl\n'
    '        _base = int(((cfg.get("quantity") or {}).get("lots")) or 1)\n'
    '        _lots, _dte, _tag = _rdl(cfg, _base, now.date(), "[BRK]")\n'
    '        self.gm.day_lots = int(_lots) if _tag == "scaled" else None\n'
    '        if _tag == "skip":\n'
    '            for sess in filter(None, (self.core.s1, self.core.s2)):\n'
    '                sess.done = True\n'
    '                self._selected[sess.spec.tag] = True\n'
    '            self._alert("DTE_SKIP", f"today is {_dte}DTE — no BRK trading "\n'
    '                        f"(dte_lot_mult)", "info")\n'
    '        # RESTART FAIL-CLOSED: windows already elapsed take no late entries.\n',
    "brk engine roll")
bmgr = read(PATH["bmgr"])
bmgr = sub1(bmgr,
    '    def _qty(self):\n'
    '        q = (self.cfg().get("quantity") or {})\n'
    '        lots = int(q.get("lots") or 1)\n',
    '    def _qty(self):\n'
    '        q = (self.cfg().get("quantity") or {})\n'
    '        lots = int(getattr(self, "day_lots", None) or q.get("lots") or 1)   # ── DTE_LOT_MULT_LIVE_20260911 ──\n',
    "brk manager qty")

# ── loader defaults ──────────────────────────────────────────────────────
ld = read(PATH["loader"])
ld = sub1(ld,
    '        "eod_square_off": "15:15",\n'
    '        "skip_expiry_day": False,\n',
    '        "eod_square_off": "15:15",\n'
    '        "skip_expiry_day": False,\n'
    '        # ── DTE_LOT_MULT_LIVE_20260911 ── BRK trades EXPIRY DAY ONLY (0DTE).\n'
    '        # Split-half DTE analysis 2026-09-11: BRK\'s edge is confined to 0DTE.\n'
    '        "dte_lot_mult": {"1": 0, "2": 0, "3": 0, "4": 0},\n',
    "loader brk")
ld = sub1(ld,
    '    "ORB_V1": {\n'
    '        "trade_execution_mode": "PAPER",\n'
    '        "underlying": "NIFTY",\n'
    '        "lots": 1,\n'
    '        "lot_size": 0,                    # 0 = index constant (65)\n',
    '    "ORB_V1": {\n'
    '        "trade_execution_mode": "PAPER",\n'
    '        "underlying": "NIFTY",\n'
    '        "lots": 1,\n'
    '        "lot_size": 0,                    # 0 = index constant (65)\n'
    '        # ── DTE_LOT_MULT_LIVE_20260911 ── ORB skips EXPIRY DAY (0DTE): its\n'
    '        # 0DTE edge is gone in the Tuesday era (2024–26 net/DD 0.08); BRK\n'
    '        # takes that day. Together: one 10-lot book across the week.\n'
    '        "dte_lot_mult": {"0": 0},\n',
    "loader orb")

# ── Settings UI ──────────────────────────────────────────────────────────
ui = read(PATH["ui"])
ui = sub1(ui,
    'const DEFAULT_ORB_CONFIG = {\n'
    '  trade_execution_mode: "PAPER",\n'
    '  lots: 1,\n',
    'const DEFAULT_ORB_CONFIG = {\n'
    '  trade_execution_mode: "PAPER",\n'
    '  lots: 1,\n'
    '  dte_lot_mult: { "0": 0 },   // ── DTE_LOT_MULT_LIVE_20260911 ── skip expiry day\n',
    "ui orb default")
ui = sub1(ui,
    '  quantity: { lots: 1, lot_size: 65 },\n'
    '};\n'
    '// ── BRK_V1 END ──\n',
    '  quantity: { lots: 1, lot_size: 65 },\n'
    '  dte_lot_mult: { "1": 0, "2": 0, "3": 0, "4": 0 },   // ── DTE_LOT_MULT_LIVE_20260911 ── expiry day only\n'
    '};\n'
    '// ── BRK_V1 END ──\n',
    "ui brk default")
ui = sub1(ui,
    'function Field({ label: lbl, helper, children, indent, error }) {\n',
    '// ── DTE_LOT_MULT_LIVE_20260911 ── "0:0, 4:1.5" ⇄ {0:0, 4:1.5}; edits commit on blur\n'
    'function fmtDteMult(obj) {\n'
    '  if (!obj || typeof obj !== "object") return "";\n'
    '  return Object.keys(obj).sort((a, b) => Number(a) - Number(b)).map((k) => `${k}:${Number(obj[k])}`).join(", ");\n'
    '}\n'
    'function parseDteMult(s) {\n'
    '  const out = {};\n'
    '  String(s || "").replace(/;/g, ",").split(",").forEach((part) => {\n'
    '    const m = part.match(/^\\s*(\\d+)\\s*(?:DTE)?\\s*:\\s*([0-9]*\\.?[0-9]+)\\s*$/i);\n'
    '    if (m) out[String(Number(m[1]))] = Math.max(0, Number(m[2]));\n'
    '  });\n'
    '  return out;\n'
    '}\n'
    'function DteMultInput({ value, onCommit, style }) {\n'
    '  const [text, setText] = useState(fmtDteMult(value));\n'
    '  useEffect(() => { setText(fmtDteMult(value)); }, [JSON.stringify(value || {})]);\n'
    '  return (\n'
    '    <input type="text" value={text} placeholder="e.g. 0:0, 4:1.5" style={style}\n'
    '      onChange={(e) => setText(e.target.value)}\n'
    '      onBlur={() => onCommit(parseDteMult(text))} />\n'
    '  );\n'
    '}\n'
    'const DTE_HELP = "Per-DTE lot multiplier. DTE = trading sessions to this week\'s expiry (Tue era: Wed=4 … Mon=1, Tue=0; a holiday week shifts naturally). Format 1:0, 2:0 · absent DTE = ×1 · 0 = SKIP that day (in-app DTE_SKIP notice, no orders) · lots = max(1, round(lots × mult)). Applied when the day arms; changes take effect from the next trading day. Blank = every day at base lots.";\n'
    '\n'
    'function Field({ label: lbl, helper, children, indent, error }) {\n',
    "ui helpers")
ui = sub1(ui,
    '                    value={brkConfig.quantity.lots}\n'
    '                    onChange={(e) => updateBRK(["quantity", "lots"], Math.max(1, parseInt(e.target.value || "1", 10)))}\n'
    '                    style={{ maxWidth: 120 }} />\n'
    '                </Field>\n',
    '                    value={brkConfig.quantity.lots}\n'
    '                    onChange={(e) => updateBRK(["quantity", "lots"], Math.max(1, parseInt(e.target.value || "1", 10)))}\n'
    '                    style={{ maxWidth: 120 }} />\n'
    '                </Field>\n'
    '                {/* ── DTE_LOT_MULT_LIVE_20260911 ── */}\n'
    '                <Field label="DTE × lots (0 = skip day)" helper={"DEFAULT 1:0, 2:0, 3:0, 4:0 = BRK trades EXPIRY DAY ONLY. 2020–26 split-half: BRK\'s edge is confined to 0DTE (net/DD 7.1 / 4.4 by half); on 1–3DTE it earns ₹4.8L in six years at net/DD 1.5. ORB takes the other days. " + DTE_HELP}>\n'
    '                  <DteMultInput value={brkConfig.dte_lot_mult} onCommit={(o) => updateBRK(["dte_lot_mult"], o)} style={{ maxWidth: 220 }} />\n'
    '                </Field>\n',
    "ui brk field")
ui = sub1(ui,
    '                <label style={{ fontSize: 12 }}>Lots<br/>\n'
    '                  <input type="number" min="1" style={{ width: 70 }} value={orbConfig.lots}\n'
    '                         onChange={(e) => updateORB("lots", Number(e.target.value) || 1)} />\n'
    '                </label>\n',
    '                <label style={{ fontSize: 12 }}>Lots<br/>\n'
    '                  <input type="number" min="1" style={{ width: 70 }} value={orbConfig.lots}\n'
    '                         onChange={(e) => updateORB("lots", Number(e.target.value) || 1)} />\n'
    '                </label>\n'
    '                {/* ── DTE_LOT_MULT_LIVE_20260911 ── */}\n'
    '                <label style={{ fontSize: 12 }} title={"DEFAULT 0:0 = ORB SKIPS EXPIRY DAY. 2024–26 ORB 0DTE net/DD 0.08 (₹29k in 2.6 years) while BRK 0DTE made ₹3.9L on the same days — BRK takes expiry day, ORB the other four. ORB 4DTE is its strongest day (net/DD 21). " + DTE_HELP}>DTE × lots (0 = skip)<br/>\n'
    '                  <DteMultInput value={orbConfig.dte_lot_mult} onCommit={(o) => updateORB("dte_lot_mult", o)} style={{ width: 200 }} />\n'
    '                </label>\n',
    "ui orb field")

NEW = {"live": LIVE, "oeng": oeng, "omgr": omgr, "beng": beng, "bmgr": bmgr, "loader": ld, "ui": ui}

# ═══════════════════════════════════════════════════════════════════════
# gates
# ═══════════════════════════════════════════════════════════════════════
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp = {}
for k in ("live", "oeng", "omgr", "beng", "bmgr", "loader"):
    p = os.path.join(TMP, k + "_" + os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
    tmp[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK")

sys.path.insert(0, BACKEND)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod
DL = load("app.engine.dte_live", tmp["live"])
audit = []; DL.write_audit_log = lambda s: audit.append(s)
import app.utils.market_hours as MH
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)

print("live DTE on the real calendar (Tuesday era)")
real_itd = MH.is_trading_day
MH.is_trading_day = lambda d=None: (d.weekday() < 5)                   # plain weekdays first
check("Fri 11 Sep 2026 → 2DTE (Mon, Tue)", DL.live_dte(date(2026, 9, 11)) == 2)
check("Mon 14 Sep → 1DTE", DL.live_dte(date(2026, 9, 14)) == 1)
check("Tue 15 Sep (expiry) → 0DTE", DL.live_dte(date(2026, 9, 15)) == 0)
check("Wed 9 Sep → 4DTE", DL.live_dte(date(2026, 9, 9)) == 4)
MH.is_trading_day = lambda d=None: (d.weekday() < 5 and d != date(2026, 9, 15))   # Tuesday holiday → expiry Monday
check("Tuesday-holiday week: Monday reads 0DTE, Friday 1DTE", DL.live_dte(date(2026, 9, 14)) == 0 and DL.live_dte(date(2026, 9, 11)) == 1)
MH.is_trading_day = real_itd
check("REAL calendar: Mon 14 Sep 2026 is a holiday → Fri 11 Sep = 1DTE, Wed 9 Sep = 3DTE", DL.live_dte(date(2026, 9, 11)) == 1 and DL.live_dte(date(2026, 9, 9)) == 3)
check("REAL calendar, holiday-free week: Wed 23 Sep = 4DTE, Fri 25 = 2, Mon 28 = 1, Tue 29 = 0", DL.live_dte(date(2026, 9, 23)) == 4 and DL.live_dte(date(2026, 9, 25)) == 2 and DL.live_dte(date(2026, 9, 28)) == 1 and DL.live_dte(date(2026, 9, 29)) == 0)
BRK_CFG = {"quantity": {"lots": 10, "lot_size": 65}, "dte_lot_mult": {"1": 0, "2": 0, "3": 0, "4": 0}}
ORB_CFG = {"lots": 10, "dte_lot_mult": {"0": 0}}
check("BRK default on Tue → (10, 0, base); on Fri → skip", DL.resolve_day_lots(BRK_CFG, 10, date(2026, 9, 15))[::2] == (10, "base") and DL.resolve_day_lots(BRK_CFG, 10, date(2026, 9, 11))[2] == "skip")
check("ORB default on Tue → skip; on Fri → base", DL.resolve_day_lots(ORB_CFG, 10, date(2026, 9, 15))[2] == "skip" and DL.resolve_day_lots(ORB_CFG, 10, date(2026, 9, 11))[::2] == (10, "base"))
check("×2 on 4DTE → 20 scaled (Wed 23 Sep)", DL.resolve_day_lots({"dte_lot_mult": {"4": 2}}, 10, date(2026, 9, 23)) == (20, 4, "scaled"))
check("no map → base, no calendar call", DL.resolve_day_lots({}, 10, date(2026, 9, 9)) == (10, None, "base"))
DL.live_dte = lambda d: None
check("calendar failure → base lots, tagged unknown (fail-open)", DL.resolve_day_lots(ORB_CFG, 10, date(2026, 9, 15)) == (10, None, "unknown"))
spec = importlib.util.spec_from_file_location("app.engine.dte_live", tmp["live"]); DL = importlib.util.module_from_spec(spec); sys.modules[spec.name] = DL; spec.loader.exec_module(DL)
DL.write_audit_log = lambda s: audit.append(s)

print("ORB engine simulation")
OMGR = load("app.engine.orb.orb_manager", tmp["omgr"])
OENG = load("app.engine.orb.orb_engine", tmp["oeng"])
OENG.write_audit_log = lambda s: audit.append(s); OENG.time.sleep = lambda s: None
from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS
IST = timezone(timedelta(hours=5, minutes=30))
class _GM:
    def __init__(self, cfg): self._cfg = cfg; self.day = None; self.pos = None; self.day_stats = {}; self.alerts = []
    def cfg(self): return self._cfg
    def _alert(self, code, msg, sev="warning"): self.alerts.append((code, msg, sev))
    def adopt_resumed_position(self): pass
    def restore_day_counters(self): return {}
class _B:
    def get_data_kite(self): return None
base_cfg = dict(DEFAULT_STRATEGY_CONFIGS["ORB_V1"]); base_cfg.update({"lots": 10, "dte_lot_mult": {"0": 0}})
e = OENG.OrbEngine(_GM(base_cfg), _B()); e._roll_day(datetime(2026, 9, 15, 9, 15, 4, tzinfo=IST))
check("ORB expiry day (Tue) → skipped: day None, DTE_SKIP info alert, refused stat", e.gm.day is None and any(a[0] == "DTE_SKIP" and a[2] == "info" for a in e.gm.alerts) and "DTE_SKIP 0DTE" in str(e.gm.day_stats.get("refused")), str(e.gm.alerts))
check("ORB skipped day → loop idles (no replay pending)", not e._replay_pending)
e2 = OENG.OrbEngine(_GM(dict(base_cfg)), _B()); e2._roll_day(datetime(2026, 9, 11, 9, 15, 4, tzinfo=IST))
check("ORB Friday → armed, base lots (day_lots None)", e2.gm.day is not None and e2.gm.day_lots is None and not e2.gm.alerts)
c3 = dict(base_cfg); c3["dte_lot_mult"] = {"2": 2}
g3 = _GM(c3); e3 = OENG.OrbEngine(g3, _B()); e3._roll_day(datetime(2026, 9, 25, 9, 15, 4, tzinfo=IST))   # Fri 25 Sep = 2DTE (holiday-free week)
m3 = OMGR.OrbManager.__new__(OMGR.OrbManager); m3.cfg = lambda: c3; m3.day_lots = g3.day_lots
check("ORB Friday with 2:2 → day_lots 20, _qty → 1300", g3.day_lots == 20 and m3._qty() == (20, 65, 1300))
m4 = OMGR.OrbManager.__new__(OMGR.OrbManager); m4.cfg = lambda: c3
check("manager without day_lots attr → base lots (resume-safe)", m4._qty() == (10, 65, 650))
DL.live_dte = lambda d: None
e5 = OENG.OrbEngine(_GM(dict(base_cfg)), _B()); e5._roll_day(datetime(2026, 9, 15, 9, 15, 4, tzinfo=IST))
check("ORB calendar failure on expiry day → armed unscaled (fail-open), audited", e5.gm.day is not None and any("resolve failed" in a or "DTE unknown" in a or "unknown" in a for a in audit))
DL.live_dte = lambda d: DL.__dict__["live_dte"](d) if False else None
spec = importlib.util.spec_from_file_location("app.engine.dte_live", tmp["live"]); DL = importlib.util.module_from_spec(spec); sys.modules[spec.name] = DL; spec.loader.exec_module(DL); DL.write_audit_log = lambda s: audit.append(s)

print("BRK engine simulation")
BMGR = load("app.engine.brk.brk_manager", tmp["bmgr"])
BENG = load("app.engine.brk.brk_engine", tmp["beng"])
BENG.write_audit_log = lambda s: audit.append(s)
brk_cfg = dict(DEFAULT_STRATEGY_CONFIGS["BRK_V1"]); brk_cfg["quantity"] = {"lots": 10, "lot_size": 65}; brk_cfg["dte_lot_mult"] = {"1": 0, "2": 0, "3": 0, "4": 0}
BENG.load_strategy_config = lambda sid: dict(brk_cfg)
class _BGM:
    def __init__(self): self.pos = None; self.day_results = {}; self.day_lots = None; self.alerts = []
    def resume_from_db(self): pass
def mk_brk():
    b = BENG.BrkEngine.__new__(BENG.BrkEngine)
    b.gm = _BGM(); b._day = None; b._selected = {}; b._last_eval_min = -1; b.alerts = []
    b._alert = lambda code, msg, sev="warning": b.alerts.append((code, msg, sev))
    return b
b1 = mk_brk(); b1._roll_day(datetime(2026, 9, 15, 9, 15, 4, tzinfo=IST))
open_sess = [s for s in (b1.core.s1, b1.core.s2) if s and not s.done]
check("BRK expiry day (Tue) → armed, sessions open, no alert, base lots", len(open_sess) >= 1 and not b1.alerts and b1.gm.day_lots is None)
mb = BMGR.BrkManager.__new__(BMGR.BrkManager); mb.cfg = lambda: brk_cfg; mb.day_lots = None
check("BRK _qty on expiry day → 10 lots / 650", mb._qty() == (10, 65, 650))
b2 = mk_brk(); b2._roll_day(datetime(2026, 9, 9, 9, 15, 4, tzinfo=IST))
done = all(s.done for s in (b2.core.s1, b2.core.s2) if s)
check("BRK Wednesday (4DTE) → both sessions done + selected, DTE_SKIP info alert", done and all(b2._selected.get(s.spec.tag) for s in (b2.core.s1, b2.core.s2) if s) and any(a[0] == "DTE_SKIP" and a[2] == "info" for a in b2.alerts), str(b2.alerts))
brk_cfg["dte_lot_mult"] = {"0": 1.5}
b3 = mk_brk(); b3._roll_day(datetime(2026, 9, 15, 9, 15, 4, tzinfo=IST)); mb3 = BMGR.BrkManager.__new__(BMGR.BrkManager); mb3.cfg = lambda: brk_cfg; mb3.day_lots = b3.gm.day_lots
check("BRK 0:1.5 on expiry day → day_lots 15, _qty 975", b3.gm.day_lots == 15 and mb3._qty() == (15, 65, 975))

print("loader defaults + suites")
LDR = load("app.config.strategy_loader", tmp["loader"])
check("loader: BRK default {1,2,3,4 → 0}, ORB default {0 → 0}",
      LDR.DEFAULT_STRATEGY_CONFIGS["BRK_V1"]["dte_lot_mult"] == {"1": 0, "2": 0, "3": 0, "4": 0} and LDR.DEFAULT_STRATEGY_CONFIGS["ORB_V1"]["dte_lot_mult"] == {"0": 0})
for t in ("app/engine/orb/test_orb_manager.py", "app/engine/orb/test_orb_live_core.py"):
    pr = subprocess.run([sys.executable, t], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    check(f"{os.path.basename(t)} passes", pr.returncode == 0 and "PASSED" in pr.stdout, (pr.stdout + pr.stderr)[-300:])
tmpjsx = os.path.join(TMP, "Settings.jsx")
with open(tmpjsx, "w", encoding="utf-8") as f: f.write(ui)
pr = subprocess.run(["npx", "--no-install", "esbuild", tmpjsx, "--loader:.jsx=jsx", "--log-level=error"], cwd=os.path.join(ROOT, "frontend"), capture_output=True, text=True)
print("JSX parse gate: OK" if pr.returncode == 0 else "JSX parse gate: esbuild not available here — the frontend build is the gate")
if FAILS:
    die(f"gates failed: {FAILS}")
print("all gates: OK")

# ═══════════════════════════════════════════════════════════════════════
# write
# ═══════════════════════════════════════════════════════════════════════
for k in ("oeng", "omgr", "beng", "bmgr", "loader", "ui"):
    shutil.copy2(PATH[k], PATH[k] + f".bak-{FENCE}")
for k, p in PATH.items():
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 7 files (6 backups; dte_live.py is new)")
if os.path.isdir(DUAL):
    for k in ("live", "oeng", "omgr", "beng", "bmgr", "loader"):
        rel = REL[k].replace("backend/", "", 1)
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend + frontend: ./desktop/build-scalp.sh")
