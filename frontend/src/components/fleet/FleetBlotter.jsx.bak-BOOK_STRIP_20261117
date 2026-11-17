// frontend/src/components/fleet/FleetBlotter.jsx — ── DASH_MODERN_20260925 ──
// One row per (strategy, book) that placed an order today; idle strategies as one
// line of names. Every row / name focuses that strategy's panel below. Names go
// through stratName (UI_MASK). Numbers marked ≈ carry an unmarked leg or modelled
// charges.
// ── DASH_SYSTEM_20261117 ── rebuilt on components/fleet/dash.js: 9 columns (was 12,
// min-width 1180 px, last column clipped at 1470 px). Trades / Peak go to the row
// tooltip, the stop into the state text; the MTM bar is gone (the number is the
// bar); badges are words — only LIVE is boxed; only the MTM is coloured; one
// accent-free spark per row coloured by its sign.

import { stratName } from "../../strategies/displayNames";
import { fmtInr, hhmm, bookLabel } from "./fleetFormat";
import { MARKET_START_MIN, FNO_END_MIN } from "../../marketSession";
import { ReplayLaunch } from "../replay/ReplayHost";   // ── TRADE_REPLAY_STREAM_20261017 ──
import { SPACE, COLOR, TYPE, NUM, CAPS, ROW_H, tone, liveTag } from "./dash";

const clock = (ts) => hhmm(Math.floor(((ts + 19800) % 86400) / 60));
const TH = { ...CAPS, textAlign: "right", padding: `0 ${SPACE.md}px ${SPACE.sm}px`, borderBottom: `1px solid ${COLOR.line}`, whiteSpace: "nowrap", fontWeight: 400 };
const TD = { ...TYPE.body, ...NUM, height: ROW_H, padding: `0 ${SPACE.md}px`, borderBottom: `1px solid ${COLOR.line}`, textAlign: "right",
  whiteSpace: "nowrap", verticalAlign: "middle", color: COLOR.ink, overflow: "hidden", textOverflow: "ellipsis" };
const LEFT = { textAlign: "left", fontFamily: "var(--c-font-ui)" };
const QUIET_REPLAY = { border: 0, color: COLOR.muted, fontSize: TYPE.meta.fontSize, height: 20, minWidth: 16 };

function Spark({ path, w = 80, h = 20 }) {
  const pts = Array.isArray(path) ? path : [];
  if (pts.length < 2) return <span style={{ color: COLOR.muted }}>—</span>;
  let lo = 0, hi = 0;
  pts.forEach(([, v]) => { if (v < lo) lo = v; if (v > hi) hi = v; });
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const x = (m) => ((m - MARKET_START_MIN) / (FNO_END_MIN - MARKET_START_MIN)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * (h - 2) - 1;
  const d = pts.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} style={{ display: "block", marginLeft: "auto" }}>
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} style={{ stroke: COLOR.line }} strokeDasharray="2 4" />
      <path d={d} fill="none" stroke={tone(pts[pts.length - 1][1])} strokeWidth="1.25" />
    </svg>
  );
}

/* state as words: what it is (ink / down for a stop-out) + the detail (muted) */
function stateOf(r) {
  const reason = r.last_event?.reason || "";
  const legs = `${r.open_legs} leg${r.open_legs > 1 ? "s" : ""}`;
  const stop = r.stop != null ? ` · stop ${fmtInr(r.stop)}` : "";
  if (r.state === "OPEN") return { word: "Open", color: COLOR.ink, text: `${legs}${r.carried ? ", carried" : r.first_entry_ts ? ` since ${clock(r.first_entry_ts)}` : ""}${stop}` };
  if (/SL|STOP/i.test(reason)) return { word: "Stopped out", color: COLOR.down, text: r.last_event?.ts ? `at ${clock(r.last_event.ts)}` : "" };
  return { word: "Done", color: COLOR.muted, text: r.last_event?.ts ? `last exit ${clock(r.last_event.ts)}` : "" };
}

function rowTitle(r) {
  const p = [`${r.closed_trades || 0} trade${r.closed_trades === 1 ? "" : "s"} closed`];
  if (r.peak != null) p.push(`peak ${fmtInr(r.peak)}`);
  if (r.stop != null) p.push(`stop ${fmtInr(r.stop)}`);
  return `${p.join(" · ")} — click to focus its panel`;
}

function BookCell({ book }) {
  return book === "LIVE" ? <span style={liveTag}>LIVE</span>
    : <span style={{ ...TYPE.meta, color: COLOR.muted }}>{bookLabel(book)}</span>;
}

