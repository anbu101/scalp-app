#!/usr/bin/env python3
# apply_theme_phase1_20260831.py
#
# ── APP THEMES · PHASE 1 ── mechanism + picker (decisions D1–D7, 2026-08-31)
# ============================================================================
# D1  tokens.js keeps its exact export shape; every colour VALUE becomes a CSS
#     custom-property reference ("var(--c-bg-primary)"). Palettes live in
#     index.css under :root[data-theme="…"]. Switching = one attribute on
#     <html>. The 25 files that already import tokens migrate with no edits.
# D2  alpha(token, pct) helper (color-mix) replaces every string-concat alpha
#     on a token — 14 sites across 5 files — in THIS commit. Fixed strategy
#     accent hexes (`${ac}66`) are untouched: they stay plain hex and still
#     concat fine.
# D3  Three themes: dark (default, pixel-identical to today), light, terminal.
# D4  `theme` key in ~/.scalp-app/app_settings.json via the existing
#     /api/app/settings round-trip, mirrored to localStorage so the theme is
#     applied at first paint, before BackendBootGuard has a backend to ask.
#     Default "dark" → existing users see zero change on upgrade.
# D5  Strategy accents are identity, not theme — untouched. profit/loss keep
#     hue but take darker shades on light for contrast.
# D6  BBPanel.jsx (BB V1, sacred) NOT touched. Backend-rendered HTML/PNG
#     surfaces NOT touched.
# D7  Phase 1 only. Files with local `colors = {…}` copies (Connections,
#     RelayPanel, DataVisualization, LoadingStates, ToastNotifications) and
#     the 17 non-token files are phase 2.
#
# FILES
#   frontend/src/tokens.js                        colours → var(), + alpha()
#   frontend/src/theme.js                         NEW — THEMES, applyTheme,
#                                                 readStoredTheme, useTheme
#   frontend/src/index.css                        palettes + themed body
#   frontend/src/index.js                         first-paint applyTheme
#   frontend/src/context/NotificationProvider.jsx theme ⇄ settings sync
#   frontend/src/components/AppSettingsSection.jsx  picker row
#   frontend/src/pages/Settings.jsx               4 alpha sites
#   frontend/src/pages/PaperTrades.jsx            7 alpha sites
#   frontend/src/pages/LotsOnlySettings.jsx       1 alpha site
#   frontend/src/pages/Dashboard.jsx              1 alpha site
#   frontend/src/components/MarketBadge.jsx       1 alpha site
#   backend/app/api/app_settings_api.py           theme key (dual-tree)
#
# Idempotent (fence THEME_PHASE1_20260831), all-or-nothing staging, esbuild
# JSX gate + py_compile gate + Node behavioural check BEFORE any write,
# .bak-THEME_PHASE1_20260831 backups of every file touched.
#
# USAGE
#   cd <repo root>
#   python3 apply_theme_phase1_20260831.py --dry-run
#   python3 apply_theme_phase1_20260831.py

import argparse
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

FENCE = "THEME_PHASE1_20260831"
REPO = os.getcwd()

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
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:100]}")


# ════════════════════════════════════════════════════════════════════
#  1. tokens.js — colours → var(); + alpha()
# ════════════════════════════════════════════════════════════════════

TOK_A_START = "export const colors = {"
TOK_A_END = """  text: {
    primary:   "#f8fafc",
    secondary: "#cbd5e1",
    tertiary:  "#94a3b8",
    muted:     "#64748b",
  },
};"""

TOK_NEW_COLORS = f"""// ── {FENCE} ── every value is a CSS custom-property reference. The real
// hex lives in index.css under :root[data-theme="…"]; see theme.js. Keep this
// object's SHAPE stable — 25+ files read it. Never concat an alpha suffix
// onto one of these (e.g. colors.primary + "60") — use alpha() below.
export const colors = {{
  // Semantic  — profit / loss
  profit:    "var(--c-profit)",
  profitBg:  "var(--c-profit-bg)",
  loss:      "var(--c-loss)",
  lossBg:    "var(--c-loss-bg)",
  neutral:   "var(--c-neutral)",

  // Brand
  primary:      "var(--c-primary)",
  primaryBg:    "var(--c-primary-bg)",
  primaryHover: "var(--c-primary-hover)",

  // Semantic states
  success:   "var(--c-success)",
  successBg: "var(--c-success-bg)",
  warning:   "var(--c-warning)",
  warningBg: "var(--c-warning-bg)",
  danger:    "var(--c-danger)",
  dangerBg:  "var(--c-danger-bg)",

  // Backgrounds
  bg: {{
    primary:   "var(--c-bg-primary)",    // page background
    secondary: "var(--c-bg-secondary)",  // cards, panels
    tertiary:  "var(--c-bg-tertiary)",   // elevated cards, table odd rows
    elevated:  "var(--c-bg-elevated)",   // alias used by some components
    input:     "var(--c-bg-input)",      // form inputs
  }},

  // Borders
  border: {{
    light:  "var(--c-border-light)",   // visible borders
    medium: "var(--c-border-medium)",  // stronger dividers
    dark:   "var(--c-border-dark)",    // subtle separators
  }},

  // Text
  text: {{
    primary:   "var(--c-text-primary)",
    secondary: "var(--c-text-secondary)",
    tertiary:  "var(--c-text-tertiary)",
    muted:     "var(--c-text-muted)",
  }},
}};

/**
 * alpha(token, pct) — translucent version of ANY colour token (or a plain
 * hex). Replaces the old `colors.primary + "60"` string trick, which can't
 * work on a var() reference. pct is 0–100.
 *   alpha(colors.primary, 38)  ≈ old colors.primary + "60"
 */
export const alpha = (token, pct) =>
  `color-mix(in srgb, ${{token}} ${{pct}}%, transparent)`;"""


