// Shared helpers for the dashboard.

import { useState } from "react";

export const api = (path) => fetch(`/api/${path}`).then((r) => {
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
});

export const LEVEL = {
  ok: "text-ok border-ok", warn: "text-warn border-warn", alarm: "text-alarm border-alarm",
  info: "text-dim border-edge",
};
export const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
export const hhmm = (iso) => iso?.slice(5, 16).replace("T", " ");
export const ttt = (x) => (x.already_above ? "exceeded" : x.minutes !== null ? `${fmt(x.minutes, 0)} min` : "none");
// One colour rule for every score in the UI: green > 80, amber 40-80, red < 40.
export const band = (sc) => (sc === null || sc === undefined ? null : sc > 80 ? "ok" : sc >= 40 ? "warn" : "alarm");
export const TEXT = { ok: "text-ok", warn: "text-warn", alarm: "text-alarm" };
export const BG = { ok: "bg-ok", warn: "bg-warn", alarm: "bg-alarm" };
export const BORDER = { ok: "border-edge", warn: "border-warn", alarm: "border-alarm" };

export const axis = { stroke: "#7d8a9a", fontSize: 10 };
export const tip = { contentStyle: { background: "#121821", border: "1px solid #1f2a37", fontSize: 11 } };

// A panel whose body folds away; the summary stays visible when closed so a
// collapsed panel can still show an abnormal number in its colour.
export function Fold({ title, summary, children, open: initial = false, className = "" }) {
  const [open, setOpen] = useState(initial);
  return (
    <section className={`bg-panel border border-edge rounded-md p-3 ${className}`}>
      <button className="w-full flex justify-between items-baseline text-left" onClick={() => setOpen(!open)}>
        <h2 className="text-[11px] uppercase tracking-widest text-dim">{open ? "▾" : "▸"} {title}</h2>
        {!open && <span className="text-xs">{summary}</span>}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </section>
  );
}

export function Panel({ title, children, className = "", right = null }) {
  return (
    <section className={`bg-panel border border-edge rounded-md p-3 ${className}`}>
      <div className="flex justify-between items-baseline mb-2">
        <h2 className="text-[11px] uppercase tracking-widest text-dim">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}
