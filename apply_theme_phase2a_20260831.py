#!/usr/bin/env python3
# apply_theme_phase2a_20260831.py
#
# ── APP THEMES · PHASE 2a ── the app shell + header centering fix
# ============================================================================
# Requires phase 1 (THEME_PHASE1_20260831) — asserts alpha() exists in tokens.
#
# HEADER NAV DRIFT (reported on Light, present in every theme):
#   The top nav was laid out as [brand][flex:1][nav][flex:1][right cluster],
#   i.e. centred BETWEEN the brand and the right cluster. The P&L pill is
#   monospace, so every extra digit (−₹42 → −₹1,234) widens the cluster and
#   the nav slides left by half that width. Fix: a 3-track CSS grid
#   `minmax(max-content,1fr) auto minmax(max-content,1fr)` — the two outer tracks are always
#   equal, so the nav is anchored to the viewport centre and the side
#   clusters can grow or shrink without moving it.
#
# THEME SWEEP (8 files — everything that renders on EVERY screen, plus the
# two Connections-page files whose local palettes were the last hex on the
# same screen as the theme picker):
#   App.jsx               white-alpha hovers/badges/ticks → alpha(text);
#                         shadow → var(--c-shadow); + the grid fix above
#   StatusBar.jsx         8 module consts → var() refs (was dark-only)
#   NotificationCenter    local `C` palette → derived from tokens
#   ToastNotifications    local `colors` palette → derived from tokens
#   LoadingStates         local `colors` palette → derived from tokens;
#                         2 hard-coded #3b82f6 → colors.primary
#   Connections.jsx       local `colors` palette DELETED → tokens import;
#                         3 alpha concats → alpha(); shadows → var(--c-shadow)
#   RelayPanel.jsx        local `colors` palette DELETED → tokens import;
#                         1 alpha concat → alpha()
#   DataVisualization     local `colors` palette → derived from tokens
#
# Local `spacing` objects in Connections/RelayPanel are NOT touched (they
# carry xxl:28 vs tokens' 24 — a layout decision, not a colour one).
#
# NOT touched (D6): BBPanel.jsx. Remaining phase 2 files (pages/backtest/*,
# Analytics, DebugPanel, ScalpEvalDebugPanel, LicenseGate/Banner,
# AccountSelector, BrokerChip, AngelAccountCard, BackendBootGuard, BBV2Panel)
# → phase 2b.
#
# Idempotent (fence THEME_PHASE2A_20260831), all-or-nothing, esbuild gate on
# every staged file BEFORE any write, .bak-THEME_PHASE2A_20260831 backups.
#
# USAGE
#   cd <repo root>
#   python3 apply_theme_phase2a_20260831.py --dry-run
#   python3 apply_theme_phase2a_20260831.py

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

FENCE = "THEME_PHASE2A_20260831"
PHASE1 = "THEME_PHASE1_20260831"
REPO = os.getcwd()
FE_TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
            (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"),
             "desktop-fe")]


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:100]}")


def replace_block(t, start_anchor, end_anchor, new_text, lbl, must_contain=()):
    """Replace from start_anchor through the FIRST end_anchor after it."""
    one(t, start_anchor, lbl + ":start")
    s = t.index(start_anchor)
    e = t.find(end_anchor, s)
    if e < 0:
        die(f"{lbl}: end anchor not found")
    e += len(end_anchor)
    block = t[s:e]
    for h in must_contain:
        if h not in block:
            die(f"{lbl}: block missing expected {h!r} — file drifted")
    return t[:s] + new_text + t[e:]


LEFTOVER_RE = re.compile(r'colors\.[a-zA-Z.]+ *\+ *"[0-9a-fA-F]{2}"|\$\{colors\.[a-zA-Z.]+\}[0-9a-fA-F]{2}')

# ════════════════════════════════════════════════════════════════════
#  App.jsx — header grid + theme-aware whites
# ════════════════════════════════════════════════════════════════════

APP_IMP_A = 'import { colors } from "./tokens";'
APP_IMP_N = f'import {{ colors, alpha }} from "./tokens";   // ── {FENCE} ── alpha()'