function IdleLine({ idle, focusId, onFocus, name }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: `${SPACE.xs}px ${SPACE.md}px`, ...TYPE.meta, color: COLOR.muted }}>
      <span>Idle today</span>
      {idle.map((s) => (
        <button key={s.id} type="button" className="flb-chip" aria-current={focusId === s.id ? "true" : undefined} onClick={() => onFocus && onFocus(s.id)}
          style={{ border: 0, background: "transparent", padding: 0, cursor: "pointer", font: "inherit", ...TYPE.meta,
            color: focusId === s.id ? COLOR.ink : COLOR.muted, fontWeight: focusId === s.id ? 600 : 400 }}>
          {name(s.id)}{s.mode && s.mode !== "PAPER" ? ` (${s.mode === "PAPER_LIVE" ? "paper+live" : s.mode.toLowerCase()})` : ""}
        </button>
      ))}
      {idle.length === 0 && <span>—</span>}
    </div>
  );
}

function Header({ count, hint }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: SPACE.md }}>
      <div style={{ ...TYPE.title, color: COLOR.ink }}>
        Traded today <span style={{ ...TYPE.body, ...NUM, color: COLOR.muted, marginLeft: SPACE.xs }}>{count}</span>
      </div>
      <span style={{ ...TYPE.meta, color: COLOR.muted }}>{hint}</span>
    </div>
  );
}

