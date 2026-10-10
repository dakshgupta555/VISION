import { useEffect, useRef, useState } from "react";
import { api, fmt, Panel } from "./ui.jsx";
import { HELP } from "./help.js";

// Live observed stream: state arrives by Server-Sent Events after every sample.
// Replay is untouched: it keeps its own code path in App.jsx.

export function useLive(enabled) {
  const [s, setS] = useState(null);
  const [series, setSeries] = useState(null);
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState(null);
  const lastSeries = useRef(0);
  useEffect(() => {
    if (!enabled) return undefined;
    let es;
    api("live/status").then(setStatus).catch((e) => setErr(String(e)));
    es = new EventSource("/api/live/stream");
    es.addEventListener("state", (ev) => {
      const st = JSON.parse(ev.data);
      setS(st);
      setStatus(st.live);
      setErr(null);
      const now = Date.now();
      if (now - lastSeries.current > 1000) {           // trend window, at most once a second
        lastSeries.current = now;
        api("live/series").then(setSeries).catch(() => {});
      }
    });
    es.addEventListener("status", (ev) => setStatus(JSON.parse(ev.data)));
    es.onerror = () => setErr("live stream interrupted - retrying");
    return () => es && es.close();
  }, [enabled]);
  return { s, series, status, err };
}

export function ModeSwitch({ mode, setMode }) {
  return (
    <div className="flex border border-edge rounded-lg overflow-hidden" role="group" aria-label="Data mode">
      {["replay", "live"].map((m) => (
        <button key={m} onClick={() => setMode(m)}
          className={`px-4 py-1.5 text-sm ${mode === m ? "bg-fg text-panel font-semibold" : "text-dim hover:text-fg"}`}>
          {m === "replay" ? "Replay" : "Live"}
        </button>
      ))}
    </div>
  );
}

