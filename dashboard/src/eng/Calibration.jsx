// Calibration from a factory test report: paste or upload a report, or fill
// the form; preview, Apply, Reset to defaults. Every parameter shows its value,
// provenance badge, the built-in value it replaced and the change. Checks are
// labelled as checks, never as pass/fail.
import { useEffect, useRef, useState } from "react";
import { api, Card, fmt } from "../ui.jsx";
import { HELP } from "../help.js";

const BADGE = {
  NAMEPLATE: "border-fg text-fg", "TEST REPORT": "border-ok text-ok", DERIVED: "border-fg text-fg bg-ink",
  ASSUMED: "border-edge text-dim",
};

// the form: same schema as the YAML/JSON file
const FORM = [
  ["nameplate", "Nameplate", [["rating_kva", "Rating", "kVA", true], ["hv_kv", "HV voltage", "kV", true], ["lv_v", "LV voltage", "V", true],
    ["vector_group", "Vector group", "", false, "text"], ["cooling", "Cooling", "", false, "text"], ["frequency_hz", "Frequency", "Hz"],
    ["impedance_pct", "Impedance", "%"]]],
  ["open_circuit", "Open-circuit test", [["p0_w", "No-load loss P0", "W"], ["i0_pct", "No-load current", "%"], ["i0_a", "or no-load current", "A"],
    ["voltage_v", "Test voltage", "V"]]],
  ["short_circuit", "Short-circuit test", [["pk_w", "Load loss Pk", "W"], ["reference_temp_c", "Reference temperature", "°C"],
    ["i2r_w", "I²R part of Pk", "W"], ["impedance_pct", "Impedance voltage", "%"], ["winding_material", "Winding material", "", false, "text"],
    ["temperature_constant_k", "Temperature constant T", "K"]]],
  ["heat_run", "Heat-run test", [["top_oil_rise_k", "Top-oil rise", "K"], ["average_winding_rise_k", "Average winding rise", "K"],
    ["hotspot_rise_k", "Hot-spot rise over ambient", "K"], ["hotspot_gradient_k", "or hot-spot gradient", "K"],
    ["tau_oil_h", "Oil time constant", "h"], ["tau_winding_min", "Winding time constant", "min"]]],
];

