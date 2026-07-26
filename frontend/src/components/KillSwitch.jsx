/**
 * KILL SWITCH — per-strategy emergency stop (2026-07-26)
 *
 * Intended path: src/components/KillSwitch.jsx
 *
 * Mounted ONCE by StrategyHost above the focused strategy panel — no
 * per-panel edits anywhere (BB panel untouched). Self-contained:
 *
 *   - Polls /api/kill/eligibility (10s). Renders NOTHING unless the
 *     strategy is killable: mode LIVE, or (IC) a LIVE group riding under a
 *     non-LIVE config. So the button simply never exists for paper-only
 *     strategies — no dead controls.
 *   - TWO-TAP arm/confirm with a 5s disarm timer + inline banners
 *     (window.confirm is silently blocked in Tauri's webview).
 *   - Fires POST /api/kill/{sid}; renders the report: closed count, and on
 *     an incomplete kill the per-item detail (stuck GTT ids, still-open
 *     rows) with "mode NOT flipped — retry" messaging. The backend never
 *     flips to PAPER unless verifiably flat, so a red result here means
 *     real exposure remains.
 *   - On success the next eligibility poll hides the bar (mode is PAPER).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getKillEligibility, killStrategy } from "../api";
import { colors } from "../tokens";

const C = {
  red:   colors.danger  ?? "#ef4444",
  green: colors.success ?? "#10b981",
  amber: colors.warning ?? "#f59e0b",
  text:  colors.text?.primary ?? "#f9fafb",
  muted: colors.text?.muted   ?? "#6b7280",
  surf:  colors.bg?.tertiary  ?? "#1f2937",
};

const POLL_MS  = 10000;
const DISARM_MS = 5000;

export default function KillSwitch({ strategyId }) {
  const [elig, setElig] = useState(null);       // this strategy's slice
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);   // last kill report
  const disarmT = useRef(null);

  // useCallback keyed on strategyId + honest dep array below: no lint
  // suppression needed (some ESLint setups error on unknown rule names in
  // disable comments), and the stale-closure rule is satisfied properly.
  const poll = useCallback(async () => {
    const all = await getKillEligibility();
    setElig(all?.[strategyId] ?? null);
  }, [strategyId]);

  useEffect(() => {
    setElig(null); setArmed(false); setReport(null);
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => { clearInterval(t); if (disarmT.current) clearTimeout(disarmT.current); };
  }, [poll]);

  const onTap = async () => {
    if (busy) return;
    if (!armed) {
      setArmed(true);
      disarmT.current = setTimeout(() => setArmed(false), DISARM_MS);
      return;
    }
    if (disarmT.current) clearTimeout(disarmT.current);
    setArmed(false);
    setBusy(true);
    setReport(null);
    try {
      const res = await killStrategy(strategyId);
      setReport(res ?? { ok: false, error: "NO_RESPONSE" });
    } catch (e) {
      setReport({ ok: false, error: String(e) });
    }
    setBusy(false);
    poll();   // success → mode PAPER → bar hides on next render
  };

  const showBar = !!elig?.eligible || busy || (report && !report.ok);
  if (!showBar) {
    // keep a dismissible success note briefly visible after a clean kill
    if (report?.ok) {
      return (
        <div style={{
          marginBottom: 8, fontSize: 11, padding: "6px 10px", borderRadius: 6,
          color: C.green, background: `${C.green}14`, border: `1px solid ${C.green}44`,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <span>
            <strong>{strategyId} KILL complete.</strong>{" "}
            {report.closed} position(s)/engine(s) closed · flat verified ·
            mode → {report.mode_flipped ? "PAPER" : "UNCHANGED (flip failed — set PAPER in Settings)"}
          </span>
          <button onClick={() => setReport(null)} style={{
            marginLeft: "auto", fontSize: 10, cursor: "pointer",
            background: "transparent", border: "none", color: C.muted,
          }}>dismiss</button>
        </div>
      );
    }
    return null;
  }

  return (
    <div style={{ marginBottom: 8, display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "6px 10px", borderRadius: 8,
        border: `1px solid ${C.red}55`, background: `${C.red}0d`,
      }}>
        <span style={{ fontSize: 11, color: C.muted }}>
          <strong style={{ color: C.red }}>LIVE</strong>
          {elig?.reason && elig.reason !== "LIVE mode" ? ` · ${elig.reason}` : ""}
        </span>
        <button
          onClick={onTap}
          disabled={busy || !!elig?.in_flight}
          style={{
            marginLeft: "auto", fontSize: 11, fontWeight: 800,
            letterSpacing: "0.5px", padding: "6px 14px", borderRadius: 6,
            cursor: busy ? "wait" : "pointer",
            border: `1px solid ${C.red}`,
            background: armed ? C.red : `${C.red}22`,
            color: armed ? "#fff" : C.red,
            transition: "all 0.15s ease",
          }}
        >
          {busy || elig?.in_flight ? "KILLING…"
            : armed ? "CONFIRM KILL — close all & go PAPER"
            : "KILL"}
        </button>
      </div>

      {report && !report.ok && (
        <div style={{
          fontSize: 11, padding: "8px 10px", borderRadius: 6, lineHeight: 1.6,
          color: C.red, background: `${C.red}14`, border: `1px solid ${C.red}55`,
        }}>
          <strong>
            {report.error === "IN_FLIGHT" ? "A kill is already running."
              : report.error === "NOT_LIVE" ? `Not killable — mode is ${report.mode}.`
              : `KILL INCOMPLETE — mode NOT flipped.`}
          </strong>
          {report.remaining > 0 && (
            <div>{report.remaining} position(s) still open — check Kite, then retry.</div>
          )}
          {report.remaining < 0 && (
            <div>Open-position count could not be verified — check Kite before anything else.</div>
          )}
          {(report.detail || []).map((d, i) => (
            <div key={i} style={{ color: C.amber }}>• {d}</div>
          ))}
          {report.error && !["IN_FLIGHT", "NOT_LIVE"].includes(report.error) && (
            <div style={{ color: C.muted }}>{String(report.error)}</div>
          )}
        </div>
      )}
    </div>
  );
}