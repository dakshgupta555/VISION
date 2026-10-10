import { hotspotColor, SELECT, STATE, VIOLET } from "./states.js";

// 2D fallback when WebGL is unavailable: the same components, states and
// selection, drawn as a front elevation.
const BOXES = [
  ["tank", 120, 120, 360, 300], ["oil", 130, 140, 340, 270], ["radiators", 60, 140, 50, 250],
  ["core", 170, 160, 260, 200], ["conservator", 170, 50, 260, 40], ["breather", 440, 50, 22, 50],
  ["monitor_box", 140, 320, 80, 70], ["oil_pocket", 400, 100, 18, 20], ["tap_switch", 150, 98, 30, 22],
  ["drain_valve", 450, 395, 30, 18], ["earthing", 95, 395, 22, 22],
  ["hv_winding_r", 185, 180, 60, 160], ["hv_winding_y", 270, 180, 60, 160], ["hv_winding_b", 355, 180, 60, 160],
  ["lv_winding_r", 200, 195, 30, 130], ["lv_winding_y", 285, 195, 30, 130], ["lv_winding_b", 370, 195, 30, 130],
  ["hv_bushing_r", 205, 70, 18, 50], ["hv_bushing_y", 291, 70, 18, 50], ["hv_bushing_b", 377, 70, 18, 50],
  ["lv_bushing_r", 245, 100, 12, 20], ["lv_bushing_y", 275, 100, 12, 20], ["lv_bushing_b", 305, 100, 12, 20],
  ["lv_bushing_n", 335, 100, 12, 20],
];

export default function Schematic({ comps, scale, sel, setSel, forced = false }) {
  return (
    <svg viewBox="0 0 540 440" className="w-full h-full" role="img" aria-label="Transformer schematic">
      <rect width="540" height="440" fill="var(--scene-bg)" />
      {BOXES.map(([id, x, y, w, h]) => {
        const c = comps[id];
        if (!c) return null;
        const wind = id.includes("winding");
        const fill = wind ? hotspotColor(c.hotspot_c, scale) : STATE[c.state].color;
        const stroke = sel === id ? SELECT : c.state === "sensor" ? VIOLET : c.pulse ? STATE.red.color : "var(--color-fg)";
        return (
          <g key={id} onClick={() => setSel(id)} style={{ cursor: "pointer" }}>
            <title>{`${c.name}: ${c.reason}`}</title>
            <rect x={x} y={y} width={w} height={h} rx="3" fill={fill} fillOpacity={id === "oil" ? 0.25 : id === "tank" ? 0.35 : 0.9}
              stroke={stroke} strokeWidth={sel === id || c.state === "sensor" || c.pulse ? 3 : 1} />
          </g>
        );
      })}
      <text x="270" y="430" textAnchor="middle" fill="var(--chart-axis)" fontSize="14">
        {forced ? "2D schematic" : "2D schematic: WebGL is unavailable in this browser"}</text>
    </svg>
  );
}
