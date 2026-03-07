export function getApiBase() {
  const isTauri =
    typeof window !== "undefined" &&
    "__TAURI_INTERNALS__" in window;

  if (isTauri) {
    return "http://127.0.0.1:47321";
  }

  // Browser mode - use current hostname with backend port
  // This automatically works for:
  // - Local: http://localhost:47321
  // - WiFi: http://192.168.1.3:47321
  // - Tailscale: http://100.122.185.95:47321
  const hostname = window.location.hostname;
  return `http://${hostname}:47321`;
}