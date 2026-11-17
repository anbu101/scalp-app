// frontend/src/components/calm/StrategyCard.jsx — ── DASH_CALM_20261117 ──
// One card per strategy that traded today (Live and Paper rows of the same strategy
// share a card: the shown book's figure is big, the other book's sits under it as
// its "twin"). Tap the card → its panel and live chart below. Tap the meter →
// the card widens and shows why (dial + conditions, the Live chart's own panel).
// IdleCard: an armed strategy with no trade yet — dashed, with its entry readiness.
import { C, T, SP, RADIUS, tone } from "./calm";
import { fmtInr, bookLabel } from "../fleet/fleetFormat";
import { WaitingPanel } from "../replay/TradeReplay";

const clock = (ts) => {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

export function Spark({ path, w = 280, h = 48 }) {
  const pts = (path || []).map((p) => p[1]);
  if (pts.length < 2) return <div style={{ height: h }} aria-hidden="true" />;
  const lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), pad = (hi - lo || 1) * 0.1;
  const y = (v) => 3 + (h - 6) * ((hi + pad) - v) / ((hi + pad) - (lo - pad));
  const d = pts.map((v, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * w).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true" style={{ display: "block" }}>
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} stroke={C.line} strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
      <path d={d} fill="none" stroke={tone(last)} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const BADGE = {
  trade: { bg: C.inset, fg: C.sub, bd: "transparent" },
  warn:  { bg: "var(--c-warning-bg)", fg: C.warn, bd: "transparent" },
  bad:   { bg: "var(--c-loss-bg)", fg: C.down, bd: "transparent" },
  done:  { bg: "transparent", fg: C.muted, bd: C.line },
};
export function Badge({ kind = "trade", children }) {
  const b = BADGE[kind] || BADGE.trade;
  return <span data-badge={kind} style={{ ...T.meta, padding: `2px ${SP.md - 2}px`, borderRadius: RADIUS.pill, background: b.bg, color: b.fg, border: `1px solid ${b.bd}`, whiteSpace: "nowrap" }}>{children}</span>;
}

/* the meter as a bar: exit pressure while in a trade, entry readiness while waiting */
export function meterView(m) {
  if (!m || m.score == null) return null;
  const exit = m.side !== "entry";
  const pct = Math.max(0, Math.min(100, Math.round(m.score)));
  const hot = exit && pct >= 60;
  return { label: exit ? "Exit pressure" : "Entry readiness", pct, word: m.word || "", hot, color: hot ? C.warn : C.accent };
}

export function MeterBar({ v, expanded, onToggle, id }) {
  if (!v) return null;
  return (
    <button type="button" aria-expanded={expanded ? "true" : "false"} aria-controls={`why-${id}`} onClick={onToggle} data-testid="calm-meter"
      style={{ display: "block", width: "100%", textAlign: "left", background: "transparent", border: 0, padding: `${SP.xs}px 0`, color: "inherit", font: "inherit", cursor: "pointer", minHeight: 36 }}>
      <span style={{ display: "flex", justifyContent: "space-between", alignItems: "center", ...T.meta, color: C.muted, marginBottom: 6 }}>
        <span>{v.label}</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: SP.xs, color: v.hot ? C.warn : C.muted }}>
          {v.pct}%{v.word ? ` · ${v.word}` : ""}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={expanded ? "m6 15 6-6 6 6" : "m6 9 6 6 6-6"} />
          </svg>
        </span>
      </span>
      <span style={{ display: "block", height: 6, borderRadius: 3, background: C.line, overflow: "hidden" }}>
        <span data-pct={v.pct} style={{ display: "block", width: `${v.pct}%`, height: "100%", background: v.color }} />
      </span>
    </button>
  );
}

export function stateBadge(r, mv) {
  if (r.state === "OPEN") {
    if (mv && mv.hot) return <Badge kind="warn">Exit near</Badge>;
    return <Badge kind="trade">In trade · {r.open_legs} leg{r.open_legs === 1 ? "" : "s"}</Badge>;
  }
  if (/SL|STOP/i.test(r.last_event?.reason || "")) return <Badge kind="bad">Stopped out</Badge>;
  return <Badge kind="done">Done</Badge>;
}

function Facts({ r, compact }) {
  const f = [];
  f.push(["Closed", r.closed_trades > 0 ? fmtInr(r.realised).replace("₹", "") : "—"]);
  f.push(["Open", r.open_legs > 0 ? fmtInr(r.unrealised).replace("₹", "") : "—"]);
  if (r.open_legs > 0) f.push(["Legs", String(r.open_legs)]);
  else f.push(["Charges", r.charges ? `₹${Math.round(r.charges).toLocaleString("en-IN")}` : "—"]);
  if (!compact && r.stop != null && r.open_legs > 0) f.push(["Stop", fmtInr(r.stop)]);
  return (
    <div style={{ display: "flex", gap: SP.lg, flexWrap: "wrap", ...T.body, fontSize: 13, color: C.muted }}>
      {f.map(([k, v]) => <span key={k}>{k} <b style={{ color: C.ink, fontWeight: 600, ...T.num }}>{v}</b></span>)}
    </div>
  );
}

