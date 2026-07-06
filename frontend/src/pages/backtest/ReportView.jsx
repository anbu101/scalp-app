// frontend/src/pages/backtest/ReportView.jsx
//
// ── REPORT_VIEW ── rendered (non-raw) presentation of a report engine .md,
// plus a standalone-HTML export for sharing.
//
// WHY A HAND-ROLLED PARSER, NOT A MARKDOWN LIB: report_engine.py emits a
// CLOSED grammar — # / ## / ### headings, pipe tables, "- " bullets, **bold**,
// _italic_, `code`, paragraphs. Nothing else, ever. Parsing exactly that in
// ~80 lines buys domain-aware styling a generic renderer can't do (negative ₹
// red, ⚠ FLIP badges, numeric right-alignment) with ZERO new dependencies.
// If the engine's grammar ever grows, grow this parser with it — they're a
// pair.
//
// The .md on disk remains the source of truth (LLM input, diffable, durable);
// this is presentation only.

import React, { useMemo, useRef } from "react";

/* ── parser: markdown (closed grammar) → block AST ── */
export function parseReportMd(md) {
  const lines = String(md || "").split("\n");
  const blocks = [];
  let i = 0;
  const isBullet = (l) => /^\s*[-*]\s+/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("### ")) { blocks.push({ t: "h3", text: line.slice(4) }); i++; continue; }
    if (line.startsWith("## "))  { blocks.push({ t: "h2", text: line.slice(3) }); i++; continue; }
    if (line.startsWith("# "))   { blocks.push({ t: "h1", text: line.slice(2) }); i++; continue; }
    if (line.trimStart().startsWith("|")) {
      const raw = [];
      while (i < lines.length && lines[i].trimStart().startsWith("|")) { raw.push(lines[i]); i++; }
      const rows = raw.map((r) => r.trim().replace(/^\|/, "").replace(/\|$/, "")
        .split("|").map((c) => c.trim()));
      const header = rows[0] || [];
      const body = rows.slice(1).filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c)));
      blocks.push({ t: "table", header, body });
      continue;
    }
    if (isBullet(line)) {
      const items = [];
      while (i < lines.length && isBullet(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++;
      }
      blocks.push({ t: "ul", items });
      continue;
    }
    const para = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !lines[i].startsWith("#")
           && !lines[i].trimStart().startsWith("|") && !isBullet(lines[i])) {
      para.push(lines[i]); i++;
    }
    blocks.push({ t: "p", text: para.join(" ") });
  }
  return blocks;
}

/* ── inline formatting: `code`, **bold**, _italic_ (word-underscores like
   sl_points deliberately NOT italicised) ── */
const INLINE_RX = /(`[^`]+`|\*\*[^*]+\*\*|(?:^|(?<=\s))_[^_]+_(?=\s|$|[.,)]))/g;

function inlineSegments(text) {
  const out = [];
  let last = 0;
  for (const m of String(text || "").matchAll(INLINE_RX)) {
    if (m.index > last) out.push({ k: "t", v: text.slice(last, m.index) });
    const tok = m[0];
    if (tok.startsWith("`")) out.push({ k: "code", v: tok.slice(1, -1) });
    else if (tok.startsWith("**")) out.push({ k: "b", v: tok.slice(2, -2) });
    else out.push({ k: "i", v: tok.replace(/^_|_$/g, "") });
    last = m.index + tok.length;
  }
  if (last < String(text || "").length) out.push({ k: "t", v: text.slice(last) });
  return out;
}

/* ── domain heuristics for table cells ── */
const isNumericCell = (c) =>
  /^([+\-−]?₹|[+\-−]?\d|∞|—)/.test(c) || /%$/.test(c) || /^\d/.test(c);
// sign is EXPLICIT in engine output: negatives carry "-"/"−"; unsigned ₹ stays
// neutral on purpose (Max DD is a positive ₹ figure that must not read green).
const cellTone = (c) =>
  /^[-−]₹/.test(c) ? "loss" : /^\+₹/.test(c) ? "profit"
    : c.includes("⚠") || /FLIP/.test(c) ? "warn" : null;

