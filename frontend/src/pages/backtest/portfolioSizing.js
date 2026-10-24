// frontend/src/pages/backtest/portfolioSizing.js
//
// ── PF_SHARED_EQUITY_20261024 ── portfolio-level compounding ("shared equity,
// fixed weights") for Backtest → Portfolio.
//
// The per-strategy lot compounding in the runners (lot_compounding.py, equity
// mode) grows each strategy from ITS OWN profits, so the biggest winner grows
// fastest and the portfolio drifts into one strategy. Here every strategy is
// sized from ONE account instead:
//
//   equity(d)  = starting capital + realised net of every strategy's trades
//                closed BEFORE day d − tax already withdrawn
//   s(d)       = equity(d) ÷ starting capital        (capped by Max total lots)
//   lots_i(d)  = max(1, ⌊start_lots_i × s(d)⌋)      (capped by Max lots_i)
//
// so the start-lot ratio (e.g. 3 / 3 / 2) is kept as the account grows or
// shrinks. Same causality as the runner's equity mode: sized once per day
// from trades already closed; a trade keeps the lots of its ENTRY day
// (carried positions are never resized); min 1 lot.
//
//   DD guard (optional, proportional — the runner's ∝ shape at portfolio
//   level): factor = max(0, 1 − dd ÷ limit) on the account's equity peak;
//   lots_i = max(min(rule_i, start_i), round(rule_i × factor)).
//   Tax (optional): each FY's realised net (all strategies, losses carried
//   forward) is taxed and WITHDRAWN on the first sized day on/after the FY
//   ends; the peak shifts with it, so a withdrawal is not a drawdown. The open
//   FY at the end is accrued. For display the FY tax is spread over that FY's
//   trades pro rata (the page's existing convention).
//
// HOW: post-processing of fixed-lot runs, not a re-run. A trade's net is
// scaled by lots_i(entry day) ÷ the run's own lots. Valid because the
// runners' ₹ knobs scale with lots (lot_comp_scale_rs default), so the trade
// path is lot-invariant — the same assumption the runner's equity mode makes.
// Charges scale linearly too (flat per-order brokerage would really cost less
// per lot at size, so this errs conservative). Runs that already compound on
// their own are refused.
import { baseLotsOf, lotCompOf } from "./lotCompounding";

export const PF_SHARED_FENCE = "PF_SHARED_EQUITY_20261024";
const IST_S = 19800;
const fin = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
const netOf = (t) => (t.net_pnl != null ? fin(Number(t.net_pnl)) : fin(Number(t.pnl)) - fin(Number(t.charges)));
const posInt = (v) => { const n = Math.floor(Number(v)); return Number.isFinite(n) && n > 0 ? n : 0; };
const posNum = (v) => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : 0; };

export function istDay(ts) {
  return new Date((Number(ts) + IST_S) * 1000).toISOString().slice(0, 10);
}
export function fyStartOf(dayIso, m = 4) {          // "2024-05-02" → "2024-04-01"
  const y = Number(dayIso.slice(0, 4)), mo = Number(dayIso.slice(5, 7));
  const sy = mo >= m ? y : y - 1;
  return `${sy}-${String(m).padStart(2, "0")}-01`;
}
const fyNext = (start) => `${Number(start.slice(0, 4)) + 1}${start.slice(4)}`;
export const fyLabel = (start) => `${start.slice(0, 4)}-${String((Number(start.slice(0, 4)) + 1) % 100).padStart(2, "0")}`;
const dayBefore = (iso) => new Date(Date.parse(iso + "T00:00:00Z") - 86400000).toISOString().slice(0, 10);

export const SHARED_DEFAULTS = { mode: "run", capital: 1000000, lots: {}, maxLots: {}, maxTotal: "", ddPct: "", taxPct: "", fyMonth: 4,
  floorStart: false };   // ── PF_SHARED_EQUITY_FIX1_20261024 ── never size below start lots

