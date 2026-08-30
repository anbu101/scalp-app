#!/usr/bin/env python3
# apply_vet_v1_dashboard_20260829.py
#
# ── VET_V1 LIVE WIRING, PART 4 ── dashboard panel + friend-facing surfaces
# ============================================================================
# The gap-sweep remainder (grep TMA_V2 minus VET_V1). Everything here is what
# makes the strategy VISIBLE and friend-safe:
#
#   frontend  StrategyHost.jsx        import + ACTIVE ids + accent + case
#             strategies/registry.js  launch metadata (slots:false)
#             strategies/vet/VETPanel.jsx   ← CREATED (status/trades panel)
#             displayNames.js         codename "Velvet" (non-admin masking)
#             AppSettingsSection.jsx  settings rail entry
#             Analytics.jsx           filter chip + desc
#             Connections.jsx         color + account-binding option
#             LotsOnlySettings.jsx    friends' lots-only editor (quantity.lots)
#             PaperTrades.jsx         name maps (both directions)
#   backend   config/strategy_display.py  backend codename (must match FE)
#             config/lots_whitelist.py    "VET_V1": ["quantity.lots"] — a
#                                         missing entry silently REJECTS the
#                                         friends' lots save
#             config/account_bindings.py  executor-path tuple + BUY-side map
#   license   server.py KNOWN_STRATEGY_IDS — missing id = 400 on override save
#
# NOT edited: jobs/eod_safety.py (reuses OVERNIGHT_EXEMPT_STRATEGIES, where
# VET_V1 already sits — single source of truth doing its job).
#
# Idempotent, assert-anchored, staged esbuild/py_compile, dual-tree.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_dashboard_20260829.py --dry-run
#   python3 apply_vet_v1_dashboard_20260829.py

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

REPO = os.getcwd()
ACCENT = "#34d399"   # emerald-400 — unused by any other strategy

FE_TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
            (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"),
             "desktop-fe")]
BE_TREES = [(os.path.join(REPO, "backend"), "backend"),
            (os.path.join(REPO, "desktop", "src-tauri", "backend"),
             "desktop-be")]


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:90]}")


