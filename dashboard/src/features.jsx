import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, axis, Fold, fmt, LEVEL, Panel, tip, ttt } from "./ui.jsx";

// Day 4 features, in priority order. Every number comes from the engine.

function Projection({ p, hsLimit, trip, height = 120 }) {
  const data = p.minutes.map((m, k) => ({ h: (m / 60).toFixed(1), oil: p.oil_c[k], hs: p.hotspot_c[k] }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid stroke="#1f2a37" />
        <XAxis dataKey="h" tick={axis} minTickGap={30} unit="h" />
        <YAxis tick={axis} domain={["auto", "auto"]} />
        <Tooltip {...tip} />
        <ReferenceLine y={hsLimit} stroke="#ef4444" strokeDasharray="4 4" />
        <ReferenceLine y={trip} stroke="#7d8a9a" strokeDasharray="4 4" />
        <Line dataKey="hs" name="hot-spot" stroke="#22d3ee" dot={false} isAnimationActive={false} />
        <Line dataKey="oil" name="top oil" stroke="#d7dee8" dot={false} strokeDasharray="3 3" isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------- 1. time-to-trip
export function Countdown({ s }) {
  const { hotspot: h, fixed: f, steady } = s.countdown;
  const lvl = s.status.level;
  const top = s.alerts_live[0];
  // Abnormal states lead with the problem; the countdown is detail beneath it.
  const head = lvl !== "ok" && top ? top.headline
    : h.already_above ? "Hot-spot limit exceeded"
    : h.minutes !== null ? `Hot-spot ${h.limit_c} °C in ${fmt(h.minutes, 0)} min` : "Hot-spot limit not reached";
  const headTone = lvl === "alarm" ? "text-alarm" : lvl === "warn" ? "text-warn" : "text-ok";
  const cdTone = h.already_above ? "text-alarm" : h.minutes !== null ? "text-warn" : "text-dim";
  const more = s.alerts_live.length - 1;
  return (
    <Panel title={lvl === "ok" ? "Status · thermal twin projection" : `${lvl === "alarm" ? "Alarm" : "Attention"} · ${s.alerts_live.length} active`}
      className={lvl === "alarm" ? "border-alarm border-2" : lvl === "warn" ? "border-warn border-2" : ""}>
      <div className="grid md:grid-cols-3 gap-3 items-center">
        <div>
          <div className={`text-2xl font-semibold leading-tight ${headTone}`}>{head}</div>
          {lvl !== "ok" && more > 0 && <div className="text-xs text-dim mt-1">+{more} more: {s.alerts_live.slice(1).map((a) => a.what).join("; ")}</div>}
          <div className="num text-sm mt-2 border-t border-edge pt-1">
            <span className={cdTone}>
              {h.already_above ? `Hot-spot over ${h.limit_c} °C` : h.minutes !== null ? `Hot-spot ${h.limit_c} °C in ${fmt(h.minutes, 0)} min` : `Hot-spot ${h.limit_c} °C not reached`}
            </span>
            {h.shed_pct > 0 ? <span className={cdTone}> · shed {fmt(h.shed_pct, 0)}%</span> : <span className="text-dim"> · no shed needed</span>}
            <span className="text-dim"> · safe load {fmt(100 * h.safe_load_pu, 0)}%</span>
          </div>
          <div className="num text-[11px] text-dim mt-1">
            settles at {fmt(h.settles_at_c)} °C hot-spot · ageing {fmt(steady.faa, 2)} pu ·
            ₹{fmt(steady.life_cost_inr_per_day, 0)}/day · cooling factor {fmt(s.countdown.cooling_factor_used, 3)}
          </div>
          <div className="num text-[11px] text-dim mt-1">
            Existing device ({f.limit_c} °C oil): {ttt(f)} — comparison only
          </div>
        </div>
        <div className="md:col-span-2">
          <Projection p={s.countdown.projection} hsLimit={h.limit_c} trip={f.limit_c} />
        </div>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 2. cooling degradation
export function Cooling({ s, series }) {
  const r = s.readings;
  const cool = s.alerts_live.find((a) => a.kind === "cooling");
  const data = series ? series.t.map((t, k) => ({ t: t.slice(5, 16).replace("T", " "), slope: series.slope[k], res: series.residual_k[k] })) : [];
  return (
    <Panel title="Cooling condition · rise-fit slope (1.00 = as designed)"
      right={<span className={`num text-xs ${cool ? "text-alarm" : "text-ok"}`}>{cool ? "DEGRADED" : "as designed"}</span>}>
      <div className="num text-sm mb-1">
        slope {fmt(r.slope, 3)} · oil {fmt(r.oil_c)} °C vs twin {fmt(r.oil_pred_c)} °C
        {cool && r.oil_c < s.countdown.fixed.limit_c && <span className="text-warn"> · raw oil below the {s.countdown.fixed.limit_c} °C trip — a threshold sees nothing</span>}
      </div>
      <ResponsiveContainer width="100%" height={110}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="#1f2a37" />
          <XAxis dataKey="t" tick={axis} minTickGap={60} />
          <YAxis tick={axis} domain={[0.9, "auto"]} />
          <Tooltip {...tip} />
          <ReferenceLine y={1.06} stroke="#ef4444" strokeDasharray="4 4" />
          <ReferenceLine y={1.0} stroke="#475569" />
          <Line dataKey="slope" stroke="#22d3ee" dot={false} isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      {cool && <div className="text-xs mt-1"><b className="text-dim">Why </b>{cool.why}</div>}
    </Panel>
  );
}

// ---------------------------------------------------------------- 3. life in rupees
export function Life({ s }) {
  const L = s.life;
  const hot = L.last_24h.rate_pu > 1;
  const summary = (
    <span className="num">
      {fmt(L.since_start.aged_h, 1)} aged h · ₹{fmt(L.since_start.inr, 0)} since start
      <span className={hot ? "text-alarm" : "text-dim"}> · 24 h {fmt(L.last_24h.rate_pu, 2)}×</span>
    </span>
  );
  const row = (label, x) => (
    <tr key={label} className="border-t border-edge">
      <td className="py-1 text-dim">{label}</td>
      <td className="num text-right">{fmt(x.aged_h, 2)} h</td>
      <td className="num text-right">{fmt(x.real_h, 1)} h</td>
      <td className={`num text-right ${x.rate_pu > 1 ? "text-alarm" : ""}`}>{fmt(x.rate_pu, 2)}×</td>
      <td className="num text-right">₹{fmt(x.inr, 0)}</td>
    </tr>
  );
  return (
    <Fold title="Insulation life" summary={summary}>
      <table className="w-full text-xs">
        <thead><tr className="text-dim"><th className="text-left font-normal">window</th><th className="text-right font-normal">aged</th>
          <th className="text-right font-normal">real</th><th className="text-right font-normal">rate</th><th className="text-right font-normal">value</th></tr></thead>
        <tbody>{row("last hour", L.last_1h)}{row("last 24 h", L.last_24h)}{row("since record start", L.since_start)}</tbody>
      </table>
      <div className="num text-[11px] text-dim mt-2">
        ₹{fmt(L.replacement_cost_inr, 0)} ÷ {fmt(L.normal_life_hours, 0)} h rated life = ₹{fmt(L.cost_per_aged_hour_inr, 2)} per aged hour (IEEE C57.91)
      </div>
    </Fold>
  );
}

// ---------------------------------------------------------------- 4. sensor isolation
// Rendered inside Live readings, and only when some channel is not trusted.
export function untrusted(s) {
  const sus = new Set((s.status.suspect_channels || "").split(",").filter(Boolean));
  return Object.entries(s.readings.channels)
    .filter(([c, v]) => sus.has(c) || !["ok", "step"].includes(v))
    .map(([c, v]) => [c, ["ok", "step"].includes(v) ? "offset" : v]);
}

export function ChannelGrid({ s }) {
  const bad = Object.fromEntries(untrusted(s));
  return (
    <div>
      <div className="grid grid-cols-8 gap-1">
        {Object.keys(s.readings.channels).map((c) => (
          <div key={c} className={`border rounded px-1 py-0.5 text-center ${bad[c] ? "border-warn text-warn border-2" : "border-edge text-dim"}`}>
            <div className="num text-[11px]">{c.replace("_temp", "")}</div>
            <div className="text-[9px] uppercase">{bad[c] || "trusted"}</div>
          </div>
        ))}
      </div>
      <div className="text-[11px] text-dim mt-1">
        Suspect: {Object.keys(bad).join(", ")}. Transformer diagnosis uses the remaining channels; a failed
        phase current is rebuilt from the other two.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 5. ambient-compensated threshold
export function AmbientTable({ s }) {
  const A = s.ambient_table;
  return (
    <div className="mt-2">
      <table className="w-full text-xs">
        <thead><tr className="text-dim">
          <th className="text-left font-normal">ambient</th>
          <th className="text-right font-normal">fixed {A.fixed_trip_c} °C oil</th>
          <th className="text-right font-normal">rise {A.rise_trip_k} K</th>
          <th className="text-right font-normal">hot-spot {A.hotspot_limit_c} °C</th>
        </tr></thead>
        <tbody>
          {A.rows.map((r) => (
            <tr key={r.ambient_c} className={`border-t border-edge ${r.current ? "text-accent" : ""}`}>
              <td className="num py-0.5">{r.ambient_c} °C{r.current ? " ◂ now" : ""}</td>
              <td className={`num text-right ${r.fixed_trip_pu === 0 ? "text-alarm" : ""}`}>{fmt(r.fixed_trip_pu, 2)} pu</td>
              <td className="num text-right">{fmt(r.rise_trip_pu, 2)} pu</td>
              <td className="num text-right">{fmt(r.hotspot_pu, 2)} pu</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-[11px] text-dim mt-2">
        A fixed oil trip carries the ambient inside it: at 48 °C it trips with no load. Recommendation: trip on top-oil
        rise over measured ambient ({A.rise_trip_k} K keeps the protection the {A.fixed_trip_c} °C setting gives at 30 °C),
        or on hot-spot, which the twin computes.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 6. what-if
export function WhatIf({ scenario, kva, i, s }) {
  const [load, setLoad] = useState(null);
  const [amb, setAmb] = useState(null);
  const [cf, setCf] = useState(null);
  const [w, setW] = useState(null);
  const timer = useRef(null);
  const L = load ?? Math.round(100 * s.countdown.load_pu);
  const A = amb ?? Math.round(s.countdown.ambient_c);
  const C = cf ?? s.countdown.cooling_factor_estimated;
  useEffect(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      api(`${scenario}/whatif?t=${i}&load=${L / 100}&ambient=${A}&cf=${C}${kva ? `&kva=${kva}` : ""}`).then(setW).catch(() => {});
    }, 120);
    return () => clearTimeout(timer.current);
  }, [scenario, kva, i, L, A, C]);
  const slider = (label, val, set, min, max, step, unit, d = 0) => (
    <label className="block text-xs">
      <div className="flex justify-between"><span className="text-dim">{label}</span><span className="num">{fmt(val, d)} {unit}</span></div>
      <input type="range" min={min} max={max} step={step} value={val} className="w-full accent-cyan-400"
        onChange={(e) => set(Number(e.target.value))} />
    </label>
  );
  return (
    <Panel title="What-if · hand the judge the slider"
      right={<button className="text-[11px] text-dim border border-edge rounded px-2" onClick={() => { setLoad(null); setAmb(null); setCf(null); }}>reset to now</button>}>
      <div className="grid md:grid-cols-3 gap-3">
        <div className="space-y-2">
          {slider("Load", L, setLoad, 0, 150, 1, "%")}
          {slider("Ambient", A, setAmb, 5, 50, 1, "°C")}
          {slider("Cooling factor", C, setCf, 1.0, 1.6, 0.01, "×", 2)}
        </div>
        {w && (
          <div className="num text-sm space-y-1">
            <div>hot-spot settles <b className={w.steady.hotspot_c > w.hotspot.limit_c ? "text-alarm" : ""}>{fmt(w.steady.hotspot_c)} °C</b></div>
            <div>top oil settles {fmt(w.steady.top_oil_c)} °C</div>
            <div>ageing <b className={w.steady.faa > 1 ? "text-alarm" : ""}>{fmt(w.steady.faa, 2)}×</b> rated · ₹{fmt(w.steady.life_cost_inr_per_day, 0)}/day</div>
            <div>hot-spot {w.hotspot.limit_c} °C: <b>{ttt(w.hotspot)}</b></div>
            <div className="text-dim">device {w.fixed.limit_c} °C: {ttt(w.fixed)}</div>
            <div className="text-dim">safe load {fmt(100 * w.hotspot.safe_load_pu, 0)}%</div>
          </div>
        )}
        {w && <Projection p={w.projection} hsLimit={w.hotspot.limit_c} trip={w.fixed.limit_c} height={140} />}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 8. self-test
export function SelfTest({ kva }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = () => { setBusy(true); api(`selftest?kva=${kva || 100}`).then(setRes).finally(() => setBusy(false)); };
  return (
    <Panel title="Fault-injection self-test"
      right={<button className="text-xs border border-accent text-accent rounded px-2 py-0.5" disabled={busy} onClick={run}>{busy ? "running…" : "Run"}</button>}>
      {!res && <div className="text-xs text-dim">Injects cooling degradation of increasing severity into a fresh simulated record for this nameplate and measures the smallest the twin catches.</div>}
      {res && (
        <>
          <div className="num text-sm mb-1">Minimum detectable cooling degradation: <b className="text-accent">{res.min_detectable_pct}%</b>
            <span className="text-dim"> (≈{fmt(res.runs.find((x) => x.severity_pct === res.min_detectable_pct)?.extra_rise_at_rated_k, 1)} K extra rise at rated load)</span></div>
          <table className="w-full text-xs num">
            <thead><tr className="text-dim"><th className="text-left font-normal">severity</th><th className="text-right font-normal">extra rise</th><th className="text-right font-normal">caught</th><th className="text-right font-normal">after</th><th className="text-right font-normal">false before</th></tr></thead>
            <tbody>{res.runs.map((x) => (
              <tr key={x.severity_pct} className="border-t border-edge">
                <td>{x.severity_pct}%</td><td className="text-right">{fmt(x.extra_rise_at_rated_k, 1)} K</td>
                <td className={`text-right ${x.detected ? "text-ok" : "text-alarm"}`}>{x.detected ? "yes" : "no"}</td>
                <td className="text-right">{x.latency_h === null ? "—" : `${fmt(x.latency_h, 1)} h`}</td>
                <td className={`text-right ${x.alarm_before_fault ? "text-alarm" : ""}`}>{x.alarm_before_fault ? "yes" : "none"}</td>
              </tr>))}</tbody>
          </table>
          <div className="text-[11px] text-dim mt-1">{res.note}</div>
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- 9. GPRS payload
export function Gprs({ s }) {
  const g = s.gprs;
  return (
    <Panel title="GPRS alert payload preview" right={<span className="text-[10px] text-warn">format assumed</span>}>
      <div className="num text-sm bg-ink border border-edge rounded p-2 break-all">{g.message}</div>
      <div className="num text-[11px] text-dim mt-1">{g.length} / 160 chars · {g.fields.join(" · ")}</div>
      <div className="text-[11px] text-dim mt-1">CPRI's message format is not yet known; field order will be matched to it.</div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 10. digital nameplate
export function NameplateSelect({ kva, setKva, rated }) {
  return (
    <label className="text-xs text-dim flex items-center gap-1">
      nameplate
      <select className="bg-ink border border-edge rounded px-1 py-1 text-sm text-fg num" value={kva ?? ""}
        onChange={(e) => setKva(e.target.value ? Number(e.target.value) : null)}>
        <option value="">as recorded ({rated} kVA)</option>
        {[25, 63, 100, 250].map((k) => <option key={k} value={k}>{k} kVA</option>)}
      </select>
    </label>
  );
}

// ---------------------------------------------------------------- notifications
const TIER_TONE = { P1: "text-alarm border-alarm", P2: "text-warn border-warn", P3: "text-accent border-accent", DIGEST: "text-dim border-edge" };

export function Notifications({ scenario, kva, i }) {
  const [d, setD] = useState(null);
  const [open, setOpen] = useState(null);
  useEffect(() => {
    api(`${scenario}/notifications?t=${i}${kva ? `&kva=${kva}` : ""}`).then(setD).catch(() => {});
  }, [scenario, kva, i]);
  if (!d) return <Panel title="Notifications sent">loading…</Panel>;
  const shown = d.entries;
  return (
    <Panel title="Notifications sent" className="overflow-auto max-h-[420px]"
      right={<span className="text-[10px] text-warn">{d.mode === "replay" ? "REPLAY · outbox only, no real email/SMS" : "LIVE"}</span>}>
      <div className="text-[11px] text-dim mb-2">
        {shown.length} shown up to the cursor · {d.total_planned} planned for the whole record from {d.episodes} alert episodes ·
        SMTP {d.smtp_configured ? "configured" : "not configured"} · outbox <span className="num">{d.outbox.split("/").slice(-2).join("/")}</span>
      </div>
      {shown.length === 0 && <div className="text-sm text-dim">Nothing sent yet.</div>}
      <table className="w-full text-xs">
        <tbody>
          {shown.map((m) => (
            <tr key={m.id} className="border-t border-edge align-top cursor-pointer" onClick={() => setOpen(open === m.id ? null : m.id)}>
              <td className="num py-1 pr-2 whitespace-nowrap text-dim">{m.time.slice(5, 16).replace("T", " ")}</td>
              <td className="pr-2"><span className={`border rounded px-1 num ${TIER_TONE[m.tier] || ""}`}>{m.tier}</span></td>
              <td className="pr-2 num">{m.type}</td>
              <td className="pr-2">{m.recipient_name}<div className="text-[10px] text-dim">{m.channel} → {m.delivered_via}</div></td>
              <td>
                {m.reason}
                {m.attach_pdf && (m.meta.attachment_status === "ready"
                  ? <a className="text-accent ml-2" onClick={(e) => e.stopPropagation()}
                      href={`/api/${scenario}/outbox/${m.attachment.split("/").pop()}${kva ? `?kva=${kva}` : ""}`}>PDF</a>
                  : <span className="text-dim ml-2">PDF {m.meta.attachment_status}</span>)}
                {open === m.id && (
                  <pre className="whitespace-pre-wrap text-[11px] text-fg/80 bg-ink border border-edge rounded p-2 mt-1">
{`Subject: ${m.subject}\n\n${m.body}\n\nSMS: ${m.sms}\n${m.note}`}
                  </pre>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
