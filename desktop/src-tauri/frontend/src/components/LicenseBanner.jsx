import { useEffect, useState } from "react";
import { getLicenseStatus } from "../api";

export default function LicenseBanner() {
  const [license, setLicense] = useState(null);

  useEffect(() => {
    getLicenseStatus()
      .then(setLicense)
      .catch(() =>
        setLicense({
          status: "UNKNOWN", // licensing not implemented yet
        })
      );
  }, []);

  // ✅ Hide banner for VALID or UNKNOWN (dev-friendly)
  if (
    !license ||
    license.status === "VALID" ||
    license.status === "UNKNOWN"
  ) {
    return null;
  }

  return (
    <div
      style={{
        background: "#3b0a0a",
        color: "#ffb4b4",
        padding: "10px",
        textAlign: "center",
        fontWeight: 500,
        fontSize: "14px",
      }}
    >
      🔒 {license.message || "License not valid"}
    </div>
  );
}
