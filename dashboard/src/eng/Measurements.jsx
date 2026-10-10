import { useState } from "react";
import { BG, band, Card, fmt, LEVEL, PRIORITY, TEXT, when } from "../ui.jsx";
import { HELP } from "../help.js";
import { AmbientTable, Cooling, Countdown, Gprs, Life, Notifications, SelfTest, WhatIf } from "../features.jsx";
import { InjectPanel } from "../live.jsx";
import { Details } from "../overview/Overview.jsx";

// Engineering tab, "Measurements and alarms": everything the old dashboard
// showed, with full history and the technical names.

function HealthBreakdown({ s }) {
  const h = s.health;
  const B = s.display_limits.health;
  return (
    <Card title="Health index, by component" help={HELP.healthBreakdown}
      right={<span className="text-base font-semibold">{fmt(h.index, 0)} of 100</span>}>
      <div className="space-y-3">
        {h.components.map((c) => {
          const b = band(c.score, B) ?? "alarm";
          return (
            <div key={c.key}>
              <div className="flex justify-between text-[15px]">
                <span>{c.label} <span className="text-dim text-sm">weight {Math.round(100 * c.weight)} %</span></span>
                <span>{fmt(c.value, 3)} {c.unit}: <b className={b === "ok" ? "" : TEXT[b]}>{fmt(c.score, 0)}</b></span>
              </div>
              <div className="h-2 rounded-full bg-ink mt-1"><div className={`h-2 rounded-full ${BG[b]}`} style={{ width: `${Math.max(c.score ?? 0, 3)}%` }} /></div>
              <div className="text-sm text-dim mt-0.5">100 at {c.good}, 0 at {fmt(c.bad, 3)} (where its alarm fires)</div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function AlertHistory({ groups }) {
  const [open, setOpen] = useState({});
  return (
    <Card title="Alert history, repeats grouped" help={HELP.alerts} className="max-h-[560px] overflow-auto">
      {groups.length === 0 && <p className="text-dim">No alerts so far.</p>}
      <ul className="space-y-4">
        {groups.map((g) => {
          const k = g.kind + g.what;
          const tone = g.family === "sensor" ? "sensor" : g.level;
          return (
            <li key={k} className={`border-l-4 pl-3 ${LEVEL[tone] || ""} ${g.active ? "" : "opacity-60"}`}>
              <button className="w-full flex justify-between gap-3 text-left" onClick={() => setOpen({ ...open, [k]: !open[k] })}>
                <span className="font-semibold text-fg">{g.what}{g.count > 1 && <span className="font-normal text-dim">, {g.count} times</span>}</span>
                <span className="text-sm text-dim whitespace-nowrap">{g.active ? "active now" : `last ${when(g.last)}`}</span>
              </button>
              <p className="text-sm text-dim">{g.family === "sensor" ? "Instrument fault" : g.family === "asset" ? "Transformer fault" : "Other"}, first {when(g.first)}</p>
              <p className="text-[15px] text-fg mt-1"><b>Why: </b>{g.why}</p>
              <p className="text-[15px] text-fg"><b>Do: </b>{g.action}</p>
              {open[k] && (
                <ul className="text-sm text-dim mt-1 ml-3">
                  {g.episodes.map((e, j) => <li key={j}>{when(e.start)} to {e.active ? "now" : when(e.end)}</li>)}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Recommendations({ s }) {
  return (
    <Card title="Recommended actions" help={HELP.whats}>
      <ol className="space-y-3">
        {s.recommendations.map((r, k) => {
          const p = PRIORITY[r.priority] || PRIORITY[3];
          return (
            <li key={k} className="flex gap-3 items-start">
              <span className={`shrink-0 text-sm font-semibold px-2 py-0.5 rounded-md border ${LEVEL[p.tone]}`}>{p.label}</span>
              <span className="text-[15px]">{r.text}<span className="block text-sm text-dim">{r.because}</span></span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function Protection({ s }) {
  const f = s.countdown.fixed, ref = s.reference;
  return (
    <Card title="Existing protection setting (comparison)" help={HELP.protection}>
      <p className="text-[15px]">
        Fixed {f.limit_c} °C oil trip: <b>{ref.fixed_trip_active ? `would have tripped at ${when(ref.since)}`
          : f.minutes !== null ? `would trip in ${fmt(f.minutes, 0)} min` : "not reached at this load"}</b>.
        It would have fired {ref.trip_episodes_24h} time{ref.trip_episodes_24h === 1 ? "" : "s"} in the last 24 h.
      </p>
      <p className="text-sm text-dim mt-1">Oil {fmt(f.now_c)} °C at {fmt(s.countdown.ambient_c)} °C ambient leaves
        {" "}{fmt(f.limit_c - s.countdown.ambient_c)} K of rise before that setting trips. The twin at the same moment:
        {" "}{s.alerts_live.length ? s.alerts_live.map((a) => a.what).join("; ") : "no fault"}.</p>
      <AmbientTable s={s} />
    </Card>
  );
}

export default function Measurements({ s, series, scen, kva, mode }) {
  return (
    <div className="space-y-6">
      <Details s={s} />
      <div className="grid gap-6 xl:grid-cols-2 items-start">
        <Countdown s={s} />
        <Cooling s={s} series={series} />
        <Recommendations s={s} />
        <HealthBreakdown s={s} />
        <AlertHistory groups={s.alert_groups} />
        <div className="space-y-6"><Life s={s} /><Protection s={s} /></div>
      </div>
      {mode === "live" && <InjectPanel />}
      <WhatIf scenario={scen} kva={mode === "live" ? null : kva} i={s.i} s={s} />
      <div className="grid gap-6 xl:grid-cols-2">
        <Notifications scenario={scen} kva={mode === "live" ? null : kva} i={mode === "live" ? s.timestamp : s.i} />
        <div className="space-y-6"><Gprs s={s} /><SelfTest kva={kva ?? s.nameplate.rating_kva} /></div>
      </div>
    </div>
  );
}
