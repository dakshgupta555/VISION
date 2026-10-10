import { useCallback, useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

// Six zones: status header, live gauges, trends, health breakdown, alert feed,
// recommendations. A replay cursor drives all of them: the twin answers as of
// the selected instant, using only data up to it.

import { api, axis, band, BG, BORDER, Fold, fmt, hhmm, LEVEL, Panel, TEXT, tip, ttt } from "./ui.jsx";
import {
  AmbientTable, ChannelGrid, Cooling, Countdown, Gprs, Life, NameplateSelect, Notifications, SelfTest, untrusted,
  WhatIf,
} from "./features.jsx";
import { InjectPanel, LiveBar, ModeSwitch, useLive } from "./live.jsx";
import TwinPage from "./twin/TwinPage.jsx";
import SimulateWorkspace from "./twin/Simulate.jsx";
import TwinBar, { NavTabs } from "./twin/TwinBar.jsx";

// ---------------------------------------------------------------- 1. header

function HeadStat({ label, value, unit = "", tone = "" }) {
  return (
    <div className="text-right">
      <div className="text-[10px] uppercase tracking-widest text-dim">{label}</div>
      <div className={`num text-xl ${tone}`}>{value}<span className="text-dim text-xs">{unit}</span></div>
    </div>
  );
}

function Header({ s, scenarios, scenario, setScenario, kva, setKva, recorded, mode, setMode, stale, view, setView }) {
  const lvl = stale ? "info" : s.status.level;
  const label = stale ? "NO RECENT DATA" : { ok: "NORMAL", warn: "ATTENTION", alarm: "ALARM" }[lvl];
  const active = s.alerts_live.length;
  const hb = Object.fromEntries(s.health.components.map((c) => [c.key, band(c.score)]));
  const tx = (b) => (b && b !== "ok" ? TEXT[b] : "");
  // the index never reads healthier than the status beside it
  const worse = (a, b) => (["alarm", "warn", "ok"].find((x) => x === a || x === b) ?? a);
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 border-b border-edge">
      <div>
        <div className="text-lg font-semibold">{s.nameplate.tag}</div>
        <div className="text-xs text-dim num">
          {s.nameplate.rating_kva} kVA · {s.nameplate.hv_kv} kV / {s.nameplate.lv_v} V · {s.nameplate.cooling}
        </div>
      </div>
      <NavTabs view={view} setView={setView} />
      <div className={`border rounded px-3 py-1 font-semibold tracking-wider ${LEVEL[lvl]}`}>{label}</div>
      <div className="num text-sm text-dim">{s.timestamp.replace("T", " ")}</div>
      <div className="ml-auto flex flex-wrap items-center gap-5">
        <div className={`flex flex-wrap items-center gap-5 ${stale ? "grayscale opacity-40" : ""}`}
          title={stale ? "last known values - no recent data" : undefined}>
        <HeadStat label="Health index" value={fmt(s.health.index, 0)} unit="/100" tone={tx(worse(band(s.health.index), lvl))} />
        <HeadStat label="Hot-spot" value={fmt(s.readings.hotspot_c)} unit="°C"
          tone={tx(hb.ageing)} />
        <HeadStat label="Loading" value={fmt(s.readings.load_pct, 0)} unit="%"
          tone={tx(hb.loading)} />
        <HeadStat label={`Time to hot-spot ${s.countdown.hotspot.limit_c} °C`} value={ttt(s.countdown.hotspot)}
          tone={s.countdown.hotspot.already_above ? "text-alarm" : s.countdown.hotspot.minutes !== null ? "text-warn" : ""} />
        <HeadStat label="Active alerts" value={active}
          tone={active ? LEVEL[lvl].split(" ")[0] : "text-ok"} />
        </div>
        <a className="border border-accent text-accent rounded px-2 py-1 text-xs"
          href={mode === "live" ? "/api/live/report.pdf" : `/api/${scenario}/report.pdf?t=${s.i}${kva ? `&kva=${kva}` : ""}`} download>
          Download report (PDF)
        </a>
        <ModeSwitch mode={mode} setMode={setMode} />
        {mode === "replay" && <NameplateSelect kva={kva} setKva={setKva} rated={recorded} />}
        {mode === "replay" && (
          <select className="bg-ink border border-edge rounded px-2 py-1 text-sm" value={scenario}
            onChange={(e) => setScenario(e.target.value)}>
            {scenarios.map((n) => <option key={n}>{n}</option>)}
          </select>
        )}
      </div>
    </header>
  );
}

