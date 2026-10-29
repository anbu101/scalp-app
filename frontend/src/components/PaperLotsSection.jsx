/**
 * ── PAPER_LOTS_20261030 ── paper-twin lot counts for PAPER+LIVE mode.
 *
 * Intended path: src/components/PaperLotsSection.jsx
 *
 * One paper value per lots field. Blank / 0 = same as live. Stored in the
 * strategy config as  paper_lots = { "<lots path, '.' → '/'>": n }  so the
 * backend whitelist (config/lots_whitelist.py friend_paths), the licence
 * max-lots clamp and the paper-twin sizer (trading/paper_lots.py) all key
 * off the same names. Used by the admin Settings page (one row per lots
 * field) and by LotsOnlySettings (one row per user-facing field; a field
 * covering several paths writes the same value to each).
 */

import { colors, spacing, typography } from "../tokens";

export const paperKey = (path) => String(path).replace(/\./g, "/");

export const isPaperLive = (mode) =>
  String(mode || "").toUpperCase().replace(/[+\s-]/g, "_") === "PAPER_LIVE";

function pathGet(obj, dotted) {
  let cur = obj;
  for (const seg of String(dotted).split(".")) {
    if (cur == null) return undefined;
    cur = Array.isArray(cur) ? cur[Number(seg)] : cur[seg];
  }
  return cur;
}

/* Admin rows — one per lots field. Mirror of backend LOTS_PATHS. */
export function adminPaperRows(id, cfg) {
  const R = (path, label) => ({ label, paths: [path], live: pathGet(cfg, path) });
  switch (id) {
    case "SCALP_V1": case "SCALP_V3": case "SCALP_V5": case "HA_V1":
    case "VET_V1": case "BRK_V1":
      return [R("quantity.lots", "Lots")];
    case "ORB_V1": case "EMC_V1":
      return [R("lots", "Lots")];
    case "BB_V1":
      return [R("lots", "Total lots")];
    case "BB_V2":
      return [R("ce_lots", "CE lots"), R("pe_lots", "PE lots")];
    case "TSG_V1":
      return [R("lots", "Lots"), R("expiry_lots", "Expiry-day lots")];
    case "TMA_V1":
      return [R("c1.sell.lots", "SELL leg lots"), R("c1.buy.lots", "HEDGE leg lots")];
    case "TMA_V2":
      return [R("s1.main.lots", "SELL leg lots"), R("s1.hedge.lots", "HEDGE leg lots")];
    case "IC_V1": case "IC_V2": {
      const legs = (Array.isArray(cfg?.legs) ? cfg.legs : []).slice(0, 4).map((leg, i) =>
        R(`legs.${i}.lots`, `${leg?.id || `L${i + 1}`} · ${leg?.action === "SELL" ? "S" : "B"}·${leg?.opt_type || ""} lots`));
      if (id === "IC_V2") {
        legs.push(R("adjust.L1.lots", "L1·ADJ B·CE lots"), R("adjust.L2.lots", "L2·ADJ B·PE lots"));
      }
      return legs;
    }
    default:
      return [];
  }
}

const microLabel = {
  fontSize: 10, fontWeight: 500, letterSpacing: "0.5px",
  textTransform: "uppercase", color: colors.text.muted,
};

/**
 * rows   [{ label, paths: [...], live }]
 * paper  current paper_lots object (may be undefined)
 * onSet  (nextPaperLotsObject) => void
 * maxLots  >0 clamps input (non-admin licence cap)
 * bare   true = no card chrome (sits inside an existing section)
 */
export default function PaperLotsSection({ rows, paper, onSet, maxLots = 0, bare = false }) {
  if (!rows || !rows.length) return null;
  const cur = paper && typeof paper === "object" ? paper : {};

  const setRow = (row, raw) => {
    let n = Math.max(0, Math.floor(Number(raw) || 0));
    if (maxLots > 0 && n > maxLots) n = maxLots;
    const next = { ...cur };
    for (const p of row.paths) next[paperKey(p)] = n;
    onSet(next);
  };

  const body = (
    <>
      <div style={{ fontSize: 11, color: colors.text.muted, lineHeight: 1.5, marginBottom: spacing.sm }}>
        The paper twin copies every live entry and exit at the live prices; only its size changes.
        Blank = same as live.{maxLots > 0 ? ` Maximum ${maxLots} lots.` : ""} Slippage is measured on the
        live orders, so it is not affected. Applies from the next entry.
      </div>
      <div style={{
        display: "grid", gridTemplateColumns: "minmax(120px, 1fr) 70px 110px 70px",
        gap: 8, alignItems: "center", fontSize: 12,
      }}>
        <span style={microLabel}>Field</span>
        <span style={microLabel}>Live</span>
        <span style={microLabel}>Paper</span>
        <span style={microLabel} />
        {rows.map((row) => {
          const v = Number(cur[paperKey(row.paths[0])]) || 0;
          const live = Number(row.live) || 0;
          const note = v > 0 && live > 0 && v !== live
            ? `×${(v / live).toFixed(v % live === 0 ? 0 : 2)}`
            : "";
          return (
            <div key={row.paths.join("|")} style={{ display: "contents" }}>
              <span style={{ color: colors.text.primary }}>{row.label}</span>
              <span style={{ color: colors.text.secondary, ...typography.mono }}>{live > 0 ? live : "—"}</span>
              <input
                type="number" min={0} step={1} max={maxLots > 0 ? maxLots : undefined}
                placeholder="same" value={v > 0 ? v : ""}
                onChange={(e) => setRow(row, e.target.value)}
                aria-label={`Paper ${row.label}`}
                style={{
                  width: 100, padding: "6px 9px", borderRadius: 6,
                  background: colors.bg.input, color: colors.text.primary,
                  border: `1px solid ${colors.border.light}`, fontSize: 12, ...typography.mono,
                }}
              />
              <span style={{ fontSize: 11, color: colors.warning }}>{note}</span>
            </div>
          );
        })}
      </div>
    </>
  );

  if (bare) return <div style={{ padding: `${spacing.md}px 0` }}>{body}</div>;

  return (
    <div style={{
      background: colors.bg.tertiary,
      border: `1px solid ${colors.warning}55`,
      borderRadius: 10, padding: spacing.md,
    }}>
      <div style={{ ...microLabel, color: colors.warning, marginBottom: 6 }}>
        Paper twin size · Paper+Live
      </div>
      {body}
    </div>
  );
}
