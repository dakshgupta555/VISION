// GridTwin network dashboard. Three tabs: Network twin | Deployment planner |
// Strategy comparison. Same look as the single-transformer app (:5173), which
// it links to; nothing is shared at runtime.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmt, Icon, StatusPill, useTheme, when } from "./ui.jsx";
import { DISCLAIMER } from "./help.js";
import { post, shownLevel, usePopover } from "./parts.jsx";
import NetworkTab from "./NetworkTab.jsx";
import AssetPage from "./AssetPage.jsx";
import Planner from "./Planner.jsx";
import Strategy from "./Strategy.jsx";
import MapView from "./MapView.jsx";

const SINGLE_TWIN = "http://localhost:5173";
const FAULTS = [
  { kind: "overload", label: "Overload", severity: 1.3, ramp: 1, unit: "pu load, at most 1.5" },
  { kind: "cooling_degradation", label: "Cooling degradation", severity: 0.5, ramp: 0, unit: "× rise" },
  { kind: "oil_loss", label: "Oil loss", severity: 0.5, ramp: 6, unit: "fraction" },
  { kind: "unbalance_growth", label: "Phase unbalance", severity: 0.15, ramp: 6, unit: "fraction" },
  { kind: "sensor_drift", label: "Oil probe drift", severity: 6, ramp: 6, unit: "K", channel: "oil_temp" },
  { kind: "sensor_dead", label: "Dead current channel", severity: 1, ramp: 0, unit: "", channel: "IB" },
];

// ======================================================================= network status pill
function netStatus(ov) {
  if (!ov?.devices) return { level: "info", text: "No data" };
  const ver = ov.devices.filter((d) => d.health.verified);
  const lv = ver.map(shownLevel);
  const dark = ov.devices.length - ver.length;
  if (lv.includes("alarm")) return { level: "alarm", text: `${lv.filter((x) => x === "alarm").length} in alarm` };
  if (lv.includes("warn")) return { level: "warn", text: "Warning" };
  if (lv.includes("sensor")) return { level: "sensor", text: "Instrument fault" };
  if (dark) return { level: "info", text: `${dark} not reporting` };
  return { level: "ok", text: "All normal" };
}

// Replay snapshots carry devices and gateways only; the words are rebuilt here
// from the same rules, numbers from the snapshot.
function replayView(snap, fleet) {
  const devs = snap.devices;
  const ver = devs.filter((d) => d.health.verified);
  const bad = ver.filter((d) => ["alarm", "warn"].includes(d.health.level));
  const sens = ver.filter((d) => d.health.level === "sensor");
  const dark = devs.filter((d) => d.comms.status !== "online");
  const used = snap.gateways.reduce((s, g) => s + g.used, 0), cap = snap.gateways.reduce((s, g) => s + g.capacity, 0);
  const whats = bad.length
    ? { level: bad.some((d) => d.health.level === "alarm") ? "alarm" : "warn", headline: `${bad[0].id} needed attention: ${(bad[0].health.label || "").toLowerCase()}`,
      meaning: `${bad.length} with an asset problem, ${dark.length} not reporting, at ${when(snap.clock)}.` }
    : dark.length ? { level: "info", headline: `${dark.length} not reporting at ${when(snap.clock)}`, meaning: "Last known health shown unverified, in grey." }
      : { level: "ok", headline: `All ${devs.length} monitored transformers normal at ${when(snap.clock)}`, meaning: "Replay of what the network twin showed then." };
  return {
    ...snap, whats: { ...whats, actions: [], evidence: "Replay: the state the backend held at this network time." },
    kpis: { monitored: devs.length, assets: fleet.assets.length, alarms: bad.length, sensor_faults: sens.length,
      stale: dark.filter((d) => d.comms.status === "stale").length, offline: dark.filter((d) => d.comms.status !== "stale").length,
      gateway_used: used, gateway_capacity: cap, gateway_pct: cap ? Math.round((100 * used) / cap) : null },
    risks: null, visit_first: [],
  };
}

