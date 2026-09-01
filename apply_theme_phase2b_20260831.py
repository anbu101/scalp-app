#!/usr/bin/env python3
# apply_theme_phase2b_20260831.py
#
# ── APP THEMES · PHASE 2b (final sweep) ── everything still dark-only
# ============================================================================
# Requires phase 1 + 2a (asserts both fences). Designed so ONE rebuild after
# this closes the theme work (BBPanel.jsx excepted, D6).
#
# A. DARK-ONLY FILES → tokens  (all confirmed on the 2026-08-31 screenshots)
#   AccountSelector.jsx    black "Execution Account" <select> on Light
#   AngelAccountCard.jsx   dark-grey "Edit Credentials" button on Light
#   DebugPanel.jsx         dark "Debug" pill + drawer; 7 consts → var()
#   LicenseBanner.jsx      fixed dark amber/red banner → warning/danger tokens
#   LicenseGate.jsx        GitHub-dark palette → tokens
#   BackendBootGuard.jsx   splash `C` palette → tokens (canvas keeps accents)
#   Analytics.jsx          `C` palette → tokens; 2 alpha concats → alpha()
#   Settings.jsx           4 residual grey literals on the leg-toggle chips
#   PaperTrades.jsx        1 white-alpha hover (invisible on Light)
#
# B. LIGHT QA — drop shadows
#   Every `boxShadow: … rgba(0,0,0,x)` in the swept tree → var(--c-shadow),
#   which is heavy on dark and soft on light. The DebugPanel scrim
#   (`background: rgba(0,0,0,0.55)`) is deliberately kept — a scrim should be
#   dark in every theme.
#
# C. CONNECTIONS — collapsed side-panel bug (pre-existing, theme-independent)
#   The collapsed desktop panel rendered BOTH a clipped horizontal title
#   ("Notification Cl") AND a vertical label centred on a panel taller than
#   the viewport (so it showed up half-cut at the bottom). Now: the header
#   title ellipsises instead of clipping, and the vertical label is anchored
#   to the top of the panel where it is always visible.
#
# NOT touched: BBPanel.jsx (D6). ReportView.buildReportHtml's `P` palette —
# that is the standalone exported HTML report, not app UI; it stays dark by
# design. Strategy/broker accent hexes (BrokerChip, StrategyHost, META maps,
# Portfolio ACCENT, Analytics teal/violet/cyan/indigo) are identity (D5).
#
# Idempotent (fence THEME_PHASE2B_20260831), all-or-nothing, esbuild gate on
# every staged file before any write, .bak-THEME_PHASE2B_20260831 backups.
#
# USAGE
#   cd <repo root>
#   python3 apply_theme_phase2b_20260831.py --dry-run
#   python3 apply_theme_phase2b_20260831.py

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

FENCE = "THEME_PHASE2B_20260831"
PHASE1, PHASE2A = "THEME_PHASE1_20260831", "THEME_PHASE2A_20260831"
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
    one(t, start_anchor, lbl + ":start")
    s = t.index(start_anchor)
    e = t.find(end_anchor, s)
    if e < 0:
        die(f"{lbl}: end anchor not found")
    e += len(end_anchor)
    for h in must_contain:
        if h not in t[s:e]:
            die(f"{lbl}: block missing expected {h!r} — file drifted")
    return t[:s] + new_text + t[e:]


def add_import_after_last(t, line, lbl):
    imports = [m for m in re.finditer(r"^import .*?;[^\n]*\n", t, re.M)]
    if not imports:
        die(f"{lbl}: no import lines")
    last = imports[-1]
    return t[:last.end()] + line + t[last.end():]


SHADOW_RE = re.compile(r'(boxShadow:[^\n]*?)rgba\(0, ?0, ?0, ?0\.\d+\)')


def fix_shadows(t):
    """boxShadow rgba(0,0,0,x) → var(--c-shadow); returns (text, n)."""
    n = 0
    while True:
        t2, k = SHADOW_RE.subn(r"\1var(--c-shadow)", t, count=1)
        if k == 0:
            return t, n
        t, n = t2, n + 1


