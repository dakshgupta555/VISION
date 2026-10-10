// One palette for component state. Colour is used only for status.
// Grey doubles as the paint: close to IS 5 shade 697 (light admiralty grey),
// so an unmonitored part looks like plain painted steel, never healthy.
export const STATE = {
  green: { color: "#2da44e", label: "No monitored abnormality" },
  yellow: { color: "#d4a72c", label: "Concerning deviation or uncertainty" },
  red: { color: "#cf222e", label: "Critical condition" },
  grey: { color: "#a3aaa6", label: "Not assessed: unmonitored, missing or stale" },
  sensor: { color: "#a3aaa6", outline: "#8250df", label: "Instrument fault, transformer unaffected" },
};
export const SELECT = "#1f2328";
export const VIOLET = "#8250df";

// Winding hot-spot scale: cool grey to orange. It stops before red, which is
// reserved for alarms.
const COOL = [100, 116, 139], HOT = [249, 115, 22];
export function hotspotColor(c, scale) {
  if (c === null || c === undefined) return STATE.grey.color;
  const f = Math.max(0, Math.min(1, (c - scale.cool_c) / (scale.hot_c - scale.cool_c)));
  const m = COOL.map((a, k) => Math.round(a + f * (HOT[k] - a)));
  return `rgb(${m[0]}, ${m[1]}, ${m[2]})`;
}

export const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}
