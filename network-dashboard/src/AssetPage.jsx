// One transformer inside the network app, laid out like the single-transformer
// Overview: trend in the twin's slot, "What's happening" and the four key
// numbers on the right, engineering details folded below. The numbers come
// from this unit's own twin (the same engine as :8000), run on its packets.
import { useEffect, useState } from "react";
import { api, Card, duration, fmt, Fold, Help, Icon, PRIORITY, StatusPill, when } from "./ui.jsx";
import { HELP } from "./help.js";
import { HELP as TWIN_HELP } from "./twinhelp.js";
import { capNote, headlineFor, overallStatus, suspectSet } from "./plain.js";
import { Chip, COMM_WORD, CommMark, inrIN, KeyNumber, TONE_BORDER, TONE_TEXT, Trends } from "./parts.jsx";

const SINGLE_TWIN = "http://localhost:5173";

function Whats({ s, unverified, comm }) {
  const st = overallStatus(s, unverified);
  const known = headlineFor(s);
  // not reporting: never let the last known "all normal" read as healthy now
  const h = unverified
    ? { text: `Not reporting since ${when(comm.last_seen)}`, meaning: `Last known, unverified: ${known.text.charAt(0).toLowerCase()}${known.text.slice(1)}.`, alert: null }
    : known;
  const acts = s.recommendations.filter((r) => r.family !== "reference" && !(r.priority === 3 && st.level !== "ok")).slice(0, 2);
  const hs = s.countdown.hotspot;
  const evidence = unverified ? "Diagnostic confidence: none until packets arrive again."
    : h.alert ? `Detected ${when(h.alert.start)}; ${h.evidence ?? h.alert.why}`
      : s.recommendations.find((r) => r.priority === 3)?.because;
  const calm = st.level === "ok";
  return (
    <Card title="What's happening" help={TWIN_HELP.whats} className={`border-l-4 ${TONE_BORDER[st.level]}`}>
      {unverified && (
        <p className="mb-3 px-3 py-2 rounded-lg border border-edge text-dim flex items-center gap-2">
          <Icon name="info" className="w-5 h-5 shrink-0" />Last known, unverified: {comm.reason || "not reporting"}.
          Lost communication is not a transformer fault.
        </p>
      )}
      <div className={`flex gap-3 ${unverified ? "opacity-60" : ""}`}>
        <span className={`mt-1 ${TONE_TEXT[st.level]}`}><Icon name={st.level === "info" ? "info" : st.level} className="w-7 h-7" /></span>
        <div className="min-w-0">
          <p className={`text-xl font-semibold leading-snug ${calm ? "text-ok" : ""}`}>{h.text}</p>
          <p className="text-[15px] text-dim mt-1">{h.meaning}</p>
        </div>
      </div>
      {!calm && !unverified && acts.length > 0 && (
        <div className="mt-4">
          <div className="text-sm text-dim mb-1.5">What to do</div>
          <ul className="space-y-2">
            {acts.map((r, k) => {
              const p = PRIORITY[r.priority] || PRIORITY[3];
              return (
                <li key={k} className="flex gap-3 items-start">
                  <span className={`shrink-0 text-sm font-semibold px-2 py-0.5 rounded-md border ${TONE_BORDER[p.tone]} ${TONE_TEXT[p.tone]}`}>{p.label}</span>
                  <span className="text-[15px]">{r.text}{r.detail && <span className="block text-dim">{r.detail}</span>}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {!unverified && (hs.already_above || hs.minutes !== null) && (
        <p className="mt-3 text-[15px]">
          <b className={hs.already_above ? "text-alarm" : "text-warn"}>
            {hs.already_above ? `Hot-spot is above ${hs.limit_c} °C now` : `Hot-spot reaches ${hs.limit_c} °C in ${duration(hs.minutes)}`}
          </b>{hs.shed_pct > 0 ? `; shedding ${fmt(hs.shed_pct, 0)} % of the load keeps it under.` : "."}
        </p>
      )}
      {evidence && <p className="mt-3 text-sm text-dim"><span className="font-semibold text-fg">How sure: </span>{evidence}</p>}
    </Card>
  );
}

function KeyNumbers({ s, unverified }) {
  const L = s.display_limits, r = s.readings, sus = suspectSet(s);
  const oilSus = sus.has("oil_temp") || s.status.thermal_status === "sensor_temp_offset";
  const curSus = ["IR", "IY", "IB"].some((c) => sus.has(c));
  return (
    <div className="grid grid-cols-2 gap-4">
      <KeyNumber label="Hot-spot temperature" tech="Winding hot-spot, IEEE C57.91" value={r.hotspot_c} unit="°C" lim={L.hotspot_c}
        help={TWIN_HELP.hotspot(L.hotspot_c)} suspect={oilSus} caption={`limit ${L.hotspot_c.limit} °C`} unverified={unverified} />
      <KeyNumber label="Load" tech="Load ratio K, RMS of three phases" value={r.load_pct} unit="% of rating" lim={L.load_pct}
        help={TWIN_HELP.load(L.load_pct, s.nameplate.rated_current_a)} suspect={curSus} unverified={unverified} />
      <KeyNumber label="Top-oil temperature" tech="Top oil, measured" value={r.oil_c} unit="°C" lim={L.top_oil_c}
        help={TWIN_HELP.topoil(L.top_oil_c)} suspect={oilSus} unverified={unverified} />
      <KeyNumber label="Health index" tech="Weighted health index, 0-100" value={s.health.index} unit="of 100" lim={L.health} inverse
        help={TWIN_HELP.health(s.health.components, L.health)} unverified={unverified} />
    </div>
  );
}

// Health, confidence and communication: three fields, never merged.
function Fields({ d }) {
  const h = d.health, c = d.comms, conf = d.confidence;
  const ht = !h.verified ? "info" : h.level === "ok" ? "ok" : h.level;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Chip tone={ht} title="Health: this unit's twin">
        Health: {h.index === null ? "no data" : `${fmt(h.index, 0)} of 100`}{h.verified ? "" : ", last known, unverified"}
      </Chip>
      <Chip tone={conf.level === "high" ? "ok" : conf.level === "reduced" ? "warn" : "info"} title={conf.reason}>
        Confidence: {conf.level}
      </Chip>
      <Chip tone={c.status === "online" ? "ok" : c.status === "stale" ? "warn" : "info"} title={c.reason || "packets arriving on time"}>
        <CommMark status={c.status} />Communication: {COMM_WORD[c.status]}
      </Chip>
      <Help text={HELP.fields} title="these three fields" />
    </div>
  );
}

function Comms({ d }) {
  const c = d.comms;
  const lp = c.last_packet;
  const clock = (w) => (w ? new Date(w * 1000).toLocaleTimeString("en-GB", { hour12: false }) : "—");
  return (
    <Card title="Communication and confidence" help={HELP.confidence}>
      <div className="grid sm:grid-cols-2 gap-x-8 gap-y-1.5 text-[15px]">
        <div className="flex justify-between"><span className="text-dim">Status</span><b>{COMM_WORD[c.status]}{c.missed ? `, ${c.missed} intervals since last packet` : ""}</b></div>
        <div className="flex justify-between"><span className="text-dim">Gateway</span><b>{d.gateway}{c.last_gateway && c.last_gateway !== d.gateway ? ` (last heard by ${c.last_gateway})` : ""}</b></div>
        <div className="flex justify-between"><span className="text-dim">Last sample</span><b>{when(c.last_seen)}</b></div>
        <div className="flex justify-between"><span className="text-dim">Packets received / lost</span><b>{fmt(c.received, 0)} / {fmt(c.lost, 0)}</b></div>
        <div className="flex justify-between"><span className="text-dim">Arrived late from buffer</span><b>{fmt(c.late, 0)}</b></div>
        <div className="flex justify-between"><span className="text-dim">Duplicates / rejected</span><b>{fmt(c.duplicates, 0)} / {fmt(c.rejected, 0)}</b></div>
        <div className="sm:col-span-2 flex justify-between"><span className="text-dim">Diagnostic confidence</span><b className="text-right">{d.confidence.level}: {d.confidence.reason}</b></div>
        {lp && <div className="sm:col-span-2 text-sm text-dim pt-1">Newest packet #{lp.seq} ({lp.priority}):{" "}
          {lp.chain.map((h, i) => <span key={i}>{i > 0 && " → "}<b className="text-fg">{h.hop}</b> {clock(h.wall)}</span>)}</div>}
      </div>
    </Card>
  );
}

function RiskBreakdown({ r }) {
  return (
    <div>
      <h3 className="font-semibold mb-1">Risk score {fmt(r.risk, 0)} ({r.band})</h3>
      <p className="text-sm text-dim mb-2">100 × likelihood {fmt(r.likelihood, 2)} ({r.likelihood_rule}) × (0.5 + 0.5 × consequence {fmt(r.consequence.value, 2)})</p>
      <table className="w-full text-[15px]">
        <thead><tr className="text-dim text-sm text-left"><th className="font-normal">Term</th><th className="font-normal">Input</th>
          <th className="font-normal text-right">Score</th><th className="font-normal text-right">Weight</th></tr></thead>
        <tbody>
          {r.likelihood_terms.map((t) => (
            <tr key={t.name} className="border-t border-edge"><td className="py-1">{t.name}</td><td>{String(t.input ?? "—")}</td>
              <td className="text-right">{fmt(t.score, 2)}</td><td className="text-right">{t.weight !== undefined ? fmt(t.weight, 2) : "max"}</td></tr>
          ))}
          {r.consequence.terms.map((t) => (
            <tr key={t.name} className="border-t border-edge text-dim"><td className="py-1">consequence: {t.name}</td><td>{String(t.input)}</td>
              <td className="text-right">{fmt(t.score, 2)}</td><td className="text-right">{fmt(t.weight, 2)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Unmonitored({ a }) {
  const m = a.metadata;
  return (
    <main className="px-6 py-6 space-y-6">
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Not monitored" help={HELP.risk}>
          <p className="text-[15px] text-dim">No node is installed, so there is no live data and no health index for this transformer. Its
            risk score below comes from metadata only. It is shown hollow grey on the map: unknown, not healthy.</p>
          <div className="grid grid-cols-2 gap-x-8 gap-y-1.5 mt-4 text-[15px]">
            {[["Rating", `${fmt(a.rating_kva, 0)} kVA`], ["Manufacturer (synthetic)", a.manufacturer], ["Age", `${m.age_years} years`],
              ["Estimated peak load", `${m.peak_load_ratio} of rating`], ["Customers served", m.customers], ["Criticality (1-3)", `${m.criticality}, ${m.site}`],
              ["Failures, last 5 years", m.past_failures_5y], ["Gateways in range", a.in_range.join(", ") || "none"]].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3 border-t border-edge py-1"><span className="text-dim">{k}</span><b className="text-right">{v}</b></div>
            ))}
          </div>
        </Card>
        <Card title="Risk score"><RiskBreakdown r={a.risk} /></Card>
      </div>
    </main>
  );
}

export default function AssetPage({ id, version, onBack }) {
  const [a, setA] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    api(`net/asset/${id}`).then((x) => { if (live) { setA(x); setErr(null); } }).catch((e) => live && setErr(String(e)));
    return () => { live = false; };
  }, [id, version]);
  const head = (
    <div className="px-6 pt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
      <button className="twin-btn" onClick={onBack}>← Network</button>
      <div className="min-w-0">
        <div className="text-lg font-semibold leading-tight">Transformer {id}</div>
        {a && <div className="text-sm text-dim">{fmt(a.rating_kva, 0)} kVA, {a.manufacturer} (synthetic), {a.monitored ? `node on ${a.gateway}` : "not monitored"}</div>}
      </div>
      {a?.monitored && a.summary && <Fields d={a.summary} />}
      {id === "DT-001" && (
        <a className="twin-btn ml-auto" href={SINGLE_TWIN} target="_blank" rel="noreferrer">Open full 3D twin ↗</a>
      )}
    </div>
  );
  if (err && !a) return <>{head}<p className="p-6 text-alarm">{err}</p></>;
  if (!a) return <>{head}<p className="p-6 text-dim">Loading {id}…</p></>;
  if (!a.monitored) return <>{head}<Unmonitored a={a} /></>;
  const s = a.state, d = a.summary;
  if (!s) return <>{head}<p className="p-6 text-dim">No analysis yet for {id}: {d.comms.reason || "waiting for packets"}.</p></>;
  const unverified = !d.health.verified;
  const st = overallStatus(s, unverified);
  return (
    <>
      {head}
      <main className="px-6 py-6 space-y-6">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-stretch">
          <div className={unverified ? "grayscale opacity-60" : ""}>
            <Trends series={a.series} limits={s.display_limits} height={360} />
            <div className="mt-3 flex items-center gap-3 text-sm text-dim">
              <StatusPill level={unverified ? "info" : st.level} text={unverified ? "Last known, unverified" : st.text} />
              <span>Twin state as of {when(s.timestamp)}, from {fmt(a.summary.analysis.samples, 0)} samples of this unit only.</span>
            </div>
          </div>
          <div className="space-y-6">
            <Whats s={s} unverified={unverified} comm={d.comms} />
            <div className={unverified ? "grayscale" : ""}><KeyNumbers s={s} unverified={unverified} /></div>
          </div>
        </div>
        <Comms d={d} />
        <Fold title="Engineering details" help={HELP.asset} summary="Health components, risk breakdown">
          <div className="grid gap-x-10 gap-y-6 lg:grid-cols-2">
            <div>
              <h3 className="font-semibold mb-1">Health index, by component</h3>
              <table className="w-full text-[15px]"><tbody>
                {s.health.components.map((c) => (
                  <tr key={c.key} className="border-t border-edge" title={c.scale}>
                    <td className="py-1.5 text-dim">{c.label}</td><td className="text-right">{fmt(c.value, 3)} {c.unit}</td>
                    <td className="text-right font-semibold">{fmt(c.score, 0)}</td><td className="text-right text-dim">× {fmt(c.weight, 2)}</td>
                  </tr>
                ))}
              </tbody></table>
              {capNote(s.health) && <p className="text-sm mt-2">{capNote(s.health)}</p>}
              <p className="text-sm text-dim mt-2">Life used, last 24 h: {fmt(s.life.last_24h.aged_h, 1)} h, worth {inrIN(s.life.last_24h.inr)}.</p>
            </div>
            <RiskBreakdown r={a.risk} />
          </div>
        </Fold>
      </main>
    </>
  );
}