export function StrategyCard({ id, name, accent, row, twin, books, meter, expanded, onToggle, selected, onFocus, compact = false }) {
  const mv = row.state === "OPEN" ? meterView(meter?.waiting?.meter) : null;
  const ev = row.last_event || {};
  const evText = [ev.ts ? clock(ev.ts) : null, ev.text].filter(Boolean).join(" · ");
  const wide = expanded && !compact;
  return (
    <article data-testid="calm-card" data-strategy={id} data-state={row.state} aria-current={selected ? "true" : undefined}
      style={{ gridColumn: wide ? "span 2" : undefined, background: C.surface, borderRadius: compact ? 14 : RADIUS.card,
        padding: compact ? `${SP.md}px ${SP.lg}px` : SP.xl - 4, display: "flex", flexDirection: "column", gap: compact ? SP.sm : SP.md, minWidth: 0,
        boxShadow: selected ? `inset 0 0 0 2px ${C.accent}` : "none" }}>
      <button type="button" className="calm-card-main" onClick={() => onFocus && onFocus(id)} title={`Show ${name}'s panel and live chart`}
        style={{ display: "flex", flexDirection: "column", gap: compact ? SP.xs : SP.md, textAlign: "left", background: "transparent", border: 0, padding: 0, margin: 0, font: "inherit", color: "inherit", cursor: "pointer", minWidth: 0 }}>
        <span style={{ display: "flex", alignItems: "center", gap: SP.sm, width: "100%", minWidth: 0 }}>
          <span data-accent="" aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 5, background: accent || C.line, flexShrink: 0 }} />
          <span style={{ ...T.title, whiteSpace: "nowrap", flexShrink: 0, maxWidth: "70%", overflow: "hidden", textOverflow: "ellipsis" }}>{name}</span>
          <span data-testid="calm-card-books" style={{ ...T.meta, color: C.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{books}{row.carried && row.state === "OPEN" ? " · carried" : ""}</span>
          {compact && <span data-testid="calm-card-mtm" style={{ marginLeft: "auto", fontSize: 18, fontWeight: 700, color: tone(row.gross), ...T.num }}>{fmtInr(row.gross)}</span>}
        </span>
        {/* ── CARD_HEAD_20261117 ── the state badge sits beside the figure, so the header's room goes to the name and the book ("Paper · carried" is no longer cut) */}
        {!compact && (
          <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: `${SP.xs}px ${SP.sm}px`, width: "100%" }}>
            <span data-testid="calm-card-mtm" style={{ ...T.card, color: tone(row.gross) }}>{row.approx ? "≈" : ""}{fmtInr(row.gross)}</span>
            {stateBadge(row, mv)}
          </span>
        )}
        {twin && <span style={{ ...T.meta, color: C.muted, marginTop: compact ? 0 : -SP.sm }}>{bookLabel(twin.book)} twin <b style={{ color: C.sub, ...T.num }}>{fmtInr(twin.gross)}</b></span>}
        {compact && <span style={{ display: "flex", gap: SP.sm, alignItems: "center", ...T.meta, color: C.muted }}>{stateBadge(row, mv)}</span>}
        {!compact && <Spark path={row.path} />}
        {!compact && <Facts r={row} compact={compact} />}
      </button>
      <MeterBar v={mv} expanded={expanded} onToggle={onToggle} id={id} />
      {expanded && meter?.waiting && (
        <div id={`why-${id}`} data-testid="calm-why" style={{ background: C.inset, borderRadius: RADIUS.inner, padding: compact ? SP.md : SP.sm, overflow: "hidden" }}>
          <WaitingPanel w={meter.waiting} compact={compact} />
        </div>
      )}
      {evText && <div style={{ ...T.meta, color: C.muted, borderTop: compact ? 0 : `1px solid ${C.line}`, paddingTop: compact ? 0 : SP.md, marginTop: compact ? 0 : "auto",
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={ev.text || ""}>{evText}</div>}
    </article>
  );
}

export function IdleCard({ id, name, accent, mode, meter, expanded, onToggle, selected, onFocus, compact = false }) {
  const mv = meterView(meter?.waiting?.meter);
  const why = meter?.waiting?.meter?.reason || meter?.waiting?.headline || "";
  const modeTxt = mode === "PAPER_LIVE" ? "Live + Paper" : mode === "LIVE" ? "Live" : mode === "PAPER" ? "Paper" : (mode || "").toLowerCase();
  return (
    <article data-testid="calm-idle" data-strategy={id} aria-current={selected ? "true" : undefined}
      style={{ gridColumn: expanded && !compact ? "span 2" : undefined, background: "transparent", border: `1px dashed ${C.line}`, borderRadius: compact ? 14 : RADIUS.card,
        padding: compact ? `${SP.md}px ${SP.lg}px` : SP.xl - 4, display: "flex", flexDirection: "column", gap: SP.md, minWidth: 0,
        boxShadow: selected ? `inset 0 0 0 2px ${C.accent}` : "none" }}>
      <button type="button" onClick={() => onFocus && onFocus(id)} title={`Show ${name}'s panel`}
        style={{ display: "flex", flexDirection: "column", gap: SP.sm, textAlign: "left", background: "transparent", border: 0, padding: 0, font: "inherit", color: "inherit", cursor: "pointer", minWidth: 0 }}>
        <span style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: SP.sm, width: "100%" }}>   {/* CARD_HEAD_20261117: wraps instead of overflowing */}
          <span data-accent="" aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 5, background: accent || C.line, flexShrink: 0 }} />
          <span style={{ ...T.title, color: C.sub }}>{name}</span>
          <span style={{ ...T.meta, color: C.muted }}>{modeTxt}</span>
          <span style={{ marginLeft: "auto" }}><Badge kind="done">Waiting</Badge></span>
        </span>
        <span style={{ ...T.body, color: C.muted }}>No trade yet today{why ? ` · ${why}` : ""}</span>
      </button>
      <MeterBar v={mv} expanded={expanded} onToggle={onToggle} id={id} />
      {expanded && meter?.waiting && (
        <div id={`why-${id}`} data-testid="calm-why" style={{ background: C.inset, borderRadius: RADIUS.inner, padding: compact ? SP.md : SP.sm }}>
          <WaitingPanel w={meter.waiting} compact={compact} />
        </div>
      )}
    </article>
  );
}
