#!/usr/bin/env python3
"""
apply_TSG_HARD_STOP_20260911.py — FENCE: TSG_HARD_STOP_20260911

2026-09-11 live: mtm_sl 3,500 booked −4,228 (+21%). Not slippage — the
basket SL is evaluated once per minute (on_minute, LD2 backtest parity), so
a spike inside the minute runs past the level before it is seen. The
overshoot scales linearly with lots.

Intra-minute HARD STOP (live + paper, backtest untouched):
  * The ~4 s refresh_display() (display-only until now) additionally checks
    day MTM ≤ −mtm_sl × mtm_sl_hard_mult and, if breached, closes every open
    leg with reason MTM_SL (exit vocabulary unchanged).
  * mtm_sl_hard_mult default 1.05 (user, 2026-09-11) → hard level −3,675 on a
    ₹3,500 SL. The multiplier is clamped to ≥ 1.0 so the hard stop can never
    fire BEFORE the minute-close SL would; 0 = off. Not risk_scale'd
    separately — it multiplies the already-scaled mtm_sl.
  * Why a buffer, not a tick-level SL at 1.0: an intra-minute wick that
    recovers by the close would exit live where the backtest never did.
    With the buffer only runaway spikes are cut. Live SL days can now be
    worse than backtest by at most (mult − 1), never by "whatever the
    minute did".
  * Audit line [TSG][HARD_STOP] with the MTM and level; the group-exit
    Telegram/in-app message is the existing one.
  * Divergence ledger in tsg_live_core.py updated. Resume-safe: new
    dataclass field with a default, from_state() fills it for old snapshots.

Files:
  backend/app/engine/tsg/tsg_live_core.py     field + evaluate_hard_stop + ledger
  backend/app/engine/tsg/tsg_manager.py       cfg → core; guard in refresh_display; risk row
  backend/app/engine/tsg/test_tsg_live_core.py  +4 core tests
  backend/app/config/strategy_loader.py       TSG_V1 default 1.05
  frontend/src/pages/Settings.jsx             default + field

Safety: fence check, py_compile gate, core tests on the patched module, a
manager-level simulation (wick that recovers → no exit; runaway → exit on
the refresh; minute SL still fires at 1.0×; paper + live paths; mult 0 →
off; mult 0.8 → clamped to 1.0; old snapshot resumes), staged .bak writes,
dual-tree mirror. Backend + frontend → full rebuild.

Run from the repo root:  python3 apply_TSG_HARD_STOP_20260911.py
"""
import importlib.util, os, py_compile, shutil, subprocess, sys, tempfile, threading, types

FENCE = "TSG_HARD_STOP_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"core":   "backend/app/engine/tsg/tsg_live_core.py",
       "mgr":    "backend/app/engine/tsg/tsg_manager.py",
       "test":   "backend/app/engine/tsg/test_tsg_live_core.py",
       "loader": "backend/app/config/strategy_loader.py",
       "ui":     "frontend/src/pages/Settings.jsx"}
PATH = {k: os.path.join(ROOT, v) for k, v in REL.items()}
if not os.path.isfile(PATH["core"]):
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

# ── core ────────────────────────────────────────────────────────────────
c = read(PATH["core"])
c = sub1(c,
    '#   - Exit fills  : live market-order fills vs backtest at that candle\'s\n'
    '#     close. Slippage on fast moves makes live MTM_SL days worse than\n'
    '#     booked backtest ones — treat backtest SL losses as a floor (noted\n'
    '#     2026-08-02 KPI analysis).\n',
    '#   - Exit fills  : live market-order fills vs backtest at that candle\'s\n'
    '#     close. Slippage on fast moves makes live MTM_SL days worse than\n'
    '#     booked backtest ones — treat backtest SL losses as a floor (noted\n'
    '#     2026-08-02 KPI analysis).\n'
    '#   - HARD STOP   : ── TSG_HARD_STOP_20260911 ── live/paper additionally\n'
    '#     check day MTM ≤ −mtm_sl × mtm_sl_hard_mult on the ~4 s refresh and\n'
    '#     exit MTM_SL at once. The backtest has no intra-minute view. Live\n'
    '#     SL days can now overshoot the level by at most (mult − 1) instead\n'
    '#     of "whatever the minute did" (2026-09-11: 3,500 booked −4,228).\n'
    '#     A wick that recovers within the buffer still behaves like the\n'
    '#     backtest (no exit).\n',
    "core ledger")