APP_WRAP_A = ('      <div style={{ padding: compact ? "0 14px" : "0 24px", display: "flex", '
              'alignItems: "center", height: 54, gap: compact ? 8 : 16 }}>')
APP_WRAP_N = f'''      {{/* ── {FENCE} ── 3-track grid: the nav sits in the middle `auto` track and
          the two outer tracks are equal whenever there is room, so a wider
          P&L pill (more digits) or extra account chips never push the nav
          sideways; max-content floors mean nothing is ever clipped. The old
          [flex:1][nav][flex:1] layout centred the nav between the brand and
          the right cluster, which moved by half of every width change. */}}
      <div style={{{{ padding: compact ? "0 14px" : "0 24px",
        display: "grid", gridTemplateColumns: "minmax(max-content,1fr) auto minmax(max-content,1fr)",
        alignItems: "center", height: 54, gap: compact ? 8 : 16 }}}}>'''

APP_BRAND_A = ('        <div style={{ fontSize: 17, fontWeight: 700, color: colors.text.primary, '
               'display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>')
APP_BRAND_N = ('        <div style={{ fontSize: 17, fontWeight: 700, color: colors.text.primary, '
               'display: "flex", alignItems: "center", gap: 8, flexShrink: 0, justifySelf: "start" }}>')

APP_SPACER = "        <div style={{ flex: 1 }} />\n"   # exactly 2 in the header (8-space indent)

APP_RIGHT_A = ('        <div style={{ display: "flex", alignItems: "center", gap: compact ? 8 : 14, '
               'flexShrink: 0 }}>\n          <NavPnLPill />')
APP_RIGHT_N = ('        <div style={{ display: "flex", alignItems: "center", gap: compact ? 8 : 14, '
               'flexShrink: 0, justifySelf: "end" }}>\n          <NavPnLPill />')

APP_SHADOW_A = 'boxShadow: "0 1px 3px rgba(0,0,0,0.3)", position: "sticky", top: 0, zIndex: 100 }}>'
APP_SHADOW_N = 'boxShadow: "0 1px 3px var(--c-shadow)", position: "sticky", top: 0, zIndex: 100 }}>'

APP_HOVER_A = 'e.currentTarget.style.background = "rgba(255,255,255,0.05)"; e.currentTarget.style.color = colors.text.primary;'
APP_HOVER_N = 'e.currentTarget.style.background = alpha(colors.text.primary, 6); e.currentTarget.style.color = colors.text.primary;'

APP_BADGE_A = 'background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",'
APP_BADGE_N = 'background: alpha(colors.text.primary, 6), border: `1px solid ${alpha(colors.text.primary, 10)}`,'

APP_TICK_A = 'width: 1, height: "100%", background: "rgba(255,255,255,0.08)" }} />'
APP_TICK_N = 'width: 1, height: "100%", background: alpha(colors.text.primary, 8) }} />'


def edit_app(t):
    if FENCE in t:
        return t, 0
    for a, lbl in ((APP_IMP_A, "app:import"), (APP_WRAP_A, "app:wrap"),
                   (APP_BRAND_A, "app:brand"), (APP_RIGHT_A, "app:right"),
                   (APP_SHADOW_A, "app:shadow"), (APP_HOVER_A, "app:hover"),
                   (APP_BADGE_A, "app:badge"), (APP_TICK_A, "app:tick")):
        one(t, a, lbl)
    one(t, APP_SPACER, "app:spacers", 2)
    # spacers must both sit between wrap and right cluster
    ws, rs = t.index(APP_WRAP_A), t.index(APP_RIGHT_A)
    if not (ws < t.index(APP_SPACER) < t.rindex(APP_SPACER) < rs):
        die("app: spacers not inside the header block")
    for a, n in ((APP_IMP_A, APP_IMP_N), (APP_WRAP_A, APP_WRAP_N),
                 (APP_BRAND_A, APP_BRAND_N), (APP_RIGHT_A, APP_RIGHT_N),
                 (APP_SHADOW_A, APP_SHADOW_N), (APP_HOVER_A, APP_HOVER_N),
                 (APP_BADGE_A, APP_BADGE_N), (APP_TICK_A, APP_TICK_N)):
        t = t.replace(a, n, 1)
    t = t.replace(APP_SPACER, "")
    if 'rgba(255,255,255' in t:
        die("app: white-alpha literal still present")
    return t, 9


