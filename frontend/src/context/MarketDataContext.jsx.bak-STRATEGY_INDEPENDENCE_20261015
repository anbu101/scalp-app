/**
 * MarketDataProvider — src/context/MarketDataContext.jsx
 *
 * Lifts the global market-data + P&L pipeline OUT of Dashboard.jsx so it can be
 * consumed app-wide (nav bar P&L pill, status bar breakdown, dashboard, etc.).
 *
 * Owns (moved verbatim from the old Dashboard):
 *   - ltpMap            (500ms live poll)   → also passed to StrategyHost
 *   - indices           (500ms live poll)   → NIFTY / BANKNIFTY badges
 *   - positions struct  (15s poll)          → open/closed/realised
 *   - DERIVED positions with live P&L recomputed every 500ms from ltpMap
 *
 * P&L architecture (unchanged from the proven Dashboard logic):
 *   - positions STRUCTURE (symbol, avg price, qty) fetched every 15s
 *   - unrealised P&L computed LIVE from ltpMap (re-runs every 500ms)
 *   - realised P&L from closed positions (API value, no LTP needed)
 *
 * Consumers use the useMarketData() hook:
 *   const { ltpMap, indices, positions, positionsLoading } = useMarketData();
 *   positions = { open:[{...,pnl}], closed:[...], totals:{realised,unrealised,total} }
 */

import { createContext, useContext, useEffect, useRef, useState, useMemo } from "react";
import { getTodayPositions } from "../api";
import { getApiBase } from "../api/base";

const safeNum = (v) => (typeof v === "number" && !isNaN(v) ? v : 0);
const normalizeSymbol = (s) => (s || "").replace(/\s+/g, "").toUpperCase();

const MarketDataContext = createContext(null);

export function useMarketData() {
  const ctx = useContext(MarketDataContext);
  if (!ctx) {
    // Defensive default so a stray consumer never crashes the app.
    return {
      ltpMap: {}, indices: {},
      positions: { open: [], closed: [], totals: { realised: 0, unrealised: 0, total: 0 } },
      positionsLoading: true,
    };
  }
  return ctx;
}

export function MarketDataProvider({ children }) {
  const [ltpMap,  setLtpMap]  = useState({});
  const [indices, setIndices] = useState({});

  const [positionsData, setPositionsData] = useState({ open: [], closed: [], realisedPnl: 0 });
  const [positionsLoading, setPositionsLoading] = useState(true);
  const posFirstLoad = useRef(true);

  // ---- LTP poll: 500ms ----
  useEffect(() => {
    let alive = true;
    async function pollLtp() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/ltp_snapshot`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") {
              const normalized = {};
              Object.entries(data).forEach(([symbol, price]) => {
                normalized[normalizeSymbol(symbol)] = price;
              });
              setLtpMap(normalized);
            }
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 500));
      }
    }
    pollLtp();
    return () => { alive = false; };
  }, []);

  // ---- Indices poll: 500ms ----
  useEffect(() => {
    let alive = true;
    async function pollIndices() {
      while (alive) {
        try {
          const res = await fetch(`${getApiBase()}/market_indices`);
          if (res.ok) {
            const data = await res.json();
            if (data && typeof data === "object") setIndices(data);
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 500));
      }
    }
    pollIndices();
    return () => { alive = false; };
  }, []);

  // ---- Positions structure poll: 15s ----
  useEffect(() => {
    async function loadPositions() {
      if (posFirstLoad.current) setPositionsLoading(true);
      try {
        const p      = await getTodayPositions();
        const open   = p?.open   || [];
        const closed = p?.closed || [];
        const realisedPnl = closed.reduce((s, x) => s + safeNum(x.pnl), 0);
        setPositionsData({ open, closed, realisedPnl });
      } catch {
        // keep previous on error — display stays live via ltpMap
      } finally {
        if (posFirstLoad.current) {
          setPositionsLoading(false);
          posFirstLoad.current = false;
        }
      }
    }
    loadPositions();
    const t = setInterval(loadPositions, 15000);
    return () => clearInterval(t);
  }, []);

  // ---- Derived positions with live P&L (re-runs every 500ms via ltpMap) ----
  const positions = useMemo(() => {
    const { open, closed, realisedPnl } = positionsData;
    let unrealised = 0;
    const liveOpen = open.map((p) => {
      const sym = normalizeSymbol(p.tradingsymbol);
      const ltp = ltpMap[sym];
      const livePnl = ltp != null
        ? (ltp - safeNum(p.average_price)) * safeNum(p.quantity)
        : safeNum(p.pnl);
      unrealised += livePnl;
      return { ...p, pnl: livePnl };
    });
    return {
      open: liveOpen,
      closed,
      totals: { realised: realisedPnl, unrealised, total: realisedPnl + unrealised },
    };
  }, [positionsData, ltpMap]);

  // ── SPOT_IN_LTPMAP BEGIN ── (2026-07-20 fix: "spot LTP unavailable")
  // The backend's tick handler routes index ticks to MarketIndicesState and
  // `continue`s BEFORE LTPStore.update — so /ltp_snapshot structurally NEVER
  // contains NIFTY/BANKNIFTY/SENSEX, and every panel spot lookup (e.g. the
  // PST spot-SL RiskBar) degraded to "levels only" forever. The indices DO
  // arrive here via the /market_indices poll — merge their LTPs into the
  // ltpMap handed to consumers. Panels already look up the "NIFTY" key
  // (PSTPanel SPOT_KEYS), so no panel changes are needed.
  const mergedLtpMap = useMemo(() => {
    const m = { ...ltpMap };
    for (const k of ["NIFTY", "BANKNIFTY", "SENSEX"]) {
      const v = indices?.[k]?.ltp;
      if (v != null && m[k] == null) m[k] = v;
    }
    return m;
  }, [ltpMap, indices]);
  // ── SPOT_IN_LTPMAP END ──

  const value = useMemo(
    () => ({ ltpMap: mergedLtpMap, indices, positions, positionsLoading }),
    [mergedLtpMap, indices, positions, positionsLoading]
  );

  return (
    <MarketDataContext.Provider value={value}>
      {children}
    </MarketDataContext.Provider>
  );
}