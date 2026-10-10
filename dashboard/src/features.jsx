import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, axis, Card, duration, Fold, fmt, grid, LEVEL, tip, ttt, when } from "./ui.jsx";
import { HELP } from "./help.js";

// Engineering features. Every number comes from the engine.

function LineKey({ items }) {
  return (
    <div className="flex flex-wrap gap-5 text-sm mb-1">
      {items.map(([name, c, dash]) => (
        <span key={name} className="flex items-center gap-2">
          <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={c} strokeWidth="2.4" strokeDasharray={dash} /></svg>{name}
        </span>
      ))}
    </div>
  );
}

function Projection({ p, hsLimit, trip, height = 180 }) {
  const data = p.minutes.map((m, k) => ({ h: (m / 60).toFixed(1), oil: p.oil_c[k], hs: p.hotspot_c[k] }));
  return (
    <div className="min-w-0">
      <LineKey items={[["Hot-spot", "var(--chart-1)"], ["Top oil", "var(--chart-2)", "5 4"]]} />
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="h" tick={axis} minTickGap={30} unit=" h" stroke="var(--color-edge)" />
          <YAxis tick={axis} width={44} domain={["auto", "auto"]} stroke="var(--color-edge)" />
          <Tooltip {...tip} />
          <ReferenceLine y={hsLimit} stroke="var(--color-alarm)" strokeDasharray="6 4" ifOverflow="extendDomain"
            label={{ value: `Hot-spot limit ${hsLimit} °C`, fill: "var(--color-alarm)", fontSize: 14, position: "insideTopRight" }} />
          {trip && <ReferenceLine y={trip} stroke="var(--chart-ref)" strokeDasharray="3 4"
            label={{ value: `Existing protection setting ${trip} °C (comparison)`, fill: "var(--chart-axis)", fontSize: 14, position: "insideBottomRight" }} />}
          <Line dataKey="hs" name="Hot-spot" stroke="var(--chart-1)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
          <Line dataKey="oil" name="Top oil" stroke="var(--chart-2)" strokeDasharray="5 4" strokeWidth={1.8} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------- 1. time-to-limit projection
export function Countdown({ s }) {
  const { hotspot: h, steady } = s.countdown;
  return (
    <Card title="Where the temperatures are heading" help={HELP.projection}>
      <p className="text-[15px]">
        {h.already_above ? <b className="text-alarm">Hot-spot is above {h.limit_c} °C now.</b>
          : h.minutes !== null ? <b className="text-warn">Hot-spot reaches {h.limit_c} °C in {duration(h.minutes)}.</b>
            : <>Hot-spot stays below {h.limit_c} °C at the present load.</>}
        {" "}{h.shed_pct > 0 ? `Shed ${fmt(h.shed_pct, 0)} % of the load to stay under it.` : "No load needs to be shed."}
      </p>
      <p className="text-sm text-dim mt-1">
        Settles at {fmt(h.settles_at_c)} °C. Safe load here: {fmt(100 * h.safe_load_pu, 0)} %. Insulation would wear at
        {" "}{fmt(steady.faa, 2)}× normal speed, ₹{fmt(steady.life_cost_inr_per_day, 0)} of asset value per day.
      </p>
      <div className="mt-3"><Projection p={s.countdown.projection} hsLimit={h.limit_c} /></div>
    </Card>
  );
}

// ---------------------------------------------------------------- 2. cooling degradation
export function Cooling({ s, series }) {
  const r = s.readings;
  const cool = s.alerts_live.find((a) => a.kind === "cooling");
  const data = series ? series.t.map((t, k) => ({ t: when(t), slope: series.slope[k] })) : [];
  return (
    <Card title="Cooling condition" help={HELP.cooling}
      right={<span className={`text-sm font-semibold ${cool ? "text-alarm" : "text-ok"}`}>{cool ? "Degraded" : "As designed"}</span>}>
      <p className="text-[15px]">Cooling fit slope <b>{fmt(r.slope, 3)}</b> (1.00 = as designed, alarm at 1.06). Oil {fmt(r.oil_c)} °C
        against {fmt(r.oil_pred_c)} °C predicted.</p>
      {cool && r.oil_c < s.countdown.fixed.limit_c && (
        <p className="text-sm text-warn mt-1">Raw oil is still below the existing {s.countdown.fixed.limit_c} °C setting: a fixed threshold sees nothing yet.</p>
      )}
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="t" tick={axis} minTickGap={80} tickMargin={6} stroke="var(--color-edge)" />
          <YAxis tick={axis} width={44} domain={[0.95, "auto"]} tickFormatter={(v) => v.toFixed(2)} stroke="var(--color-edge)" />
          <Tooltip {...tip} />
          <ReferenceLine y={1.06} stroke="var(--color-alarm)" strokeDasharray="6 4" ifOverflow="extendDomain"
            label={{ value: "Cooling alarm 1.06", fill: "var(--color-alarm)", fontSize: 14, position: "insideTopRight" }} />
          <ReferenceLine y={1.0} stroke="var(--chart-3)" />
          <Line dataKey="slope" name="Cooling fit slope" stroke="var(--chart-1)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      {cool && <p className="text-sm mt-2"><b>Why: </b>{cool.why}</p>}
    </Card>
  );
}

// ---------------------------------------------------------------- 3. life in rupees
export function Life({ s }) {
  const L = s.life;
  const hot = L.last_24h.rate_pu > 1;
  const row = (label, x) => (
    <tr key={label} className="border-t border-edge">
      <td className="py-1.5 text-dim">{label}</td>
      <td className="text-right">{fmt(x.aged_h, 2)} h</td>
      <td className="text-right">{fmt(x.real_h, 1)} h</td>
      <td className={`text-right ${x.rate_pu > 1 ? "text-alarm font-semibold" : ""}`}>{fmt(x.rate_pu, 2)}×</td>
      <td className="text-right">₹{fmt(x.inr, 0)}</td>
    </tr>
  );
  return (
    <Fold title="Insulation life used" help={HELP.life} open
      summary={<span className={hot ? "text-alarm" : ""}>{fmt(L.since_start.aged_h, 1)} h, ₹{fmt(L.since_start.inr, 0)} since the record began</span>}>
      <table className="w-full text-[15px]">
        <thead><tr className="text-dim text-sm"><th className="text-left font-normal">Window</th><th className="text-right font-normal">Life used</th>
          <th className="text-right font-normal">Clock time</th><th className="text-right font-normal">Wear speed</th><th className="text-right font-normal">Value</th></tr></thead>
        <tbody>{row("Last hour", L.last_1h)}{row("Last 24 h", L.last_24h)}{row("Since the record began", L.since_start)}</tbody>
      </table>
      <p className="text-sm text-dim mt-2">
        ₹{fmt(L.replacement_cost_inr, 0)} replacement cost over {fmt(L.normal_life_hours, 0)} h rated life is ₹{fmt(L.cost_per_aged_hour_inr, 2)} per hour of life used (IEEE C57.91).
      </p>
    </Fold>
  );
}

// ---------------------------------------------------------------- 4. sensor isolation
export function untrusted(s) {
  const sus = new Set((s.status.suspect_channels || "").split(",").filter(Boolean));
  return Object.entries(s.readings.channels)
    .filter(([c, v]) => sus.has(c) || !["ok", "step"].includes(v))
    .map(([c, v]) => [c, ["ok", "step"].includes(v) ? "offset" : v]);
}

// ---------------------------------------------------------------- 5. ambient-compensated threshold
export function AmbientTable({ s }) {
  const A = s.ambient_table;
  return (
    <div className="mt-3">
      <table className="w-full text-[15px]">
        <thead><tr className="text-dim text-sm">
          <th className="text-left font-normal">Ambient</th>
          <th className="text-right font-normal">Fixed {A.fixed_trip_c} °C oil setting</th>
          <th className="text-right font-normal">Rise of {A.rise_trip_k} K</th>
          <th className="text-right font-normal">Hot-spot {A.hotspot_limit_c} °C</th>
        </tr></thead>
        <tbody>
          {A.rows.map((r) => (
            <tr key={r.ambient_c} className={`border-t border-edge ${r.current ? "font-semibold" : ""}`}>
              <td className="py-1">{r.ambient_c} °C{r.current ? " (now)" : ""}</td>
              <td className={`text-right ${r.fixed_trip_pu === 0 ? "text-alarm" : ""}`}>{fmt(r.fixed_trip_pu, 2)} pu</td>
              <td className="text-right">{fmt(r.rise_trip_pu, 2)} pu</td>
              <td className="text-right">{fmt(r.hotspot_pu, 2)} pu</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-sm text-dim mt-2">
        Largest load each setting allows. A fixed oil setting carries the ambient inside it: at 48 °C it trips with no load.
        Recommended: trip on top-oil rise over measured ambient ({A.rise_trip_k} K gives the protection the {A.fixed_trip_c} °C
        setting gives at 30 °C), or on the hot-spot the twin computes.
      </p>
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
    <label className="block text-[15px]">
      <div className="flex justify-between"><span className="text-dim">{label}</span><span className="font-semibold">{fmt(val, d)} {unit}</span></div>
      <input type="range" min={min} max={max} step={step} value={val} className="w-full"
        onChange={(e) => set(Number(e.target.value))} />
    </label>
  );
  return (
    <Card title="What if?" help={HELP.whatif}
      right={<button className="twin-btn" onClick={() => { setLoad(null); setAmb(null); setCf(null); }}>Back to now</button>}>
      <div className="grid lg:grid-cols-3 gap-6">
        <div className="space-y-3">
          {slider("Load", L, setLoad, 0, 150, 1, "%")}
          {slider("Ambient", A, setAmb, 5, 50, 1, "°C")}
          {slider("Cooling factor", C, setCf, 1.0, 1.6, 0.01, "×", 2)}
        </div>
        {w && (
          <div className="text-[15px] space-y-1.5">
            <div>Hot-spot settles at <b className={w.steady.hotspot_c > w.hotspot.limit_c ? "text-alarm" : ""}>{fmt(w.steady.hotspot_c)} °C</b></div>
            <div>Top oil settles at {fmt(w.steady.top_oil_c)} °C</div>
            <div>Insulation wears at <b className={w.steady.faa > 1 ? "text-alarm" : ""}>{fmt(w.steady.faa, 2)}×</b> normal speed, ₹{fmt(w.steady.life_cost_inr_per_day, 0)} per day</div>
            <div>Hot-spot {w.hotspot.limit_c} °C: <b>{ttt(w.hotspot) === "none" ? "not reached" : ttt(w.hotspot)}</b></div>
            <div className="text-dim">Safe load {fmt(100 * w.hotspot.safe_load_pu, 0)} %</div>
            <div className="text-dim">Existing {w.fixed.limit_c} °C setting (comparison): {ttt(w.fixed) === "none" ? "not reached" : ttt(w.fixed)}</div>
          </div>
        )}
        {w && <Projection p={w.projection} hsLimit={w.hotspot.limit_c} height={190} />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- 8. self-test
export function SelfTest({ kva }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = () => { setBusy(true); api(`selftest?kva=${kva || 100}`).then(setRes).finally(() => setBusy(false)); };
  return (
    <Card title="Detection self-test" help={HELP.selftest}
      right={<button className="twin-btn twin-btn-primary" disabled={busy} onClick={run}>{busy ? "Running…" : "Run"}</button>}>
      {!res && <p className="text-[15px] text-dim">Finds the smallest cooling fault the twin can catch on this nameplate.</p>}
      {res && (
        <>
          <p className="text-[15px] mb-2">Smallest cooling degradation caught: <b>{res.min_detectable_pct} %</b>
            <span className="text-dim"> (about {fmt(res.runs.find((x) => x.severity_pct === res.min_detectable_pct)?.extra_rise_at_rated_k, 1)} K extra rise at rated load)</span></p>
          <table className="w-full text-[15px]">
            <thead><tr className="text-dim text-sm"><th className="text-left font-normal">Severity</th><th className="text-right font-normal">Extra rise</th><th className="text-right font-normal">Caught</th><th className="text-right font-normal">After</th><th className="text-right font-normal">False alarms before</th></tr></thead>
            <tbody>{res.runs.map((x) => (
              <tr key={x.severity_pct} className="border-t border-edge">
                <td className="py-1">{x.severity_pct} %</td><td className="text-right">{fmt(x.extra_rise_at_rated_k, 1)} K</td>
                <td className={`text-right ${x.detected ? "text-ok" : "text-alarm"}`}>{x.detected ? "yes" : "no"}</td>
                <td className="text-right">{x.latency_h === null ? "—" : `${fmt(x.latency_h, 1)} h`}</td>
                <td className={`text-right ${x.alarm_before_fault ? "text-alarm" : ""}`}>{x.alarm_before_fault ? "yes" : "none"}</td>
              </tr>))}</tbody>
          </table>
          <p className="text-sm text-dim mt-2">{res.note}</p>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- 9. GPRS payload
export function Gprs({ s }) {
  const g = s.gprs;
  return (
    <Card title="Alarm message over GPRS" help={HELP.gprs} right={<span className="text-sm text-warn">Format assumed</span>}>
      <div className="text-[15px] bg-ink border border-edge rounded-lg p-3 break-all">{g.message}</div>
      <p className="text-sm text-dim mt-2">{g.length} of 160 characters. Fields: {g.fields.join(", ")}.</p>
    </Card>
  );
}

// ---------------------------------------------------------------- 10. digital nameplate
export function NameplateSelect({ kva, setKva, rated }) {
  return (
    <label className="block text-sm">
      <span className="text-dim">Nameplate rating</span>
      <select aria-label="Nameplate rating" className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5" value={kva ?? ""}
        onChange={(e) => setKva(e.target.value ? Number(e.target.value) : null)}>
        <option value="">As recorded ({rated} kVA)</option>
        {[25, 63, 100, 250].map((k) => <option key={k} value={k}>{k} kVA</option>)}
      </select>
    </label>
  );
}

// ---------------------------------------------------------------- notifications
const TIER = { P1: ["Act now", "alarm"], P2: ["Plan this week", "warn"], P3: ["For information", "ok"], DIGEST: ["Daily digest", "info"] };

export function Notifications({ scenario, kva, i }) {
  const [d, setD] = useState(null);
  const [open, setOpen] = useState(null);
  const [demo, setDemo] = useState(null);
  const [armed, setArmed] = useState(null);       // two-step: arm, then confirm
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    api(`${scenario}/notifications?t=${i}${kva ? `&kva=${kva}` : ""}`).then(setD).catch(() => {});
  }, [scenario, kva, i, tick]);
  useEffect(() => { api("demo/status").then(setDemo).catch(() => {}); }, [tick]);
  useEffect(() => { if (!armed) return undefined; const t = setTimeout(() => setArmed(null), 5000); return () => clearTimeout(t); }, [armed]);

  const sendReal = (m) => {
    if (armed !== m.id) { setArmed(m.id); return; }
    setArmed(null); setBusy(m.id); setResult(null);
    fetch(`/api/${scenario}/demo-send/${encodeURIComponent(m.id)}${kva ? `?kva=${kva}` : ""}`, { method: "POST" })
      .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.status); return j; })
      .then((j) => setResult({ ok: true, text: j.sent.map((x) => `${x.to} (${x.as})`).join(", ") }))
      .catch((e) => setResult({ ok: false, text: String(e.message || e) }))
      .finally(() => { setBusy(null); setTick((x) => x + 1); });
  };
  if (!d) return <Card title="Notifications sent">Loading…</Card>;
  const shown = d.entries;
  return (
    <Card title="Notifications sent" help={HELP.notifications} className="max-h-[560px] overflow-auto"
      right={<span className="text-sm text-warn">{d.mode === "replay" ? "Replay: outbox only, no real email or SMS"
        : d.mode === "outbox" ? "Live: outbox only, no real email or SMS" : "Live"}</span>}>
      <p className={`text-sm mb-2 ${demo?.enabled ? "text-fg" : "text-dim"}`}>
        {demo?.enabled
          ? <>Demo sends on, {demo.sends_left} left, to {demo.recipients.join(", ")}. Use "Send for real" on a row; it asks to confirm.</>
          : <>Demo sends off: {demo?.reason || "checking…"}</>}
      </p>
      {result && (
        <p className={`text-sm mb-2 ${result.ok ? "text-ok" : "text-alarm"}`}>
          {result.ok ? `Sent for real to ${result.text}. Check the inbox (and Spam).` : `Not sent: ${result.text}`}
        </p>
      )}
      <p className="text-sm text-dim mb-2">
        {shown.length} shown up to this moment, {d.total_planned} planned for the whole record from {d.episodes} alert episodes.
        Email server {d.smtp_configured ? "configured" : "not configured"}.
      </p>
      {shown.length === 0 && <p className="text-dim">Nothing sent yet.</p>}
      <table className="w-full text-[15px]">
        <tbody>
          {shown.map((m) => {
            const [tl, tone] = TIER[m.tier] || [m.tier, "info"];
            return (
              <tr key={m.id} className="border-t border-edge align-top cursor-pointer" onClick={() => setOpen(open === m.id ? null : m.id)}>
                <td className="py-1.5 pr-3 whitespace-nowrap text-dim">{when(m.time)}</td>
                <td className="pr-3"><span className={`border rounded-md px-1.5 text-sm whitespace-nowrap ${LEVEL[tone]}`}>{tl}</span></td>
                <td className="pr-3">{m.recipient_name}<div className="text-sm text-dim">{m.channel} to {m.delivered_via}</div></td>
                <td>
                  {m.reason}
                  {m.attach_pdf && (m.meta.attachment_status === "ready"
                    ? <a className="underline ml-2" onClick={(e) => e.stopPropagation()}
                        href={`/api/${scenario}/outbox/${m.attachment.split("/").pop()}${kva ? `?kva=${kva}` : ""}`}>PDF</a>
                    : <span className="text-dim ml-2">PDF {m.meta.attachment_status}</span>)}
                  {demo?.enabled && (
                    m.meta.demo_sent
                      ? <span className="text-ok ml-2 text-sm">Sent for real to {m.meta.demo_sent.to}</span>
                      : <button className={`ml-2 twin-btn ${armed === m.id ? "text-alarm border-alarm" : ""}`}
                          disabled={busy !== null} onClick={(e) => { e.stopPropagation(); sendReal(m); }}>
                          {busy === m.id ? "Sending…" : armed === m.id ? "Confirm: send a real email" : "Send for real"}
                        </button>
                  )}
                  {open === m.id && (
                    <pre className="whitespace-pre-wrap text-sm font-sans bg-ink border border-edge rounded-lg p-3 mt-1">
{`Subject: ${m.subject}\n\n${m.body}\n\nSMS: ${m.sms}\n${m.note}`}
                    </pre>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