# ════════════════════════════════════════════════════════════════════
#  StatusBar.jsx — module consts → var()
# ════════════════════════════════════════════════════════════════════

SB_A = '''const BG      = "#060e1f";
const SUCCESS = "#10b981";
const WARNING = "#f59e0b";
const DANGER  = "#ef4444";
const PRIMARY = "#3b82f6";
const MUTED   = "#475569";
const TEXT    = "#94a3b8";
const BORDER  = "#1e293b";'''
SB_N = f'''// ── {FENCE} ── theme-aware. Was a fixed dark palette, so the bar stayed
// dark under the light theme. Names kept; values now follow <html data-theme>.
const BG      = "var(--c-bg-secondary)";
const SUCCESS = "var(--c-success)";
const WARNING = "var(--c-warning)";
const DANGER  = "var(--c-danger)";
const PRIMARY = "var(--c-primary)";
const MUTED   = "var(--c-text-muted)";
const TEXT    = "var(--c-text-tertiary)";
const BORDER  = "var(--c-border-dark)";'''
SB_CONCAT_RE = re.compile(r'\b(BG|SUCCESS|WARNING|DANGER|PRIMARY|MUTED|TEXT|BORDER)\b *\+ *"|\$\{(BG|SUCCESS|WARNING|DANGER|PRIMARY|MUTED|TEXT|BORDER)\}[0-9a-fA-F]{2}')


def edit_statusbar(t):
    if FENCE in t:
        return t, 0
    one(t, SB_A, "statusbar:consts")
    t = t.replace(SB_A, SB_N, 1)
    if SB_CONCAT_RE.search(t):
        die("statusbar: a colour const is string-concatenated — needs alpha()")
    return t, 1


# ════════════════════════════════════════════════════════════════════
#  NotificationCenter.jsx — `C` palette → tokens
# ════════════════════════════════════════════════════════════════════

NC_IMP_A = 'import { useNotifications } from "../context/NotificationProvider";\n'
NC_IMP_N = NC_IMP_A + f'import {{ colors as T }} from "../tokens";   // ── {FENCE} ──\n'
NC_START = "/* ── tokens (match the dark terminal palette) ── */\nconst C = {"
NC_N = f'''/* ── tokens ── {FENCE}: derived from the shared theme tokens so the
   panel follows <html data-theme>. Key names kept for the 40-odd call sites. */
const C = {{
  panel:   T.bg.secondary,
  card:    T.bg.tertiary,
  border:  T.border.dark,
  text:    T.text.primary,
  muted:   T.text.tertiary,
  faint:   T.text.muted,
  error:   T.danger,
  errorBg: T.dangerBg,
  warn:    T.warning,
  warnBg:  T.warningBg,
  info:    T.primary,
  infoBg:  T.primaryBg,
}};'''
NC_SHADOW_A = 'boxShadow: "0 12px 40px rgba(0,0,0,0.55)",'
NC_SHADOW_N = 'boxShadow: "0 12px 40px var(--c-shadow)",'
NC_UNREAD_A = 'background: n.read ? "transparent" : "rgba(148,163,184,0.05)",'
NC_UNREAD_N = 'background: n.read ? "transparent" : "var(--c-primary-bg)",'
NC_CHIP_A = '''        color: "#94a3b8",
        background: "rgba(148,163,184,0.10)",
        border: "1px solid rgba(148,163,184,0.18)",'''
NC_CHIP_N = '''        color: C.muted,
        background: "var(--c-bg-tertiary)",
        border: "1px solid var(--c-border-light)",'''


