#!/usr/bin/env python3
# apply_orb_panel_v2.py — ORB_V1 dashboard brought to fleet grade
# (BRKPanel v2 / ScalpV3 parity, plus ORB-specific views).
#
# Fence: ORB_PANEL_V2_20260916   PREREQUISITE: ORB_RECONCILE_20260911 on the engine
# (verified — anchors on the post-reconcile engine; three small inserts,
# never a replacement).
#
# WHAT THIS DOES
#   EDIT engine (anchored, both trees): publish spot ("NIFTY50") and every
#        option quote to LTPStore (TSG_LTP_PUBLISH doctrine) so the panel
#        TICKS between polls; keep last spot / marks / heartbeat.
#   NEW  api/orb_state_routes.py v2 (replaces the 38-line stub, backed up):
#        phase, levels+range, spot, last 1m bar, position with mark,
#        budgets + drop counters, sealed cfg summary, heartbeat, today's
#        paper_trades rows.
#   NEW  ORBPanel.jsx v2 (backed up): phase/mode/heartbeat chips, guard and
#        refusal banners, ticking day P&L (closed + open MTM), scoreboard,
#        RANGE TRACK (live spot between ORB low/high, distance to each
#        trigger, per-side armed/used), position card with premium
#        entry->TP track + spot-side stop room + two-tap square-off,
#        today's rows table, sealed footer.
#
# Rebuild (frontend + backend) and restart to see it.
# USAGE: python3 apply_orb_panel_v2.py --check && python3 apply_orb_panel_v2.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_PANEL_V2_20260916'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {'backend/app/api/orb_state_routes.py': '# backend/app/api/orb_state_routes.py\n#\n# ── ORB_V1 STATE ROUTES · v2 ── Fence: ORB_PANEL_V2_20260916\n# Composes the dashboard payload from manager + core + engine (read-only,\n# isolated try/except everywhere; sane payload when the runtime never\n# launched). Prices TICK on the frontend via LTPStore (the engine\n# publishes every quote it takes — TSG_LTP_PUBLISH doctrine); this route\n# supplies STRUCTURE every 4 s.\n\nfrom __future__ import annotations\nfrom datetime import datetime, timedelta, timezone\nfrom fastapi import APIRouter\n\nrouter = APIRouter(prefix="/api/orb_v1", tags=["ORB_V1"])\nIST = timezone(timedelta(hours=5, minutes=30))\n\n_EMPTY = {"ok": True, "running": False, "strategy": "ORB_V1", "mode": "OFF",\n          "phase": "OFFLINE", "position": None, "levels": None, "spot": None,\n          "last_bar": None, "day": {}, "cfg": {}, "heartbeat_ts": None,\n          "trades": [], "frozen": False, "refused": None}\n\n\ndef _phase(mgr, day, now):\n    hm = now.hour * 60 + now.minute\n    if day is None:\n        return "REFUSED" if (mgr.day_stats or {}).get("refused") else "PRE_OPEN"\n    if day.guard.frozen:\n        return "FROZEN"\n    if day.refused:\n        return "REFUSED"\n    if mgr.pos is not None:\n        return "IN_POSITION"\n    if day.orb_high is None:\n        return "WINDOW" if hm >= 555 else "PRE_OPEN"\n    if hm >= day._eod_min:\n        return "DONE"\n    if hm >= day._block_min:\n        return "NO_NEW_ENTRIES"\n    return "ARMED"\n\n\n@router.get("/state")\ndef orb_state():\n    try:\n        from app.engine.orb.orb_runtime import get_orb_manager, get_orb_engine\n        mgr, eng = get_orb_manager(), get_orb_engine()\n        if mgr is None:\n            return dict(_EMPTY)\n        now = datetime.now(IST)\n        day = mgr.day\n        cfg = mgr.cfg() or {}\n        out = dict(_EMPTY)\n        out.update({\n            "running": True, "mode": mgr.mode(),\n            "phase": _phase(mgr, day, now),\n            "frozen": bool(day and day.guard.frozen),\n            "refused": (day.refused if day else None)\n                       or (mgr.day_stats or {}).get("refused"),\n            "heartbeat_ts": getattr(eng, "last_poll_ts", None),\n            "spot": ({"ltp": eng.last_spot, "ts": eng.last_spot_ts}\n                     if getattr(eng, "last_spot", None) else None),\n            "cfg": {\n                "target_value": cfg.get("target_value"),\n                "sl_pct": cfg.get("sl_points"),\n                "entry_block_time": cfg.get("entry_block_time"),\n                "eod_square_off": cfg.get("eod_square_off"),\n                "lots": cfg.get("lots"),\n                "premium_min": cfg.get("premium_min"),\n                "premium_max": cfg.get("premium_max"),\n                "max_trades_per_day": cfg.get("max_trades_per_day"),\n                "max_trades_per_side": cfg.get("max_trades_per_side"),\n            },\n            "day": dict(mgr.day_stats or {}),\n        })\n        if day is not None:\n            if day.orb_high is not None:\n                out["levels"] = {"high": day.orb_high, "low": day.orb_low,\n                                 "range": round(day.orb_high - day.orb_low, 2)}\n            if day.prefix:\n                b = day.prefix[-1]\n                out["last_bar"] = {"ts": b.ts, "close": b.close,\n                                   "bars": len(day.prefix)}\n            out["day"].update({\n                "day_trades": day.day_trades,\n                "side_trades": dict(day.side_trades),\n                "dropped_open": day.dropped_open,\n                "dropped_budget": day.dropped_budget,\n                "dropped_block": day.dropped_block,\n                "pending_side": day.pending_side,\n                "consumed_signals": day.consumed_sigs,\n            })\n        p = mgr.pos\n        if p is not None:\n            marks = getattr(eng, "last_marks", {}) or {}\n            out["position"] = {\n                "symbol": p.symbol, "side": p.side, "mode": p.mode,\n                "entry": p.entry_px, "qty": p.qty, "lots": p.lots,\n                "sl_spot": p.sl_spot, "tp": p.tp_prem,\n                "entry_ts": p.entry_ts, "mark": marks.get(p.symbol),\n                "row_id": p.row_id}\n        try:\n            from app.db.sqlite import get_conn\n            day0 = int(now.replace(hour=0, minute=0, second=0,\n                                   microsecond=0).timestamp())\n            rows = get_conn().execute(\n                "SELECT paper_trade_id, symbol, side, trade_mode, group_id,"\n                " entry_price, exit_price, exit_reason, qty, state,"\n                " entry_time, exit_time, sl_price, tp_price FROM paper_trades"\n                " WHERE strategy_name=\'ORB_V1\' AND candle_ts >= ?"\n                " ORDER BY entry_time DESC LIMIT 20", (day0,)).fetchall()\n            out["trades"] = [dict(r) for r in rows]\n        except Exception:\n            pass\n        return out\n    except Exception as e:\n        return dict(_EMPTY, ok=False, error=repr(e))\n\n\n@router.post("/square_off")\ndef orb_square_off():\n    try:\n        from app.engine.orb.orb_runtime import get_orb_manager\n        mgr = get_orb_manager()\n        if mgr is None:\n            return {"ok": False, "error": "runtime not up"}\n        return {"ok": True, "closed": mgr.kill_all()}\n    except Exception as e:\n        return {"ok": False, "error": repr(e)}\n', 'frontend/src/strategies/orb/ORBPanel.jsx': '// frontend/src/strategies/orb/ORBPanel.jsx\n//\n// ── ORB_V1 dashboard panel · v2 ── (fleet-grade rework, 2026-09-16)\n// v1 was a static stub. v2 is BRKPanel-parity where ORB has the concept,\n// plus the two things that make ORB legible at a glance:\n//   * RANGE TRACK — live NIFTY spot on a bar between ORB low and ORB high\n//     (both edges are the triggers), ticking via ltpMap between polls.\n//   * POSITION TRACKS — premium entry→TP (ticking LTP + MTM) and the\n//     spot-side stop distance (0.04% close-trigger), because ORB\'s two\n//     exits live on two different instruments.\n//   * Header day P&L = closed rows + open MTM, ticking. Phase chip,\n//     PREFIX-GUARD / REFUSED banners, budgets, drop counters, today\'s rows.\n// Structure from /api/orb_v1/state (4 s poll); prices at ltpMap cadence.\n// Square-off is TWO-TAP (window.confirm is swallowed by Tauri).\n\nimport { useEffect, useMemo, useRef, useState } from "react";\nimport { getApiBase } from "../../api/base";\nimport { colors, spacing, pnlStyle } from "../../tokens";\nimport { stratName } from "../displayNames";                      // ── UI_MASK ──\n\nconst ACCENT = "#f59e0b";\nconst SPOT_KEY = "NIFTY50";\n\nfunction normalizeSymbol(sym) { return sym ? sym.replace(/\\s+/g, "").toUpperCase() : sym; }\nfunction fmtInr(v) {\n  if (v == null || isNaN(v)) return "—";\n  const a = Math.abs(v);\n  const s = a >= 100 ? Math.round(a).toLocaleString("en-IN") : a.toFixed(2);\n  return `${v < 0 ? "−" : ""}₹${s}`;\n}\nfunction fmt2(v) { return v == null || isNaN(v) ? "—" : Number(v).toFixed(2); }\nfunction fmtPts(v) { return v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}`; }\nfunction hhmm(ts) {\n  if (!ts) return "—";\n  const d = new Date(ts * 1000);\n  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });\n}\nfunction ago(ts) {\n  if (!ts) return null;\n  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));\n  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m`;\n}\n\nconst PHASE = {\n  OFFLINE:        ["#1f2937", "#9ca3af", "OFFLINE"],\n  PRE_OPEN:       ["#1c2a4a", "#93c5fd", "PRE-OPEN"],\n  WINDOW:         ["#1c2a4a", "#93c5fd", "ORB WINDOW 09:15–09:30"],\n  ARMED:          ["#14351f", "#4ade80", "ARMED · both edges live"],\n  IN_POSITION:    ["#3b2a05", ACCENT,    "IN POSITION"],\n  NO_NEW_ENTRIES: ["#2a2a2a", "#d1d5db", "NO NEW ENTRIES ≥ 12:00"],\n  DONE:           ["#2a2a2a", "#d1d5db", "DAY DONE · flat since 13:00"],\n  REFUSED:        ["#3a1420", "#f87171", "DAY REFUSED"],\n  FROZEN:         ["#3a1420", "#f87171", "FROZEN — prefix guard"],\n};\n\nexport default function ORBPanel({ strategyId = "ORB_V1", ltpMap = {} }) {\n  const [st, setSt] = useState(null);\n  const [armed, setArmed] = useState(false);\n  const armTimer = useRef(null);\n\n  useEffect(() => {\n    let alive = true;\n    const pull = async () => {\n      try {\n        const r = await fetch(`${getApiBase()}/api/orb_v1/state`);\n        const d = await r.json();\n        if (alive) setSt(d);\n      } catch { /* backend down — keep last */ }\n    };\n    pull();\n    const t = setInterval(pull, 4000);\n    return () => { alive = false; clearInterval(t); };\n  }, []);\n\n  const squareOff = async () => {\n    if (!armed) {\n      setArmed(true);\n      clearTimeout(armTimer.current);\n      armTimer.current = setTimeout(() => setArmed(false), 4000);\n      return;\n    }\n    setArmed(false);\n    clearTimeout(armTimer.current);\n    try { await fetch(`${getApiBase()}/api/orb_v1/square_off`, { method: "POST" }); } catch { }\n  };\n\n  const pos = st?.position;\n  const lv = st?.levels;\n  const day = st?.day || {};\n  const cfg = st?.cfg || {};\n  const trades = st?.trades || [];\n  const phase = st?.phase || "OFFLINE";\n\n  // ── live marks (ltpMap ticks between polls; route values as fallback) ──\n  const liveLtp = (sym) => {\n    if (!sym) return null;\n    const v = ltpMap[normalizeSymbol(sym)];\n    return typeof v === "number" && v > 0 ? v : null;\n  };\n  const spot = liveLtp(SPOT_KEY) ?? st?.spot?.ltp ?? null;\n  const posLtp = liveLtp(pos?.symbol) ?? pos?.mark ?? null;\n  const openMtm = pos && posLtp != null ? (posLtp - pos.entry) * pos.qty : null;\n  const closedPnl = trades\n    .filter((t) => t.exit_price != null)\n    .reduce((a, t) => a + ((t.exit_price ?? 0) - t.entry_price) * t.qty, 0);\n  const dayTotal = closedPnl + (openMtm ?? 0);\n\n  // range track: 0 at ORB low, 1 at ORB high (spot may sit outside)\n  const range = useMemo(() => {\n    if (!lv || spot == null || lv.high <= lv.low) return null;\n    const pad = (lv.high - lv.low) * 0.35;\n    const lo = lv.low - pad, hi = lv.high + pad;\n    const f = (x) => Math.min(1, Math.max(0, (x - lo) / (hi - lo)));\n    return { spot: f(spot), low: f(lv.low), high: f(lv.high),\n             toHigh: lv.high - spot, toLow: spot - lv.low,\n             outside: spot > lv.high || spot < lv.low };\n  }, [lv, spot]);\n\n  // premium track: entry→TP (headroom below entry = one TP-distance)\n  const ptrack = useMemo(() => {\n    if (!pos || posLtp == null || pos.tp == null) return null;\n    const hi = pos.tp, lo = pos.entry - (pos.tp - pos.entry);\n    if (hi <= lo) return null;\n    const f = (x) => Math.min(1, Math.max(0, (x - lo) / (hi - lo)));\n    return { frac: f(posLtp), entryFrac: f(pos.entry), toTp: pos.tp - posLtp };\n  }, [pos, posLtp]);\n\n  // spot-side stop distance (close-trigger); sign = room left before breach\n  const stopRoom = pos && spot != null && pos.sl_spot\n    ? (pos.side === "CE" ? spot - pos.sl_spot : pos.sl_spot - spot) : null;\n\n  const card = { background: colors.bg.secondary, borderRadius: 10, padding: spacing.md,\n                 marginBottom: spacing.md, border: `1px solid ${colors.border.subtle}` };\n  const label = { fontSize: 11, color: colors.text.muted, textTransform: "uppercase", letterSpacing: 0.5 };\n  const num = { fontVariantNumeric: "tabular-nums" };\n  const chip = (bg, fg, text) => (\n    <span style={{ background: bg, color: fg, borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 600, whiteSpace: "nowrap" }}>{text}</span>\n  );\n  const Stat = ({ k, v, sub, style }) => (\n    <div style={{ minWidth: 92 }}>\n      <div style={label}>{k}</div>\n      <div style={{ fontSize: 16, fontWeight: 700, ...num, ...style }}>{v}</div>\n      {sub && <div style={{ fontSize: 10.5, color: colors.text.muted }}>{sub}</div>}\n    </div>\n  );\n  const [pBg, pFg, pText] = PHASE[phase] || PHASE.OFFLINE;\n  const hb = ago(st?.heartbeat_ts);\n  const stale = st?.running && st?.heartbeat_ts && (Date.now() / 1000 - st.heartbeat_ts) > 20;\n  const sideUsed = (s) => (day.side_trades?.[s] ?? 0) >= (cfg.max_trades_per_side ?? 1);\n\n  return (\n    <div style={{ padding: spacing.md, color: colors.text.primary }}>\n      {/* ── header ── */}\n      <div style={{ display: "flex", alignItems: "center", gap: spacing.md, flexWrap: "wrap", marginBottom: spacing.md }}>\n        <div>\n          <div style={{ fontSize: 16, fontWeight: 800 }}>{stratName(strategyId)}\n            <span style={{ marginLeft: 8, fontSize: 11, color: colors.text.muted, fontWeight: 500 }}>Outrider · 15m opening-range breakout</span>\n          </div>\n          <div style={{ fontSize: 11, color: colors.text.muted, marginTop: 2 }}>\n            long weekly NIFTY options · touch of either edge · TP +{cfg.target_value ?? 50}% · stop {cfg.sl_pct ?? 0.04}% of spot on 1m closes · flat by {cfg.eod_square_off ?? "13:00"}\n          </div>\n        </div>\n        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>\n          {chip(pBg, pFg, pText)}\n          {st?.mode === "LIVE" ? chip("#3a1420", "#f87171", "LIVE") : st?.mode === "PAPER" ? chip("#1c2a4a", "#93c5fd", "PAPER") : chip("#1f2937", "#9ca3af", st?.mode || "—")}\n          {st?.running && chip(stale ? "#3a1420" : colors.bg.tertiary, stale ? "#f87171" : colors.text.muted, stale ? `ENGINE STALE ${hb}` : `♥ ${hb ?? "…"}`)}\n        </span>\n      </div>\n\n      {(st?.frozen || st?.refused) && (\n        <div style={{ ...card, borderColor: "#f87171", background: "#3a1420", color: "#fecaca", fontSize: 12 }}>\n          <b>{st.frozen ? "PREFIX GUARD TRIPPED" : "DAY REFUSED"}</b> — {st.refused || day.frozen || "inputs unreliable; the day will not trade (fail-closed)."}\n        </div>\n      )}\n      {!st?.running && (\n        <div style={{ ...card, fontSize: 12, color: colors.text.muted }}>\n          Runtime not up — mode OFF, not licensed, or the backend is still booting (40–45 s).\n        </div>\n      )}\n\n      {/* ── day scoreboard ── */}\n      <div style={{ ...card, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "flex-start" }}>\n        <Stat k="Day P&L" v={fmtInr(dayTotal)} style={pnlStyle(dayTotal)} sub={openMtm != null ? `closed ${fmtInr(closedPnl)} · open ${fmtInr(openMtm)}` : "closed rows"} />\n        <Stat k="NIFTY spot" v={spot != null ? Math.round(spot).toLocaleString("en-IN") : "…"} sub={st?.last_bar ? `last 1m ${hhmm(st.last_bar.ts)} · ${st.last_bar.bars} bars` : "no bars yet"} />\n        <Stat k="Trades" v={`${day.day_trades ?? 0} / ${cfg.max_trades_per_day ?? 2}`} sub={`CE ${day.side_trades?.CE ?? 0}·PE ${day.side_trades?.PE ?? 0} of ${cfg.max_trades_per_side ?? 1} each`} />\n        <Stat k="Signals" v={day.signals ?? day.consumed_signals ?? 0} sub={`dropped open ${day.dropped_open ?? 0} · budget ${day.dropped_budget ?? 0} · block ${day.dropped_block ?? 0}`} />\n        <Stat k="Exits" v={Object.values(day.exits || {}).reduce((a, b) => a + b, 0)} sub={Object.entries(day.exits || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"} />\n      </div>\n\n      {/* ── range track ── */}\n      <div style={card}>\n        <div style={{ ...label, marginBottom: 8 }}>Opening range · first 15 min · either edge is a trigger</div>\n        {!lv && <div style={{ fontSize: 12, color: colors.text.muted }}>{phase === "WINDOW" ? "Building the range from 1-minute bars…" : phase === "PRE_OPEN" ? "Levels lock at 09:30." : "No levels today."}</div>}\n        {lv && (\n          <>\n            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 6, ...num }}>\n              <span>PE trigger · low <b>{lv.low.toLocaleString("en-IN")}</b> {sideUsed("PE") ? chip(colors.bg.tertiary, colors.text.muted, "PE used") : chip("#14351f", "#4ade80", "PE armed")}</span>\n              <span style={{ color: colors.text.muted }}>range {lv.range} pts</span>\n              <span>{sideUsed("CE") ? chip(colors.bg.tertiary, colors.text.muted, "CE used") : chip("#14351f", "#4ade80", "CE armed")} high <b>{lv.high.toLocaleString("en-IN")}</b> · CE trigger</span>\n            </div>\n            <div style={{ position: "relative", height: 12, background: colors.bg.tertiary, borderRadius: 6 }}>\n              {range && (\n                <>\n                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `${range.low * 100}%`, width: `${(range.high - range.low) * 100}%`, background: "#1c2a4a", opacity: 0.9, borderRadius: 6 }} />\n                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `calc(${range.low * 100}% - 1px)`, width: 2, background: "#93c5fd" }} />\n                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `calc(${range.high * 100}% - 1px)`, width: 2, background: "#93c5fd" }} />\n                  <div style={{ position: "absolute", top: -3, bottom: -3, left: `calc(${range.spot * 100}% - 4px)`, width: 8, borderRadius: 4, background: range.outside ? "#4ade80" : ACCENT, boxShadow: "0 0 6px rgba(0,0,0,.5)" }} />\n                </>\n              )}\n            </div>\n            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.text.muted, marginTop: 4, ...num }}>\n              <span>{range ? `${fmtPts(-range.toLow)} to low` : "—"}</span>\n              <span>spot <b style={{ color: colors.text.primary }}>{spot != null ? spot.toFixed(1) : "…"}</b>{range?.outside && " · OUTSIDE the range"}</span>\n              <span>{range ? `${fmtPts(range.toHigh)} to high` : "—"}</span>\n            </div>\n          </>\n        )}\n      </div>\n\n      {/* ── open position ── */}\n      <div style={card}>\n        <div style={{ ...label, marginBottom: 8 }}>Open position · exits at 1-minute closes only</div>\n        {!pos && <div style={{ fontSize: 12, color: colors.text.muted }}>{day.pending_side ? `Signal ${day.pending_side} pending fill…` : "Flat."}</div>}\n        {pos && (\n          <>\n            <div style={{ fontSize: 13, display: "flex", gap: spacing.lg, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>\n              {chip(pos.side === "CE" ? "#14351f" : "#3a1420", pos.side === "CE" ? "#4ade80" : "#f87171", pos.side)}\n              <b>{pos.symbol}</b>\n              <span style={{ color: colors.text.muted }}>{pos.mode} · qty {pos.qty} ({pos.lots} lot{pos.lots > 1 ? "s" : ""}) · in since {hhmm(pos.entry_ts)}</span>\n              <span style={{ marginLeft: "auto", ...num }}>\n                LTP <b style={{ fontSize: 15 }}>{posLtp != null ? fmt2(posLtp) : "…"}</b>\n                {openMtm != null && <b style={{ ...pnlStyle(openMtm), marginLeft: 10 }}>{fmtInr(openMtm)}</b>}\n              </span>\n              <button onClick={squareOff}\n                style={{ cursor: "pointer", borderRadius: 8, padding: "6px 14px", fontWeight: 700, fontSize: 12,\n                         border: `1px solid ${armed ? "#f87171" : colors.border.subtle}`,\n                         background: armed ? "#3a1420" : colors.bg.tertiary, color: armed ? "#f87171" : colors.text.primary }}>\n                {armed ? "TAP AGAIN TO SQUARE OFF" : "Square off"}\n              </button>\n            </div>\n            {/* premium track: entry → TP */}\n            <div style={{ position: "relative", height: 10, background: colors.bg.tertiary, borderRadius: 5, overflow: "hidden" }}>\n              {ptrack && (\n                <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${ptrack.frac * 100}%`, borderRadius: 5,\n                              background: `linear-gradient(90deg, #f87171, ${ACCENT} ${ptrack.entryFrac * 100}%, #4ade80)`, opacity: 0.85 }} />\n              )}\n              {ptrack && <div style={{ position: "absolute", left: `calc(${ptrack.entryFrac * 100}% - 1px)`, top: 0, bottom: 0, width: 2, background: colors.text.primary, opacity: 0.7 }} />}\n            </div>\n            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.text.muted, marginTop: 4, ...num }}>\n              <span>premium</span>\n              <span>entry {fmt2(pos.entry)}</span>\n              <span>TP {fmt2(pos.tp)}{ptrack ? ` · ${fmt2(ptrack.toTp)} to go` : ""}</span>\n            </div>\n            {/* spot-side stop */}\n            <div style={{ display: "flex", gap: spacing.lg, fontSize: 12, marginTop: 8, flexWrap: "wrap", ...num }}>\n              <span style={{ color: colors.text.muted }}>spot stop <b style={{ color: colors.text.primary }}>{fmt2(pos.sl_spot)}</b> ({pos.side === "CE" ? "close below" : "close above"})</span>\n              <span style={{ color: stopRoom != null && stopRoom < 3 ? "#f87171" : colors.text.muted }}>\n                room <b>{stopRoom != null ? `${stopRoom.toFixed(1)} pts` : "…"}</b>{stopRoom != null && stopRoom < 0 && " · breached intrabar — waits for the close"}\n              </span>\n              {pos.mode === "LIVE" && chip("#1c2a4a", "#93c5fd", "engine exits · no GTT")}\n            </div>\n          </>\n        )}\n      </div>\n\n      {/* ── today\'s rows ── */}\n      <div style={card}>\n        <div style={{ ...label, marginBottom: 8 }}>Today · {trades.length} row{trades.length === 1 ? "" : "s"}</div>\n        {trades.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>No trades yet today.</div>}\n        {trades.length > 0 && (\n          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, ...num }}>\n            <thead>\n              <tr style={{ color: colors.text.muted, textAlign: "left" }}>\n                {["Time", "Side", "Symbol", "Qty", "Entry", "Exit", "Reason", "P&L"].map((h) => (\n                  <th key={h} style={{ fontWeight: 600, padding: "4px 6px", borderBottom: `1px solid ${colors.border.subtle}` }}>{h}</th>\n                ))}\n              </tr>\n            </thead>\n            <tbody>\n              {trades.map((t) => {\n                const pnl = t.exit_price != null ? (t.exit_price - t.entry_price) * t.qty : null;\n                return (\n                  <tr key={t.paper_trade_id} style={{ borderBottom: `1px solid ${colors.border.subtle}` }}>\n                    <td style={{ padding: "4px 6px" }}>{hhmm(t.entry_time)}{t.exit_time ? `→${hhmm(t.exit_time)}` : ""}</td>\n                    <td style={{ padding: "4px 6px" }}>{chip(t.side === "CE" ? "#14351f" : "#3a1420", t.side === "CE" ? "#4ade80" : "#f87171", t.side)}</td>\n                    <td style={{ padding: "4px 6px" }}><b>{t.symbol}</b> <span style={{ color: colors.text.muted }}>{t.trade_mode}</span></td>\n                    <td style={{ padding: "4px 6px" }}>{t.qty}</td>\n                    <td style={{ padding: "4px 6px" }}>{fmt2(t.entry_price)}</td>\n                    <td style={{ padding: "4px 6px" }}>{t.exit_price != null ? fmt2(t.exit_price) : chip("#3b2a05", ACCENT, "OPEN")}</td>\n                    <td style={{ padding: "4px 6px" }}>{t.exit_reason || "—"}</td>\n                    <td style={{ padding: "4px 6px", fontWeight: 700, ...pnlStyle(pnl ?? 0) }}>{pnl != null ? fmtInr(pnl) : "—"}</td>\n                  </tr>\n                );\n              })}\n            </tbody>\n          </table>\n        )}\n      </div>\n\n      <div style={{ fontSize: 10.5, color: colors.text.muted, padding: "0 4px" }}>\n        Sealed 2026-09-03 · Config {Number(cfg.target_value) === 60 ? "B (+60%)" : "A (+50%)"} · premium band ₹{cfg.premium_min ?? 150}–{cfg.premium_max ?? 200} · no new entries ≥ {cfg.entry_block_time ?? "12:00"} · budgets {cfg.max_trades_per_day ?? 2}/day, {cfg.max_trades_per_side ?? 1}/side · docs/ORB_V1_BIBLE.pdf\n      </div>\n    </div>\n  );\n}\n'}

