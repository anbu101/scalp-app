#!/usr/bin/env python3
"""
apply_theme_nonadmin_20260908.py — THEME_NONADMIN_20260908

Why: the theme picker lives in AppSettingsSection, which is mounted only on the
admin Settings page. Non-admin (STANDARD ui_level) licenses render
LotsOnlySettings, which has only the strategy rail — so they could never see or
switch themes. /api/app_settings is not admin-gated on the backend, so this is
a curtain fix, not a wall change.

What: expose ONLY the theme picker to non-admin — not the notification/sound
matrix, which lists strategies and would breach UI_MASK.

  frontend/src/components/AppSettingsSection.jsx
      new named export `AppearanceSection` — a self-contained card that renders
      the existing ThemeRow via useAppSettings. Default export (admin) unchanged.
  frontend/src/pages/LotsOnlySettings.jsx
      import AppearanceSection; an "Appearance" rail item pinned at the bottom
      of the strategy rail (mirrors the admin APP item — same accent-bar rail
      styling, no strategy name lookups); when active, the detail pane renders
      AppearanceSection instead of StrategyDetail. Fail-closed order is kept:
      the rail item is added after the `if (!loaded) return null` guard and
      the `ids.length` check is untouched — a license with zero strategies
      still gets the Appearance pane, which is the point.

Idempotent; staged all-or-nothing; esbuild gate; .bak-THEME_NONADMIN_20260908
backups. Frontend-only -> npm run build + Tauri frontend rebuild. No backend,
no PyInstaller.
"""
import os, shutil, subprocess, sys

FENCE = "THEME_NONADMIN_20260908"
BAK = f".bak-{FENCE}"
ROOT = os.getcwd()
if not os.path.isfile(os.path.join(ROOT, "frontend", "src", "index.css")):
    ROOT = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isfile(os.path.join(ROOT, "frontend", "src", "index.css")):
        sys.exit("run from the scalp-app repo root")
SRC = os.path.join(ROOT, "frontend", "src")
def P(rel): return os.path.join(SRC, rel)
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def must(c, m):
    if not c: sys.exit(f"ABORT: {m}")

# ── AppSettingsSection.jsx: named export AppearanceSection ─────────────────────
ASS_EDITS = [
    ('export default function AppSettingsSection() {\n  const { settings, loading, saveSettings } = useAppSettings();\n',
     f'''// ── {FENCE} ── theme picker alone, for the non-admin Settings surface.
// Deliberately excludes the notification/sound matrix (it lists strategies —
// UI_MASK). Same persistence path as the admin row: saveSettings -> backend ->
// applyTheme inside NotificationProvider.saveSettings.
export function AppearanceSection() {{
  const {{ settings, loading, saveSettings }} = useAppSettings();
  return (
    <div style={{{{
      background: colors.bg.secondary,
      border: `1px solid ${{colors.border.medium}}`,
      borderRadius: 12, overflow: "hidden",
      display: "flex", flexDirection: "column", minHeight: 320,
    }}}}>
      <div style={{{{
        padding: `${{spacing.md}}px ${{spacing.xl}}px`,
        background: colors.bg.tertiary,
        borderBottom: `1px solid ${{colors.border.medium}}`,
        display: "flex", alignItems: "center", gap: spacing.md,
      }}}}>
        <span style={{{{ width: 10, height: 10, borderRadius: 3, background: colors.primary, flexShrink: 0 }}}} />
        <span style={{{{ fontSize: 16, fontWeight: 700, color: colors.text.primary }}}}>App Settings</span>
        <span style={{{{ fontSize: 11, color: colors.text.muted }}}}>Appearance</span>
      </div>
      <div style={{{{ padding: `0 ${{spacing.xl}}px` }}}}>
        <ThemeRow
          value={{normalizeTheme(settings.theme)}}
          onChange={{(id) => saveSettings({{ ...settings, theme: id }})}}
          loading={{loading}}
        />
      </div>
    </div>
  );
}}

export default function AppSettingsSection() {{
  const {{ settings, loading, saveSettings }} = useAppSettings();
''', 1),
]