/* ── React renderer ── */
export default function ReportView({ markdown, colors, typography }) {
  const c = colors;
  const blocks = useMemo(() => parseReportMd(markdown), [markdown]);
  const secRefs = useRef({});
  const sections = blocks.filter((b) => b.t === "h2");

  const inl = (text, keyBase, depth = 0) => inlineSegments(text).map((s, j) => {
    const key = `${keyBase}-${depth}-${j}`;
    if (s.k === "code") return (
      <code key={key} style={{ fontFamily: "'JetBrains Mono','Fira Code',monospace",
        fontSize: "0.92em", background: c.bg.tertiary, padding: "1px 5px", borderRadius: 4 }}>{s.v}</code>);
    // one level of nesting: the attribution line is _italic with `code` inside_
    if (s.k === "b") return <b key={key} style={{ color: c.text.primary }}>{depth < 1 ? inl(s.v, key, depth + 1) : s.v}</b>;
    if (s.k === "i") return <i key={key} style={{ color: c.text.tertiary }}>{depth < 1 ? inl(s.v, key, depth + 1) : s.v}</i>;
    return <React.Fragment key={key}>{s.v}</React.Fragment>;
  });

  let h2Idx = -1;
  return (
    <div style={{ maxHeight: "65vh", overflow: "auto", paddingRight: 4 }}>
      {/* section jump nav */}
      {sections.length > 1 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12,
          position: "sticky", top: 0, zIndex: 1, background: c.bg.tertiary,
          padding: "6px 0", borderBottom: `1px solid ${c.border.dark}` }}>
          {sections.map((s, idx) => (
            <button key={idx}
              onClick={() => secRefs.current[idx]?.scrollIntoView({ behavior: "smooth", block: "start" })}
              style={{ padding: "3px 10px", borderRadius: 10, border: `1px solid ${c.border.light}`,
                background: c.bg.secondary, color: c.text.muted, fontSize: 11, fontWeight: 600, cursor: "pointer" }}>
              {s.text.replace(/^\d+\.\s*/, "").split("(")[0].trim()}
            </button>
          ))}
        </div>
      )}
      {blocks.map((b, bi) => {
        if (b.t === "h1") return (
          <div key={bi} style={{ fontSize: 20, fontWeight: 800, color: c.text.primary, margin: "4px 0 2px" }}>{inl(b.text, bi)}</div>);
        if (b.t === "h2") {
          h2Idx += 1;
          const my = h2Idx;
          return (
            <div key={bi} ref={(el) => { secRefs.current[my] = el; }}
              style={{ fontSize: 15, fontWeight: 700, color: c.text.primary,
                margin: "22px 0 8px", paddingBottom: 5, borderBottom: `1px solid ${c.border.light}`,
                scrollMarginTop: 44 }}>
              {inl(b.text, bi)}
            </div>);
        }
        if (b.t === "h3") return (
          <div key={bi} style={{ fontSize: 13, fontWeight: 700, color: c.text.secondary, margin: "14px 0 6px" }}>{inl(b.text, bi)}</div>);
        if (b.t === "ul") return (
          <ul key={bi} style={{ margin: "6px 0 10px", paddingLeft: 22 }}>
            {b.items.map((it, ii) => (
              <li key={ii} style={{ fontSize: 12.5, color: c.text.secondary, lineHeight: 1.65, marginBottom: 3 }}>{inl(it, `${bi}-${ii}`)}</li>
            ))}
          </ul>);
        if (b.t === "table") return (
          <div key={bi} style={{ overflowX: "auto", margin: "8px 0 14px" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", ...typography.bodyMedium }}>
              <thead>
                <tr style={{ background: c.bg.tertiary }}>
                  {b.header.map((h, hi) => (
                    <th key={hi} style={{ padding: "7px 10px", fontSize: 10, fontWeight: 800,
                      letterSpacing: 0.4, textTransform: "uppercase", color: c.text.muted,
                      textAlign: hi === 0 ? "left" : "right", whiteSpace: "nowrap",
                      borderBottom: `2px solid ${c.border.light}` }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.body.map((row, ri) => (
                  <tr key={ri} style={{ background: ri % 2 ? c.bg.secondary : "transparent",
                    borderTop: `1px solid ${c.border.dark}` }}>
                    {row.map((cell, ci) => {
                      const tone = cellTone(cell);
                      const num = isNumericCell(cell);
                      return (
                        <td key={ci} style={{ padding: "6px 10px", fontSize: 12, whiteSpace: "nowrap",
                          // content-based alignment: numbers right, text left —
                          // run labels in the ranking table must not right-align
                          textAlign: ci === 0 || !num ? "left" : "right",
                          fontFamily: num ? "'JetBrains Mono','Fira Code',monospace" : undefined,
                          fontWeight: ci === 0 ? 700 : tone ? 700 : 400,
                          color: tone === "loss" ? c.loss : tone === "profit" ? c.profit
                            : tone === "warn" ? c.warning
                            : ci === 0 ? c.text.primary : c.text.secondary }}>
                          {tone === "warn" && cell
                            ? <span style={{ padding: "1px 7px", borderRadius: 4, background: c.warningBg, fontSize: 11 }}>{cell}</span>
                            : (cell || "—")}
                        </td>);
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>);
        return (
          <p key={bi} style={{ fontSize: 12.5, color: c.text.secondary, lineHeight: 1.7, margin: "6px 0" }}>{inl(b.text, bi)}</p>);
      })}
    </div>
  );
}

/* ── standalone HTML export (share with people who don't have the app) ── */
const _esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function inlineHtml(text, depth = 0) {
  return inlineSegments(text).map((s) =>
    s.k === "code" ? `<code>${_esc(s.v)}</code>`
      : s.k === "b" ? `<b>${depth < 1 ? inlineHtml(s.v, depth + 1) : _esc(s.v)}</b>`
      : s.k === "i" ? `<i>${depth < 1 ? inlineHtml(s.v, depth + 1) : _esc(s.v)}</i>`
      : _esc(s.v)).join("");
}

export function buildReportHtml(markdown, title = "Backtest Report") {
  const blocks = parseReportMd(markdown);
  const P = { bg: "#0b1220", panel: "#111a2c", border: "#233045", text: "#c7d2e3",
              head: "#f1f5fb", muted: "#7d8aa0", loss: "#ef4444", profit: "#10b981",
              warn: "#f59e0b", zebra: "#0e1626" };
  const parts = [];
  for (const b of blocks) {
    if (b.t === "h1") parts.push(`<h1>${inlineHtml(b.text)}</h1>`);
    else if (b.t === "h2") parts.push(`<h2>${inlineHtml(b.text)}</h2>`);
    else if (b.t === "h3") parts.push(`<h3>${inlineHtml(b.text)}</h3>`);
    else if (b.t === "ul") parts.push(`<ul>${b.items.map((it) => `<li>${inlineHtml(it)}</li>`).join("")}</ul>`);
    else if (b.t === "table") {
      // content-based alignment: a column is right-aligned when its FIRST
      // body cell is numeric (headers follow their column's data)
      const colNum = (ci) => b.body.length ? isNumericCell(b.body[0][ci] || "") : ci !== 0;
      const th = b.header.map((h, i) =>
        `<th class="${i !== 0 && colNum(i) ? "r" : "l"}">${_esc(h)}</th>`).join("");
      const trs = b.body.map((row) => "<tr>" + row.map((cell, ci) => {
        const tone = cellTone(cell);
        const num = isNumericCell(cell);
        const cls = [ci === 0 || !num ? "l" : "r", ci === 0 ? "first" : "",
                     num ? "mono" : "",
                     tone === "loss" ? "loss" : tone === "profit" ? "profit" : tone === "warn" ? "warn" : ""]
          .filter(Boolean).join(" ");
        return `<td class="${cls}">${_esc(cell || "—")}</td>`;
      }).join("") + "</tr>").join("");
      parts.push(`<div class="tw"><table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`);
    } else parts.push(`<p>${inlineHtml(b.text)}</p>`);
  }
  return `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${_esc(title)}</title>
<style>
body{background:${P.bg};color:${P.text};font-family:-apple-system,'Segoe UI',Inter,sans-serif;
  margin:0;padding:24px;line-height:1.65;font-size:14px}
main{max-width:980px;margin:0 auto;background:${P.panel};border:1px solid ${P.border};
  border-radius:12px;padding:24px 28px}
h1{color:${P.head};font-size:22px;margin:4px 0 2px}
h2{color:${P.head};font-size:16px;margin:26px 0 8px;padding-bottom:6px;border-bottom:1px solid ${P.border}}
h3{color:${P.text};font-size:13.5px;margin:16px 0 6px}
p{font-size:13px;color:${P.text}} i{color:${P.muted}} b{color:${P.head}}
code{font-family:ui-monospace,'JetBrains Mono',monospace;background:${P.zebra};
  padding:1px 5px;border-radius:4px;font-size:.92em}
ul{padding-left:22px} li{font-size:13px;margin-bottom:4px}
.tw{overflow-x:auto;margin:8px 0 14px}
table{border-collapse:collapse;width:100%}
th{font-size:10px;letter-spacing:.4px;text-transform:uppercase;color:${P.muted};
  padding:7px 10px;border-bottom:2px solid ${P.border};white-space:nowrap}
td{padding:6px 10px;font-size:12.5px;border-top:1px solid ${P.border};white-space:nowrap}
tr:nth-child(even) td{background:${P.zebra}}
.l{text-align:left}.r{text-align:right}
.mono{font-family:ui-monospace,'JetBrains Mono',monospace}
.first{color:${P.head};font-weight:700}
.loss{color:${P.loss};font-weight:700}.profit{color:${P.profit};font-weight:700}
.warn{color:${P.warn};font-weight:700}
footer{max-width:980px;margin:10px auto 0;color:${P.muted};font-size:11px;text-align:center}
</style></head><body><main>${parts.join("\n")}</main>
<footer>Generated by Scalp Terminal — every figure computed by the report engine.</footer>
</body></html>`;
}