# ════════════════════════════════════════════════════════════════════
#  AccountSelector.jsx
# ════════════════════════════════════════════════════════════════════
AS_A = '''const selectStyle = {
  padding: "6px 10px", borderRadius: 6, fontSize: 13,
  background: "#1a1a1a", color: "#eee", border: "1px solid #333",
};

const saveStyle = (armed) => ({
  padding: "6px 12px", borderRadius: 6, border: "none",
  cursor: "pointer", fontSize: 12, fontWeight: 700, color: "#fff",
  background: armed ? "#dc2626" : "#2563eb",
});'''
AS_N = f'''// ── {FENCE} ── theme-aware (was a fixed near-black select that stayed
// black under the light theme).
const selectStyle = {{
  padding: "6px 10px", borderRadius: 6, fontSize: 13,
  background: colors.bg.input, color: colors.text.primary,
  border: `1px solid ${{colors.border.light}}`,
}};

const saveStyle = (armed) => ({{
  padding: "6px 12px", borderRadius: 6, border: "none",
  cursor: "pointer", fontSize: 12, fontWeight: 700, color: "#fff",
  background: armed ? colors.danger : colors.primary,
}});'''


def edit_accountselector(t):
    if FENCE in t:
        return t, 0
    one(t, AS_A, "accsel:styles")
    t = t.replace(AS_A, AS_N, 1)
    t = add_import_after_last(t, f'import {{ colors }} from "../tokens";   // ── {FENCE} ──\n', "accsel")
    return t, 2


# ════════════════════════════════════════════════════════════════════
#  AngelAccountCard.jsx
# ════════════════════════════════════════════════════════════════════
AC_A = '''  marginRight: 8,
  background: variant === "primary" ? "#2563eb" : "#374151",
  color: "#fff",
});'''
AC_N = f'''  marginRight: 8,
  // ── {FENCE} ── secondary variant follows the theme (was fixed #374151)
  background: variant === "primary" ? colors.primary : colors.bg.tertiary,
  color: variant === "primary" ? "#fff" : colors.text.primary,
  boxShadow: variant === "primary" ? "none" : `inset 0 0 0 1px ${{colors.border.light}}`,
}});'''
AC_DOT_A = 'color: connected ? "#22c55e" : (configured ? "#f59e0b" : "#6b7280"),'
AC_DOT_N = 'color: connected ? colors.success : (configured ? colors.warning : colors.text.muted),'


def edit_angelcard(t):
    if FENCE in t:
        return t, 0
    one(t, AC_A, "angel:btn")
    one(t, AC_DOT_A, "angel:dot")
    return t.replace(AC_A, AC_N, 1).replace(AC_DOT_A, AC_DOT_N, 1), 2


# ════════════════════════════════════════════════════════════════════
#  DebugPanel.jsx
# ════════════════════════════════════════════════════════════════════
DP_A = '''const BG_SURFACE  = "#0b1120";
const BG_CARD     = "#111827";
const BG_INPUT    = "#020617";
const BORDER      = "#1e2d45";
const TEXT        = "#cbd5e1";
const TEXT_MUTED  = "#475569";
const PRIMARY     = "#3b82f6";'''
DP_N = f'''// ── {FENCE} ── theme-aware (names kept for the call sites below)
const BG_SURFACE  = "var(--c-bg-primary)";
const BG_CARD     = "var(--c-bg-secondary)";
const BG_INPUT    = "var(--c-bg-input)";
const BORDER      = "var(--c-border-dark)";
const TEXT        = "var(--c-text-secondary)";
const TEXT_MUTED  = "var(--c-text-muted)";
const PRIMARY     = "var(--c-primary)";'''
DP_CONCAT_A = 'e.currentTarget.style.borderColor  = PRIMARY + "80";'
DP_CONCAT_N = 'e.currentTarget.style.borderColor  = `color-mix(in srgb, ${PRIMARY} 50%, transparent)`;'
DP_HOVER_A = 'onMouseEnter={(e) => (e.currentTarget.style.background = "#1a2540")}'
DP_HOVER_N = 'onMouseEnter={(e) => (e.currentTarget.style.background = "var(--c-bg-tertiary)")}'
DP_LOG = [('"#f87171"', '"var(--c-danger)"', 4), ('"#fbbf24"', '"var(--c-warning)"', 1),
          ('"#34d399"', '"var(--c-success)"', 1), ('"#94a3b8"', '"var(--c-text-tertiary)"', 1)]


