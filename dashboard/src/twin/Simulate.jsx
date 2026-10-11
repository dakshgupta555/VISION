import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import Schematic from "./Schematic.jsx";
import { FemCompare } from "./HeatMap.jsx";
import { webglAvailable } from "./states.js";
import { CONDITIONS, setSilica, useSilica } from "./silica.jsx";
import { Badge } from "../overview/Explain.jsx";

const Scene = lazy(() => import("./Scene.jsx"));

// Simulate workspace: an isolated copy of the observed baseline. Everything
// on this page is hypothetical and says so; the observed data are untouched.

const fmt = (v, d = 1) => {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return v;
  const x = Math.abs(v) < 0.5 * 10 ** -d ? 0 : v;      // no "-0.00"
  return x.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
};
const axis = { stroke: "var(--chart-axis)", fontSize: 14 };

function Section({ title, children }) {
  return (
    <section className="border-b border-edge px-4 py-3">
      <h3 className="text-sm font-semibold mb-2">{title}</h3>
      {children}
    </section>
  );
}

function Compare({ r, k, label, unit, limit }) {
  const data = r.baseline.series.hours.map((h, j) => ({ h, base: r.baseline.series[k][j], sim: r.simulated.series[k][j] }));
  return (
    <div className="mb-3">
      <div className="text-xs mb-1">{label} <span className="text-dim">({unit})</span></div>
      <ResponsiveContainer width="100%" height={130}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="h" tick={axis} unit=" h" minTickGap={30} />
          <YAxis tick={axis} domain={["auto", "auto"]} />
          <Tooltip contentStyle={{ background: "var(--tip-bg)", border: "1px solid var(--color-edge)", borderRadius: 8, fontSize: 14 }} />
          {limit && <ReferenceLine y={limit} stroke="var(--color-alarm)" strokeDasharray="6 4" />}
          <Line dataKey="base" name="Baseline" stroke="var(--chart-2)" strokeDasharray="5 4" dot={false} isAnimationActive={false} />
          <Line dataKey="sim" name="Simulated" stroke="var(--chart-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Legend wrapperStyle={{ fontSize: 14 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// Input change -> equation -> intermediate values -> outputs. Every number is
// taken from the backend's baseline and simulated points; only differences of
// backend values are formed here, no physics is computed.
function Chain({ r, silica }) {
  const b = r.baseline.point, s = r.simulated.point, bi = r.inputs.baseline, si = r.inputs.simulated;
  const rise = (p, i) => (p.steady_top_oil_c === null || p.steady_top_oil_c === undefined ? null : p.steady_top_oil_c - i.ambient_c);
  const grad = (p) => p.steady_hotspot_c - p.steady_top_oil_c;
  const rows = [
    ["Load", "% of rated", bi.load_pct, si.load_pct, 1, "input"],
    ["Copper loss", "W", b.p_cu_w, s.p_cu_w, 0, "P = I²R, resistance from the equivalent circuit"],
    ["Core loss", "W", b.p_core_w, s.p_core_w, 0, "P = V²/Rc, from the applied voltage"],
    ["Loss ratio to rated", "pu", b.loss_ratio, s.loss_ratio, 3, "(Pcu + Pcore) / (Pcu,R + Pcore,R)"],
    ["Rth multiplier (cooling factor)", "x", b.cooling_factor, s.cooling_factor, 3, "1 / effectiveness: the oil rise scales with Rth"],
    ["Steady top-oil rise", "K", rise(b, bi), rise(s, si), 1, "cooling factor × rise at rated × loss ratio^n"],
    ["Steady winding gradient", "K", grad(b), grad(s), 1, "gradient at rated × (worst-phase Pcu ratio)^m"],
    ["Ambient", "°C", bi.ambient_c, si.ambient_c, 1, "input"],
    ["Steady hot-spot", "°C", b.steady_hotspot_c, s.steady_hotspot_c, 1, "ambient + oil rise + gradient"],
    ["Ageing factor F_AA", "pu", b.steady_faa, s.steady_faa, 2, "exp(15000/383 − 15000/(θ + 273))"],
    ["Life cost", "₹/day", b.life_cost_inr_per_day, s.life_cost_inr_per_day, 0, "F_AA × cost per hour of life × 24"],
  ];
  return (
    <>
      <table className="w-full text-xs tabular-nums">
        <thead><tr className="text-dim text-left"><th className="font-normal">Step</th>
          <th className="font-normal text-right">Baseline</th><th className="font-normal text-right">Simulated</th></tr></thead>
        <tbody>
          {rows.map(([lab, u, x, y, d, how]) => (
            <tr key={lab} className="border-t border-edge align-top">
              <td className="py-0.5 pr-1">{lab} <span className="text-dim">{u}</span><div className="text-dim">{how}</div></td>
              <td className="text-right">{fmt(x, d)}</td>
              <td className={`text-right ${x !== y ? "text-accent font-semibold" : ""}`}>{fmt(y, d)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 text-xs">
        <div className="font-semibold">Moisture pathway (separate from the thermal chain)</div>
        {silica.rec ? silica.rec.chain.map((c) => (
          <div key={c.key} className="flex justify-between gap-2 border-t border-edge py-0.5">
            <span>{c.label}</span>
            <span className="text-right">{c.value} <Badge p={c.kind} /></span>
          </div>
        )) : <span className="text-dim">Loading…</span>}
        <p className="text-dim mt-1">The gel setting changes only this pathway. Hot-spot, top oil and ageing above are
          computed from load, ambient and cooling and do not change with it.</p>
      </div>
    </>
  );
}

export default function SimulateWorkspace({ scen, cursor, timestamp, onExit }) {
  const [inputs, setInputs] = useState(null);       // edits on top of the baseline
  const [r, setR] = useState(null);
  const [sel, setSel] = useState(null);
  const [xray, setXray] = useState(true);
  const [err, setErr] = useState(null);
  const gl = useMemo(() => webglAvailable(), []);
  const silica = useSilica();
  const silica0 = useRef(silica.condition);       // the baseline copy keeps the condition it opened with
  useEffect(() => () => setSilica(silica0.current), []);   // leaving the what-if never changes the observed view
  const timer = useRef(null);
  const [heat, setHeat] = useState(false);
  const [femR, setFemR] = useState(null);
  const runFem = (inp) => fetch(`/api/${scen}/fem/simulate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ t: scen === "live" ? null : cursor, inputs: inp || {} }),
  }).then((x) => (x.ok ? x.json() : null)).then((j) => j && setFemR(j)).catch(() => {});
  const run = (inp) => (runFem(inp), fetch(`/api/${scen}/simulate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ t: scen === "live" ? null : cursor, inputs: inp || {} }),
  }).then(async (x) => { const j = await x.json(); if (!x.ok) throw new Error(j.detail); return j; })
    .then((j) => { setR(j); setErr(null); }).catch((e) => setErr(String(e.message || e))));
  // the baseline is frozen when the workspace opens
  useEffect(() => { run({}); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (inputs === null) return undefined;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => run(inputs), 150);
    return () => clearTimeout(timer.current);
  }, [inputs]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!r) return <div className="p-6 text-dim text-sm">{err || "Preparing an isolated copy of the baseline…"}</div>;
  const spec = r.inputs.spec, base = r.inputs.baseline, sim = r.inputs.simulated;
  const comps = Object.fromEntries(r.components.components.map((c) => [c.id, c]));
  const set = (k, v) => setInputs({ ...(inputs || {}), [k]: v });
  const changed = Object.keys(spec).filter((k) => Math.abs((sim[k] ?? 0) - (base[k] ?? 0)) > 1e-9);
  const c = sel ? comps[sel] : null;

  return (
    <div className="twin-ui h-screen flex flex-col">
      <header className="flex items-center gap-3 px-4 py-2 border-b-4 border-fg bg-panel">
        <span className="px-3 py-1 rounded-full bg-fg text-panel text-sm font-semibold">Simulated</span>
        <span className="text-sm">Isolated copy of the observed state at <b className="tabular-nums">{r.baseline_timestamp.replace("T", " ")}</b>.
          Observed data are not changed.</span>
        <div className="ml-auto flex gap-2">
          <button className="twin-btn" onClick={() => { setInputs({}); setSilica(silica0.current); }}>Reset to baseline</button>
          <button className="twin-btn twin-btn-primary" onClick={onExit}>Exit simulation</button>
        </div>
      </header>
      <div className="flex-1 grid grid-cols-[19rem_1fr_27rem] min-h-0">
        <aside className="border-r border-edge overflow-y-auto">
          <Section title="Inputs">
            {Object.entries(spec).map(([k, s]) => (
              <label key={k} className="block text-xs mb-3">
                <div className="flex justify-between">
                  <span className={changed.includes(k) ? "text-accent" : ""}>{s.label}</span>
                  <span className="tabular-nums">{fmt(sim[k], s.step < 0.1 ? 2 : 1)} {s.unit}</span>
                </div>
                <input type="range" min={s.min} max={s.max} step={s.step} value={sim[k]}
                  className="w-full" onChange={(e) => set(k, Number(e.target.value))} />
                <div className="text-dim tabular-nums">baseline {fmt(base[k], s.step < 0.1 ? 2 : 1)}</div>
              </label>
            ))}
            <label className="block text-xs mb-3">
              <div className="flex justify-between">
                <span className={Math.abs(sim.cooling_effectiveness - base.cooling_effectiveness) > 1e-9 ? "text-accent" : ""}>
                  Thermal resistance Rth</span>
                <span className="tabular-nums">{fmt(1 / sim.cooling_effectiveness, 2)} x design</span>
              </div>
              <input type="range" min={0.85} max={2} step={0.05} aria-label="Thermal resistance multiplier"
                value={Math.min(2, Math.max(0.85, 1 / sim.cooling_effectiveness))} className="w-full"
                onChange={(e) => set("cooling_effectiveness", 1 / Number(e.target.value))} />
              <div className="text-dim">baseline {fmt(1 / base.cooling_effectiveness, 2)} x design. The reciprocal of
                cooling effectiveness above: the same input shown the other way round.</div>
            </label>
            <label className="block text-xs mb-1">
              <div className="flex justify-between"><span>Silica-gel condition</span><Badge p="ASSUMED" /></div>
              <select value={silica.condition} onChange={(e) => setSilica(e.target.value)}
                aria-label="Simulated silica-gel condition" className="twin-btn w-full mt-1">
                {CONDITIONS.map((k) => (
                  <option key={k} value={k}>{{ unknown: "Unknown", effective: "Effective", partial: "Partially exhausted", saturated: "Saturated" }[k]}</option>
                ))}
              </select>
              <div className="text-dim">Scenario, not a measurement. Affects the moisture pathway only.</div>
            </label>
          </Section>
          <Section title="Calculation chain">
            <Chain r={r} silica={silica} />
          </Section>
          <Section title="Illustrative parameters">
            <p className="text-xs text-dim">No test data for this unit, so these are typical values and editable above:
              {" "}{r.illustrative.join(", ")}.</p>
            <p className="text-xs text-dim mt-2 tabular-nums">Oil model as a thermal RC: Rth {(r.thermal_rc.Rth_k_per_w * 1000).toFixed(1)} K/kW,
              Cth {(r.thermal_rc.Cth_j_per_k / 1e6).toFixed(2)} MJ/K, tau = Rth x Cth = {r.thermal_rc.tau_oil_h} h, exponent n = {r.thermal_rc.n}.</p>
          </Section>
          <Section title="Not simulated">
            {r.unsupported.map((u) => (
              <div key={u.scenario} className="text-xs mb-2">
                <b>{u.scenario}.</b> Would need: {u.needs.join("; ")}. {u.cannot}
              </div>
            ))}
          </Section>
        </aside>

        <main className="relative min-h-0 ring-4 ring-inset ring-accent">
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 whitespace-nowrap px-3 py-1 rounded-full bg-fg text-panel text-sm font-semibold">
            Simulated: predicted states after {r.horizon_h} h, not the observed transformer
          </div>
          <div className="absolute top-3 left-3 z-10 flex gap-2">
            <button className={`twin-btn ${heat ? "twin-btn-primary" : ""}`} onClick={() => setHeat(!heat)}>
              {heat ? "Back to model" : "Heat map"}</button>
            {!heat && gl && <button className="twin-btn" onClick={() => setXray(!xray)}>{xray ? "Solid tank" : "X-ray"}</button>}
          </div>
          {heat ? <FemCompare res={femR} /> : gl ? (
            <Suspense fallback={<div className="p-6 text-dim text-sm">Loading 3D…</div>}>
              <Scene comps={comps} scale={r.components.scale} sel={sel} setSel={setSel} xray={xray} flyTo={null} resetKey={0}
                theme={document.documentElement.dataset.theme} silica={silica} />
            </Suspense>
          ) : <Schematic comps={comps} scale={r.components.scale} sel={sel} setSel={setSel} />}
          {c && (
            <div className="absolute bottom-3 left-3 right-3 z-10 twin-tip" style={{ width: "auto" }}>
              <b>{c.name}</b>: {c.reason}
            </div>
          )}
        </main>

        <aside className="border-l border-edge overflow-y-auto">
          <Section title="Baseline versus simulated, next 24 h">
            <Compare r={r} k="hotspot_c" label="Hot-spot" unit="°C" limit={110} />
            <Compare r={r} k="top_oil_c" label="Top oil" unit="°C" />
            <Compare r={r} k="aged_h" label="Insulation ageing, cumulative" unit="equivalent hours" />
          </Section>
          <Section title="Changes">
            <table className="w-full text-xs tabular-nums">
              <thead><tr className="text-dim text-left"><th className="font-normal">Quantity</th>
                <th className="font-normal text-right">Baseline</th><th className="font-normal text-right">Simulated</th>
                <th className="font-normal text-right">Change</th></tr></thead>
              <tbody>
                {r.delta.map((d) => (
                  <tr key={d.key} className="border-t border-edge">
                    <td className="py-0.5 pr-1">{d.label} <span className="text-dim">{d.unit}</span></td>
                    <td className="text-right">{fmt(d.baseline, d.nd)}</td>
                    <td className="text-right">{fmt(d.simulated, d.nd)}</td>
                    <td className={`text-right ${d.delta ? "text-accent" : "text-dim"}`}>{d.delta == null ? "—" : `${d.delta > 0 ? "+" : ""}${fmt(d.delta, d.nd)}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
          <Section title="Why it changed">
            <ul className="text-xs list-disc ml-4 space-y-1">{r.explanations.map((x) => <li key={x}>{x}</li>)}</ul>
          </Section>
          <Section title="Recommended actions">
            <ul className="text-xs list-disc ml-4 space-y-1">{r.actions.map((x) => <li key={x}>{x}</li>)}</ul>
          </Section>
          <Section title="Limitations">
            <ul className="text-xs list-disc ml-4 space-y-1 text-dim">{r.limits.map((x) => <li key={x}>{x}</li>)}</ul>
          </Section>
        </aside>
      </div>
    </div>
  );
}
