// frontend/src/components/fleet/FleetBlotter.jsx — ── DASH_MODERN_20260925 ──
// One row per (strategy, book) that placed an order today, Live and Paper as
// separate rows; the strategies that did nothing collapse into one idle line
// of chips. Every row and chip focuses that strategy's panel below (the rail
// is gone under the Modern layout, so this IS the picker). Names go through
// stratName (UI_MASK: codenames for non-admin). Numbers marked ≈ carry an
// unmarked leg or modelled charges — never an invented figure shown as exact.

import { colors, spacing, typography, pnlStyle } from "../../tokens";
import { stratName } from "../../strategies/displayNames";
import { fmtInr, hhmm, bookLabel } from "./fleetFormat";
import { MARKET_START_MIN, FNO_END_MIN } from "../../marketSession";

const TH = { ...typography.label, color: colors.text.muted, textAlign: "right", padding: "8px 12px", borderBottom: `1px solid ${colors.border.light}`, whiteSpace: "nowrap", fontWeight: 600 };
const TD = { padding: "9px 12px", borderBottom: `1px solid ${colors.border.dark}`, textAlign: "right", whiteSpace: "nowrap", verticalAlign: "middle", ...typography.mono, fontSize: 12.5 };
const LEFT = { textAlign: "left" };

function Badge({ children, tone }) {
  const c = tone === "live" ? colors.warning : tone === "open" ? colors.profit : tone === "sl" ? colors.loss : colors.text.muted;
  return (
    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.4px", padding: "1px 6px", borderRadius: 4, border: `1px solid ${c}`, color: c, opacity: tone ? 1 : 0.8, whiteSpace: "nowrap", fontFamily: "var(--c-font-ui)" }}>
      {children}
    </span>
  );
}

function Spark({ path, color, w = 110, h = 22 }) {
  const pts = Array.isArray(path) ? path : [];
  if (pts.length < 2) return <span style={{ color: colors.text.muted, fontSize: 11 }}>—</span>;
  let lo = 0, hi = 0;
  pts.forEach(([, v]) => { if (v < lo) lo = v; if (v > hi) hi = v; });
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const x = (m) => ((m - MARKET_START_MIN) / (FNO_END_MIN - MARKET_START_MIN)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * (h - 2) - 1;
  const d = pts.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} style={{ display: "block", marginLeft: "auto" }}>
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} style={{ stroke: colors.border.light }} strokeDasharray="2 3" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.4" />
    </svg>
  );
}

function stateOf(r) {
  const reason = r.last_event?.reason || "";
  if (r.state === "OPEN") return { badge: "Open", tone: "open", text: r.carried ? `${r.open_legs} leg${r.open_legs > 1 ? "s" : ""}, carried` : `${r.open_legs} leg${r.open_legs > 1 ? "s" : ""}${r.first_entry_ts ? ` since ${hhmm(Math.floor(((r.first_entry_ts + 19800) % 86400) / 60))}` : ""}` };
  if (/SL|STOP/i.test(reason)) return { badge: "Stopped out", tone: "sl", text: r.last_event ? `at ${hhmm(Math.floor(((r.last_event.ts + 19800) % 86400) / 60))}` : "" };
  return { badge: "Done", tone: null, text: r.last_event ? `last exit ${hhmm(Math.floor(((r.last_event.ts + 19800) % 86400) / 60))}` : "" };
}

