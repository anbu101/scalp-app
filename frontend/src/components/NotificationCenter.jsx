/**
 * NotificationCenter — src/components/NotificationCenter.jsx
 *
 * A bell icon with an unread badge for the top bar (place next to the
 * "TODAY P&L / balance" cluster). Clicking it opens a dropdown listing recent
 * operational alerts (the "needs attention" events: rejected/dead entries,
 * partial fills, GTT failures, fill timeouts) pushed via the in-app event
 * feed and collected by NotificationProvider.
 *
 * Data source: useNotifications() from ../context/NotificationProvider
 *   { items, unreadCount, markAllRead, markRead, clearAll }
 *
 * Trade entries/exits are NOT shown here (they have their own toast/audio);
 * only ALERT-class events land in this center.
 *
 * Usage (in the top bar):
 *   import NotificationCenter from "./components/NotificationCenter";
 *   ...
 *   <NotificationCenter />
 *
 * It is self-contained (own styles, click-outside to close) and renders
 * nothing intrusive when there are no notifications — just a quiet bell.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { useNotifications } from "../context/NotificationProvider";
import { colors as T } from "../tokens";   // ── THEME_PHASE2A_20260831 ──

/* ── tokens ── THEME_PHASE2A_20260831: derived from the shared theme tokens so the
   panel follows <html data-theme>. Key names kept for the 40-odd call sites. */
const C = {
  panel:   T.bg.secondary,
  card:    T.bg.tertiary,
  border:  T.border.dark,
  text:    T.text.primary,
  muted:   T.text.tertiary,
  faint:   T.text.muted,
  error:   T.danger,
  errorBg: T.dangerBg,
  warn:    T.warning,
  warnBg:  T.warningBg,
  info:    T.primary,
  infoBg:  T.primaryBg,
};

const SEV = {
  error:   { color: C.error, bg: C.errorBg, icon: "✕", label: "Critical" },
  warning: { color: C.warn,  bg: C.warnBg,  icon: "⚠", label: "Warning" },
  info:    { color: C.info,  bg: C.infoBg,  icon: "ℹ", label: "Info" },
};
function sev(s) { return SEV[(s || "warning").toLowerCase()] || SEV.warning; }

function timeAgo(ts) {
  const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(ts).toLocaleDateString();
}

function clockTime(ts) {
  return new Date(ts).toLocaleTimeString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

export default function NotificationCenter() {
  const { items, unreadCount, markAllRead, markRead, clearAll } = useNotifications();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  // Close on click-outside.
  useEffect(() => {
    if (!open) return;
    function onDoc(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = useCallback(() => {
    setOpen((o) => {
      const next = !o;
      // Opening marks everything read (the user is now looking at them).
      if (next && unreadCount > 0) markAllRead();
      return next;
    });
  }, [unreadCount, markAllRead]);

  const hasError = items.some((n) => !n.read && (n.severity || "").toLowerCase() === "error");
  const badgeColor = hasError ? C.error : C.warn;

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-block" }}>
      {/* Bell button */}
      <button
        onClick={toggle}
        title="Notifications"
        style={{
          position: "relative",
          width: 38,
          height: 38,
          borderRadius: 9,
          background: open ? C.card : "transparent",
          border: `1px solid ${open ? C.border : "transparent"}`,
          color: unreadCount > 0 ? C.text : C.muted,
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 18,
          transition: "background 0.15s, border-color 0.15s, color 0.15s",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.color = C.text; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.color = unreadCount > 0 ? C.text : C.muted; }}
      >
        <span aria-hidden>🔔</span>
        {unreadCount > 0 && (
          <span
            style={{
              position: "absolute",
              top: 3,
              right: 3,
              minWidth: 16,
              height: 16,
              padding: "0 4px",
              borderRadius: 8,
              background: badgeColor,
              color: "#fff",
              fontSize: 10,
              fontWeight: 700,
              lineHeight: "16px",
              textAlign: "center",
              boxShadow: `0 0 0 2px ${C.panel}`,
            }}
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div
          style={{
            position: "absolute",
            top: 46,
            right: 0,
            width: 380,
            maxWidth: "92vw",
            background: C.panel,
            border: `1px solid ${C.border}`,
            borderRadius: 12,
            boxShadow: "0 12px 40px var(--c-shadow)",
            zIndex: 9000,
            overflow: "hidden",
          }}
        >
          {/* Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "12px 14px",
              borderBottom: `1px solid ${C.border}`,
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 700, color: C.text, letterSpacing: "0.3px" }}>
              Notifications
            </span>
            {items.length > 0 && (
              <button
                onClick={clearAll}
                style={{
                  background: "none",
                  border: "none",
                  color: C.faint,
                  fontSize: 11,
                  cursor: "pointer",
                  padding: "2px 4px",
                }}
                onMouseEnter={(e) => (e.target.style.color = C.muted)}
                onMouseLeave={(e) => (e.target.style.color = C.faint)}
              >
                Clear all
              </button>
            )}
          </div>

          {/* List */}
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            {items.length === 0 ? (
              <div
                style={{
                  padding: "32px 16px",
                  textAlign: "center",
                  color: C.faint,
                  fontSize: 13,
                }}
              >
                <div style={{ fontSize: 26, marginBottom: 8, opacity: 0.5 }}>🔕</div>
                You're all caught up.
                <div style={{ fontSize: 11, marginTop: 6, color: C.faint }}>
                  Entry/exit issues that need your attention will appear here.
                </div>
              </div>
            ) : (
              items.map((n) => {
                const meta = sev(n.severity);
                return (
                  <div
                    key={n.id}
                    onClick={() => markRead(n.id)}
                    style={{
                      display: "flex",
                      gap: 11,
                      padding: "11px 14px",
                      borderBottom: `1px solid ${C.border}`,
                      background: n.read ? "transparent" : "var(--c-primary-bg)",
                      cursor: "default",
                    }}
                  >
                    {/* severity dot */}
                    <div
                      style={{
                        flexShrink: 0,
                        width: 22,
                        height: 22,
                        borderRadius: "50%",
                        background: meta.bg,
                        color: meta.color,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 700,
                        marginTop: 1,
                      }}
                    >
                      {meta.icon}
                    </div>

                    {/* content */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "baseline",
                          justifyContent: "space-between",
                          gap: 8,
                        }}
                      >
                        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
                          {n.title}
                        </span>
                        <span
                          title={new Date(n.ts).toLocaleString("en-IN")}
                          style={{ fontSize: 10, color: C.faint, flexShrink: 0, textAlign: "right", lineHeight: 1.3 }}
                        >
                          {timeAgo(n.ts)}
                          <br />
                          <span style={{ fontFamily: "monospace", opacity: 0.8 }}>{clockTime(n.ts)}</span>
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.45, marginTop: 3 }}>
                        {n.message}
                      </div>
                      <div style={{ marginTop: 5, display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {n.symbol && (
                          <Tag>{n.symbol}</Tag>
                        )}
                        {n.mode && (
                          <Tag mono>{n.mode}</Tag>
                        )}
                        {!n.read && (
                          <span style={{ fontSize: 10, color: meta.color, fontWeight: 600 }}>
                            ● new
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Tag({ children, mono }) {
  return (
    <span
      style={{
        fontSize: 10,
        color: C.muted,
        background: "var(--c-bg-tertiary)",
        border: "1px solid var(--c-border-light)",
        borderRadius: 5,
        padding: "1px 6px",
        fontFamily: mono ? "monospace" : "inherit",
        letterSpacing: mono ? "0.4px" : 0,
      }}
    >
      {children}
    </span>
  );
}