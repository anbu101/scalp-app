/**
 * LOTS-ONLY SETTINGS — UI_MASK  (v2: visual parity with admin Settings)
 *
 * Intended path: src/pages/LotsOnlySettings.jsx
 *
 * The ENTIRE settings surface for non-admin (STANDARD ui_level) licenses.
 * Rendered by Settings.jsx instead of the full admin settings UI.
 *
 * Per strategy the user can see/edit ONLY lot count field(s) and the
 * PAPER/LIVE mode (LIVE additionally gated server-side by live_trading).
 * GET /api/config is masked to exactly these fields for non-admin and
 * POST /api/save_config whitelist-merges — this page is the curtain,
 * config_routes.py is the wall.
 *
 * DESIGN: deliberately mirrors the admin Settings page's components —
 * StrategyRailItem (accent bar + status dot + dirty dot), DetailPane
 * shell (tertiary header, ModeChip, blue SaveButton), ModeToggle pill
 * group and Field rows — so both ui levels feel like one product.
 *
 * Field paths MUST mirror backend _LOTS_PATHS (app/config/lots_whitelist.py).
 */

import { useEffect, useState, useCallback } from "react";
import { getStrategyConfig, saveStrategyConfig } from "../api";
import { useEntitlements } from "../hooks/useEntitlements";
import { useIsMobile } from "../hooks/useIsMobile";
import { stratName, stratSub } from "../strategies/displayNames";
import AccountSelector from "../components/AccountSelector"; // ACC2
import { colors, spacing, typography } from "../tokens";

/* ── ordering + accents: same fixed order as the host rail; accent hues
   match STRATEGY_ACCENT on the admin page where defined ─────────────── */
const ORDERED_IDS = [
  "SCALP_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "IC_V2", "TSG_V1",
  "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
  "VET_V1",
];

const ACCENT = {
  SCALP_V1: "#f59e0b", SCALP_V3: "#ec4899", SCALP_V5: "#06b6d4",
  IC_V1: "#14b8a6", IC_V2: "#6366f1", TSG_V1: "#eab308",   // ── IC_SPLIT ──
  BB_V1: "#3b82f6", BB_V2: "#3b82f6", HA_V1: "#14b8a6",
  PST_SELL: "#fb7185", PST_HEDGE: "#be123c", TMA_V1: "#8b5cf6", TMA_V2: "#c084fc",
  VET_V1: "#34d399",
};

/* Mirror of backend LOTS_PATHS grouped into user-facing fields. A field
   with several paths writes the SAME value to each (IC legs uniform). */
