import { useEffect, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../ui.jsx";
import Ranked from "./Ranked.jsx";
import { STATE, VIOLET } from "../twin/states.js";

// Engineering page: characteristic curves, losses and efficiency, and (later
// sections) diagnostics and corrective actions. Sentence case, tabular
// figures, colour only where it carries meaning.

const axis = { stroke: "#7d8a9a", fontSize: 11 };
const tip = { contentStyle: { background: "#121821", border: "1px solid #1f2a37", fontSize: 12 } };
const f = (v, d = 2) => (v === null || v === undefined ? "—" : typeof v === "number" ? v.toFixed(d) : v);

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex gap-1 border-b border-edge mb-4">
      {tabs.map(([k, label]) => (
        <button key={k} onClick={() => onChange(k)}
          className={`px-3 py-2 text-sm -mb-px border-b-2 ${value === k ? "border-accent text-fg" : "border-transparent text-dim hover:text-fg"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

function Card({ title, children, note }) {
  return (
    <section className="bg-panel border border-edge rounded-md p-4 mb-4">
      {title && <h3 className="text-base font-semibold mb-1">{title}</h3>}
      {note && <p className="text-xs text-dim mb-3">{note}</p>}
      {children}
    </section>
  );
}

function Definition({ c }) {
  return (
    <div className="text-xs space-y-1 mb-3">
      <p><b>Definition and test conditions.</b> {c.definition}</p>
      {c.not_modelled && <p className="text-dim"><b>Not modelled.</b> {c.not_modelled}</p>}
    </div>
  );
}

function Num({ label, value, onChange, step = 1, unit }) {
  return (
    <label className="text-xs flex flex-col">
      <span className="text-dim">{label} {unit && <span>({unit})</span>}</span>
      <input type="number" step={step} value={value} onChange={(e) => onChange(e.target.value)}
        className="bg-ink border border-edge rounded px-2 py-1 w-28 tabular-nums" />
    </label>
  );
}

// ---------------------------------------------------------------- curves
function BH({ c }) {
  const data = c.H_a_per_m.map((h, k) => ({ h: Math.max(h, 1), b: c.B_t[k] })).filter((x) => x.h >= 1);
  return (
    <Card title="B-H magnetisation curve" note={`Core steel: ${c.source}.`}>
      <Definition c={c} />
      <ResponsiveContainer width="100%" height={340}>
        <LineChart data={data} margin={{ top: 22, right: 16, bottom: 22, left: 0 }}>
          <CartesianGrid stroke="#1f2a37" />
          <XAxis dataKey="h" type="number" scale="log" domain={[1, 100000]} tick={axis}
            ticks={[1, 10, 100, 1000, 10000, 100000]} label={{ value: "H (A/m, log scale)", position: "insideBottom", offset: -8, fill: "#7d8a9a", fontSize: 11 }} />
          <YAxis tick={axis} domain={[0, 2.2]} label={{ value: "B (T)", angle: -90, position: "insideLeft", fill: "#7d8a9a", fontSize: 11 }} />
          <Tooltip {...tip} formatter={(v) => (typeof v === "number" ? v.toFixed(3) : v)} labelFormatter={(h) => `H ${Number(h).toFixed(1)} A/m`} />
          <ReferenceLine y={c.knee_t} stroke="#eab308" strokeDasharray="4 4"
            label={{ value: `saturation knee ~${c.knee_t} T`, fill: "#eab308", fontSize: 11, position: "insideBottomRight" }} />
          <Line dataKey="b" name="B" stroke="#22d3ee" dot={false} strokeWidth={2} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      <p className="text-xs text-dim">Below about 1.7 T a small field gives a large flux (relative permeability in the
        tens of thousands). Past the knee each extra tenth of a tesla needs several times more field.</p>
    </Card>
  );
}

function Excitation({ c, design, setDesign }) {
  const [logY, setLogY] = useState(true);
  const pct = c.rated.i0_pct / c.rated.i0_a;            // amperes -> % of rated line current
  const data = c.points.filter((p) => p.v_pct > 0).map((p) => ({ v: p.v_pct, i0: p.i0_pct, ic: p.ic_a * pct, b: p.b_max_t }));
  const wave = c.waveform.deg.map((d, k) => ({ d, r: c.waveform.rated_a[k], o: c.waveform.v120_a[k] }));
  const d = c.design;
  return (
    <Card title="Excitation characteristic (V-I, open circuit)" note={`${d.source} ${d.gap_source || ""}`}>
      <Definition c={c} />
      <div className="text-xs bg-ink border border-edge rounded p-2 mb-3 tabular-nums">
        B_max = V / (4.44 f N1 A) = {d.v_winding_rated_v.toFixed(0)} / (4.44 x 50 x {d.n1_turns.toFixed(0)} x {d.a_core_m2.toFixed(4)})
        {" "}= {d.b_rated_t.toFixed(3)} T at rated voltage · i_m(t) = [H(B(t)) l + B(t) g / mu0] / N1 with l = {d.l_core_m} m,
        g = {d.gap_mm} mm · I0 = sqrt(I_m^2 + I_c^2)
      </div>
      <div className="flex flex-wrap gap-3 mb-3 items-end">
        <Num label="HV turns N1" value={design.n1 ?? d.n1_turns.toFixed(0)} onChange={(v) => setDesign({ ...design, n1: v })} />
        <Num label="Core area" unit="cm²" step={1} value={design.area_cm2 ?? (d.a_core_m2 * 1e4).toFixed(1)} onChange={(v) => setDesign({ ...design, area_cm2: v })} />
        <Num label="Magnetic path" unit="m" step={0.05} value={design.path_m ?? d.l_core_m} onChange={(v) => setDesign({ ...design, path_m: v })} />
        <Num label="Joint gap" unit="mm" step={0.01} value={design.gap_mm ?? d.gap_mm} onChange={(v) => setDesign({ ...design, gap_mm: v })} />
        <button className="twin-btn" onClick={() => setDesign({})}>Reset design</button>
        <label className="text-xs flex items-center gap-1 ml-auto"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> log scale</label>
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 22, right: 16, bottom: 22, left: 0 }}>
          <CartesianGrid stroke="#1f2a37" />
          <XAxis dataKey="v" type="number" domain={[0, 130]} tick={axis} label={{ value: "Applied voltage (% of rated)", position: "insideBottom", offset: -8, fill: "#7d8a9a", fontSize: 11 }} />
          <YAxis tick={axis} scale={logY ? "log" : "auto"} domain={logY ? [0.01, "auto"] : [0, "auto"]} allowDataOverflow
            label={{ value: "No-load current (% of rated)", angle: -90, position: "insideLeft", fill: "#7d8a9a", fontSize: 11 }} />
          <Tooltip {...tip} formatter={(v) => (typeof v === "number" ? `${v.toFixed(3)} %` : v)} labelFormatter={(v) => `${v} % voltage`} />
          <ReferenceLine x={100} stroke="#7d8a9a" strokeDasharray="3 3" label={{ value: "rated", fill: "#7d8a9a", fontSize: 11, position: "top" }} />
          {c.knee_v_pct && <ReferenceLine x={c.knee_v_pct} stroke="#eab308" strokeDasharray="4 4" label={{ value: "B = knee", fill: "#eab308", fontSize: 11, position: "top" }} />}
          <Line dataKey="i0" name="I0 total" stroke="#22d3ee" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line dataKey="ic" name="I_c core-loss part" stroke="#94a3b8" dot={false} strokeDasharray="4 3" isAnimationActive={false} />
          <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
        </LineChart>
      </ResponsiveContainer>
      <p className="text-sm my-3">{c.explanation}</p>
      <div className="text-xs mb-1">Magnetising current over one cycle (line A)</div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={wave} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#1f2a37" />
          <XAxis dataKey="d" type="number" domain={[0, 360]} ticks={[0, 90, 180, 270, 360]} tick={axis} unit="°" />
          <YAxis tick={axis} />
          <Tooltip {...tip} />
          <Line dataKey="r" name="at 100 % voltage" stroke="#94a3b8" dot={false} isAnimationActive={false} />
          <Line dataKey="o" name="at 120 % voltage" stroke="#22d3ee" dot={false} isAnimationActive={false} />
          <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

function Transfer({ c, opts, setOpts }) {
  const data = c.points.map((p) => ({ v1: p.v1_line_v / 1000, v2: p.v2_line_v, ideal: p.v1_line_v / c.no_load_ratio }));
  return (
    <Card title="Voltage-transfer characteristic" note="From the per-phase equivalent circuit (illustrative impedance).">
      <Definition c={c} />
      <div className="flex flex-wrap gap-3 mb-3 items-end">
        <Num label="Load" unit="% of rated" value={opts.load_pct} onChange={(v) => setOpts({ ...opts, load_pct: v })} />
        <Num label="Power factor" step={0.05} value={opts.pf} onChange={(v) => setOpts({ ...opts, pf: v })} />
        <label className="text-xs flex flex-col"><span className="text-dim">Power factor sense</span>
          <select className="bg-ink border border-edge rounded px-2 py-1" value={opts.lagging ? "lag" : "lead"}
            onChange={(e) => setOpts({ ...opts, lagging: e.target.value === "lag" })}>
            <option value="lag">lagging</option><option value="lead">leading</option>
          </select>
        </label>
        <span className="text-xs tabular-nums ml-auto">Regulation at rated voltage: <b>{f(c.regulation_pct_at_rated_v)} %</b></span>
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 22, right: 16, bottom: 22, left: 0 }}>
          <CartesianGrid stroke="#1f2a37" />
          <XAxis dataKey="v1" type="number" domain={[0, "dataMax"]} tick={axis} label={{ value: "HV line voltage V1 (kV)", position: "insideBottom", offset: -8, fill: "#7d8a9a", fontSize: 11 }} />
          <YAxis tick={axis} label={{ value: "LV line voltage V2 (V)", angle: -90, position: "insideLeft", fill: "#7d8a9a", fontSize: 11 }} />
          <Tooltip {...tip} formatter={(v) => (typeof v === "number" ? v.toFixed(1) : v)} />
          <ReferenceLine x={11} stroke="#7d8a9a" strokeDasharray="3 3" label={{ value: "rated", fill: "#7d8a9a", fontSize: 11, position: "top" }} />
          <Line dataKey="ideal" name="no load, V1 / a" stroke="#94a3b8" strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          <Line dataKey="v2" name="at this load and power factor" stroke="#22d3ee" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

function Curves({ scen }) {
  const [tab, setTab] = useState("excitation");
  const [design, setDesign] = useState({});
  const [opts, setOpts] = useState({ load_pct: 100, pf: 0.9, lagging: true });
  const [c, setC] = useState(null);
  useEffect(() => {
    const q = new URLSearchParams();
    Object.entries(design).forEach(([k, v]) => v !== "" && v !== undefined && q.set(k, v));
    q.set("load_pct", opts.load_pct); q.set("pf", opts.pf); q.set("lagging", opts.lagging);
    const id = setTimeout(() => api(`${scen}/curves?${q}`).then(setC).catch(() => {}), 200);
    return () => clearTimeout(id);
  }, [scen, design, opts]);
  return (
    <div>
      <Tabs value={tab} onChange={setTab} tabs={[["bh", "B-H curve"], ["excitation", "Excitation (V-I)"], ["transfer", "Voltage transfer"]]} />
      {!c ? <p className="text-dim text-sm">Computing…</p>
        : tab === "bh" ? <BH c={c.bh} />
          : tab === "excitation" ? <Excitation c={c.excitation} design={design} setDesign={setDesign} />
            : <Transfer c={c.transfer} opts={opts} setOpts={setOpts} />}
    </div>
  );
}

// ---------------------------------------------------------------- losses
function Losses({ scen, cursor }) {
  const [L, setL] = useState(null);
  useEffect(() => { api(`${scen}/losses${scen === "live" ? "" : `?t=${cursor}`}`).then(setL).catch(() => {}); }, [scen, cursor]);
  if (!L) return <p className="text-dim text-sm">Computing…</p>;
  const maxP = Math.max(...Object.values(L.per_phase).map((x) => x.p_cu_w || 0), L.core_loss_w, 1);
  const eff = L.efficiency_curve.map((p) => ({ k: p.load_pct, e: p.efficiency_pct }));
  return (
    <div>
      <Card title={`Loss breakdown at ${L.timestamp.replace("T", " ")}`} note={L.notes.join(" ")}>
        <div className="grid grid-cols-4 gap-3 text-sm">
          {["R", "Y", "B"].map((ph) => (
            <div key={ph}>
              <div className="text-xs text-dim">Copper loss, {ph} phase</div>
              <div className="tabular-nums text-lg">{f(L.per_phase[ph].p_cu_w, 0)} W</div>
              <div className="text-xs text-dim tabular-nums">{f(L.per_phase[ph].current_a, 1)} A² x {L.req_lv_ohm} ohm</div>
              <div className="h-2 bg-ink rounded mt-1"><div className="h-2 rounded bg-accent" style={{ width: `${100 * (L.per_phase[ph].p_cu_w || 0) / maxP}%` }} /></div>
            </div>
          ))}
          <div>
            <div className="text-xs text-dim">Core loss</div>
            <div className="tabular-nums text-lg">{f(L.core_loss_w, 0)} W</div>
            <div className="text-xs text-dim">V² / Rc, three phases</div>
            <div className="h-2 bg-ink rounded mt-1"><div className="h-2 rounded bg-dim" style={{ width: `${100 * L.core_loss_w / maxP}%` }} /></div>
          </div>
        </div>
        <p className="text-sm mt-3 tabular-nums">Load {f(L.load_pct, 1)} % · total loss {f(L.copper_loss_w + L.core_loss_w, 0)} W ·
          efficiency {f(L.efficiency_pct, 2)} % (at power factor {L.pf_assumed}, assumed)</p>
      </Card>
      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Efficiency against load" note={`Peaks where copper loss equals core loss: ${L.max_efficiency.rule}.`}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={eff} margin={{ top: 22, right: 16, bottom: 22, left: 0 }}>
              <CartesianGrid stroke="#1f2a37" />
              <XAxis dataKey="k" type="number" domain={[0, 150]} tick={axis} label={{ value: "Load (% of rated)", position: "insideBottom", offset: -8, fill: "#7d8a9a", fontSize: 11 }} />
              <YAxis tick={axis} domain={["auto", "auto"]} />
              <Tooltip {...tip} formatter={(v) => `${Number(v).toFixed(2)} %`} />
              <ReferenceLine x={L.max_efficiency.load_pct} stroke="#eab308" strokeDasharray="4 4"
                label={{ value: `P_cu = P_core (${L.max_efficiency.load_pct} %)`, fill: "#eab308", fontSize: 11, position: "insideBottomRight" }} />
              <ReferenceLine x={L.load_pct} stroke="#22d3ee" strokeDasharray="2 2" label={{ value: "now", fill: "#22d3ee", fontSize: 11, position: "top" }} />
              <Line dataKey="e" name="efficiency" stroke="#22d3ee" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Regulation against power factor" note={`At the present load (${f(L.load_pct, 0)} %). Positive: the LV voltage falls under load.`}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={L.regulation_curve} margin={{ top: 22, right: 16, bottom: 22, left: 0 }}>
              <CartesianGrid stroke="#1f2a37" />
              <XAxis dataKey="label" tick={axis} interval={1} />
              <YAxis tick={axis} unit=" %" />
              <Tooltip {...tip} formatter={(v) => `${Number(v).toFixed(2)} %`} />
              <ReferenceLine y={0} stroke="#475569" />
              <Line dataKey="regulation_pct" name="regulation" stroke="#22d3ee" dot isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>
      <Card title="Rated values and the present operating point" note="Rated column at the assumed power factor; the design copper and core loss (1,300 W and 260 W) are defined at unity power factor.">
        <table className="w-full text-sm tabular-nums">
          <thead><tr className="text-dim text-left text-xs"><th className="font-normal">Quantity</th><th className="font-normal text-right">Rated</th><th className="font-normal text-right">Present</th></tr></thead>
          <tbody>{L.rated_vs_present.map((r) => (
            <tr key={r.quantity} className="border-t border-edge"><td className="py-1">{r.quantity} <span className="text-dim text-xs">{r.unit}</span></td>
              <td className="text-right">{f(r.rated, 2)}</td><td className="text-right">{f(r.present, 2)}</td></tr>
          ))}</tbody>
        </table>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- diagnostics
function Diagnostics({ scen, cursor, version }) {
  const [d, setD] = useState(null);
  useEffect(() => { api(`${scen}/diagnose${scen === "live" ? "" : `?t=${cursor}`}`).then(setD).catch(() => {}); }, [scen, cursor, version]);
  if (!d) return <p className="text-dim text-sm">Computing…</p>;
  return (
    <div>
      <p className="text-sm text-dim mb-3">Evidence for each component group at {d.timestamp.replace("T", " ")}, and ranked
        hypotheses for every abnormal finding. Rankings come from stated rules on computed quantities; they are ordinal,
        not probabilities. A finding is placed on a component only where there is evidence for that component.</p>
      {d.groups.map((g) => {
        const st = STATE[g.state] || STATE.grey;
        return (
          <Card key={g.group} title={g.group}>
            <div className="flex items-center gap-3 text-xs mb-2">
              <span className="px-2 py-0.5 rounded" style={{ border: `1px solid ${g.state === "sensor" ? VIOLET : st.color}`, color: g.state === "grey" ? "#b6bdb9" : g.state === "sensor" ? VIOLET : st.color }}>{st.label}</span>
              <span className="text-dim">Evidence coverage</span>
              <div className="w-32 h-2 bg-ink rounded"><div className="h-2 rounded bg-accent" style={{ width: `${100 * g.evidence_coverage}%` }} /></div>
              <span className="tabular-nums text-dim">{Math.round(100 * g.evidence_coverage)} % of the listed evidence is available</span>
            </div>
            <div className="grid md:grid-cols-2 gap-3 text-xs mb-2">
              <div><div className="font-semibold mb-0.5">Available</div>{g.evidence_available.length ? g.evidence_available.join("; ") : <span className="text-dim">none</span>}</div>
              <div><div className="font-semibold mb-0.5">Missing</div><span className="text-dim">{g.evidence_missing.join("; ")}</span></div>
            </div>
            {g.findings.length ? g.findings.map((f) => <Ranked key={f.condition} finding={f} />)
              : <p className="text-xs text-dim">No abnormal finding in this group.</p>}
          </Card>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- structured agent output
function Agents({ scen, cursor, version }) {
  const [d, setD] = useState(null);
  useEffect(() => { api(`${scen}/agents${scen === "live" ? "" : `?t=${cursor}`}`).then(setD).catch(() => {}); }, [scen, cursor, version]);
  if (!d) return <p className="text-dim text-sm">Running the agents…</p>;
  const names = { observation: "Observation", physics: "Physics", diagnostic: "Diagnostic", recommendation: "Recommendation" };
  const note = { observation: "ingestion and data-quality flags", physics: "circuit, thermal and magnetic calculations",
    diagnostic: "the detector, residuals and ranked hypotheses", recommendation: "rules and the catalogue" };
  return (
    <div>
      <p className="text-sm text-dim mb-3">{d.method}. Each record has the same fields: asset, component, observations,
        calculations, deviations, hypotheses, evidence coverage, recommended action and additional evidence required.</p>
      {Object.entries(d.agents).map(([k, recs]) => (
        <Card key={k} title={`${names[k]} agent`} note={`Wraps ${note[k]}. ${recs.length} record(s).`}>
          {recs.filter((r) => k !== "diagnostic" || r.state !== "grey").map((r, j) => (
            <details key={j} className="border-t border-edge py-1.5">
              <summary className="text-sm cursor-pointer">
                <span className="text-dim tabular-nums">{r.component_id || "asset"}</span> · {r.explanation}
              </summary>
              <pre className="text-[11px] bg-ink border border-edge rounded p-2 mt-1 overflow-x-auto">{JSON.stringify(r, null, 2)}</pre>
            </details>
          ))}
          {k === "diagnostic" && <p className="text-xs text-dim mt-1">Grey (not assessed) components are hidden here; they are in the JSON from /api/{scen}/agents.</p>}
        </Card>
      ))}
    </div>
  );
}

export default function EngineeringPage({ scen, cursor, version, header, extraTabs = [] }) {
  const [tab, setTab] = useState("curves");
  const tabs = [["curves", "Characteristic curves"], ["losses", "Losses and efficiency"], ["diagnostics", "Diagnostics"],
    ["agents", "Structured output"],
    ...extraTabs.map((t) => [t.key, t.label])];
  const extra = extraTabs.find((t) => t.key === tab);
  return (
    <div className="twin-ui min-h-screen">
      {header}
      <div className="max-w-6xl mx-auto px-4 py-4">
        <Tabs tabs={tabs} value={tab} onChange={setTab} />
        {tab === "curves" ? <Curves scen={scen} /> : tab === "losses" ? <Losses scen={scen} cursor={cursor} />
          : tab === "diagnostics" ? <Diagnostics scen={scen} cursor={cursor} version={version} />
            : tab === "agents" ? <Agents scen={scen} cursor={cursor} version={version} /> : extra?.render()}
      </div>
    </div>
  );
}