def edit_tokens(t):
    if FENCE in t:
        return t, 0
    one(t, TOK_A_START, "tokens:start")
    one(t, TOK_A_END, "tokens:end")
    s = t.index(TOK_A_START)
    e = t.index(TOK_A_END) + len(TOK_A_END)
    block = t[s:e]
    # sanity: the block we're replacing is the dark palette we expect
    for h in ("#020817", "#0f172a", "#1e293b", "#3b82f6", "#10b981", "#ef4444"):
        if h not in block:
            die(f"tokens.js colours block missing expected {h} — file drifted")
    return t[:s] + TOK_NEW_COLORS + t[e:], 1


# ════════════════════════════════════════════════════════════════════
#  2. theme.js — NEW
# ════════════════════════════════════════════════════════════════════

THEME_JS = f"""/**
 * theme.js — {FENCE}
 *
 * Runtime theme switch. The palette for each theme lives in index.css under
 * :root[data-theme="…"]; tokens.js exposes var() references to it. This
 * module just decides WHICH palette is active.
 *
 *   applyTheme(name)     → sets <html data-theme> + mirrors to localStorage
 *   readStoredTheme()    → localStorage → "dark"   (first-paint, pre-backend)
 *   useTheme()           → [theme, setTheme] hook (reads the DOM attribute)
 *
 * Source of truth is app_settings.json via /api/app/settings (D4);
 * localStorage is only a first-paint mirror so the window never flashes the
 * wrong palette while BackendBootGuard waits for the backend.
 */

import {{ useCallback, useEffect, useState }} from "react";

export const THEMES = [
  {{ id: "dark",     name: "Dark",     desc: "Slate — the current look" }},
  {{ id: "light",    name: "Light",    desc: "White panels, dark text" }},
  {{ id: "terminal", name: "Terminal", desc: "True black, high contrast" }},
];

export const THEME_DEFAULT = "dark";
export const THEME_IDS = THEMES.map((t) => t.id);

const LS_KEY = "scalp.theme";

export function normalizeTheme(name) {{
  return THEME_IDS.includes(name) ? name : THEME_DEFAULT;
}}

export function readStoredTheme() {{
  try {{
    return normalizeTheme(window.localStorage.getItem(LS_KEY));
  }} catch {{
    return THEME_DEFAULT;
  }}
}}

export function currentTheme() {{
  try {{
    return normalizeTheme(document.documentElement.getAttribute("data-theme"));
  }} catch {{
    return THEME_DEFAULT;
  }}
}}

/** Idempotent — safe to call from a 30 s settings poll. */
export function applyTheme(name) {{
  const t = normalizeTheme(name);
  try {{
    const el = document.documentElement;
    if (el.getAttribute("data-theme") !== t) {{
      el.setAttribute("data-theme", t);
      el.dispatchEvent(new CustomEvent("scalp:theme", {{ detail: t }}));
    }}
  }} catch {{ /* non-DOM env */ }}
  try {{ window.localStorage.setItem(LS_KEY, t); }} catch {{ /* private mode */ }}
  return t;
}}

/** Local view of the active theme; persistence goes through saveSettings. */
export function useTheme() {{
  const [theme, setThemeState] = useState(currentTheme);
  useEffect(() => {{
    const h = (e) => setThemeState(e.detail);
    document.documentElement.addEventListener("scalp:theme", h);
    return () => document.documentElement.removeEventListener("scalp:theme", h);
  }}, []);
  const setTheme = useCallback((n) => setThemeState(applyTheme(n)), []);
  return [theme, setTheme];
}}
"""


# ════════════════════════════════════════════════════════════════════
#  3. index.css — palettes + themed body
# ════════════════════════════════════════════════════════════════════

CSS_A_BODY = ('body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, '
              '"Segoe UI", Roboto, "Helvetica Neue", Arial; background:#0f1724; '
              'color:#e6eef8; }')