def edit_debugpanel(t):
    if FENCE in t:
        return t, 0
    one(t, DP_A, "debug:consts")
    one(t, DP_CONCAT_A, "debug:concat")
    one(t, DP_HOVER_A, "debug:hover", 2)
    for a, _, w in DP_LOG:
        one(t, a, f"debug:log {a}", w)
    t = t.replace(DP_A, DP_N, 1).replace(DP_CONCAT_A, DP_CONCAT_N, 1).replace(DP_HOVER_A, DP_HOVER_N)
    for a, n, _ in DP_LOG:
        t = t.replace(a, n)
    t, ns = fix_shadows(t)
    if ns != 2:
        die(f"debug: expected 2 boxShadow fixes, got {ns}")
    one(t, 'background: "rgba(0,0,0,0.55)",', "debug:scrim kept")
    return t, 4 + ns


# ════════════════════════════════════════════════════════════════════
#  LicenseBanner.jsx
# ════════════════════════════════════════════════════════════════════
LB_A = '''    background: isGrace ? "#3b2f0a" : "#3b0a0a",
    color: isGrace ? "#ffe28a" : "#ffb4b4",'''
LB_N = f'''    // ── {FENCE} ── warning/danger tokens read on every theme
    background: isGrace ? "var(--c-warning-bg)" : "var(--c-danger-bg)",
    color: isGrace ? "var(--c-warning)" : "var(--c-danger)",
    borderBottom: `1px solid ${{isGrace ? "var(--c-warning)" : "var(--c-danger)"}}`,'''


def edit_licensebanner(t):
    if FENCE in t:
        return t, 0
    one(t, LB_A, "banner:style")
    return t.replace(LB_A, LB_N, 1), 1


# ════════════════════════════════════════════════════════════════════
#  LicenseGate.jsx
# ════════════════════════════════════════════════════════════════════
LG_START = "const S = {\n  wrap: {"
LG_N = f'''// ── {FENCE} ── theme tokens (was a fixed GitHub-dark palette)
const S = {{
  wrap: {{
    minHeight: "100vh", display: "flex", alignItems: "center",
    justifyContent: "center", background: colors.bg.primary, color: colors.text.primary,
    fontFamily: "system-ui, -apple-system, sans-serif", padding: "24px",
  }},
  card: {{
    width: "100%", maxWidth: "420px", background: colors.bg.secondary,
    border: `1px solid ${{colors.border.light}}`, borderRadius: "12px", padding: "28px",
    textAlign: "center",
  }},
  h: {{ margin: "0 0 8px", fontSize: "20px", fontWeight: 600 }},
  p: {{ margin: "0 0 20px", fontSize: "14px", color: colors.text.tertiary, lineHeight: 1.5 }},
  input: {{
    width: "100%", boxSizing: "border-box", padding: "12px",
    fontSize: "16px", letterSpacing: "1px", textAlign: "center",
    background: colors.bg.input, color: colors.text.primary, border: `1px solid ${{colors.border.light}}`,
    borderRadius: "8px", outline: "none", textTransform: "uppercase",
  }},
  btn: {{
    width: "100%", marginTop: "14px", padding: "12px", fontSize: "15px",
    fontWeight: 600, background: colors.success, color: "#fff", border: "none",
    borderRadius: "8px", cursor: "pointer",
  }},
  err: {{ marginTop: "12px", fontSize: "13px", color: colors.danger }},
  ok: {{ marginTop: "12px", fontSize: "13px", color: colors.success }},
}};'''