// Normalised settings (numbers, defaults filled from each run's own lots).
export function sharedSettingsOf(entries, raw) {
  const r = { ...SHARED_DEFAULTS, ...(raw || {}) };
  const lots = {}, maxLots = {}, runLots = {};
  for (const { run } of entries) {
    const sid = run.strategy_id;
    runLots[sid] = posInt(baseLotsOf(run.config || {}));
    lots[sid] = posInt(r.lots?.[sid]) || runLots[sid];
    maxLots[sid] = posInt(r.maxLots?.[sid]);
  }
  const dd = posNum(r.ddPct), tax = posNum(r.taxPct);
  return {
    capital: posNum(r.capital), lots, maxLots, runLots,
    maxTotal: posInt(r.maxTotal),
    ddPct: dd > 0 && dd < 100 ? dd : 0,
    taxPct: tax > 0 && tax < 100 ? tax : 0,
    fyMonth: Math.min(12, Math.max(1, posInt(r.fyMonth) || 4)),
    floorStart: r.floorStart === true || String(r.floorStart).toLowerCase() === "true",   // ── PF_SHARED_EQUITY_FIX1_20261024 ──
  };
}

// Exact reasons shared-equity sizing cannot run on this selection ([] = OK).
export function sharedSizingErrors(entries, raw) {
  const errs = [];
  const st = sharedSettingsOf(entries, raw);
  if (!(st.capital > 0)) errs.push("Enter a starting capital above ₹0.");
  for (const { run } of entries) {
    const sid = run.strategy_id;
    if (lotCompOf(run.config || {}, run.date_from, run.date_to, run.summary)) {
      errs.push(`${sid} run ${String(run.run_id).slice(0, 8)} compounds on its own (lot compounding is on) — pick a fixed-lot run of it; the portfolio does the compounding.`);
    }
    if (!(st.runLots[sid] > 0)) errs.push(`${sid}: the run's lots could not be read from its config.`);
  }
  return errs;
}

// Mirrors backend fy_tax_schedule (carry-forward; settled once a session
// after the FY end exists in the run, i.e. dateTo > FY end; the last open FY
// is accrued).
export function fyTaxSchedule(items, ratePct, m, dateTo) {
  const rate = posNum(ratePct) / 100;
  if (!(rate > 0)) return { on: false, fys: [], paid: 0, accrued: 0 };
  const buckets = new Map();
  for (const it of items) {
    const k = fyStartOf(it.exitDay, m);
    buckets.set(k, (buckets.get(k) || 0) + it.scaledNet);
  }
  if (!buckets.size) return { on: true, fys: [], paid: 0, accrued: 0 };
  const keys = [...buckets.keys()].sort();
  const fys = [];
  let carry = 0, paid = 0, accrued = 0;
  for (let s = keys[0]; s <= keys[keys.length - 1]; s = fyNext(s)) {
    const net = buckets.get(s) || 0;
    const taxable = net + carry;
    const tax = taxable > 0 ? rate * taxable : 0;
    const carryOut = taxable > 0 ? 0 : taxable;
    const end = dayBefore(fyNext(s));
    const settled = dateTo ? String(dateTo).slice(0, 10) > end : s !== keys[keys.length - 1];
    fys.push({ fy: fyLabel(s), start: s, end, net, carry_in: carry, taxable, tax, carry_out: carryOut, settled });
    if (settled) paid += tax; else accrued += tax;
    carry = carryOut;
  }
  return { on: true, fys, paid, accrued };
}