CSS_PALETTES = f"""/* ── {FENCE} ── theme palettes. tokens.js references these by name.
   dark  = the pre-theme slate palette, value-for-value (default, no change
           for existing users).
   light = Tailwind slate light. profit/loss/warning take the -600 shades so
           they clear contrast on white (D5).
   terminal = true black, neutral greys, brighter semantic colours.
   Strategy accents (SCALP amber, BB blue, …) are NOT here — they are
   identity, not theme (D5). */

:root, :root[data-theme="dark"] {{
  color-scheme: dark;
  --c-profit: #10b981;   --c-profit-bg: rgba(16, 185, 129, 0.10);
  --c-loss: #ef4444;     --c-loss-bg: rgba(239, 68, 68, 0.10);
  --c-neutral: #6b7280;
  --c-primary: #3b82f6;  --c-primary-bg: rgba(59, 130, 246, 0.12);
  --c-primary-hover: #2563eb;
  --c-success: #10b981;  --c-success-bg: rgba(16, 185, 129, 0.12);
  --c-warning: #f59e0b;  --c-warning-bg: rgba(245, 158, 11, 0.12);
  --c-danger: #ef4444;   --c-danger-bg: rgba(239, 68, 68, 0.12);
  --c-bg-primary: #020817;  --c-bg-secondary: #0f172a;
  --c-bg-tertiary: #1e293b; --c-bg-elevated: #1e293b;  --c-bg-input: #060d1a;
  --c-border-light: #334155; --c-border-medium: #475569; --c-border-dark: #1e293b;
  --c-text-primary: #f8fafc; --c-text-secondary: #cbd5e1;
  --c-text-tertiary: #94a3b8; --c-text-muted: #64748b;
  --c-shadow: rgba(0, 0, 0, 0.45);
}}

:root[data-theme="light"] {{
  color-scheme: light;
  --c-profit: #059669;   --c-profit-bg: rgba(5, 150, 105, 0.10);
  --c-loss: #dc2626;     --c-loss-bg: rgba(220, 38, 38, 0.10);
  --c-neutral: #6b7280;
  --c-primary: #2563eb;  --c-primary-bg: rgba(37, 99, 235, 0.10);
  --c-primary-hover: #1d4ed8;
  --c-success: #059669;  --c-success-bg: rgba(5, 150, 105, 0.12);
  --c-warning: #d97706;  --c-warning-bg: rgba(217, 119, 6, 0.12);
  --c-danger: #dc2626;   --c-danger-bg: rgba(220, 38, 38, 0.12);
  --c-bg-primary: #f1f5f9;  --c-bg-secondary: #ffffff;
  --c-bg-tertiary: #f8fafc; --c-bg-elevated: #ffffff;  --c-bg-input: #ffffff;
  --c-border-light: #cbd5e1; --c-border-medium: #94a3b8; --c-border-dark: #e2e8f0;
  --c-text-primary: #0f172a; --c-text-secondary: #334155;
  --c-text-tertiary: #475569; --c-text-muted: #64748b;
  --c-shadow: rgba(15, 23, 42, 0.12);
}}

:root[data-theme="terminal"] {{
  color-scheme: dark;
  --c-profit: #22c55e;   --c-profit-bg: rgba(34, 197, 94, 0.12);
  --c-loss: #f43f5e;     --c-loss-bg: rgba(244, 63, 94, 0.12);
  --c-neutral: #737373;
  --c-primary: #60a5fa;  --c-primary-bg: rgba(96, 165, 250, 0.14);
  --c-primary-hover: #3b82f6;
  --c-success: #22c55e;  --c-success-bg: rgba(34, 197, 94, 0.14);
  --c-warning: #fbbf24;  --c-warning-bg: rgba(251, 191, 36, 0.14);
  --c-danger: #f43f5e;   --c-danger-bg: rgba(244, 63, 94, 0.14);
  --c-bg-primary: #000000;  --c-bg-secondary: #0a0a0a;
  --c-bg-tertiary: #171717; --c-bg-elevated: #171717;  --c-bg-input: #050505;
  --c-border-light: #333333; --c-border-medium: #4d4d4d; --c-border-dark: #1f1f1f;
  --c-text-primary: #ffffff; --c-text-secondary: #d4d4d4;
  --c-text-tertiary: #a3a3a3; --c-text-muted: #737373;
  --c-shadow: rgba(0, 0, 0, 0.7);
}}

html {{ background: var(--c-bg-primary); }}
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; background: var(--c-bg-primary); color: var(--c-text-primary); }}"""


def edit_css(t):
    if FENCE in t:
        return t, 0
    one(t, CSS_A_BODY, "css:body")
    return t.replace(CSS_A_BODY, CSS_PALETTES, 1), 1


# ════════════════════════════════════════════════════════════════════
#  4. index.js — first-paint applyTheme
# ════════════════════════════════════════════════════════════════════

IDX_A = 'import "./index.css";\n'
IDX_N = IDX_A + f"""import {{ applyTheme, readStoredTheme }} from "./theme";   // ── {FENCE} ──

// Apply the last-used theme BEFORE the first render so the window never
// flashes the wrong palette while BackendBootGuard waits for the backend.
// app_settings.json (via NotificationProvider) remains the source of truth
// and will re-apply on load if it differs.
applyTheme(readStoredTheme());
"""