EDITS = [('backend/app/engine/orb/orb_engine.py', 'replace', '        self._chain_meta: Dict[str, dict] = {}     # symbol -> {token, type}\n', '        self._chain_meta: Dict[str, dict] = {}     # symbol -> {token, type}\n        # ── ORB_PANEL_V2_20260916 ── dashboard telemetry (read by the state\n        # route; prices themselves tick to the UI through LTPStore).\n        self.last_spot: Optional[float] = None\n        self.last_spot_ts: Optional[int] = None\n        self.last_marks: Dict[str, float] = {}\n        self.last_poll_ts: Optional[int] = None\n', 1), ('backend/app/engine/orb/orb_engine.py', 'replace', '            q = kite.quote([SPOT_KEY]) or {}\n            return float(q.get(SPOT_KEY, {}).get("last_price") or 0) or None\n        except Exception:\n            return None\n', '            q = kite.quote([SPOT_KEY]) or {}\n            v = float(q.get(SPOT_KEY, {}).get("last_price") or 0) or None\n            if v:\n                self.last_spot, self.last_spot_ts = v, int(time.time())\n                try:                                   # TSG_LTP_PUBLISH doctrine\n                    from app.marketdata.ltp_store import LTPStore\n                    LTPStore.update("NIFTY50", v)      # ── ORB_PANEL_V2_20260916 ──\n                except Exception:\n                    pass\n            return v\n        except Exception:\n            return None\n', 1), ('backend/app/engine/orb/orb_engine.py', 'replace', '            q = kite.quote([f"NFO:{s}" for s in symbols]) or {}\n            return {s: float(q.get(f"NFO:{s}", {}).get("last_price") or 0)\n                    for s in symbols}\n        except Exception:\n            return {}\n', '            q = kite.quote([f"NFO:{s}" for s in symbols]) or {}\n            out = {s: float(q.get(f"NFO:{s}", {}).get("last_price") or 0)\n                   for s in symbols}\n            try:                                       # TSG_LTP_PUBLISH doctrine\n                from app.marketdata.ltp_store import LTPStore\n                for s, v in out.items():               # ── ORB_PANEL_V2_20260916 ──\n                    if v > 0:\n                        LTPStore.update(s, v)\n                        self.last_marks[s] = v\n            except Exception:\n                pass\n            return out\n        except Exception:\n            return {}\n', 1), ('backend/app/engine/orb/orb_engine.py', 'replace', '                now = now_ist()\n                hm = now.hour * 60 + now.minute\n', '                now = now_ist()\n                hm = now.hour * 60 + now.minute\n                self.last_poll_ts = int(now.timestamp())   # ── ORB_PANEL_V2_20260916 ── heartbeat\n', 1)]