def edit_notifcenter(t):
    if FENCE in t:
        return t, 0
    for a, lbl in ((NC_IMP_A, "nc:import"), (NC_SHADOW_A, "nc:shadow"),
                   (NC_UNREAD_A, "nc:unread"), (NC_CHIP_A, "nc:chip")):
        one(t, a, lbl)
    t = replace_block(t, NC_START, "\n};", NC_N, "nc:palette",
                      must_contain=("#0f172a", "#111827", "#dc2626"))
    t = t.replace(NC_IMP_A, NC_IMP_N, 1).replace(NC_SHADOW_A, NC_SHADOW_N, 1)
    t = t.replace(NC_UNREAD_A, NC_UNREAD_N, 1).replace(NC_CHIP_A, NC_CHIP_N, 1)
    return t, 5


# ════════════════════════════════════════════════════════════════════
#  ToastNotifications.jsx — local palette → tokens
# ════════════════════════════════════════════════════════════════════

TN_IMP_A = 'import { createContext, useContext, useState, useCallback } from "react";\n'
TN_IMP_N = TN_IMP_A + f'import {{ colors as T }} from "../tokens";   // ── {FENCE} ──\n'
TN_START = "const colors = {\n  success: \"#059669\","
TN_N = f'''// ── {FENCE} ── derived from the shared theme tokens (was a fixed dark
// palette). `info` maps to the brand primary — tokens carry no info colour.
const colors = {{
  success:   T.success,
  successBg: T.successBg,
  warning:   T.warning,
  warningBg: T.warningBg,
  danger:    T.danger,
  dangerBg:  T.dangerBg,
  info:      T.primary,
  infoBg:    T.primaryBg,
  bg:     {{ secondary: T.bg.secondary }},
  border: {{ light: T.border.light }},
  text:   {{ primary: T.text.primary, secondary: T.text.secondary }},
}};'''
TN_SHADOW_A = 'boxShadow: "0 4px 12px rgba(0, 0, 0, 0.4)",'
TN_SHADOW_N = 'boxShadow: "0 4px 12px var(--c-shadow)",'


def edit_toast(t):
    if FENCE in t:
        return t, 0
    one(t, TN_IMP_A, "toast:import")
    one(t, TN_SHADOW_A, "toast:shadow")
    t = replace_block(t, TN_START, "\n};", TN_N, "toast:palette",
                      must_contain=("#0f172a", "#2563eb"))
    t = t.replace(TN_IMP_A, TN_IMP_N, 1).replace(TN_SHADOW_A, TN_SHADOW_N, 1)
    if LEFTOVER_RE.search(t):
        die("toast: token alpha-concat present")
    return t, 3


# ════════════════════════════════════════════════════════════════════
#  LoadingStates.jsx — local palette → tokens, 2 raw #3b82f6
# ════════════════════════════════════════════════════════════════════

LS_START = '''/* -------------------------
   Design Tokens
-------------------------- */

const colors = {
    bg: {
      primary: "#020817",'''
LS_N = f'''/* -------------------------
   Design Tokens — {FENCE}: derived from the shared theme tokens
-------------------------- */

import {{ colors as T }} from "../tokens";

const colors = {{
    bg:     {{ primary: T.bg.primary, secondary: T.bg.secondary, tertiary: T.bg.tertiary }},
    border: {{ light: T.border.light }},
    text:   {{ primary: T.text.primary, secondary: T.text.secondary, muted: T.text.muted }},
    primary: T.primary,
  }};'''
LS_SPIN_A = "          borderTop: `3px solid #3b82f6`,"
LS_SPIN_N = "          borderTop: `3px solid ${colors.primary}`,"
LS_BTN_A = '''              background: "#3b82f6",
              color: colors.text.primary,'''
LS_BTN_N = '''              background: colors.primary,
              color: "#fff",'''


def edit_loading(t):
    if FENCE in t:
        return t, 0
    one(t, LS_SPIN_A, "loading:spinner")
    one(t, LS_BTN_A, "loading:button")
    t = replace_block(t, LS_START, "\n  };", LS_N, "loading:palette",
                      must_contain=("#0f172a", "#64748b"))
    t = t.replace(LS_SPIN_A, LS_SPIN_N, 1).replace(LS_BTN_A, LS_BTN_N, 1)
    if re.search(r'#[0-9a-fA-F]{6}\b', t.replace('"#fff"', "")):
        die("loading: hex literal remains")
    return t, 3