c = sub1(c,
    '    mtm_sl: float = 35000.0            # ₹, 0 = off\n'
    '    mtm_target: float = 0.0            # ₹, 0 = off (production: 0)\n',
    '    mtm_sl: float = 35000.0            # ₹, 0 = off\n'
    '    mtm_sl_hard_mult: float = 1.05     # ── TSG_HARD_STOP_20260911 ── intra-minute hard stop at −mtm_sl×mult (0 = off, clamped ≥ 1.0)\n'
    '    mtm_target: float = 0.0            # ₹, 0 = off (production: 0)\n',
    "core field")
c = sub1(c,
    '    @staticmethod\n'
    '    def eod_due(now_hhmm: str, exit_hhmm: str) -> bool:\n',
    '    # ── TSG_HARD_STOP_20260911 ────────────────────────────────────────\n'
    '    def hard_stop_level(self) -> float:\n'
    '        """Rupee level (positive) of the intra-minute hard stop; 0 = off.\n'
    '        Clamped to ≥ 1.0× so it can never fire before the minute SL."""\n'
    '        if self.mtm_sl <= 0 or (self.mtm_sl_hard_mult or 0) <= 0:\n'
    '            return 0.0\n'
    '        return self.mtm_sl * max(1.0, float(self.mtm_sl_hard_mult))\n'
    '\n'
    '    def evaluate_hard_stop(self, marks: Dict[str, Optional[float]]\n'
    '                           ) -> Optional[Tuple[str, List[str]]]:\n'
    '        """Intra-minute check run by the wrapper on its ~4 s refresh.\n'
    '        Uses the given marks (falling back to last_mark), does NOT touch\n'
    '        peak/trough (those stay minute-close, backtest parity) and does\n'
    '        NOT evaluate target/IV — only the runaway-loss case. Returns\n'
    '        ("MTM_SL", open_ids) or None."""\n'
    '        if self.state not in (D_OPEN, D_PARTIAL):\n'
    '            return None\n'
    '        lvl = self.hard_stop_level()\n'
    '        if lvl <= 0:\n'
    '            return None\n'
    '        eff = {i: (marks.get(i) if marks.get(i) is not None\n'
    '                   else self.legs[i].last_mark) for i in self.open_ids()}\n'
    '        if not eff or any(v is None for v in eff.values()):\n'
    '            return None\n'
    '        if self.day_mtm(eff) <= -lvl:\n'
    '            return ("MTM_SL", self.open_ids())\n'
    '        return None\n'
    '    # ── TSG_HARD_STOP_20260911 END ────────────────────────────────────\n'
    '\n'
    '    @staticmethod\n'
    '    def eod_due(now_hhmm: str, exit_hhmm: str) -> bool:\n',
    "core method")

# ── manager ─────────────────────────────────────────────────────────────
m = read(PATH["mgr"])
m = sub1(m,
    '            mtm_sl=abs(float(cfg.get("mtm_sl", 35000) or 0)) * risk_scale,\n'
    '            mtm_target=abs(float(cfg.get("mtm_target", 0) or 0)) * risk_scale,\n',
    '            mtm_sl=abs(float(cfg.get("mtm_sl", 35000) or 0)) * risk_scale,\n'
    '            mtm_sl_hard_mult=abs(float(cfg.get("mtm_sl_hard_mult", 1.05) or 0)),   # ── TSG_HARD_STOP_20260911 ──\n'
    '            mtm_target=abs(float(cfg.get("mtm_target", 0) or 0)) * risk_scale,\n',
    "mgr core cfg")
