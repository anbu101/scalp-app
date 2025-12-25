export default function DebugPanel({ rows = [] }) {
    const base = "http://127.0.0.1:8000/debug/ui";
  
    function open(path) {
      window.open(`${base}${path}`, "_blank");
    }
  
    // Build slot → symbol map
    const slotMap = {};
    rows.forEach((r) => {
      if (r.slot && r.tradingsymbol && !slotMap[r.slot]) {
        slotMap[r.slot] = r.tradingsymbol;
      }
    });
  
    const ceSlots = Object.keys(slotMap)
      .filter((s) => s.startsWith("CE"))
      .sort();
  
    const peSlots = Object.keys(slotMap)
      .filter((s) => s.startsWith("PE"))
      .sort();
  
    return (
      <div
        style={{
          border: "1px solid #243055",
          borderRadius: 10,
          padding: 16,
          background: "#0f1628",
          marginTop: 20,
        }}
      >
        <h3 style={{ marginTop: 0 }}>🛠 Debug Tools</h3>
  
        {/* ---------- GLOBAL ---------- */}
        <Section title="Global">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 12,
            }}
          >
            <Btn onClick={() => open("/market_timeline?refresh=3")}>
              📊 Market Timeline (Live)
            </Btn>
  
            <Btn
              onClick={() =>
                open("/trades?state=BUY_FILLED&refresh=5")
              }
            >
              📘 Active Trades
            </Btn>
  
            <Btn onClick={() => open("/trades?refresh=5")}>
              📘 All Trades
            </Btn>
          </div>
        </Section>
  
        {/* ---------- CE ---------- */}
        {ceSlots.length > 0 && (
          <Section title="CE Slots">
            {ceSlots.map((slot) => {
              const symbol = slotMap[slot];
              return (
                <Row key={slot} label={`${slot} (${symbol})`}>
                  <MiniBtn
                    onClick={() =>
                      open(
                        `/market_timeline?symbol=${symbol}&refresh=3`
                      )
                    }
                  >
                    📊 Timeline
                  </MiniBtn>
                  <MiniBtn
                    onClick={() =>
                      open(
                        `/trades?symbol=${symbol}&refresh=5`
                      )
                    }
                  >
                    📘 Trades
                  </MiniBtn>
                </Row>
              );
            })}
          </Section>
        )}
  
        {/* ---------- PE ---------- */}
        {peSlots.length > 0 && (
          <Section title="PE Slots">
            {peSlots.map((slot) => {
              const symbol = slotMap[slot];
              return (
                <Row key={slot} label={`${slot} (${symbol})`}>
                  <MiniBtn
                    onClick={() =>
                      open(
                        `/market_timeline?symbol=${symbol}&refresh=3`
                      )
                    }
                  >
                    📊 Timeline
                  </MiniBtn>
                  <MiniBtn
                    onClick={() =>
                      open(
                        `/trades?symbol=${symbol}&refresh=5`
                      )
                    }
                  >
                    📘 Trades
                  </MiniBtn>
                </Row>
              );
            })}
          </Section>
        )}
      </div>
    );
  }
  
  /* ---------------- UI helpers ---------------- */
  
  function Section({ title, children }) {
    return (
      <div style={{ marginBottom: 18 }}>
        <div
          style={{
            fontWeight: 600,
            marginBottom: 10,
            opacity: 0.9,
          }}
        >
          {title}
        </div>
        {children}
      </div>
    );
  }
  
  /**
   * GRID ROW:
   * | label (50%) | buttons (50%) |
   */
  function Row({ label, children }) {
    return (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          alignItems: "center",
          gap: 12,
          marginBottom: 10,
        }}
      >
        <div
          style={{
            fontFamily: "monospace",
            opacity: 0.85,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {label}
        </div>
  
        <div
          style={{
            display: "grid",
            gridAutoFlow: "column",
            gap: 10,
            justifyContent: "start",
          }}
        >
          {children}
        </div>
      </div>
    );
  }
  
  /* ---------------- Buttons ---------------- */
  
  function Btn({ children, onClick }) {
    return (
      <button
        onClick={onClick}
        style={{
          padding: "10px 14px",
          borderRadius: 8,
          border: "1px solid #243055",
          background: "#020617",
          color: "#e6e6e6",
          cursor: "pointer",
          width: "100%",
        }}
      >
        {children}
      </button>
    );
  }
  
  function MiniBtn({ children, onClick }) {
    return (
      <button
        onClick={onClick}
        style={{
          padding: "6px 12px",
          borderRadius: 6,
          border: "1px solid #243055",
          background: "#020617",
          color: "#e6e6e6",
          cursor: "pointer",
          fontSize: 12,
        }}
      >
        {children}
      </button>
    );
  }
  