// entries: [{run, trades}] (fixed-lot runs, same date range). Returns scaled
// entries for composePortfolio + per-run tax plans + the account summary.
export function applySharedEquity(entries, raw, period = {}) {
  const st = sharedSettingsOf(entries, raw);
  const W = entries.reduce((a, { run }) => a + st.lots[run.strategy_id], 0);
  const sCap = st.maxTotal > 0 && W > 0 ? st.maxTotal / W : Infinity;
  const limit = st.ddPct / 100, rate = st.taxPct / 100, m = st.fyMonth;

  // every closed trade, tagged with its strategy and its entry / exit day
  const items = [];
  entries.forEach(({ run, trades }, si) => {
    for (const t of trades || []) {
      if (!t || !t.exit_ts || t.exit_price == null) continue;
      const exitDay = istDay(t.exit_ts);
      const entryDay = t.entry_ts ? istDay(t.entry_ts) : exitDay;
      items.push({ t, si, entryDay: entryDay <= exitDay ? entryDay : exitDay, exitDay, exitTs: Number(t.exit_ts), net: netOf(t), lots: 0, scaledNet: 0 });
    }
  });
  const byEntry = [...items].sort((a, b) => (a.entryDay < b.entryDay ? -1 : a.entryDay > b.entryDay ? 1 : 0));
  const byExit = [...items].sort((a, b) => a.exitTs - b.exitTs);
  const sids = entries.map(({ run }) => run.strategy_id);

  let realised = 0, taxPaid = 0, carry = 0, curFY = null, peak = st.capital, factor = 1, guardDays = 0, xp = 0;
  const fyNet = new Map();
  const withdrawals = [];                 // {day, tax, fy}
  const ladder = sids.map(() => []);      // [{from, lots}] at each change
  let ei = 0;
  while (ei < byEntry.length) {
    const d = byEntry[ei].entryDay;
    // realise everything that closed before today
    while (xp < byExit.length && byExit[xp].exitDay < d) {
      const it = byExit[xp++];
      realised += it.scaledNet;
      const k = fyStartOf(it.exitDay, m);
      fyNet.set(k, (fyNet.get(k) || 0) + it.scaledNet);
    }
    // settle any FY that ended before today
    if (rate > 0) {
      const fy = fyStartOf(d, m);
      if (curFY == null) curFY = fy;
      while (curFY < fy) {
        const taxable = (fyNet.get(curFY) || 0) + carry;
        const tax = taxable > 0 ? rate * taxable : 0;
        carry = taxable > 0 ? 0 : taxable;
        if (tax > 0) { taxPaid += tax; peak -= tax; withdrawals.push({ day: d, tax, fy: fyLabel(curFY) }); }
        curFY = fyNext(curFY);
      }
    }
    const equity = st.capital + realised - taxPaid;
    if (limit > 0) {
      if (equity > peak) peak = equity;
      const dd = peak > 0 ? (peak - equity) / peak : 0;
      factor = Math.max(0, 1 - dd / limit);
      if (factor < 1) guardDays += 1;
    }
    const s = Math.min(Math.max(0, equity / st.capital), sCap);
    const lotsToday = sids.map((sid, i) => {
      const w = st.lots[sid];
      let n = Math.max(st.floorStart ? w : 1, Math.floor(w * s + 1e-9));   // ── PF_SHARED_EQUITY_FIX1_20261024 ── optional floor at start lots
      if (st.maxLots[sid] > 0) n = Math.min(n, st.maxLots[sid]);
      if (limit > 0) n = Math.max(Math.min(n, w), Math.round(n * factor));
      const lad = ladder[i];
      if (!lad.length || lad[lad.length - 1].lots !== n) lad.push({ from: d, lots: n });
      return n;
    });
    // size every trade that enters today
    while (ei < byEntry.length && byEntry[ei].entryDay === d) {
      const it = byEntry[ei++];
      const sid = sids[it.si];
      it.lots = lotsToday[it.si];
      it.scaledNet = it.net * (it.lots / st.runLots[sid]);
    }
  }

  // FY schedule over the scaled trades (identical FY nets to the loop above)
  const sched = rate > 0 ? fyTaxSchedule(items, st.taxPct, m, period.to) : { on: false, fys: [], paid: 0, accrued: 0 };
  const factorBy = new Map();
  for (const f of sched.fys) if (f.tax > 0 && f.net > f.tax) factorBy.set(f.fy, (f.net - f.tax) / f.net);
  const factorOf = (t) => factorBy.get(fyLabel(fyStartOf(istDay(t.exit_ts), m))) ?? 1;

  // scaled trade copies (qty too, so Exposure reads the sized position)
  const scaled = entries.map(() => []);
  const lotsUsed = entries.map(() => ({ min: null, max: null }));
  for (const it of items) {
    const sid = sids[it.si];
    const mult = it.lots / st.runLots[sid];
    const q = Number(it.t.qty);
    scaled[it.si].push({ ...it.t, net_pnl: it.scaledNet, pnl: fin(Number(it.t.pnl)) * mult,
      charges: fin(Number(it.t.charges)) * mult, qty: q > 0 ? Math.round(q * mult) : it.t.qty, pf_lots: it.lots });
    const u = lotsUsed[it.si];
    u.min = u.min == null ? it.lots : Math.min(u.min, it.lots);
    u.max = u.max == null ? it.lots : Math.max(u.max, it.lots);
  }

  // per-run tax plans in taxPlanOf's shape (composePortfolio's opts.planOf)
  const plans = new Map();
  if (sched.on && (sched.paid + sched.accrued) > 0) {
    entries.forEach(({ run }, si) => {
      const tax = scaled[si].reduce((a, t) => a + t.net_pnl * (1 - factorOf(t)), 0);
      plans.set(run.run_id, { m, factor: factorBy, tax, unplaced: 0, ratePct: st.taxPct,
                              source: "portfolio FY schedule", factorOf });
    });
  }

  // account equity by day (realised − withdrawals), for % drawdown and CAGR
  const totalNet = items.reduce((a, it) => a + it.scaledNet, 0);
  const evs = items.map((it) => ({ day: it.exitDay, v: it.scaledNet, w: 0 }));
  for (const w of withdrawals) evs.push({ day: w.day, v: -w.tax, w: w.tax });
  const paidInLoop = withdrawals.reduce((a, w) => a + w.tax, 0);
  for (const f of sched.fys) {                        // settled after the last sized day
    if (f.settled && f.tax > 0 && !withdrawals.some((w) => w.fy === f.fy)) evs.push({ day: fyNext(f.start), v: -f.tax, w: f.tax });
  }
  evs.sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
  let eq = st.capital, pk = st.capital, maxDDPct = 0, maxDDAbs = 0;
  const curve = [];
  for (const e of evs) {
    eq += e.v;
    if (e.w) pk -= e.w;                                // a withdrawal is not a drawdown
    if (eq > pk) pk = eq;
    if (pk > 0 && (pk - eq) / pk > maxDDPct) maxDDPct = (pk - eq) / pk;
    if (pk - eq > maxDDAbs) maxDDAbs = pk - eq;
    if (curve.length && curve[curve.length - 1].day === e.day) curve[curve.length - 1].v = eq;
    else curve.push({ day: e.day, v: eq });
  }
  const endEquity = st.capital + totalNet - sched.paid - sched.accrued;
  // ── PF_SHARED_EQUITY_FIX1_20261024 ── CAGR from the FIRST trade, not the run start (the corpus may
  // begin months before the first option trade; counting idle months understated CAGR)
  const cagrFrom = items.length ? items.reduce((m, it) => (it.entryDay < m ? it.entryDay : m), items[0].entryDay) : (period.from || null);
  const span = cagrFrom && period.to ? (Date.parse(period.to) - Date.parse(cagrFrom)) / 86400000 : 0;
  const cagrPct = span > 30 && endEquity > 0 ? (Math.pow(endEquity / st.capital, 365.25 / span) - 1) * 100 : null;

  return {
    entries: entries.map(({ run }, si) => ({ run, trades: scaled[si] })),
    plans,
    settings: st,
    perStrat: entries.map(({ run }, si) => ({
      sid: run.strategy_id, startLots: st.lots[run.strategy_id], runLots: st.runLots[run.strategy_id],
      maxLots: st.maxLots[run.strategy_id] || null, lotsMin: lotsUsed[si].min, lotsMax: lotsUsed[si].max, ladder: ladder[si],
    })),
    summary: {
      capital: st.capital, totalLots: W, capitalPerLot: W > 0 ? st.capital / W : null,
      maxTotal: st.maxTotal || null, ddPct: st.ddPct || null, taxPct: st.taxPct || null,
      grossNet: totalNet, taxPaid: sched.paid, taxAccrued: sched.accrued, taxPaidInLoop: paidInLoop, fys: sched.fys,
      endEquity, cagrPct, maxDDPct: maxDDPct * 100, maxDDAbs, guardDays, curve,
      cagrFrom, floorStart: st.floorStart,   // ── PF_SHARED_EQUITY_FIX1_20261024 ──
    },
  };
}