m = sub1(m,
    '    def refresh_display(self) -> None:\n'
    '        """DISPLAY-ONLY refresh (~4s cadence from the engine): update\n'
    '        leg.last_mark + leg.last_iv for the panel. Deliberately performs\n'
    '        NO exit evaluation and NO persistence — decisions remain exclusively\n'
    '        the property of on_minute at 1m closes (LD2 backtest parity). Side\n'
    '        benefit: the D11 carry-forward fallback inside evaluate_minute now\n'
    '        falls back to a seconds-old price instead of a minutes-old one."""\n',
    '    def refresh_display(self) -> None:\n'
    '        """~4 s refresh from the engine: update leg.last_mark + leg.last_iv\n'
    '        for the panel. Decisions remain the property of on_minute at 1m\n'
    '        closes (LD2 backtest parity) with ONE exception —\n'
    '        ── TSG_HARD_STOP_20260911 ── the intra-minute HARD STOP\n'
    '        (day MTM ≤ −mtm_sl × mtm_sl_hard_mult) is evaluated here and exits\n'
    '        immediately, because a minute is long enough for a spike to run a\n'
    '        ₹3,500 SL to −4,228 (2026-09-11). Target / trail / IV stay\n'
    '        minute-close. Side benefit: the D11 carry-forward fallback inside\n'
    '        evaluate_minute falls back to a seconds-old price."""\n',
    "mgr docstring")
m = sub1(m,
    '                if leg.is_short and leg.iv_threshold is not None:\n'
    '                    iv = self._solve_strike_iv(\n'
    '                        leg, mk, px.get(self._sibling.get(i) or ""), spot)\n'
    '                    if iv is not None:\n'
    '                        leg.last_iv = iv\n'
    '\n'
    '    def _quotes(self, symbols: List[str]) -> Dict[str, float]:\n',
    '                if leg.is_short and leg.iv_threshold is not None:\n'
    '                    iv = self._solve_strike_iv(\n'
    '                        leg, mk, px.get(self._sibling.get(i) or ""), spot)\n'
    '                    if iv is not None:\n'
    '                        leg.last_iv = iv\n'
    '            # ── TSG_HARD_STOP_20260911 ── runaway-loss guard on the refresh\n'
    '            try:\n'
    '                dec = self._core.evaluate_hard_stop(\n'
    '                    {i: self._core.legs[i].last_mark\n'
    '                     for i in self._core.open_ids()})\n'
    '            except Exception as e:\n'
    '                write_audit_log(f"[TSG][HARD_STOP][EVAL_FAIL] {e!r}")\n'
    '                dec = None\n'
    '            if dec is not None:\n'
    '                reason, ids = dec\n'
    '                _mtm = self._core.day_mtm({i: self._core.legs[i].last_mark\n'
    '                                           for i in ids})\n'
    '                write_audit_log(\n'
    '                    f"[TSG][HARD_STOP] day MTM {_mtm:,.0f} ≤ "\n'
    '                    f"-{self._core.hard_stop_level():,.0f} "\n'
    '                    f"(SL {self._core.mtm_sl:,.0f} × "\n'
    '                    f"{max(1.0, self._core.mtm_sl_hard_mult):.2f}) — "\n'
    '                    f"intra-minute exit of {ids}")\n'
    '                self._execute_exits(ids, reason)\n'
    '                self._persist()\n'
    '\n'
    '    def _quotes(self, symbols: List[str]) -> Dict[str, float]:\n',
    "mgr guard")
m = sub1(m,
    '            if self._core.mtm_sl:\n'
    '                risk.append(["Group SL", f"-₹{self._core.mtm_sl:,.0f}"])\n',
    '            if self._core.mtm_sl:\n'
    '                risk.append(["Group SL", f"-₹{self._core.mtm_sl:,.0f}"])\n'
    '                _hs = getattr(self._core, "hard_stop_level", lambda: 0.0)()   # ── TSG_HARD_STOP_20260911 ──\n'
    '                if _hs > 0:\n'
    '                    risk.append(["Hard stop (intra-min)", f"-₹{_hs:,.0f}"])\n',
    "mgr risk row")

# ── loader default ──────────────────────────────────────────────────────
ld = read(PATH["loader"])
ld = sub1(ld,
    '        "mtm_sl": 3500,\n'
    '        "mtm_target": 0,\n'
    '        "iv_sl_delta_pts": 4,\n'
    '        "iv_sl_pct": 25,\n'
    '        "min_entry_iv": 0.10,   # LD11/IV13 entry-IV floor (validated 2026-08-03)\n',
    '        "mtm_sl": 3500,\n'
    '        "mtm_sl_hard_mult": 1.05,   # ── TSG_HARD_STOP_20260911 ── intra-minute hard stop at −SL×1.05 (0 = off)\n'
    '        "mtm_target": 0,\n'
    '        "iv_sl_delta_pts": 4,\n'
    '        "iv_sl_pct": 25,\n'
    '        "min_entry_iv": 0.10,   # LD11/IV13 entry-IV floor (validated 2026-08-03)\n',
    "loader default")

