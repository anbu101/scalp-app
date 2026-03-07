import { useState, useEffect } from "react";

/**
 * Returns true when the viewport width is below 768px.
 * Updates live on resize so components re-render when the user rotates
 * their device or resizes the browser window.
 *
 * Place at: src/hooks/useIsMobile.js
 *
 * Usage:
 *   import { useIsMobile } from "../hooks/useIsMobile";
 *   const isMobile = useIsMobile();
 */
export function useIsMobile(breakpoint = 768) {
  const [mobile, setMobile] = useState(() => window.innerWidth < breakpoint);

  useEffect(() => {
    const handler = () => setMobile(window.innerWidth < breakpoint);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, [breakpoint]);

  return mobile;
}