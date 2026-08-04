/**
 * LOTS-ONLY SETTINGS — UI_MASK
 *
 * Intended path: src/pages/LotsOnlySettings.jsx
 *
 * The ENTIRE settings surface for non-admin (STANDARD ui_level) licenses.
 * Rendered by Settings.jsx instead of the full admin settings UI.
 *
 * Per strategy the user can see/edit ONLY:
 *   - lot count field(s)          (whitelisted server-side too)
 *   - PAPER / LIVE mode toggle    (LIVE is additionally gated server-side
 *                                  by the live_trading entitlement)
 *
 * Everything else runs on the stored/default config and is never sent to
 * this client: GET /api/config is masked to exactly these fields for
 * non-admin, and POST /api/save_config whitelist-merges, so even a
 * hand-crafted payload cannot change other params. This page is the
 * curtain; config_routes.py is the wall.
 *
 * Field paths MUST mirror backend _LOTS_PATHS (config_routes.py). If they
 * drift, the save silently no-ops on the extra paths — verify both files
 * together on any change.
 */

import { useEffect, useState, useCallback } from "react";
import { getStrategyConfig, saveStrategyConfig } from "../api";
import { useEntitlements } from "../hooks/useEntitlements";
import { stratName, stratSub } from "../strategies/displayNames";
import { colors, spacing, typography } from "../tokens";

// Same fixed order as StrategyHost / admin Settings rail.
const ORDERED_IDS = [
  "SCALP_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "TSG_V1",
  "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1",
];

// Mirror of backend _LOTS_PATHS, grouped into user-facing fields.
// A field with multiple paths writes the SAME value to every path
// (e.g. one "Lots" drives all IC legs uniformly).
const LOTS_FIELDS = {
  SCALP_V1:  [{ label: "Lots", paths: ["quantity.lots"] }],
  SCALP_V3:  [{ label: "Lots", paths: ["quantity.lots"] }],
  SCALP_V5:  [{ label: "Lots", paths: ["quantity.lots"] }],
  HA_V1:     [{ label: "Lots", paths: ["quantity.lots"] }],
  BB_V1:     [{ label: "Lots", paths: ["lots"] }],
  BB_V2:     [{ label: "CE Lots", paths: ["ce_lots"] },
              { label: "PE Lots", paths: ["pe_lots"] }],
  TSG_V1:    [{ label: "Lots", paths: ["lots"] }],
  TMA_V1:    [{ label: "Lots", paths: ["c1.sell.lots", "c1.buy.lots"] }],
  PST_SELL:  [{ label: "Lots · A", paths: ["legs.0.lots"] },
              { label: "Lots · B", paths: ["legs.1.lots"] }],
  PST_HEDGE: [{ label: "Lots · A", paths: ["legs.0.lots"] },
              { label: "Lots · B", paths: ["legs.1.lots"] }],
  IC_V1:     [{ label: "Lots", paths: [
                "legs.0.lots", "legs.1.lots", "legs.2.lots", "legs.3.lots",
                "adjust.L1.lots", "adjust.L2.lots"] }],
};

const MODES_FOR = (id) => (id === "IC_V1" ? ["OFF", "PAPER", "LIVE"] : ["PAPER", "LIVE"]);

function pathGet(obj, dotted) {
  let cur = obj;
  for (const seg of dotted.split(".")) {
    if (cur == null) return undefined;
    cur = Array.isArray(cur) ? cur[Number(seg)] : cur[seg];
  }
  return cur;
}

function pathSet(obj, dotted, value) {
  const segs = dotted.split(".");
  let cur = obj;
  for (let i = 0; i < segs.length - 1; i++) {
    const seg = segs[i];
    const nextIsIndex = /^\d+$/.test(segs[i + 1]);
    if (Array.isArray(cur)) {
      const idx = Number(seg);
      if (cur[idx] == null) cur[idx] = nextIsIndex ? [] : {};
      cur = cur[idx];
    } else {
      if (cur[seg] == null || typeof cur[seg] !== "object") {
        cur[seg] = nextIsIndex ? [] : {};
      }
      cur = cur[seg];
    }
  }
  const last = segs[segs.length - 1];
  if (Array.isArray(cur)) cur[Number(last)] = value;
  else cur[last] = value;
}

function modeColor(m) {
  if (m === "LIVE") return colors.danger ?? "#ef4444";
  if (m === "PAPER") return colors.success ?? "#10b981";
  return colors.text?.muted ?? "#6b7280";
}

