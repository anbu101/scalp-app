// frontend/src/strategies/vet/VETPanel.jsx
//
// ── VET_V1 dashboard panel ── (TMA2Panel conventions, 2026-08-29)
// Dual-EMA(10/20) + regime channel on 5m NIFTY spot; ONE position at a time,
// BUY or SELL by config, intraday or positional by config. There is no GTT
// layer (sl/tp are 0 by design), so the panel's job is health + position +
// today's closed groups. Engine-health strip surfaces the PREFIX-GUARD
// frozen flag and the warmup depth — the two "why is it not trading?"
// answers — because silent failure is the enemy.
// "Today" numbers are EXIT-timestamp based; the open card ignores entry day
// entirely (positional carries must show).

import { useEffect, useState } from "react";
import { getApiBase } from "../../api/base";
import { colors, spacing, pnlStyle } from "../../tokens";
import { stratName } from "../displayNames";                      // ── UI_MASK ──

const ACCENT = "#34d399";

function fmtInr(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(Math.round(v));
  return `${v < 0 ? "−" : ""}₹${a.toLocaleString("en-IN")}`;
}
function tsFmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const hm = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? hm
    : `${d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} · ${hm}`;
}

export default function VETPanel({ strategyId = "VET_V1" }) {
  const [status, setStatus] = useState(null);
  const [trades, setTrades] = useState([]);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const base = getApiBase();
        const [s, t] = await Promise.all([
          fetch(`${base}/api/vet/status`).then((r) => r.json()),
          fetch(`${base}/api/vet/trades?limit=60`).then((r) => r.json()),
        ]);
        if (!alive) return;
        setStatus(s);
        setTrades(t?.trades || []);
      } catch { /* next poll */ }
    }
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const mgr = status?.manager;
  const eng = status?.signal_engine;
  const pos = mgr?.position;
  const midnight = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return Math.floor(d.getTime() / 1000); })();
  const groups = {};
  for (const r of trades) {
    if (r.status !== "CLOSED" || !r.exit_ts || r.exit_ts < midnight) continue;
    (groups[r.group_id] = groups[r.group_id] || []).push(r);
  }
  const todayGroups = Object.values(groups)
    .map((legs) => ({
      main: legs.find((l) => l.leg_role === "MAIN") || legs[0],
      net: legs.reduce((a, l) => a + (l.net_pnl ?? l.pnl ?? 0), 0),
    }))
    .sort((a, b) => (b.main.exit_ts || 0) - (a.main.exit_ts || 0));
  const todayNet = todayGroups.reduce((a, g) => a + g.net, 0);
  const frozen = eng?.frozen || mgr?.frozen;
  const warmShort = eng && eng.warmup_ok === false;

  const card = { background: colors.bgAlt, borderRadius: 10, padding: spacing.md,
                 border: `1px solid ${colors.border}`, marginBottom: spacing.sm };
  const dim = { fontSize: 12, opacity: 0.65 };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, marginBottom: spacing.sm }}>
        <div style={{ width: 8, height: 8, borderRadius: 4, background: ACCENT }} />
        <div style={{ fontWeight: 700 }}>{stratName(strategyId)}</div>
        <div style={dim}>
          {mgr ? `${mgr.mode} · ${mgr.leg_action} · ${mgr.eod_square ? "INTRADAY" : "POSITIONAL"}${mgr.hedged ? " · wing" : ""}` : "loop not running"}
        </div>
      </div>

      {(frozen || warmShort) && (
        <div style={{ ...card, borderColor: "#ef4444aa", background: "#7f1d1d22" }}>
          <b>{frozen ? "ENGINE FROZEN" : "WARMUP SHORT"}</b>
          <div style={dim}>
            {frozen
              ? (eng?.freeze_reason || mgr?.freeze_reason || "prefix guard tripped — not trading (fail closed)")
              : `${eng?.warmup_sessions}/${eng?.warmup_required} sessions — decisions blocked until warm`}
          </div>
        </div>
      )}

      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Open position</div>
        {pos ? (
          <div>
            <div>
              {pos.direction === "SHORT" ? "SHORT " : "LONG "}<b>{pos.symbol}</b>
              {" "}@ {pos.entry_price}
              {pos.wing ? <span style={dim}>{"  + wing "}{pos.wing} @ {pos.wing_entry}</span> : null}
            </div>
            <div style={dim}>exits: FLIP / SIGNAL / {mgr?.eod_square ? "EOD 15:15" : "expiry day"} — no SL/TP by design</div>
          </div>
        ) : (
          <div style={dim}>flat{eng?.last_bar_ts ? ` · last 5m bar ${tsFmt(eng.last_bar_ts)}` : ""}</div>
        )}
      </div>

      <div style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontWeight: 600 }}>Today (closed)</div>
          <div style={pnlStyle ? pnlStyle(todayNet) : { color: todayNet >= 0 ? "#34d399" : "#f87171" }}>{fmtInr(todayNet)}</div>
        </div>
        {todayGroups.length === 0 ? (
          <div style={dim}>no closed positions yet</div>
        ) : todayGroups.map((g) => (
          <div key={g.main.group_id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "3px 0" }}>
            <div>
              {g.main.direction === "SHORT" ? "S " : "L "}{g.main.tradingsymbol}
              <span style={dim}>{" "}{g.main.exit_reason} · {tsFmt(g.main.exit_ts)}</span>
            </div>
            <div style={{ color: g.net >= 0 ? "#34d399" : "#f87171" }}>{fmtInr(g.net)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}