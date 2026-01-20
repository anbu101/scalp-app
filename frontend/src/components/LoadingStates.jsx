/* -------------------------
   Design Tokens
-------------------------- */

const colors = {
    bg: {
      primary: "#020817",
      secondary: "#0f172a",
      tertiary: "#1e293b",
    },
    border: {
      light: "#334155",
    },
    text: {
      primary: "#f8fafc",
      secondary: "#cbd5e1",
      muted: "#64748b"
    }
  };
  
  /* -------------------------
     Skeleton Loader
  -------------------------- */
  
  export function Skeleton({ width = "100%", height = 20, style }) {
    return (
      <div
        style={{
          width,
          height,
          background: `linear-gradient(90deg, ${colors.bg.tertiary} 25%, ${colors.border.light} 50%, ${colors.bg.tertiary} 75%)`,
          backgroundSize: "200% 100%",
          animation: "shimmer 1.5s infinite",
          borderRadius: 4,
          ...style
        }}
      />
    );
  }
  
  /* -------------------------
     Table Skeleton Loader
  -------------------------- */
  
  export function TableSkeleton({ rows = 3, columns = 11 }) {
    return (
      <div style={{ padding: "12px" }}>
        {Array.from({ length: rows }).map((_, rowIndex) => (
          <div
            key={rowIndex}
            style={{
              display: "grid",
              gridTemplateColumns: `repeat(${columns}, 1fr)`,
              gap: 12,
              marginBottom: 12,
              padding: "12px 0"
            }}
          >
            {Array.from({ length: columns }).map((_, colIndex) => (
              <Skeleton key={colIndex} height={16} />
            ))}
          </div>
        ))}
      </div>
    );
  }
  
  /* -------------------------
     Card Skeleton Loader
  -------------------------- */
  
  export function CardSkeleton({ rows = 3 }) {
    return (
      <div style={{ padding: "16px" }}>
        <Skeleton width="40%" height={18} style={{ marginBottom: 16 }} />
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <Skeleton height={14} />
          </div>
        ))}
      </div>
    );
  }
  
  /* -------------------------
     Empty State Component
  -------------------------- */
  
  export function EmptyState({ 
    icon = "📭", 
    title = "No data available", 
    description, 
    action 
  }) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "60px 20px",
          textAlign: "center"
        }}
      >
        <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.5 }}>
          {icon}
        </div>
        <h3 style={{
          margin: 0,
          marginBottom: 8,
          fontSize: 16,
          fontWeight: 600,
          color: colors.text.primary
        }}>
          {title}
        </h3>
        {description && (
          <p style={{
            margin: 0,
            marginBottom: 20,
            fontSize: 13,
            color: colors.text.muted,
            maxWidth: 400
          }}>
            {description}
          </p>
        )}
        {action}
      </div>
    );
  }
  
  /* -------------------------
     Loading Spinner
  -------------------------- */
  
  export function LoadingSpinner({ size = 40, style }) {
    return (
      <div
        style={{
          width: size,
          height: size,
          border: `3px solid ${colors.border.light}`,
          borderTop: `3px solid #3b82f6`,
          borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
          ...style
        }}
      />
    );
  }
  
  /* -------------------------
     Full Page Loader
  -------------------------- */
  
  export function FullPageLoader({ message = "Loading..." }) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          background: colors.bg.primary,
          color: colors.text.primary
        }}
      >
        <LoadingSpinner size={48} style={{ marginBottom: 20 }} />
        <div style={{ fontSize: 14, color: colors.text.secondary }}>
          {message}
        </div>
      </div>
    );
  }
  
  /* -------------------------
     Error State Component
  -------------------------- */
  
  export function ErrorState({ 
    title = "Something went wrong", 
    description = "We encountered an error while loading data.",
    onRetry 
  }) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "60px 20px",
          textAlign: "center"
        }}
      >
        <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
        <h3 style={{
          margin: 0,
          marginBottom: 8,
          fontSize: 16,
          fontWeight: 600,
          color: colors.text.primary
        }}>
          {title}
        </h3>
        <p style={{
          margin: 0,
          marginBottom: 20,
          fontSize: 13,
          color: colors.text.muted,
          maxWidth: 400
        }}>
          {description}
        </p>
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              padding: "10px 20px",
              borderRadius: 6,
              border: "none",
              background: "#3b82f6",
              color: colors.text.primary,
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer"
            }}
          >
            Try Again
          </button>
        )}
      </div>
    );
  }
  
  /* -------------------------
     Inline Loader (for cards)
  -------------------------- */
  
  export function InlineLoader({ message = "Loading..." }) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "20px",
          color: colors.text.muted,
          fontSize: 13
        }}
      >
        <LoadingSpinner size={20} />
        {message}
      </div>
    );
  }
  
  /* -------------------------
     Global Animations CSS
  -------------------------- */
  
  export const LoadingAnimations = () => (
    <style>{`
      @keyframes shimmer {
        0% { background-position: 200% 0; }
        100% { background-position: -200% 0; }
      }
      
      @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
      
      @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
    `}</style>
  );
  
  /* -------------------------
     Usage Examples
  -------------------------- */
  
  /*
  
  // In Dashboard.jsx - Loading state
  if (loading) {
    return (
      <>
        <LoadingAnimations />
        <FullPageLoader message="Loading dashboard..." />
      </>
    );
  }
  
  // In table - Empty state
  {rows.length === 0 && (
    <EmptyState
      icon="📊"
      title="No active positions"
      description="Positions will appear here once trades are executed based on your strategy settings."
    />
  )}
  
  // In Today's PnL - Loading
  {loading ? (
    <CardSkeleton rows={3} />
  ) : (
    // ... actual content
  )}
  
  // Table loading
  {loading ? (
    <TableSkeleton rows={5} columns={11} />
  ) : (
    // ... actual table
  )}
  
  // Error state
  {error && (
    <ErrorState
      title="Failed to load positions"
      description="We couldn't fetch your trading data. Please check your connection."
      onRetry={refresh}
    />
  )}
  
  */