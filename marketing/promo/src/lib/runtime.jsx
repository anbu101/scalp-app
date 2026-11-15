// Frame runtime shared by every video: the page exposes window.VIDEO (size, duration,
// poster, warm-up times) and window.renderAt(t) — every frame is a pure function of t.
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";

export function mount(Video, meta) {
  document.documentElement.setAttribute("data-theme", meta.theme || "dark");
  window.VIDEO = { fps: 30, warmup: [], ...meta };
  const root = createRoot(document.getElementById("root"));
  window.renderAt = (t) => new Promise((res) => {
    flushSync(() => root.render(<Video t={t} />));
    // second pass picks up layout measured in the first (chart width, scroll targets)
    requestAnimationFrame(() => requestAnimationFrame(() => {
      flushSync(() => root.render(<Video t={t} />));
      requestAnimationFrame(() => res(true));
    }));
  });
  window.stageReady = true;
}
