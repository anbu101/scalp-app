import { createContext, useContext, useState, useCallback } from "react";
import { colors as T } from "../tokens";   // ── THEME_PHASE2A_20260831 ──

/* -------------------------
   Design Tokens
-------------------------- */

// ── THEME_PHASE2A_20260831 ── derived from the shared theme tokens (was a fixed dark
// palette). `info` maps to the brand primary — tokens carry no info colour.
const colors = {
  success:   T.success,
  successBg: T.successBg,
  warning:   T.warning,
  warningBg: T.warningBg,
  danger:    T.danger,
  dangerBg:  T.dangerBg,
  info:      T.primary,
  infoBg:    T.primaryBg,
  bg:     { secondary: T.bg.secondary },
  border: { light: T.border.light },
  text:   { primary: T.text.primary, secondary: T.text.secondary },
};

/* -------------------------
   Toast Context
-------------------------- */

const ToastContext = createContext(null);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return context;
}

/* -------------------------
   Toast Provider
-------------------------- */

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((toast) => {
    const id = Date.now() + Math.random();
    const newToast = {
      id,
      type: toast.type || "info",
      title: toast.title,
      message: toast.message,
      duration: toast.duration || 5000,
      icon: toast.icon,
    };

    setToasts((prev) => [...prev, newToast]);

    if (newToast.duration > 0) {
      setTimeout(() => {
        removeToast(id);
      }, newToast.duration);
    }

    return id;
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const toast = {
    success: (title, message, options = {}) =>
      addToast({ ...options, type: "success", title, message, icon: "✓" }),
    error: (title, message, options = {}) =>
      addToast({ ...options, type: "danger", title, message, icon: "✕" }),
    warning: (title, message, options = {}) =>
      addToast({ ...options, type: "warning", title, message, icon: "⚠" }),
    info: (title, message, options = {}) =>
      addToast({ ...options, type: "info", title, message, icon: "ℹ" }),
    custom: (options) => addToast(options),
  };

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </ToastContext.Provider>
  );
}

/* -------------------------
   Toast Container
-------------------------- */

function ToastContainer({ toasts, onRemove }) {
  return (
    <div
      style={{
        position: "fixed",
        bottom: 24,
        right: 24,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        gap: 12,
        maxWidth: 420,
      }}
    >
      {toasts.map((toast) => (
        <Toast key={toast.id} toast={toast} onRemove={() => onRemove(toast.id)} />
      ))}
    </div>
  );
}

/* -------------------------
   Individual Toast
-------------------------- */

function Toast({ toast, onRemove }) {
  const typeStyles = {
    success: { bg: colors.successBg, color: colors.success, border: colors.success },
    danger: { bg: colors.dangerBg, color: colors.danger, border: colors.danger },
    warning: { bg: colors.warningBg, color: colors.warning, border: colors.warning },
    info: { bg: colors.infoBg, color: colors.info, border: colors.info },
  };

  const style = typeStyles[toast.type] || typeStyles.info;

  return (
    <div
      style={{
        background: colors.bg.secondary,
        border: `1px solid ${style.border}`,
        borderRadius: 8,
        padding: "12px 16px",
        boxShadow: "0 4px 12px var(--c-shadow)",
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        minWidth: 320,
        animation: "slideIn 0.3s ease-out",
      }}
    >
      {/* Icon */}
      <div
        style={{
          width: 24,
          height: 24,
          borderRadius: "50%",
          background: style.bg,
          color: style.color,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 14,
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {toast.icon}
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: colors.text.primary,
            marginBottom: 4,
          }}
        >
          {toast.title}
        </div>
        {toast.message && (
          <div
            style={{
              fontSize: 13,
              color: colors.text.secondary,
              lineHeight: 1.4,
            }}
          >
            {toast.message}
          </div>
        )}
      </div>

      {/* Close Button */}
      <button
        onClick={onRemove}
        style={{
          background: "none",
          border: "none",
          color: colors.text.secondary,
          cursor: "pointer",
          padding: 0,
          fontSize: 18,
          lineHeight: 1,
          opacity: 0.6,
          transition: "opacity 0.2s",
          flexShrink: 0,
        }}
        onMouseEnter={(e) => (e.target.style.opacity = 1)}
        onMouseLeave={(e) => (e.target.style.opacity = 0.6)}
      >
        ×
      </button>
    </div>
  );
}

/* -------------------------
   Toast Animations
-------------------------- */

export const ToastAnimations = () => (
  <style>{`
    @keyframes slideIn {
      from {
        transform: translateX(400px);
        opacity: 0;
      }
      to {
        transform: translateX(0);
        opacity: 1;
      }
    }
    
    @keyframes slideOut {
      from {
        transform: translateX(0);
        opacity: 1;
      }
      to {
        transform: translateX(400px);
        opacity: 0;
      }
    }
  `}</style>
);

/* -------------------------
   Usage Examples
-------------------------- */

/*

// 1. Wrap your app with ToastProvider (in App.jsx or main layout)
import { ToastProvider, ToastAnimations } from './components/ToastNotifications';

function App() {
  return (
    <ToastProvider>
      <ToastAnimations />
      <YourAppContent />
    </ToastProvider>
  );
}

// 2. Use in any component
import { useToast } from './components/ToastNotifications';

function Dashboard() {
  const toast = useToast();

  // Success notification
  toast.success(
    "Position Entered",
    "NIFTY25800PE @ ₹124.00"
  );

  // Error notification
  toast.error(
    "Stop Loss Hit",
    "NIFTY25800PE closed at -₹2,500"
  );

  // Warning notification
  toast.warning(
    "Max Loss Approaching",
    "Daily loss limit at 80%"
  );

  // Info notification
  toast.info(
    "Market Update",
    "NIFTY up 0.75%"
  );

  // Custom duration
  toast.success(
    "Trade Executed",
    "Order filled successfully",
    { duration: 3000 } // 3 seconds
  );

  // Custom notification
  toast.custom({
    type: "success",
    title: "Target Reached",
    message: "NIFTY25850CE +₹5,000 🎉",
    icon: "🎯",
    duration: 7000
  });
}

*/