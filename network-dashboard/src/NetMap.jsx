// The district map: plain SVG, no tiles, no network fetches, so it works
// offline. Coordinates are the fleet file's synthetic km grid. The SVG is drawn
// in real pixels (measured), so text stays at 14 px whatever the card width.
import { useEffect, useMemo, useRef, useState } from "react";
import { fmt } from "./ui.jsx";
import { COLOR, COMM_WORD, CommMark, LEVEL_WORD, shownLevel } from "./parts.jsx";

const UNMON = "#8c959f";

function useSize() {
  const ref = useRef();
  const [sz, setSz] = useState([800, 0]);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setSz([Math.max(320, Math.floor(e.contentRect.width)), Math.floor(e.contentRect.height)]));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, sz];
}

export function inCoverage(a, gws, scale = 1) {
  return gws.some((g) => Math.hypot(a.xy_km[0] - g.xy_km[0], a.xy_km[1] - g.xy_km[1]) <= g.coverage_radius_km * scale + 1e-9);
}

// filters: { gateway, health, coverage, priority }; anything filtered out stays faint, for context
function passes(a, dev, risk, f, existing) {
  if (!f) return true;
  if (f.gateway !== "all" && (!a.monitored || a.gateway !== f.gateway)) return false;
  if (f.health !== "all") {
    const lv = dev ? shownLevel(dev) : null;
    if (f.health === "problem" && !(lv && ["warn", "alarm", "sensor"].includes(lv))) return false;
    if (f.health === "unknown" && !(dev && (lv === "unknown"))) return false;
    if (f.health === "ok" && lv !== "ok") return false;
    if (f.health === "unmonitored" && a.monitored) return false;
  }
  if (f.coverage !== "all") {
    const inside = inCoverage(a, existing);
    if (f.coverage === "inside" && !inside) return false;
    if (f.coverage === "outside" && inside) return false;
  }
  if (f.priority !== "all" && risk) {
    if (f.priority === "high" && risk.band !== "high") return false;
    if (f.priority === "medium" && risk.band === "low") return false;
  }
  return true;
}