def edit_licensegate(t):
    if FENCE in t:
        return t, 0
    t = replace_block(t, LG_START, "\n};", LG_N, "gate:S",
                      must_contain=("#0d1117", "#238636", "#7ee787"))
    t = add_import_after_last(t, f'import {{ colors }} from "../tokens";   // ── {FENCE} ──\n', "gate")
    if re.search(r'#[0-9a-fA-F]{6}\b', t.replace('"#fff"', "")):
        die("gate: hex literal remains")
    return t, 2


# ════════════════════════════════════════════════════════════════════
#  BackendBootGuard.jsx
# ════════════════════════════════════════════════════════════════════
BG_START = 'const C = {\n  bg:         "#020817",'
BG_N = f'''// ── {FENCE} ── derived from the shared theme tokens so the boot splash
// honours the theme applied at first paint (index.js). The candlestick
// canvas only ever uses the CATEGORY accents above, never these.
const C = {{
  bg:         T.bg.primary,
  card:       T.bg.secondary,
  border:     T.border.dark,
  borderLit:  T.border.light,
  text:       T.text.primary,
  textSoft:   T.text.secondary,
  textMuted:  T.text.muted,
  textDim:    T.border.dark,
  success:    T.success,
  divider:    T.border.dark,
}};'''
BG_SITES = [
    ('color: "#cbd5e1", fontStyle: "italic", maxWidth: 310,',
     'color: C.textSoft, fontStyle: "italic", maxWidth: 310,', 1),
    ('background: isReady ? "#10b981" : ec,', 'background: isReady ? C.success : ec,', 2),
    ('color: isReady ? "#10b981" : ec,', 'color: isReady ? C.success : ec,', 1),
    ('color: isReady ? "#10b981" : C.textMuted,', 'color: isReady ? C.success : C.textMuted,', 1),
]


def edit_bootguard(t):
    if FENCE in t:
        return t, 0
    for a, _, w in BG_SITES:
        one(t, a, "boot:site", w)
    t = replace_block(t, BG_START, "\n};", BG_N, "boot:C",
                      must_contain=("#0b1221", "#243048", "#1a2540"))
    for a, n, _ in BG_SITES:
        t = t.replace(a, n)
    t = add_import_after_last(t, f'import {{ colors as T }} from "../tokens";   // ── {FENCE} ──\n', "boot")
    t, ns = fix_shadows(t)
    if ns != 1:
        die(f"boot: expected 1 boxShadow fix, got {ns}")
    # canvas must not have picked up a var() by accident
    if re.search(r'ctx\.(fillStyle|strokeStyle)\s*=\s*C\.', t):
        die("boot: canvas is reading a C.* token (var() is invalid on canvas)")
    return t, 3 + ns


# ════════════════════════════════════════════════════════════════════
#  Analytics.jsx
# ════════════════════════════════════════════════════════════════════
AN_START = 'const C = {\n  bg:        "#020817",'
AN_N = f'''// ── {FENCE} ── surfaces/text/semantic colours derived from the shared
// theme tokens; teal/violet/cyan/indigo are strategy accents and stay fixed.
const C = {{
  bg:        T.bg.primary,
  bgCard:    T.bg.secondary,
  bgSurface: T.bg.tertiary,
  border:    T.border.light,
  borderDim: T.border.dark,
  text:      T.text.primary,
  textSec:   T.text.tertiary,
  textMuted: T.text.muted,
  green:     T.profit,
  greenBg:   T.profitBg,
  red:       T.loss,
  redBg:     T.lossBg,
  amber:     T.warning,
  amberBg:   T.warningBg,
  blue:      T.primary,
  blueBg:    T.primaryBg,
  teal:      "#14b8a6",
  violet:    "#8b5cf6",
  cyan:      "#06b6d4",
  indigo:    "#6366f1",
}};'''
AN_CONCAT_A = "border: `1px solid ${C.amber}40`,"
AN_CONCAT_N = "border: `1px solid ${alpha(C.amber, 25)}`,"
AN_LEFTOVER = re.compile(r'\$\{C\.[a-zA-Z]+\}[0-9a-fA-F]{2}|C\.[a-zA-Z]+ *\+ *"[0-9a-fA-F]{2}"')