def edit_index_js(t):
    if FENCE in t:
        return t, 0
    one(t, IDX_A, "index.js:css import")
    return t.replace(IDX_A, IDX_N, 1), 1


# ════════════════════════════════════════════════════════════════════
#  5. NotificationProvider.jsx — theme ⇄ settings sync
# ════════════════════════════════════════════════════════════════════

NP_A1 = """  const [settings, setSettings] = useState({
    notify_audio: true,
    notify_toast: true,
    show_account_balance: true,
    audio_rules: {},
  });"""
NP_N1 = f"""  const [settings, setSettings] = useState({{
    notify_audio: true,
    notify_toast: true,
    show_account_balance: true,
    audio_rules: {{}},
    theme: "dark",   // ── {FENCE} ──
  }});"""

NP_A2 = """          setSettings({
            ...data,
            notify_audio: data.notify_audio !== false,
            notify_toast: data.notify_toast !== false,
            show_account_balance: data.show_account_balance !== false,
            audio_rules: (data.audio_rules && typeof data.audio_rules === "object") ? data.audio_rules : {},
          });"""
NP_N2 = f"""          setSettings({{
            ...data,
            notify_audio: data.notify_audio !== false,
            notify_toast: data.notify_toast !== false,
            show_account_balance: data.show_account_balance !== false,
            audio_rules: (data.audio_rules && typeof data.audio_rules === "object") ? data.audio_rules : {{}},
            theme: normalizeTheme(data.theme),   // ── {FENCE} ──
          }});
          // Backend is the source of truth (D4): re-apply on every load so a
          // theme changed on another machine sharing the settings file wins.
          applyTheme(data.theme);   // ── {FENCE} ── idempotent"""

NP_A3 = """  const saveSettings = useCallback(async (next) => {
    setSettings(next);"""
NP_N3 = f"""  const saveSettings = useCallback(async (next) => {{
    setSettings(next);
    if (next && next.theme) applyTheme(next.theme);   // ── {FENCE} ──"""


def edit_np(t):
    if FENCE in t:
        return t, 0
    one(t, NP_A1, "np:default state")
    one(t, NP_A2, "np:refresh")
    one(t, NP_A3, "np:save")
    # import: append after the last import line
    imports = [m for m in re.finditer(r"^import .*?;\n", t, re.M)]
    if not imports:
        die("np: no import lines found")
    last = imports[-1]
    imp = f'import {{ applyTheme, normalizeTheme }} from "../theme";   // ── {FENCE} ──\n'
    t = t[:last.end()] + imp + t[last.end():]
    t = t.replace(NP_A1, NP_N1, 1).replace(NP_A2, NP_N2, 1).replace(NP_A3, NP_N3, 1)
    return t, 4


# ════════════════════════════════════════════════════════════════════
#  6. AppSettingsSection.jsx — picker row
# ════════════════════════════════════════════════════════════════════

AS_IMP_A = 'import { colors, spacing } from "../tokens";\n'
AS_IMP_N = AS_IMP_A + f'import {{ THEMES, normalizeTheme }} from "../theme";   // ── {FENCE} ──\n'

AS_ROW_A = """        <GlobalRow
          icon="💰"
          title="Show account balance in header"
          desc="Display your Zerodha available balance in the top navigation bar."
          checked={settings.show_account_balance}
          onChange={(v) => saveSettings({ ...settings, show_account_balance: v })}
          loading={loading}
        />
      </div>"""
AS_ROW_N = AS_ROW_A[:-len("      </div>")] + f"""        {{/* ── {FENCE} ── appearance */}}
        <ThemeRow
          value={{normalizeTheme(settings.theme)}}
          onChange={{(id) => saveSettings({{ ...settings, theme: id }})}}
          loading={{loading}}
        />
      </div>"""

