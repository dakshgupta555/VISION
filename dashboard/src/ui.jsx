// Shared helpers for the dashboard: data access, formatting, and the small
// set of building blocks every page uses (card, help, status pill, charts).

import { useEffect, useRef, useState } from "react";

export const api = (path) => fetch(`/api/${path}`).then((r) => {
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
});

export const LEVEL = {
  ok: "text-ok border-ok", warn: "text-warn border-warn", alarm: "text-alarm border-alarm",
  sensor: "text-sensor border-sensor", info: "text-dim border-edge",
};
// Every number on the dashboard: Indian digit grouping (8,99,647), fixed decimals.
// Conventions: rupees no decimals (inr), hours at most 1 (hrs), pu at most 2.
export const fmt = (v, d = 1) => {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const x = Math.abs(Number(v)) < 0.5 * 10 ** -d ? 0 : Number(v);          // no "-0.0"
  return x.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
};
export const inr = (v) => (v === null || v === undefined ? "—" : `₹${fmt(v, 0)}`);
export const hrs = (v) => fmt(v, 1);
// "2026-04-22T11:00:00" -> "22 Apr, 11:00"
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const when = (iso) => (iso ? `${Number(iso.slice(8, 10))} ${MON[Number(iso.slice(5, 7)) - 1]}, ${iso.slice(11, 16)}` : "—");
export const hhmm = (iso) => iso?.slice(5, 16).replace("T", " ");
export const ttt = (x) => (x.already_above ? "exceeded" : x.minutes !== null ? `${fmt(x.minutes, 0)} min` : "none");
export const duration = (min) => (min === null || min === undefined ? "—"
  : min < 90 ? `${fmt(min, 0)} min` : `${fmt(min / 60, 1)} h`);
// One colour rule for every score in the UI; the bands come from the backend
// (display_limits.health) where a page has them, these are the same values.
export const band = (sc, b = { warn_below: 80, alarm_below: 40 }) =>
  (sc === null || sc === undefined ? null : sc >= b.warn_below ? "ok" : sc >= b.alarm_below ? "warn" : "alarm");
export const TEXT = { ok: "text-ok", warn: "text-warn", alarm: "text-alarm", sensor: "text-sensor" };
export const BG = { ok: "bg-ok", warn: "bg-warn", alarm: "bg-alarm", sensor: "bg-sensor" };
export const BORDER = { ok: "border-edge", warn: "border-warn", alarm: "border-alarm", sensor: "border-sensor" };

// Priorities in words. The backend ranks 1 (alarm), 2 (warning), 3 (nothing to do).
export const PRIORITY = {
  1: { label: "Act now", tone: "alarm" },
  2: { label: "Plan this week", tone: "warn" },
  3: { label: "For information", tone: "ok" },
};

// Charts read their colours from CSS variables, so they follow the theme.
export const axis = { stroke: "var(--chart-axis)", fontSize: 14, fill: "var(--chart-axis)" };
export const grid = "var(--chart-grid)";
export const tip = {
  contentStyle: { background: "var(--tip-bg)", border: "1px solid var(--color-edge)", borderRadius: 8, fontSize: 14,
    color: "var(--color-fg)" },
  labelStyle: { color: "var(--color-dim)" },
};
export const PHASE = { R: "var(--phase-r)", Y: "var(--phase-y)", B: "var(--phase-b)" };

// ---------------------------------------------------------------- theme
export function useTheme() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("dtwin-theme") || "light"; } catch { return "light"; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("dtwin-theme", theme); } catch { /* private window */ }
  }, [theme]);
  return [theme, setTheme];
}

