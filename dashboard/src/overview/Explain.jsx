import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { axis, fmt, grid, tip } from "../ui.jsx";

// Calculation panel for a tile. The numbers all come from
// GET /api/{scenario}/explain/{metric}: the backend calls the same functions
// that produce the tile, so this file only lays them out. One request per
// (scenario, cursor, metric), cached; hovering again costs nothing.

const cache = new Map();
const pending = new Map();

export function loadExplain(scen, t, kva, metric) {
  const key = `${scen}|${t ?? "last"}|${kva ?? ""}|${metric}`;
  if (cache.has(key)) return Promise.resolve(cache.get(key));
  if (pending.has(key)) return pending.get(key);
  const q = [t !== null && t !== undefined && scen !== "live" ? `t=${t}` : null, kva ? `kva=${kva}` : null]
    .filter(Boolean).join("&");
  const p = fetch(`/api/${scen}/explain/${metric}${q ? `?${q}` : ""}`)
    .then(async (r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
    .then((j) => { cache.set(key, j); if (cache.size > 80) cache.delete(cache.keys().next().value); return j; })
    .finally(() => pending.delete(key));
  pending.set(key, p);
  return p;
}

// Provenance badges, same family as the calibration page.
const BADGE = {
  NAMEPLATE: "border-fg text-fg", DERIVED: "border-fg text-fg bg-ink", ASSUMED: "border-edge text-dim",
  MEASURED: "border-ok text-ok", SIMULATED: "border-sensor text-sensor",
};
export function Badge({ p }) {
  return <span className={`inline-block text-sm font-semibold px-1.5 rounded-md border whitespace-nowrap ${BADGE[p] || BADGE.ASSUMED}`}>{p}</span>;
}

function Sec({ title, children }) {
  return (
    <section className="py-3 border-t border-edge first:border-t-0">
      <h3 className="text-sm font-semibold text-dim mb-1.5">{title}</h3>
      {children}
    </section>
  );
}

const show = (v, d = 2) => (v === null || v === undefined ? "n/a" : fmt(v, d));

function Equation({ e }) {
  return (
    <div className="mb-3 last:mb-0">
      <div className="rounded-md bg-ink px-3 py-2 text-[15px] font-semibold break-words">{e.tex}</div>
      <table className="w-full text-sm mt-2">
        <tbody>
          {e.vars.map((v) => (
            <tr key={v.sym} className="border-t border-edge align-top">
              <td className="py-1 pr-2 font-semibold whitespace-nowrap">{v.sym}</td>
              <td className="py-1 pr-2">{v.meaning}{v.unit ? <span className="text-dim"> ({v.unit})</span> : null}</td>
              <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">{v.value === null ? "" : show(v.value, 3)}</td>
              <td className="py-1 text-right"><Badge p={v.source} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {e.assumptions?.map((a) => <p key={a} className="text-sm text-dim mt-1">Assumes: {a}</p>)}
      <p className="text-sm text-dim mt-1">Valid when: {e.valid}</p>
    </div>
  );
}

function Steps({ steps, final }) {
  return (
    <ol className="space-y-1.5">
      {steps.map((s, k) => (
        <li key={`${s.label}${k}`} className="text-sm flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold">{s.label}</span>
          <span className="text-dim">{s.expr}</span>
          <span className="text-dim">= {s.substituted}</span>
          {s.value !== null && s.value !== undefined && (
            <span className="font-semibold tabular-nums">{show(s.value, 3)} {s.unit}</span>)}
          {s.score !== undefined && s.score !== null && s.contribution !== undefined && (
            <span className="text-dim">(input {show(s.input_value, 2)} {s.input_unit}, score {s.score})</span>)}
          <Badge p={s.source} />
        </li>
      ))}
      <li className="text-sm font-semibold pt-1 border-t border-edge">
        Displayed on the tile: {show(final.value, 1)} {final.unit}
      </li>
    </ol>
  );
}

function Graph({ g }) {
  if (!g) return <p className="text-sm text-dim">Not calculated: a required input is missing.</p>;
  const data = g.x.map((x, k) => ({ x, ...Object.fromEntries(g.series.map((s) => [s.name, s.y[k]])) }));
  return (
    <div>
      <p className="text-sm font-semibold">{g.title}</p>
      <ResponsiveContainer width="100%" height={190}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 22, left: 4 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="x" type="number" domain={[(m) => Math.floor(m * 10) / 10, "dataMax"]} tick={axis}
            label={{ value: g.xlabel, position: "insideBottom", offset: -14, fontSize: 14, fill: "var(--chart-axis)" }} />
          <YAxis tick={axis} domain={["auto", "auto"]} width={52}
            label={{ value: g.ylabel, angle: -90, position: "insideLeft", offset: 12, fontSize: 14, fill: "var(--chart-axis)", style: { textAnchor: "middle" } }} />
          <Tooltip contentStyle={tip} formatter={(v) => fmt(v, 1)} labelFormatter={(x) => `${g.xlabel}: ${x}`} />
          {g.vlines.map((l) => <ReferenceLine key={l.label} x={l.x} stroke="var(--chart-ref)" strokeDasharray="5 4" />)}
          {g.hlines.map((l) => <ReferenceLine key={l.label} y={l.y} stroke="var(--color-alarm)" strokeDasharray="5 4"
            label={{ value: l.label, position: "insideTopLeft", fontSize: 14, fill: "var(--chart-axis)" }} />)}
          {g.series.map((s) => <Line key={s.name} dataKey={s.name} stroke="var(--chart-1)" strokeWidth={2} dot={false} isAnimationActive={false} />)}
          {g.point.x !== null && g.point.y !== null && (
            <ReferenceDot x={g.point.x} y={g.point.y} r={6} fill="var(--color-fg)" stroke="var(--color-panel)" strokeWidth={2} ifOverflow="extendDomain" />)}
        </LineChart>
      </ResponsiveContainer>
      <p className="text-sm text-dim">Dot: {g.point.label}. {g.note}</p>
    </div>
  );
}

function Sensitivity({ rows }) {
  if (!rows.length) return <p className="text-sm text-dim">Not calculated: a required input is missing.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums">
        <thead><tr className="text-dim text-left">
          <th className="font-normal pr-2">Input change</th><th className="font-normal pr-2 text-right">Before</th>
          <th className="font-normal pr-2 text-right">After</th><th className="font-normal pr-2 text-right">Change</th>
          <th className="font-normal text-right">%</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.input} className="border-t border-edge align-top">
              <td className="py-1 pr-2">{r.input}<div className="text-dim">{show(r.current, 3)} to {show(r.perturbed, 3)} {r.unit}</div></td>
              <td className="py-1 pr-2 text-right">{show(r.before)}</td>
              <td className="py-1 pr-2 text-right">{show(r.after)}</td>
              <td className="py-1 pr-2 text-right font-semibold">{r.delta === null ? "n/a" : `${r.delta > 0 ? "+" : ""}${show(r.delta)}`}</td>
              <td className="py-1 text-right">{r.pct === null ? "n/a" : `${r.pct > 0 ? "+" : ""}${show(r.pct, 1)}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.map((r) => r.note && <p key={r.input} className="text-sm text-dim mt-1">{r.input}: {r.note} ({r.out_unit})</p>)}
    </div>
  );
}

export function TraceBody({ tr }) {
  return (
    <>
      <Sec title="A. What this metric means">
        <p className="text-sm">{tr.meaning.definition}</p>
        <p className="text-sm text-dim mt-1">Unit: {tr.meaning.unit}. Normal: {tr.meaning.normal}</p>
        <p className="text-sm text-dim mt-1">Why it matters: {tr.meaning.why}</p>
      </Sec>
      <Sec title="B. Governing physics (as implemented)">
        {tr.equations.map((e) => <Equation key={e.id} e={e} />)}
      </Sec>
      <Sec title="C. Live calculation trace">
        {tr.missing?.length > 0 && (
          <p className="text-sm text-warn mb-1.5">Not available or caveat: {tr.missing.join("; ")}.</p>)}
        <Steps steps={tr.steps} final={tr.final} />
      </Sec>
      <Sec title="D. Graph (model sweep, not history)"><Graph g={tr.graph} /></Sec>
      <Sec title="E. Sensitivity (one input changed, the rest held)"><Sensitivity rows={tr.sensitivity} /></Sec>
      <Sec title="F. Data quality">
        <p className="text-sm">Data origin: <Badge p={tr.quality.origin} /></p>
        <ul className="list-disc ml-5 mt-1 space-y-1">
          {tr.quality.notes.map((n) => <li key={n} className="text-sm text-dim">{n}</li>)}
        </ul>
      </Sec>
    </>
  );
}

// -------------------------------------------------------------- popover
const W = 560;

function Panel({ anchor, tr, err, onClose, onEnter, onLeave, pinned, title }) {
  const ref = useRef();
  const [pos, setPos] = useState({ left: 8, top: 8, maxH: 400 });
  useLayoutEffect(() => {
    const place = () => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const a = anchor.getBoundingClientRect();
      const w = Math.min(W, vw - 16);
      // prefer the side with room: left of the card, else right, else centred; always inside the viewport
      let left = a.left - w - 8;
      if (left < 8) left = a.right + 8 + w <= vw - 8 ? a.right + 8 : Math.max(8, (vw - w) / 2);
      const maxH = vh - 16;                      // never taller than the window: it scrolls inside
      const top = Math.max(8, Math.min(a.top, vh - 8 - maxH));
      setPos({ left, top, maxH, w });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [anchor, tr]);
  useEffect(() => { ref.current?.focus({ preventScroll: true }); }, [pinned]);
  return createPortal(
    <div ref={ref} role="dialog" aria-label={`How ${title} is calculated`} tabIndex={-1}
      onMouseEnter={onEnter} onMouseLeave={onLeave}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
      className="card fixed z-[60] overflow-y-auto p-4 text-fg shadow-xl outline-none"
      style={{ left: pos.left, top: pos.top, width: pos.w || W, maxHeight: pos.maxH, boxShadow: "0 8px 30px rgba(0,0,0,0.25)" }}>
      <div className="flex items-start justify-between gap-3 mb-1">
        <h2 className="text-base font-semibold">How {title} is calculated</h2>
        <button type="button" className="twin-btn px-2 py-0.5 text-sm" onClick={onClose} aria-label="Close calculation">Close</button>
      </div>
      {err && <p className="text-sm text-alarm">Could not load the calculation ({err}).</p>}
      {!tr && !err && <p className="text-sm text-dim">Loading the calculation from the model…</p>}
      {tr && <TraceBody tr={tr} />}
    </div>,
    document.body);
}

// Wraps a tile. Hover opens the panel, click or tap pins it, Enter on the
// "Calculation" button does the same; Escape and outside click close it.
export function Explainable({ scen, t, kva, metric, title, children }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [tr, setTr] = useState(null);
  const [err, setErr] = useState(null);
  const box = useRef();
  const timer = useRef();
  const hoverCapable = useRef(typeof window !== "undefined" && window.matchMedia?.("(hover: hover)").matches);

  const fetchIt = useCallback(() => {
    setErr(null);
    loadExplain(scen, t, kva, metric).then(setTr).catch((e) => setErr(String(e.message || e)));
  }, [scen, t, kva, metric]);
  // the cursor moved: the open panel refreshes (cached per cursor), a closed one just forgets
  useEffect(() => { setTr(null); if (open) fetchIt(); }, [t, scen, kva]);   // eslint-disable-line react-hooks/exhaustive-deps

  const openIt = () => { clearTimeout(timer.current); if (!open) { setOpen(true); fetchIt(); } };
  const closeSoon = () => { clearTimeout(timer.current); timer.current = setTimeout(() => { if (!pinned) setOpen(false); }, 220); };
  const closeNow = () => { clearTimeout(timer.current); setPinned(false); setOpen(false); };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!pinned) return undefined;
    const away = (e) => {
      if (box.current?.contains(e.target) || e.target.closest?.('[role="dialog"]')) return;
      closeNow();
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [pinned]);

  return (
    <div ref={box} className="relative"
      onMouseEnter={() => { if (hoverCapable.current) openIt(); }}
      onMouseLeave={() => { if (hoverCapable.current) closeSoon(); }}>
      {children}
      <button type="button" aria-expanded={open} aria-haspopup="dialog"
        aria-label={`Show how ${title} is calculated`}
        onClick={() => { if (open && pinned) closeNow(); else { openIt(); setPinned(true); } }}
        onKeyDown={(e) => { if (e.key === "Escape") closeNow(); }}
        className="absolute bottom-2 right-3 text-sm text-dim underline decoration-dotted underline-offset-4 hover:text-fg">
        Calculation
      </button>
      {open && box.current && (
        <Panel anchor={box.current} tr={tr} err={err} title={title} pinned={pinned}
          onClose={closeNow} onEnter={() => clearTimeout(timer.current)} onLeave={closeSoon} />
      )}
    </div>
  );
}
