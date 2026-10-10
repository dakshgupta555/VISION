// Map view: the network on a real lat/lon map (react-leaflet + OpenStreetMap),
// with the gateway placement optimiser. If the tiles cannot load it falls back
// to a plain light background with a lat/lon grid and a scale bar; every
// marker, circle, line and panel keeps working. Positions, coverage and costs
// are synthetic and labelled so on screen.
import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, ScaleControl, TileLayer, Tooltip, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api, Card, fmt, Help, Icon, PRIORITY, when } from "./ui.jsx";
import { Chip, COMM_WORD, CommMark, inrIN, LEVEL_WORD, post, shownLevel, TONE_TEXT } from "./parts.jsx";

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const SINGLE_TWIN = "http://localhost:5173";
const LAYERS = [["transformers", "Transformers"], ["existing", "Existing gateways"], ["candidates", "Candidate sites"],
  ["coverage", "Coverage circles"], ["links", "Association lines"], ["uncovered", "Uncovered transformers"],
  ["overlap", "Overlapping candidate zones"]];
const BAND_TONE = { high: "alarm", medium: "warn", low: "ok" };

// SVG attributes cannot read CSS variables: resolve the theme's colours once per render
function useColors(theme) {
  return useMemo(() => {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, d) => cs.getPropertyValue(n).trim() || d;
    return { ok: v("--color-ok", "#1a7f37"), warn: v("--color-warn", "#9a6700"), alarm: v("--color-alarm", "#cf222e"),
      sensor: v("--color-sensor", "#8250df"), unknown: "#a3aaa6", fg: v("--color-fg", "#1f2328"), dim: v("--color-dim", "#59636e"),
      edge: v("--color-edge", "#e1e4e8"), panel: v("--color-panel", "#ffffff"), unmon: "#8c959f" };
  }, [theme]);
}

function useDebounced(v, ms) {
  const [d, setD] = useState(v);
  useEffect(() => { const id = setTimeout(() => setD(v), ms); return () => clearTimeout(id); }, [v, ms]);
  return d;
}

// ---------------------------------------------------------------- offline grid
function Graticule({ bounds, step = 0.02, color }) {
  const [[s, w], [n, e]] = bounds;
  const lat0 = Math.floor(s / step) * step, lon0 = Math.floor(w / step) * step;
  const lats = [], lons = [];
  for (let la = lat0; la <= n + step; la += step) lats.push(+la.toFixed(4));
  for (let lo = lon0; lo <= e + step; lo += step) lons.push(+lo.toFixed(4));
  const label = (t) => L.divIcon({ className: "grid-label", html: t, iconSize: [64, 16], iconAnchor: [-4, 18] });
  return (
    <>
      {lats.map((la) => <Polyline key={`la${la}`} positions={[[la, w - 0.1], [la, e + 0.1]]} pathOptions={{ color, weight: 1, opacity: 0.6 }} interactive={false} />)}
      {lons.map((lo) => <Polyline key={`lo${lo}`} positions={[[s - 0.1, lo], [n + 0.1, lo]]} pathOptions={{ color, weight: 1, opacity: 0.6 }} interactive={false} />)}
      {lats.map((la) => <Marker key={`tla${la}`} position={[la, w]} icon={label(`${la.toFixed(2)}° N`)} interactive={false} />)}
      {lons.map((lo) => <Marker key={`tlo${lo}`} position={[s, lo]} icon={label(`${lo.toFixed(2)}° E`)} interactive={false} />)}
    </>
  );
}

// The basemap with a watch on its own tile events (Leaflet fires them on the
// layer, not the map): no tile loaded and 3 errors, or nothing at all in 6 s,
// means the basemap is down and the offline grid takes over.
function Basemap({ onFail }) {
  const n = useRef({ loads: 0, errors: 0 });
  useEffect(() => {
    const timer = setTimeout(() => { if (!n.current.loads) onFail("no tile loaded within 6 s"); }, 6000);
    return () => clearTimeout(timer);
  }, [onFail]);
  return (
    <TileLayer url={TILE_URL} attribution={OSM_ATTR} maxZoom={19}
      eventHandlers={{
        tileload: () => { n.current.loads += 1; },
        tileerror: () => { n.current.errors += 1; if (!n.current.loads && n.current.errors >= 3) onFail(`${n.current.errors} tile requests failed`); },
      }} />
  );
}

