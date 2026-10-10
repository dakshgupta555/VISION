// Network twin tab: mirrors the single-transformer Overview. Map in the twin's
// slot (left), "What's happening in the network" and four key numbers on the
// right, the event log below everything.
import { useEffect, useState } from "react";
import { api, Card, fmt, Help, Icon, PRIORITY } from "./ui.jsx";
import { HELP } from "./help.js";
import NetMap, { MapLegend } from "./NetMap.jsx";
import { Chip, COMM_WORD, CommMark, inrIN, KeyNumber, LEVEL_WORD, shownLevel, ShareBar, TONE_BORDER, TONE_TEXT } from "./parts.jsx";

const clock = (w) => {
  if (!w) return "—";
  const d = new Date(w * 1000);
  return `${d.toLocaleTimeString("en-GB", { hour12: false })}.${String(d.getMilliseconds()).padStart(3, "0")}`;
};
const hm = (iso) => (iso ? iso.slice(11, 16) : "—");

// ======================================================================= map card
function Filters({ f, setF, gateways }) {
  const sel = (key, label, opts) => (
    <label className="flex items-center gap-1.5 text-sm text-dim">{label}
      <select aria-label={label} className="border border-edge rounded-lg px-2 py-1 text-fg" value={f[key]}
        onChange={(e) => setF({ ...f, [key]: e.target.value })}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {sel("gateway", "Gateway", [["all", "All"], ...gateways.filter((g) => g.existing).map((g) => [g.id, g.id])])}
      {sel("health", "Health", [["all", "All"], ["problem", "Problems"], ["ok", "Normal"], ["unknown", "Unknown"], ["unmonitored", "Not monitored"]])}
      {sel("coverage", "Coverage", [["all", "All"], ["inside", "Inside coverage"], ["outside", "Outside coverage"], ["candidates", "Show candidate sites"]])}
      {sel("priority", "Priority", [["all", "All"], ["high", "High risk"], ["medium", "Medium and up"]])}
    </div>
  );
}