AS_COMP_A = "export default function AppSettingsSection() {"
AS_COMP_N = f"""// ── {FENCE} ── theme picker. Persists through saveSettings (backend is
// the source of truth); the visual switch itself happens in applyTheme
// inside NotificationProvider.saveSettings, so this row is stateless.
function ThemeRow({{ value, onChange, loading }}) {{
  return (
    <div style={{{{
      display: "flex", alignItems: "center", gap: spacing.md,
      padding: `${{spacing.md}}px 0`,
      flexWrap: "wrap",
    }}}}>
      <span style={{{{ fontSize: 20, flexShrink: 0 }}}}>🎨</span>
      <div style={{{{ flex: 1, minWidth: 180 }}}}>
        <div style={{{{ fontSize: 14, fontWeight: 600, color: colors.text.primary }}}}>Appearance</div>
        <div style={{{{ fontSize: 12, color: colors.text.muted, marginTop: 2 }}}}>
          Colour theme for the whole app. Applies instantly; saved for next launch.
        </div>
      </div>
      <div role="radiogroup" aria-label="Theme" style={{{{ display: "flex", gap: 6, flexShrink: 0 }}}}>
        {{THEMES.map((t) => {{
          const on = t.id === value;
          return (
            <button
              key={{t.id}}
              role="radio" aria-checked={{on}}
              title={{t.desc}}
              disabled={{loading}}
              onClick={{() => !on && onChange(t.id)}}
              style={{{{
                padding: "6px 12px", borderRadius: 6,
                border: `1px solid ${{on ? colors.primary : colors.border.light}}`,
                background: on ? colors.primaryBg : colors.bg.tertiary,
                color: on ? colors.primary : colors.text.secondary,
                fontSize: 12, fontWeight: 600,
                cursor: loading ? "default" : on ? "default" : "pointer",
                opacity: loading ? 0.5 : 1,
                transition: "background 0.15s, border-color 0.15s, color 0.15s",
              }}}}
            >
              {{t.name}}
            </button>
          );
        }})}}
      </div>
    </div>
  );
}}

""" + AS_COMP_A


def edit_appsettings(t):
    if FENCE in t:
        return t, 0
    one(t, AS_IMP_A, "as:import")
    one(t, AS_ROW_A, "as:balance row")
    one(t, AS_COMP_A, "as:component")
    t = t.replace(AS_IMP_A, AS_IMP_N, 1)
    t = t.replace(AS_ROW_A, AS_ROW_N, 1)
    t = t.replace(AS_COMP_A, AS_COMP_N, 1)
    return t, 3


# ════════════════════════════════════════════════════════════════════
#  7. Alpha-concat sites → alpha()   (D2 — same commit as D1, always)
#     hex alpha → percent: 1f≈12 30≈19 40≈25 50≈31 55≈33 60≈38 80≈50 99≈60
# ════════════════════════════════════════════════════════════════════

ALPHA_SITES = {
    os.path.join("pages", "Settings.jsx"): [
        ('colors.danger + "40"',          "alpha(colors.danger, 25)"),
        ('colors.primary + "60"',         "alpha(colors.primary, 38)"),
        ('colors.bg.secondary + "80"',    "alpha(colors.bg.secondary, 50)"),
        ('colors.bg.secondary + "60"',    "alpha(colors.bg.secondary, 38)"),
    ],
    os.path.join("pages", "PaperTrades.jsx"): [
        ('colors.success + "50"',         "alpha(colors.success, 31)"),
        ('`1px solid ${colors.primary}50`', "`1px solid ${alpha(colors.primary, 31)}`"),
        # boxShadow `0 0 6px ${colors.warning}99` occurs twice (lines 1050 & 1164)
        ('`0 0 6px ${colors.warning}99`',  "`0 0 6px ${alpha(colors.warning, 60)}`"),
        # `${colors.warning}40` occurs twice (lines 1156 & 1182)
        ('`${colors.warning}40`',          "alpha(colors.warning, 25)"),
    ],
    os.path.join("pages", "LotsOnlySettings.jsx"): [
        ('colors.bg.secondary + "80"',    "alpha(colors.bg.secondary, 50)"),
    ],
    os.path.join("pages", "Dashboard.jsx"): [
        ('`1px solid ${colors.warning}55`', "`1px solid ${alpha(colors.warning, 33)}`"),
    ],
    os.path.join("components", "MarketBadge.jsx"): [
        ('`1px solid ${colors.border.light}40`', "`1px solid ${alpha(colors.border.light, 25)}`"),
    ],
}
# expected occurrence counts where a needle legitimately appears >1×
ALPHA_MULTI = {
    ('pages/PaperTrades.jsx', '`0 0 6px ${colors.warning}99`'): 2,
    ('pages/PaperTrades.jsx', '`${colors.warning}40`'): 2,
}

# any leftover token+alpha concat after patching is a hard failure
LEFTOVER_RE = re.compile(r'colors\.[a-zA-Z.]+ *\+ *"[0-9a-fA-F]{2}"|\$\{colors\.[a-zA-Z.]+\}[0-9a-fA-F]{2}')

TOKENS_IMPORT_RE = re.compile(r'^import \{([^}]*)\} from "(\.\./)+tokens";', re.M)


def edit_alpha_file(rel, t):
    if FENCE in t:
        return t, 0
    sites = ALPHA_SITES[rel]
    key = rel.replace(os.sep, "/")
    for needle, _ in sites:
        want = ALPHA_MULTI.get((key, needle), 1)
        one(t, needle, f"alpha:{key}", want)
    for needle, repl in sites:
        t = t.replace(needle, repl)
    # ensure `alpha` is in the tokens import
    m = TOKENS_IMPORT_RE.search(t)
    if not m:
        die(f"{key}: tokens import not found")
    names = [n.strip() for n in m.group(1).split(",") if n.strip()]
    if "alpha" not in names:
        names.append("alpha")
        new_imp = (f'import {{ {", ".join(names)} }} from "{m.group(2)}tokens";'
                   f"   // ── {FENCE} ── alpha()")
        t = t[:m.start()] + new_imp + t[m.end():]
    if LEFTOVER_RE.search(t):
        die(f"{key}: token alpha-concat still present after patch")
    return t, len(sites)