const LOTS_FIELDS = {
  SCALP_V1:  [{ label: "Number of Lots", helper: "Applies to every trade this strategy takes", paths: ["quantity.lots"] }],
  SCALP_V3:  [{ label: "Number of Lots", helper: "Applies to every trade this strategy takes", paths: ["quantity.lots"] }],
  SCALP_V5:  [{ label: "Number of Lots", helper: "Applies to every trade this strategy takes", paths: ["quantity.lots"] }],
  HA_V1:     [{ label: "Number of Lots", helper: "Applies to every trade this strategy takes", paths: ["quantity.lots"] }],
  BB_V1:     [{ label: "Total Lots", helper: "Both CE and PE trades use this lot count", paths: ["lots"] }],
  BB_V2:     [{ label: "CE Lots", helper: "Lot count for CE-side trades", paths: ["ce_lots"] },
              { label: "PE Lots", helper: "Lot count for PE-side trades", paths: ["pe_lots"] }],
  TSG_V1:    [{ label: "Number of Lots", helper: "Applies to every position this strategy opens", paths: ["lots"] },
              // ── TSG_EXPIRY_LOTS ── 0 = use the same count as above
              { label: "Expiry-Day Lots", helper: "Used only on expiry day · 0 = same as Number of Lots", paths: ["expiry_lots"] }],
  TMA_V1:    [{ label: "Number of Lots", helper: "Applies to both legs of every position", paths: ["c1.sell.lots", "c1.buy.lots"] }],
  TMA_V2:    [{ label: "Number of Lots", helper: "Applies to both legs of every position", paths: ["s1.main.lots", "s1.hedge.lots"] }],
  VET_V1:    [{ label: "Number of Lots", helper: "One position at a time; the wing (when selling) always matches this size", paths: ["quantity.lots"] }],
  PST_SELL:  [{ label: "Lots · A", helper: "First allocation", paths: ["legs.0.lots"] },
              { label: "Lots · B", helper: "Second allocation (0 = off)", paths: ["legs.1.lots"] }],
  PST_HEDGE: [{ label: "Lots · A", helper: "First allocation", paths: ["legs.0.lots"] },
              { label: "Lots · B", helper: "Second allocation (0 = off)", paths: ["legs.1.lots"] }],
  // ── IC_SPLIT ── IC_V1 (legacy EOD) has NO adjustment legs, so its lots
  // field must NOT write adjust.* paths — the backend whitelist rejects
  // them for this id and a silent partial save is worse than an error.
  IC_V1:     [{ label: "Number of Lots", helper: "Applies to every leg of the position", paths: [
                "legs.0.lots", "legs.1.lots", "legs.2.lots", "legs.3.lots"] }],
  IC_V2:     [{ label: "Number of Lots", helper: "Applies to every leg of the position (adjustments included)", paths: [
                "legs.0.lots", "legs.1.lots", "legs.2.lots", "legs.3.lots",
                "adjust.L1.lots", "adjust.L2.lots"] }],
};

// ── IC_SPLIT ── both IC instances support OFF (they ship OFF by default)
const MODES_FOR = (id) =>
  (id === "IC_V1" || id === "IC_V2" ? ["OFF", "PAPER", "LIVE"] : ["PAPER", "LIVE"]);
const MODE_LABEL = { OFF: "⏸ OFF", PAPER: "✏️ PAPER", LIVE: "🟢 LIVE" };
const modeActiveColor = (m) =>
  m === "LIVE" ? colors.success : m === "PAPER" ? colors.primary : colors.text.muted;

/* ── dotted-path helpers (list indices are numeric segments) ─────────── */
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
      if (cur[seg] == null || typeof cur[seg] !== "object") cur[seg] = nextIsIndex ? [] : {};
      cur = cur[seg];
    }
  }
  const last = segs[segs.length - 1];
  if (Array.isArray(cur)) cur[Number(last)] = value;
  else cur[last] = value;
}

/* ─────────────────────────────────────────────
   Visual components — mirrored from admin Settings
───────────────────────────────────────────── */

const microLabel = {
  fontSize: 10, fontWeight: 500, letterSpacing: "0.5px",
  textTransform: "uppercase", color: colors.text.muted,
};

