import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  api, axis, Card, duration, fmt, Fold, grid, Help, hrs, Icon, inr, PHASE, PRIORITY, StatusPill, Tabs, Term, tip, when,
} from "../ui.jsx";
import { HELP } from "../help.js";
import { capNote, headlineFor, overallStatus, suspectSet } from "../plain.js";
import { ModeSwitch, InjectPanel, SPEEDS } from "../live.jsx";
import { NameplateSelect, AmbientTable } from "../features.jsx";
import { Inspector, useComponents, useFem, useRecommendations } from "../twin/TwinPage.jsx";
import { FOCUS } from "../twin/focus.js";
import { webglAvailable } from "../twin/states.js";
import Schematic from "../twin/Schematic.jsx";
import HeatMapView from "../twin/HeatMap.jsx";

const Scene = lazy(() => import("../twin/Scene.jsx"));
const TONE_TEXT = { ok: "text-ok", warn: "text-warn", alarm: "text-alarm", sensor: "text-sensor", info: "text-dim" };
const TONE_BORDER = { ok: "border-ok", warn: "border-warn", alarm: "border-alarm", sensor: "border-sensor", info: "border-edge" };

// ======================================================================= top bar
export function NavTabs({ view, setView }) {
  return (
    <nav className="flex gap-1" aria-label="Pages">
      {[["overview", "Overview"], ["engineering", "Engineering"]].map(([k, label]) => (
        <button key={k} onClick={() => setView(k)} aria-current={view === k ? "page" : undefined}
          className={`px-3 py-1.5 rounded-lg text-[15px] ${view === k ? "bg-ink text-fg font-semibold" : "text-dim hover:text-fg"}`}>
          {label}
        </button>
      ))}
    </nav>
  );
}

function Settings({ ctx }) {
  const [open, setOpen] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const { mode, scenario, scenarios, setScenario, kva, setKva, recorded, theme, setTheme, s } = ctx;
  const report = mode === "live" ? "/api/live/report.pdf" : `/api/${scenario}/report.pdf?t=${s.i}${kva ? `&kva=${kva}` : ""}`;
  return (
    <div ref={ref} className="relative">
      <button className="twin-btn flex items-center gap-1.5" aria-label="Settings" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="gear" className="w-5 h-5" /><span className="hidden xl:inline">Settings</span>
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-50 card p-5 w-80 space-y-4 text-sm">
          {mode === "replay" && (
            <label className="block">
              <span className="text-dim">Data file</span>
              <select aria-label="Data file" className="mt-1 w-full border border-edge rounded-lg px-2 py-1.5" value={scenario}
                onChange={(e) => setScenario(e.target.value)}>
                {scenarios.map((n) => <option key={n}>{n}</option>)}
              </select>
            </label>
          )}
          {mode === "replay" && <NameplateSelect kva={kva} setKva={setKva} rated={recorded} />}
          <div>
            <span className="text-dim">Theme</span>
            <div className="mt-1 flex gap-2">
              {["light", "dark"].map((t) => (
                <button key={t} className={`twin-btn flex-1 ${theme === t ? "twin-btn-primary" : ""}`} onClick={() => setTheme(t)}>
                  {t === "light" ? "Light" : "Dark"}
                </button>
              ))}
            </div>
          </div>
          <a className="twin-btn block text-center" href={report} download>Download condition report (PDF)</a>
        </div>
      )}
    </div>
  );
}

