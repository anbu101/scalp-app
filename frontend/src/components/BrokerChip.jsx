// frontend/src/components/BrokerChip.jsx
// ============================================================
// ACC2_W3 BEGIN — Execution-account chip for strategy panel headers.
//
// Shows which broker account this strategy's orders route through:
//   [Z Zerodha]  blue   ·  [A Angel One]  amber
//
// Behaviour:
//   - Renders NOTHING until Account 2 is configured — single-account
//     users see pixel-identical panels.
//   - One shared fetch across ALL chip instances (module-level cache,
//     30s TTL): bindings + acc2 status ride the same two requests no
//     matter how many panels mount.
//   - Resolution mirrors the server: binding if present AND the id is
//     bindable, else ZERODHA — so the chip can never disagree with
//     where orders actually go.
//
// Mount (one line, next to the panel's ModeBadge):
//   <BrokerChip strategyId="SCALP_V3" />
// ============================================================

import { useEffect, useState } from "react";
import { getApiBase } from "../api/base";

let _cache = null;          // { at, configured, bindings, bindable }
let _inflight = null;
const TTL_MS = 30_000;

async function loadAccountInfo() {
  const now = Date.now();
  if (_cache && now - _cache.at < TTL_MS) return _cache;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    try {
      const base = getApiBase();
      const [st, bd] = await Promise.all([
        fetch(`${base}/api/acc2/status`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${base}/api/acc2/bindings`).then((r) => (r.ok ? r.json() : null)),
      ]);
      _cache = {
        at: Date.now(),
        configured: st?.configured === true,
        bindings: bd?.bindings || {},
        bindable: Array.isArray(bd?.bindable) ? bd.bindable : [],
      };
    } catch {
      _cache = { at: Date.now(), configured: false, bindings: {}, bindable: [] };
    } finally {
      _inflight = null;
    }
    return _cache;
  })();
  return _inflight;
}

const STYLES = {
  ZERODHA: { tag: "Z", label: "Zerodha", color: "#3b82f6" },
  ANGELONE: { tag: "A", label: "Angel One", color: "#f59e0b" },
};

export default function BrokerChip({ strategyId }) {
  const [info, setInfo] = useState(_cache);

  useEffect(() => {
    let alive = true;
    loadAccountInfo().then((i) => alive && setInfo(i));
    return () => { alive = false; };
  }, [strategyId]);

  if (!info || !info.configured) return null;   // single-account: invisible

  const bound = info.bindings[strategyId];
  const broker = (bound && info.bindable.includes(strategyId))
    ? bound : "ZERODHA";
  const s = STYLES[broker] || STYLES.ZERODHA;

  return (
    <span
      title={`Orders for this strategy route through ${s.label} (Account ${broker === "ANGELONE" ? 2 : 1})`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        fontSize: 11, fontWeight: 700, color: s.color,
        border: `1px solid ${s.color}55`, background: `${s.color}14`,
        borderRadius: 5, padding: "1px 7px", flexShrink: 0,
        letterSpacing: "0.3px",
      }}
    >
      <span style={{
        fontSize: 9, fontWeight: 800, borderRadius: 3, padding: "0 4px",
        background: `${s.color}26`,
      }}>
        {s.tag}
      </span>
      {s.label}
    </span>
  );
}
// ACC2_W3 END