function RailItem({ id, mode, active, dirty, onClick }) {
  const isLive = mode === "LIVE";
  const isOff = mode === "OFF";
  const dot = mode == null ? colors.text.muted : isOff ? colors.text.muted : isLive ? colors.success : colors.primary;
  const modeLabel = mode == null ? "" : isOff ? "OFF" : isLive ? "LIVE" : "PAPER";
  const ac = ACCENT[id] || colors.primary;
  return (
    <button
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      style={{
        display: "flex", alignItems: "center", gap: spacing.sm,
        width: "100%", textAlign: "left",
        padding: `${spacing.sm}px ${spacing.md}px`,
        borderRadius: 8,
        border: `1px solid ${active ? `${ac}66` : "transparent"}`,
        background: active ? `${ac}1f` : "transparent",
        cursor: "pointer",
        transition: "background 0.15s ease, border-color 0.15s ease",
        position: "relative", overflow: "hidden",
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = colors.bg.secondary + "80"; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >
      <span style={{
        position: "absolute", left: 0, top: 0, bottom: 0, width: 3,
        borderRadius: "2px 0 0 2px", background: ac,
        opacity: active ? 1 : 0.55, transition: "opacity 0.2s ease",
      }} />
      <span style={{
        width: 8, height: 8, borderRadius: "50%", flexShrink: 0, marginLeft: 2,
        background: dot, boxShadow: `0 0 6px ${dot}66`,
      }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          fontSize: 13, fontWeight: 600,
          color: active ? colors.text.primary : colors.text.secondary,
        }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {stratName(id, false)}
          </span>
          {dirty && (
            <span title="Unsaved changes" style={{
              width: 6, height: 6, borderRadius: "50%",
              background: colors.warning, flexShrink: 0,
            }} />
          )}
        </div>
        <div style={{ ...microLabel, marginTop: 2, fontSize: 9 }}>
          {stratSub(id, false)}{modeLabel ? ` · ${modeLabel}` : ""}
        </div>
      </div>
    </button>
  );
}

function ModeChip({ mode }) {
  if (!mode) return null;
  const c = modeActiveColor(mode);
  return (
    <span style={{
      ...microLabel, color: c, border: `1px solid ${c}55`,
      background: `${c}14`, padding: "3px 10px", borderRadius: 5, flexShrink: 0,
    }}>
      {mode}
    </span>
  );
}

function ModeToggle({ value, onChange, modes }) {
  const isMobile = useIsMobile();
  return (
    <div style={{
      display: "flex", width: isMobile ? "100%" : "auto", gap: 3,
      background: colors.bg.tertiary, padding: 3, borderRadius: 6,
      border: `1px solid ${colors.border.medium}`,
    }}>
      {modes.map((m) => {
        const active = value === m;
        const activeBg = modeActiveColor(m);
        return (
          <button key={m} onClick={() => onChange(m)}
            style={{
              flex: isMobile ? 1 : undefined,
              padding: isMobile ? "8px 14px" : "4px 14px",
              borderRadius: 4, border: "none",
              background: active ? activeBg : "transparent",
              color: active ? (m === "OFF" ? colors.bg.primary : "#fff") : colors.text.muted,
              fontSize: 11, fontWeight: 600, cursor: "pointer",
              transition: "all 0.15s ease",
              textTransform: "uppercase", letterSpacing: "0.3px",
            }}
          >
            {MODE_LABEL[m] || m}
          </button>
        );
      })}
    </div>
  );
}

function SaveButton({ onClick, saving, status, disabled }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.sm }}>
      {status && (
        <span style={{
          padding: "3px 10px", borderRadius: 5, fontSize: 11, fontWeight: 600,
          background: status === "success" ? colors.successBg : colors.warningBg,
          color: status === "success" ? colors.success : colors.warning,
          border: `1px solid ${(status === "success" ? colors.success : colors.warning)}30`,
        }}>
          {status === "success" ? "✓ Saved" : "✗ Failed"}
        </span>
      )}
      <button onClick={onClick} disabled={saving || disabled}
        style={{
          padding: "6px 18px", borderRadius: 5, border: "none",
          background: saving || disabled ? colors.bg.tertiary : colors.primary,
          color: saving || disabled ? colors.text.muted : "#fff",
          fontSize: 12, fontWeight: 600,
          cursor: saving || disabled ? "not-allowed" : "pointer",
          transition: "background 0.15s",
        }}
        onMouseEnter={(e) => { if (!saving && !disabled) e.target.style.background = colors.primaryHover; }}
        onMouseLeave={(e) => { if (!saving && !disabled) e.target.style.background = colors.primary; }}
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

const LABEL_W = 160;

function Field({ label: lbl, helper, children }) {
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: spacing.lg,
      padding: `${spacing.md}px 0`,
      borderBottom: `1px solid ${colors.border.dark}`,
    }}>
      <div style={{ width: LABEL_W, flexShrink: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: colors.text.primary }}>{lbl}</div>
        {helper && (
          <div style={{ fontSize: 10.5, color: colors.text.muted, marginTop: 3, lineHeight: 1.4 }}>
            {helper}
          </div>
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}>{children}</div>
    </div>
  );
}