# ── UI ──────────────────────────────────────────────────────────────────
u = read(PATH["ui"])
u = sub1(u,
    '  mtm_sl: 35000,\n'
    '  mtm_target: 0,\n'
    '  iv_sl_delta_pts: 4,\n'
    '  iv_sl_pct: 0,\n'
    '  min_entry_iv: 0.10,\n',
    '  mtm_sl: 35000,\n'
    '  mtm_sl_hard_mult: 1.05,   // ── TSG_HARD_STOP_20260911 ──\n'
    '  mtm_target: 0,\n'
    '  iv_sl_delta_pts: 4,\n'
    '  iv_sl_pct: 0,\n'
    '  min_entry_iv: 0.10,\n',
    "ui default")
u = sub1(u,
    '                  <Input type="number" value={tsgConfig.mtm_sl}\n'
    '                    onChange={(e) => updateTSG(["mtm_sl"], Number(e.target.value))}\n'
    '                    style={{ maxWidth: 110 }} />\n'
    '                </Field>\n',
    '                  <Input type="number" value={tsgConfig.mtm_sl}\n'
    '                    onChange={(e) => updateTSG(["mtm_sl"], Number(e.target.value))}\n'
    '                    style={{ maxWidth: 110 }} />\n'
    '                </Field>\n'
    '                {/* ── TSG_HARD_STOP_20260911 ── */}\n'
    '                <Field label="Hard stop × SL" helper="INTRA-MINUTE runaway guard (live + paper). The MTM SL is checked once per 1m close (backtest parity); a spike inside the minute can overshoot it (2026-09-11: ₹3,500 booked −4,228). Every ~4s the app also checks day MTM ≤ −SL × this and exits ALL legs at once if breached. 1.05 → −3,675 on a ₹3,500 SL. Clamped to ≥ 1.0 so it never fires before the minute SL would; 0 = off. Kept above 1.0 on purpose: at exactly 1.0 an intra-minute wick that recovers would exit live where the backtest never did.">\n'
    '                  <Input type="number" step="0.01" min="0" value={tsgConfig.mtm_sl_hard_mult ?? 1.05}\n'
    '                    onChange={(e) => updateTSG(["mtm_sl_hard_mult"], Number(e.target.value))}\n'
    '                    style={{ maxWidth: 110 }} />\n'
    '                </Field>\n',
    "ui field")

# ── core tests (inserted BEFORE the __main__ collector) ─────────────────
t = read(PATH["test"])
tests = '''
# ── TSG_HARD_STOP_20260911 ── intra-minute hard stop ─────────────────────
def test_hard_stop_level_default_and_clamp():
    c = _entered(mtm_sl=3500.0)                       # default mult 1.05
    assert abs(c.hard_stop_level() - 3675.0) < 1e-6
    assert TsgDayCore(mtm_sl=3500.0, mtm_sl_hard_mult=0.8).hard_stop_level() == 3500.0   # clamped ≥ 1.0
    assert TsgDayCore(mtm_sl=3500.0, mtm_sl_hard_mult=0).hard_stop_level() == 0.0        # off
    assert TsgDayCore(mtm_sl=0.0, mtm_sl_hard_mult=1.05).hard_stop_level() == 0.0        # no SL → no guard

def test_hard_stop_fires_only_past_the_buffer():
    c = _entered(mtm_sl=3500.0)   # qty 650 (10 lots × 65); shorts at 78 / 81
    # L1 78 → 83.5: −5.5 × 650 = −3,575 on the basket → past the SL, inside the buffer → NO hard stop
    assert c.evaluate_hard_stop({"L1": 83.5, "L2": 81.0, "L3": 4.2, "L4": 3.9}) is None
    # but the MINUTE SL would fire at that mark (layering proof)
    assert c.evaluate_minute({"L1": 83.5, "L2": 81.0, "L3": 4.2, "L4": 3.9}, {})[0] == "MTM_SL"
    c2 = _entered(mtm_sl=3500.0)
    # L1 78 → 83.7: −5.7 × 650 = −3,705 → past −3,675 → hard stop, all legs
    dec = c2.evaluate_hard_stop({"L1": 83.7, "L2": 81.0, "L3": 4.2, "L4": 3.9})
    assert dec is not None and dec[0] == "MTM_SL" and set(dec[1]) == {"L1", "L2", "L3", "L4"}

def test_hard_stop_does_not_touch_peak_or_state():
    c = _entered(mtm_sl=3500.0)
    c.evaluate_minute({"L1": 70.0, "L2": 81.0, "L3": 4.2, "L4": 3.9}, {})    # peak +5,200
    peak = c.peak_mtm
    assert c.evaluate_hard_stop({"L1": 77.0, "L2": 81.0, "L3": 4.2, "L4": 3.9}) is None
    assert c.peak_mtm == peak and c.state == D_OPEN

def test_hard_stop_resume_from_old_snapshot():
    c = _entered(mtm_sl=3500.0)
    d = c.to_state(); d.pop("mtm_sl_hard_mult")      # snapshot written before this fence
    r = TsgDayCore.from_state(d)
    assert r.mtm_sl_hard_mult == 1.05 and abs(r.hard_stop_level() - 3675.0) < 1e-6

'''
t = sub1(t, '\nif __name__ == "__main__":\n', tests + '\nif __name__ == "__main__":\n', "test insert")

