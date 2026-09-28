// frontend/src/components/fleet/fleetFormat.js — ── DASH_MODERN_20260925 ──
// Number/time formatting shared by the Modern dashboard pieces.

export const fmtInr = (v) => {
  const n = Math.round(Number(v) || 0);
  const s = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${s}₹${Math.abs(n).toLocaleString("en-IN")}`;
};

export const fmtK = (v) => {
  const n = Number(v) || 0;
  const a = Math.abs(n);
  const s = n > 0 ? "+" : n < 0 ? "−" : "";
  return a >= 10000 ? `${s}₹${(a / 1000).toFixed(1)}k` : fmtInr(n);
};

export const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

/* The trading machine runs IST (marketSession.js doctrine), so the local
   clock IS the session clock. */
export const nowMinIst = () => {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
};

export const bookLabel = (b) => (b === "LIVE" ? "Live" : "Paper");

/* Latest sample at or before `minute` in a [[minute, mtm], …] path. */
export const valueAt = (path, minute) => {
  if (!Array.isArray(path) || path.length === 0) return null;
  let v = null;
  for (const p of path) {
    if (p[0] > minute) break;
    v = p[1];
  }
  return v;
};