PANEL = r'''// frontend/src/strategies/vet/VETPanel.jsx
//
// ── VET_V1 dashboard panel ── (TMA2Panel conventions, 2026-08-29)
// Dual-EMA(10/20) + regime channel on 5m NIFTY spot; ONE position at a time,
// BUY or SELL by config, intraday or positional by config. There is no GTT
// layer (sl/tp are 0 by design), so the panel's job is health + position +
// today's closed groups. Engine-health strip surfaces the PREFIX-GUARD
// frozen flag and the warmup depth — the two "why is it not trading?"
// answers — because silent failure is the enemy.
// "Today" numbers are EXIT-timestamp based; the open card ignores entry day
// entirely (positional carries must show).

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, pnlStyle } from "../../tokens";
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = "__ACCENT__";

function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(Math.round(v));
  return `${v < 0 ? "−" : ""}₹${a.toLocaleString("en-IN")}`;
}
function tsFmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const hm = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? hm
    : `${d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} · ${hm}`;
}

export default function VETPanel({ strategyId = "VET_V1" }) {
  const [status, setStatus] = useState(null);
  const [trades, setTrades] = useState([]);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const base = getApiBase();
        const [s, t] = await Promise.all([
          fetch(`${base}/api/vet/status`).then((r) => r.json()),
          fetch(`${base}/api/vet/trades?limit=60`).then((r) => r.json()),
        ]);
        if (!alive) return;
        setStatus(s);
        setTrades(t?.trades || []);
      } catch { /* next poll */ }
    }
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const mgr = status?.manager;
  const eng = status?.signal_engine;
  const pos = mgr?.position;
  const midnight = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return Math.floor(d.getTime() / 1000); })();
  const groups = {};
  for (const r of trades) {
    if (r.status !== "CLOSED" || !r.exit_ts || r.exit_ts < midnight) continue;
    (groups[r.group_id] = groups[r.group_id] || []).push(r);
  }
  const todayGroups = Object.values(groups)
    .map((legs) => ({
      main: legs.find((l) => l.leg_role === "MAIN") || legs[0],
      net: legs.reduce((a, l) => a + (l.net_pnl ?? l.pnl ?? 0), 0),
    }))
    .sort((a, b) => (b.main.exit_ts || 0) - (a.main.exit_ts || 0));
  const todayNet = todayGroups.reduce((a, g) => a + g.net, 0);
  const frozen = eng?.frozen || mgr?.frozen;
  const warmShort = eng && eng.warmup_ok === false;

  const card = { background: colors.bgAlt, borderRadius: 10, padding: spacing.md,
                 border: `1px solid ${colors.border}`, marginBottom: spacing.sm };
  const dim = { fontSize: 12, opacity: 0.65 };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, marginBottom: spacing.sm }}>
        <div style={{ width: 8, height: 8, borderRadius: 4, background: ACCENT }} />
        <div style={{ fontWeight: 700 }}>{stratName(strategyId)}</div>
        <div style={dim}>
          {mgr ? `${mgr.mode} · ${mgr.leg_action} · ${mgr.eod_square ? "INTRADAY" : "POSITIONAL"}${mgr.hedged ? " · wing" : ""}` : "loop not running"}
        </div>
      </div>

      {(frozen || warmShort) && (
        <div style={{ ...card, borderColor: "#ef4444aa", background: "#7f1d1d22" }}>
          <b>{frozen ? "ENGINE FROZEN" : "WARMUP SHORT"}</b>
          <div style={dim}>
            {frozen
              ? (eng?.freeze_reason || mgr?.freeze_reason || "prefix guard tripped — not trading (fail closed)")
              : `${eng?.warmup_sessions}/${eng?.warmup_required} sessions — decisions blocked until warm`}
          </div>
        </div>
      )}

      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Open position</div>
        {pos ? (
          <div>
            <div>
              {pos.direction === "SHORT" ? "SHORT " : "LONG "}<b>{pos.symbol}</b>
              {" "}@ {pos.entry_price}
              {pos.wing ? <span style={dim}>{"  + wing "}{pos.wing} @ {pos.wing_entry}</span> : null}
            </div>
            <div style={dim}>exits: FLIP / SIGNAL / {mgr?.eod_square ? "EOD 15:15" : "expiry day"} — no SL/TP by design</div>
          </div>
        ) : (
          <div style={dim}>flat{eng?.last_bar_ts ? ` · last 5m bar ${tsFmt(eng.last_bar_ts)}` : ""}</div>
        )}
      </div>

      <div style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontWeight: 600 }}>Today (closed)</div>
          <div style={pnlStyle ? pnlStyle(todayNet) : { color: todayNet >= 0 ? "#34d399" : "#f87171" }}>{fmtInr(todayNet)}</div>
        </div>
        {todayGroups.length === 0 ? (
          <div style={dim}>no closed positions yet</div>
        ) : todayGroups.map((g) => (
          <div key={g.main.group_id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "3px 0" }}>
            <div>
              {g.main.direction === "SHORT" ? "S " : "L "}{g.main.tradingsymbol}
              <span style={dim}>{" "}{g.main.exit_reason} · {tsFmt(g.main.exit_ts)}</span>
            </div>
            <div style={{ color: g.net >= 0 ? "#34d399" : "#f87171" }}>{fmtInr(g.net)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
'''.replace("__ACCENT__", ACCENT)


# ── frontend grafts ─────────────────────────────────────────────────────
SH = os.path.join("components", "StrategyHost.jsx")
SH_IMP_A = 'import TMA2Panel    from "../strategies/tma2/TMA2Panel.jsx";   // ── TMA_V2 ──'
SH_IMP_N = SH_IMP_A + '\nimport VETPanel     from "../strategies/vet/VETPanel.jsx";     // ── VET_V1 ──'
SH_IDS_A = '"TMA_V1", "TMA_V2"];'
SH_IDS_N = '"TMA_V1", "TMA_V2", "VET_V1"];'
SH_MAP_A = '  TMA_V2:    { name: "TMA V2",        accent: "#c084fc" },   // ── TMA_V2 ──'
SH_MAP_N = SH_MAP_A + f'\n  VET_V1:    {{ name: "VET V1",        accent: "{ACCENT}" }},   // ── VET_V1 ──'
SH_CASE_A = '    case "TMA_V2":    return <TMA2Panel    {...common} />;   // ── TMA_V2 ──'
SH_CASE_N = SH_CASE_A + '\n    case "VET_V1":    return <VETPanel     {...common} />;   // ── VET_V1 ──'


