import { useMemo } from "react";

/* -------------------------
   Design Tokens
-------------------------- */

const colors = {
  profit: "#10b981",
  loss: "#ef4444",
  neutral: "#6b7280",
  primary: "#3b82f6",
  bg: {
    tertiary: "#1f2937",
  },
  text: {
    muted: "#6b7280"
  }
};

/* -------------------------
   Mini Sparkline Chart
-------------------------- */

export function Sparkline({ data = [], width = 60, height = 24, color }) {
  if (!data || data.length < 2) {
    return (
      <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 10, color: colors.text.muted }}>—</span>
      </div>
    );
  }

  const points = useMemo(() => {
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;

    return data.map((value, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((value - min) / range) * height;
      return { x, y };
    });
  }, [data, width, height]);

  const pathD = useMemo(() => {
    if (points.length === 0) return "";
    return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  }, [points]);

  const isUp = data[data.length - 1] >= data[0];
  const lineColor = color || (isUp ? colors.profit : colors.loss);

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <path
        d={pathD}
        fill="none"
        stroke={lineColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Last point dot */}
      {points.length > 0 && (
        <circle
          cx={points[points.length - 1].x}
          cy={points[points.length - 1].y}
          r="2"
          fill={lineColor}
        />
      )}
    </svg>
  );
}

/* -------------------------
   Price Change Indicator
-------------------------- */

export function PriceChangeIndicator({ current, previous }) {
  if (typeof current !== "number" || typeof previous !== "number") {
    return null;
  }

  const change = current - previous;
  const changePercent = (change / previous) * 100;
  const isUp = change >= 0;

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 11,
        fontWeight: 600,
        color: isUp ? colors.profit : colors.loss,
      }}
    >
      <span style={{ fontSize: 10 }}>{isUp ? "▲" : "▼"}</span>
      <span>
        {isUp ? "+" : ""}
        {change.toFixed(2)} ({isUp ? "+" : ""}
        {changePercent.toFixed(2)}%)
      </span>
    </div>
  );
}

/* -------------------------
   Progress Bar (SL/TP)
-------------------------- */