function SectionTitle({ children }) {
  return (
    <div style={{
      ...microLabel, paddingBottom: 6, marginTop: spacing.xl,
      borderBottom: `1px solid ${colors.border.medium}`,
    }}>
      {children}
    </div>
  );
}

function NumberInput({ value, onChange, max }) {
  return (
    <input
      type="number" min={0} step={1} max={max || undefined} value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      style={{
        width: 110, padding: "7px 10px", borderRadius: 6,
        background: colors.bg.input, color: colors.text.primary,
        border: `1px solid ${colors.border.light}`,
        fontSize: 13, ...typography.mono,
      }}
    />
  );
}

/* ─────────────────────────────────────────────
   Per-strategy detail pane (data logic unchanged)
───────────────────────────────────────────── */

function StrategyDetail({ id, onDirtyChange, maxLots }) {   // ── MAX_LOTS ──
  const fields = LOTS_FIELDS[id] || [];
  const [values, setValues] = useState({});
  const [mode, setMode] = useState(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [dirty, setDirty] = useState(false);

  const markDirty = (d) => { setDirty(d); onDirtyChange?.(id, d); };

  const load = useCallback(async () => {
    try {
      const cfg = (await getStrategyConfig(id)) || {};
      // Derive fields from id INSIDE the callback — [id] deps are then
      // legitimately complete, no eslint suppression needed.
      const flds = LOTS_FIELDS[id] || [];
      const v = {};
      for (const f of flds) v[f.label] = pathGet(cfg, f.paths[0]) ?? 1;
      setValues(v);
      setMode(cfg.trade_execution_mode ?? "PAPER");
      setDirty(false);
    } catch { /* keep last state */ }
  }, [id]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => onDirtyChange?.(id, false), [id, onDirtyChange]);

  async function save() {
    setSaving(true); setStatus("");
    try {
      const payload = {};
      for (const f of fields) {
        let n = Math.max(0, Math.floor(Number(values[f.label]) || 0));
        // ── MAX_LOTS ── client-side clamp mirrors the server's _clamp_lots
        // (the server remains the wall; this keeps the UI honest pre-save).
        if (maxLots > 0 && n > maxLots) n = maxLots;
        for (const p of f.paths) pathSet(payload, p, n);
      }
      if (mode != null) payload.trade_execution_mode = mode;
      await saveStrategyConfig(id, payload);
      setStatus("success"); markDirty(false);
      // Reload so the pane reflects what the backend ACTUALLY persisted
      // (mode may downgrade to PAPER via the live_trading gate; lots may
      // clamp to the license max).
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
      borderRadius: 12, overflow: "hidden",
      display: "flex", flexDirection: "column", minHeight: 320,
    }}>
      {/* Header — mirrors admin DetailPane */}
      <div style={{
        padding: `${spacing.md}px ${spacing.xl}px`,
        background: colors.bg.tertiary,
        borderBottom: `1px solid ${colors.border.medium}`,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexShrink: 0, gap: spacing.md,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.md, minWidth: 0 }}>
          <span style={{
            width: 10, height: 10, borderRadius: 3, flexShrink: 0,
            background: ACCENT[id] || colors.primary,
          }} />
          <span style={{ fontSize: 16, fontWeight: 700, color: colors.text.primary, flexShrink: 0 }}>
            {stratName(id, false)}
          </span>
          <span style={{
            fontSize: 11, color: colors.text.muted,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            {stratSub(id, false)}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, flexShrink: 0 }}>
          <ModeChip mode={mode} />
          <SaveButton onClick={save} saving={saving} status={status} disabled={!dirty} />
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: "auto", padding: `0 ${spacing.xl}px ${spacing.xl}px` }}>
        <SectionTitle>Execution</SectionTitle>
        <Field label="Mode" helper="LIVE = real orders · PAPER = simulated · changes apply from the next trade">
          <ModeToggle value={mode} modes={MODES_FOR(id)}
            onChange={(m) => { setMode(m); markDirty(true); }} />
        </Field>

        <SectionTitle>Order Quantity</SectionTitle>
        {fields.map((f) => (
          <Field key={f.label} label={f.label}
            helper={maxLots > 0 ? `${f.helper} · maximum ${maxLots} lots` : f.helper}>
            <NumberInput
              value={values[f.label]} max={maxLots}
              onChange={(v) => { setValues((s) => ({ ...s, [f.label]: v })); markDirty(true); }}
            />
          </Field>
        ))}
        <AccountSelector strategyId={id} /> {/* ACC2 */}

        <div style={{ fontSize: 11, color: colors.text.muted, marginTop: spacing.lg, lineHeight: 1.5 }}>
          All other behavior for this strategy is managed for you.
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Page — rail + detail, mirroring admin Settings
───────────────────────────────────────────── */