export function LiveBar({ status }) {
  const st = status || {};
  const control = (body) => fetch("/api/live/control", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const tone = !st.has_data || st.stale ? "text-dim border-edge" : "text-ok border-ok";
  return (
    <div className="px-4 py-2 border-b border-edge flex flex-wrap items-center gap-3 text-xs">
      <span className={`border rounded px-2 py-1 ${tone}`}>{st.source_label || "No source"}</span>
      <span className="num">sample age <b>{st.sample_age_s == null ? "—" : `${fmt(st.sample_age_s, 1)} s`}</b></span>
      <span className="num text-dim">every {st.period_s == null ? "—" : `${fmt(st.period_s, 1)} s`}</span>
      <span className="num text-dim">record time {st.last_sample ? st.last_sample.replace("T", " ") : "—"}</span>
      {st.prewarm_until && (
        <span className="text-dim">prewarm history to {st.prewarm_until.slice(5, 16).replace("T", " ")}</span>
      )}
      <label className="flex items-center gap-1 text-dim">speed
        <select className="bg-ink border border-edge rounded px-1 py-0.5 num text-fg" value={st.speed || 600}
          onChange={(e) => control({ speed: Number(e.target.value) })}>
          {[60, 300, 600, 1800, 3600].map((x) => <option key={x} value={x}>{x}x</option>)}
        </select>
      </label>
      <button className="border border-edge rounded px-2 py-1" onClick={() => control({ paused: !st.paused })}>
        {st.paused ? "Resume" : "Pause"}
      </button>
      {st.has_data && st.history?.rebuilding && (
        <span className="border border-warn text-warn rounded px-2 py-1 num"
          title="The 24 h rise fit and the persistence rules need history. They fill in as samples arrive; a restarted simulator re-sends it automatically.">
          History rebuilding ({st.history.n}/{st.history.need ?? 192})
        </span>
      )}
      {st.analysis_error && <span className="text-warn num" title={st.analysis_error}>analysis paused: {st.analysis_error.slice(0, 60)}</span>}
      {st.stale && <span className="text-dim">● {st.reason}</span>}
      {!st.has_data && <span className="text-dim">Start the stream: <span className="num">python -m dtwin.live --speed 600</span></span>}
      <span className="ml-auto text-dim num">analysis {st.analysis_ms ?? "—"} ms, {st.n_samples ?? 0} samples</span>
    </div>
  );
}

const FAULTS = [
  { kind: "cooling_degradation", label: "Cooling degradation", severity: 0.3, unit: "x rise", ramp: 24 },
  { kind: "oil_loss", label: "Oil loss", severity: 0.3, unit: "fraction", ramp: 72 },
  { kind: "overload", label: "Overload", severity: 1.25, unit: "pu load", ramp: 1 },
  { kind: "unbalance_growth", label: "Phase unbalance", severity: 0.12, unit: "fraction", ramp: 24 },
  { kind: "sensor_drift", label: "Oil probe drift", severity: 5, unit: "K", ramp: 24, channel: "oil_temp" },
  { kind: "sensor_stuck", label: "Stuck current channel", severity: 1, unit: "", ramp: 0, channel: "IY", pick: true },
  { kind: "sensor_dead", label: "Dead current channel", severity: 1, unit: "", ramp: 0, channel: "IB", pick: true },
];

export function InjectPanel() {
  const [list, setList] = useState([]);
  const [form, setForm] = useState(FAULTS[0]);
  const [msg, setMsg] = useState(null);
  const refresh = () => api("live/inject").then((d) => setList(d.faults)).catch(() => {});
  useEffect(() => { refresh(); const id = setInterval(refresh, 3000); return () => clearInterval(id); }, []);
  const send = () => fetch("/api/live/inject", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: form.kind, severity: Number(form.severity), ramp_h: Number(form.ramp), channel: form.channel }),
  }).then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail); setMsg(`Injected ${form.label}`); refresh(); })
    .catch((e) => setMsg(String(e.message || e)));
  const remove = (id) => fetch(`/api/live/inject${id ? `/${id}` : ""}`, { method: "DELETE" }).then(refresh);
  return (
    <Panel title="Inject a fault" help={HELP.inject}>
      <p className="text-sm text-dim -mt-2 mb-3">Changes the simulated physics; the detector is not told.</p>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 items-end text-sm">
        <label className="md:col-span-2">Fault
          <select className="w-full border border-edge rounded-lg px-2 py-1.5" value={form.kind}
            onChange={(e) => setForm({ ...FAULTS.find((f) => f.kind === e.target.value) })}>
            {FAULTS.map((f) => <option key={f.kind} value={f.kind}>{f.label}</option>)}
          </select>
        </label>
        <label>Severity {form.unit && <span className="text-dim">({form.unit})</span>}
          <input className="w-full border border-edge rounded-lg px-2 py-1.5 num" value={form.severity}
            onChange={(e) => setForm({ ...form, severity: e.target.value })} />
        </label>
        <label>Ramp (h)
          <input className="w-full border border-edge rounded-lg px-2 py-1.5 num" value={form.ramp}
            onChange={(e) => setForm({ ...form, ramp: e.target.value })} />
        </label>
        {form.pick ? (
          <label>Channel
            <select className="w-full border border-edge rounded-lg px-2 py-1.5" value={form.channel}
              onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              {["IR", "IY", "IB"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        ) : <span />}
      </div>
      <div className="flex gap-2 mt-2">
        <button className="twin-btn twin-btn-primary" onClick={send}>Inject</button>
        <button className="twin-btn" onClick={() => remove(null)}>Clear all</button>
        {msg && <span className="text-xs text-dim self-center">{msg}</span>}
      </div>
      <ul className="mt-3 text-sm space-y-1">
        {list.length === 0 && <li className="text-dim">No faults injected.</li>}
        {list.map((f) => (
          <li key={f.id} className="flex justify-between border-t border-edge pt-1">
            <span>{FAULTS.find((x) => x.kind === f.kind)?.label || f.kind}, severity {f.severity}
              {f.channel ? `, ${f.channel}` : ""}, ramp {f.ramp_h} h
              <span className="text-dim">, since {f.injected_at_sample?.slice(5, 16).replace("T", " ")}</span></span>
            <button className="text-dim" onClick={() => remove(f.id)}>remove</button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
