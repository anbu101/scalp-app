// frontend/src/strategies/orb/ORBPanel.jsx
// ── ORB_V1 PANEL ── Fence: ORB_LIVE3_20260903. Read-only day view +
// two-tap square-off (window.confirm is DEAD in Tauri — checklist 3.1).
import React, { useEffect, useState, useCallback } from "react";
import { getORBV1State, orbV1SquareOff } from "../../api";

export default function ORBPanel(props) {
  const [st, setSt] = useState(null);
  const [armKill, setArmKill] = useState(false);
  const refresh = useCallback(async () => {
    try { setSt(await getORBV1State()); } catch { /* keep last */ }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);
  const pos = st?.position;
  const lv = st?.levels;
  const chip = (txt, bg) => (
    <span style={{ background: bg, color: "#fff", borderRadius: 4,
                   padding: "1px 8px", fontSize: 11, marginRight: 6 }}>{txt}</span>);
  return (
    <div style={{ padding: 10, fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
        <b style={{ marginRight: 8 }}>ORB V1 — Outrider</b>
        {chip(st?.mode || "…", st?.mode === "LIVE" ? "#c62828" : st?.mode === "PAPER" ? "#1565c0" : "#607d8b")}
        {st?.frozen && chip("FROZEN", "#c62828")}
        {st?.day?.refused && chip("DAY REFUSED", "#795548")}
      </div>
      <div style={{ opacity: 0.85, marginBottom: 6 }}>
        {lv ? <>ORB {lv.low?.toFixed?.(1)} – {lv.high?.toFixed?.(1)}</> : "ORB window forming…"}
        {"  ·  "}signals {st?.day?.signals ?? 0} · entries {st?.day?.entries ?? 0}
        {" · exits "}{Object.entries(st?.day?.exits || {}).map(([k, v]) => `${k}:${v}`).join(" ") || "—"}
      </div>
      {pos ? (
        <div style={{ border: "1px solid #455a64", borderRadius: 6, padding: 8, marginBottom: 8 }}>
          <div><b>{pos.side}</b> {pos.symbol} × {pos.qty} @ ₹{pos.entry_price}</div>
          <div style={{ opacity: 0.8 }}>spot stop {pos.sl_spot?.toFixed?.(2)} (1m close) · TP ₹{pos.tp_prem?.toFixed?.(2)}</div>
        </div>
      ) : <div style={{ opacity: 0.6, marginBottom: 8 }}>flat</div>}
      {!armKill ? (
        <button onClick={() => setArmKill(true)} disabled={!pos}
                style={{ fontSize: 11 }}>Square off…</button>
      ) : (
        <span>
          <button onClick={async () => { setArmKill(false); try { await orbV1SquareOff(); } catch {} refresh(); }}
                  style={{ fontSize: 11, background: "#c62828", color: "#fff", marginRight: 6 }}>CONFIRM square-off</button>
          <button onClick={() => setArmKill(false)} style={{ fontSize: 11 }}>cancel</button>
        </span>
      )}
      <div style={{ marginTop: 8, fontSize: 10.5, opacity: 0.65 }}>
        Sealed 2026-09-03 · entries ≤12:00 · everything closed 13:00 · docs/ORB_V1_BIBLE.pdf
      </div>
    </div>
  );
}
