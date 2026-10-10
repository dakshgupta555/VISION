import { useCallback, useEffect, useRef, useState } from "react";

// Two pages: Overview (the twin, what is happening, four key numbers) and
// Engineering (everything behind them). A replay cursor drives both: the twin
// answers as of the selected instant, using only data up to it.

import { api, useTheme } from "./ui.jsx";
import { ModeSwitch, useLive } from "./live.jsx";
import Overview, { TimeBar, TopBar } from "./overview/Overview.jsx";
import SimulateWorkspace from "./twin/Simulate.jsx";
import EngineeringPage from "./eng/EngineeringPage.jsx";
import Measurements from "./eng/Measurements.jsx";
import { useFem } from "./twin/TwinPage.jsx";
import HeatMapView from "./twin/HeatMap.jsx";
import { HELP } from "./help.js";
import { Card } from "./ui.jsx";

function FemTab({ scen, cursor, version }) {
  const [fem, resolve] = useFem(scen, cursor, version);
  return <Card title="Winding temperature field" help={HELP.fem} pad="p-0"><HeatMapView fem={fem} live={scen === "live"} onResolve={resolve} /></Card>;
}

export default function App() {
  const [theme, setTheme] = useTheme();
  const [scenarios, setScenarios] = useState([]);
  const [scenario, setScenario] = useState("showcase");
  const [tl, setTl] = useState(null);
  const [i, setI] = useState(null);
  const [s, setS] = useState(null);
  const [series, setSeries] = useState(null);
  const [err, setErr] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [kva, setKva] = useState(null);          // null = nameplate as recorded
  const [recorded, setRecorded] = useState(null);
  const inflight = useRef(false);
  const q = kva ? `&kva=${kva}` : "";
  const [mode, setMode] = useState("replay");     // replay is the default and the demo fallback
  const [view, setView] = useState("overview");
  const live = useLive(mode === "live");

  useEffect(() => { api("scenarios").then((d) => setScenarios(d.scenarios)).catch((e) => setErr(String(e))); }, []);

  useEffect(() => {
    setS(null); setSeries(null); setTl(null); setPlaying(false);
    api(`${scenario}/timeline?${q}`).then((d) => { setTl(d); setI((x) => x ?? 0); }).catch((e) => setErr(String(e)));
  }, [scenario, q]);
  useEffect(() => { setKva(null); setRecorded(null); setI(null); }, [scenario]);
  useEffect(() => { if (s && !kva) setRecorded(s.nameplate.rating_kva); }, [s, kva]);

  const load = useCallback((idx) => {
    if (inflight.current) return;
    inflight.current = true;
    Promise.all([api(`${scenario}/state?t=${idx}${q}`), api(`${scenario}/series?t=${idx}${q}`)])
      .then(([st, se]) => { setS(st); setSeries(se); setErr(null); })
      .catch((e) => setErr(String(e)))
      .finally(() => { inflight.current = false; });
  }, [scenario, q]);

  useEffect(() => { if (i !== null) load(i); }, [i, load]);

  useEffect(() => {
    if (!playing || !s) return undefined;
    const id = setInterval(() => setI((x) => Math.min(x + speed, s.n - 1)), 400);
    return () => clearInterval(id);
  }, [playing, speed, s]);

  if (mode === "live" && !live.s) {
    return (
      <div className="min-h-screen">
        <header className="flex items-center gap-4 px-6 py-3 bg-panel border-b border-edge">
          <div className="text-lg font-semibold">Live</div>
          <div className="ml-auto"><ModeSwitch mode={mode} setMode={setMode} /></div>
        </header>
        <div className="p-6 text-dim text-[15px]">
          {live.status?.has_data === false
            ? <>No live data yet. Start the stream in a terminal: <code className="text-fg">python -m dtwin.live --speed 600</code></>
            : live.err || "Waiting for the live stream…"}
        </div>
      </div>
    );
  }
  if (err && !s) return <div className="p-6 text-alarm">Backend unreachable: {err}. Start it with: uvicorn dtwin.api.app:app --port 8000</div>;
  if (!s) return <div className="p-6 text-dim">Loading twin…</div>;
  const cur = mode === "live" ? live.s : s;
  const ctx = {
    s: cur, mode, setMode, live, stale: mode === "live" && Boolean(live.status?.stale), theme, setTheme,
    scenario, scenarios, setScenario, kva, setKva, recorded: recorded ?? s.nameplate.rating_kva,
    tl, i, setI, playing, setPlaying, speed, setSpeed, scenKey: mode === "live" ? "live" : scenario,
  };
  return <Board ctx={ctx} series={mode === "live" ? live.series : series} view={view} setView={setView} />;
}

function Board({ ctx, series, view, setView }) {
  const [simAt, setSimAt] = useState(null);       // baseline cursor frozen when Simulate opens
  const { s, scenKey, mode, kva } = ctx;
  if (simAt !== null) {
    return <SimulateWorkspace scen={scenKey} cursor={simAt} onExit={() => setSimAt(null)} />;
  }
  const header = (
    <>
      <TopBar ctx={ctx} view={view} setView={setView} />
      {s.data_source.warnings.length > 0 && (
        <div className="px-6 py-2 bg-panel border-b border-warn text-sm text-warn">
          <b>Data source: </b>{s.data_source.warnings.join("; ")}.
          <span className="text-dim"> See the mapping report: python -m dtwin.ingest data/{ctx.scenario}.csv</span>
        </div>
      )}
      <TimeBar ctx={ctx} />
      {ctx.stale && (
        <div className="px-6 py-2 bg-panel border-b border-edge text-sm text-dim">
          Showing the last known state, greyed out: {ctx.live.status.reason}.
        </div>
      )}
    </>
  );
  if (view === "engineering") {
    return (
      <EngineeringPage scen={scenKey} cursor={s.i} version={s.timestamp} header={header}
        extraTabs={[
          { key: "measure", label: "Measurements and alarms", first: true,
            render: () => <Measurements s={s} series={series} scen={scenKey} kva={kva} mode={mode} /> },
          { key: "fem", label: "Heat map (FEM)", render: () => <FemTab scen={scenKey} cursor={s.i} version={s.timestamp} /> },
        ]} />
    );
  }
  return (
    <div className="min-h-screen">
      {header}
      <Overview ctx={ctx} series={series} onSimulate={() => { ctx.setPlaying(false); setSimAt(s.i); }} />
    </div>
  );
}