# ════════════════════════════════════════════════════════════════════
#  8. backend app_settings_api.py — theme key (dual-tree)
# ════════════════════════════════════════════════════════════════════

BE_A1 = '''_DEFAULTS = {
    "notify_toast": True,
    "notify_audio": True,                  # master sound switch
    "audio_rules":  _default_audio_rules(),  # per-strategy / per-mode
    "show_account_balance": True,          # show Zerodha balance in header
}'''
BE_N1 = f'''# ── {FENCE} ── UI colour theme. Validated against THEMES; anything else
# (typo, future removed theme, older file) falls back to "dark" so the app
# can never boot into an undefined palette.
THEMES = ("dark", "light", "terminal")
THEME_DEFAULT = "dark"


def _norm_theme(v) -> str:
    return v if isinstance(v, str) and v in THEMES else THEME_DEFAULT


_DEFAULTS = {{
    "notify_toast": True,
    "notify_audio": True,                  # master sound switch
    "audio_rules":  _default_audio_rules(),  # per-strategy / per-mode
    "show_account_balance": True,          # show Zerodha balance in header
    "theme": THEME_DEFAULT,                # ── {FENCE} ──
}}'''

BE_A2 = '''        "show_account_balance": data.get("show_account_balance", True) is not False,
    }
    rules_in = data.get("audio_rules", {}) or {}'''
BE_N2 = f'''        "show_account_balance": data.get("show_account_balance", True) is not False,
        "theme": _norm_theme(data.get("theme")),   # ── {FENCE} ──
    }}
    rules_in = data.get("audio_rules", {{}}) or {{}}'''

BE_A3 = '''class AppSettings(BaseModel):
    notify_toast: bool = True
    notify_audio: bool = True
    show_account_balance: bool = True'''
BE_N3 = BE_A3 + f'''
    theme: str = THEME_DEFAULT   # ── {FENCE} ──'''

BE_A4 = '''        "show_account_balance": settings.show_account_balance,
        "audio_rules": {sid: rule.dict() for sid, rule in settings.audio_rules.items()},
    }'''
BE_N4 = f'''        "show_account_balance": settings.show_account_balance,
        "theme": settings.theme,   # ── {FENCE} ── normalised in _merge_defaults
        "audio_rules": {{sid: rule.dict() for sid, rule in settings.audio_rules.items()}},
    }}'''


def edit_backend(t):
    if FENCE in t:
        return t, 0
    for a, lbl in ((BE_A1, "be:defaults"), (BE_A2, "be:merge"),
                   (BE_A3, "be:model"), (BE_A4, "be:post")):
        one(t, a, lbl)
    for a, n in ((BE_A1, BE_N1), (BE_A2, BE_N2), (BE_A3, BE_N3), (BE_A4, BE_N4)):
        t = t.replace(a, n, 1)
    return t, 4


# ════════════════════════════════════════════════════════════════════
#  Gates
# ════════════════════════════════════════════════════════════════════

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