VERIFY = [('backend/app/engine/orb/orb_engine.py', 'ORB_PANEL_V2_20260916', 4), ('backend/app/engine/orb/orb_engine.py', 'ORB_RECONCILE_20260911', 1), ('backend/app/api/orb_state_routes.py', 'ORB_PANEL_V2_20260916', 1), ('frontend/src/strategies/orb/ORBPanel.jsx', 'dashboard panel · v2', 1)]



def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    """A backend-relative path lands in both trees; frontend in one."""
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def stage_edit(text, kind, anchor, payload, count, path):
    n = text.count(anchor)
    if kind == "replaceall":
        if n != count:
            fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
        return text.replace(anchor, payload)
    if n != count:
        fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
    if kind == "replace":
        return text.replace(anchor, payload)
    if kind == "before":
        return text.replace(anchor, payload + anchor)
    if kind == "after":
        return text.replace(anchor, anchor + payload)
    fail(f"unknown edit kind {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--single-tree", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard "
             "requirement locally; pass --single-tree only on a CI checkout")

    # ── prerequisite + idempotency ──
    probe = os.path.join(ROOT, "backend", "app", "engine", "orb", "orb_engine.py")
    ptext = open(probe, encoding="utf-8").read()
    if "ORB_RECONCILE_20260911" not in ptext:
        fail("engine is older than ORB_RECONCILE_20260911 — pull main first")
    if FENCE in ptext:
        print(f"  SKIP   panel v2 already present — "
              f"nothing to do")
        return

    # ── stage every write in memory first ──
    staged = {}   # abs path -> new text
    for rel, body in PAYLOADS.items():
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                shutil.copy2(p, p + ".bak-" + FENCE)
            staged[p] = body
    per_file = {}
    for rel, kind, anchor, payload, count in EDITS:
        per_file.setdefault(rel, []).append((kind, anchor, payload, count))
    for rel, ops in per_file.items():
        src_path = os.path.join(ROOT, rel)
        if not os.path.exists(src_path):
            fail(f"{src_path} not found")
        text = open(src_path, encoding="utf-8").read()
        if FENCE in text:
            fail(f"{rel} already carries the fence — mixed state, resolve by hand")
        for kind, anchor, payload, count in ops:
            text = stage_edit(text, kind, anchor, payload, count, rel)
        for p in both_trees(rel, a.single_tree):
            if p != src_path and not os.path.exists(p):
                fail(f"dual-tree copy missing: {p}")
            staged[p] = text

    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── staged compile gates ──
    tmp = tempfile.mkdtemp(prefix="orv_gate_")
    jsx_targets = []
    for p, body in staged.items():
        t = os.path.join(tmp, os.path.basename(p))
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        elif p.endswith((".jsx", ".js")):
            jsx_targets.append((p, t))
    print(f"  OK     py_compile gate passed")
    esb = shutil.which("esbuild")
    npx = shutil.which("npx")
    for p, t in jsx_targets:
        cmd = None
        if esb:
            cmd = [esb, "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        elif npx:
            cmd = [npx, "--yes", "esbuild", "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        if cmd is None:
            print(f"  WARN   esbuild unavailable — JSX gate skipped for {p}")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "frontend"))
        if r.returncode != 0:
            fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
    if jsx_targets and (esb or npx):
        print(f"  OK     esbuild JSX gate passed ({len(jsx_targets)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  write {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write, with backups for edited files ──
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    # ── grep-count verification ──
    bad = 0
    for rel, needle, mn in VERIFY:
        got = open(os.path.join(ROOT, rel), encoding="utf-8").read().count(needle)
        ok = got >= mn
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} (need >= {mn})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   ORB dashboard v2 applied. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD python3 app/engine/orb/test_orb_manager.py && full rebuild + restart")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