NEW = {"core": c, "mgr": m, "test": t, "loader": ld, "ui": u}

# ── gates ───────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp = {}
for k in ("core", "mgr", "test", "loader"):
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
    tmp[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK")

proc = subprocess.run([sys.executable, tmp["test"]], cwd=TMP, capture_output=True, text=True, timeout=120)
for ln in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
    print("   ", ln)
if proc.returncode != 0:
    die("test_tsg_live_core.py failed on the patched module")

# manager-level simulation on the patched modules (real app package for deps)
sys.path.insert(0, BACKEND)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod
CORE = load("app.engine.tsg.tsg_live_core", tmp["core"])
MGR = load("app.engine.tsg.tsg_manager", tmp["mgr"])
audit = []; MGR.write_audit_log = lambda s: audit.append(s)

CFG = [{"id": "L1", "action": "SELL", "opt_type": "CE", "premium_max": 85},
       {"id": "L2", "action": "SELL", "opt_type": "PE", "premium_max": 85},
       {"id": "L3", "action": "BUY", "opt_type": "CE", "premium_max": 5},
       {"id": "L4", "action": "BUY", "opt_type": "PE", "premium_max": 5}]
LAD = {"CE": [("C2", 78.0), ("C4", 4.2)], "PE": [("P2", 81.0), ("P4", 3.9)]}
META = {s: {"strike": 100, "expiry": "2026-09-15"} for s in ("C2", "C4", "P2", "P4")}

def mk_mgr(paper=True, mult=1.05, quotes=None):
    core = CORE.TsgDayCore(mtm_sl=3500.0, mtm_sl_hard_mult=mult)
    assert core.plan_entry(LAD, CFG, 65, 1, META) is not None
    for lid, px in (("L1", 78.0), ("L2", 81.0), ("L3", 4.2), ("L4", 3.9)):
        core.leg_filled(lid, px)
    g = MGR.TsgManager.__new__(MGR.TsgManager)
    g._lock = threading.RLock(); g._core = core; g._sibling = {}; g._paper = paper
    g._paper_row_ids = {}; g._quote_fn = quotes
    g._persist = lambda: None; g.closed = []
    g._notify_group_exit = lambda asked, reason: g.closed.append((tuple(asked), reason))
    g._solve_strike_iv = lambda *a, **k: None; g._parity_spot = lambda px: None
    g._market_close = lambda leg, r: g.closed.append(("MKT", leg.leg_id, r))
    return g

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)
print("manager simulation (qty 65, SL 3,500, hard 1.05 → 3,675)")
# 1) wick to −3,575 on the refresh (past SL, inside buffer) → no exit; recovers → nothing
g = mk_mgr(quotes=lambda s: {"C2": 78.0 + 55.0, "P2": 81.0, "C4": 4.2, "P4": 3.9})   # L1 +55 → −3,575
g.refresh_display()
check("refresh: −3,575 (past SL, inside buffer) → NO exit", g._core.state == CORE.D_OPEN and not g.closed)
g._quote_fn = lambda s: {"C2": 78.0 + 40.0, "P2": 81.0, "C4": 4.2, "P4": 3.9}     # recovers to −2,600
g.refresh_display(); check("refresh: recovered wick → still open", g._core.state == CORE.D_OPEN and not g.closed)
# 2) minute close at −3,575 → the MINUTE SL fires (layering intact)
g._quote_fn = lambda s: {"C2": 78.0 + 55.0, "P2": 81.0, "C4": 4.2, "P4": 3.9}
g.on_minute(None); check("on_minute: −3,575 → minute SL fires (1.0×)", g.closed and g.closed[-1][1] == "MTM_SL")
# 3) runaway on the refresh → exits at once, all four legs, reason MTM_SL, audited
g2 = mk_mgr(quotes=lambda s: {"C2": 78.0 + 57.0, "P2": 81.0, "C4": 4.2, "P4": 3.9})  # −3,705
g2.refresh_display()
check("refresh: −3,705 → hard stop exits all 4 legs as MTM_SL", g2.closed and g2.closed[-1][1] == "MTM_SL" and set(g2.closed[-1][0]) == {"L1", "L2", "L3", "L4"}, str(g2.closed))
check("paper path booked exits", all(g2._core.legs[i].state != CORE.L_OPEN for i in ("L1", "L2", "L3", "L4")))
check("[TSG][HARD_STOP] audited with level", any("[TSG][HARD_STOP]" in a and "3,675" in a for a in audit), str(audit[-2:]))
# 4) live path → market closes issued for every leg
g3 = mk_mgr(paper=False, quotes=lambda s: {"C2": 78.0 + 57.0, "P2": 81.0, "C4": 4.2, "P4": 3.9})
g3.refresh_display()
check("live path: 4 market closes with reason MTM_SL", sorted(x[1] for x in g3.closed if x[0] == "MKT") == ["L1", "L2", "L3", "L4"] and all(x[2] == "MTM_SL" for x in g3.closed if x[0] == "MKT"))
# 5) mult 0 → off even at −5,000
g4 = mk_mgr(mult=0, quotes=lambda s: {"C2": 78.0 + 77.0, "P2": 81.0, "C4": 4.2, "P4": 3.9})
g4.refresh_display(); check("mult 0 → guard off (minute SL still owns it)", not g4.closed)
# 6) mult 0.8 → clamped to 1.0: −3,575 exits on the refresh (never BEFORE the SL: −3,400 does not)
g5 = mk_mgr(mult=0.8, quotes=lambda s: {"C2": 78.0 + 52.0, "P2": 81.0, "C4": 4.2, "P4": 3.9})   # −3,380
g5.refresh_display(); ok_a = not g5.closed
g5._quote_fn = lambda s: {"C2": 78.0 + 55.0, "P2": 81.0, "C4": 4.2, "P4": 3.9}
g5.refresh_display(); check("mult 0.8 → clamped to 1.0 (no exit at −3,380, exit at −3,575)", ok_a and bool(g5.closed))
# 7) refresh with a quote failure → no decision, no crash
g6 = mk_mgr(quotes=lambda s: (_ for _ in ()).throw(RuntimeError("kite")))
g6.refresh_display(); check("quote failure on refresh → no exit, no crash", not g6.closed and g6._core.state == CORE.D_OPEN)
# 8) closed day → refresh is a no-op
g7 = mk_mgr(quotes=lambda s: {"C2": 200.0, "P2": 81.0, "C4": 4.2, "P4": 3.9}); g7._core.state = CORE.D_CLOSED
g7.refresh_display(); check("closed day → no-op", not g7.closed)
# 9) loader default present
LDR = load("app.config.strategy_loader", tmp["loader"])
check("strategy_loader TSG_V1 default mtm_sl_hard_mult = 1.05", LDR.DEFAULT_STRATEGY_CONFIGS["TSG_V1"].get("mtm_sl_hard_mult") == 1.05)
if FAILS:
    die(f"manager simulation failed: {FAILS}")
print("manager simulation: OK")

# ── write ───────────────────────────────────────────────────────────────
for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
for k, p in PATH.items():
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 5 files (+ .bak backups)")
if os.path.isdir(DUAL):
    for k in ("core", "mgr", "test", "loader"):
        rel = REL[k].replace("backend/", "", 1)
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend + frontend: ./desktop/build-scalp.sh")
