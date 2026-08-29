# UI edit bodies for frontend/src/pages/Backtest.jsx
# fence STOCK_SCREENER_UI_20260828

OLD_STATE = '''  const [vetMinVol, setVetMinVol] = useState(vetSaved.minVol ?? 0);'''
NEW_STATE = '''  const [vetMinVol, setVetMinVol] = useState(vetSaved.minVol ?? 0);
  // ── STOCK_SCREENER_UI_20260828 ── optional daily equity screener gate
  const [vetScrOn, setVetScrOn] = useState(vetSaved.scrOn ?? false);
  const [vetScrEmaFast, setVetScrEmaFast] = useState(vetSaved.scrEmaFast ?? 10);
  const [vetScrEmaSlow, setVetScrEmaSlow] = useState(vetSaved.scrEmaSlow ?? 20);
  const [vetScrSmaTrend, setVetScrSmaTrend] = useState(vetSaved.scrSmaTrend ?? 40);
  const [vetScrVolSma, setVetScrVolSma] = useState(vetSaved.scrVolSma ?? 10);
  const [vetScrMinVolume, setVetScrMinVolume] = useState(vetSaved.scrMinVolume ?? 2000000);
  const [vetScrWindow, setVetScrWindow] = useState(vetSaved.scrWindow ?? 1);'''

OLD_LS = '''minVol: vetMinVol, rollover: vetRollover,'''
NEW_LS = '''minVol: vetMinVol, scrOn: vetScrOn, scrEmaFast: vetScrEmaFast, scrEmaSlow: vetScrEmaSlow, scrSmaTrend: vetScrSmaTrend, scrVolSma: vetScrVolSma, scrMinVolume: vetScrMinVolume, scrWindow: vetScrWindow, rollover: vetRollover,'''

# two dep arrays share the same variable run; anchor each on its unique tail
OLD_DEP_LS = '''vetReenterForced, vetReenterSltp]);'''
NEW_DEP_LS = '''vetReenterForced, vetReenterSltp, vetScrOn, vetScrEmaFast, vetScrEmaSlow, vetScrSmaTrend, vetScrVolSma, vetScrMinVolume, vetScrWindow]);'''

OLD_DEP_CFG = '''vetReenterForced, vetReenterSltp,   // ── VET_V1 ── stale-closure rule'''
NEW_DEP_CFG = '''vetReenterForced, vetReenterSltp,
    // ── STOCK_SCREENER_UI_20260828 ── buildConfig reads these, so they land
    // in the dep array in the SAME commit (stale-closure rule).
    vetScrOn, vetScrEmaFast, vetScrEmaSlow, vetScrSmaTrend, vetScrVolSma, vetScrMinVolume, vetScrWindow,   // ── VET_V1 ── stale-closure rule'''

OLD_CFG = '''        min_entry_volume: Number(vetMinVol) || 0,'''
NEW_CFG = '''        min_entry_volume: Number(vetMinVol) || 0,
        // ── STOCK_SCREENER_UI_20260828 ──
        screener_enabled: !!vetScrOn,
        screener_ema_fast: Number(vetScrEmaFast) || 10,
        screener_ema_slow: Number(vetScrEmaSlow) || 20,
        screener_sma_trend: Number(vetScrSmaTrend) || 40,
        screener_vol_sma: Number(vetScrVolSma) || 10,
        screener_min_volume: Math.max(0, Number(vetScrMinVolume) || 0),
        screener_cross_window_days: Math.max(1, Number(vetScrWindow) || 1),'''

OLD_UI = '''              <div style={{ marginTop: 6, fontSize: 11, color: colors.text.tertiary }}>
                Regime = SMA{vetTrendLen}'''
NEW_UI = '''              {/* ── STOCK_SCREENER_UI_20260828 ── optional daily equity
                  screener gate. Blocks ENTRIES only; exits, rolls and EOD are
                  untouched. Stock underlyings only — the volume filters are
                  meaningless on an index. */}
              <div style={{ marginTop: spacing.md, paddingTop: spacing.md, borderTop: `1px solid ${colors.border.light}` }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12, color: colors.text.secondary }} title="OFF (default). ON gates ENTRIES to days selected by a daily equity scan run on the PREVIOUS completed session — a position already open is still managed to its own exit. Ignored for index underlyings.">
                  <input type="checkbox" checked={vetScrOn} onChange={(e) => setVetScrOn(e.target.checked)} /> Daily screener gate (stock only)
                </label>
                {vetScrOn && (
                  <div style={{ display: "flex", gap: spacing.md, marginTop: spacing.md, flexWrap: "wrap" }}>
                    <Field label="EMA fast"><input type="number" style={inputStyle} value={vetScrEmaFast} onChange={(e) => setVetScrEmaFast(Number(e.target.value))} title="Daily EMA length. The scan needs EMA(fast) > EMA(slow) on the firing session." /></Field>
                    <Field label="EMA slow"><input type="number" style={inputStyle} value={vetScrEmaSlow} onChange={(e) => setVetScrEmaSlow(Number(e.target.value))} title="Daily EMA length. This is also the line that must CROSS the trend SMA." /></Field>
                    <Field label="Trend SMA"><input type="number" style={inputStyle} value={vetScrSmaTrend} onChange={(e) => setVetScrSmaTrend(Number(e.target.value))} title="Daily SMA the slow EMA must cross ABOVE. This is the longest lookback, so the run needs this many daily bars before the start date or it ABORTS rather than trading ungated." /></Field>
                    <Field label="Volume SMA"><input type="number" style={inputStyle} value={vetScrVolSma} onChange={(e) => setVetScrVolSma(Number(e.target.value))} title="Daily volume must exceed its own SMA of this length. A flat volume series never exceeds its own average, so this leg alone can silence the gate." /></Field>
                    <Field label="Min daily volume"><input type="number" style={inputStyle} value={vetScrMinVolume} onChange={(e) => setVetScrMinVolume(Number(e.target.value))} title="Absolute share-count floor. Designed to drop illiquid names from a full-universe scan — on a large cap it is always true and does nothing. NOTE it is a SHARE count, so a split or bonus inside the range shifts what it means." /></Field>
                    <Field label="Cross window (days)"><input type="number" style={inputStyle} value={vetScrWindow} onChange={(e) => setVetScrWindow(Number(e.target.value))} title="TRADING days kept open after a cross fires. 1 (default) reproduces the screener exactly: fire on the session that closes, trade the next one only. Larger values test how fast the edge decays." /></Field>
                  </div>
                )}
                {vetScrOn && (
                  <div style={{ marginTop: 6, fontSize: 11, color: colors.text.tertiary }}>
                    Entries are permitted only on the {vetScrWindow} trading day{Number(vetScrWindow) === 1 ? "" : "s"} AFTER a session where all four hold: EMA{vetScrEmaFast} &gt; EMA{vetScrEmaSlow}, EMA{vetScrEmaSlow} CROSSED ABOVE SMA{vetScrSmaTrend}, volume &gt; SMA{vetScrVolSma}(volume), and volume &gt; {Number(vetScrMinVolume).toLocaleString("en-IN")}. The cross is an EVENT, not a state — a slow EMA sitting above the SMA for months fires ONCE. The firing session is never itself tradable, so no future bar can leak into the decision. Daily bars are aggregated from the 1m corpus, so volume will differ slightly from a live Chartink scan, which includes auction and block deals. Read screener_veto_entries in the run diag.
                  </div>
                )}
              </div>
              <div style={{ marginTop: 6, fontSize: 11, color: colors.text.tertiary }}>
                Regime = SMA{vetTrendLen}'''