// ---------------------------------------------------------------- icons
export function Icon({ name, className = "w-4 h-4" }) {
  const p = {
    ok: <path d="M4 10.5l4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />,
    warn: <><path d="M10 2.5l8.5 15h-17z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="M10 8v4.5M10 15v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></>,
    alarm: <><path d="M6.5 2.5h7l4 4v7l-4 4h-7l-4-4v-7z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="M10 6v5M10 14v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></>,
    sensor: <><circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M10 5.5v5l3 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></>,
    info: <><circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M10 9v5M10 6v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></>,
    gear: <><circle cx="10" cy="10" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M10 1.8v2.4M10 15.8v2.4M1.8 10h2.4M15.8 10h2.4M4.2 4.2l1.7 1.7M14.1 14.1l1.7 1.7M4.2 15.8l1.7-1.7M14.1 5.9l1.7-1.7"
        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></>,
    chevron: <path d="M7 4l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  }[name];
  return <svg viewBox="0 0 20 20" className={className} aria-hidden="true">{p}</svg>;
}

// Status in a shape, a word and a colour; never colour alone.
export function StatusPill({ level, text, big = false }) {
  const tone = { ok: "bg-ok", warn: "bg-warn", alarm: "bg-alarm", sensor: "bg-sensor", info: "bg-dim" }[level] || "bg-dim";
  return (
    <span className={`inline-flex items-center gap-2 rounded-full text-white font-semibold ${tone}
      ${big ? "px-4 py-1.5 text-base" : "px-2.5 py-0.5 text-sm"}`}>
      <Icon name={level === "info" ? "info" : level} className={big ? "w-5 h-5" : "w-4 h-4"} />{text}
    </span>
  );
}

// ---------------------------------------------------------------- help
// A small "?" that opens a 2-3 line explanation: what it is, how it is
// computed, what normal looks like.
export function Help({ text, title }) {
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
  if (!text) return null;
  return (
    <span ref={ref} className="relative inline-block align-middle">
      <button type="button" aria-label={`What is ${title || "this"}?`} aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="w-6 h-6 rounded-full border border-edge text-dim text-sm leading-none hover:text-fg hover:border-dim">?</button>
      {open && (
        <span role="dialog" className="absolute right-0 top-8 z-50 w-80 card p-4 text-sm font-normal text-fg text-left
          whitespace-normal leading-relaxed">
          {Array.isArray(text) ? text.map((t, k) => <span key={k} className={`block ${k ? "mt-2" : ""}`}>{t}</span>) : text}
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------- cards
export function Card({ title, help, right, children, className = "", pad = "p-6", titleTip }) {
  return (
    <section className={`card ${pad} ${className}`}>
      {(title || help || right) && (
        <div className="flex items-start justify-between gap-3 mb-4">
          {title && <h2 className="text-base font-semibold text-fg" title={titleTip}>{title}</h2>}
          <div className="flex items-center gap-2 ml-auto">{right}<Help text={help} title={title} /></div>
        </div>
      )}
      {children}
    </section>
  );
}
export const Panel = Card;

// A card whose body folds away; the summary stays visible when closed so a
// collapsed card can still show an abnormal number in its colour.
export function Fold({ title, summary, children, open: initial = false, className = "", help }) {
  const [open, setOpen] = useState(initial);
  return (
    <section className={`card p-6 ${className}`}>
      <div className="flex items-center gap-3">
        <button className="flex-1 flex items-center gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
          <Icon name="chevron" className={`w-4 h-4 text-dim transition-transform ${open ? "rotate-90" : ""}`} />
          <h2 className="text-base font-semibold">{title}</h2>
          {!open && summary && <span className="ml-auto text-sm text-dim">{summary}</span>}
        </button>
        <Help text={help} title={title} />
      </div>
      {open && <div className="mt-4">{children}</div>}
    </section>
  );
}

// A label with its technical name in the tooltip.
export function Term({ children, tech }) {
  return <span title={tech} className={tech ? "underline decoration-dotted decoration-dim underline-offset-4 cursor-help" : ""}>{children}</span>;
}

export function Tabs({ tabs, value, onChange, className = "mb-4" }) {
  return (
    <div role="tablist" className={`flex gap-1 border-b border-edge ${className}`}>
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} onClick={() => onChange(k)}
          className={`px-3 py-2 text-sm -mb-px border-b-2 ${value === k ? "border-fg text-fg font-semibold" : "border-transparent text-dim hover:text-fg"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}