// ── DASH_MODERN_MOBILE_20261007 ── phone: one card per (strategy, book)
function FleetCards({ rows, idle, focusId, onFocus, isAdminUi, meta }) {
  const list = Array.isArray(rows) ? rows : [];
  const name = (id) => stratName(id, isAdminUi, meta?.[id]?.name);
  const num = (r, v) => `${r.approx ? "≈" : ""}${fmtInr(v)}`;
  return (
    <div style={{ fontFamily: "var(--c-font-ui)" }}>
      <style>{`.flm-card { cursor: pointer; -webkit-tap-highlight-color: transparent; }
        .flm-card[aria-current="true"] { box-shadow: inset 2px 0 0 ${COLOR.ink}; border-color: ${COLOR.ink} !important; }`}</style>
      <Header count={list.length} hint="tap to focus" />
      {list.length === 0 && <div style={{ padding: `${SPACE.lg}px 0`, textAlign: "center", ...TYPE.body, color: COLOR.muted }}>No strategy has placed an order yet today.</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: SPACE.sm }}>
        {list.map((r) => {
          const st = stateOf(r);
          const facts = [
            r.closed_trades > 0 && `R ${num(r, r.realised)}`, r.open_legs > 0 && `U ${num(r, r.unrealised)}`,
            r.closed_trades > 0 && `chg ₹${Math.round(r.charges || 0).toLocaleString("en-IN")}`,
          ].filter(Boolean).join(" · ");
          return (
            <button key={r.key} type="button" className="flm-card" aria-current={focusId === r.id ? "true" : undefined} onClick={() => onFocus && onFocus(r.id)}
              style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: `${SPACE.xs}px ${SPACE.md}px`, width: "100%", textAlign: "left",
                padding: SPACE.md, background: "transparent", border: `1px solid ${COLOR.line}`, borderRadius: 8, font: "inherit", color: COLOR.ink }}>
              <span style={{ display: "flex", alignItems: "center", gap: SPACE.sm, minWidth: 0 }}>
                <span style={{ ...TYPE.body, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{name(r.id)}</span>
                <BookCell book={r.book} />
              </span>
              <span style={{ ...TYPE.title, ...NUM, color: tone(r.gross), whiteSpace: "nowrap", textAlign: "right" }}>{num(r, r.gross)}</span>
              <span style={{ ...TYPE.meta, color: COLOR.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                <span style={{ color: st.color, fontWeight: 600 }}>{st.word}</span>{st.text ? ` · ${st.text}` : ""}
              </span>
              <span style={{ justifySelf: "end", alignSelf: "center" }}><Spark path={r.path} w={72} h={18} /></span>
              {facts && <span style={{ gridColumn: "1 / -1", ...TYPE.meta, ...NUM, color: COLOR.muted }}>{facts}</span>}
              {r.last_event?.text && <span style={{ gridColumn: "1 / -1", ...TYPE.meta, color: COLOR.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.last_event.text}</span>}
            </button>
          );
        })}
      </div>
      <div style={{ marginTop: SPACE.md }}><IdleLine idle={Array.isArray(idle) ? idle : []} focusId={focusId} onFocus={onFocus} name={name} /></div>
    </div>
  );
}

export default function FleetBlotter({ rows, idle, focusId, onFocus, isAdminUi, meta, compact = false }) {
  if (compact) return <FleetCards rows={rows} idle={idle} focusId={focusId} onFocus={onFocus} isAdminUi={isAdminUi} meta={meta} />;
  const list = Array.isArray(rows) ? rows : [];
  const name = (id) => stratName(id, isAdminUi, meta?.[id]?.name);
  const num = (r, v) => `${r.approx ? "≈" : ""}${fmtInr(v)}`;
  const dash = <span style={{ color: COLOR.muted }}>—</span>;
  return (
    <div style={{ fontFamily: "var(--c-font-ui)" }}>
      <style>{`
        .flb-row { cursor: pointer; }
        .flb-row:hover td { box-shadow: inset 0 -1px 0 ${COLOR.ink}; }
        .flb-row[aria-current="true"] td:first-child { box-shadow: inset 2px 0 0 ${COLOR.ink}; }
        .flb-chip:hover { color: ${COLOR.ink} !important; }
      `}</style>
      <Header count={list.length} hint="click a row to focus its panel" />
      <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
        <colgroup>
          <col style={{ width: "19%" }} /><col style={{ width: 64 }} /><col style={{ width: "17%" }} />
          <col style={{ width: 112 }} /><col style={{ width: 104 }} /><col style={{ width: 104 }} /><col style={{ width: 80 }} />
          <col style={{ width: 104 }} /><col />
        </colgroup>
        <thead>
          <tr>
            <th style={{ ...TH, ...LEFT }}>Strategy</th>
            <th style={{ ...TH, ...LEFT }}>Book</th>
            <th style={{ ...TH, ...LEFT }}>State</th>
            <th style={TH}>MTM today</th>
            <th style={TH}>Realised</th>
            <th style={TH}>Unrealised</th>
            <th style={TH}>Charges</th>
            <th style={TH}>Path</th>
            <th style={{ ...TH, ...LEFT }}>Last event</th>
          </tr>
        </thead>
        <tbody>
          {list.length === 0 && (
            <tr><td colSpan={9} style={{ ...TD, ...LEFT, textAlign: "center", color: COLOR.muted }}>
              No strategy has placed an order yet today. Rows appear here at the first fill.
            </td></tr>
          )}
          {list.map((r) => {
            const st = stateOf(r);
            return (
              <tr key={r.key} className="flb-row" aria-current={focusId === r.id ? "true" : undefined} title={rowTitle(r)} onClick={() => onFocus && onFocus(r.id)}>
                <td style={{ ...TD, ...LEFT }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: SPACE.sm, maxWidth: "100%" }}>
                    <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>{name(r.id)}</span>
                    {isAdminUi && <span style={{ ...TYPE.meta, color: COLOR.muted }}>{r.id}</span>}
                    <ReplayLaunch q={{ strategy: r.id, book: r.book }} title={`Replay ${name(r.id)} — open position or latest trade`} style={QUIET_REPLAY} />
                  </span>
                </td>
                <td style={{ ...TD, ...LEFT }}><BookCell book={r.book} /></td>
                <td style={{ ...TD, ...LEFT }}>
                  <span style={{ color: st.color, fontWeight: 600 }}>{st.word}</span>
                  <span style={{ color: COLOR.muted }}>{st.text ? ` · ${st.text}` : ""}</span>
                </td>
                <td style={{ ...TD, fontWeight: 600, color: tone(r.gross) }}>{num(r, r.gross)}</td>
                <td style={TD}>{r.closed_trades ? num(r, r.realised) : dash}</td>
                <td style={TD}>{r.open_legs ? num(r, r.unrealised) : dash}</td>
                <td style={{ ...TD, color: COLOR.muted }}>{r.closed_trades ? `${r.approx ? "≈" : ""}₹${Math.round(r.charges || 0).toLocaleString("en-IN")}` : "—"}</td>
                <td style={TD}><Spark path={r.path} /></td>
                <td style={{ ...TD, ...LEFT, color: COLOR.muted }} title={r.last_event?.text || undefined}>{r.last_event?.text || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: SPACE.md }}><IdleLine idle={Array.isArray(idle) ? idle : []} focusId={focusId} onFocus={onFocus} name={name} /></div>
    </div>
  );
}
