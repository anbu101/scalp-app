#!/usr/bin/env python3
"""
apply_trades_multi_strat.py — Trades page: multi-select strategy filter
Fence: TRADES_MULTI_STRAT_20261011           (frontend only, PaperTrades.jsx)

What it does
  * Strategy tabs become a multi-select:
      - plain click         → show ONLY that strategy (old behaviour, unchanged)
      - ⌘ / Ctrl + click    → add / remove that strategy
      - "Multi" toggle      → every click adds / removes (for touch / phone)
      - "All"               → clear the selection
  * Summary cards, table, CSV export all follow the selection (they already
    read `filtered`; only the filter predicate changes).
  * Strategy column is shown whenever more than one strategy is in view
    (All or ≥2 selected); Side column / lot multiplier follow the selection.
  * With ≥2 strategies selected, a "Selected strategies" breakdown card
    appears under the summary: per-strategy trades, W/L, win %, gross,
    charges, net, max DD + a combined row (book DD, chronological across
    strategies) and a combined net-equity curve by exit day.
    Uses the SAME charge/lot formula as the summary cards, so the combined
    row reconciles to the Net P&L card to the rupee.

Safety
  * Aborts if the fence is already present (idempotent).
  * Aborts if PaperTrades.jsx has uncommitted changes (git status 'M'),
    unless --allow-dirty.
  * Every anchor must match exactly once, else nothing is written.
  * JSX parse gate (@babel/parser from frontend/node_modules, or esbuild)
    before the write; .bak-TRADES_MULTI_STRAT_20261011 backup kept.

Usage (repo root):   python3 apply_trades_multi_strat.py [--allow-dirty]
Then:                ./desktop/build-scalp.sh   (frontend-only change)
"""
import os, re, sys, shutil, subprocess, tempfile

FENCE = "TRADES_MULTI_STRAT_20261011"
REL   = "frontend/src/pages/PaperTrades.jsx"
ALLOW_DIRTY = "--allow-dirty" in sys.argv


def die(msg):
    print(f"✗ {msg}\n  Nothing written.")
    sys.exit(1)


def find_root():
    here = os.path.abspath(os.getcwd())
    for c in (here, os.path.dirname(os.path.abspath(__file__))):
        if os.path.isfile(os.path.join(c, REL)):
            return c
    die(f"run from the repo root (could not find {REL})")


# ───────────────────────────────────────────────────────────── payloads

