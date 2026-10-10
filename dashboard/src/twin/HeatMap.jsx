import { useId, useMemo, useRef, useState } from "react";
import { hotspotColor } from "./states.js";

// FEM heat map: the r-z temperature field of the LV and HV windings of one
// phase leg, drawn to scale. Fill from the element (cell) means, isotherms by
// marching squares on the nodal grid, the hot-spot marked with its value.
// Colour is the same cool grey to orange as the 3D windings; red stays for alarms.

const MM = 1000;
const nice = (span) => [0.5, 1, 2, 5, 10, 20].find((s) => span / s <= 12) || 50;
const f1 = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(1));

// difference maps: cooler blue, unchanged grey, hotter orange
const GREY = [100, 116, 139], BLUE = [96, 165, 250], ORANGE = [249, 115, 22];
export function diffColor(d, span) {
  const f = Math.max(-1, Math.min(1, d / (span || 1)));
  const to = f < 0 ? BLUE : ORANGE;
  const m = GREY.map((a, k) => Math.round(a + Math.abs(f) * (to[k] - a)));
  return `rgb(${m[0]}, ${m[1]}, ${m[2]})`;
}

function rgb(s) {
  const m = s.match(/\d+/g).map(Number);
  return m;
}

// cell-mean colours into a tiny canvas; the browser scales it onto the block
function blockImage(b, color) {
  const nr = b.r.length - 1, nz = b.z.length - 1;
  const c = document.createElement("canvas");
  c.width = nr; c.height = nz;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(nr, nz);
  for (let j = 0; j < nz; j++) {
    for (let i = 0; i < nr; i++) {
      const v = (b.T[j][i] + b.T[j][i + 1] + b.T[j + 1][i] + b.T[j + 1][i + 1]) / 4;
      const [r, g, bl] = rgb(color(v));
      const p = 4 * ((nz - 1 - j) * nr + i);              // row 0 of the image is the top
      img.data[p] = r; img.data[p + 1] = g; img.data[p + 2] = bl; img.data[p + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL();
}

// marching squares, linear interpolation along cell edges; returns an SVG path
function isoPath(b, level, H) {
  const X = (r) => r * MM, Y = (z) => (H - z) * MM;
  let d = "";
  const nr = b.r.length - 1, nz = b.z.length - 1;
  for (let j = 0; j < nz; j++) {
    for (let i = 0; i < nr; i++) {
      const v = [b.T[j][i], b.T[j][i + 1], b.T[j + 1][i + 1], b.T[j + 1][i]];     // bl, br, tr, tl
      const p = [[b.r[i], b.z[j]], [b.r[i + 1], b.z[j]], [b.r[i + 1], b.z[j + 1]], [b.r[i], b.z[j + 1]]];
      const hits = [];
      for (let e = 0; e < 4; e++) {
        const a = v[e], c = v[(e + 1) % 4];
        if ((a < level) !== (c < level)) {
          const t = (level - a) / (c - a);
          const [pa, pc] = [p[e], p[(e + 1) % 4]];
          hits.push([pa[0] + t * (pc[0] - pa[0]), pa[1] + t * (pc[1] - pa[1])]);
        }
      }
      for (let k = 0; k + 1 < hits.length; k += 2) {
        d += `M${X(hits[k][0]).toFixed(2)} ${Y(hits[k][1]).toFixed(2)}L${X(hits[k + 1][0]).toFixed(2)} ${Y(hits[k + 1][1]).toFixed(2)}`;
      }
    }
  }
  return d;
}

// the solver's mesh: grid lines plus the alternating diagonals of rect_mesh()
function meshPath(b, H) {
  const X = (r) => (r * MM).toFixed(2), Y = (z) => ((H - z) * MM).toFixed(2);
  let d = "";
  b.r.forEach((r) => { d += `M${X(r)} ${Y(b.z[0])}V${Y(b.z[b.z.length - 1])}`; });
  b.z.forEach((z) => { d += `M${X(b.r[0])} ${Y(z)}H${X(b.r[b.r.length - 1])}`; });
  for (let j = 0; j < b.z.length - 1; j++) {
    for (let i = 0; i < b.r.length - 1; i++) {
      d += (i + j) % 2 === 0
        ? `M${X(b.r[i])} ${Y(b.z[j])}L${X(b.r[i + 1])} ${Y(b.z[j + 1])}`
        : `M${X(b.r[i + 1])} ${Y(b.z[j])}L${X(b.r[i])} ${Y(b.z[j + 1])}`;
    }
  }
  return d;
}

function sample(b, r, z) {
  if (r < b.r[0] || r > b.r[b.r.length - 1] || z < b.z[0] || z > b.z[b.z.length - 1]) return null;
  const fi = ((r - b.r[0]) / (b.r[b.r.length - 1] - b.r[0])) * (b.r.length - 1);
  const fj = ((z - b.z[0]) / (b.z[b.z.length - 1] - b.z[0])) * (b.z.length - 1);
  const i = Math.min(Math.floor(fi), b.r.length - 2), j = Math.min(Math.floor(fj), b.z.length - 2);
  const u = fi - i, w = fj - j;
  return (1 - u) * (1 - w) * b.T[j][i] + u * (1 - w) * b.T[j][i + 1] + (1 - u) * w * b.T[j + 1][i] + u * w * b.T[j + 1][i + 1];
}

// One leg's field. `color(v)` maps a value to CSS; `levels` are isotherm values.
export function FieldPlot({ phase, geometry, oil, color, levels, mesh, unit = "°C", height = 520, marker = true, label }) {
  const svg = useRef();
  const gid = `oil${useId().replace(/:/g, "")}`;
  const [hover, setHover] = useState(null);
  const H = oil.height_m;
  const lv = geometry.lv, hv = geometry.hv;
  const r0 = lv.r_in_m - 0.016, r1 = hv.r_out_m + 0.022;
  const vb = [r0 * MM, -0.012 * MM, (r1 - r0) * MM, (H + 0.024) * MM];
  const blocks = phase.blocks;
  const imgs = useMemo(() => Object.fromEntries(Object.entries(blocks).map(([n, b]) => [n, blockImage(b, color)])),
    [blocks, color]);
  const isos = useMemo(() => Object.fromEntries(Object.entries(blocks).map(([n, b]) =>
    [n, levels.map((l) => isoPath(b, l, H)).join("")])), [blocks, levels, H]);
  const meshes = useMemo(() => (mesh ? Object.fromEntries(Object.entries(blocks).map(([n, b]) => [n, meshPath(b, H)])) : null),
    [blocks, mesh, H]);
  const hs = phase.hotspot;
  const move = (e) => {
    const pt = svg.current.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const q = pt.matrixTransform(svg.current.getScreenCTM().inverse());
    const r = q.x / MM, z = H - q.y / MM;
    for (const [n, b] of Object.entries(blocks)) {
      const v = sample(b, r, z);
      if (v !== null) { setHover({ n, r, z, v }); return; }
    }
    setHover(null);
  };
  const sw = { vectorEffect: "non-scaling-stroke" };
  return (
    <div className="flex flex-col items-center">
      {label}
      <svg ref={svg} viewBox={vb.join(" ")} style={{ height, maxWidth: "100%" }} onMouseMove={move}
        onMouseLeave={() => setHover(null)} role="img" aria-label="FEM temperature field of one phase leg">
        {/* core limb side and oil */}
        <rect x={r0 * MM} y={-12} width={(lv.r_in_m - 0.007 - r0) * MM} height={(H + 0.024) * MM} fill="#1b222b" />
        <text x={(r0 + 0.002) * MM} y={(H / 2) * MM} fontSize={7} fill="#7d8a9a"
          transform={`rotate(-90 ${(r0 + 0.006) * MM} ${(H / 2) * MM})`}>core limb</text>
        {Object.entries(blocks).map(([n, b]) => {
          const x = b.r[0] * MM, y = (H - b.z[b.z.length - 1]) * MM;
          const w = (b.r[b.r.length - 1] - b.r[0]) * MM, h = (b.z[b.z.length - 1] - b.z[0]) * MM;
          return (
            <g key={n}>
              <image href={imgs[n]} x={x} y={y} width={w} height={h} preserveAspectRatio="none" style={{ imageRendering: "auto" }} />
              {meshes && <path d={meshes[n]} stroke="#0b0f14" strokeOpacity={0.55} strokeWidth={0.5} fill="none" style={sw} />}
              <path d={isos[n]} stroke="#e2e8f0" strokeOpacity={0.75} strokeWidth={0.8} fill="none" style={sw} />
              <rect x={x} y={y} width={w} height={h} fill="none" stroke="#d7dee8" strokeOpacity={0.6} strokeWidth={0.8} style={sw} />
              <text x={x + w / 2} y={(H + 0.009) * MM} fontSize={6.5} fill="#7d8a9a" textAnchor="middle">{n.toUpperCase()}</text>
            </g>
          );
        })}
        {/* the prescribed oil profile, bottom to top, beside the HV winding */}
        <defs>
          <linearGradient id={gid} x1="0" y1="1" x2="0" y2="0">
            <stop offset="0" stopColor={unit === "°C" ? color(oil.bottom_c) : "#64748b"} />
            <stop offset="1" stopColor={unit === "°C" ? color(oil.top_c) : "#64748b"} />
          </linearGradient>
        </defs>
        <rect x={(hv.r_out_m + 0.008) * MM} y={0} width={5} height={H * MM} fill={`url(#${gid})`}
          stroke="#1f2a37" strokeWidth={0.6} style={sw} />
        <text x={(hv.r_out_m + 0.0105) * MM} y={-3} fontSize={6} fill="#7d8a9a" textAnchor="middle">oil</text>
        {marker && hs && (
          <g>
            <circle cx={hs.r_m * MM} cy={(H - hs.z_m) * MM} r={3.2} fill="none" stroke="#f8fafc" strokeWidth={1.4} style={sw} />
            <path d={`M${hs.r_m * MM - 6} ${(H - hs.z_m) * MM}h3.4M${hs.r_m * MM + 2.6} ${(H - hs.z_m) * MM}h3.4`}
              stroke="#f8fafc" strokeWidth={1.2} style={sw} />
          </g>
        )}
      </svg>
      <div className="text-xs tabular-nums text-dim h-4">
        {hover ? `${hover.n.toUpperCase()} r ${(hover.r * MM).toFixed(0)} mm, z ${(hover.z * MM).toFixed(0)} mm: ${f1(hover.v)} ${unit}` : "hover for a reading"}
      </div>
    </div>
  );
}

export function ScaleBar({ lo, hi, color, unit = "°C", note }) {
  const stops = Array.from({ length: 11 }, (_, k) => color(lo + (k / 10) * (hi - lo)));
  return (
    <div className="text-xs w-56">
      <div className="h-2.5 rounded" style={{ background: `linear-gradient(to right, ${stops.join(", ")})` }} />
      <div className="flex justify-between text-dim tabular-nums mt-0.5"><span>{f1(lo)} {unit}</span><span>{f1(hi)} {unit}</span></div>
      {note && <div className="text-dim mt-0.5">{note}</div>}
    </div>
  );
}

// common colour range across the phases shown, rounded to whole kelvin
export function rangeOf(fems) {
  let lo = Infinity, hi = -Infinity;
  fems.forEach((f) => Object.values(f.phases).forEach((p) => p && Object.values(p.blocks).forEach((b) => b.T.forEach((row) =>
    row.forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; })))));
  fems.forEach((f) => { lo = Math.min(lo, f.oil.bottom_c); hi = Math.max(hi, f.oil.top_c); });
  return [Math.floor(lo), Math.ceil(hi)];
}

export const levelsFor = (lo, hi) => {
  const s = nice(hi - lo);
  const out = [];
  for (let v = Math.ceil(lo / s) * s; v <= hi; v += s) out.push(v);
  return out;
};

export function Limits({ items }) {
  return (
    <ul className="text-xs text-dim list-disc ml-4 space-y-0.5">{items.map((x) => <li key={x}>{x}</li>)}</ul>
  );
}

// Simulate workspace: baseline, simulated and difference fields for one phase.
export function FemCompare({ res }) {
  const [ph, setPh] = useState(null);
  const [mesh, setMesh] = useState(false);
  const [lo, hi] = useMemo(() => (res ? rangeOf([res.baseline, res.simulated]) : [0, 1]), [res]);
  const color = useMemo(() => (v) => hotspotColor(v, { cool_c: lo, hot_c: hi }), [lo, hi]);
  const levels = useMemo(() => levelsFor(lo, hi), [lo, hi]);
  const phase = ph || res?.simulated.worst_phase || "R";
  const diff = res?.difference?.[phase];
  const span = useMemo(() => {
    if (!diff) return 1;
    let m = 0;
    Object.values(diff.blocks).forEach((b) => b.T.forEach((row) => row.forEach((v) => { m = Math.max(m, Math.abs(v)); })));
    return Math.max(Math.ceil(m), 1);
  }, [diff]);
  const dcolor = useMemo(() => (v) => diffColor(v, span), [span]);
  const dlevels = useMemo(() => levelsFor(-span, span).filter((v) => Math.abs(v) > 1e-9), [span]);
  if (!res) return <div className="p-6 text-dim text-sm">Solving the winding fields…</div>;
  const b = res.baseline.phases[phase], s = res.simulated.phases[phase];
  const dPhase = diff && { ...s, blocks: diff.blocks };
  const head = (title, p, extra) => (
    <div className="text-xs tabular-nums w-full mb-1">
      <b className="text-sm">{title}</b>
      <div>{p ? <>Hot-spot <b className="text-base">{f1(p.hotspot.c)} °C</b> <span className="text-dim">{f1(p.current_a)} A, {p.hotspot.winding.toUpperCase()}</span></> : "—"}</div>
      <div className="text-dim">{extra}</div>
    </div>
  );
  return (
    <div className="h-full overflow-y-auto px-4 pt-24 pb-4">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2 mb-2">
        <div>
          <div className="text-sm font-semibold">Winding field, baseline versus simulated (steady end state)</div>
          <div className="text-xs text-dim">{res.note} {res.simulated.mesh.elements_per_phase.toLocaleString()} elements per phase.</div>
        </div>
        <div className="flex gap-1 ml-auto items-center text-xs">
          Phase
          {["R", "Y", "B"].map((p) => (
            <button key={p} className={`twin-btn ${phase === p ? "twin-btn-primary" : ""}`} onClick={() => setPh(p)}>{p}</button>
          ))}
          <button className="twin-btn ml-2" onClick={() => setMesh(!mesh)} aria-pressed={mesh}>{mesh ? "Hide mesh" : "Show mesh"}</button>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div className="border border-edge rounded p-2">
          <FieldPlot phase={b} geometry={res.baseline.geometry} oil={res.baseline.oil} color={color} levels={levels} mesh={mesh} height={470}
            label={head("Baseline", b, `top oil ${f1(res.baseline.oil.top_c)} °C · C57.91 hot-spot ${f1(res.baseline.c57.hotspot_c)} °C`)} />
        </div>
        <div className="border border-accent rounded p-2">
          <FieldPlot phase={s} geometry={res.simulated.geometry} oil={res.simulated.oil} color={color} levels={levels} mesh={mesh} height={470}
            label={head("Simulated", s, `top oil ${f1(res.simulated.oil.top_c)} °C · C57.91 hot-spot ${f1(res.simulated.c57.hotspot_c)} °C`)} />
        </div>
        <div className="border border-edge rounded p-2">
          {dPhase ? (
            <FieldPlot phase={dPhase} geometry={res.simulated.geometry} oil={res.simulated.oil} color={dcolor} levels={dlevels} mesh={mesh}
              height={470} unit="K" marker={false}
              label={<div className="text-xs tabular-nums w-full mb-1"><b className="text-sm">Difference, simulated - baseline</b>
                <div>Hot-spot <b className="text-base">{diff.hotspot_k > 0 ? "+" : ""}{f1(diff.hotspot_k)} K</b></div>
                <div className="text-dim">isotherm step {dlevels.length > 1 ? dlevels[1] - dlevels[0] : span} K</div></div>} />
          ) : <div className="text-dim text-sm">No difference available.</div>}
        </div>
      </div>
      <div className="flex flex-wrap gap-6 mt-3">
        <ScaleBar lo={lo} hi={hi} color={color} note="baseline and simulated share this scale" />
        <ScaleBar lo={-span} hi={span} color={dcolor} unit="K" note="blue cooler, orange hotter" />
      </div>
      <div className="mt-3"><div className="text-xs font-semibold mb-1">Limits</div><Limits items={res.simulated.limits} /></div>
    </div>
  );
}

// The Digital twin "Heat map" view: R, Y, B side by side.
export default function HeatMapView({ fem, onResolve, live }) {
  const [mesh, setMesh] = useState(false);
  const [lo, hi] = useMemo(() => (fem ? rangeOf([fem]) : [0, 1]), [fem]);
  const color = useMemo(() => (v) => hotspotColor(v, { cool_c: lo, hot_c: hi }), [lo, hi]);
  const levels = useMemo(() => levelsFor(lo, hi), [lo, hi]);
  if (!fem) return <div className="p-6 text-dim text-sm">Solving the winding field…</div>;
  const step = levels.length > 1 ? levels[1] - levels[0] : 0;
  return (
    <div className="h-full overflow-y-auto px-4 pt-14 pb-4">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2 mb-2">
        <div>
          <div className="text-sm font-semibold">Winding temperature field, finite elements</div>
          <div className="text-xs text-dim tabular-nums">{fem.timestamp?.replace("T", " ")} · steady conduction, r-z section of each
            leg to scale · {fem.mesh.elements_per_phase.toLocaleString()} elements per phase · solved in {fem.solve_ms} ms</div>
        </div>
        <ScaleBar lo={lo} hi={hi} color={color} note={`isotherms every ${step} K`} />
        <div className="flex gap-2 ml-auto">
          <button className="twin-btn" onClick={() => setMesh(!mesh)} aria-pressed={mesh}>{mesh ? "Hide mesh" : "Show mesh"}</button>
          {live && <button className="twin-btn" onClick={onResolve}>Re-solve now</button>}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {["R", "Y", "B"].map((ph) => {
          const p = fem.phases[ph];
          if (!p) return <div key={ph} className="text-dim text-sm p-4">Phase {ph}: no current reading.</div>;
          const worst = fem.worst_phase === ph;
          return (
            <div key={ph} className={`border rounded px-2 py-2 ${worst ? "border-[#f97316]" : "border-edge"}`}>
              <FieldPlot phase={p} geometry={fem.geometry} oil={fem.oil} color={color} levels={levels} mesh={mesh}
                label={
                  <div className="text-xs tabular-nums w-full mb-1">
                    <div className="flex justify-between"><b className="text-sm">Phase {ph}{worst ? " · hottest" : ""}</b>
                      <span>{f1(p.current_a)} A · {Math.round(100 * p.load_pu)} %</span></div>
                    <div>Hot-spot <b className="text-base">{f1(p.hotspot.c)} °C</b> <span className="text-dim">
                      in {p.hotspot.winding.toUpperCase()}, {Math.round(100 * p.hotspot.z_frac)} % of height</span></div>
                    <div className="text-dim">C57.91, same top oil: {f1(fem.c57.per_phase_hotspot_c?.[ph] ?? fem.c57.hotspot_c)} °C
                      {p.H != null && <> · H {p.H.toFixed(2)}</>}</div>
                  </div>
                } />
              <table className="w-full text-xs tabular-nums mt-1"><tbody>
                {Object.entries(p.blocks).map(([n, b]) => (
                  <tr key={n} className="border-t border-edge">
                    <td className="py-0.5 text-dim">{n.toUpperCase()}</td>
                    <td className="text-right">{f1(b.p_w)} W</td>
                    <td className="text-right">mean {f1(b.mean_c)}</td>
                    <td className="text-right">max {f1(b.max_c)} °C</td>
                  </tr>
                ))}
              </tbody></table>
            </div>
          );
        })}
      </div>
      <div className="grid grid-cols-2 gap-4 mt-3">
        <div className="text-xs space-y-1">
          <div className="font-semibold text-fg">What is solved</div>
          <p className="text-dim">-div(k grad T) = q in each winding, axisymmetric, with q = that phase's I²R from the equivalent
            circuit over the winding volume. Every face that touches oil: -k dT/dn = h (T - T_oil(z)), h = {fem.calibration.h_w_m2k.toFixed(1)} W/m²K,
            calibrated once so the rated mean-winding to mean-oil gradient is {fem.calibration.g_rated_k} K.</p>
          <p className="text-dim tabular-nums">Oil: {f1(fem.oil.bottom_c)} °C at the bottom to {f1(fem.oil.top_c)} °C top oil (C57.91 at this
            instant); {fem.oil.assumption}. Conductor {fem.materials.conductor}; homogenised k_r / k_z: LV {fem.materials.k.lv.kr.toFixed(2)} / {fem.materials.k.lv.kz.toFixed(0)},
            HV {fem.materials.k.hv.kr.toFixed(2)} / {fem.materials.k.hv.kz.toFixed(0)} W/mK.</p>
        </div>
        <div>
          <div className="text-xs font-semibold text-fg mb-1">Limits</div>
          <Limits items={fem.limits} />
        </div>
      </div>
    </div>
  );
}