// ======================================================================= top bar
function Settings({ theme, setTheme }) {
  const p = usePopover();
  return (
    <div ref={p.ref} className="relative">
      <button className="twin-btn flex items-center gap-1.5" aria-label="Settings" aria-expanded={p.open} onClick={() => p.setOpen(!p.open)}>
        <Icon name="gear" className="w-5 h-5" /><span className="hidden xl:inline">Settings</span>
      </button>
      {p.open && (
        <div className="absolute right-0 top-11 z-50 card p-5 w-80 space-y-4 text-sm">
          <div>
            <span className="text-dim">Theme</span>
            <div className="mt-1 flex gap-2">
              {["light", "dark"].map((t) => (
                <button key={t} className={`twin-btn flex-1 ${theme === t ? "twin-btn-primary" : ""}`} onClick={() => setTheme(t)}>
                  {t === "light" ? "Light" : "Dark"}</button>
              ))}
            </div>
          </div>
          <p className="text-dim">{DISCLAIMER}</p>
          <p className="text-dim">Fleet file: config_network/fleet.yaml. Backend :8001, this app :5180.</p>
        </div>
      )}
    </div>
  );
}

function ModeSwitch({ mode, setMode }) {
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

function TopBar({ ov, fleet, tab, setTab, mode, setMode, theme, setTheme }) {
  const st = netStatus(ov);
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3 bg-panel border-b border-edge">
      <div className="min-w-0">
        <div className="text-lg font-semibold leading-tight">{fleet.district.name}</div>
        <div className="text-sm text-dim">GridTwin network: {fleet.assets.filter((a) => a.monitored).length} of {fleet.assets.length} transformers monitored, LoRaWAN</div>
      </div>
      <nav className="flex gap-1" aria-label="Pages">
        {[["network", "Network twin"], ["map", "Map"], ["planner", "Deployment planner"], ["strategy", "Strategy comparison"]].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} aria-current={tab === k ? "page" : undefined}
            className={`px-3 py-1.5 rounded-lg text-[15px] ${tab === k ? "bg-ink text-fg font-semibold" : "text-dim hover:text-fg"}`}>{label}</button>
        ))}
      </nav>
      <StatusPill big level={st.level} text={st.text} />
      <div className="ml-auto flex items-center gap-4">
        <span className="text-sm font-semibold px-3 py-1 rounded-full border border-fg" title={DISCLAIMER}>Simulated network</span>
        <ModeSwitch mode={mode} setMode={setMode} />
        <div className="text-right leading-tight">
          <div className="text-[15px] font-semibold">{when(ov?.clock)}</div>
          <div className="text-sm text-dim">{mode === "live" ? "network time" : "replay time"}</div>
        </div>
        <a className="twin-btn whitespace-nowrap" href={SINGLE_TWIN} target="_blank" rel="noreferrer">Open single-transformer twin ↗</a>
        <Settings theme={theme} setTheme={setTheme} />
      </div>
    </header>
  );
}