NEW_TABS_AND_BREAKDOWN = r'''// ── TRADES_MULTI_STRAT_20261011 BEGIN ── multi-select strategy filter.
// selected = Set of canonical names; empty Set = All.
// Plain click = only this one (the old single-select behaviour);
// ⌘/Ctrl-click, or any click while "Multi" is on, adds/removes it.
function StrategyTabs({ options, selected, multi, onSelectOnly, onToggle, onClear, onMultiChange }) {
  const allActive = selected.size === 0;
  return (
    <div style={{
      display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 3,
      background: colors.bg.secondary,
      padding: 3, borderRadius: 8,
      border: `1px solid ${colors.border.dark}`,
    }} data-testid="strat-tabs">
      {options.map((opt) => {
        const isAll    = opt.value === "ALL";
        const isActive = isAll ? allActive : selected.has(opt.value);
        return (
          <button key={opt.value}
            aria-pressed={isActive}
            data-strat={opt.value}
            title={isAll ? "Show all strategies"
              : multi ? "Click to add / remove"
              : "Click: show only this · ⌘/Ctrl-click: add to selection"}
            onClick={(e) => {
              if (isAll) { onClear(); return; }
              if (multi || e.metaKey || e.ctrlKey) onToggle(opt.value);
              else onSelectOnly(opt.value);
            }}
            style={{
              padding: "6px 16px", borderRadius: 5, border: "none",
              background: isActive ? colors.bg.tertiary : "transparent",
              color:      isActive ? colors.text.primary : colors.text.muted,
              fontSize: 12, fontWeight: isActive ? 600 : 400, cursor: "pointer",
              boxShadow: isActive ? "0 1px 3px var(--c-shadow)" : "none",
              transition: "all 0.15s ease",
              borderBottom: isActive ? `2px solid ${colors.primary}` : "2px solid transparent",
              display: "inline-flex", alignItems: "center",
            }}
          >
            {multi && !isAll && (
              <span aria-hidden="true" style={{
                width: 11, height: 11, marginRight: 6, borderRadius: 3, flexShrink: 0,
                border: `1px solid ${isActive ? colors.primary : colors.border.light}`,
                background: isActive ? colors.primary : "transparent",
                color: colors.bg.secondary, fontSize: 9, lineHeight: "10px",
                display: "inline-flex", alignItems: "center", justifyContent: "center",
              }}>{isActive ? "✓" : ""}</span>
            )}
            {opt.label}
            {opt.count != null && (
              <span style={{
                marginLeft: 6, fontSize: 10, padding: "1px 5px", borderRadius: 10,
                background: isActive ? colors.primaryBg : alpha(colors.text.primary, 6),
                color:      isActive ? colors.primary   : colors.text.muted,
              }}>
                {opt.count}
              </span>
            )}
          </button>
        );
      })}
      <span style={{ width: 1, height: 18, background: colors.border.light, margin: "0 4px" }} />
      <button
        data-testid="strat-multi"
        aria-pressed={multi}
        title="Pick several strategies (⌘/Ctrl-click works without this)"
        onClick={() => onMultiChange(!multi)}
        style={{
          padding: "6px 12px", borderRadius: 5, cursor: "pointer",
          border: `1px solid ${multi ? colors.primary : colors.border.light}`,
          background: multi ? colors.primaryBg : "transparent",
          color: multi ? colors.primary : colors.text.muted,
          fontSize: 12, fontWeight: 600, transition: "all 0.15s ease",
        }}
      >
        {multi ? "☑" : "☐"} Multi{selected.size > 1 ? ` · ${selected.size}` : ""}
      </button>
    </div>
  );
}

// Per-strategy breakdown for a multi-selection. Lot/charge math is the
// exact formula of the summary cards, so "Combined" net == Net P&L card.
function StrategyBreakdown({ trades, selected, scalpLots, showParams }) {
  const data = useMemo(() => {
    const rowPnl = (t) => {
      const lots = isScalpStrategy(t.strategy_name) ? scalpLots : 1;
      const qty  = (t.qty || 1) * lots;
      const base = t.pnl_value || 0;
      const chg  = calcCharges(t.entry_price, t.exit_price, qty);
      return { gross: base * lots, chg, net: base * lots - chg };
    };
    const stat = (list) => {
      const closed = list
        .filter((t) => t.state === "CLOSED")
        .sort((a, b) => (a.exit_time || 0) - (b.exit_time || 0));
      let gross = 0, chg = 0, net = 0, peak = 0, dd = 0, w = 0, l = 0;
      closed.forEach((t) => {
        const r = rowPnl(t);
        gross += r.gross; chg += r.chg; net += r.net;
        if (net > peak) peak = net;
        if (peak - net > dd) dd = peak - net;
        const p = t.pnl_value || 0;
        if (p > 0) w++; else if (p < 0) l++;
      });
      return {
        closed: closed.length,
        open: list.filter((t) => t.state === "OPEN").length,
        gross, chg, net, dd, w, l,
        wr: closed.length ? (w / closed.length) * 100 : null,
        closedList: closed,
      };
    };
    const rows = [...selected]
      .map((name) => ({ name, ...stat(trades.filter((t) => displayStrategyName(t.strategy_name) === name)) }))
      .sort((a, b) => b.net - a.net);
    const total = stat(trades);
    const byDay = new Map();
    total.closedList.forEach((t) => {
      const d = new Date((t.exit_time || 0) * 1000);
      const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      byDay.set(k, (byDay.get(k) || 0) + rowPnl(t).net);
    });
    let cum = 0;
    const curve = [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([k, v]) => ({ k, v: (cum += v) }));
    return { rows, total, curve };
  }, [trades, selected, scalpLots]);

  const { rows, total, curve } = data;
  const rs = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net)));
  const label  = (n) => (showParams ? n : maskCanon(n));
  const th = { padding: "8px 10px", fontSize: 10, fontWeight: 600, letterSpacing: "0.5px",
               textTransform: "uppercase", color: colors.text.muted, textAlign: "right", whiteSpace: "nowrap" };
  const td = { padding: "7px 10px", fontSize: 12, textAlign: "right", whiteSpace: "nowrap", ...typography.mono };

  const Row = ({ r, isTotal }) => (
    <tr style={{ borderTop: `1px solid ${isTotal ? colors.border.light : colors.border.dark}`,
                 background: isTotal ? colors.bg.tertiary : "transparent" }}>
      <td style={{ ...td, textAlign: "left", fontFamily: "var(--c-font-ui)", fontWeight: isTotal ? 700 : 400 }}>
        {isTotal ? "Combined" : <StrategyChip name={r.name} />}
      </td>
      <td style={td}>{r.closed}{r.open ? <span style={{ color: colors.warning }}> +{r.open}</span> : null}</td>
      <td style={td}>{r.w}W / {r.l}L</td>
      <td style={{ ...td, color: r.wr == null ? colors.text.muted : r.wr >= 50 ? colors.profit : colors.loss }}>
        {r.wr == null ? "—" : `${r.wr.toFixed(1)}%`}
      </td>
      <td style={{ ...td, ...pnlStyle(r.gross) }}>{r.closed ? rs(r.gross) : "—"}</td>
      <td style={{ ...td, color: colors.loss }}>{r.closed ? `−₹${Math.round(r.chg).toLocaleString("en-IN")}` : "—"}</td>
      <td style={{ ...td, fontWeight: 700, ...pnlStyle(r.net) }}>{r.closed ? rs(r.net) : "—"}</td>
      <td style={{ ...td, color: r.dd > 0 ? colors.loss : colors.text.muted }}>
        {r.dd > 0 ? `−₹${Math.round(r.dd).toLocaleString("en-IN")}` : "—"}
      </td>
      <td style={{ ...td, width: 140, textAlign: "left" }}>
        {!isTotal && r.closed > 0 && (
          <div style={{ display: "flex", alignItems: "center", height: 8 }}>
            <div style={{ flex: 1, display: "flex", justifyContent: "flex-end" }}>
              {r.net < 0 && <div style={{ width: `${(Math.abs(r.net) / maxAbs) * 100}%`, height: 8, borderRadius: "3px 0 0 3px", background: colors.loss }} />}
            </div>
            <div style={{ width: 1, height: 12, background: colors.border.light }} />
            <div style={{ flex: 1 }}>
              {r.net > 0 && <div style={{ width: `${(r.net / maxAbs) * 100}%`, height: 8, borderRadius: "0 3px 3px 0", background: colors.profit }} />}
            </div>
          </div>
        )}
      </td>
    </tr>
  );

  // Equity curve (combined, by exit day)
  let chart = null;
  if (curve.length >= 2) {
    const W = 600, H = 90, pad = 4;
    const vals = curve.map((p) => p.v);
    const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    const span = hi - lo || 1;
    const x = (i) => pad + (i / (curve.length - 1)) * (W - 2 * pad);
    const y = (v) => pad + (1 - (v - lo) / span) * (H - 2 * pad);
    const pts = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    const last = vals[vals.length - 1];
    const stroke = last >= 0 ? colors.profit : colors.loss;
    chart = (
      <div style={{ padding: `0 ${spacing.lg}px ${spacing.md}px` }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: colors.text.muted, marginBottom: 4 }}>
          <span>Combined net equity · by exit day · {curve.length} days</span>
          <span style={{ ...typography.mono }}>{curve[0].k} → {curve[curve.length - 1].k}</span>
        </div>
        <svg data-testid="strat-equity" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
             style={{ width: "100%", height: H, display: "block" }}>
          <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="currentColor" strokeOpacity="0.25"
                strokeDasharray="4 4" vectorEffect="non-scaling-stroke" style={{ color: colors.text.muted }} />
          <polyline points={pts} fill="none" stroke={stroke} strokeWidth="2"
                    vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        </svg>
      </div>
    );
  }

  return (
    <Card elevated style={{ marginBottom: spacing.xl, overflow: "hidden" }}>
      <div data-testid="strat-breakdown" style={{
        display: "flex", alignItems: "baseline", justifyContent: "space-between",
        padding: `${spacing.md}px ${spacing.lg}px`, gap: spacing.md, flexWrap: "wrap",
      }}>
        <div style={{ ...typography.label, color: colors.text.muted }}>
          Selected strategies <span style={{ color: colors.primary }}>· {rows.length}</span>
        </div>
        <div style={{ fontSize: 10, color: colors.text.muted }}>
          Max DD = peak-to-trough of cumulative net, closed trades in exit order · Combined DD is the book's, not the sum
        </div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: "left" }}>Strategy</th>
              <th style={th}>Closed</th>
              <th style={th}>W / L</th>
              <th style={th}>Win %</th>
              <th style={th}>Gross</th>
              <th style={th}>Charges</th>
              <th style={th}>Net</th>
              <th style={th}>Max DD</th>
              <th style={{ ...th, textAlign: "center" }}>Net share</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => <Row key={r.name} r={r} />)}
            <Row r={total} isTotal />
          </tbody>
        </table>
      </div>
      {chart}
    </Card>
  );
}
// ── TRADES_MULTI_STRAT_20261011 END ──
'''