# Behavioural check: tokens/theme/css agree, in Node, on the STAGED text.
NODE_CHECK = r"""
const fs = require("fs");
const [tokPath, themePath, cssPath] = process.argv.slice(2);
const fail = (m) => { console.error("NODE-CHECK FAIL: " + m); process.exit(1); };

// tokens.js is ESM → strip `export ` and eval as CJS
const tokSrc = fs.readFileSync(tokPath, "utf8").replace(/^export /gm, "");
const tok = new Function(tokSrc + "\nreturn { colors, alpha, pnlColor, pnlStyle };")();
const leaves = [];
(function walk(o, p) { for (const k of Object.keys(o)) {
  const v = o[k]; typeof v === "object" ? walk(v, p + k + ".") : leaves.push([p + k, v]); } })(tok.colors, "");
if (leaves.length !== 26) fail("expected 26 colour leaves, got " + leaves.length);
const varNames = new Set();
for (const [k, v] of leaves) {
  const m = /^var\((--c-[a-z-]+)\)$/.exec(v);
  if (!m) fail(`token ${k} is not a var() ref: ${v}`);
  varNames.add(m[1]);
}
if (tok.alpha("var(--c-primary)", 38) !== "color-mix(in srgb, var(--c-primary) 38%, transparent)") fail("alpha() format");
if (tok.pnlColor(5) !== "var(--c-profit)" || tok.pnlColor(-5) !== "var(--c-loss)" || tok.pnlColor(0) !== "var(--c-neutral)") fail("pnlColor");

// every var name used by tokens must be defined in EVERY theme block in css
const css = fs.readFileSync(cssPath, "utf8");
const blocks = {};
for (const id of ["dark", "light", "terminal"]) {
  const re = new RegExp(`\\[data-theme="${id}"\\]\\s*\\{([^}]*)\\}`);
  const m = re.exec(css); if (!m) fail("css block missing for " + id);
  blocks[id] = new Set([...m[1].matchAll(/(--c-[a-z-]+)\s*:/g)].map((x) => x[1]));
}
for (const id of Object.keys(blocks)) for (const v of varNames)
  if (!blocks[id].has(v)) fail(`theme ${id} lacks ${v}`);
// dark block must be value-for-value the old palette (D3: pixel-identical)
const dark = /\[data-theme="dark"\]\s*\{([^}]*)\}/.exec(css)[1];
for (const [n, hex] of [["--c-bg-primary","#020817"],["--c-bg-secondary","#0f172a"],["--c-bg-tertiary","#1e293b"],
  ["--c-bg-input","#060d1a"],["--c-border-light","#334155"],["--c-border-medium","#475569"],["--c-border-dark","#1e293b"],
  ["--c-text-primary","#f8fafc"],["--c-text-secondary","#cbd5e1"],["--c-text-tertiary","#94a3b8"],["--c-text-muted","#64748b"],
  ["--c-primary","#3b82f6"],["--c-primary-hover","#2563eb"],["--c-profit","#10b981"],["--c-loss","#ef4444"],
  ["--c-neutral","#6b7280"],["--c-success","#10b981"],["--c-warning","#f59e0b"],["--c-danger","#ef4444"]])
  if (!new RegExp(`${n}\\s*:\\s*${hex}\\b`).test(dark)) fail(`dark palette drift: ${n} != ${hex}`);
if (!/body\s*\{[^}]*background:\s*var\(--c-bg-primary\)/.test(css)) fail("body not themed");

// theme.js: simulate DOM + localStorage, check applyTheme/normalize/readStored
let attr = null, ls = {}; const listeners = [];
global.window = { localStorage: { getItem: (k) => ls[k] ?? null, setItem: (k, v) => { ls[k] = v; } } };
global.document = { documentElement: {
  getAttribute: () => attr, setAttribute: (_, v) => { attr = v; },
  dispatchEvent: (e) => listeners.forEach((h) => h(e)),
  addEventListener: (_, h) => listeners.push(h), removeEventListener: () => {} } };
global.CustomEvent = function (t, o) { this.type = t; this.detail = o.detail; };
const thSrc = fs.readFileSync(themePath, "utf8")
  .replace(/^import .*react.*;$/m, "const useCallback=(f)=>f, useEffect=()=>{}, useState=(v)=>[typeof v==='function'?v():v,()=>{}];")
  .replace(/^export /gm, "");
const th = new Function(thSrc + "\nreturn { THEMES, THEME_IDS, normalizeTheme, readStoredTheme, applyTheme, currentTheme };")();
if (th.THEME_IDS.join() !== "dark,light,terminal") fail("THEME_IDS");
if (th.normalizeTheme("neon") !== "dark" || th.normalizeTheme(undefined) !== "dark" || th.normalizeTheme("light") !== "light") fail("normalize");
if (th.readStoredTheme() !== "dark") fail("readStored empty → dark");
let fired = 0; listeners.push(() => fired++);
th.applyTheme("light"); if (attr !== "light" || ls["scalp.theme"] !== "light" || fired !== 1) fail("applyTheme light");
th.applyTheme("light"); if (fired !== 1) fail("applyTheme not idempotent");
th.applyTheme("bogus"); if (attr !== "dark" || fired !== 2) fail("applyTheme bogus → dark");
if (th.currentTheme() !== "dark" || th.readStoredTheme() !== "dark") fail("current/stored after bogus");
console.log("  node behavioural check OK (26 tokens × 3 themes, alpha, theme.js)");
"""