export default function NetMap({ fleet, devices, gatewaysLive, risks, filters, plan, mode = "network", onAsset, onGateway,
  selected, height, fill = false }) {
  const [ref, [W, h0]] = useSize();
  const [hover, setHover] = useState(null);
  const [ex, ey] = fleet.district.extent_km;
  // fill: take the height the card gives; otherwise keep the district's aspect
  const natural = Math.round((W * ey) / ex);
  const H = height || (fill ? Math.max(h0, natural) : natural);
  const k = Math.min(W / ex, H / ey);
  // anchored to the top: when the card is taller than the district, the extra
  // space is open grid below, never empty space above the network
  const ox = (W - k * ex) / 2, oy = Math.min(8, (H - k * ey) / 2);
  const X = (x) => ox + x * k, Y = (y) => oy + (ey - y) * k;     // north up
  const rowsBelow = Math.max(0, Math.ceil((H - Y(0)) / k));
  const devById = useMemo(() => Object.fromEntries((devices || []).map((d) => [d.id, d])), [devices]);
  const gws = fleet.gateways;
  const existing = gws.filter((g) => g.existing);
  const gwStatus = Object.fromEntries((gatewaysLive || []).map((g) => [g.id, g]));
  const opened = new Set(plan?.gateways_opened || []);
  const planNodes = new Set(plan?.nodes || []);
  const showGw = (g) => g.existing || mode === "planner" || filters?.coverage === "candidates";
  const assetsById = Object.fromEntries(fleet.assets.map((a) => [a.id, a]));
  const gwById = Object.fromEntries(gws.map((g) => [g.id, g]));

  return (
    <div ref={ref} className={`relative w-full select-none ${fill ? "h-full" : ""}`} style={fill ? { minHeight: natural } : { height: H }}>
      <svg width={W} height={H} role="img" aria-label="District map of transformers and gateways" className="block">
        <rect x="0" y="0" width={W} height={H} fill="var(--scene-bg)" />
        {Array.from({ length: Math.floor(ex) + 1 }, (_, i) => (
          <line key={`vx${i}`} x1={X(i)} x2={X(i)} y1={0} y2={H} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {Array.from({ length: Math.floor(ey) + 1 + rowsBelow }, (_, i) => (
          <line key={`hy${i}`} y1={Y(i - rowsBelow)} y2={Y(i - rowsBelow)} x1={0} x2={W} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {/* coverage */}
        {gws.filter(showGw).map((g) => {
          const live = g.existing || opened.has(g.id);
          return (
            <circle key={`c${g.id}`} cx={X(g.xy_km[0])} cy={Y(g.xy_km[1])} r={g.coverage_radius_km * k * (plan?.radius_scale || 1)}
              fill={live ? "color-mix(in srgb, var(--color-fg) 5%, transparent)" : "none"}
              stroke={live ? "var(--color-dim)" : UNMON} strokeWidth={live ? 1.4 : 1.2}
              strokeDasharray={live ? "6 5" : "2 5"} />
          );
        })}
        {/* links: node -> gateway, one hop */}
        {mode === "network" && fleet.assets.filter((a) => a.monitored).map((a) => {
          const g = gwById[a.gateway], d = devById[a.id];
          const st = d?.comms.status;
          if (!g || st === "offline" || st === "no data") return null;
          const on = passes(a, d, risks?.[a.id], filters, existing);
          return <line key={`l${a.id}`} x1={X(a.xy_km[0])} y1={Y(a.xy_km[1])} x2={X(g.xy_km[0])} y2={Y(g.xy_km[1])}
            stroke="var(--color-dim)" strokeWidth="1.4" strokeDasharray={st === "stale" ? "3 4" : undefined} opacity={on ? 0.7 : 0.12} />;
        })}
        {mode === "planner" && [...planNodes].map((id) => {
          const a = assetsById[id], g = gwById[plan.assign[id]];
          if (!a || !g) return null;
          return <line key={`p${id}`} x1={X(a.xy_km[0])} y1={Y(a.xy_km[1])} x2={X(g.xy_km[0])} y2={Y(g.xy_km[1])}
            stroke="var(--color-fg)" strokeWidth="1.4" strokeDasharray="5 4" opacity="0.8" />;
        })}
        {/* unmonitored transformers: hollow grey, size by risk */}
        {fleet.assets.filter((a) => !a.monitored).map((a) => {
          const r = risks?.[a.id];
          const on = passes(a, null, r, filters, existing);
          const rad = 4 + (r ? r.risk / 14 : 0);
          const chosen = planNodes.has(a.id);
          return (
            <g key={a.id} opacity={on ? 1 : 0.15} className="cursor-pointer"
              onMouseEnter={() => setHover({ a, x: X(a.xy_km[0]), y: Y(a.xy_km[1]) })} onMouseLeave={() => setHover(null)}
              onClick={() => onAsset?.(a.id)}>
              <circle cx={X(a.xy_km[0])} cy={Y(a.xy_km[1])} r={rad + 4} fill="transparent" />
              <circle cx={X(a.xy_km[0])} cy={Y(a.xy_km[1])} r={rad} fill={chosen ? "var(--color-panel)" : "none"}
                stroke={chosen ? "var(--color-fg)" : UNMON} strokeWidth={chosen ? 2.6 : r?.band === "high" ? 2.2 : 1.5} />
              {chosen && <circle cx={X(a.xy_km[0])} cy={Y(a.xy_km[1])} r="2.5" fill="var(--color-fg)" />}
              {selected === a.id && <circle cx={X(a.xy_km[0])} cy={Y(a.xy_km[1])} r={rad + 5} fill="none" stroke="var(--color-fg)" strokeWidth="2" />}
            </g>
          );
        })}
        {/* gateways */}
        {gws.filter(showGw).map((g) => {
          const st = gwStatus[g.id]?.status;
          const live = g.existing || opened.has(g.id);
          const down = g.existing && st && st !== "online" && st !== "no data";
          const s = 9;
          return (
            <g key={g.id} className="cursor-pointer" onClick={() => onGateway?.(g.id)}
              onMouseEnter={() => setHover({ g, x: X(g.xy_km[0]), y: Y(g.xy_km[1]) })} onMouseLeave={() => setHover(null)}>
              <rect x={X(g.xy_km[0]) - s - 4} y={Y(g.xy_km[1]) - s - 4} width={2 * s + 8} height={2 * s + 8} fill="transparent" />
              <rect x={X(g.xy_km[0]) - s} y={Y(g.xy_km[1]) - s} width={2 * s} height={2 * s} rx="3"
                fill={live ? (down ? "var(--color-dim)" : "var(--color-fg)") : "var(--color-panel)"}
                stroke={live ? "var(--color-fg)" : UNMON} strokeWidth="1.6" strokeDasharray={live ? undefined : "3 2"} />
              <path d={`M${X(g.xy_km[0]) - 4} ${Y(g.xy_km[1]) + 4} L${X(g.xy_km[0])} ${Y(g.xy_km[1]) - 5} L${X(g.xy_km[0]) + 4} ${Y(g.xy_km[1]) + 4}`}
                fill="none" stroke={live ? "var(--color-panel)" : UNMON} strokeWidth="1.6" />
              {down && <path d={`M${X(g.xy_km[0]) - s} ${Y(g.xy_km[1]) - s} L${X(g.xy_km[0]) + s} ${Y(g.xy_km[1]) + s}`}
                stroke="var(--color-panel)" strokeWidth="2" />}
              <text x={X(g.xy_km[0])} y={Y(g.xy_km[1]) - s - 6} textAnchor="middle" fontSize="14" fontWeight="600"
                fill={live ? "var(--color-fg)" : "var(--color-dim)"} paintOrder="stroke" stroke="var(--scene-bg)" strokeWidth="4">
                {g.id}{opened.has(g.id) ? " (new)" : ""}{down ? ` ${st}` : ""}</text>
            </g>
          );
        })}
        {/* monitored transformers */}
        {fleet.assets.filter((a) => a.monitored).map((a) => {
          const d = devById[a.id];
          const lv = d ? shownLevel(d) : "unknown";
          const on = passes(a, d, risks?.[a.id], filters, existing);
          const x = X(a.xy_km[0]), y = Y(a.xy_km[1]);
          return (
            <g key={a.id} opacity={on ? 1 : 0.15} className="cursor-pointer" onClick={() => onAsset?.(a.id)}
              onMouseEnter={() => setHover({ a, d, x, y })} onMouseLeave={() => setHover(null)}>
              <circle cx={x} cy={y} r="16" fill="transparent" />
              {lv === "alarm" && <circle cx={x} cy={y} r="10" fill="none" stroke={COLOR.alarm} strokeWidth="2">
                <animate attributeName="r" from="10" to="20" dur="1.6s" repeatCount="indefinite" />
                <animate attributeName="opacity" from="0.9" to="0" dur="1.6s" repeatCount="indefinite" /></circle>}
              <circle cx={x} cy={y} r="9" fill={COLOR[lv]} stroke="var(--color-panel)" strokeWidth="2" />
              {selected === a.id && <circle cx={x} cy={y} r="14" fill="none" stroke="var(--color-fg)" strokeWidth="2" />}
              <CommSvg status={d?.comms.status || "no data"} cx={x + 11} cy={y - 10} />
              <text x={x + 13} y={y + 5} fontSize="14" fill="var(--color-fg)" paintOrder="stroke" stroke="var(--scene-bg)" strokeWidth="4">
                {a.id}</text>
            </g>
          );
        })}
        {/* scale bar */}
        <g transform={`translate(${W - k * 2 - 20},${Math.min(H - 18, Y(0) + 26)})`}>
          <line x1="0" x2={k * 2} y1="0" y2="0" stroke="var(--color-dim)" strokeWidth="2" />
          <text x={k} y="-6" textAnchor="middle" fontSize="14" fill="var(--color-dim)">2 km</text>
        </g>
      </svg>
      {hover && <MapTip hover={hover} W={W} risks={risks} />}
    </div>
  );
}

// the communication mark, drawn in place (same shapes as parts.jsx CommMark)
function CommSvg({ status, cx, cy }) {
  const c = "var(--color-fg)", r = 4.5;
  return (
    <g>
      <circle cx={cx} cy={cy} r={r + 2.5} fill="var(--color-panel)" />
      {status === "online" && <circle cx={cx} cy={cy} r={r} fill={c} />}
      {status === "stale" && <><circle cx={cx} cy={cy} r={r} fill="none" stroke={c} strokeWidth="1.6" />
        <path d={`M${cx} ${cy - r} A${r} ${r} 0 0 1 ${cx} ${cy + r} Z`} fill={c} /></>}
      {(status === "offline" || status === "no data") && <><circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--color-dim)" strokeWidth="1.6" />
        <path d={`M${cx - 3.2} ${cy - 3.2} L${cx + 3.2} ${cy + 3.2}`} stroke="var(--color-dim)" strokeWidth="1.6" /></>}
    </g>
  );
}

function MapTip({ hover, W, risks }) {
  const { a, d, g, x, y } = hover;
  const left = Math.min(Math.max(8, x + 18), W - 290);
  let body;
  if (g) {
    body = <><b>{g.id}</b> · {g.name}<div className="text-dim">{g.existing ? `capacity ${g.capacity_nodes} nodes` : "candidate site"}, {fmt(g.coverage_radius_km, 1)} km radius assumed. Click for details.</div></>;
  } else if (a.monitored && d) {
    const lv = shownLevel(d);
    body = (
      <><b>{a.id}</b> · {fmt(a.rating_kva, 0)} kVA · {a.gateway}
        <div>Health: <b>{d.health.verified ? `${LEVEL_WORD[lv] || "—"}, ${fmt(d.health.index, 0)} of 100` : `last known ${fmt(d.health.index, 0)}, unverified`}</b></div>
        <div className="flex items-center gap-1.5">Communication: <CommMark status={d.comms.status} /> <b>{COMM_WORD[d.comms.status]}</b></div>
        {d.health.label && d.health.level !== "ok" && <div className="text-dim">{d.health.label}</div>}
      </>
    );
  } else {
    const r = risks?.[a.id];
    body = <><b>{a.id}</b> · {fmt(a.rating_kva, 0)} kVA · not monitored
      <div className="text-dim">Risk {r ? `${fmt(r.risk, 0)} (${r.band})` : "—"}; {a.metadata.age_years} years, peak {a.metadata.peak_load_ratio} of rating</div></>;
  }
  return (
    <div className="absolute z-30 card px-3 py-2 text-sm pointer-events-none w-[280px]" style={{ left, top: Math.max(4, y - 10) }}>
      {body}
    </div>
  );
}

export function MapLegend({ mode = "network" }) {
  const dot = (c, hollow) => <span className="inline-block w-3 h-3 rounded-full" style={hollow ? { border: `2px solid ${c}` } : { background: c }} />;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-dim">
      {mode === "network" && <>
        {[["ok", "Normal"], ["warn", "Warning"], ["alarm", "Alarm"], ["sensor", "Instrument fault"], ["unknown", "Unknown / unverified"]].map(([k, l]) => (
          <span key={k} className="flex items-center gap-1.5">{dot(COLOR[k])}{l}</span>))}
        <span className="flex items-center gap-1.5"><CommMark status="online" />Online</span>
        <span className="flex items-center gap-1.5"><CommMark status="stale" />Stale</span>
        <span className="flex items-center gap-1.5"><CommMark status="offline" />Offline</span>
      </>}
      {mode === "planner" && <>
        <span className="flex items-center gap-1.5">{dot("var(--color-fg)", true)}Proposed node</span>
        <span className="flex items-center gap-1.5">{dot("#a3aaa6")}Monitored already</span>
      </>}
      <span className="flex items-center gap-1.5">{dot(UNMON, true)}Not monitored (size = risk)</span>
      <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-sm bg-fg" />Gateway</span>
    </div>
  );
}
