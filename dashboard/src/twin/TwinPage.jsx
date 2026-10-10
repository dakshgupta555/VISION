import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { api } from "../ui.jsx";
import { FOCUS } from "./focus.js";
import Schematic from "./Schematic.jsx";
import { hotspotColor, STATE, VIOLET, webglAvailable } from "./states.js";

const Scene = lazy(() => import("./Scene.jsx"));
const INSIDE = /^(core$|oil$|lv_winding|hv_winding)/;    // parts inside the tank (not oil_pocket)

// The digital twin workspace: the transformer in the centre, panels docked at
// the sides. Sentence case, tabular figures, colour only for status.

// numbers at a precision suited to their size; text passes through
const n = (v) => {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return v;
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
};

function Dock({ title, children, className = "" }) {
  return (
    <section className={`border-b border-edge px-4 py-3 ${className}`}>
      {title && <h3 className="text-sm font-semibold text-fg mb-2">{title}</h3>}
      {children}
    </section>
  );
}

function Dot({ state }) {
  const s = STATE[state] || STATE.grey;
  return <span className="inline-block w-2.5 h-2.5 rounded-full mr-2 align-middle"
    style={{ background: s.color, boxShadow: state === "sensor" ? `0 0 0 2px ${VIOLET}` : "none" }} />;
}

function Legend({ scale }) {
  return (
    <Dock title="Legend">
      <ul className="space-y-1 text-xs">
        {["green", "yellow", "red", "grey", "sensor"].map((k) => (
          <li key={k}><Dot state={k} />{STATE[k].label}</li>
        ))}
        <li className="text-dim">A red asset fault pulses at most once a second (a steady outline with reduced motion).</li>
      </ul>
      <div className="mt-3 text-xs">
        <div className="mb-1">Winding hot-spot</div>
        <div className="h-2.5 rounded" style={{
          background: `linear-gradient(to right, ${hotspotColor(scale.cool_c, scale)}, ${hotspotColor(scale.hot_c, scale)})`,
        }} />
        <div className="flex justify-between text-dim tabular-nums mt-0.5">
          <span>{scale.cool_c} °C</span><span>{scale.hot_c} °C</span>
        </div>
        <div className="text-dim mt-1">Cool grey to orange; red is kept for alarms.</div>
      </div>
    </Dock>
  );
}