function GatewayDrawer({ gid, onClose, onAsset, version }) {
  const [g, setG] = useState(null);
  useEffect(() => { api(`net/gateway/${gid}`).then(setG).catch(() => setG(null)); }, [gid, version]);
  if (!g) return null;
  const st = g.status;
  return (
    <div className="absolute top-16 right-4 bottom-4 w-[22rem] card overflow-y-auto z-20 p-5 text-[15px]">
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="text-base font-semibold">{g.id}</div>
          <div className="text-sm text-dim">{g.name}</div>
        </div>
        <button className="twin-btn ml-auto" onClick={onClose}>Close</button>
      </div>
      <div className="mt-3 space-y-1.5">
        <div className="flex justify-between"><span className="text-dim">Status</span>
          <b className={st === "online" ? "" : "text-dim"}>{st}{g.down_by_demo ? " (switched off in demo)" : ""}</b></div>
        {g.existing && <div className="flex justify-between"><span className="text-dim">Capacity used</span>
          <b>{g.used} of {g.capacity} nodes</b></div>}
        {g.existing && <ShareBar value={g.used} max={g.capacity} />}
        {g.existing && <div className="flex justify-between"><span className="text-dim">Last batch, waiting in nodes</span>
          <b>{g.last_batch} forwarded, {g.waiting} waiting</b></div>}
        <div className="flex justify-between"><span className="text-dim">Cost (illustrative)</span>
          <b>{inrIN(g.cost_inr)} + {inrIN(g.annual_inr)}/yr</b></div>
        <p className="text-sm text-dim pt-1">Coverage: {g.coverage_assumption}. {g.in_range.length} transformers inside it.</p>
        <p className="text-sm text-dim">{HELP.gateway[0]}</p>
      </div>
      {g.existing && (
        <div className="mt-4">
          <div className="text-sm text-dim mb-1">Connected devices</div>
          <ul className="divide-y divide-edge">
            {g.devices.map((d) => {
              const lv = shownLevel(d);
              return (
                <li key={d.id}>
                  <button className="w-full flex items-center gap-2 py-1.5 text-left hover:bg-ink rounded" onClick={() => onAsset(d.id)}>
                    <span className="w-3 h-3 rounded-full shrink-0" style={{ background: `var(--color-${lv === "unknown" ? "dim" : lv})` }} />
                    <b>{d.id}</b>
                    <span className="text-sm text-dim">{d.health.verified ? LEVEL_WORD[lv] : "last known, unverified"}</span>
                    <span className="ml-auto flex items-center gap-1 text-sm"><CommMark status={d.comms.status} />{COMM_WORD[d.comms.status]}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export function MapCard({ fleet, ov, onAsset, title = "District map", right }) {
  const [f, setF] = useState({ gateway: "all", health: "all", coverage: "all", priority: "all" });
  const [gw, setGw] = useState(null);
  return (
    <Card pad="p-0" className="relative overflow-hidden h-full flex flex-col">
      <div className="px-4 pt-4 pb-3 border-b border-edge">
        <div className="flex items-center gap-3 mb-2">
          <span className="text-base font-semibold">{title}</span>
          <span className="ml-auto flex items-center gap-2">{right}<Help text={HELP.map} title="the map" /></span>
        </div>
        <Filters f={f} setF={setF} gateways={fleet.gateways} />
        <div className="mt-2"><MapLegend /></div>
      </div>
      <div className="relative flex-1 min-h-0">
        <NetMap fleet={fleet} devices={ov.devices} gatewaysLive={ov.gateways} risks={ov.risks} filters={f}
          onAsset={onAsset} onGateway={setGw} selected={gw} fill />
        {gw && <GatewayDrawer gid={gw} onClose={() => setGw(null)} onAsset={onAsset} version={ov.clock} />}
      </div>
    </Card>
  );
}

// ======================================================================= what's happening
export function NetWhats({ w }) {
  const tone = w.level === "info" ? "info" : w.level;
  return (
    <Card title="What's happening in the network" help={HELP.whats} className={`border-l-4 ${TONE_BORDER[tone]}`}>
      <div className="flex gap-3">
        <span className={`mt-1 ${TONE_TEXT[tone]}`}><Icon name={tone === "info" ? "info" : tone} className="w-7 h-7" /></span>
        <div className="min-w-0">
          <p className={`text-xl font-semibold leading-snug ${tone === "ok" ? "text-ok" : ""}`}>{w.headline}</p>
          <p className="text-[15px] text-dim mt-1">{w.meaning}</p>
        </div>
      </div>
      {w.actions.length > 0 && (
        <div className="mt-4">
          <div className="text-sm text-dim mb-1.5">What to do</div>
          <ul className="space-y-2">
            {w.actions.map((r, k) => {
              const p = PRIORITY[r.priority] || PRIORITY[3];
              return (
                <li key={k} className="flex gap-3 items-start">
                  <span className={`shrink-0 text-sm font-semibold px-2 py-0.5 rounded-md border ${TONE_BORDER[p.tone]} ${TONE_TEXT[p.tone]}`}>{p.label}</span>
                  <span className="text-[15px]">{r.text}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {w.evidence && <p className="mt-3 text-sm text-dim"><span className="font-semibold text-fg">Evidence: </span>{w.evidence}</p>}
    </Card>
  );
}

// ======================================================================= key numbers
function NetNumber({ label, value, of, unit, tone, word, help, bar }) {
  return (
    <Card pad="p-5" className={tone !== "ok" && tone !== "info" ? `border-2 ${TONE_BORDER[tone]}` : ""}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-[15px] text-dim">{label}</span>
        <Help text={help} title={label} />
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-[34px] leading-none font-semibold tracking-tight">{fmt(value, 0)}</span>
        <span className="text-base text-dim">{of !== undefined ? `of ${fmt(of, 0)}` : ""}{unit ? ` ${unit}` : ""}</span>
      </div>
      <div className={`mt-1.5 text-sm font-semibold flex items-center gap-1.5 ${TONE_TEXT[tone]}`}>
        <Icon name={tone === "info" ? "info" : tone} className="w-4 h-4" />{word}
      </div>
      {bar}
    </Card>
  );
}

export function NetNumbers({ k }) {
  const dark = k.stale + k.offline;
  const gwTone = k.gateway_pct >= 100 ? "alarm" : k.gateway_pct >= 85 ? "warn" : "ok";
  return (
    <div className="grid grid-cols-2 gap-4">
      <NetNumber label="Assets monitored" value={k.monitored} of={k.assets} tone="info" help={HELP.monitored}
        word={`${k.assets - k.monitored} not monitored`} bar={<ShareBar value={k.monitored} max={k.assets} />} />
      <NetNumber label="Active alarms" value={k.alarms} tone={k.alarms ? "alarm" : "ok"} help={HELP.alarms}
        word={k.alarms ? "Asset problem" : k.sensor_faults ? `${k.sensor_faults} instrument fault` : "None"}
        bar={<ShareBar value={k.alarms} max={k.monitored} tone={k.alarms ? "alarm" : "ok"} />} />
      <NetNumber label="Stale or offline nodes" value={dark} tone={dark ? "warn" : "ok"} help={HELP.stale}
        word={dark ? `${k.stale} stale, ${k.offline} offline` : "All reporting"}
        bar={<ShareBar value={dark} max={k.monitored} tone={dark ? "warn" : "ok"} />} />
      <NetNumber label="Gateway capacity used" value={k.gateway_pct} unit="%" tone={gwTone} help={HELP.capacity}
        word={`${k.gateway_used} of ${k.gateway_capacity} node slots`}
        bar={<ShareBar value={k.gateway_used} max={k.gateway_capacity} tone={gwTone} warnAt={Math.round(0.85 * k.gateway_capacity)} />} />
    </div>
  );
}

// ======================================================================= visit first
export function VisitFirst({ rows, onAsset }) {
  const tone = { high: "alarm", medium: "warn", low: "ok" };
  return (
    <Card title="Which transformer to visit first" help={HELP.visit}>
      <ol className="space-y-1">
        {rows.map((r, i) => (
          <li key={r.id}>
            <button className="w-full flex items-start gap-3 py-1.5 px-1 rounded-lg text-left hover:bg-ink" onClick={() => onAsset(r.id)}>
              <span className="w-5 text-dim text-right shrink-0">{i + 1}</span>
              <b className="shrink-0 w-16">{r.id}</b>
              <span className="text-[15px] min-w-0 flex-1">{r.reason}</span>
              <span className={`shrink-0 text-sm font-semibold ${TONE_TEXT[tone[r.band]]}`} title="Risk score, 0-100">risk {fmt(r.risk, 0)}</span>
            </button>
          </li>
        ))}
      </ol>
    </Card>
  );
}

// ======================================================================= event log
export function EventLog({ events, onAsset }) {
  const [only, setOnly] = useState("all");
  const kinds = { all: () => true, packets: (e) => e.chain, comms: (e) => ["comms", "gateway", "replay"].includes(e.kind),
    demo: (e) => e.kind === "demo" };
  const list = events.filter(kinds[only]).slice(0, 40);
  const tone = (l) => (l === "alarm" ? "alarm" : l === "warn" ? "warn" : l === "sensor" ? "sensor" : l === "ok" ? "ok" : "info");
  return (
    <Card title="Event log" help={HELP.events}
      right={<select aria-label="Show events" className="border border-edge rounded-lg px-2 py-1 text-sm" value={only} onChange={(e) => setOnly(e.target.value)}>
        <option value="all">All events</option><option value="packets">Packet paths</option>
        <option value="comms">Communication</option><option value="demo">Demo actions</option></select>}>
      {!list.length && <p className="text-dim">Nothing yet.</p>}
      <ul className="divide-y divide-edge max-h-[340px] overflow-y-auto">
        {list.map((e) => (
          <li key={e.n} className="py-2 flex flex-wrap gap-x-3 gap-y-1 items-baseline text-[15px]">
            <span className="text-sm text-dim w-[6.5rem] shrink-0">{clock(e.wall)}</span>
            <span className={`flex items-center gap-1.5 font-semibold ${TONE_TEXT[tone(e.level)]}`}>
              <Icon name={tone(e.level) === "info" ? "info" : tone(e.level)} className="w-4 h-4" /></span>
            <span className="min-w-0 flex-1">
              {e.device ? <button className="underline decoration-dotted underline-offset-4" onClick={() => onAsset(e.device)}>{e.text}</button> : e.text}
              {e.sim && <span className="text-dim text-sm"> [{hm(e.sim)}]</span>}
              {e.chain && (
                <span className="block text-sm text-dim mt-0.5">
                  {e.chain.map((h, i) => (
                    <span key={i}>{i > 0 && " → "}<b className="text-fg font-semibold">{h.hop === "node" ? `node ${h.who}` : h.hop === "gateway" ? h.who : h.hop}</b> {clock(h.wall)}</span>
                  ))}
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ======================================================================= page
export default function NetworkTab({ fleet, ov, onAsset, replay }) {
  return (
    <main className="px-6 py-6 space-y-6">
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-stretch">
        <div className="min-h-[460px] flex flex-col"><MapCard fleet={fleet} ov={ov} onAsset={onAsset} /></div>
        <div className="space-y-6">
          <NetWhats w={ov.whats} />
          <NetNumbers k={ov.kpis} />
          {!replay && <VisitFirst rows={ov.visit_first} onAsset={onAsset} />}
        </div>
      </div>
      <EventLog events={ov.events} onAsset={onAsset} />
    </main>
  );
}