export function TopBar({ ctx, view, setView }) {
  const { s, mode, setMode, live, stale } = ctx;
  const st = overallStatus(s, stale);
  const ls = live?.status;
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3 bg-panel border-b border-edge">
      <div className="min-w-0">
        <div className="text-lg font-semibold leading-tight">Transformer {s.nameplate.tag}</div>
        <div className="text-sm text-dim">{s.nameplate.rating_kva} kVA, {s.nameplate.hv_kv} kV / {s.nameplate.lv_v} V, {s.nameplate.cooling}</div>
      </div>
      <NavTabs view={view} setView={setView} />
      <StatusPill big level={st.level} text={st.text} />
      <div className="ml-auto flex items-center gap-4">
        {mode === "live" && (
          <span className={`text-sm font-semibold px-3 py-1 rounded-full border ${ls?.stale ? "border-edge text-dim" : "border-fg text-fg"}`}
            title="Where the live samples come from">{ls?.source_label || "No source"}</span>
        )}
        <ModeSwitch mode={mode} setMode={setMode} />
        <div className="text-right leading-tight">
          <div className="text-[15px] font-semibold">{when(s.timestamp)}</div>
          <div className="text-sm text-dim">{mode === "live" ? `sample ${fmt(live?.link?.age ?? ls?.sample_age_s, 0)} s old` : "replay time"}</div>
        </div>
        <Settings ctx={ctx} />
      </div>
    </header>
  );
}

// ======================================================================= time bar
const BAND = { alarm: "var(--color-alarm)", warn: "var(--color-warn)", sensor: "var(--color-sensor)" };
const bandOf = (e) => (e.family === "sensor" ? "sensor" : e.level === "alarm" ? "alarm" : "warn");

function Timeline({ tl, n, i, setI }) {
  const [hover, setHover] = useState(null);
  if (!tl?.episodes) return <div className="h-4" />;
  // one lane per family of band so overlapping episodes stay visible
  const lanes = ["alarm", "warn", "sensor"];
  return (
    <div className="relative">
      <div className="relative h-[18px] rounded bg-ink border border-edge overflow-hidden" onMouseLeave={() => setHover(null)}>
        {tl.episodes.map((e, k) => {
          const b = bandOf(e);
          const lane = lanes.indexOf(b);
          return (
            <button key={k} aria-label={`${e.what}, ${when(e.start)} to ${when(e.end)}`}
              onMouseEnter={() => setHover({ e, k })} onFocus={() => setHover({ e, k })} onClick={() => setI(e.i0)}
              className="absolute"
              style={{ left: `${(100 * e.i0) / n}%`, width: `max(3px, ${(100 * (e.i1 - e.i0 + 1)) / n}%)`,
                top: `${lane * 6}px`, height: 6, background: BAND[b] }} />
          );
        })}
        <div className="absolute top-0 bottom-0 w-0.5 bg-fg" style={{ left: `${(100 * i) / Math.max(n - 1, 1)}%` }} />
      </div>
      {hover && (
        <div className="absolute z-40 -top-12 card px-3 py-1.5 text-sm whitespace-nowrap pointer-events-none"
          style={{ left: `min(max(0px, calc(${(100 * hover.e.i0) / n}% - 60px)), calc(100% - 320px))` }}>
          <b>{hover.e.what}</b> <span className="text-dim">{when(hover.e.start)} to {when(hover.e.end)}</span>
        </div>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-4 text-sm text-dim whitespace-nowrap">
      {[["alarm", "Alarm"], ["warn", "Warning"], ["sensor", "Sensor fault"]].map(([k, l]) => (
        <span key={k} className="flex items-center gap-1.5"><span className="inline-block w-4 h-2 rounded-sm" style={{ background: BAND[k] }} />{l}</span>
      ))}
    </div>
  );
}

export function TimeBar({ ctx }) {
  const { s, mode, tl, i, setI, playing, setPlaying, speed, setSpeed, live } = ctx;
  const [inject, setInject] = useState(false);
  if (mode === "live") {
    const st = live?.status || {};
    const control = (body) => fetch("/api/live/control", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return (
      <div className="px-6 py-3 bg-panel border-b border-edge flex flex-wrap items-center gap-4 text-sm">
        <span className="font-semibold">Live stream</span>
        <span className="text-dim">new sample every {fmt(st.period_s, 1)} s</span>
        <label className="flex items-center gap-2 text-dim">Speed
          <select className="border border-edge rounded-lg px-2 py-1 text-fg" value={st.speed || 120}
            onChange={(e) => control({ speed: Number(e.target.value) })}>
            {SPEEDS.map((x) => <option key={x} value={x}>{x}× real time</option>)}
          </select>
        </label>
        <button className="twin-btn" onClick={() => control({ paused: !st.paused })}>{st.paused ? "Resume" : "Pause"}</button>
        {st.has_data && st.history?.rebuilding && (
          <span className="text-warn" title="The 24 h cooling fit needs history; it fills in as samples arrive.">
            History rebuilding, {st.history.n} of {st.history.need ?? 192} samples</span>
        )}
        {st.stale && !live?.link?.disconnected && <span className="text-dim">{st.reason}</span>}
        <div className="relative ml-auto">
          <button className="twin-btn" onClick={() => setInject(!inject)} aria-expanded={inject}>Inject a fault</button>
          {inject && <div className="absolute right-0 top-11 z-50 w-[36rem]"><InjectPanel /></div>}
        </div>
      </div>
    );
  }
  return (
    <div className="px-6 py-3 bg-panel border-b border-edge">
      <div className="flex items-center gap-4">
        <button className="twin-btn w-20" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
        <select aria-label="Replay speed" className="border border-edge rounded-lg px-2 py-1.5 text-sm" value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}>
          {[1, 4, 12, 48].map((x) => <option key={x} value={x}>{x}× speed</option>)}
        </select>
        <div className="flex-1 min-w-0">
          <Timeline tl={tl} n={s.n} i={i ?? 0} setI={(x) => { setPlaying(false); setI(x); }} />
          <input type="range" aria-label="Replay position" min={0} max={s.n - 1} value={i ?? 0} className="w-full mt-1"
            onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }} />
        </div>
        <Legend />
        <button className="twin-btn" onClick={() => setI(s.n - 1)}>Latest</button>
        <Help text={HELP.timeline} title="the timeline" />
      </div>
    </div>
  );
}

