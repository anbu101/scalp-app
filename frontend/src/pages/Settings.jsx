import { useEffect, useState } from "react";
import {
  getStrategyConfig,
  saveStrategyConfig,
} from "../api";

/* -------------------------
   Default config shape
-------------------------- */

const DEFAULT_CONFIG = {
  trade_on: false,

  min_sl_points: 0,
  risk_reward_ratio: 1,

  target_override: {
    enabled: false,
    points: 0,
  },

  session: {
    primary: {
      start: "09:15",
      end: "15:30",
    },
    secondary: {
      enabled: false,
      start: "09:15",
      end: "15:30",
    },
  },

  option_premium: {
    min: 0,
    max: 0,
  },

  quantity: {
    lots: 1,
    lot_size: 75,
  },
};

/* -------------------------
   Reusable UI blocks
-------------------------- */

function Section({ title, children }) {
  return (
    <div
      style={{
        border: "1px solid #243055",
        borderRadius: 10,
        padding: 16,
        marginBottom: 20,
        background: "#0f1628",
      }}
    >
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {children}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "240px 1fr",
        alignItems: "center",
        marginBottom: 12,
        gap: 12,
      }}
    >
      <div style={{ opacity: 0.85 }}>{label}</div>
      <div>{children}</div>
    </div>
  );
}

/* -------------------------
   Settings Page
-------------------------- */

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    load();
  }, []);

  async function load() {
    const data = await getStrategyConfig();

    setConfig({
      ...DEFAULT_CONFIG,
      ...data,

      target_override: {
        ...DEFAULT_CONFIG.target_override,
        ...data?.target_override,
      },

      session: {
        ...DEFAULT_CONFIG.session,
        ...data?.session,
        primary: {
          ...DEFAULT_CONFIG.session.primary,
          ...data?.session?.primary,
        },
        secondary: {
          ...DEFAULT_CONFIG.session.secondary,
          ...data?.session?.secondary,
        },
      },

      option_premium: {
        ...DEFAULT_CONFIG.option_premium,
        ...data?.option_premium,
      },

      quantity: {
        ...DEFAULT_CONFIG.quantity,
        ...data?.quantity,
      },
    });
  }

  function update(path, value) {
    const updated = structuredClone(config);
    path.reduce((obj, key, i) => {
      if (i === path.length - 1) obj[key] = value;
      return obj[key];
    }, updated);
    setConfig(updated);
  }

  async function save() {
    await saveStrategyConfig(config);
    setStatus("✅ Saved. Will apply to NEXT trade.");
    setTimeout(() => setStatus(""), 3000);
  }

  if (!config) {
    return (
      <div style={{ padding: 24, color: "#e6e6e6" }}>
        Loading settings…
      </div>
    );
  }

  return (
    <div
      style={{
        padding: 24,
        background: "#0b1220",
        color: "#e6e6e6",
        minHeight: "100vh",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <h2 style={{ marginTop: 0 }}>Strategy Settings</h2>

      {/* Trade ON / OFF */}
      <Section title="Trading Control">
        <Row label="Enable Trading">
          <input
            type="checkbox"
            checked={config.trade_on}
            onChange={(e) =>
              update(["trade_on"], e.target.checked)
            }
          />{" "}
          <span style={{ marginLeft: 8 }}>
            Trade ON (applies to next trade only)
          </span>
        </Row>
      </Section>

      {/* Risk & Reward */}
      <Section title="Risk Management">
        <Row label="Minimum SL Points">
          <input
            type="number"
            min="0"
            value={config.min_sl_points}
            onChange={(e) =>
              update(
                ["min_sl_points"],
                Math.max(0, Number(e.target.value))
              )
            }
          />
        </Row>

        <Row label="Risk / Reward Ratio">
          <input
            type="number"
            step="0.1"
            min="0"
            value={config.risk_reward_ratio}
            onChange={(e) =>
              update(
                ["risk_reward_ratio"],
                Math.max(0, Number(e.target.value))
              )
            }
          />
        </Row>
      </Section>

      {/* Target Override */}
      <Section title="Target Override">
        <Row label="Enable Override">
          <input
            type="checkbox"
            checked={config.target_override.enabled}
            onChange={(e) =>
              update(
                ["target_override", "enabled"],
                e.target.checked
              )
            }
          />
        </Row>

        <Row label="Target Points">
          <input
            type="number"
            min="0"
            disabled={!config.target_override.enabled}
            value={config.target_override.points}
            onChange={(e) =>
              update(
                ["target_override", "points"],
                Math.max(0, Number(e.target.value))
              )
            }
          />
        </Row>
      </Section>

      {/* Sessions */}
      <Section title="Trading Sessions">
        <Row label="Primary Session">
          <input
            type="time"
            value={config.session.primary.start}
            onChange={(e) =>
              update(
                ["session", "primary", "start"],
                e.target.value
              )
            }
          />
          &nbsp;to&nbsp;
          <input
            type="time"
            value={config.session.primary.end}
            onChange={(e) =>
              update(
                ["session", "primary", "end"],
                e.target.value
              )
            }
          />
        </Row>

        <Row label="Enable Secondary Session">
          <input
            type="checkbox"
            checked={config.session.secondary.enabled}
            onChange={(e) =>
              update(
                ["session", "secondary", "enabled"],
                e.target.checked
              )
            }
          />
        </Row>

        <Row label="Secondary Session">
          <input
            type="time"
            disabled={!config.session.secondary.enabled}
            value={config.session.secondary.start}
            onChange={(e) =>
              update(
                ["session", "secondary", "start"],
                e.target.value
              )
            }
          />
          &nbsp;to&nbsp;
          <input
            type="time"
            disabled={!config.session.secondary.enabled}
            value={config.session.secondary.end}
            onChange={(e) =>
              update(
                ["session", "secondary", "end"],
                e.target.value
              )
            }
          />
        </Row>
      </Section>

      {/* Option Premium */}
      <Section title="Option Premium Filter">
        <Row label="Minimum Premium">
          <input
            type="number"
            min="0"
            value={config.option_premium.min}
            onChange={(e) =>
              update(
                ["option_premium", "min"],
                Math.max(0, Number(e.target.value))
              )
            }
          />
        </Row>

        <Row label="Maximum Premium">
          <input
            type="number"
            min="0"
            value={config.option_premium.max}
            onChange={(e) =>
              update(
                ["option_premium", "max"],
                Math.max(0, Number(e.target.value))
              )
            }
          />
        </Row>
      </Section>

      {/* Quantity */}
      <Section title="Order Quantity">
        <Row label="Lots">
          <input
            type="number"
            min="0"
            value={config.quantity.lots}
            onChange={(e) =>
              update(
                ["quantity", "lots"],
                Math.max(0, Number(e.target.value))
              )
            }
          />
          <span style={{ marginLeft: 8, opacity: 0.7 }}>
            (1 lot = {config.quantity.lot_size})
          </span>
        </Row>
      </Section>

      {/* Save */}
      <div style={{ marginTop: 20 }}>
        <button
          onClick={save}
          style={{
            padding: "10px 20px",
            borderRadius: 8,
            border: "none",
            background: "#2563eb",
            color: "#fff",
            fontSize: 14,
            cursor: "pointer",
          }}
        >
          Save Settings
        </button>
        <span style={{ marginLeft: 12 }}>{status}</span>
      </div>
    </div>
  );
}
