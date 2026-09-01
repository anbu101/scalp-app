// frontend/src/components/AccountSelector.jsx
// ============================================================
// ACC2 BEGIN — Per-strategy execution-account selector (D2c)
//
// Mounted inside each strategy's detail pane (LotsOnlySettings
// StrategyDetail and admin Settings) with a one-line patch:
//     <AccountSelector strategyId={id} />
//
// Behaviour:
//   - GET /api/acc2/bindings on mount; dropdown Account 1 (Zerodha)
//     / Account 2 (Angel One).
//   - Save posts the FULL merged bindings map. If the server replies
//     needs_confirm (D8 buy+sell on one account), the button enters
//     the two-tap arm/confirm pattern (NO window.confirm — silently
//     blocked in the Tauri webview).
//   - Account 2 option is disabled (with hint) when /api/acc2/status
//     says not configured — binding to a dead account would only
//     produce PAPER degrades the user didn't expect.
// ============================================================

import { useEffect, useState } from "react";
import { getApiBase } from "../api/base";
import { colors } from "../tokens";   // ── THEME_PHASE2B_20260831 ──

const rowStyle = {
  display: "flex", alignItems: "center", gap: 10,
  marginTop: 14, flexWrap: "wrap",
};

// ── THEME_PHASE2B_20260831 ── theme-aware (was a fixed near-black select that stayed
// black under the light theme).
const selectStyle = {
  padding: "6px 10px", borderRadius: 6, fontSize: 13,
  background: colors.bg.input, color: colors.text.primary,
  border: `1px solid ${colors.border.light}`,
};

const saveStyle = (armed) => ({
  padding: "6px 12px", borderRadius: 6, border: "none",
  cursor: "pointer", fontSize: 12, fontWeight: 700, color: "#fff",
  background: armed ? colors.danger : colors.primary,
});

export default function AccountSelector({ strategyId }) {
  const [bindings, setBindings] = useState({});
  const [value, setValue] = useState("ZERODHA");
  const [saved, setSaved] = useState("ZERODHA");
  const [acc2Ready, setAcc2Ready] = useState(false);
  const [bindable, setBindable] = useState(null);   // ── ACC2_W3 ──
  const [armed, setArmed] = useState(false);       // two-tap state
  const [conflicts, setConflicts] = useState([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const b = await fetch(`${getApiBase()}/api/acc2/bindings`)
          .then((r) => r.json());
        setBindings(b.bindings || {});
        setBindable(Array.isArray(b.bindable) ? b.bindable : null); // ── ACC2_W3 ──
        const cur = (b.bindings || {})[strategyId] || "ZERODHA";
        setValue(cur); setSaved(cur);
        const s = await fetch(`${getApiBase()}/api/acc2/status`)
          .then((r) => r.json());
        setAcc2Ready(!!s.configured);
      } catch { /* backend not up */ }
    })();
  }, [strategyId]);

  async function post(confirm) {
    setBusy(true); setMsg("");
    try {
      const merged = { ...bindings, [strategyId]: value };
      const j = await fetch(`${getApiBase()}/api/acc2/bindings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bindings: merged, confirm }),
      }).then((r) => r.json());

      if (j.needs_confirm) {
        // D8 layer 1: arm the confirm tap, show the warning inline.
        setConflicts(j.conflicts || []);
        setArmed(true);
        setMsg(
          `Warning: ${(j.conflicts || []).join(", ")} would run BOTH ` +
          `buying and selling strategies. Positions on the same strike ` +
          `NET at the broker and exit protection can misfire. ` +
          `Tap CONFIRM to proceed anyway.`);
        return;
      }
      setBindings(j.bindings || merged);
      setSaved(value);
      setArmed(false);
      setConflicts(j.conflicts || []);
      setMsg("Saved.");
    } catch (e) {
      setMsg(`Save failed: ${e}`);
      setArmed(false);
    } finally { setBusy(false); }
  }

  const dirty = value !== saved;

  // ── ACC2_W3 ── strategies whose full execution path isn't wired to the
  // binding yet stay on Account 1; showing an editable dropdown here would
  // promise routing the exits can't honor.
  const isBindable = bindable === null || bindable.includes(strategyId);
  if (!isBindable) {
    return (
      <div style={rowStyle}>
        <span style={{ fontSize: 12, opacity: 0.75 }}>Execution Account</span>
        <span style={{ fontSize: 12, opacity: 0.6 }}>
          Account 1 · Zerodha <span style={{ fontSize: 10 }}>(Account 2 support coming for this strategy)</span>
        </span>
      </div>
    );
  }

  return (
    <div style={rowStyle}>
      <span style={{ fontSize: 12, opacity: 0.75 }}>Execution Account</span>
      <select
        style={selectStyle}
        value={value}
        disabled={busy}
        onChange={(e) => { setValue(e.target.value); setArmed(false); setMsg(""); }}
      >
        <option value="ZERODHA">Account 1 · Zerodha</option>
        <option value="ANGELONE" disabled={!acc2Ready}>
          {acc2Ready
            ? "Account 2 · Angel One"
            : "Account 2 · Angel One (configure in Connections)"}
        </option>
      </select>
      {dirty && (
        <button style={saveStyle(armed)} disabled={busy}
          onClick={() => post(armed)}>
          {busy ? "…" : armed ? "CONFIRM" : "Save"}
        </button>
      )}
      {msg && (
        <span style={{
          fontSize: 11, maxWidth: 420,
          color: armed ? "#f59e0b" : /Saved/.test(msg) ? "#22c55e" : "#f59e0b",
        }}>
          {msg}
        </span>
      )}
      {!dirty && conflicts.length > 0 && (
        <span style={{ fontSize: 11, color: "#f59e0b" }}>
          ⚠ buy+sell mix on {conflicts.join(", ")} (accepted)
        </span>
      )}
    </div>
  );
}
// ACC2 END