def edit_sh(t):
    if "VETPanel" in t:
        return t, 0
    for a, l in ((SH_IMP_A, "import"), (SH_IDS_A, "ids"),
                 (SH_MAP_A, "map"), (SH_CASE_A, "case")):
        one(t, a, "StrategyHost:" + l)
    for a, n in ((SH_IMP_A, SH_IMP_N), (SH_IDS_A, SH_IDS_N),
                 (SH_MAP_A, SH_MAP_N), (SH_CASE_A, SH_CASE_N)):
        t = t.replace(a, n, 1)
    return t, 4


RG = os.path.join("strategies", "registry.js")
RG_A = "  // ── TSG_V1 BEGIN ──"
RG_N = '''  // ── VET_V1 BEGIN ──
  {
    id: "VET_V1",
    label: "VET V1",
    broker: "ZERODHA",
    timeframe: "1m",                    // signals on 5m spot bars; fills on 1m
    modeSupported: ["PAPER", "LIVE"],
    capabilities: {
      hasSelection: true,   // ATM±offset on the live chain (+ wing when SELL)
      hasSlots:     false,  // VetManager owns all state (vet_trades)
      hasCEPE:      false,  // side comes from the trend + leg_action config
    },
  },
  // ── VET_V1 END ──
''' + RG_A


def edit_rg(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, RG_A, "registry:TSG anchor")
    return t.replace(RG_A, RG_N, 1), 1


DN = os.path.join("strategies", "displayNames.js")
DN_A = '  TMA_V2:    { real: "TMA V2",        code: "Timberwolf", sub: "NIFTY weekly" },'
DN_N = DN_A + '\n  VET_V1:    { real: "VET V1",        code: "Velvet",     sub: "NIFTY 5m trend" },'


def edit_dn(t):
    if "VET_V1" in t:
        return t, 0
    one(t, DN_A, "displayNames:TMA_V2 row")
    return t.replace(DN_A, DN_N, 1), 1


AS = os.path.join("components", "AppSettingsSection.jsx")
AS_A = '  { id: "TMA_V2",   name: "TMA V2",        accent: "#c084fc" },   // ── TMA_V2 ── (added 2026-08-19)'
AS_N = AS_A + f'\n  {{ id: "VET_V1",   name: "VET V1",        accent: "{ACCENT}" }},   // ── VET_V1 ── (added 2026-08-29)'


def edit_as(t):
    if "VET_V1" in t:
        return t, 0
    one(t, AS_A, "AppSettings:TMA_V2 row")
    return t.replace(AS_A, AS_N, 1), 1


AN = os.path.join("pages", "Analytics.jsx")
AN_A = ('  { id: "TMA_V2",    label: "TMA V2",    color: "#c084fc", desc: '
        '"Four-EMA stack 13/55/89/144 @5m spot · NIFTY weekly credit spread '
        '· SELL opposite trend + hedge · 13×55 crossover exit" },   '
        '// ── TMA_V2 ──')
AN_N = AN_A + (f'\n  {{ id: "VET_V1",    label: "VET V1",    color: "{ACCENT}", '
               'desc: "Dual-EMA 10/20 + regime channel @5m spot · buy or sell '
               'by config · intraday or overnight carry · exits on trend flip, '
               'no SL/TP" },   // ── VET_V1 ──')


def edit_an(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, AN_A, "Analytics:TMA_V2 row")
    return t.replace(AN_A, AN_N, 1), 1


CN = os.path.join("pages", "Connections.jsx")
CN_C_A = '  TMA_V2:    "#c084fc",   // ── TMA_V2 ──'
CN_C_N = CN_C_A + f'\n  VET_V1:    "{ACCENT}",   // ── VET_V1 ──'
CN_O_A = '  { value: "TMA_V2",    title: "TMA V2" },   // ── TMA_V2 ──'
CN_O_N = CN_O_A + '\n  { value: "VET_V1",    title: "VET V1" },   // ── VET_V1 ──'