# ════════════════════════════════════════════════════════════════════
#  Connections.jsx — delete local palette, import tokens, 3 concats
# ════════════════════════════════════════════════════════════════════

CN_IMP_A = 'import RelayPanel from "../components/RelayPanel";\n'
CN_IMP_N = CN_IMP_A + f'import {{ colors, alpha }} from "../tokens";   // ── {FENCE} ── replaces the local palette\n'
CN_PAL_START = "const colors = {\n  primary:      \"#3b82f6\","
CN_PAL_N = f"// ── {FENCE} ── local `colors` palette removed; see tokens.js"
CN_SITES = [
    ('border: `1px solid ${checked ? colors.primary + "40" : "transparent"}`,',
     'border: `1px solid ${checked ? alpha(colors.primary, 25) : "transparent"}`,'),
    ('background: allSelected ? `${colors.primary}1f` : colors.bg.input,',
     'background: allSelected ? alpha(colors.primary, 12) : colors.bg.input,'),
    ('background: channel.mode_filter === m.v ? `${colors.primary}1f` : colors.bg.input,',
     'background: channel.mode_filter === m.v ? alpha(colors.primary, 12) : colors.bg.input,'),
    ('boxShadow:    isPrimary ? "0 4px 12px rgba(0,0,0,0.3)" : "0 2px 6px rgba(0,0,0,0.2)",',
     'boxShadow:    isPrimary ? "0 4px 12px var(--c-shadow)" : "0 2px 6px var(--c-shadow)",'),
]


def edit_connections(t):
    if FENCE in t:
        return t, 0
    one(t, CN_IMP_A, "conn:import")
    for a, _ in CN_SITES:
        one(t, a, "conn:site")
    t = replace_block(t, CN_PAL_START, "\n};", CN_PAL_N, "conn:palette",
                      must_contain=("#020817", "#243044", "#4b6280"))
    t = t.replace(CN_IMP_A, CN_IMP_N, 1)
    for a, n in CN_SITES:
        t = t.replace(a, n, 1)
    if LEFTOVER_RE.search(t):
        die("conn: token alpha-concat present")
    return t, 2 + len(CN_SITES)


# ════════════════════════════════════════════════════════════════════
#  RelayPanel.jsx — delete local palette, import tokens, 1 concat
# ════════════════════════════════════════════════════════════════════

RP_IMP_A = 'import { getApiBase } from "../api/base";\n'
RP_IMP_N = RP_IMP_A + f'import {{ colors, alpha }} from "../tokens";   // ── {FENCE} ── replaces the local palette\n'
RP_PAL_START = "const colors = {\n  primary:      \"#3b82f6\","
RP_PAL_N = f"// ── {FENCE} ── local `colors` palette removed; see tokens.js"
RP_SITE_A = "border: `1px solid ${colors.danger}30`,"
RP_SITE_N = "border: `1px solid ${alpha(colors.danger, 19)}`,"


def edit_relay(t):
    if FENCE in t:
        return t, 0
    one(t, RP_IMP_A, "relay:import")
    one(t, RP_SITE_A, "relay:site")
    t = replace_block(t, RP_PAL_START, "\n};", RP_PAL_N, "relay:palette",
                      must_contain=("#020817", "#243044"))
    t = t.replace(RP_IMP_A, RP_IMP_N, 1).replace(RP_SITE_A, RP_SITE_N, 1)
    if LEFTOVER_RE.search(t):
        die("relay: token alpha-concat present")
    return t, 3


# ════════════════════════════════════════════════════════════════════
#  DataVisualization.jsx — local palette → tokens
# ════════════════════════════════════════════════════════════════════

DV_START = "const colors = {\n  profit: \"#10b981\","
DV_N = f'''// ── {FENCE} ── derived from the shared theme tokens (was a fixed
// gray-family palette that predates the slate canonicalisation).
import {{ colors as T }} from "../tokens";

const colors = {{
  profit:  T.profit,
  loss:    T.loss,
  neutral: T.neutral,
  primary: T.primary,
  bg:   {{ tertiary: T.bg.tertiary }},
  text: {{ muted: T.text.muted }},
}};'''