// PDF payload blocks (shared reports: results and sizing only, never params).
export function sharedSizingBlockOf(sh, i) {
  const p = sh.perStrat[i] || {};
  const s = sh.summary;
  return {
    base_lots: p.startLots ?? null, mode: "portfolio", max_lots: p.maxLots || null,
    capital_per_lot: s.capitalPerLot, dd_limit_pct: s.ddPct || null, dd_mode: s.ddPct ? "proportional" : null,
    dd_release_pct: null, lots_min: p.lotsMin, lots_max: p.lotsMax, tax_pct: s.taxPct || null,
    step_months: null, add_lots: null,
  };
}
export function sharedAccountBlockOf(sh) {
  const s = sh.summary;
  const f = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  return {
    start_capital: f(s.capital), total_lots: s.totalLots, capital_per_lot: f(s.capitalPerLot),
    end_equity: f(s.endEquity), cagr_pct: f(s.cagrPct), max_dd_pct: f(s.maxDDPct),
    tax_paid: f(s.taxPaid), tax_accrued: f(s.taxAccrued), max_total: s.maxTotal, dd_pct: s.ddPct, tax_pct: s.taxPct,
    guard_days: s.guardDays,
    cagr_from: s.cagrFrom || null, floor_start: !!s.floorStart,   // ── PF_SHARED_EQUITY_FIX1_20261024 ──
  };
}