export function ProgressBar({ 
  current, 
  entry, 
  stopLoss, 
  takeProfit,
  width = 100,
  height = 6
}) {
  if (
    typeof current !== "number" ||
    typeof entry !== "number" ||
    (typeof stopLoss !== "number" && typeof takeProfit !== "number")
  ) {
    return null;
  }

  // Calculate position
  const hasSL = typeof stopLoss === "number";
  const hasTP = typeof takeProfit === "number";

  let progress = 50; // Default middle
  let status = "neutral";

  if (hasSL && hasTP) {
    // Both SL and TP defined
    const totalRange = Math.abs(takeProfit - stopLoss);
    const currentRange = Math.abs(current - stopLoss);
    progress = (currentRange / totalRange) * 100;
    
    if (current >= takeProfit) {
      status = "profit";
      progress = 100;
    } else if (current <= stopLoss) {
      status = "loss";
      progress = 0;
    } else {
      status = current > entry ? "profit" : "loss";
    }
  } else if (hasSL) {
    // Only SL defined
    const range = Math.abs(entry - stopLoss);
    const distance = Math.abs(current - stopLoss);
    progress = Math.min(100, (distance / range) * 100);
    status = current > entry ? "profit" : "loss";
  } else if (hasTP) {
    // Only TP defined
    const range = Math.abs(takeProfit - entry);
    const distance = Math.abs(current - entry);
    progress = Math.min(100, (distance / range) * 100);
    status = current > entry ? "profit" : "loss";
  }

  progress = Math.max(0, Math.min(100, progress));

  const barColor = 
    status === "profit" ? colors.profit :
    status === "loss" ? colors.loss :
    colors.neutral;

  return (
    <div style={{ width }}>
      <div
        style={{
          width: "100%",
          height,
          background: colors.bg.tertiary,
          borderRadius: height / 2,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${progress}%`,
            height: "100%",
            background: barColor,
            transition: "width 0.3s ease",
            borderRadius: height / 2,
          }}
        />
      </div>
      <div
        style={{
          fontSize: 9,
          color: colors.text.muted,
          marginTop: 2,
          textAlign: "center",
        }}
      >
        {progress.toFixed(0)}%
      </div>
    </div>
  );
}

/* -------------------------
   Distance to Target Indicator
-------------------------- */

export function DistanceIndicator({ current, target, type = "SL" }) {
  if (typeof current !== "number" || typeof target !== "number") {
    return <span style={{ fontSize: 11, color: colors.text.muted }}>—</span>;
  }

  const distance = Math.abs(target - current);
  const percentDistance = ((distance / target) * 100).toFixed(1);
  
  const isClose = percentDistance < 5; // Within 5%
  const color = isClose ? colors.loss : colors.text.muted;

  return (
    <div
      style={{
        fontSize: 10,
        color,
        fontWeight: isClose ? 600 : 400,
      }}
    >
      {type}: {distance.toFixed(2)} ({percentDistance}%)
    </div>
  );
}

/* -------------------------
   PnL Trend Arrow
-------------------------- */

export function PnLTrendArrow({ values = [] }) {
  if (values.length < 2) return null;

  const recent = values.slice(-5); // Last 5 values
  const trend = recent[recent.length - 1] - recent[0];
  
  if (Math.abs(trend) < 0.01) return null; // No significant trend

  const isUp = trend > 0;

  return (
    <span
      style={{
        fontSize: 14,
        color: isUp ? colors.profit : colors.loss,
        marginLeft: 4,
      }}
    >
      {isUp ? "↗" : "↘"}
    </span>
  );
}

/* -------------------------
   Compact Price Display with Sparkline
-------------------------- */

export function PriceWithSparkline({ 
  currentPrice, 
  priceHistory = [], 
  showChange = true 
}) {
  if (!currentPrice) {
    return <span style={{ color: colors.text.muted }}>—</span>;
  }

  const previousPrice = priceHistory.length > 0 ? priceHistory[0] : null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>
          {currentPrice.toFixed(2)}
        </div>
        {showChange && previousPrice && (
          <PriceChangeIndicator current={currentPrice} previous={previousPrice} />
        )}
      </div>
      {priceHistory.length > 1 && (
        <Sparkline data={priceHistory} width={50} height={20} />
      )}
    </div>
  );
}

/* -------------------------
   Usage Examples & Notes
-------------------------- */

/*

USAGE IN DASHBOARD TABLE:

1. Add price history tracking:

const [priceHistory, setPriceHistory] = useState({});

useEffect(() => {
  if (ltpMap && Object.keys(ltpMap).length > 0) {
    setPriceHistory(prev => {
      const updated = { ...prev };
      Object.entries(ltpMap).forEach(([symbol, price]) => {
        if (!updated[symbol]) {
          updated[symbol] = [];
        }
        updated[symbol] = [...updated[symbol], price].slice(-20); // Keep last 20 points
      });
      return updated;
    });
  }
}, [ltpMap]);

2. In table LTP column:

<td style={{ ...td, textAlign: "center" }}>
  <PriceWithSparkline 
    currentPrice={liveLtp}
    priceHistory={priceHistory[r.tradingsymbol] || []}
  />
</td>

3. Add Progress column (new column between TP and P&L):

<th style={{ ...th, textAlign: "center" }}>Progress</th>

<td style={{ ...td, textAlign: "center" }}>
  <ProgressBar
    current={liveLtp}
    entry={slot?.buy_price}
    stopLoss={slot?.sl_price}
    takeProfit={slot?.tp_price}
    width={80}
  />
</td>

4. Enhanced P&L with trend:

<td style={{ ...td, textAlign: "center" }}>
  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 4 }}>
    <span>{pnl === null ? "—" : `₹${Math.round(pnl).toLocaleString('en-IN')}`}</span>
    <PnLTrendArrow values={pnlHistory[r.tradingsymbol] || []} />
  </div>
</td>

*/