# backend behavioural check on the STAGED module text (no FastAPI import needed:
# we exec only the pure helpers by slicing the source).
PY_CHECK = r'''
import re, sys
src = open(sys.argv[1]).read()
head = src[src.index("STRATEGIES = ["): src.index("def _load()")]
ns = {}
exec(head, ns)
m = ns["_merge_defaults"]
assert m({})["theme"] == "dark"
assert m({"theme": "light"})["theme"] == "light"
assert m({"theme": "terminal"})["theme"] == "terminal"
assert m({"theme": "neon"})["theme"] == "dark"
assert m({"theme": 7})["theme"] == "dark"
assert m({"notify_toast": False, "theme": "light"})["notify_toast"] is False
assert ns["_DEFAULTS"]["theme"] == "dark"
assert "theme: str = THEME_DEFAULT" in src
assert '"theme": settings.theme' in src
print("  py behavioural check OK (_merge_defaults theme normalisation)")
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, creates, notes = {}, {}, []

    # ── frontend trees ──
    for root, label in FE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue

        def fe(rel, fn):
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            out, n = fn(open(path).read())
            if n == 0:
                notes.append(f"[{label}] SKIP (fenced): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")

        fe("tokens.js", edit_tokens)
        fe("index.css", edit_css)
        fe("index.js", edit_index_js)
        fe(os.path.join("context", "NotificationProvider.jsx"), edit_np)
        fe(os.path.join("components", "AppSettingsSection.jsx"), edit_appsettings)
        for rel in ALPHA_SITES:
            fe(rel, lambda t, rel=rel: edit_alpha_file(rel, t))
        tpath = os.path.join(root, "theme.js")
        if os.path.isfile(tpath):
            if FENCE not in open(tpath).read():
                die(f"[{label}] theme.js exists but is not ours")
            notes.append(f"[{label}] SKIP (exists): theme.js")
        else:
            creates[tpath] = THEME_JS
            notes.append(f"[{label}] CREATE: theme.js")

    # ── backend trees ──
    for root, label in BE_TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        path = os.path.join(root, "app", "api", "app_settings_api.py")
        if not os.path.isfile(path):
            die(f"[{label}] missing {path}")
        out, n = edit_backend(open(path).read())
        if n == 0:
            notes.append(f"[{label}] SKIP (fenced): app/api/app_settings_api.py")
        else:
            writes[path] = out
            notes.append(f"[{label}] EDIT ({n}): app/api/app_settings_api.py")

    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes and not creates:
        print("\nNothing to do.")
        return

    # ── gates ──
    tmp = tempfile.mkdtemp(prefix="theme_p1_")
    try:
        print("\n── PY_COMPILE ───────────────────────────────────────────────")
        pyn = 0
        for dest, body in writes.items():
            if dest.endswith(".py"):
                st = os.path.join(tmp, f"p{pyn}.py")
                open(st, "w").write(body)
                try:
                    py_compile.compile(st, doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile FAILED for {dest}:\n{e}")
                chk = os.path.join(tmp, "chk.py")
                open(chk, "w").write(PY_CHECK)
                r = subprocess.run([sys.executable, chk, st], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"backend check FAILED for {dest}:\n{r.stdout}{r.stderr[-1500:]}")
                print(r.stdout.strip())
                pyn += 1
        print(f"  {pyn} python file(s) compile clean")

        print("\n── JSX SYNTAX CHECK ─────────────────────────────────────────")
        jsx = {d: b for d, b in {**writes, **creates}.items() if d.endswith((".jsx", ".js"))}
        if a.skip_jsx_check:
            print("  skipped by request")
        else:
            can = os.path.join(tmp, "c.jsx")
            open(can, "w").write("const A = () => <div>{1}</div>;\n")
            cmd, where = find_esbuild(can)
            if cmd is None:
                print("  !! no working esbuild — check SKIPPED (not an error)")
            else:
                print(f"  esbuild via {where}")
                for i, (dest, body) in enumerate(jsx.items()):
                    st = os.path.join(tmp, f"s{i}" + (".jsx" if dest.endswith(".jsx") else ".js"))
                    open(st, "w").write(body)
                    r = subprocess.run(cmd + ["--log-level=warning", "--loader:.js=jsx", st],
                                       capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=120)
                    if r.returncode != 0:
                        die(f"esbuild FAILED for {dest}:\n{r.stderr[:1500]}")
                print(f"  {len(jsx)} file(s) parse clean")

        print("\n── NODE BEHAVIOURAL CHECK ───────────────────────────────────")
        node = shutil.which("node")
        fe_root = FE_TREES[0][0]
        tokb = writes.get(os.path.join(fe_root, "tokens.js"))
        cssb = writes.get(os.path.join(fe_root, "index.css"))
        thb = creates.get(os.path.join(fe_root, "theme.js"))
        if not node:
            print("  !! node not found — check SKIPPED (not an error)")
        elif not (tokb and cssb and thb):
            print("  partial re-run (some files already fenced) — check SKIPPED")
        else:
            tp, cp, hp, sp = (os.path.join(tmp, n) for n in ("t.js", "i.css", "th.js", "chk.js"))
            open(tp, "w").write(tokb); open(cp, "w").write(cssb)
            open(hp, "w").write(thb); open(sp, "w").write(NODE_CHECK)
            r = subprocess.run([node, sp, tp, hp, cp], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                die(f"node check FAILED:\n{r.stdout}{r.stderr[-1500:]}")
            print(r.stdout.strip())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if a.dry_run:
        print("\n--dry-run: no files written.")
        return

    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        shutil.copy2(dest, dest + f".bak-{FENCE}")
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    for dest, body in creates.items():
        open(dest, "w").write(body)
        print("  created " + os.path.relpath(dest, REPO))
    print(f"\nDONE. Themes phase 1 in place (fence {FENCE}).")
    print("  Rebuild required (PyInstaller + Tauri) — backend gained the `theme` key.")
    print("  Default is dark; existing users see no change until they pick one in App Settings.")


if __name__ == "__main__":
    main()