function FitOnce({ bounds }) {
  const map = useMap();
  const done = useRef(false);
  useEffect(() => { if (!done.current) { map.fitBounds(bounds, { padding: [24, 24] }); done.current = true; } }, [map, bounds]);
  return null;
}

// ---------------------------------------------------------------- icons
function gwIcon(kind, label, c, down) {
  const style = kind === "existing"
    ? `background:${down ? c.dim : c.fg};color:${c.panel};border:2px solid ${c.fg}`
    : kind === "planned"
      ? `background:${c.panel};color:${c.fg};border:2px dashed ${c.fg}`
      : `background:${c.panel};color:${c.unmon};border:2px dotted ${c.unmon}`;
  return L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
    html: `<div class="gw-icon" style="width:26px;height:26px;${style}" title="${label}">▲</div>` });
}

// ---------------------------------------------------------------- side panels
function Row({ k, children }) {
  return <div className="flex justify-between gap-3 py-1 border-t border-edge text-[15px]"><span className="text-dim">{k}</span><span className="text-right">{children}</span></div>;
}

function TransformerPanel({ id, geo, plan, dev, risk, onClose, onOpenAsset }) {
  const a = geo.fleet.assets.find((x) => x.id === id);
  const pairs = plan?.pairs?.[id] || [];
  const opt = plan?.optimised;
  const planned = opt?.assign?.[id];
  const lbOn = plan?.link_budget;
  const h = dev?.health, c = dev?.comms;
  const lv = dev ? shownLevel(dev) : null;
  const assigned = a.monitored ? (opt?.assign?.[id] || a.gateway) : planned;
  const asg = pairs.find((p) => p.gateway === (a.monitored ? a.gateway : planned));
  const link = dev?.link_simulated;
  return (
    <Card title={`Transformer ${id}`} right={<button className="twin-btn" onClick={onClose}>Close</button>}>
      <Row k="Position (synthetic)">{a.lat.toFixed(5)}° N, {a.lon.toFixed(5)}° E</Row>
      <Row k="Rating, manufacturer">{fmt(a.rating_kva, 0)} kVA, {a.manufacturer}</Row>
      {a.monitored ? (
        <>
          <Row k="Health">{h?.index === null || h?.index === undefined ? "no data" :
            <b className={h.verified ? TONE_TEXT[lv] : "text-dim"}>{h.verified ? `${LEVEL_WORD[lv]}, ${fmt(h.index, 0)} of 100` : `last known ${fmt(h.index, 0)}, unverified`}</b>}</Row>
          {dev?.twin && <Row k="Latest readings">hot-spot {fmt(dev.twin.hotspot_c, 0)} °C, load {fmt(dev.twin.load_pct, 0)} %, oil {fmt(dev.twin.oil_c, 0)} °C</Row>}
          <Row k="Last seen">{when(c?.last_seen)}</Row>
          {(dev?.twin?.alerts || []).length > 0 && <Row k="Active alerts"><span className="text-alarm">{dev.twin.alerts.map((x) => x.what).join("; ")}</span></Row>}
        </>
      ) : <Row k="Monitoring">not monitored: no live data{risk ? `, risk ${fmt(risk.risk, 0)} (${risk.band})` : ""}</Row>}
      <Row k={a.monitored ? "Assigned gateway" : "Planned gateway"}>{assigned ? `${assigned}${a.monitored && opt?.reassigned?.includes(id) ? ` (plan moves it from ${a.gateway})` : ""}` : "none in this plan"}</Row>

      <div className="mt-4 text-sm font-semibold">Four different things</div>
      <div className="mt-1 space-y-1.5 text-[15px]">
        <div className="flex gap-2"><span className="shrink-0 w-[8.5rem] text-sm text-dim">Geographic proximity</span>
          <span>{asg ? `${fmt(asg.distance_m / 1000, 2)} km to ${asg.gateway}, ${asg.within_assumed_radius ? "within" : "outside"} its assumed ${fmt(asg.assumed_radius_m / 1000, 1)} km radius` : "no gateway assigned"}</span></div>
        <div className="flex gap-2"><span className="shrink-0 w-[8.5rem] text-sm text-dim">Estimated radio feasibility</span>
          <span>{!lbOn ? "link budget off (proximity only)" : asg ? `margin ${asg.est_link_margin_db >= 0 ? "+" : ""}${fmt(asg.est_link_margin_db, 1)} dB, estimate` : "—"}</span></div>
        <div className="flex gap-2"><span className="shrink-0 w-[8.5rem] text-sm text-dim">Measured link (simulated)</span>
          <span>{link ? `RSSI ${fmt(link.rssi_mean_dbm, 0)} dBm, SNR ${fmt(link.snr_mean_db, 1)} dB, mean of ${link.packets} simulated packets` : a.monitored ? "no simulated packets yet" : "no node, nothing measured"}</span></div>
        <div className="flex gap-2"><span className="shrink-0 w-[8.5rem] text-sm text-dim">Actual status</span>
          <span className="flex items-center gap-1.5">{a.monitored ? <><CommMark status={c?.status || "no data"} /><b>{COMM_WORD[c?.status || "no data"]}</b>{c?.reason ? <span className="text-dim">, {c.reason}</span> : null}</> : "not connected (no node)"}</span></div>
      </div>

      <div className="mt-4 text-sm font-semibold">Distance to every gateway, nearest first</div>
      <p className="text-sm text-dim">The nearest is not assumed feasible: radius{lbOn ? ", estimated margin" : ""} and capacity are checked.</p>
      <table className="w-full text-[15px] mt-1">
        <tbody>
          {pairs.map((p) => (
            <tr key={p.gateway} className="border-t border-edge align-top">
              <td className="py-1 font-semibold">{p.gateway}</td>
              <td className="text-right whitespace-nowrap">{fmt(p.distance_m / 1000, 2)} km</td>
              <td className={`pl-2 font-semibold ${p.feasible ? "text-ok" : "text-dim"}`}>{p.feasible ? "yes" : "no"}</td>
              <td className="pl-2 text-sm text-dim">{p.why}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-4 flex gap-2">
        <button className="twin-btn" onClick={() => onOpenAsset(id)}>Open asset page</button>
        {id === "DT-001" && <a className="twin-btn" href={SINGLE_TWIN} target="_blank" rel="noreferrer">Open full 3D twin ↗</a>}
      </div>
    </Card>
  );
}

function GatewayPanel({ id, geo, plan, live, ov, setOv, onClose }) {
  const g = geo.fleet.gateways.find((x) => x.id === id);
  const opt = plan?.optimised;
  const load = (opt?.gateway_load || []).find((x) => x.id === id);
  const nodes = Object.entries(opt?.assign || {}).filter(([, gw]) => gw === id).map(([a]) => a).sort();
  const radius = ov[id]?.radius_m ?? g.assumed_radius_m;
  const cost = ov[id]?.cost_inr ?? g.install_cost_inr;
  const st = live?.status;
  const set = (k, v) => setOv({ ...ov, [id]: { ...(ov[id] || {}), [k]: v } });
  const built = g.existing || opt?.gateways_opened?.includes(id);
  return (
    <Card title={`Gateway ${id}`} right={<button className="twin-btn" onClick={onClose}>Close</button>}>
      <p className="text-sm text-dim -mt-2 mb-2">{g.name}</p>
      <Row k="Position (synthetic)">{g.lat.toFixed(5)}° N, {g.lon.toFixed(5)}° E</Row>
      <Row k="Status">{g.existing ? `existing, ${st || "no data"}` : built ? "candidate, BUILT in this plan" : "candidate site, not built"}</Row>
      <Row k="Nodes in this plan">{nodes.length} {nodes.length ? <span className="text-dim text-sm">({nodes.join(", ")})</span> : null}</Row>
      <Row k="Capacity used / max">{load ? `${load.used} / ${load.capacity}` : `0 / ${g.max_nodes}`}</Row>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="block text-sm"><span className="text-dim">Assumed radius (km)</span>
          <input type="number" aria-label="Assumed radius km" min={0.1} max={20} step={0.1} className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5"
            value={+(radius / 1000).toFixed(2)} onChange={(e) => e.target.value !== "" && set("radius_m", Math.round(Number(e.target.value) * 1000))} /></label>
        <label className="block text-sm"><span className="text-dim">Install cost (₹){g.existing ? ", sunk" : ""}</span>
          <input type="number" aria-label="Install cost" min={0} step={5000} disabled={g.existing} className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5 disabled:text-dim"
            value={cost} onChange={(e) => e.target.value !== "" && set("cost_inr", Number(e.target.value))} /></label>
      </div>
      <p className="text-sm text-dim mt-2">Editing re-solves the plan and redraws coverage. ESTIMATED PROXIMITY COVERAGE — not verified LoRa coverage. Illustrative costs.</p>
      {ov[id] && <button className="twin-btn mt-2" onClick={() => { const n = { ...ov }; delete n[id]; setOv(n); }}>Reset {id} to the fleet file</button>}
    </Card>
  );
}

function PlanPanel({ plan, budget, setBudget, lbOn, setLbOn, solving, onSelect }) {
  if (!plan) return <Card title="Gateway placement">Solving…</Card>;
  const { optimised: o, greedy: g, current: c } = plan;
  const rows = [
    ["Capital cost", (p) => `₹${fmt(p.capital_inr / 1e5, 2)} L`], ["Nodes (new)", (p) => `${p.n_instrumented} (+${p.nodes.length})`],
    ["Gateways (new)", (p) => `${p.gateways_built.length} (+${p.gateways_opened.length})`],
    ["Transformers instrumented", (p) => `${fmt(p.pct_instrumented, 0)} %`],
    ["Within estimated coverage", (p) => `${fmt(p.pct_within_coverage, 0)} %`],
    ["High-risk instrumented", (p) => `${p.high_covered} of ${p.high_total}`],
  ];
  return (
    <Card title="Gateway placement" help={["A capacitated facility-location MILP (scipy, HiGHS): which candidate gateways to build, which transformers get nodes and which gateway each reports through, maximising risk covered within the budget and every gateway's capacity.",
      "A pair is feasible only if within the gateway's assumed radius (and, with the link budget on, the estimated margin meets the requirement). Illustrative costs."]}>
      <div className="flex items-baseline gap-2">
        <span className="text-[28px] leading-none font-semibold">{inrIN(budget)}</span>
        <span className="text-sm text-dim">budget{solving ? ", solving…" : plan.solve_ms ? `, solved in ${fmt(plan.solve_ms, 0)} ms` : ""}</span>
      </div>
      <input type="range" aria-label="Map budget" min={0} max={1200000} step={10000} value={budget} className="w-full mt-2" onChange={(e) => setBudget(Number(e.target.value))} />
      <label className="mt-2 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={lbOn} onChange={(e) => setLbOn(e.target.checked)} />
        Use link-budget estimate (log-distance model, rough; off by default)
      </label>
      <table className="w-full text-[15px] mt-3">
        <thead><tr className="text-sm text-dim text-left"><th className="font-normal" /><th className="font-normal text-right">Current</th>
          <th className="font-normal text-right">Greedy</th><th className="font-normal text-right">Optimised</th></tr></thead>
        <tbody>{rows.map(([k, f]) => (
          <tr key={k} className="border-t border-edge"><td className="py-1 text-dim">{k}</td><td className="text-right pl-3 whitespace-nowrap">{f(c)}</td>
            <td className="text-right pl-3 whitespace-nowrap">{f(g)}</td><td className="text-right pl-3 whitespace-nowrap font-semibold">{f(o)}</td></tr>))}</tbody>
      </table>
      <p className="text-sm text-dim mt-1">Illustrative costs (L = lakh, ₹1,00,000). Current = existing gateways and installed nodes, nothing added.
        {o.gateways_opened.length ? ` Optimised builds ${o.gateways_opened.join(", ")}.` : ""}</p>
      {o.message && <p className="mt-2 text-[15px]">{o.message}</p>}
      {o.reasons?.length > 0 && (
        <div className="mt-3">
          <div className="text-sm text-dim mb-1">Recommendations</div>
          <ul className="space-y-1 text-[15px] max-h-[220px] overflow-y-auto">
            {o.reasons.map((r) => (
              <li key={r.kind + r.id}><button className="text-left hover:underline" onClick={() => onSelect(r.kind === "gateway" ? { type: "gateway", id: r.id } : { type: "asset", id: r.id })}>
                <b className="text-fg">{r.kind === "gateway" ? "▲ " : ""}</b>{r.text}</button></li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-3">
        <div className="text-sm text-dim mb-1">Capacity used per gateway</div>
        <div className="flex flex-wrap gap-2">{o.gateway_load?.map((x) => (
          <span key={x.id} className={`text-sm px-2 py-0.5 rounded-md border ${x.used >= x.capacity ? "border-warn text-warn" : "border-edge"}`}>
            {x.id}{x.planned ? " (planned)" : ""} {x.used}/{x.capacity}</span>))}</div>
      </div>
      {o.uncovered_high?.length > 0 && (
        <div className="mt-3">
          <div className="text-sm text-dim mb-1">High-risk transformers left uncovered</div>
          <ul className="space-y-1 text-[15px]">{o.uncovered_high.map((u) => (
            <li key={u.id} className="flex gap-2"><button className="font-semibold w-16 shrink-0 text-left hover:underline" onClick={() => onSelect({ type: "asset", id: u.id })}>{u.id}</button>
              <span className="shrink-0 w-[4.5rem] text-sm font-semibold text-warn">{u.reason}</span><span className="text-sm text-dim">{u.text.replace(/^[a-z ]+: /, "")}</span></li>))}</ul>
        </div>
      )}
      <p className="mt-3 text-[15px]"><span className="text-sm text-dim">Against greedy: </span>{plan.why}</p>
    </Card>
  );
}

// ---------------------------------------------------------------- demo bar
function DemoBar({ demo, go, exit }) {
  return (
    <div className="px-6 py-3 bg-panel border-b-2 border-fg flex items-start gap-4">
      <span className="shrink-0 text-sm font-semibold px-2 py-0.5 rounded-md border border-fg mt-0.5">Demo {demo.step} of {demo.n_steps}</span>
      <div className="min-w-0 flex-1">
        <div className="text-base font-semibold">{demo.title}</div>
        <p className="text-[15px] text-dim">{demo.caption}</p>
      </div>
      <div className="shrink-0 flex gap-2">
        <button className="twin-btn" disabled={demo.step <= 1} onClick={() => go(demo.step - 1)}>Back</button>
        <button className="twin-btn twin-btn-primary" disabled={demo.step >= demo.n_steps} onClick={() => go(demo.step + 1)}>Next</button>
        <button className="twin-btn" onClick={() => go(0)}>Reset</button>
        <button className="twin-btn" onClick={exit}>Exit demo</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- page
export default function MapView({ live, theme, onOpenAsset, onDemo = () => {} }) {
  const C = useColors(theme);
  const [geo, setGeo] = useState(null);
  const [plan, setPlan] = useState(null);
  const [solving, setSolving] = useState(false);
  const [err, setErr] = useState(null);
  const [budget, setBudget] = useState(400000);
  const [ov, setOv] = useState({});
  const [lbOn, setLbOn] = useState(false);
  const [layers, setLayers] = useState(Object.fromEntries(LAYERS.map(([k]) => [k, k !== "overlap"])));
  const [f, setF] = useState({ health: "all", comm: "all", cover: "all", risk: "all", gateway: "all" });
  const [basemap, setBasemap] = useState(true);
  const [tilesDown, setTilesDown] = useState(null);
  const [sel, setSel] = useState(null);
  const [demo, setDemo] = useState(null);
  const [showPlan, setShowPlan] = useState(true);
  const seq = useRef(0);

  useEffect(() => { api("net/geo").then((g) => { setGeo(g); setBudget(g.budget_inr || 400000); }).catch((e) => setErr(String(e))); }, []);
  const req = useDebounced(JSON.stringify({ budget, overrides: ov, link_budget: lbOn }), 120);
  useEffect(() => {
    if (demo) return;
    const n = ++seq.current;
    setSolving(true);
    post("net/plan", JSON.parse(req)).then((p) => { if (n === seq.current) { setPlan(p); setErr(null); } })
      .catch((e) => setErr(String(e.message))).finally(() => n === seq.current && setSolving(false));
  }, [req, demo]);

  const goDemo = (k) => {
    const p = k === 0 ? post("net/demo-scenario/reset") : api(`net/demo-scenario?step=${k}`);
    setSolving(true);
    p.then((d) => { setDemo(d); onDemo(d.overview); setSel(d.highlight.assets[0] ? { type: "asset", id: d.highlight.assets[0] } : null); })
      .catch((e) => setErr(String(e.message))).finally(() => setSolving(false));
  };

  const onFail = useMemo(() => (why) => setTilesDown((x) => x || why), []);
  if (err && !geo) return <main className="p-6 text-alarm">{err}</main>;
  if (!geo) return <main className="p-6 text-dim">Loading map…</main>;

  const fleet = geo.fleet;
  const P = demo ? demo.plan : plan;
  const devices = demo ? demo.overview.devices : live?.devices || [];
  const dev = Object.fromEntries(devices.map((d) => [d.id, d]));
  const risks = P?.risks || {};
  const opt = P?.optimised;
  const planOn = demo ? demo.show_plan : showPlan;
  const shown = planOn && opt?.feasible ? opt : P?.current;
  const radius = (gid) => P?.radius_m?.[gid] ?? fleet.gateways.find((g) => g.id === gid).assumed_radius_m;
  const uncovered = new Set(shown?.uncovered || []);
  const opened = new Set(planOn ? opt?.gateways_opened || [] : []);
  const newNodes = new Set(planOn ? opt?.nodes || [] : []);
  const gwById = Object.fromEntries(fleet.gateways.map((g) => [g.id, g]));
  const hl = new Set(demo?.highlight.assets || []);
  const gwLive = Object.fromEntries((demo ? demo.overview.gateways : live?.gateways || []).map((g) => [g.id, g]));
  const lats = [...fleet.assets, ...fleet.gateways].map((x) => x.lat), lons = [...fleet.assets, ...fleet.gateways].map((x) => x.lon);
  const bounds = [[Math.min(...lats) - 0.01, Math.min(...lons) - 0.01], [Math.max(...lats) + 0.01, Math.max(...lons) + 0.01]];
  const offline = !basemap || Boolean(tilesDown);

  const pass = (a) => {
    const d = dev[a.id];
    const lv = d ? shownLevel(d) : "unmonitored";
    if (f.health !== "all" && !(f.health === "problem" ? ["warn", "alarm", "sensor"].includes(lv) : lv === f.health)) return false;
    if (f.comm !== "all" && (!d || (f.comm === "online" ? d.comms.status !== "online" : d.comms.status === "online"))) return false;
    if (f.cover !== "all" && (f.cover === "covered") === uncovered.has(a.id)) return false;
    if (f.risk === "high" && risks[a.id]?.band !== "high") return false;
    const asg = a.monitored ? (shown?.assign?.[a.id] || a.gateway) : shown?.assign?.[a.id];
    if (f.gateway !== "all" && asg !== f.gateway) return false;
    return true;
  };
  const assignOf = (a) => (planOn && opt?.assign?.[a.id]) || (a.monitored ? (P?.current?.assign?.[a.id] || a.gateway) : null);

  return (
    <>
      {demo && <DemoBar demo={demo} go={goDemo} exit={() => { setDemo(null); onDemo(null); setSel(null); }} />}
      <main className={`px-6 py-5 space-y-4 ${offline ? "map-offline" : ""}`}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span className="font-semibold px-2 py-0.5 rounded-md border border-warn text-warn">{geo.labels.positions}</span>
          <span className="font-semibold px-2 py-0.5 rounded-md border border-edge text-dim">{geo.labels.coverage}</span>
          <span className="text-dim">{geo.labels.costs}. Area: {geo.area}.</span>
          <span className="ml-auto flex items-center gap-2">
            {!demo && <button className="twin-btn twin-btn-primary" onClick={() => goDemo(1)}>Demo</button>}
            {!demo && <label className="flex items-center gap-1.5"><input type="checkbox" checked={showPlan} onChange={(e) => setShowPlan(e.target.checked)} />Show plan</label>}
            <button className="twin-btn" aria-pressed={basemap} onClick={() => setBasemap(!basemap)}>Basemap {basemap ? "on" : "off"}</button>
            <Help title="the map" text={["Transformers coloured by their own twin's health; grey with a dashed outline when stale or offline (last known, unverified).",
              "Gateways: solid square existing, dotted candidate site, dashed planned. Coverage circles are an ESTIMATED proximity radius, not verified LoRa coverage. Planned links are dotted.",
              "Basemap © OpenStreetMap contributors. If tiles cannot load the map switches to a lat/lon grid with a scale bar."]} />
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span className="text-dim">Layers</span>
          {LAYERS.map(([k, l]) => (
            <label key={k} className="flex items-center gap-1.5"><input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />{l}</label>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          {[["health", "Health", [["all", "All"], ["problem", "Problems"], ["ok", "Normal"], ["unknown", "Unknown"], ["unmonitored", "Not monitored"]]],
            ["comm", "Communication", [["all", "All"], ["online", "Online"], ["dark", "Stale or offline"]]],
            ["cover", "Coverage", [["all", "All"], ["covered", "Covered"], ["uncovered", "Uncovered"]]],
            ["risk", "Risk", [["all", "All"], ["high", "High risk only"]]],
            ["gateway", "Gateway", [["all", "All"], ...fleet.gateways.map((g) => [g.id, g.id])]]].map(([k, l, opts]) => (
            <label key={k} className="flex items-center gap-1.5 text-dim">{l}
              <select aria-label={`${l} filter`} className="border border-edge rounded-lg px-2 py-1 text-fg" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })}>
                {opts.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
            </label>
          ))}
          {tilesDown && basemap && <span className="ml-auto font-semibold text-dim">Basemap unavailable ({tilesDown}): offline grid shown</span>}
        </div>
        {err && <p className="text-alarm">{err}</p>}
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px] items-start">
          <div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-dim mb-2">
            {[["ok", "Normal"], ["warn", "Warning"], ["alarm", "Alarm"], ["sensor", "Instrument fault"], ["unknown", "Unknown / unverified"]].map(([k, l]) => (
              <span key={k} className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full" style={{ background: C[k] }} />{l}</span>))}
            <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full border-2 border-dashed" style={{ borderColor: C.dim, background: C.unknown }} />Stale / offline</span>
            <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full border-2" style={{ borderColor: C.unmon }} />Not monitored</span>
            <span className="flex items-center gap-1.5"><span className="w-3.5 h-3.5 rounded-full border border-dashed" style={{ borderColor: C.fg }} />Uncovered</span>
            <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm" style={{ background: C.fg }} />Gateway, <span className="w-3 h-3 rounded-sm border-2 border-dashed" style={{ borderColor: C.fg }} />planned, <span className="w-3 h-3 rounded-sm border-2 border-dotted" style={{ borderColor: C.unmon }} />candidate</span>
            <span className="flex items-center gap-1.5"><span className="w-5 border-t-2 border-dotted" style={{ borderColor: C.fg }} />Planned link</span>
          </div>
          <div className="card overflow-hidden relative" style={{ height: "calc(100vh - 290px)", minHeight: 520 }} data-testid="geo-map" data-offline={offline ? "yes" : "no"}>
            <MapContainer bounds={bounds} style={{ height: "100%", width: "100%" }} zoomSnap={0.25} scrollWheelZoom attributionControl>
              <FitOnce bounds={bounds} />
              {basemap && !tilesDown && <Basemap onFail={onFail} />}
              {offline && <Graticule bounds={bounds} color={C.edge} />}
              <ScaleControl position="bottomleft" metric imperial={false} />
              {/* coverage circles */}
              {layers.coverage && fleet.gateways.filter((g) => g.existing || opened.has(g.id)).map((g) => (
                <Circle key={`cov${g.id}`} center={[g.lat, g.lon]} radius={radius(g.id)} interactive={false}
                  pathOptions={{ color: C.dim, weight: 1.4, dashArray: g.existing ? "6 5" : "2 6", fillColor: C.fg, fillOpacity: 0.05 }} />
              ))}
              {layers.overlap && fleet.gateways.filter((g) => !g.existing && !opened.has(g.id)).map((g) => (
                <Circle key={`ovl${g.id}`} center={[g.lat, g.lon]} radius={radius(g.id)} interactive={false}
                  pathOptions={{ color: C.unmon, weight: 1, dashArray: "2 5", fillColor: C.sensor, fillOpacity: 0.08 }} />
              ))}
              {/* association lines */}
              {layers.links && fleet.assets.map((a) => {
                const gid = assignOf(a);
                if (!gid || !pass(a)) return null;
                const d = dev[a.id];
                const gone = d && d.comms.status !== "online";
                const planned = newNodes.has(a.id) || opened.has(gid) || (a.monitored && gid !== a.gateway);
                const g = gwById[gid];
                return <Polyline key={`ln${a.id}`} positions={[[a.lat, a.lon], [g.lat, g.lon]]} interactive={false}
                  pathOptions={{ color: planned ? C.fg : C.dim, weight: planned ? 1.6 : 1.3, opacity: gone ? 0.4 : 0.75, dashArray: planned ? "1 6" : gone ? "4 5" : undefined, lineCap: "round" }} />;
              })}
              {/* uncovered transformers: a distinct dashed ring */}
              {layers.uncovered && fleet.assets.filter((a) => uncovered.has(a.id) && pass(a)).map((a) => (
                <CircleMarker key={`unc${a.id}`} center={[a.lat, a.lon]} radius={13} interactive={false}
                  pathOptions={{ color: C.fg, weight: 1.6, dashArray: "3 3", fill: false }} />
              ))}
              {/* transformers */}
              {layers.transformers && fleet.assets.filter(pass).map((a) => {
                const d = dev[a.id];
                const r = risks[a.id];
                const isSel = sel?.type === "asset" && sel.id === a.id;
                if (!a.monitored) {
                  return (
                    <CircleMarker key={a.id} center={[a.lat, a.lon]} radius={4 + (r ? r.risk / 14 : 0)} eventHandlers={{ click: () => setSel({ type: "asset", id: a.id }) }}
                      pathOptions={{ color: newNodes.has(a.id) ? C.fg : C.unmon, weight: newNodes.has(a.id) ? 2.6 : r?.band === "high" ? 2.2 : 1.5,
                        fillColor: C.panel, fillOpacity: newNodes.has(a.id) ? 0.95 : 0.5 }}>
                      <Tooltip>{a.id} · not monitored · risk {fmt(r?.risk, 0)} ({r?.band}){newNodes.has(a.id) ? " · planned node" : ""}{uncovered.has(a.id) ? " · uncovered" : ""}</Tooltip>
                    </CircleMarker>
                  );
                }
                const lv = d ? shownLevel(d) : "unknown";
                const dark = d && d.comms.status !== "online";
                return (
                  <CircleMarker key={a.id} center={[a.lat, a.lon]} radius={9} eventHandlers={{ click: () => setSel({ type: "asset", id: a.id }) }}
                    pathOptions={{ color: dark ? C.dim : isSel ? C.fg : C.panel, weight: dark ? 2.4 : 2, dashArray: dark ? "3 3" : undefined,
                      fillColor: C[lv] || C.unknown, fillOpacity: 1 }}>
                    <Tooltip direction="right" offset={[10, 0]} permanent={hl.has(a.id)}>
                      {a.id} · {d?.health.verified ? `${LEVEL_WORD[lv]}` : `last known, unverified`} · {COMM_WORD[d?.comms.status || "no data"]}
                      {dark && d?.comms.last_seen ? ` · last seen ${when(d.comms.last_seen)}` : ""}</Tooltip>
                  </CircleMarker>
                );
              })}
              {fleet.assets.filter((a) => hl.has(a.id)).map((a) => (
                <CircleMarker key={`hl${a.id}`} center={[a.lat, a.lon]} radius={20} interactive={false} className="ring-pulse"
                  pathOptions={{ color: C.fg, weight: 3, fill: false, className: "ring-pulse" }} />
              ))}
              {/* gateways */}
              {fleet.gateways.filter((g) => (g.existing ? layers.existing : layers.candidates || opened.has(g.id))).map((g) => {
                const kind = g.existing ? "existing" : opened.has(g.id) ? "planned" : "candidate";
                const st = gwLive[g.id]?.status;
                return (
                  <Marker key={g.id} position={[g.lat, g.lon]} icon={gwIcon(kind, g.id, C, g.existing && st && st !== "online" && st !== "no data")}
                    eventHandlers={{ click: () => setSel({ type: "gateway", id: g.id }) }}>
                    <Tooltip direction="top" offset={[0, -14]} permanent>{g.id}{kind === "planned" ? " (planned)" : kind === "candidate" ? " (candidate)" : ""}</Tooltip>
                  </Marker>
                );
              })}
            </MapContainer>
          </div>
          </div>
          <div className="space-y-4">
            {sel?.type === "asset" && <TransformerPanel key={sel.id} id={sel.id} geo={geo} plan={P} dev={dev[sel.id]} risk={risks[sel.id]}
              onClose={() => setSel(null)} onOpenAsset={onOpenAsset} />}
            {sel?.type === "gateway" && <GatewayPanel key={sel.id} id={sel.id} geo={geo} plan={P} live={gwLive[sel.id]} ov={ov}
              setOv={demo ? () => {} : setOv} onClose={() => setSel(null)} />}
            {!demo && <PlanPanel plan={plan} budget={budget} setBudget={setBudget} lbOn={lbOn} setLbOn={setLbOn} solving={solving} onSelect={setSel} />}
            {demo && P && <PlanPanel plan={{ ...P, solve_ms: null }} budget={demo.budget} setBudget={() => {}} lbOn={false} setLbOn={() => {}} solving={solving} onSelect={setSel} />}
          </div>
        </div>
      </main>
    </>
  );
}