// ---------------------------------------------------------------- replay bar
function Replay({ tl, i, n, setI, playing, setPlaying, speed, setSpeed }) {
  const color = ["#1f2a37", "#f59e0b", "#ef4444"];
  return (
    <div className="px-4 py-2 border-b border-edge flex items-center gap-3">
      <button className="border border-edge rounded px-3 py-1 text-sm w-20" onClick={() => setPlaying(!playing)}>
        {playing ? "Pause" : "Play"}
      </button>
      <select className="bg-ink border border-edge rounded px-1 py-1 text-xs num" value={speed}
        onChange={(e) => setSpeed(Number(e.target.value))}>
        {[1, 4, 12, 48].map((x) => <option key={x} value={x}>{x}×</option>)}
      </select>
      <div className="flex-1 relative">
        <div className="flex h-2 rounded overflow-hidden mb-1">
          {tl?.level.map((l, k) => <div key={k} style={{ flex: 1, background: color[l] }} />)}
        </div>
        <input type="range" min={0} max={n - 1} value={i} className="w-full accent-cyan-400"
          onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }} />
      </div>
      <button className="border border-edge rounded px-2 py-1 text-xs" onClick={() => setI(n - 1)}>Latest</button>
    </div>
  );
}

// ---------------------------------------------------------------- 2. gauges
function Tile({ label, value, unit, sub, b }) {
  return (
    <div className={`bg-ink border rounded p-2 ${b ? BORDER[b] : "border-edge"} ${b && b !== "ok" ? "border-2" : ""}`}>
      <div className={`text-[10px] uppercase tracking-widest ${b && b !== "ok" ? TEXT[b] : "text-dim"}`}>{label}</div>
      <div className={`num text-2xl ${b && b !== "ok" ? TEXT[b] : ""}`}>{value}<span className="text-sm text-dim ml-1">{unit}</span></div>
      {sub && <div className="num text-[11px] text-dim">{sub}</div>}
    </div>
  );
}