# ── LotsOnlySettings.jsx ───────────────────────────────────────────────────────
LOS_EDITS = [
    ('import AccountSelector from "../components/AccountSelector"; // ACC2\n',
     'import AccountSelector from "../components/AccountSelector"; // ACC2\n'
     f'import {{ AppearanceSection }} from "../components/AppSettingsSection";   // ── {FENCE} ── theme picker for non-admin\n', 1),

    # rail item component, placed right before ModeChip (after RailItem)
    ('function ModeChip({ mode }) {\n',
     f'''// ── {FENCE} ── "Appearance" rail item. Mirrors RailItem's chrome without any
// stratName/stratSub lookup (no strategy id behind it). Pinned below strategies.
function AppearanceRailItem({{ active, onClick }}) {{
  const ac = colors.primary;
  return (
    <button
      onClick={{onClick}}
      aria-current={{active ? "true" : undefined}}
      style={{{{
        display: "flex", alignItems: "center", gap: spacing.sm,
        width: "100%", textAlign: "left",
        padding: `${{spacing.sm}}px ${{spacing.md}}px`,
        borderRadius: 8,
        border: `1px solid ${{active ? `${{ac}}66` : "transparent"}}`,
        background: active ? `${{ac}}1f` : "transparent",
        cursor: "pointer",
        transition: "background 0.15s ease, border-color 0.15s ease",
        position: "relative", overflow: "hidden",
      }}}}
      onMouseEnter={{(e) => {{ if (!active) e.currentTarget.style.background = alpha(colors.bg.secondary, 50); }}}}
      onMouseLeave={{(e) => {{ if (!active) e.currentTarget.style.background = "transparent"; }}}}
    >
      <span style={{{{
        position: "absolute", left: 0, top: 0, bottom: 0, width: 3,
        borderRadius: "2px 0 0 2px", background: ac,
        opacity: active ? 1 : 0.55, transition: "opacity 0.2s ease",
      }}}} />
      <span style={{{{ fontSize: 13, flexShrink: 0, marginLeft: 2 }}}}>🎨</span>
      <div style={{{{ minWidth: 0, flex: 1 }}}}>
        <div style={{{{ fontSize: 13, fontWeight: 600, color: active ? colors.text.primary : colors.text.secondary }}}}>
          App Settings
        </div>
        <div style={{{{ ...microLabel, marginTop: 2, fontSize: 9 }}}}>Appearance · theme</div>
      </div>
    </button>
  );
}}

function ModeChip({{ mode }}) {{
''', 1),

    # active-id resolution: allow the pseudo id
    ('  const active = primaryId && ids.includes(primaryId) ? primaryId : ids[0];\n',
     f'  // ── {FENCE} ── "APPEARANCE" is a pseudo rail id (not a strategy); it is valid\n'
     f'  // even when the license has no strategies, so it is checked before ids[].\n'
     f'  const active = primaryId === "APPEARANCE" ? "APPEARANCE"\n'
     f'    : (primaryId && ids.includes(primaryId) ? primaryId : ids[0]);\n', 1),

    # empty-license branch: still offer appearance
    ('  if (!ids.length) {\n'
     '    return (\n'
     '      <div style={{ color: colors.text.muted, fontSize: 13, padding: spacing.xxl, textAlign: "center" }}>\n'
     '        No strategies are enabled on this license.\n'
     '      </div>\n'
     '    );\n'
     '  }\n',
     f'  if (!ids.length) {{\n'
     f'    // ── {FENCE} ── no strategies: still let them pick a theme.\n'
     f'    return (\n'
     f'      <div style={{{{ maxWidth: 720, margin: "0 auto", padding: spacing.lg }}}}>\n'
     f'        <div style={{{{ color: colors.text.muted, fontSize: 13, padding: spacing.xl, textAlign: "center" }}}}>\n'
     f'          No strategies are enabled on this license.\n'
     f'        </div>\n'
     f'        <AppearanceSection />\n'
     f'      </div>\n'
     f'    );\n'
     f'  }}\n', 1),

    # rail: append the item after the strategy list
    ('        {ids.map((id) => (\n'
     '          <div key={id} style={{ minWidth: isMobile ? 170 : undefined }}>\n'
     '            <RailItem\n'
     '              id={id}\n'
     '              mode={modes[id]}\n'
     '              active={active === id}\n'
     '              dirty={!!dirtyMap[id]}\n'
     '              onClick={() => setPrimaryId(id)}\n'
     '            />\n'
     '          </div>\n'
     '        ))}\n'
     '      </div>\n',
     '        {ids.map((id) => (\n'
     '          <div key={id} style={{ minWidth: isMobile ? 170 : undefined }}>\n'
     '            <RailItem\n'
     '              id={id}\n'
     '              mode={modes[id]}\n'
     '              active={active === id}\n'
     '              dirty={!!dirtyMap[id]}\n'
     '              onClick={() => setPrimaryId(id)}\n'
     '            />\n'
     '          </div>\n'
     '        ))}\n'
     f'        {{/* ── {FENCE} ── pinned below strategies, mirrors the admin APP rail item */}}\n'
     '        <div style={{ minWidth: isMobile ? 170 : undefined, marginTop: isMobile ? 0 : spacing.sm }}>\n'
     '          <AppearanceRailItem active={active === "APPEARANCE"} onClick={() => setPrimaryId("APPEARANCE")} />\n'
     '        </div>\n'
     '      </div>\n', 1),

    # detail pane: branch on the pseudo id
    ('        <StrategyDetail key={active} id={active} onDirtyChange={onDirtyChange} maxLots={maxLots} />\n',
     f'        {{active === "APPEARANCE"\n'
     f'          ? <AppearanceSection />   /* ── {FENCE} ── */\n'
     f'          : <StrategyDetail key={{active}} id={{active}} onDirtyChange={{onDirtyChange}} maxLots={{maxLots}} />}}\n', 1),
]

