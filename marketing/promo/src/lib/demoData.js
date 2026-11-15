// FICTIONAL demo data for the promo video. Not real trading data.
// Shape mirrors GET /api/fleet/today so the real Fleet components render it as-is.

export const OPEN_MIN = 555;   // 09:15
export const END_MIN = 940;    // 15:40

// seeded PRNG
function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

// Smooth path through anchors with bridge noise (zero at anchors).
function genPath(seed, anchors, vol) {
  const r = rng(seed);
  const waves = [0, 1, 2, 3].map((i) => ({ f: 1 + i * 1.7 + r() * 1.3, p: r() * Math.PI * 2, a: (1 / (i + 1)) * (0.6 + r() * 0.8) }));
  const jitter = r() * 100;
  const out = [];
  for (let k = 0; k < anchors.length - 1; k++) {
    const [m0, v0] = anchors[k], [m1, v1] = anchors[k + 1];
    for (let m = m0; m < m1 || (k === anchors.length - 2 && m === m1); m++) {
      const f = (m - m0) / Math.max(1, m1 - m0);
      const base = v0 + (v1 - v0) * f;
      let n = 0;
      waves.forEach((w) => { n += w.a * Math.sin(w.p + (m + jitter) * w.f * 0.09); });
      const tick = Math.sin(m * 1.37 + seed) * 0.25;
      out.push([m, Math.round(base + (n + tick) * vol * Math.sin(Math.PI * f))]);
      if (m === m1) break;
    }
  }
  return out;
}

// One row per (strategy, book). entry = first order minute; exit = flat after.
export const ROWS = [
  { id: "TMA_V2", book: "LIVE", entry: OPEN_MIN, carried: true, legs: 2,
    anchors: [[555, 1150], [600, 2400], [660, 1800], [720, 5200], [780, 6400], [850, 9200], [940, 12410]], vol: 900 },
  { id: "IC_V2", book: "LIVE", entry: 560, legs: 4,
    anchors: [[560, 0], [620, 1300], [700, 600], [780, 3100], [850, 4860], [940, 6230]], vol: 320 },
  { id: "TSG_V1", book: "PAPER", entry: 556, legs: 4,
    anchors: [[556, 0], [600, -900], [660, 1500], [720, 3900], [790, 5100], [850, 7350], [940, 9420]], vol: 700 },
  { id: "IC_V2", book: "PAPER", entry: 560, legs: 4,
    anchors: [[560, 0], [620, 1380], [700, 520], [780, 3240], [850, 4940], [940, 6230]], vol: 320 },
  { id: "ORB_V1", book: "PAPER", entry: 585, exit: 640, exitKind: "TARGET", charges: 150,
    anchors: [[585, 0], [605, 1400], [625, 2300], [640, 4150]], vol: 280 },
  { id: "VET_V1", book: "PAPER", entry: 620, exit: 665, exitKind: "SL", charges: 180,
    anchors: [[620, 0], [640, 900], [655, -1900], [665, -3860]], vol: 260 },
];

export const IDLE = [
  { id: "BRK_V1", mode: "PAPER" },
  { id: "EMC_V1", mode: "PAPER" },
  { id: "HA_V1", mode: "OFF" },
  { id: "BB_V1", mode: "OFF" },
];

ROWS.forEach((r, i) => {
  r.key = `${r.id}:${r.book}`;
  r.full = genPath(31 + i * 17, r.anchors, r.vol);
});

const EPOCH_DAY = Date.UTC(2026, 10, 9) / 1000 - 19800; // 09 Nov 2026 00:00 IST
const tsAt = (m) => EPOCH_DAY + m * 60;