EDITS = [
    # (label, old, new)
    ("state",
     '  const [stratFilter, setStratFilter] = useState("ALL");\n',
     '  // ── TRADES_MULTI_STRAT_20261011 ── Set of canonical names; empty = All.\n'
     '  const [stratSel,   setStratSel]   = useState(() => new Set());\n'
     '  const [stratMulti, setStratMulti] = useState(false);\n'
     '  // Legacy single-value view kept for the existing "ALL" checks below.\n'
     '  const stratFilter = stratSel.size === 0 ? "ALL" : stratSel.size === 1 ? [...stratSel][0] : "MULTI";\n'
     '  const showStratCol = stratSel.size !== 1;\n'
     '  const stratLabel = stratSel.size === 1\n'
     '    ? (showParams ? [...stratSel][0] : maskCanon([...stratSel][0]))\n'
     '    : `${stratSel.size} selected`;\n'
     '  const toggleStrat = useCallback((v) => setStratSel((prev) => {\n'
     '    const n = new Set(prev); if (n.has(v)) n.delete(v); else n.add(v); return n;\n'
     '  }), []);\n'
     '  const selectOnlyStrat = useCallback((v) => setStratSel((prev) =>\n'
     '    (prev.size === 1 && prev.has(v)) ? prev : new Set([v])), []);\n'
     '  const clearStrat = useCallback(() => setStratSel(new Set()), []);\n'),

    ("filtered",
     '  const filtered = useMemo(() =>\n'
     '    stratFilter === "ALL"\n'
     '      ? dateFiltered\n'
     '      : dateFiltered.filter((t) => matchesStrategy(t, stratFilter)),\n'
     '    [dateFiltered, stratFilter]\n'
     '  );\n',
     '  const filtered = useMemo(() =>          // ── TRADES_MULTI_STRAT_20261011 ──\n'
     '    stratSel.size === 0\n'
     '      ? dateFiltered\n'
     '      : dateFiltered.filter((t) => stratSel.has(displayStrategyName(t.strategy_name))),\n'
     '    [dateFiltered, stratSel]\n'
     '  );\n'),

    ("scalp/side",
     '  const showingScalp = stratFilter === "ALL" || stratFilter === "SCALP V1";\n'
     '  // ── CHANGE 5 (desktop): Side column visible for SCALP and HA ──\n'
     '  const showSideCol  = stratFilter === "ALL"\n'
     '    || stratFilter === "SCALP V1"\n'
     '    || stratFilter === "SCALP V2"\n'
     '    || stratFilter === "SCALP V3"\n'
     '    || stratFilter === "SCALP V4"\n'
     '    || stratFilter === "SCALP V5"\n'
     '    || stratFilter === "HA";\n',
     '  // ── TRADES_MULTI_STRAT_20261011 ── evaluated against the whole selection\n'
     '  const showingScalp = stratSel.size === 0 || stratSel.has("SCALP V1");\n'
     '  // ── CHANGE 5 (desktop): Side column visible for SCALP and HA ──\n'
     '  const showSideCol  = stratSel.size === 0\n'
     '    || ["SCALP V1", "SCALP V2", "SCALP V3", "SCALP V4", "SCALP V5", "HA"].some((s) => stratSel.has(s));\n'),

    ("th strategy",
     '{stratFilter === "ALL" && <th style={{ ...TH, width: "7%" }}>Strategy</th>}',
     '{showStratCol && <th style={{ ...TH, width: "7%" }}>Strategy</th>}'),

    ("colCount",
     'const colCount = (stratFilter === "ALL" ? 1 : 0)',
     'const colCount = (showStratCol ? 1 : 0)'),

    ("td strategy",
     '{stratFilter === "ALL" && (\n'
     '                              <td style={TD}><StrategyChip name={trade.strategy_name} /></td>',
     '{showStratCol && (\n'
     '                              <td style={TD}><StrategyChip name={trade.strategy_name} /></td>'),

    ("empty title",
     '                  : stratFilter !== "ALL"\n'
     '                  ? `No ${stratFilter} trades in this period`\n',
     '                  : stratSel.size === 1\n'
     '                  ? `No ${stratLabel} trades in this period`\n'
     '                  : stratSel.size > 1\n'
     '                  ? `No trades from the ${stratSel.size} selected strategies in this period`\n'),

    ("tabs usage",
     '          <StrategyTabs\n'
     '            options={tabOptions}\n'
     '            active={stratFilter}\n'
     '            onChange={(v) => setStratFilter(v)}\n'
     '          />\n',
     '          <StrategyTabs\n'
     '            options={tabOptions}\n'
     '            selected={stratSel}\n'
     '            multi={stratMulti}\n'
     '            onSelectOnly={selectOnlyStrat}\n'
     '            onToggle={toggleStrat}\n'
     '            onClear={clearStrat}\n'
     '            onMultiChange={setStratMulti}\n'
     '          />\n'),

    ("breakdown mount",
     '      {/* ── Trades — card list on mobile, table on desktop ── */}\n',
     '      {/* ── TRADES_MULTI_STRAT_20261011 ── per-strategy breakdown */}\n'
     '      {stratSel.size >= 2 && filtered.length > 0 && (\n'
     '        <StrategyBreakdown trades={filtered} selected={stratSel} scalpLots={scalpLots} showParams={showParams} />\n'
     '      )}\n'
     '\n'
     '      {/* ── Trades — card list on mobile, table on desktop ── */}\n'),
]