def edit_dataviz(t):
    if FENCE in t:
        return t, 0
    t = replace_block(t, DV_START, "\n};", DV_N, "dataviz:palette",
                      must_contain=("#1f2937", "#6b7280"))
    if LEFTOVER_RE.search(t):
        die("dataviz: token alpha-concat present")
    return t, 1


# ════════════════════════════════════════════════════════════════════
#  Gates
# ════════════════════════════════════════════════════════════════════

FILES = [
    ("App.jsx", edit_app),
    (os.path.join("components", "StatusBar.jsx"), edit_statusbar),
    (os.path.join("components", "NotificationCenter.jsx"), edit_notifcenter),
    (os.path.join("components", "ToastNotifications.jsx"), edit_toast),
    (os.path.join("components", "LoadingStates.jsx"), edit_loading),
    (os.path.join("pages", "Connections.jsx"), edit_connections),
    (os.path.join("components", "RelayPanel.jsx"), edit_relay),
    (os.path.join("components", "DataVisualization.jsx"), edit_dataviz),
]


def find_esbuild(canary):
    cands = []
    loc = os.path.join(REPO, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.isfile(loc) and os.access(loc, os.X_OK):
        cands.append(([loc], "node_modules"))
    p = shutil.which("esbuild")
    if p:
        cands.append(([p], "PATH"))
    npx = shutil.which("npx")
    if npx:
        cands += [([npx, "--no", "esbuild"], "npx"),
                  ([npx, "--no-install", "esbuild"], "npx7")]
    for c, w in cands:
        try:
            if subprocess.run(c + ["--log-level=silent", canary],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=90).returncode == 0:
                return c, w
        except Exception:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in FE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        tok = os.path.join(root, "tokens.js")
        if not os.path.isfile(tok) or PHASE1 not in open(tok).read() \
                or "export const alpha" not in open(tok).read():
            die(f"[{label}] tokens.js lacks phase 1 ({PHASE1}) — run apply_theme_phase1_20260831.py first")
        for rel, fn in FILES:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (fenced): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")

    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return

    print("\n── JSX SYNTAX CHECK ─────────────────────────────────────────")
    if a.skip_jsx_check:
        print("  skipped by request")
    else:
        tmp = tempfile.mkdtemp(prefix="theme_p2a_")
        try:
            can = os.path.join(tmp, "c.jsx")
            open(can, "w").write("const A = () => <div>{1}</div>;\n")
            cmd, where = find_esbuild(can)
            if cmd is None:
                print("  !! no working esbuild — check SKIPPED (not an error)")
            else:
                print(f"  esbuild via {where}")
                for i, (dest, body) in enumerate(writes.items()):
                    st = os.path.join(tmp, f"s{i}.jsx")
                    open(st, "w").write(body)
                    r = subprocess.run(cmd + ["--log-level=warning", st],
                                       capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=120)
                    if r.returncode != 0:
                        die(f"esbuild FAILED for {dest}:\n{r.stderr[:1500]}")
                print(f"  {len(writes)} file(s) parse clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n── STRUCTURAL CHECK ─────────────────────────────────────────")
    for dest, body in writes.items():
        rel = os.path.relpath(dest, REPO)
        if rel.endswith("App.jsx"):
            assert 'gridTemplateColumns: "minmax(max-content,1fr) auto minmax(max-content,1fr)"' in body
            assert body.count(APP_SPACER) == 0
            assert 'justifySelf: "start"' in body and 'justifySelf: "end"' in body
            # mobile bottom-nav spacer (span, flex:1) must survive untouched
            assert '<span style={{ flex: 1 }} />' in body
        if rel.endswith(("Connections.jsx", "RelayPanel.jsx")):
            assert "const colors = {" not in body and 'from "../tokens"' in body
            assert "const spacing = {" in body   # layout object intentionally kept
    print("  header grid + spacer removal + palette deletions verified")

    if a.dry_run:
        print("\n--dry-run: no files written.")
        return

    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        shutil.copy2(dest, dest + f".bak-{FENCE}")
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print(f"\nDONE. Themes phase 2a in place (fence {FENCE}). Frontend-only — Tauri rebuild.")


if __name__ == "__main__":
    main()