function ComponentList({ list, sel, setSel }) {
  const groups = useMemo(() => {
    const g = {};
    list.forEach((c) => { (g[c.group] = g[c.group] || []).push(c); });
    return g;
  }, [list]);
  return (
    <Dock title="Components">
      {Object.entries(groups).map(([g, cs]) => (
        <div key={g} className="mb-2">
          <div className="text-xs text-dim mb-1">{g[0].toUpperCase() + g.slice(1)}</div>
          <ul className="text-xs space-y-0.5">
            {cs.map((c) => (
              <li key={c.id}>
                <button onClick={() => setSel(c.id)}
                  className={`w-full text-left px-1 py-0.5 rounded ${sel === c.id ? "bg-edge" : "hover:bg-edge/50"}`}>
                  <Dot state={c.state} />{c.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Dock>
  );
}

function Inspector({ c, simulated }) {
  if (!c) {
    return (
      <Dock title="Inspector">
        <p className="text-sm text-dim">Select a component in the model or the list. Everything shown here is
          generated from the analytics at this moment.</p>
      </Dock>
    );
  }
  const st = STATE[c.state] || STATE.grey;
  const Section = ({ title, children }) => (
    <div className="mt-3"><div className="text-xs font-semibold text-fg mb-1">{title}</div>{children}</div>
  );
  const Rows = ({ rows }) => (
    <table className="w-full text-xs"><tbody>
      {rows.map((o, k) => (
        <tr key={k} className="border-t border-edge">
          <td className="py-0.5 pr-2 text-dim">{o.label}</td>
          <td className="py-0.5 text-right tabular-nums">{n(o.value)} {o.unit}</td>
        </tr>
      ))}
    </tbody></table>
  );
  return (
    <Dock title={c.name}>
      <div className="text-sm">
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs mr-2"
          style={{ border: `1px solid ${c.state === "sensor" ? VIOLET : st.color}`, color: c.state === "grey" ? "#b6bdb9" : c.state === "sensor" ? VIOLET : st.color }}>
          {simulated ? "Simulated: " : ""}{st.label}
        </span>
      </div>
      <p className="text-sm mt-2">{c.reason}</p>
      {c.observed.length > 0 && <Section title={simulated ? "Simulated values" : "Observed"}><Rows rows={c.observed} /></Section>}
      {c.expected.length > 0 && <Section title="Expected or reference"><Rows rows={c.expected} /></Section>}
      {c.rule && (
        <Section title="Rule, with the numbers">
          <div className="text-xs bg-ink border border-edge rounded p-2 space-y-1">
            <div>{c.rule.equation}</div>
            {c.rule.substituted && <div className="tabular-nums">{c.rule.substituted}</div>}
            {c.rule.result && <div className="text-dim">{c.rule.result}</div>}
          </div>
        </Section>
      )}
      {c.deviation && <Section title="Deviation"><p className="text-xs tabular-nums">{c.deviation}</p></Section>}
      {c.hypotheses.length > 0 && (
        <Section title="Hypotheses (not conclusions)">
          <ul className="text-xs list-disc ml-4">{c.hypotheses.map((h) => <li key={h}>{h}</li>)}</ul>
        </Section>
      )}
      {c.verification.length > 0 && (
        <Section title="Recommended verification">
          <ul className="text-xs list-disc ml-4">{c.verification.map((h) => <li key={h}>{h}</li>)}</ul>
        </Section>
      )}
      {(c.missing.length > 0 || c.limits.length > 0) && (
        <Section title="Missing evidence and model limits">
          <ul className="text-xs list-disc ml-4 text-dim">
            {c.missing.map((h) => <li key={h}>Missing: {h}</li>)}
            {c.limits.map((h) => <li key={h}>{h}</li>)}
          </ul>
        </Section>
      )}
    </Dock>
  );
}

export function useComponents(scen, cursor, version) {
  const [d, setD] = useState(null);
  useEffect(() => {
    const t = scen === "live" ? "" : `?t=${cursor}`;
    api(`${scen}/components${t}`).then(setD).catch(() => {});
  }, [scen, cursor, version]);
  return d;
}

export default function TwinPage({ scen, cursor, version, header, simulateButton, override, onExitOverride }) {
  const observed = useComponents(scen, cursor, version);
  const data = override || observed;                 // a simulation never replaces the observed fetch
  const [sel, setSel] = useState(null);
  const [xray, setXray] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [tri, setTri] = useState(0);
  const [force2d, setForce2d] = useState(false);
  const gl = useMemo(() => webglAvailable(), []);
  const [fly, setFly] = useState(null);
  const newest = data?.newest_fault?.component;
  useEffect(() => { if (newest) setFly(FOCUS[newest] ? [...FOCUS[newest]] : null); }, [newest, data?.newest_fault?.start]);
  useEffect(() => { if (newest && !override) setSel(newest); }, [newest, override]);
  // a fault inside the tank cannot be seen through it: open the tank for it
  useEffect(() => { if (INSIDE.test(newest || "")) setXray(true); }, [newest, data?.newest_fault?.start]);
  useEffect(() => { if (INSIDE.test(sel || "")) setXray(true); }, [sel]);
  if (!data) return <div className="p-6 text-dim text-sm">Loading the twin…</div>;
  const comps = Object.fromEntries(data.components.map((c) => [c.id, c]));
  const use3d = gl && !force2d;
  const sim = Boolean(data.simulated);
  return (
    <div className="twin-ui h-[calc(100vh-0px)] flex flex-col">
      {header}
      <div className="flex-1 grid grid-cols-[17rem_1fr_23rem] min-h-0">
        <aside className="border-r border-edge overflow-y-auto">
          <Dock title={sim ? "Simulated state" : "Current state"}>
            <div className="text-xs tabular-nums text-dim">{data.timestamp?.replace("T", " ")}</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs mt-2 tabular-nums">
              {["red", "yellow", "sensor", "green", "grey"].map((k) => (
                <span key={k}><Dot state={k} />{data.counts[k]}</span>
              ))}
            </div>
            {data.newest_fault && (
              <p className="text-xs mt-2">Newest fault: <b>{data.newest_fault.what}</b>
                {data.newest_fault.family === "sensor" ? " (instrument, transformer unaffected)" : ""}</p>
            )}
            {data.stale && <p className="text-xs mt-2 text-dim">No recent data: every assessed part is grey.</p>}
          </Dock>
          <Legend scale={data.scale} />
          <ComponentList list={data.components} sel={sel} setSel={setSel} />
        </aside>

        <main className={`relative min-h-0 ${sim ? "ring-4 ring-inset ring-accent" : ""}`}>
          {sim && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 px-3 py-1 rounded bg-accent text-ink text-sm font-semibold">
              Simulated: hypothetical state, not the observed transformer
            </div>
          )}
          <div className="absolute top-3 left-3 z-10 flex gap-2">
            {simulateButton}
            {use3d && <button className="twin-btn" onClick={() => setXray(!xray)}>{xray ? "Solid tank" : "X-ray"}</button>}
            {use3d && <button className="twin-btn" onClick={() => setResetKey((k) => k + 1)}>Reset camera</button>}
            {gl && <button className="twin-btn" onClick={() => setForce2d(!force2d)}>{force2d ? "3D model" : "2D schematic"}</button>}
          </div>
          {use3d ? (
            <Suspense fallback={<div className="p-6 text-dim text-sm">Loading 3D…</div>}>
              <Scene comps={comps} scale={data.scale} sel={sel} setSel={setSel} xray={xray} flyTo={fly}
                resetKey={resetKey} onTriangles={setTri} />
            </Suspense>
          ) : (
            <Schematic comps={comps} scale={data.scale} sel={sel} setSel={setSel} forced={gl && force2d} />
          )}
          {use3d && (
            <div className="absolute bottom-2 right-3 text-xs text-dim tabular-nums">
              {tri.toLocaleString()} triangles · drag to rotate, scroll to zoom, right-drag to pan
            </div>
          )}
        </main>

        <aside className="border-l border-edge overflow-y-auto">
          {onExitOverride}
          <Inspector c={sel ? comps[sel] : null} simulated={sim} />
        </aside>
      </div>
    </div>
  );
}
