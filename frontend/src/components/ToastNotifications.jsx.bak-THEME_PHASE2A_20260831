import { createContext, useContext, useState, useCallback } from "react";

/* -------------------------
   Design Tokens
-------------------------- */

const colors = {
  success: "#059669",
  successBg: "rgba(5, 150, 105, 0.12)",
  warning: "#d97706",
  warningBg: "rgba(217, 119, 6, 0.12)",
  danger: "#dc2626",
  dangerBg: "rgba(220, 38, 38, 0.12)",
  info: "#2563eb",
  infoBg: "rgba(37, 99, 235, 0.1)",
  bg: {
    secondary: "#0f172a",
  },
  border: {
    light: "#334155",
  },
  text: {
    primary: "#f8fafc",
    secondary: "#cbd5e1",
  }
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
        boxShadow: "0 4px 12px rgba(0, 0, 0, 0.4)",
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