def edit_cn(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, CN_C_A, "Connections:color")
    one(t, CN_O_A, "Connections:option")
    t = t.replace(CN_C_A, CN_C_N, 1)
    return t.replace(CN_O_A, CN_O_N, 1), 2


LO = os.path.join("pages", "LotsOnlySettings.jsx")
LO_IDS_A = '  "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",'
LO_IDS_N = LO_IDS_A + '\n  "VET_V1",'
LO_COL_A = '  PST_SELL: "#fb7185", PST_HEDGE: "#be123c", TMA_V1: "#8b5cf6", TMA_V2: "#c084fc",'
LO_COL_N = LO_COL_A + f'\n  VET_V1: "{ACCENT}",'
LO_F_A = ('  TMA_V2:    [{ label: "Number of Lots", helper: "Applies to both '
          'legs of every position", paths: ["s1.main.lots", "s1.hedge.lots"] '
          '}],')
LO_F_N = LO_F_A + ('\n  VET_V1:    [{ label: "Number of Lots", helper: '
                   '"One position at a time; the wing (when selling) always '
                   'matches this size", paths: ["quantity.lots"] }],')


def edit_lo(t):
    if '"VET_V1"' in t or "VET_V1:" in t:
        return t, 0
    one(t, LO_IDS_A, "LotsOnly:ids")
    one(t, LO_COL_A, "LotsOnly:colors")
    one(t, LO_F_A, "LotsOnly:fields")
    t = t.replace(LO_IDS_A, LO_IDS_N, 1)
    t = t.replace(LO_COL_A, LO_COL_N, 1)
    return t.replace(LO_F_A, LO_F_N, 1), 3


PT = os.path.join("pages", "PaperTrades.jsx")
PT_A_A = '  "TMA_V2":    "TMA V2",   // ── TMA_V2 ──'
PT_A_N = PT_A_A + '\n  "VET_V1":    "VET V1",   // ── VET_V1 ──'
PT_B_A = '  "TMA V1": "TMA_V1", "TMA V2": "TMA_V2", "TSG V1": "TSG_V1", "GC V1": "GC_V1",'
PT_B_N = PT_B_A + '\n  "VET V1": "VET_V1",'


def edit_pt(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, PT_A_A, "PaperTrades:id→name")
    one(t, PT_B_A, "PaperTrades:name→id")
    t = t.replace(PT_A_A, PT_A_N, 1)
    return t.replace(PT_B_A, PT_B_N, 1), 2


# ── backend grafts ──────────────────────────────────────────────────────
SD = os.path.join("app", "config", "strategy_display.py")
SD_A = '    "TMA_V2":    ("TMA V2",         "Timberwolf"),'
SD_N = SD_A + '\n    "VET_V1":    ("VET V1",         "Velvet"),'


def edit_sd(t):
    if "VET_V1" in t:
        return t, 0
    one(t, SD_A, "strategy_display:TMA_V2 row")
    return t.replace(SD_A, SD_N, 1), 1


LW = os.path.join("app", "config", "lots_whitelist.py")
LW_A = '    "TMA_V2":    ["s1.main.lots", "s1.hedge.lots"],'
LW_N = (LW_A + '\n    # ── VET_V1 2026-08-29 ── one lots path drives BOTH legs '
        '(the wing,\n    # when selling, always matches the main leg size).\n'
        '    "VET_V1":    ["quantity.lots"],')


def edit_lw(t):
    if "VET_V1" in t:
        return t, 0
    one(t, LW_A, "lots_whitelist:TMA_V2 row")
    return t.replace(LW_A, LW_N, 1), 1


AB = os.path.join("app", "config", "account_bindings.py")
AB_T_A = '''    # ── TMA_V2 2026-08-19 ── same executor path as TMA_V1 (get_executor_for_strategy)
    "TMA_V2",'''
AB_T_N = AB_T_A + '''
    # ── VET_V1 2026-08-29 ── same executor path (get_executor_for_strategy)
    "VET_V1",'''
AB_S_A = '    "TSG_V1": "SELL", "IC_V1": "SELL", "IC_V2": "SELL", "TMA_V2": "SELL",'
AB_S_N = (AB_S_A + '\n    # VET_V1 is BUY by default; leg_action=SELL flips the '
          'book net-short —\n    # this map is grouping metadata, not an '
          'execution constraint.\n    "VET_V1": "BUY",')


