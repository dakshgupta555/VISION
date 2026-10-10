// Building blocks shared by the network pages. LimitBar, KeyNumber and Trends
// are copied from the single-transformer dashboard (dashboard/src/overview/
// Overview.jsx) so both apps read the same; copied, not imported, so a change
// in one app cannot leak into the other.
import { useEffect, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axis, Card, fmt, grid, Help, Icon, PHASE, Tabs, Term, tip, when } from "./ui.jsx";
import { HELP as TWIN_HELP } from "./twinhelp.js";

export const TONE_TEXT = { ok: "text-ok", warn: "text-warn", alarm: "text-alarm", sensor: "text-sensor", info: "text-dim" };
export const TONE_BORDER = { ok: "border-ok", warn: "border-warn", alarm: "border-alarm", sensor: "border-sensor", info: "border-edge" };
// grey for unknown / unverified: the same "not monitored" grey as the 3D twin legend
export const COLOR = { ok: "var(--color-ok)", warn: "var(--color-warn)", alarm: "var(--color-alarm)",
  sensor: "var(--color-sensor)", unknown: "#a3aaa6" };
export const LEVEL_WORD = { ok: "Normal", warn: "Warning", alarm: "Alarm", sensor: "Instrument fault" };
export const COMM_WORD = { online: "Online", stale: "Stale", offline: "Offline", "no data": "No data" };

// The colour a transformer is drawn in: its health only when verified.
export const shownLevel = (d) => (d.health.verified && d.health.level ? d.health.level : "unknown");

export const inrIN = (v) => (v === null || v === undefined ? "—" : `₹${fmt(v, 0)}`);
// ₹ in lakh for compact labels: 4,00,000 -> "₹4.0 L"
export const lakh = (v) => `₹${fmt(v / 1e5, 1)} L`;

export async function post(path, body) {
  const r = await fetch(`/api/${path}`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`);
  return r.json();
}

// Small popover anchored to a button; closes on outside click or Escape.
export function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  return { open, setOpen, ref };
}

// Communication mark, drawn the same in the map, the legend and the lists.
export function CommMark({ status, size = 14 }) {
  const c = "var(--color-fg)";
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true" className="inline-block shrink-0">
      {status === "online" && <circle cx="7" cy="7" r="4.5" fill={c} />}
      {status === "stale" && <><circle cx="7" cy="7" r="4.5" fill="none" stroke={c} strokeWidth="1.6" />
        <path d="M7 2.5 A4.5 4.5 0 0 1 7 11.5 Z" fill={c} /></>}
      {(status === "offline" || status === "no data") && <><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--color-dim)" strokeWidth="1.6" />
        <path d="M3.5 3.5 L10.5 10.5" stroke="var(--color-dim)" strokeWidth="1.6" /></>}
    </svg>
  );
}

export function Chip({ tone = "info", children, title }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 text-sm font-semibold px-2 py-0.5 rounded-md border ${TONE_BORDER[tone]} ${TONE_TEXT[tone]}`}>
      {children}
    </span>
  );
}

// ======================================================================= limit bar + key number
export function LimitBar({ value, lim, inverse = false }) {
  const { min, max } = lim;
  const pos = (v) => `${Math.max(0, Math.min(100, (100 * (v - min)) / (max - min)))}%`;
  const w = inverse ? lim.warn_below : lim.warn, a = inverse ? lim.alarm_below : lim.alarm;
  const zones = inverse
    ? [[min, a, "alarm"], [a, w, "warn"], [w, max, "ok"]]
    : [[min, w, "ok"], [w, a, "warn"], [a, max, "alarm"]];
  const fill = { ok: "var(--color-edge)", warn: "color-mix(in srgb, var(--color-warn) 30%, transparent)",
    alarm: "color-mix(in srgb, var(--color-alarm) 30%, transparent)" };
  return (
    <div className="mt-3">
      <div className="relative h-2.5 rounded-full overflow-hidden flex">
        {zones.map(([x0, x1, k]) => <div key={k} style={{ width: `${(100 * (x1 - x0)) / (max - min)}%`, background: fill[k] }} />)}
      </div>
      <div className="relative h-0">
        {value !== null && value !== undefined && (
          <div className="absolute -top-[15px] w-1 h-[20px] rounded bg-fg -translate-x-1/2" style={{ left: pos(value) }} />
        )}
      </div>
      <div className="relative h-5 text-sm text-dim mt-1">
        <span className="absolute -translate-x-full pr-1" style={{ left: pos(Math.min(w, a)) }}>{Math.min(w, a)}</span>
        <span className="absolute pl-1" style={{ left: pos(Math.max(w, a)) }}>{Math.max(w, a)}</span>
      </div>
    </div>
  );
}