function Gauges({ s }) {
  const r = s.readings;
  const sc = Object.fromEntries(s.health.components.map((c) => [c.key, band(c.score)]));
  const res = Math.abs(r.residual_k ?? 0);
  return (
    <Panel title="Live readings">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Tile label="Top oil" value={fmt(r.oil_c)} unit="°C" sub={`twin ${fmt(r.oil_pred_c)} °C`} />
        <Tile label="Hot-spot" value={fmt(r.hotspot_c)} unit="°C" sub="measured oil + winding gradient" b={sc.ageing} />
        <Tile label="Ambient" value={fmt(r.ambient_c)} unit="°C" sub={`oil rise ${fmt(r.rise_k)} K`} />
        <Tile label="Residual" value={fmt(r.residual_k, 2)} unit="K" sub="measured − twin" b={res > 4 ? "alarm" : res > 2 ? "warn" : null} />
        <Tile label="Load" value={fmt(r.load_pct, 0)} unit="%" sub={`worst phase ${fmt(r.load_worst_phase_pct, 0)} %`} b={sc.loading} />
        <Tile label="Current unbalance" value={fmt(r.current_unbalance_pct)} unit="%" b={sc.unbalance}
          sub={`VUF ${fmt(r.voltage_unbalance_pct, 2)} %`} />
        <Tile label="Ageing factor" value={fmt(r.faa, 2)} unit="pu" sub={`last 24 h: ${fmt(s.ageing_24h.equivalent_hours, 1)} h · ₹${fmt(s.ageing_24h.life_cost_inr, 0)}`} b={sc.ageing} />
        <Tile label="Rise fit" value={fmt(r.slope, 3)} unit="slope" sub={`intercept ${fmt(r.intercept_k, 2)} K`} b={sc.cooling} />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 num text-xs">
        <div className="text-dim">I&nbsp; {["IR", "IY", "IB"].map((c) => `${c} ${fmt(r.currents_a[c])}`).join("  ")} A</div>
        <div className="text-dim">V&nbsp; {["VR", "VY", "VB"].map((c) => `${c} ${fmt(r.voltages_v[c])}`).join("  ")} V</div>
      </div>
      <div className="mt-2 text-xs">
        {untrusted(s).length === 0
          ? <span className="text-ok">All 8 channels trusted</span>
          : <ChannelGrid s={s} />}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 3. trends
function Trends({ series, prewarmUntil }) {
  if (!series) return <Panel title="Trends">loading…</Panel>;
  // live: the last data point inside the prewarm history, as an x-axis category
  const preIdx = prewarmUntil ? series.t.filter((t) => t <= prewarmUntil).length - 1 : -1;
  const pre = preIdx >= 0 ? hhmm(series.t[preIdx]) : null;
  const data = series.t.map((t, k) => ({
    t: hhmm(t), oil: series.oil_c[k], twin: series.pred_oil_c[k], hs: series.hotspot_c[k],
    amb: series.ambient_c[k], res: series.residual_k[k], load: series.K_oil[k] && 100 * series.K_oil[k],
  }));
  return (
    <Panel title="Trends · last 48 h">
      <ResponsiveContainer width="100%" height={210}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="#1f2a37" />
          <XAxis dataKey="t" tick={axis} minTickGap={40} />
          <YAxis tick={axis} domain={["auto", "auto"]} />
          <Tooltip {...tip} />
          {pre && <ReferenceArea x1={data[0].t} x2={pre} fill="#7d8a9a" fillOpacity={0.12}
            label={{ value: "prewarm history", fill: "#7d8a9a", fontSize: 10, position: "insideTopLeft" }} />}
          <ReferenceLine y={series.trip_c} stroke="#7d8a9a" strokeDasharray="4 4"
            label={{ value: `fixed trip ${series.trip_c} °C`, fill: "#7d8a9a", fontSize: 10, position: "insideTopLeft" }} />
          <Line dataKey="oil" name="oil measured" stroke="#22d3ee" dot={false} strokeWidth={1.6} isAnimationActive={false} />
          <Line dataKey="twin" name="oil twin" stroke="#d7dee8" dot={false} strokeDasharray="3 3" isAnimationActive={false} />
          <Line dataKey="hs" name="hot-spot" stroke="#94a3b8" dot={false} strokeWidth={1} isAnimationActive={false} />
          <Line dataKey="amb" name="ambient" stroke="#475569" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      <div className="grid grid-cols-2 gap-2">
        {[["res", "residual K (measured − twin)", [-4, 8]], ["load", "load %", [0, "auto"]]].map(([k, name, dom]) => (
          <ResponsiveContainer key={k} width="100%" height={110}>
            <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="#1f2a37" />
              <XAxis dataKey="t" tick={axis} minTickGap={60} />
              <YAxis tick={axis} domain={dom} />
              <Tooltip {...tip} />
              {k === "res" && <ReferenceLine y={0} stroke="#475569" />}
              {k === "load" && <ReferenceLine y={100} stroke="#7d8a9a" strokeDasharray="4 4" />}
              <Line dataKey={k} name={name} stroke="#22d3ee" dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ))}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 4. health
function Health({ h, lvl }) {
  const worst = [...h.components].sort((a, b) => (a.score ?? 0) - (b.score ?? 0))[0];
  const wb = band(worst.score) ?? "alarm";
  const ib = ["alarm", "warn", "ok"].find((x) => x === band(h.index) || x === lvl);
  const summary = (
    <span className="num">
      <b className={ib === "ok" ? "" : TEXT[ib]}>{fmt(h.index, 0)}/100</b>
      {wb !== "ok" && <span className={TEXT[wb]}> · {worst.label} {fmt(worst.score, 0)}</span>}
    </span>
  );
  return (
    <Fold title="Health breakdown" summary={summary}>
      <div className="space-y-2">
        {h.components.map((c) => {
          const b = band(c.score) ?? "alarm";
          return (
            <div key={c.key}>
              <div className="flex justify-between text-xs">
                <span className={b === "ok" ? "" : TEXT[b]}>{c.label} <span className="text-dim">· w {c.weight}</span></span>
                <span className="num">{fmt(c.value, 3)} {c.unit} → <b className={TEXT[b]}>{fmt(c.score, 0)}</b>
                  <span className="text-dim"> (100 ≤{c.good} · 0 at {fmt(c.bad, 3)})</span></span>
              </div>
              <div className={`h-2 rounded ${b === "ok" ? "bg-ink" : b === "warn" ? "bg-warn/20" : "bg-alarm/20"}`}>
                <div className={`h-2 rounded ${BG[b]}`} style={{ width: `${Math.max(c.score ?? 0, 4)}%` }} />
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-3 text-[11px] text-dim">
        Each score is the quantity its alarm fires on: 0 = that alarm's threshold, 100 = healthy
        behaviour. Linear between (ageing on log₂). Index = weighted mean.
      </div>
    </Fold>
  );
}

// ---------------------------------------------------------------- 5. alerts
function Alerts({ groups }) {
  const [open, setOpen] = useState({});
  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  return (
    <Panel title="Alert feed · repeats grouped" className="overflow-auto max-h-[420px]">
      {groups.length === 0 && <div className="text-dim text-sm">No alerts.</div>}
      <ul className="space-y-2">
        {groups.map((g) => {
          const k = g.kind + g.what;
          return (
            <li key={k} className={`border-l-2 pl-2 ${LEVEL[g.level]} ${g.active ? "" : "opacity-50"}`}>
              <button className="w-full flex justify-between text-sm text-left" onClick={() => toggle(k)}>
                <span className="font-semibold">
                  {g.count > 1 ? (open[k] ? "▾ " : "▸ ") : ""}{g.what}
                  {g.count > 1 && <span className="num font-normal text-dim"> ×{g.count}</span>}
                </span>
                <span className="num text-[11px] text-dim">
                  {hhmm(g.first)}{g.count > 1 || !g.active ? ` → ${g.active ? "now" : hhmm(g.last)}` : ""}{g.active ? " · active" : ""}
                </span>
              </button>
              <div className="text-[11px] uppercase tracking-wider text-dim">{g.family}</div>
              <div className="text-xs text-fg/80"><b className="text-dim">Why </b>{g.why}</div>
              <div className="text-xs text-fg/80"><b className="text-dim">Do </b>{g.action}</div>
              {open[k] && (
                <ul className="num text-[11px] text-dim mt-1 ml-3">
                  {g.episodes.map((e, j) => (
                    <li key={j}>{hhmm(e.start)} → {e.active ? <span className={TEXT[g.level === "alarm" ? "alarm" : "warn"]}>active</span> : hhmm(e.end)}</li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

// The existing device's fixed oil trip, shown for contrast. Deliberately not an
// alert: it does not count toward status, the alert total or the health index.
function Comparison({ s }) {
  const f = s.countdown.fixed, ref = s.reference;
  const [table, setTable] = useState(false);
  return (
    <Panel title="Comparison · existing device (not an alert)">
      <div className="text-sm">
        Fixed {f.limit_c} °C oil trip:{" "}
        <span className="num">{ref.fixed_trip_active ? `would be tripped since ${hhmm(ref.since)}` : ttt(f) === "none" ? "not reached at this load" : `would trip in ${ttt(f)}`}</span>
      </div>
      <div className="num text-[11px] text-dim mt-1">
        oil {fmt(f.now_c)} °C · ambient {fmt(s.countdown.ambient_c)} °C leaves {fmt(f.limit_c - s.countdown.ambient_c)} K of rise ·
        {" "}{ref.trip_episodes_24h} trip episode{ref.trip_episodes_24h === 1 ? "" : "s"} in the last 24 h
      </div>
      <div className="text-[11px] text-dim mt-1">
        Twin verdict at the same instant: <span className={LEVEL[s.status.level].split(" ")[0]}>
        {s.alerts_live.length ? s.alerts_live.map((a) => a.what).join("; ") : "no fault"}</span>.
        An absolute oil threshold carries the ambient inside it; the twin judges rise over ambient.
      </div>
      <button className="text-[11px] text-accent mt-2" onClick={() => setTable(!table)}>
        {table ? "▾ hide" : "▸ show"} protection-setting comparison by ambient
      </button>
      {table && <AmbientTable s={s} />}
    </Panel>
  );
}

// ---------------------------------------------------------------- 6. recommendations
function Recommendations({ s }) {
  return (
    <Panel title="Recommendations">
      <ol className="space-y-2">
        {s.recommendations.map((r, k) => (
          <li key={k} className="text-sm">
            <span className={`num mr-2 ${r.priority === 1 ? "text-alarm" : r.priority === 2 ? "text-warn" : "text-ok"}`}>P{r.priority}</span>
            {r.text}
            <div className="text-[11px] text-dim ml-7">{r.because}</div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

// ---------------------------------------------------------------- app
export default function App() {
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
  const [view, setView] = useState("dashboard");
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
    if (!playing || !s) return;
    const id = setInterval(() => setI((x) => Math.min(x + speed, s.n - 1)), 400);
    return () => clearInterval(id);
  }, [playing, speed, s]);

  if (mode === "live" && !live.s) {
    return (
      <div className="min-h-screen">
        <div className="px-4 py-3 border-b border-edge flex items-center gap-4">
          <div className="text-lg font-semibold">Live</div>
          <div className="ml-auto"><ModeSwitch mode={mode} setMode={setMode} /></div>
        </div>
        <LiveBar status={live.status} />
        <div className="p-6 text-dim text-sm">
          {live.status?.has_data === false
            ? <>No live data yet. Start the stream in a terminal: <span className="num text-fg">python -m dtwin.live --speed 600</span></>
            : live.err || "Waiting for the live stream…"}
        </div>
      </div>
    );
  }
  if (err && !s) return <div className="p-6 text-alarm num">Backend unreachable: {err}. Start it with: uvicorn dtwin.api.app:app --port 8000</div>;
  if (!s) return <div className="p-6 text-dim">Loading twin…</div>;
  return <Board s={mode === "live" ? live.s : s} series={mode === "live" ? live.series : series}
    scen={mode === "live" ? "live" : scenario} mode={mode} setMode={setMode} live={live} view={view} setView={setView}
    scenarios={scenarios} scenario={scenario} setScenario={setScenario} kva={kva} setKva={setKva}
    recorded={recorded} tl={tl} i={i} setI={setI} playing={playing} setPlaying={setPlaying}
    speed={speed} setSpeed={setSpeed} />;
}

function Board({ s, series, scen, mode, setMode, live, view, setView, scenarios, scenario, setScenario, kva, setKva,
  recorded, tl, i, setI, playing, setPlaying, speed, setSpeed }) {
  const stale = mode === "live" && Boolean(live.status?.stale);
  const [simAt, setSimAt] = useState(null);       // baseline cursor frozen when Simulate opens
  if (view === "twin" && simAt !== null) {
    return <SimulateWorkspace scen={scen} cursor={simAt} onExit={() => setSimAt(null)} />;
  }
  if (view === "twin") {
    return (
      <TwinPage scen={scen} cursor={s.i} version={`${s.timestamp}|${live.status?.stale}`}
        simulateButton={<button className="twin-btn twin-btn-primary" onClick={() => { setPlaying(false); setSimAt(s.i); }}>
          Simulate</button>}
        header={<TwinBar s={s} view={view} setView={setView} mode={mode} setMode={setMode} live={live}
          tl={tl} i={i} setI={setI} playing={playing} setPlaying={setPlaying} />} />
    );
  }
  return (
    <div className="min-h-screen">
      <Header s={s} scenarios={scenarios} scenario={scenario} setScenario={setScenario}
        kva={kva} setKva={setKva} recorded={recorded ?? s.nameplate.rating_kva} mode={mode} setMode={setMode}
        stale={stale} view={view} setView={setView} />
      {s.data_source.warnings.length > 0 && (
        <div className="px-4 py-1.5 border-b border-warn bg-warn/10 text-xs text-warn">
          <b>Data source:</b> {s.data_source.warnings.join(" · ")}
          <span className="text-dim"> — see the mapping report: python -m dtwin.ingest data/{scenario}.csv</span>
        </div>
      )}
      {mode === "live" ? <LiveBar status={live.status} /> : (
        <Replay tl={tl} i={i ?? 0} n={s.n} setI={setI} playing={playing} setPlaying={setPlaying}
          speed={speed} setSpeed={setSpeed} />
      )}
      {stale && (
        <div className="px-4 py-1.5 border-b border-edge bg-edge/40 text-xs text-dim">
          Everything below is the last known state and is greyed out: {live.status.reason}.
        </div>
      )}
      <main className={`p-3 grid gap-3 lg:grid-cols-3 ${stale ? "grayscale opacity-50" : ""}`}>
        <div className="lg:col-span-2 space-y-3">
          <Countdown s={s} />
          <Gauges s={s} />
          <Trends series={series} prewarmUntil={mode === "live" ? live.status?.prewarm_until : null} />
          <Cooling s={s} series={series} />
        </div>
        <div className="space-y-3">
          {mode === "live" && <InjectPanel />}
          <Recommendations s={s} />
          <Health h={s.health} lvl={s.status.level} />
          <Life s={s} />
        </div>
        <div className="lg:col-span-2 space-y-3">
          <Alerts groups={s.alert_groups} />
          <Notifications scenario={scen} kva={mode === "live" ? null : kva} i={mode === "live" ? s.timestamp : s.i} />
        </div>
        <div>
          <Comparison s={s} />
        </div>
        <div className="lg:col-span-3">
          <WhatIf scenario={scen} kva={mode === "live" ? null : kva} i={s.i} s={s} />
        </div>
        <div className="lg:col-span-2"><Gprs s={s} /></div>
        <div><SelfTest kva={kva ?? s.nameplate.rating_kva} /></div>
      </main>
    </div>
  );
}
