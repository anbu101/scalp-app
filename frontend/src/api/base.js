export function getApiBase() {
    const isTauri =
      typeof window !== "undefined" &&
      "__TAURI_INTERNALS__" in window;
  
    if (isTauri) {
      return "http://127.0.0.1:47321";
    }
  
    // Browser dev
    return "http://127.0.0.1:8000";
  }
  