export function KeyNumber({ label, tech, value, unit, d = 0, lim, inverse, help, suspect, caption, note, unverified }) {
  const v = value === null || value === undefined ? null : Number(value);
  const lv = v === null ? null : inverse
    ? (v < lim.alarm_below ? "alarm" : v < lim.warn_below ? "warn" : "ok")
    : (v >= lim.alarm ? "alarm" : v >= lim.warn ? "warn" : "ok");
  const word = unverified ? "Last known, unverified" : suspect ? "Suspect reading" : { ok: "Normal", warn: "Warning", alarm: "Alarm" }[lv] || "No data";
  const tone = unverified ? "info" : suspect ? "sensor" : lv || "info";
  const ring = !unverified && lv && lv !== "ok" && !suspect ? `border-2 ${TONE_BORDER[lv]}` : suspect && !unverified ? "border-2 border-sensor" : "";
  return (
    <Card pad="p-5" className={ring}>
      <div className="flex items-start justify-between gap-2">
        <Term tech={tech}><span className="text-[15px] text-dim">{label}</span></Term>
        <Help text={help} title={label} />
      </div>
      <div className={`mt-1 flex items-baseline gap-1 ${suspect || unverified ? "text-dim" : ""}`}>
        <span className="text-[34px] leading-none font-semibold tracking-tight">{fmt(v, d)}</span>
        <span className="text-base text-dim">{unit}</span>
        {caption && <span className="ml-auto text-sm text-dim">{caption}</span>}
      </div>
      <div className={`mt-1.5 text-sm font-semibold flex items-center gap-1.5 ${TONE_TEXT[tone]}`}>
        <Icon name={tone === "info" ? "info" : tone} className="w-4 h-4" />{note || word}
      </div>
      {lim && <LimitBar value={v} lim={lim} inverse={inverse} />}
    </Card>
  );
}

// ======================================================================= trends (48 h)
export function Trends({ series, limits, height = 250 }) {
  const [tab, setTab] = useState("temp");
  if (!series) return <Card title="Trends, last 48 hours">No samples yet.</Card>;
  const rated = series.rated_current_a;
  const data = series.t.map((t, k) => ({
    t: when(t), oil: series.oil_c[k], hs: series.hotspot_c[k], amb: series.ambient_c[k],
    R: series.IR?.[k] != null ? (100 * series.IR[k]) / rated : null,
    Y: series.IY?.[k] != null ? (100 * series.IY[k]) / rated : null,
    B: series.IB?.[k] != null ? (100 * series.IB[k]) / rated : null,
  }));
  const views = {
    temp: { unit: "°C", lines: [["hs", "Hot-spot", "var(--chart-1)", 2.2], ["oil", "Top oil (measured)", "var(--chart-2)", 1.8],
      ["amb", "Ambient", "var(--chart-3)", 1.6, "5 4"]],
    refs: [[limits.hotspot_c.limit, `Hot-spot limit ${limits.hotspot_c.limit} °C`, "var(--color-alarm)"]] },
    load: { unit: "% of rating", lines: [["R", "R phase", PHASE.R, 2], ["Y", "Y phase", PHASE.Y, 2], ["B", "B phase", PHASE.B, 2]],
      refs: [[limits.load_pct.alarm, `Rated load ${limits.load_pct.alarm} %`, "var(--color-alarm)"]] },
  };
  const v = views[tab];
  return (
    <Card title="Trends, last 48 hours" help={TWIN_HELP.trends(limits.voltage_v)}
      right={<Tabs className="mb-0 border-b-0" value={tab} onChange={setTab} tabs={[["temp", "Temperatures"], ["load", "Load"]]} />}>
      <div className="flex flex-wrap gap-5 text-sm mb-2">
        {v.lines.map(([k, name, c, w, dash]) => (
          <span key={k} className="flex items-center gap-2">
            <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={c} strokeWidth={w + 0.6} strokeDasharray={dash} /></svg>{name}
          </span>
        ))}
        <span className="text-dim ml-auto">{v.unit}</span>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="t" tick={axis} minTickGap={80} tickMargin={8} stroke="var(--color-edge)" />
          <YAxis tick={axis} width={48} domain={["auto", "auto"]} stroke="var(--color-edge)" />
          <Tooltip {...tip} formatter={(x) => (typeof x === "number" ? x.toFixed(1) : x)} />
          {v.refs.map(([y, label, c]) => (
            <ReferenceLine key={label} y={y} stroke={c} strokeDasharray="6 4" ifOverflow="extendDomain"
              label={{ value: label, fill: c, fontSize: 14, position: "insideTopRight" }} />
          ))}
          {v.lines.map(([k, name, c, w, dash]) => (
            <Line key={k} dataKey={k} name={name} stroke={c} strokeWidth={w} strokeDasharray={dash} dot={false}
              isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

// A small bar for the network key numbers: share of a whole, with a warn mark.
export function ShareBar({ value, max, warnAt, tone = "ok" }) {
  const pct = max ? Math.min(100, (100 * value) / max) : 0;
  const fill = tone === "ok" ? "var(--color-fg)" : COLOR[tone] || "var(--color-dim)";
  return (
    <div className="mt-3">
      <div className="relative h-2.5 rounded-full bg-edge overflow-hidden">
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pct}%`, background: fill, opacity: 0.75 }} />
        {warnAt !== undefined && <div className="absolute inset-y-0 w-0.5 bg-warn" style={{ left: `${(100 * warnAt) / max}%` }} />}
      </div>
      <div className="flex justify-between text-sm text-dim mt-1"><span>0</span><span>{max}</span></div>
    </div>
  );
}