// ======================================================================= demo bar (live) / scrubber (replay)
function InjectPop({ devices, onDone }) {
  const p = usePopover();
  const [dev, setDev] = useState("DT-007");
  const [k, setK] = useState(0);
  const [sev, setSev] = useState(FAULTS[0].severity);
  const [msg, setMsg] = useState(null);
  const f = FAULTS[k];
  const go = () => post("net/demo/inject", { device: dev, kind: f.kind, severity: Number(sev), ramp_h: f.ramp, channel: f.channel })
    .then(() => { setMsg(`Injected into ${dev}. Only its own physics changes.`); onDone(); }).catch((e) => setMsg(String(e.message)));
  return (
    <div ref={p.ref} className="relative">
      <button className="twin-btn twin-btn-primary" onClick={() => p.setOpen(!p.open)} aria-expanded={p.open}>Inject a fault</button>
      {p.open && (
        <div className="absolute left-0 top-11 z-50 card p-5 w-[26rem] space-y-3 text-sm">
          <label className="block"><span className="text-dim">Transformer</span>
            <select aria-label="Transformer" className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5" value={dev} onChange={(e) => setDev(e.target.value)}>
              {devices.map((d) => <option key={d.id}>{d.id}</option>)}</select></label>
          <label className="block"><span className="text-dim">Fault</span>
            <select aria-label="Fault" className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5" value={k}
              onChange={(e) => { setK(Number(e.target.value)); setSev(FAULTS[Number(e.target.value)].severity); }}>
              {FAULTS.map((x, i) => <option key={x.kind} value={i}>{x.label}</option>)}</select></label>
          {f.unit && <label className="block"><span className="text-dim">Severity ({f.unit})</span>
            <input type="number" step="0.05" className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5" value={sev} onChange={(e) => setSev(e.target.value)} /></label>}
          <p className="text-dim">The fault changes that transformer's physics in the simulator. The backend only sees its packets.</p>
          <button className="twin-btn twin-btn-primary w-full" onClick={go}>Inject into {dev}</button>
          {msg && <p className="text-dim">{msg}</p>}
        </div>
      )}
    </div>
  );
}

function CutPop({ devices, gateways, demo, onDone }) {
  const p = usePopover();
  const [dev, setDev] = useState("DT-004");
  const cut = new Set(demo?.comm_cut || []);
  const gdown = new Set(demo?.gateway_down || []);
  return (
    <div ref={p.ref} className="relative">
      <button className="twin-btn" onClick={() => p.setOpen(!p.open)} aria-expanded={p.open}>Cut communication</button>
      {p.open && (
        <div className="absolute left-0 top-11 z-50 card p-5 w-[26rem] space-y-3 text-sm">
          <label className="block"><span className="text-dim">Transformer</span>
            <select aria-label="Transformer link" className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5" value={dev} onChange={(e) => setDev(e.target.value)}>
              {devices.map((d) => <option key={d.id}>{d.id}</option>)}</select></label>
          <div className="flex gap-2">
            <button className="twin-btn twin-btn-primary flex-1" onClick={() => post("net/demo/link", { device: dev, cut: true }).then(onDone)}>Cut {dev}'s radio link</button>
            <button className="twin-btn flex-1" onClick={() => post("net/demo/link", { device: dev, cut: false }).then(onDone)}>Restore</button>
          </div>
          <p className="text-dim">The node keeps measuring and buffers its packets; on restore they are replayed oldest first. It goes stale after 2 missed intervals, offline after 6.</p>
          <div className="border-t border-edge pt-3">
            <span className="text-dim">Gateways</span>
            <div className="mt-1 flex gap-2">
              {gateways.map((g) => (
                <button key={g.id} className={`twin-btn flex-1 ${gdown.has(g.id) ? "twin-btn-primary" : ""}`}
                  onClick={() => post("net/demo/gateway", { gateway: g.id, down: !gdown.has(g.id) }).then(onDone)}>
                  {g.id} {gdown.has(g.id) ? "off" : "on"}</button>
              ))}
            </div>
          </div>
          {cut.size > 0 && <p>Cut now: <b>{[...cut].join(", ")}</b></p>}
        </div>
      )}
    </div>
  );
}

function LiveBar({ ov, refresh }) {
  const demo = ov?.demo || {};
  const loss = demo.packet_loss || 0;
  return (
    <div className="px-6 py-3 bg-panel border-b border-edge flex flex-wrap items-center gap-4 text-sm">
      <span className="font-semibold">Live network</span>
      <span className="text-dim">{ov?.receiving ? "gateways reporting" : "no gateway reporting: start python -m dtwin_network.sim"}</span>
      <InjectPop devices={ov?.devices || []} onDone={refresh} />
      <CutPop devices={ov?.devices || []} gateways={ov?.gateways || []} demo={demo} onDone={refresh} />
      <label className="flex items-center gap-2 text-dim">Packet loss
        <select className="border border-edge rounded-lg px-2 py-1 text-fg" value={loss}
          onChange={(e) => post("net/demo/control", { packet_loss: Number(e.target.value) }).then(refresh)}>
          {[0, 0.05, 0.2, 0.5].map((x) => <option key={x} value={x}>{Math.round(100 * x)} %</option>)}
        </select>
      </label>
      <button className="twin-btn" onClick={() => post("net/demo/control", { paused: !demo.paused }).then(refresh)}>{demo.paused ? "Resume" : "Pause"}</button>
      <button className="twin-btn ml-auto" onClick={() => post("net/demo/reset").then(refresh)}>Reset demo</button>
    </div>
  );
}

function ReplayBar({ hist, k, setK }) {
  if (!hist?.n) return <div className="px-6 py-3 bg-panel border-b border-edge text-sm text-dim">No history yet: switch to Live and let the network run.</div>;
  return (
    <div className="px-6 py-3 bg-panel border-b border-edge flex items-center gap-4 text-sm">
      <span className="font-semibold whitespace-nowrap">Replay</span>
      <input type="range" aria-label="Replay position" min={0} max={hist.n - 1} value={k ?? hist.n - 1} className="flex-1"
        onChange={(e) => setK(Number(e.target.value))} />
      <span className="text-dim whitespace-nowrap">{when(hist.sim[k ?? hist.n - 1])} ({fmt((k ?? hist.n - 1) + 1, 0)} of {fmt(hist.n, 0)} snapshots)</span>
      <button className="twin-btn" onClick={() => setK(hist.n - 1)}>Latest</button>
    </div>
  );
}

// ======================================================================= app
export default function App() {
  const [theme, setTheme] = useTheme();
  const [fleet, setFleet] = useState(null);
  const [ov, setOv] = useState(null);
  const [err, setErr] = useState(null);
  const [tab, setTab] = useState("network");
  const [mode, setMode] = useState("live");
  const [asset, setAsset] = useState(null);
  const [hist, setHist] = useState(null);
  const [k, setK] = useState(null);
  const [budget, setBudget] = useState(400000);
  const [demoOv, setDemoOv] = useState(null);       // the map's demo state, while a demo runs
  const inflight = useRef(0);

  useEffect(() => { api("net/fleet").then(setFleet).catch((e) => setErr(String(e))); }, []);
  // always fetch; a response that is older than the newest request is dropped
  const refresh = useCallback(() => {
    const n = ++inflight.current;
    const q = mode === "replay" && k !== null ? `?at=${k}` : "";
    api(`net/overview${q}`).then((x) => { if (n === inflight.current) { setOv(x); setErr(null); } })
      .catch((e) => { if (n === inflight.current) setErr(String(e)); });
  }, [mode, k]);
  useEffect(() => {
    refresh();
    if (mode !== "live") return undefined;
    const id = setInterval(refresh, 1500);
    return () => clearInterval(id);
  }, [refresh, mode]);
  useEffect(() => {
    if (mode !== "replay") { setK(null); return; }
    api("net/history").then((h) => { setHist(h); setK(Math.max(0, h.n - 1)); }).catch(() => {});
  }, [mode]);

  if (err && !fleet) return <div className="p-6 text-alarm">Network backend unreachable: {err}. Start it with: uvicorn dtwin_network.app:app --port 8001</div>;
  if (!fleet || !ov) return <div className="p-6 text-dim">Loading network…</div>;
  const view = ov.replay ? replayView(ov, fleet) : ov;
  const openAsset = (id) => { setAsset(id); window.scrollTo(0, 0); };
  return (
    <div className="min-h-screen">
      <TopBar ov={tab === "map" && demoOv ? demoOv : view} fleet={fleet} tab={tab} setTab={(t) => { setTab(t); setAsset(null); setDemoOv(null); }} mode={mode} setMode={setMode} theme={theme} setTheme={setTheme} />
      <div className="px-6 py-1.5 bg-panel border-b border-edge text-sm text-dim">{DISCLAIMER}</div>
      {tab === "network" && (mode === "live" ? <LiveBar ov={ov} refresh={refresh} /> : <ReplayBar hist={hist} k={k} setK={setK} />)}
      {err && <div role="alert" className="px-6 py-2 bg-panel border-b-2 border-alarm text-alarm font-semibold">Backend unreachable: {err}. Showing the last state received.</div>}
      {tab === "network" && (asset
        ? <AssetPage id={asset} version={view.clock} onBack={() => setAsset(null)} />
        : <NetworkTab fleet={fleet} ov={view} onAsset={openAsset} replay={Boolean(ov.replay)} />)}
      {tab === "planner" && <Planner fleet={fleet} budget={budget} setBudget={setBudget} />}
      {tab === "strategy" && <Strategy budget={budget} setBudget={setBudget} />}
      {tab === "map" && <MapView live={view} theme={theme} onDemo={setDemoOv} onOpenAsset={(id) => { setTab("network"); openAsset(id); }} />}
    </div>
  );
}