function rowAt(r, nowMin) {
  if (nowMin < r.entry) return null;
  const upto = r.exit != null ? Math.min(nowMin, r.exit) : nowMin;
  const path = r.full.filter(([m]) => m <= upto);
  const v = path.length ? path[path.length - 1][1] : 0;
  const closed = r.exit != null && nowMin >= r.exit;
  let peak = null, peakMin = null;
  path.forEach(([m, x]) => { if (peak == null || x > peak) { peak = x; peakMin = m; } });
  const charges = closed ? r.charges : 0;
  const ev = closed
    ? { reason: r.exitKind === "SL" ? "SL" : "TARGET", ts: tsAt(r.exit), text: r.exitKind === "SL" ? "Exit · stop hit" : "Exit · target hit" }
    : { reason: "ENTRY", ts: tsAt(r.entry), text: r.carried ? "Holding · trend intact" : "Position open · all legs filled" };
  return {
    key: r.key, id: r.id, book: r.book, mode: r.book,
    state: closed ? "CLOSED" : "OPEN",
    carried: !!r.carried, open_legs: closed ? 0 : (r.legs || 1),
    first_entry_ts: tsAt(r.entry),
    closed_trades: closed ? 1 : 0,
    gross: v, realised: closed ? v : 0, unrealised: closed ? 0 : v,
    charges, peak, peak_min: peakMin, stop: null,
    last_event: ev, path,
  };
}

function sumPaths(rows, nowMin) {
  const out = [];
  for (let m = OPEN_MIN; m <= nowMin; m++) {
    let v = 0, any = false;
    rows.forEach((r) => {
      if (!r.path.length || m < r.path[0][0]) return;
      any = true;
      let last = 0;
      for (const p of r.path) { if (p[0] > m) break; last = p[1]; }
      v += last;
    });
    if (any) out.push([m, v]);
  }
  return out;
}

const PERIOD = {
  LIVE: { week_net: 24310, month_net: 61880, week_trades: 7, month_trades: 23 },
  PAPER: { week_net: 31240, month_net: 48960, week_trades: 16, month_trades: 61 },
};

export function fleetAt(nowMin) {
  const rows = ROWS.map((r) => rowAt(r, nowMin)).filter(Boolean)
    .sort((a, b) => (a.book === b.book ? Math.abs(b.gross) - Math.abs(a.gross) : a.book === "LIVE" ? -1 : 1));
  const paths = {}, totals = {};
  ["LIVE", "PAPER"].forEach((bk) => {
    const br = rows.filter((r) => r.book === bk);
    const p = sumPaths(br, nowMin);
    paths[bk] = p;
    let peak = null, peakMin = null;
    p.forEach(([m, v]) => { if (peak == null || v > peak) { peak = v; peakMin = m; } });
    const gross = br.reduce((s, r) => s + r.gross, 0);
    const charges = br.reduce((s, r) => s + r.charges, 0);
    totals[bk] = {
      rows: br.length, gross,
      realised: br.reduce((s, r) => s + r.realised, 0),
      unrealised: br.reduce((s, r) => s + r.unrealised, 0),
      charges, net: br.reduce((s, r) => s + r.realised, 0) - charges,
      peak, peak_min: peakMin, ...PERIOD[bk],
    };
  });
  const traded = new Set(rows.map((r) => r.id));
  const idle = [...IDLE, ...ROWS.filter((r) => !traded.has(r.id)).map((r) => ({ id: r.id, mode: r.book }))]
    .filter((s, i, a) => a.findIndex((x) => x.id === s.id) === i);
  return { strategies: rows, idle, paths, totals, active: traded.size, fleet: traded.size + idle.length };
}

export const INDICES = {
  NIFTY: { open: 25812.4, prev_close: 25790.15 },
  BANKNIFTY: { open: 58204.9, prev_close: 58331.6 },
};
export function indicesAt(nowMin) {
  const f = (nowMin - OPEN_MIN) / (END_MIN - OPEN_MIN);
  const n = 25812.4 + 74 * f + 18 * Math.sin(nowMin * 0.11) + 7 * Math.sin(nowMin * 0.53);
  const b = 58204.9 - 96 * f + 41 * Math.sin(nowMin * 0.09 + 1) + 12 * Math.sin(nowMin * 0.61);
  return { NIFTY: { ltp: n, prev_close: 25790.15 }, BANKNIFTY: { ltp: b, prev_close: 58331.6 } };
}