def edit_analytics(t):
    if FENCE in t:
        return t, 0
    one(t, AN_CONCAT_A, "analytics:concat", 2)
    t = replace_block(t, AN_START, "\n};", AN_N, "analytics:C",
                      must_contain=("#0f172a", "#4b6280", "#6366f1"))
    t = t.replace(AN_CONCAT_A, AN_CONCAT_N)
    t = add_import_after_last(t, f'import {{ colors as T, alpha }} from "../tokens";   // ── {FENCE} ──\n', "analytics")
    if AN_LEFTOVER.search(t):
        die("analytics: C.* alpha-concat still present")
    return t, 3


# ════════════════════════════════════════════════════════════════════
#  Settings.jsx — residual greys on the leg-toggle chips
# ════════════════════════════════════════════════════════════════════
ST_BORDER_A = 'border: `1px solid ${on ? "#3b82f6" : "#374151"}`,'
ST_BORDER_N = 'border: `1px solid ${on ? colors.primary : colors.border.light}`,   /* ── ' + FENCE + ' ── */'
ST_COLOR_A = 'color: on ? "#3b82f6" : "#9ca3af",'
ST_COLOR_N = 'color: on ? colors.primary : colors.text.tertiary,'


def edit_settings(t):
    if FENCE in t:
        return t, 0
    one(t, ST_BORDER_A, "settings:border", 2)
    one(t, ST_COLOR_A, "settings:color", 2)
    t = t.replace(ST_BORDER_A, ST_BORDER_N).replace(ST_COLOR_A, ST_COLOR_N)
    t, ns = fix_shadows(t)
    return t, 4 + ns


# ════════════════════════════════════════════════════════════════════
#  PaperTrades.jsx — white-alpha hover
# ════════════════════════════════════════════════════════════════════
PT_A = 'background: isActive ? colors.primaryBg : "rgba(255,255,255,0.06)",'
PT_N = 'background: isActive ? colors.primaryBg : alpha(colors.text.primary, 6),   /* ── ' + FENCE + ' ── */'


def edit_papertrades(t):
    if FENCE in t:
        return t, 0
    one(t, PT_A, "paper:hover")
    if "alpha" not in re.search(r'^import \{([^}]*)\} from "\.\./tokens"', t, re.M).group(1):
        die("paper: alpha not imported (phase 1 missing?)")
    t = t.replace(PT_A, PT_N, 1)
    t, ns = fix_shadows(t)
    return t, 1 + ns


# ════════════════════════════════════════════════════════════════════
#  Connections.jsx — collapsed side-panel
# ════════════════════════════════════════════════════════════════════
CN_HEAD_A = '''          {!isPrimary && <span style={{ fontSize: 10, color: colors.text.muted, flexShrink: 0 }}>↗</span>}
          <span style={{ fontSize: 15, fontWeight: 600, color: colors.text.primary, flexShrink: 0 }}>{name}</span>'''
CN_HEAD_N = f'''          {{!isPrimary && <span style={{{{ fontSize: 10, color: colors.text.muted, flexShrink: 0 }}}}>↗</span>}}
          {{/* ── {FENCE} ── collapsed: ellipsise instead of clipping mid-word
              ("Notification Cl"); the vertical label below carries the name. */}}
          <span title={{name}} style={{{{ fontSize: 15, fontWeight: 600, color: colors.text.primary,
            flexShrink: isPrimary ? 0 : 1, minWidth: 0, overflow: "hidden",
            textOverflow: "ellipsis", whiteSpace: "nowrap" }}}}>{{name}}</span>'''
CN_VERT_A = '''            <div style={{
              writingMode: "vertical-rl", textAlign: "center", display: "flex",
              alignItems: "center", justifyContent: "center", height: "100%",
              fontSize: 12, fontWeight: 500, color: colors.text.muted,
              letterSpacing: "1px", textTransform: "uppercase", padding: spacing.md,
            }}>'''