export default function LotsOnlySettings() {
  const { loaded, allowsStrategy, license } = useEntitlements();
  // ── MAX_LOTS ── cap from the signed token (server floors non-admin to 5)
  const maxLots = Number(license?.entitlements?.max_lots) || 0;
  const isMobile = useIsMobile();
  const [primaryId, setPrimaryId] = useState(null);
  const [modes, setModes] = useState({});
  const [dirtyMap, setDirtyMap] = useState({});

  const onDirtyChange = useCallback((id, d) => {
    setDirtyMap((m) => (m[id] === d ? m : { ...m, [id]: d }));
  }, []);

  // Rail status dots: light one fetch per entitled strategy.
  const ids = ORDERED_IDS.filter((id) => allowsStrategy(id));
  useEffect(() => {
    if (!loaded) return;
    let alive = true;
    (async () => {
      const next = {};
      for (const id of ids) {
        try {
          const cfg = (await getStrategyConfig(id)) || {};
          next[id] = cfg.trade_execution_mode ?? "PAPER";
        } catch { next[id] = null; }
      }
      if (alive) setModes(next);
    })();
    return () => { alive = false; };
    // deps: ids derives from loaded + entitlements; join() keeps it stable
  }, [loaded, ids.join(",")]);

  // Fail CLOSED — never flash an unfiltered surface pre-license.
  if (!loaded) return null;

  const active = primaryId && ids.includes(primaryId) ? primaryId : ids[0];

  if (!ids.length) {
    return (
      <div style={{ color: colors.text.muted, fontSize: 13, padding: spacing.xxl, textAlign: "center" }}>
        No strategies are enabled on this license.
      </div>
    );
  }

  return (
    <div style={{
      display: "flex", flexDirection: isMobile ? "column" : "row",
      gap: spacing.lg, padding: spacing.lg,
      maxWidth: 1100, margin: "0 auto", alignItems: "stretch",
    }}>
      {/* Rail */}
      <div style={{
        width: isMobile ? "100%" : 230, flexShrink: 0,
        background: colors.bg.secondary,
        border: `1px solid ${colors.border.medium}`,
        borderRadius: 12,
        padding: spacing.sm,
        display: "flex",
        flexDirection: isMobile ? "row" : "column",
        gap: 2,
        overflowX: isMobile ? "auto" : "hidden",
        alignSelf: "flex-start",
        position: isMobile ? "static" : "sticky", top: spacing.lg,
      }}>
        <div style={{ ...microLabel, padding: `${spacing.xs}px ${spacing.md}px ${spacing.sm}px` }}>
          {isMobile ? "" : "Strategies"}
        </div>
        {ids.map((id) => (
          <div key={id} style={{ minWidth: isMobile ? 170 : undefined }}>
            <RailItem
              id={id}
              mode={modes[id]}
              active={active === id}
              dirty={!!dirtyMap[id]}
              onClick={() => setPrimaryId(id)}
            />
          </div>
        ))}
      </div>

      {/* Detail */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <StrategyDetail key={active} id={active} onDirtyChange={onDirtyChange} maxLots={maxLots} />
      </div>
    </div>
  );
}