// ======================================================================= 3D twin
function TwinPanel({ ctx, onSimulate }) {
  const { scenKey: scen, s, theme } = ctx;
  const cursor = s.i;
  const version = `${s.timestamp}|${ctx.live?.status?.stale}`;
  const data = useComponents(scen, cursor, version);
  const recs = useRecommendations(scen, cursor, version);
  const [fem, resolveFem] = useFem(scen, cursor, version);
  const [sel, setSel] = useState(null);
  const [heat, setHeat] = useState(false);
  const [xray, setXray] = useState(true);
  const [resetKey, setResetKey] = useState(0);
  const gl = useMemo(() => webglAvailable(), []);
  const nf = data?.newest_fault;
  const fly = nf && FOCUS[nf.component] ? [...FOCUS[nf.component]] : null;
  const comps = data ? Object.fromEntries(data.components.map((c) => [c.id, c])) : {};
  const fc = nf ? comps[nf.component] : null;
  const callout = nf && fc ? {
    id: nf.component, text: nf.family === "sensor" ? `${fc.name}: instrument fault` : `${fc.name}: ${nf.what}`,
    tone: nf.family === "sensor" ? "sensor" : fc.state === "red" ? "alarm" : "warn",
  } : null;
  return (
    <Card pad="p-0" className="relative overflow-hidden h-full min-h-[460px] flex flex-col">
      <div className="absolute top-4 left-4 right-4 z-10 flex items-center gap-2">
        <span className="text-base font-semibold bg-panel/90 rounded-lg px-2 py-1">Digital twin</span>
        <button className="twin-btn twin-btn-primary" onClick={onSimulate}>Simulate</button>
        <button className="twin-btn" onClick={() => setHeat(!heat)} aria-pressed={heat}>{heat ? "3D model" : "Heat map"}</button>
        {!heat && gl && <button className="twin-btn" onClick={() => setXray(!xray)}>{xray ? "Solid tank" : "See inside"}</button>}
        {!heat && gl && <button className="twin-btn" onClick={() => setResetKey((k) => k + 1)}>Reset view</button>}
        <span className="ml-auto"><Help text={HELP.twin} title="the digital twin" /></span>
      </div>
      <div className="flex-1 relative min-h-0">
        {!data ? <div className="p-6 text-dim">Loading the twin…</div> : heat ? (
          <div className="absolute inset-0 overflow-auto"><HeatMapView fem={fem} live={scen === "live"} onResolve={resolveFem} /></div>
        ) : gl ? (
          <div className="absolute inset-0">
            <Suspense fallback={<div className="p-6 text-dim">Loading 3D…</div>}>
              <Scene comps={comps} scale={data.scale} sel={sel} setSel={setSel} xray={xray} flyTo={fly}
                resetKey={resetKey} fem={fem} callout={callout} theme={theme} />
            </Suspense>
          </div>
        ) : <Schematic comps={comps} scale={data.scale} sel={sel} setSel={setSel} />}
        {!heat && sel && comps[sel] && (
          <div className="absolute top-16 right-4 bottom-4 w-[24rem] card overflow-y-auto z-20">
            <div className="flex justify-end p-2"><button className="twin-btn" onClick={() => setSel(null)}>Close</button></div>
            <Inspector c={comps[sel]} rec={recs[sel]} />
          </div>
        )}
        {!heat && data && (
          <div className="absolute bottom-3 left-4 flex flex-wrap gap-x-4 gap-y-1 text-sm text-dim bg-panel/85 rounded-lg px-3 py-1.5 z-10">
            {[["var(--color-ok)", "Normal"], ["var(--color-warn)", "Warning"], ["var(--color-alarm)", "Alarm"],
              ["var(--color-sensor)", "Instrument fault"], ["#a3aaa6", "Not monitored"]].map(([c, l]) => (
              <span key={l} className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full" style={{ background: c }} />{l}</span>
            ))}
            <span>Click a part for details</span>
          </div>
        )}
      </div>
    </Card>
  );
}