function StrategyLotsCard({ id }) {
  const fields = LOTS_FIELDS[id] || [];
  const [values, setValues] = useState({});   // fieldLabel -> number
  const [mode, setMode] = useState(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");   // "", "saved", "error"
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    try {
      const cfg = (await getStrategyConfig(id)) || {};
      // Derive fields from id INSIDE the callback — [id] deps are then
      // legitimately complete, no eslint suppression needed (an unknown-rule
      // disable comment hard-errors CRA's eslint during `react-scripts build`).
      const flds = LOTS_FIELDS[id] || [];
      const v = {};
      for (const f of flds) v[f.label] = pathGet(cfg, f.paths[0]) ?? 1;
      setValues(v);
      setMode(cfg.trade_execution_mode ?? "PAPER");
      setDirty(false);
    } catch { /* keep last state */ }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      const payload = {};
      for (const f of fields) {
        const n = Math.max(0, Math.floor(Number(values[f.label]) || 0));
        for (const p of f.paths) pathSet(payload, p, n);
      }
      if (mode != null) payload.trade_execution_mode = mode;
      await saveStrategyConfig(id, payload);
      setStatus("saved");
      setDirty(false);
      // Reload so the card reflects what the backend ACTUALLY persisted
      // (mode may have been downgraded to PAPER by the live_trading gate,
      // lots may have been clamped to the license max).
      await load();
      setTimeout(() => setStatus(""), 2500);
    } catch {
      setStatus("error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{
      background: colors.bg.secondary,
      border: `1px solid ${colors.border.medium}`,
      borderRadius: 10, padding: spacing.lg,
      display: "flex", flexDirection: "column", gap: spacing.md,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: spacing.md }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: colors.text.primary }}>
            {stratName(id, false)}
          </div>
          <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 2 }}>
            {stratSub(id, false)}
          </div>
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: "0.5px",
          color: modeColor(mode), border: `1px solid ${modeColor(mode)}55`,
          borderRadius: 4, padding: "2px 8px", flexShrink: 0,
        }}>
          {mode ?? "…"}
        </span>
      </div>

      <div style={{ display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "flex-end" }}>
        {fields.map((f) => (
          <label key={f.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ ...typography.label, fontSize: 10, color: colors.text.muted }}>{f.label}</span>
            <input
              type="number" min={0} step={1}
              value={values[f.label] ?? ""}
              onChange={(e) => { setValues((v) => ({ ...v, [f.label]: e.target.value })); setDirty(true); }}
              style={{
                width: 90, padding: "7px 10px", borderRadius: 6,
                background: colors.bg.primary, color: colors.text.primary,
                border: `1px solid ${colors.border.medium}`, fontSize: 13,
              }}
            />
          </label>
        ))}

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ ...typography.label, fontSize: 10, color: colors.text.muted }}>Mode</span>
          <div style={{ display: "flex", gap: 4 }}>
            {MODES_FOR(id).map((m) => (
              <button key={m}
                onClick={() => { setMode(m); setDirty(true); }}
                style={{
                  padding: "6px 12px", borderRadius: 6, fontSize: 11, fontWeight: 700,
                  cursor: "pointer",
                  border: `1px solid ${mode === m ? modeColor(m) : colors.border.medium}`,
                  background: mode === m ? `${modeColor(m)}22` : "transparent",
                  color: mode === m ? modeColor(m) : colors.text.muted,
                }}>
                {m}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={save}
          disabled={saving || !dirty}
          style={{
            marginLeft: "auto", padding: "8px 18px", borderRadius: 6,
            fontSize: 12, fontWeight: 700, cursor: dirty ? "pointer" : "default",
            border: `1px solid ${colors.primary}66`,
            background: dirty ? `${colors.primary}22` : "transparent",
            color: dirty ? colors.text.primary : colors.text.muted,
            opacity: saving ? 0.6 : 1,
          }}>
          {saving ? "Saving…" : status === "saved" ? "Saved ✓" : status === "error" ? "Retry" : "Save"}
        </button>
      </div>
    </div>
  );
}

export default function LotsOnlySettings() {
  const { loaded, allowsStrategy } = useEntitlements();

  // Fail CLOSED on this page — never flash the admin-shaped UI or an
  // unfiltered list before the first license read resolves.
  if (!loaded) return null;

  const ids = ORDERED_IDS.filter((id) => allowsStrategy(id));

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: spacing.xl, display: "flex", flexDirection: "column", gap: spacing.md }}>
      <div>
        <div style={{ ...typography.label, color: colors.text.muted }}>Settings</div>
        <div style={{ fontSize: 20, fontWeight: 700, color: colors.text.primary, marginTop: 2 }}>
          Strategy Lots
        </div>
        <div style={{ fontSize: 12, color: colors.text.muted, marginTop: 4 }}>
          Set how many lots each strategy trades. All other behavior is managed for you.
        </div>
      </div>

      {ids.map((id) => <StrategyLotsCard key={id} id={id} />)}

      {ids.length === 0 && (
        <div style={{ color: colors.text.muted, fontSize: 13, padding: spacing.xl, textAlign: "center" }}>
          No strategies are enabled on this license.
        </div>
      )}
    </div>
  );
}