EDITS = [("components/AppSettingsSection.jsx", ASS_EDITS), ("pages/LotsOnlySettings.jsx", LOS_EDITS)]

staged = {}
for rel, pairs in EDITS:
    path = P(rel); must(os.path.isfile(path), f"missing {rel}")
    src = read(path)
    if FENCE in src:
        for old, new, n in pairs:
            must(src.count(new) == n and (old in new or src.count(old) == 0), f"{rel}: fence present but an edit is missing/damaged")
        print(f"{rel:<40} already applied"); continue
    out = src
    for old, new, n in pairs:
        must(out.count(old) == n, f"{rel}: anchor count {out.count(old)} != {n} for:\n{old[:110]}")
        out = out.replace(old, new)
    staged[path] = out

if not staged:
    print("nothing to do — tree already at", FENCE); sys.exit(0)

esb = os.path.join(ROOT, "frontend", "node_modules", ".bin", "esbuild")
if not os.path.exists(esb): esb = shutil.which("esbuild")
must(esb, "esbuild not found (frontend/node_modules/.bin/esbuild)")
tmp = os.path.join(ROOT, f".stage-{FENCE}"); os.makedirs(tmp, exist_ok=True)
try:
    for path, content in staged.items():
        t = os.path.join(tmp, os.path.basename(path))
        with open(t, "w", encoding="utf-8") as f: f.write(content)
        r = subprocess.run([esb, t, "--loader:.jsx=jsx", "--log-level=error", "--outfile=" + t + ".out"],
                           capture_output=True, text=True)
        must(r.returncode == 0, f"esbuild gate failed on {os.path.relpath(path, ROOT)}:\n{r.stderr}")
    # UI_MASK invariant: the non-admin page must not import the admin default export
    los = staged.get(P("pages/LotsOnlySettings.jsx")) or read(P("pages/LotsOnlySettings.jsx"))
    must("import AppSettingsSection from" not in los, "LotsOnlySettings must not mount the admin AppSettingsSection")
    must(los.count("<AppearanceSection />") == 2, "expected exactly two AppearanceSection mounts (empty-license + detail pane)")
    # fail-closed guard still precedes everything
    must(los.index("if (!loaded) return null;") < los.index('primaryId === "APPEARANCE" ? "APPEARANCE"'), "fail-closed guard must precede the rail logic")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

for path, content in staged.items():
    shutil.copy2(path, path + BAK)
    with open(path, "w", encoding="utf-8") as f: f.write(content)
    print(f"wrote  {os.path.relpath(path, ROOT)}")

print(f"\n{FENCE} applied.")
print("next: cd frontend && npm run build, then ./desktop/build-scalp.sh (frontend-only; no PyInstaller needed).")
print("verify with a non-admin license: Settings shows 'App Settings · Appearance' at the bottom of the rail; theme applies instantly and survives relaunch.")