// ======================================================================= what's happening
function Whats({ s, stale }) {
  const st = overallStatus(s, stale);
  const h = headlineFor(s);
  const acts = s.recommendations.filter((r) => r.family !== "reference" && !(r.priority === 3 && st.level !== "ok")).slice(0, 2);
  const beyond = s.physical_limit?.beyond;
  const hs = s.countdown.hotspot;
  const evidence = h.alert ? `Detected ${when(h.alert.start)}; ${h.evidence ?? h.alert.why}`
    : s.recommendations.find((r) => r.priority === 3)?.because;
  const more = s.alerts_live.length - 1;
  const calm = st.level === "ok";
  return (
    <Card title="What's happening" help={HELP.whats} className={`border-l-4 ${TONE_BORDER[st.level]}`}>
      {beyond && (
        <p className="mb-3 px-3 py-2 rounded-lg border-2 border-alarm text-alarm font-semibold flex items-center gap-2">
          <Icon name="alarm" className="w-5 h-5 shrink-0" />{s.physical_limit.message}
        </p>
      )}
      <div className="flex gap-3">
        <span className={`mt-1 ${TONE_TEXT[st.level]}`}><Icon name={st.level === "info" ? "info" : st.level} className="w-7 h-7" /></span>
        <div className="min-w-0">
          <p className={`text-xl font-semibold leading-snug ${calm ? "text-ok" : ""}`}>{h.text}</p>
          <p className="text-[15px] text-dim mt-1">{h.meaning}</p>
        </div>
      </div>
      {!calm && acts.length > 0 && (
        <div className="mt-4">
          <div className="text-sm text-dim mb-1.5">What to do</div>
          <ul className="space-y-2">
            {acts.map((r, k) => {
              const p = PRIORITY[r.priority] || PRIORITY[3];
              return (
                <li key={k} className="flex gap-3 items-start">
                  <span className={`shrink-0 text-sm font-semibold px-2 py-0.5 rounded-md border ${TONE_BORDER[p.tone]} ${TONE_TEXT[p.tone]}`}>{p.label}</span>
                  <span className="text-[15px]">{r.text}
                    {r.detail && <span className="block text-dim">{r.detail}</span>}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {(hs.already_above || hs.minutes !== null) && (calm || !acts.some((r) => r.detail)) && (
        <p className="mt-3 text-[15px]">
          <b className={hs.already_above ? "text-alarm" : "text-warn"}>
            {hs.already_above ? `Hot-spot is above ${hs.limit_c} °C now` : `Hot-spot reaches ${hs.limit_c} °C in ${duration(hs.minutes)}`}
          </b>{hs.shed_pct > 0 ? `; shedding ${fmt(hs.shed_pct, 0)} % of the load keeps it under.` : "."}
        </p>
      )}
      {evidence && (
        <p className="mt-3 text-sm text-dim"><span className="font-semibold text-fg">How sure: </span>{evidence}</p>
      )}
      {more > 0 && <p className="mt-2 text-sm text-dim">Also active: {s.alerts_live.slice(1).map((a) => a.what).join("; ")}.</p>}
    </Card>
  );
}

// ======================================================================= key numbers
function LimitBar({ value, lim, inverse = false }) {
  const { min, max } = lim;
  const pos = (v) => `${Math.max(0, Math.min(100, (100 * (v - min)) / (max - min)))}%`;
  const w = inverse ? lim.warn_below : lim.warn, a = inverse ? lim.alarm_below : lim.alarm;
  const zones = inverse
    ? [[min, a, "alarm"], [a, w, "warn"], [w, max, "ok"]]
    : [[min, w, "ok"], [w, a, "warn"], [a, max, "alarm"]];
  const fill = { ok: "var(--color-edge)", warn: "color-mix(in srgb, var(--color-warn) 30%, transparent)",
    alarm: "color-mix(in srgb, var(--color-alarm) 30%, transparent)" };
  return (
    <div className="mt-3">
      <div className="relative h-2.5 rounded-full overflow-hidden flex">
        {zones.map(([x0, x1, k]) => <div key={k} style={{ width: `${(100 * (x1 - x0)) / (max - min)}%`, background: fill[k] }} />)}
      </div>
      <div className="relative h-0">
        {value !== null && value !== undefined && (
          <div className="absolute -top-[15px] w-1 h-[20px] rounded bg-fg -translate-x-1/2" style={{ left: pos(value) }} />
        )}
      </div>
      <div className="relative h-5 text-sm text-dim mt-1">
        {/* lower limit label ends at its tick, upper one starts at its tick, so close limits never collide */}
        <span className="absolute -translate-x-full pr-1" style={{ left: pos(Math.min(w, a)) }}>{Math.min(w, a)}</span>
        <span className="absolute pl-1" style={{ left: pos(Math.max(w, a)) }}>{Math.max(w, a)}</span>
      </div>
    </div>
  );
}

function KeyNumber({ label, tech, value, unit, d = 0, lim, inverse, help, suspect, caption, note }) {
  const v = value === null || value === undefined ? null : Number(value);
  const lv = v === null ? null : inverse
    ? (v < lim.alarm_below ? "alarm" : v < lim.warn_below ? "warn" : "ok")
    : (v >= lim.alarm ? "alarm" : v >= lim.warn ? "warn" : "ok");
  const word = suspect ? "Suspect reading" : { ok: "Normal", warn: "Warning", alarm: "Alarm" }[lv] || "No data";
  const tone = suspect ? "sensor" : lv || "info";
  return (
    <Card pad="p-5" className={lv && lv !== "ok" && !suspect ? `border-2 ${TONE_BORDER[lv]}` : suspect ? "border-2 border-sensor" : ""}>
      <div className="flex items-start justify-between gap-2">
        <Term tech={tech}><span className="text-[15px] text-dim">{label}</span></Term>
        <Help text={help} title={label} />
      </div>
      <div className={`mt-1 flex items-baseline gap-1 ${suspect ? "text-dim" : ""}`}>
        <span className="text-[34px] leading-none font-semibold tracking-tight">{fmt(v, d)}</span>
        <span className="text-base text-dim">{unit}</span>
        {caption && <span className="ml-auto text-sm text-dim">{caption}</span>}
      </div>
      <div className={`mt-1.5 text-sm font-semibold flex items-center gap-1.5 ${TONE_TEXT[tone]}`}>
        <Icon name={tone === "info" ? "info" : tone} className="w-4 h-4" />{note || word}
      </div>
      <LimitBar value={v} lim={lim} inverse={inverse} />
    </Card>
  );
}

function KeyNumbers({ s }) {
  const L = s.display_limits;
  const r = s.readings;
  const sus = suspectSet(s);
  const oilSus = sus.has("oil_temp") || s.status.thermal_status === "sensor_temp_offset";
  const curSus = ["IR", "IY", "IB"].some((c) => sus.has(c));
  return (
    <div className="grid grid-cols-2 gap-4">
      <KeyNumber label="Hot-spot temperature" tech="Winding hot-spot, IEEE C57.91" value={r.hotspot_c} unit="°C" d={0}
        lim={L.hotspot_c} help={HELP.hotspot(L.hotspot_c)} suspect={oilSus} caption={`limit ${L.hotspot_c.limit} °C`}
        note={s.physical_limit?.beyond && !oilSus ? `Above the ${s.physical_limit.emergency_hotspot_c} °C limit` : null} />
      <KeyNumber label="Load" tech="Load ratio K, RMS of three phases" value={r.load_pct} unit="% of rating" lim={L.load_pct}
        help={HELP.load(L.load_pct, s.nameplate.rated_current_a)} suspect={curSus} />
      <KeyNumber label="Top-oil temperature" tech="Top oil, measured by the oil probe" value={r.oil_c} unit="°C" d={0}
        lim={L.top_oil_c} help={HELP.topoil(L.top_oil_c)} suspect={oilSus} />
      <KeyNumber label="Health index" tech="Weighted health index, 0-100" value={s.health.index} unit="of 100" lim={L.health} inverse
        help={HELP.health(s.health.components, L.health)} />
    </div>
  );
}

// ======================================================================= trends
function Trends({ series, limits, prewarmUntil }) {
  const [tab, setTab] = useState("temp");
  if (!series) return <Card title="Trends">Loading…</Card>;
  const preIdx = prewarmUntil ? series.t.filter((t) => t <= prewarmUntil).length - 1 : -1;
  const rated = series.rated_current_a;
  const data = series.t.map((t, k) => ({
    t: when(t), oil: series.oil_c[k], hs: series.hotspot_c[k], amb: series.ambient_c[k],
    R: series.IR?.[k] != null ? (100 * series.IR[k]) / rated : null,
    Y: series.IY?.[k] != null ? (100 * series.IY[k]) / rated : null,
    B: series.IB?.[k] != null ? (100 * series.IB[k]) / rated : null,
    VR: series.VR?.[k], VY: series.VY?.[k], VB: series.VB?.[k],
  }));
  const pre = preIdx >= 0 ? data[preIdx].t : null;
  const V = limits.voltage_v;
  const views = {
    temp: { unit: "°C", lines: [["hs", "Hot-spot", "var(--chart-1)", 2.2], ["oil", "Top oil (measured)", "var(--chart-2)", 1.8],
      ["amb", "Ambient", "var(--chart-3)", 1.6, "5 4"]],
    refs: [[limits.hotspot_c.limit, `Hot-spot limit ${limits.hotspot_c.limit} °C`, "var(--color-alarm)"]] },
    load: { unit: "% of rating", lines: [["R", "R phase", PHASE.R, 2], ["Y", "Y phase", PHASE.Y, 2], ["B", "B phase", PHASE.B, 2]],
      refs: [[limits.load_pct.alarm, `Rated load ${limits.load_pct.alarm} %`, "var(--color-alarm)"]] },
    volt: { unit: "V", lines: [["VR", "R phase", PHASE.R, 2], ["VY", "Y phase", PHASE.Y, 2], ["VB", "B phase", PHASE.B, 2]],
      refs: [[V.high, `Upper limit ${V.high} V`, "var(--color-warn)"], [V.low, `Lower limit ${V.low} V`, "var(--color-warn)"]] },
  };
  const v = views[tab];
  return (
    <Card title="Trends, last 48 hours" help={HELP.trends(V)}
      right={<Tabs className="mb-0 border-b-0" value={tab} onChange={setTab}
        tabs={[["temp", "Temperatures"], ["load", "Load"], ["volt", "Voltage"]]} />}>
      <div className="flex flex-wrap gap-5 text-sm mb-2">
        {v.lines.map(([k, name, c, w, dash]) => (
          <span key={k} className="flex items-center gap-2">
            <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={c} strokeWidth={w + 0.6} strokeDasharray={dash} /></svg>{name}
          </span>
        ))}
        <span className="text-dim ml-auto">{v.unit}</span>
      </div>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="t" tick={axis} minTickGap={80} tickMargin={8} stroke="var(--color-edge)" />
          <YAxis tick={axis} width={48} domain={["auto", "auto"]} stroke="var(--color-edge)" />
          <Tooltip {...tip} formatter={(x) => (typeof x === "number" ? x.toFixed(1) : x)} />
          {pre && <ReferenceArea x1={data[0].t} x2={pre} fill="var(--chart-3)" fillOpacity={0.15}
            label={{ value: "history sent at start", fill: "var(--chart-axis)", fontSize: 14, position: "insideTopLeft" }} />}
          {v.refs.map(([y, label, c]) => (
            <ReferenceLine key={label} y={y} stroke={c} strokeDasharray="6 4" ifOverflow="extendDomain"
              label={{ value: label, fill: c, fontSize: 14, position: "insideTopRight" }} />
          ))}
          {v.lines.map(([k, name, c, w, dash]) => (
            <Line key={k} dataKey={k} name={name} stroke={c} strokeWidth={w} strokeDasharray={dash} dot={false}
              isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

// ======================================================================= engineering details
// decimals by unit: pu at most 2, % one, a unitless slope three
const ND = { pu: 2, "%": 1, "": 3 };
// channel states in words (same as dtwin/labels.py STATE)
const STATE_WORD = { ok: "trusted", step: "trusted (step seen)", dead: "no valid reading", stuck: "frozen",
  suspect: "disagrees with the other phases", absent: "not recorded" };
function Row({ label, tech, children }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-t border-edge text-[15px]">
      <Term tech={tech}><span className="text-dim">{label}</span></Term>
      <span className="text-right">{children}</span>
    </div>
  );
}

export function Details({ s }) {
  const r = s.readings;
  const sus = suspectSet(s);
  const L = s.life;
  const f = s.countdown.fixed;
  const [table, setTable] = useState(false);
  const chName = { IR: "R current", IY: "Y current", IB: "B current", VR: "R voltage", VY: "Y voltage", VB: "B voltage",
    oil_temp: "Top-oil temperature", ambient_temp: "Ambient temperature" };
  return (
    <Fold title="Engineering details" help={HELP.details} summary="Phases, channels, cooling fit, insulation wear, protection">
      <div className="grid gap-x-10 gap-y-6 lg:grid-cols-3">
        <div>
          <h3 className="font-semibold mb-1">Each phase</h3>
          <table className="w-full text-[15px]">
            <thead><tr className="text-dim text-sm text-left"><th className="font-normal">Phase</th>
              <th className="font-normal text-right">Current</th><th className="font-normal text-right">Voltage</th></tr></thead>
            <tbody>{["R", "Y", "B"].map((p) => (
              <tr key={p} className="border-t border-edge">
                <td className="py-1.5"><span className="inline-block w-2.5 h-2.5 rounded-sm mr-2" style={{ background: PHASE[p] }} />{p}</td>
                <td className={`text-right ${sus.has(`I${p}`) ? "text-dim" : ""}`}>{fmt(r.currents_a[`I${p}`])} A
                  {sus.has(`I${p}`) && <span className="text-sensor text-sm"> suspect</span>}</td>
                <td className={`text-right ${sus.has(`V${p}`) ? "text-dim" : ""}`}>{fmt(r.voltages_v[`V${p}`])} V
                  {sus.has(`V${p}`) && <span className="text-sensor text-sm"> suspect</span>}</td>
              </tr>))}</tbody>
          </table>
          <Row label="Current unbalance" tech="Max deviation from the mean, NEMA">{fmt(r.current_unbalance_pct)} %</Row>
          <Row label="Voltage unbalance" tech="VUF, IEC (negative / positive sequence)">{fmt(r.voltage_unbalance_pct, 2)} %</Row>
          <Row label="Busiest phase load">{fmt(r.load_worst_phase_pct, 0)} %</Row>
        </div>
        <div>
          <h3 className="font-semibold mb-1">Measurements</h3>
          <div className="grid grid-cols-2 gap-2 mb-2">
            {Object.keys(r.channels).map((c) => (
              <div key={c} className={`rounded-lg border px-2 py-1 text-sm ${sus.has(c) ? "border-sensor text-sensor border-2" : "border-edge"}`}
                title={`${chName[c] || c}: ${STATE_WORD[r.channels[c]] || r.channels[c]}`}>
                {chName[c] || c}: {sus.has(c) ? "suspect" : "trusted"}
              </div>
            ))}
          </div>
          <Row label="Ambient">{fmt(r.ambient_c)} °C</Row>
          <Row label="Top oil, model prediction" tech="IEEE C57.91 top-oil model">{fmt(r.oil_pred_c)} °C</Row>
          <Row label="Sensor vs model gap" tech="Thermal residual: measured minus predicted top oil">{fmt(r.residual_k, 2)} K</Row>
        </div>
        <div>
          <h3 className="font-semibold mb-1">Cooling and insulation</h3>
          <Row label="Cooling fit, slope" tech="Rise-fit slope a (Huber regression of measured on predicted rise); 1.00 = as designed, alarm 1.06">{fmt(r.slope, 3)}</Row>
          <Row label="Cooling fit, offset" tech="Rise-fit intercept b: a probe offset shows here">{fmt(r.intercept_k, 2)} K</Row>
          <Row label="Insulation wear" tech="Ageing acceleration factor F_AA, IEEE C57.91">wearing at {fmt(r.faa, 2)}× normal speed</Row>
          <Row label="Life used, last 24 h" tech="Equivalent aged hours and their value at replacement cost / rated life">
            {hrs(L.last_24h.aged_h)} h, worth {inr(L.last_24h.inr)}</Row>
          <Row label="Existing protection setting (comparison)" tech={`The existing monitoring device's fixed ${f.limit_c} °C top-oil trip; not used for status`}>
            {s.reference.fixed_trip_active ? `would have tripped at ${when(s.reference.since)}`
              : f.minutes !== null ? `would trip in ${duration(f.minutes)}` : `${f.limit_c} °C trip not reached`}
          </Row>
          <button className="text-sm underline text-dim mt-2" onClick={() => setTable(!table)}>
            {table ? "Hide" : "Show"} how much load that setting allows at each ambient</button>
          {table && <AmbientTable s={s} />}
        </div>
      </div>
      <div className="mt-6">
        <h3 className="font-semibold mb-1">Health index, by component</h3>
        <div className="grid md:grid-cols-5 gap-4">
          {s.health.components.map((c) => (
            <div key={c.key} className="text-[15px]">
              <div className="text-dim text-sm" title={`${c.label}: ${c.scale}`}>{c.label.split(" (")[0]}</div>
              <div className="font-semibold">{fmt(c.score, 0)} <span className="text-dim font-normal text-sm">of 100, weight {Math.round(100 * c.weight)} %</span></div>
            </div>
          ))}
        </div>
        {capNote(s.health) && <p className="text-sm mt-2">{capNote(s.health)}</p>}
      </div>
    </Fold>
  );
}

// ======================================================================= page
export default function Overview({ ctx, series, onSimulate }) {
  const { s, stale, mode, live } = ctx;
  return (
    <main className={`px-6 py-6 space-y-6 ${stale ? "grayscale opacity-60" : ""}`}>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-stretch">
        <div className="min-h-[460px]"><TwinPanel ctx={ctx} onSimulate={onSimulate} /></div>
        <div className="space-y-6">
          <Whats s={s} stale={stale} />
          <KeyNumbers s={s} />
        </div>
      </div>
      <Trends series={series} limits={s.display_limits} prewarmUntil={mode === "live" ? live.status?.prewarm_until : null} />
      <Details s={s} />
    </main>
  );
}