export default function FleetBlotter({ rows, idle, focusId, onFocus, isAdminUi, meta }) {
  const list = Array.isArray(rows) ? rows : [];
  const idleList = Array.isArray(idle) ? idle : [];
  const maxAbs = Math.max(1, ...list.map((r) => Math.abs(r.gross || 0)));
  const name = (id) => stratName(id, isAdminUi, meta?.[id]?.name);
  const accent = (id) => meta?.[id]?.accent || colors.border.light;
  const num = (r, v) => `${r.approx ? "≈" : ""}${fmtInr(v)}`;
  return (
    <div style={{ fontFamily: "var(--c-font-ui)" }}>
      <style>{`
        .flb-row { cursor: pointer; transition: background .12s ease; }
        .flb-row:hover { background: var(--c-bg-tertiary); }
        .flb-row[aria-current="true"] { background: var(--c-bg-tertiary); box-shadow: inset 3px 0 0 var(--c-primary); }
        .flb-chip { cursor: pointer; }
        .flb-chip:hover, .flb-chip[aria-current="true"] { border-color: var(--c-primary) !important; color: var(--c-text-primary) !important; }
      `}</style>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", padding: `0 0 ${spacing.xs}px` }}>
        <div style={{ ...typography.headingSmall, color: colors.text.primary }}>
          Traded today <span style={{ ...typography.mono, fontWeight: 400, color: colors.text.muted, marginLeft: 6 }}>{list.length}</span>
        </div>
        <span style={{ fontSize: 11, color: colors.text.muted }}>click a row to focus its panel</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 1180 }}>
          <thead>
            <tr>
              <th style={{ ...TH, ...LEFT }}>Strategy</th>
              <th style={{ ...TH, ...LEFT }}>Book</th>
              <th style={{ ...TH, ...LEFT }}>State</th>
              <th style={TH}>Trades</th>
              <th style={TH}>MTM today</th>
              <th style={TH}>Realised</th>
              <th style={TH}>Unrealised</th>
              <th style={TH}>Charges</th>
              <th style={TH}>Peak</th>
              <th style={TH}>Stop</th>
              <th style={TH}>Path</th>
              <th style={{ ...TH, ...LEFT }}>Last event</th>
            </tr>
          </thead>
          <tbody>
            {list.length === 0 && (
              <tr><td colSpan={12} style={{ ...TD, textAlign: "center", color: colors.text.muted, fontFamily: "var(--c-font-ui)", padding: "22px 12px" }}>
                No strategy has placed an order yet today. Rows appear here at the first fill.
              </td></tr>
            )}
            {list.map((r) => {
              const st = stateOf(r);
              const w = (Math.abs(r.gross || 0) / maxAbs) * 50;
              return (
                <tr key={r.key} className="flb-row" aria-current={focusId === r.id ? "true" : undefined} onClick={() => onFocus && onFocus(r.id)}>
                  <td style={{ ...TD, ...LEFT, fontFamily: "var(--c-font-ui)" }}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <i style={{ width: 3, height: 16, borderRadius: 2, background: accent(r.id), display: "inline-block" }} />
                      <b style={{ fontWeight: 700, color: colors.text.primary }}>{name(r.id)}</b>
                      {isAdminUi && <span style={{ fontSize: 10, color: colors.text.muted, letterSpacing: "0.4px" }}>{r.id}{r.mode && r.mode !== "PAPER" && r.mode !== "LIVE" ? ` · ${r.mode}` : ""}</span>}
                    </span>
                  </td>
                  <td style={{ ...TD, ...LEFT }}><Badge tone={r.book === "LIVE" ? "live" : null}>{bookLabel(r.book).toUpperCase()}</Badge></td>
                  <td style={{ ...TD, ...LEFT, fontFamily: "var(--c-font-ui)" }}>
                    <Badge tone={st.tone}>{st.badge}</Badge>
                    <span style={{ color: colors.text.muted, fontSize: 12, marginLeft: 8 }}>{st.text}</span>
                  </td>
                  <td style={TD}>{r.closed_trades || "—"}</td>
                  <td style={TD}>
                    <span style={{ display: "inline-block", width: 96, height: 6, background: colors.border.dark, borderRadius: 3, position: "relative", verticalAlign: "middle", marginRight: 10 }}>
                      <i style={{ position: "absolute", top: 0, height: 6, borderRadius: 3, width: `${w.toFixed(1)}%`, ...(r.gross >= 0 ? { left: "50%", background: colors.profit } : { right: "50%", background: colors.loss }) }} />
                      <i style={{ position: "absolute", left: "50%", top: -2, width: 1, height: 10, background: colors.border.light }} />
                    </span>
                    <b style={{ fontWeight: 600, ...pnlStyle(r.gross) }}>{num(r, r.gross)}</b>
                  </td>
                  <td style={{ ...TD, ...pnlStyle(r.realised) }}>{r.closed_trades ? num(r, r.realised) : <span style={{ color: colors.text.muted }}>—</span>}</td>
                  <td style={{ ...TD, ...pnlStyle(r.unrealised) }}>{r.open_legs ? num(r, r.unrealised) : <span style={{ color: colors.text.muted }}>—</span>}</td>
                  <td style={{ ...TD, color: colors.text.muted }}>{r.closed_trades ? `${r.approx ? "≈" : ""}₹${Math.round(r.charges || 0).toLocaleString("en-IN")}` : "—"}</td>
                  <td style={TD}>{r.peak == null ? "—" : fmtInr(r.peak)}</td>
                  <td style={{ ...TD, color: r.stop == null ? colors.text.muted : colors.loss }}>{r.stop == null ? "—" : fmtInr(r.stop)}</td>
                  <td style={TD}><Spark path={r.path} color={accent(r.id)} /></td>
                  <td style={{ ...TD, ...LEFT, fontFamily: "var(--c-font-ui)", color: colors.text.secondary, fontSize: 12 }}>{r.last_event?.text || "—"}</td>
                </tr>
              );
            })}
            <tr>
              <td colSpan={12} style={{ ...TD, ...LEFT, fontFamily: "var(--c-font-ui)", color: colors.text.muted, fontSize: 12, borderBottom: 0, padding: "10px 12px 6px", whiteSpace: "normal" }}>
                <span style={{ marginRight: 10 }}>Idle today, no orders</span>
                {idleList.map((s) => (
                  <button key={s.id} type="button" className="flb-chip" aria-current={focusId === s.id ? "true" : undefined} onClick={() => onFocus && onFocus(s.id)}
                    style={{ font: "inherit", fontSize: 11.5, padding: "3px 8px", margin: "2px 6px 2px 0", borderRadius: 5, background: colors.bg.primary,
                      border: `1px solid ${colors.border.dark}`, color: colors.text.secondary, opacity: s.mode === "OFF" ? 0.55 : 1, display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <i style={{ width: 3, height: 12, borderRadius: 2, background: accent(s.id), display: "inline-block" }} />
                    {name(s.id)}
                    <em style={{ fontStyle: "normal", color: colors.text.muted }}>{s.mode === "OFF" ? "off" : s.mode === "PAPER_LIVE" ? "paper+live" : (s.mode || "").toLowerCase()}</em>
                  </button>
                ))}
                {idleList.length === 0 && <span style={{ color: colors.text.muted }}>—</span>}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
