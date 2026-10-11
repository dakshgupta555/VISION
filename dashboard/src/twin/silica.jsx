import { useState, useSyncExternalStore } from "react";
import { Badge } from "../overview/Explain.jsx";

// Silica-gel breather condition. There is no sensor for it: the value is a
// user-set scenario or an inspection entry, "unknown" until someone sets it,
// and it resets on reload so it can never be mistaken for stored data. The
// descriptive record (colours, wording, causal chain) comes from
// GET /api/breather, which reads config/breather.yaml.

export const CONDITIONS = ["unknown", "effective", "partial", "saturated"];
const FALLBACK_COLOUR = "#a3aaa6";
const recs = {};
const subs = new Set();
let cond = "unknown";
let snap = { condition: "unknown", rec: null };

const notify = () => { snap = { condition: cond, rec: recs[cond] || null }; subs.forEach((f) => f()); };
function load(c) {
  if (recs[c]) return;
  fetch(`/api/breather?condition=${c}`).then((r) => (r.ok ? r.json() : null)).then((j) => {
    if (j) { recs[c] = j; if (c === cond) notify(); }
  }).catch(() => {});
}
export function setSilica(c) {
  if (!CONDITIONS.includes(c)) return;
  cond = c;
  load(c);
  notify();
}
export function useSilica() {
  const s = useSyncExternalStore((f) => { subs.add(f); load(cond); return () => subs.delete(f); }, () => snap);
  return { condition: s.condition, rec: s.rec, colour: s.rec?.colour || FALLBACK_COLOUR };
}

// -------------------------------------------------------------- pieces
export function SilicaLegend({ rec, compact = false }) {
  const items = rec?.legend || CONDITIONS.map((k) => ({ condition: k, label: k, colour: FALLBACK_COLOUR }));
  return (
    <div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {items.map((l) => (
          <li key={l.condition} className="flex items-center gap-1.5 text-sm">
            <span className="w-3 h-3 rounded-full border border-edge" style={{ background: l.colour }} />{l.label}
          </li>
        ))}
      </ul>
      {rec && !compact && <p className="text-sm text-dim mt-1">{rec.convention}</p>}
    </div>
  );
}

function Chain({ rec }) {
  return (
    <table className="w-full text-sm">
      <tbody>
        {rec.chain.map((c) => (
          <tr key={c.key} className="border-t border-edge align-top">
            <td className="py-1 pr-2">{c.label}<div className="text-dim">{c.note}</div></td>
            <td className="py-1 pr-2 text-right font-semibold">{c.value}</td>
            <td className="py-1 text-right"><Badge p={c.kind} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function BreatherInfoBody({ rec }) {
  if (!rec) return <p className="text-sm text-dim">Loading…</p>;
  const x = rec.explain;
  const Row = ({ t, children }) => (
    <div className="mt-3"><h4 className="text-sm font-semibold text-dim">{t}</h4><p className="text-sm">{children}</p></div>
  );
  return (
    <div>
      <p className="text-sm">
        <span className="inline-flex items-center gap-1.5 font-semibold">
          <span className="w-3 h-3 rounded-full border border-edge" style={{ background: rec.colour }} />{rec.label}
        </span>{" "}<Badge p="ASSUMED" />
      </p>
      <p className="text-sm text-dim mt-1">{rec.source.text}</p>
      <Row t="What the breather is for">{x.purpose}</Row>
      <Row t="How silica gel removes moisture">{x.mechanism}</Row>
      <Row t="How protection declines">{x.decline}</Row>
      <Row t="What this condition is based on">{x.inputs}</Row>
      <Row t="What it can tell us">{x.can_tell}</Row>
      <Row t="What it cannot tell us">{x.cannot_tell}</Row>
      <h4 className="text-sm font-semibold text-dim mt-3 mb-1">Cause and effect chain</h4>
      <Chain rec={rec} />
      <Row t="Downstream effects">{x.downstream}</Row>
      <p className="text-sm text-dim mt-2">{rec.health.text}</p>
      <h4 className="text-sm font-semibold text-dim mt-3 mb-1">Colour legend</h4>
      <SilicaLegend rec={rec} />
    </div>
  );
}

// Compact parameter card: state, the control that sets it, and the full
// explanation behind a disclosure. It sits under the four main tiles.
export function SilicaCard() {
  const { condition, rec, colour } = useSilica();
  const [open, setOpen] = useState(false);
  return (
    <section className="card p-5" aria-label="Silica-gel breather condition">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="text-base font-semibold">Silica-gel breather</h2>
        <span className="flex items-center gap-1.5 text-sm font-semibold">
          <span className="w-3 h-3 rounded-full border border-edge" style={{ background: colour }} />
          {rec?.label || "Unknown (no data)"}
        </span>
        <Badge p="ASSUMED" />
        <label className="ml-auto flex items-center gap-2 text-sm text-dim">
          Set scenario or inspection entry
          <select value={condition} onChange={(e) => setSilica(e.target.value)}
            className="twin-btn text-fg" aria-label="Silica-gel condition">
            {CONDITIONS.map((k) => (
              <option key={k} value={k}>{{ unknown: "Unknown", effective: "Effective", partial: "Partially exhausted", saturated: "Saturated" }[k]}</option>
            ))}
          </select>
        </label>
      </div>
      <p className="text-sm text-dim mt-2">
        Not measured: no breather or moisture sensor exists. Moisture protection is {rec?.chain[1]?.value?.toLowerCase() || "not assessed"};
        hot-spot, top oil and the health index are unaffected by this setting.
      </p>
      <button type="button" className="text-sm underline decoration-dotted underline-offset-4 mt-2" aria-expanded={open}
        onClick={() => setOpen(!open)}>{open ? "Hide explanation" : "How this works"}</button>
      {open && <div className="mt-2 border-t border-edge pt-2"><BreatherInfoBody rec={rec} /></div>}
    </section>
  );
}
