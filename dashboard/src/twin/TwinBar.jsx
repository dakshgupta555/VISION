import { ModeSwitch } from "../live.jsx";

// Top bar of the twin workspace: navigation, mode, and the time control.
export function NavTabs({ view, setView }) {
  return (
    <nav className="flex gap-1 text-sm">
      {[["dashboard", "Dashboard"], ["twin", "Digital twin"], ["engineering", "Engineering"]].map(([k, label]) => (
        <button key={k} onClick={() => setView(k)}
          className={`px-3 py-1 rounded ${view === k ? "bg-edge text-fg" : "text-dim hover:text-fg"}`}>{label}</button>
      ))}
    </nav>
  );
}

export default function TwinBar({ s, view, setView, mode, setMode, live, tl, i, setI, playing, setPlaying }) {
  const st = live?.status;
  return (
    <header className="flex flex-wrap items-center gap-4 px-4 py-2 border-b border-edge">
      <div className="text-sm"><b>{s.nameplate.tag}</b>
        <span className="text-dim"> · {s.nameplate.rating_kva} kVA · {s.nameplate.hv_kv} kV / {s.nameplate.lv_v} V · {s.nameplate.cooling}, three-phase</span>
      </div>
      <NavTabs view={view} setView={setView} />
      <ModeSwitch mode={mode} setMode={setMode} />
      {mode === "replay" ? (
        <div className="flex items-center gap-2 flex-1 min-w-[18rem] text-xs">
          <button className="twin-btn" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
          <input type="range" min={0} max={s.n - 1} value={i ?? 0} className="flex-1 accent-cyan-400"
            onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }} />
          <span className="tabular-nums text-dim">Replay of the supplied file · {s.timestamp.replace("T", " ")}</span>
        </div>
      ) : (
        <div className="flex items-center gap-3 flex-1 text-xs">
          <span className={st?.stale ? "text-dim" : "text-ok"}>{st?.source_label || "No source"}</span>
          <span className="tabular-nums">Sample age {st?.sample_age_s ?? "—"} s</span>
          <span className="tabular-nums text-dim">{s.timestamp.replace("T", " ")}</span>
          {st?.stale && <span className="text-dim">{st.reason}</span>}
        </div>
      )}
    </header>
  );
}