async function send(path, method, body) {
  const r = await fetch(`/api/${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `${r.status}`);
  return j;
}

function toForm(rep) {
  const f = {};
  FORM.forEach(([sec, , fields]) => fields.forEach(([k]) => { const v = rep?.[sec]?.[k]; f[`${sec}.${k}`] = v === undefined || v === null ? "" : String(v); }));
  f.name = rep?.report?.name || "";
  return f;
}

function fromForm(f) {
  const rep = { report: { name: f.name || "test report entered in the form" } };
  FORM.forEach(([sec, , fields]) => {
    fields.forEach(([k, , , , type]) => {
      const v = f[`${sec}.${k}`];
      if (v === undefined || v === "") return;
      rep[sec] = rep[sec] || {};
      rep[sec][k] = type === "text" ? v : Number(v);
    });
  });
  return rep;
}

function Badge({ p }) {
  return <span className={`inline-block text-sm font-semibold px-2 py-0.5 rounded-md border whitespace-nowrap ${BADGE[p] || BADGE.ASSUMED}`}>{p}</span>;
}

const show = (v) => (typeof v === "number" ? fmt(v, Math.abs(v) >= 100 ? 1 : 3) : v ?? "—");

export function ParamTable({ res }) {
  return (
    <table className="w-full text-[15px]">
      <thead><tr className="text-sm text-dim text-left">
        <th className="font-normal py-1">Parameter</th><th className="font-normal text-right">Value</th><th className="font-normal pl-2">Unit</th>
        <th className="font-normal pl-3">Source</th><th className="font-normal text-right">Was</th><th className="font-normal text-right">Change</th>
        <th className="font-normal pl-4">How</th></tr></thead>
      <tbody>{res.params.map((p) => (
        <tr key={p.key} className="border-t border-edge align-top">
          <td className="py-1.5">{p.label}</td>
          <td className="text-right font-semibold whitespace-nowrap">{show(p.value)}</td>
          <td className="pl-2 text-dim whitespace-nowrap">{p.unit}</td>
          <td className="pl-3"><Badge p={p.provenance} /></td>
          <td className="text-right pl-4 text-dim whitespace-nowrap">{show(p.was)}</td>
          <td className={`text-right pl-4 whitespace-nowrap ${p.change ? "font-semibold" : "text-dim"}`}>{p.change ? `${p.change > 0 ? "+" : ""}${show(p.change)}` : "—"}</td>
          <td className="pl-4 text-sm text-dim">{p.formula}{p.note ? <span className="block">{p.note}</span> : null}</td>
        </tr>))}</tbody>
    </table>
  );
}

export function Checks({ res }) {
  return (
    <ul className="space-y-3">
      {res.checks.map((c) => (
        <li key={c.key} className="text-[15px]">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold px-2 py-0.5 rounded-md border border-edge text-dim">CHECK</span>
            <b>{c.label}</b>
            {c.difference !== null && c.difference !== undefined && <span className="ml-auto whitespace-nowrap font-semibold">{c.difference > 0 ? "+" : ""}{fmt(c.difference, 2)} {c.unit}</span>}
            {c.status === "not checked" && <span className="ml-auto font-semibold text-dim">not checked</span>}
          </div>
          <p className="text-dim mt-0.5">{c.text}</p>
        </li>
      ))}
    </ul>
  );
}

export default function Calibration({ scen, onChange }) {
  const [samples, setSamples] = useState(null);
  const [status, setStatus] = useState(null);
  const [mode, setMode] = useState("form");
  const [text, setText] = useState("");
  const [form, setForm] = useState(toForm(null));
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const file = useRef();

  const refresh = () => api("calibration").then(setStatus).catch(() => {});
  useEffect(() => {
    api("calibration/samples").then(setSamples).catch((e) => setErr(String(e)));
    refresh();
  }, []);

  const body = () => (mode === "text" ? { text } : { report: fromForm(form) });
  const loadSample = (s) => { setText(s.text); setForm(toForm(s.report)); setPreview(null); setErr(null); };
  const doPreview = () => { setBusy(true); setErr(null); send("calibration/preview", "POST", body()).then(setPreview).catch((e) => setErr(e.message)).finally(() => setBusy(false)); };
  const doApply = () => {
    setBusy(true); setErr(null);
    send("calibration", "POST", { ...body(), scenario: scen }).then((r) => { setPreview(r.result); refresh(); onChange?.(); })
      .catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  const doReset = () => { setBusy(true); send("calibration", "DELETE").then(() => { setPreview(null); refresh(); onChange?.(); }).finally(() => setBusy(false)); };
  const onFile = (e) => { const f = e.target.files?.[0]; if (!f) return; f.text().then((t) => { setText(t); setMode("text"); setPreview(null); }); };

  const res = preview || status?.result;
  return (
    <div className="space-y-6">
      <Card title="Calibration from a factory test report" help={HELP.calibration}>
        <p className="text-[15px] text-dim max-w-4xl">Why this matters: the twin starts from typical values for a transformer of this size. Entering the
          numbers from this unit's own test report makes its losses, impedance and heat response this unit's, so its temperatures, ageing and
          warnings rest on measured data, and every number says where it came from.</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {status?.active
            ? <span className="text-sm font-semibold px-2 py-0.5 rounded-md border border-fg">Applied: {status.result.name}</span>
            : <span className="text-sm text-dim">No report applied: the twin uses its built-in values.</span>}
          <span className="ml-auto text-sm text-dim">Samples (ILLUSTRATIVE, not real certificates):</span>
          {samples?.samples.map((s) => <button key={s.file} className="twin-btn" onClick={() => loadSample(s)}>Load sample {s.label}</button>)}
        </div>
        <div className="mt-4 flex gap-2" role="group" aria-label="Report input">
          {[["form", "Fill the form"], ["text", "Paste or upload YAML / JSON"]].map(([k, l]) => (
            <button key={k} className={`twin-btn ${mode === k ? "twin-btn-primary" : ""}`} onClick={() => setMode(k)}>{l}</button>))}
        </div>
        {mode === "text" ? (
          <div className="mt-3">
            <textarea aria-label="Test report text" rows={14} className="w-full border border-edge rounded-lg p-3 text-[15px] bg-panel"
              placeholder="Paste a test report in YAML or JSON (same fields as the form), or load a sample" value={text} onChange={(e) => setText(e.target.value)} />
            <input ref={file} type="file" accept=".yaml,.yml,.json" className="hidden" onChange={onFile} />
            <button className="twin-btn mt-2" onClick={() => file.current?.click()}>Upload a file…</button>
          </div>
        ) : (
          <div className="mt-3">
            <label className="block text-sm max-w-xl"><span className="text-dim">Report name</span>
              <input className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5 bg-panel" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <div className="mt-3 grid gap-6 lg:grid-cols-4">
              {FORM.map(([sec, title, fields]) => (
                <fieldset key={sec} className="space-y-2">
                  <legend className="font-semibold mb-1">{title}</legend>
                  {fields.map(([k, l, unit, req, type]) => (
                    <label key={k} className="block text-sm">
                      <span className="text-dim">{l}{unit ? ` (${unit})` : ""}{req ? " *" : ""}</span>
                      <input aria-label={`${title} ${l}`} type={type === "text" ? "text" : "number"} step="any"
                        className="mt-0.5 w-full border border-edge rounded-lg px-2 py-1 bg-panel" value={form[`${sec}.${k}`]}
                        onChange={(e) => setForm({ ...form, [`${sec}.${k}`]: e.target.value })} />
                    </label>
                  ))}
                </fieldset>
              ))}
            </div>
            <p className="mt-2 text-sm text-dim">* required. Every other field is optional; a missing one keeps the built-in value and is marked ASSUMED.
              Load loss is moved to the twin's {samples?.model_reference_temp_c ?? 75} °C reference when another temperature is given.</p>
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="twin-btn" disabled={busy} onClick={doPreview}>Preview</button>
          <button className="twin-btn twin-btn-primary" disabled={busy} onClick={doApply}>Apply to the twin</button>
          <button className="twin-btn" disabled={busy || !status?.active} onClick={doReset}>Reset to defaults</button>
        </div>
        {err && <p role="alert" className="mt-3 text-alarm font-semibold">Cannot calibrate: {err}</p>}
      </Card>
      {res && (
        <div className="space-y-6">
          <Card title={`Parameters${preview && !(status?.active && status.result.key === preview.key) ? " (preview, not applied)" : ""}`} help={HELP.calibrationParams}>
            {res.illustrative && <p className="mb-3 text-sm font-semibold text-warn">{res.name}: illustrative sample, not a real test certificate.</p>}
            <div className="overflow-x-auto"><ParamTable res={res} /></div>
            {res.hotspot_note && <p className="mt-2 text-sm text-dim">Note: {res.hotspot_note}</p>}
          </Card>
          <Card title="Checks" help={HELP.calibrationChecks}><Checks res={res} /></Card>
        </div>
      )}
    </div>
  );
}