def edit_ab(t):
    if "VET_V1" in t:
        return t, 0
    one(t, AB_T_A, "account_bindings:tuple")
    one(t, AB_S_A, "account_bindings:side map")
    t = t.replace(AB_T_A, AB_T_N, 1)
    return t.replace(AB_S_A, AB_S_N, 1), 2


LSRV = os.path.join(REPO, "license_server", "server.py")
LS_A = '''    "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
}'''
LS_N = '''    "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
    "VET_V1",   # ── VET_V1 added 2026-08-29 — missing id = 400 on override save
}'''


def edit_ls(t):
    if "VET_V1" in t:
        return t, 0
    one(t, LS_A, "license server:KNOWN_STRATEGY_IDS")
    return t.replace(LS_A, LS_N, 1), 1


FE_EDITORS = [(SH, edit_sh), (RG, edit_rg), (DN, edit_dn), (AS, edit_as),
              (AN, edit_an), (CN, edit_cn), (LO, edit_lo), (PT, edit_pt)]
BE_EDITORS = [(SD, edit_sd), (LW, edit_lw), (AB, edit_ab)]


def find_esbuild(canary):
    cands = []
    loc = os.path.join(REPO, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.isfile(loc) and os.access(loc, os.X_OK):
        cands.append([loc])
    p = shutil.which("esbuild")
    if p:
        cands.append([p])
    npx = shutil.which("npx")
    if npx:
        cands += [[npx, "--no", "esbuild"], [npx, "--no-install", "esbuild"]]
    for c in cands:
        try:
            if subprocess.run(c + ["--log-level=silent", canary],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=90).returncode == 0:
                return c
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, creates, notes = {}, {}, []
    for root, label in FE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        pdir = os.path.join(root, "strategies", "vet")
        ppath = os.path.join(pdir, "VETPanel.jsx")
        if os.path.isfile(ppath):
            notes.append(f"[{label}] SKIP (exists): strategies/vet/VETPanel.jsx")
        else:
            creates[ppath] = PANEL
            notes.append(f"[{label}] CREATE: strategies/vet/VETPanel.jsx")
        for rel, fn in FE_EDITORS:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")
    for root, label in BE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped (rsync target)")
            continue
        for rel, fn in BE_EDITORS:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")
    if os.path.isfile(LSRV):
        out, n = edit_ls(open(LSRV).read())
        if n:
            writes[LSRV] = out
            notes.append("[license] EDIT: server.py")
        else:
            notes.append("[license] SKIP (already wired): server.py")
    else:
        notes.append("[license] server.py NOT PRESENT — add VET_V1 to "
                     "KNOWN_STRATEGY_IDS on the droplet copy")
    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes and not creates:
        print("\nNothing to do.")
        return
    print("\n── STAGED CHECKS ────────────────────────────────────────────")
    tmp = tempfile.mkdtemp(prefix="vet_dash_")
    try:
        can = os.path.join(tmp, "c.jsx")
        open(can, "w").write("const A = () => <div>{1}</div>;\n")
        es = None if a.skip_jsx_check else find_esbuild(can)
        if es is None and not a.skip_jsx_check:
            print("  !! no working esbuild — JSX check SKIPPED (not an error)")
        i = 0
        for dest, body in list(writes.items()) + list(creates.items()):
            i += 1
            if dest.endswith(".py"):
                stage = os.path.join(tmp, f"s{i}.py")
                open(stage, "w").write(body)
                try:
                    py_compile.compile(stage, doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"compile FAILED for {dest}:\n{e}")
            elif es and dest.endswith((".jsx", ".js")):
                stage = os.path.join(tmp, f"s{i}" + dest[dest.rfind("."):])
                open(stage, "w").write(body)
                r = subprocess.run(es + ["--log-level=warning", stage],
                                   capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=120)
                if r.returncode != 0:
                    die(f"esbuild FAILED for {dest}:\n{r.stderr[:1200]}")
        print("  all staged targets check clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in creates.items():
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "w").write(body)
        print("  created " + os.path.relpath(dest, REPO))
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. VET_V1 is on the dashboard, masked as 'Velvet' for "
          "non-admins, lots-editable by friends, and filterable everywhere.")


if __name__ == "__main__":
    main()