CN_VERT_N = f'''            <div style={{{{
              /* ── {FENCE} ── anchored to the TOP: the panel is often taller
                 than the viewport, so a centred label sat below the fold and
                 showed up half-cut above the status bar. */
              writingMode: "vertical-rl", textAlign: "left", display: "flex",
              alignItems: "center", justifyContent: "flex-start",
              fontSize: 12, fontWeight: 500, color: colors.text.muted,
              letterSpacing: "1px", textTransform: "uppercase",
              padding: `${{spacing.xl}}px ${{spacing.md}}px`,
            }}}}>'''


def edit_connections(t):
    if FENCE in t:
        return t, 0
    one(t, CN_HEAD_A, "conn:head")
    one(t, CN_VERT_A, "conn:vertical")
    return t.replace(CN_HEAD_A, CN_HEAD_N, 1).replace(CN_VERT_A, CN_VERT_N, 1), 2


# ════════════════════════════════════════════════════════════════════
#  Shadow-only files
# ════════════════════════════════════════════════════════════════════
def edit_shadows_only(expected):
    def fn(t):
        if FENCE in t:
            return t, 0
        t, ns = fix_shadows(t)
        if ns != expected:
            die(f"shadow-only file: expected {expected} boxShadow fixes, got {ns}")
        # leave a fence so the file is recognised on re-run
        return t + f"\n// ── {FENCE} ── boxShadow rgba(0,0,0,x) → var(--c-shadow) ({ns})\n", ns
    return fn


FILES = [
    (os.path.join("components", "AccountSelector.jsx"), edit_accountselector),
    (os.path.join("components", "AngelAccountCard.jsx"), edit_angelcard),
    (os.path.join("components", "DebugPanel.jsx"), edit_debugpanel),
    (os.path.join("components", "LicenseBanner.jsx"), edit_licensebanner),
    (os.path.join("components", "LicenseGate.jsx"), edit_licensegate),
    (os.path.join("components", "BackendBootGuard.jsx"), edit_bootguard),
    (os.path.join("pages", "Analytics.jsx"), edit_analytics),
    (os.path.join("pages", "Settings.jsx"), edit_settings),
    (os.path.join("pages", "PaperTrades.jsx"), edit_papertrades),
    (os.path.join("pages", "Connections.jsx"), edit_connections),
    (os.path.join("components", "AppSettingsSection.jsx"), edit_shadows_only(1)),
    (os.path.join("pages", "Backtest.jsx"), edit_shadows_only(2)),
    (os.path.join("pages", "CryptoLab.jsx"), edit_shadows_only(1)),
    (os.path.join("pages", "Dashboard.jsx"), edit_shadows_only(4)),
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
        tok = open(os.path.join(root, "tokens.js")).read() if os.path.isfile(os.path.join(root, "tokens.js")) else ""
        app = open(os.path.join(root, "App.jsx")).read() if os.path.isfile(os.path.join(root, "App.jsx")) else ""
        if PHASE1 not in tok:
            die(f"[{label}] phase 1 missing — run apply_theme_phase1_20260831.py first")
        if PHASE2A not in app:
            die(f"[{label}] phase 2a missing — run apply_theme_phase2a_20260831.py first")
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
        tmp = tempfile.mkdtemp(prefix="theme_p2b_")
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
        if "/bb_v1/" in dest.replace(os.sep, "/"):
            die("BB V1 file in write set — refusing (D6)")
        if "BackendBootGuard" in dest:
            assert 'colors as T' in body
        if "Analytics.jsx" in dest:
            assert 'teal:      "#14b8a6"' in body   # accents preserved
    print("  BB V1 untouched; accents preserved")

    if a.dry_run:
        print("\n--dry-run: no files written.")
        return

    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        shutil.copy2(dest, dest + f".bak-{FENCE}")
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print(f"\nDONE. Themes phase 2b in place (fence {FENCE}). Frontend-only — Tauri rebuild.")
    print("  Remaining dark-only surfaces by design: BBPanel.jsx (BB V1, D6) and the exported HTML report.")


if __name__ == "__main__":
    main()
