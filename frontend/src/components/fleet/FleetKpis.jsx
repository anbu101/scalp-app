// frontend/src/components/fleet/FleetKpis.jsx — ── DASH_MODERN_20260925 ──
// ── DASH_SYSTEM_20261117 ── rebuilt on components/fleet/dash.js. Was a 9-column
// ledger with every figure coloured; now one block per book that traded today:
// the MTM is the hero (the only coloured figure), realised / unrealised read as a
// sentence under it, and Net · From peak · Charges · Week · Month sit in one quiet
// facts row. "Traded today" closes the card. Same feed, same rules as before:
// Live and Paper are never summed; a book with no orders today is not shown.

import { MARKET_START_MIN, FNO_END_MIN } from "../../marketSession";
import { fmtInr, hhmm } from "./fleetFormat";
import { SPACE, COLOR, TYPE, NUM, CAPS, CARD, CARD_COMPACT, tone, liveTag } from "./dash";

const BOOKS = [["LIVE", "Live"], ["PAPER", "Paper"]];   // ── LIVE_ROW_HIDE_20260925 ──
const asOf = (t) => hhmm(Math.floor(((t.mark_asof + 19800) % 86400) / 60));

function Fact({ label, title, children }) {
  return (
    <div style={{ minWidth: 0 }} title={title}>
      <div style={{ ...TYPE.meta, color: COLOR.muted, whiteSpace: "nowrap" }}>{label}</div>
      <div style={{ ...TYPE.body, ...NUM, color: COLOR.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{children}</div>
    </div>
  );
}

// ── BOOK_STRIP_20261117 ── one book on a desktop (Paper only, or Live only): the hero, the
// facts and Traded today read left to right as ONE strip at their natural widths, each part
// after a hairline, instead of the hero alone over a 5-column facts row stretched across the
// card (a half-empty card, ~300 px tall). Two books keep the side-by-side blocks.
function Book({ book, label, t, compact, strip = false }) {
  const peakTitle = t.peak != null ? `peak ${fmtInr(t.peak)}${t.peak_min != null ? ` at ${hhmm(t.peak_min)}` : ""}` : undefined;
  return (
    <div data-book={book} style={strip ? { minWidth: 0, display: "contents" } : { minWidth: 0 }}>
      <div style={{ minWidth: 0 }}>   {/* ── BOOK_STRIP_20261117 ── hero block */}
      <div style={{ display: "flex", alignItems: "center", gap: SPACE.sm, height: 20 }}>   {/* fixed: LIVE's box must not shift the row */}
        {book === "LIVE" ? <span style={liveTag}>LIVE</span> : <span style={CAPS}>{label}</span>}
        <span style={{ ...TYPE.meta, color: COLOR.muted }}>MTM today</span>
      </div>
      <div style={{ ...TYPE.hero, color: tone(t.gross), marginTop: SPACE.xs, whiteSpace: "nowrap" }}>
        {t.approx ? "≈" : ""}{fmtInr(t.gross)}
      </div>
      <div style={{ ...TYPE.body, color: COLOR.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        realised <span style={{ ...NUM, color: COLOR.ink }}>{fmtInr(t.realised)}</span>
        {" · "}unrealised <span style={{ ...NUM, color: COLOR.ink }}>{fmtInr(t.unrealised)}</span>
        {t.marks_stale > 0 && t.mark_asof ? ` · marks as of ${asOf(t)}` : ""}
      </div>
      </div>
      <div data-dash-part="facts" style={strip
        ? { display: "flex", flexWrap: "wrap", alignItems: "center", alignContent: "center", justifyContent: "space-between",
            gap: `${SPACE.md}px ${SPACE.xxl}px`, maxWidth: 720, borderLeft: `1px solid ${COLOR.line}`, paddingLeft: SPACE.xl,
            alignSelf: "stretch", minWidth: 0 }
        : { display: "grid", gridTemplateColumns: compact ? "repeat(3, minmax(0, 1fr))" : "repeat(5, minmax(0, 1fr))",
            gap: `${SPACE.md}px ${SPACE.lg}px`, marginTop: SPACE.lg }}>
        <Fact label="Net">{fmtInr(t.net)}</Fact>
        <Fact label="From peak" title={peakTitle}>{t.peak == null ? "—" : fmtInr((t.gross ?? 0) - t.peak)}</Fact>
        <Fact label="Charges">₹{Math.round(t.charges || 0).toLocaleString("en-IN")}</Fact>
        <Fact label="Week" title={`${t.week_trades ?? 0} trades exited since ${t.week_from || "—"}, net of charges`}>
          {t.week_approx ? "≈" : ""}{fmtInr(t.week_net ?? 0)}
        </Fact>
        <Fact label="Month" title={`${t.month_trades ?? 0} trades exited since ${t.month_from || "—"}, net of charges`}>
          {t.month_approx ? "≈" : ""}{fmtInr(t.month_net ?? 0)}
        </Fact>
      </div>
    </div>
  );
}

export default function FleetKpis({ totals, active, fleet, nowMin, compact = false }) {   // ── DASH_MODERN_MOBILE_20261007 ── compact
  const left = Math.max(0, FNO_END_MIN - nowMin);
  const session = nowMin < MARKET_START_MIN ? `opens ${hhmm(MARKET_START_MIN)}`
    : left > 0 ? `closes in ${Math.floor(left / 60)}h ${String(left % 60).padStart(2, "0")}m` : "market closed";
  const books = totals ? BOOKS.filter(([k]) => totals[k] && totals[k].rows > 0) : [];
  const note = !totals ? "loading books…"
    : !books.length ? "No orders yet today — Live and Paper appear here at the first fill." : null;
  const traded = (
    <div style={{ minWidth: 0 }}>
      <div style={CAPS}>Traded today</div>
      <div style={{ ...TYPE.figure, color: COLOR.ink, marginTop: SPACE.xs, whiteSpace: "nowrap" }}>
        {active ?? "—"}<span style={{ ...TYPE.body, ...NUM, color: COLOR.muted }}> of {fleet ?? "—"}</span>
      </div>
      <div style={{ ...TYPE.meta, color: COLOR.muted, whiteSpace: "nowrap" }}>{session}</div>
    </div>
  );
  const strip = !compact && books.length === 1;   // ── BOOK_STRIP_20261117 ── hero | facts | Traded today
  return (
    <div data-testid="dash-books" data-dash-part="books" data-layout={strip ? "strip" : undefined}
      style={{ ...(compact ? CARD_COMPACT : CARD), fontFamily: "var(--c-font-ui)",
      display: "grid", gap: compact ? SPACE.xl : SPACE.xxl, alignItems: strip ? "center" : undefined,
      gridTemplateColumns: compact ? "minmax(0, 1fr)"
        : strip ? "max-content minmax(0, 1fr) max-content"   // Traded today keeps the right edge, as with two books
        : `repeat(${Math.max(1, books.length)}, minmax(0, 1fr)) auto` }}>
      {note && <div style={{ ...TYPE.body, color: COLOR.muted, alignSelf: "center" }}>{note}</div>}
      {books.map(([k, label]) => <Book key={k} book={k} label={label} t={totals[k]} compact={compact} strip={strip} />)}
      <div style={compact ? { borderTop: `1px solid ${COLOR.line}`, paddingTop: SPACE.lg }
        : { borderLeft: `1px solid ${COLOR.line}`, paddingLeft: SPACE.xl, alignSelf: "stretch", display: "flex", alignItems: "center" }}>
        {traded}
      </div>
    </div>
  );
}