TABS_START = "function StrategyTabs({ options, active, onChange }) {"
TABS_END   = "\n/* ─────────────────────────────────────────────\n   Lot multiplier control"


def transform(src):
    if src.count(TABS_START) != 1:
        die(f"anchor 'StrategyTabs signature' found {src.count(TABS_START)}× (expected 1)")
    if src.count(TABS_END) != 1:
        die(f"anchor 'Lot multiplier header' found {src.count(TABS_END)}× (expected 1)")
    a = src.index(TABS_START)
    b = src.index(TABS_END)
    if b < a:
        die("StrategyTabs block anchors out of order")
    src = src[:a] + NEW_TABS_AND_BREAKDOWN + src[b:]

    for label, old, new in EDITS:
        n = src.count(old)
        if n != 1:
            die(f"anchor '{label}' found {n}× (expected 1) — file differs from GitHub main")
        src = src.replace(old, new)

    for leftover in ("setStratFilter", "active={stratFilter}", "matchesStrategy(t, stratFilter)"):
        if leftover in src:
            die(f"leftover reference after patch: {leftover}")
    return src


def parse_gate(root, path):
    fe = os.path.join(root, "frontend")
    js = ("const fs=require('fs');const p=require('@babel/parser');"
          "p.parse(fs.readFileSync(process.argv[1],'utf8'),"
          "{sourceType:'module',plugins:['jsx']});console.log('ok')")
    try:
        r = subprocess.run(["node", "-e", js, path], cwd=fe, capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and "ok" in r.stdout:
            return "babel"
        if "Cannot find module" not in (r.stderr or ""):
            die(f"JSX parse failed:\n{r.stderr.strip()[:1500]}")
    except FileNotFoundError:
        pass
    for cand in (os.path.join(fe, "node_modules", ".bin", "esbuild"),
                 os.path.join(root, "desktop", "node_modules", ".bin", "esbuild")):
        if os.path.exists(cand):
            r = subprocess.run([cand, path, "--loader:.jsx=jsx", "--log-level=error"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                die(f"JSX parse failed (esbuild):\n{r.stderr.strip()[:1500]}")
            return "esbuild"
    print("  ! no @babel/parser / esbuild found — parse gate skipped; the build will catch syntax errors")
    return None


def main():
    root = find_root()
    path = os.path.join(root, REL)
    src  = open(path, encoding="utf-8").read()

    if FENCE in src:
        print(f"✓ {FENCE} already applied — nothing to do.")
        return

    try:
        st = subprocess.run(["git", "status", "--porcelain", "--", REL], cwd=root,
                            capture_output=True, text=True).stdout.strip()
        if st and not ALLOW_DIRTY:
            die(f"{REL} has uncommitted changes ({st}). Commit/stash first, or pass --allow-dirty.")
    except FileNotFoundError:
        print("  ! git not found — skipping working-copy check")

    out = transform(src)

    with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False, encoding="utf-8") as tf:
        tf.write(out)
        tmp = tf.name
    try:
        gate = parse_gate(root, tmp)
    finally:
        os.unlink(tmp)

    bak = f"{path}.bak-{FENCE}"
    shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)

    print(f"✓ {FENCE} applied to {REL}" + (f" (parse gate: {gate})" if gate else ""))
    print(f"  backup: {os.path.relpath(bak, root)}")
    print("  next:   ./desktop/build-scalp.sh   (frontend-only; backend untouched)")


if __name__ == "__main__":
    main()
