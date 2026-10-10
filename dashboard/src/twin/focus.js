// Phase positions and camera focus points, shared by the 3D scene and the page
// (kept out of Scene.jsx so the 3D bundle can load lazily).
export const PHASE_X = { r: -0.4, y: 0, b: 0.4 };
// where the camera looks when flying to a component
export const FOCUS = {
  core: [0, 0.55, 0], oil: [0, 0.5, 0], tank: [0, 0.55, 0], radiators: [0, 0.5, 0.42],
  conservator: [0, 1.38, -0.22], breather: [0.48, 1.18, -0.22], oil_pocket: [0.5, 1.12, -0.1],
  monitor_box: [0.7, 0.6, 0.12], tap_switch: [-0.55, 1.13, -0.2], drain_valve: [0.55, 0.1, 0.4],
  earthing: [-0.67, 0.12, 0],
  ...Object.fromEntries(["r", "y", "b"].flatMap((p) => [[`lv_winding_${p}`, [PHASE_X[p], 0.55, 0]],
    [`hv_winding_${p}`, [PHASE_X[p], 0.55, 0]], [`hv_bushing_${p}`, [PHASE_X[p] * 0.85, 1.32, 0.08]],
    [`lv_bushing_${p}`, [PHASE_X[p] * 1.1 - 0.15, 1.2, 0.27]]])),
  lv_bushing_n: [0.45, 1.2, 0.27],
};

