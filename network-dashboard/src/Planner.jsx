// Deployment planner: which unmonitored transformers get a node and which
// candidate gateway sites open, for a capital budget. The plan comes from a
// MILP on the backend (dtwin_network/planner.py); every sentence here is a
// template over its output.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Card, fmt, Fold, Help, Icon, Tabs } from "./ui.jsx";
import { HELP } from "./help.js";
import NetMap, { MapLegend } from "./NetMap.jsx";
import { inrIN, lakh, TONE_TEXT } from "./parts.jsx";

export const BUDGET_MAX = 1200000;
const BAND_TONE = { high: "alarm", medium: "warn", low: "ok" };

export function BudgetSlider({ budget, setBudget, extra }) {
  return (
    <Card title="Capital budget" help={HELP.budget}>
      <div className="flex items-baseline gap-2">
        <span className="text-[34px] leading-none font-semibold tracking-tight">{inrIN(budget)}</span>
        <span className="text-dim">{lakh(budget)}</span>
        {extra && <span className="ml-auto text-sm text-dim">{extra}</span>}
      </div>
      <input type="range" aria-label="Capital budget" min={0} max={BUDGET_MAX} step={10000} value={budget}
        className="w-full mt-3" onChange={(e) => setBudget(Number(e.target.value))} />
      <div className="flex justify-between text-sm text-dim"><span>₹0</span><span>{lakh(BUDGET_MAX / 2)}</span><span>{lakh(BUDGET_MAX)}</span></div>
    </Card>
  );
}

function useDebounced(v, ms = 120) {
  const [d, setD] = useState(v);
  useEffect(() => { const id = setTimeout(() => setD(v), ms); return () => clearTimeout(id); }, [v, ms]);
  return d;
}

function CostBar({ spent, budget }) {
  const pct = budget > 0 ? Math.min(100, (100 * spent) / budget) : 0;
  return (
    <div className="mt-2">
      <div className="h-2.5 rounded-full bg-edge overflow-hidden"><div className="h-full rounded-full bg-fg opacity-75" style={{ width: `${pct}%` }} /></div>
      <div className="flex justify-between text-sm text-dim mt-1"><span>spent {inrIN(spent)}</span><span>budget {inrIN(budget)}</span></div>
    </div>
  );
}

function PlanCard({ p, gr, why }) {
  if (!p.feasible) return <Card title="Plan"><p className="text-alarm">{p.message}</p></Card>;
  const empty = !p.nodes.length;
  return (
    <Card title="Optimised plan" help={HELP.plan} className="border-l-4 border-fg">
      {empty ? <p className="text-[15px]">{p.message}</p> : (
        <>
          <p className="text-xl font-semibold leading-snug">
            {p.nodes.length} new node{p.nodes.length > 1 ? "s" : ""}
            {p.gateways_opened.length ? ` and ${p.gateways_opened.length} new gateway${p.gateways_opened.length > 1 ? "s" : ""} (${p.gateways_opened.join(", ")})` : ", no new gateway"}
          </p>
          <p className="text-[15px] text-dim mt-1">
            Covers {fmt(p.risk_covered, 0)} of {fmt(p.risk_total, 0)} risk points among unmonitored units, and {p.high_covered} of {p.high_total} high-risk
            units. Recurring {inrIN(p.annual_inr)} a year.</p>
          <CostBar spent={p.capital_inr} budget={p.budget} />
          <p className="text-sm text-dim mt-2"><span className="font-semibold text-fg">Nodes: </span>{p.nodes.join(", ")}</p>
        </>
      )}
      <div className="mt-4">
        <div className="text-sm text-dim mb-1">Against the greedy baseline (best risk per rupee, one at a time)</div>
        <p className="text-[15px]">{why}</p>
        <p className="text-sm text-dim mt-1">Greedy: {gr.nodes.length} nodes, {gr.gateways_opened.join(", ") || "no new gateway"}, {inrIN(gr.capital_inr)}, {fmt(gr.risk_covered, 0)} risk points.</p>
      </div>
      {p.uncovered_high.length > 0 && (
        <div className="mt-4">
          <div className="text-sm text-dim mb-1">High-risk units left without a node, and why</div>
          <ul className="space-y-1 text-[15px]">
            {p.uncovered_high.map((u) => (
              <li key={u.id} className="flex gap-3"><b className="w-16 shrink-0">{u.id}</b>
                <span className={`shrink-0 w-20 text-sm font-semibold ${u.reason === "budget" ? "text-warn" : "text-dim"}`}>{u.reason}</span>
                <span className="text-dim">{u.text.replace(/^[a-z ]+: /, "")}</span></li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function Sensitivity({ budget }) {
  const [s, setS] = useState(null);
  useEffect(() => { setS(null); api(`net/sensitivity?budget=${budget}`).then(setS).catch(() => {}); }, [budget]);
  return (
    <Card title="Which assumption matters most" help={HELP.sensitivity}>
      {!s ? <p className="text-dim">Solving…</p> : (
        <>
          <p className="text-[15px] font-semibold">{s.verdict}</p>
          <table className="w-full text-[15px] mt-3">
            <thead><tr className="text-sm text-dim text-left"><th className="font-normal">Assumption</th><th className="font-normal">Change</th>
              <th className="font-normal text-right">Choices moved</th><th className="font-normal text-right">Risk covered</th></tr></thead>
            <tbody>
              {s.rows.map((r) => (
                <tr key={r.assumption + r.change} className={`border-t border-edge ${r.assumption === s.most_sensitive ? "font-semibold" : ""}`}>
                  <td className="py-1">{r.assumption}</td><td>{r.change}</td>
                  <td className="text-right">{r.feasible ? r.n_changed : "infeasible"}</td>
                  <td className="text-right">{r.feasible ? `${fmt(r.risk_covered, 0)} (${r.d_risk >= 0 ? "+" : ""}${fmt(r.d_risk, 0)})` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

function RiskTable({ risks, plan }) {
  const [sort, setSort] = useState("risk");
  const rows = useMemo(() => Object.values(risks).sort((a, b) => (sort === "risk" ? b.risk - a.risk || (a.id < b.id ? -1 : 1) : a.id < b.id ? -1 : 1)), [risks, sort]);
  const chosen = new Set(plan?.nodes || []);
  return (
    <Fold title="Risk score for every transformer" help={HELP.risk} summary={`${rows.filter((r) => r.band === "high").length} high, formula and every term shown`}>
      <div className="flex items-center gap-3 mb-2 text-sm text-dim">
        <span>risk = 100 × likelihood × (0.5 + 0.5 × consequence). Monitored: the worst of its own twin's terms. Unmonitored: metadata.</span>
        <span className="ml-auto"><Tabs className="mb-0 border-b-0" value={sort} onChange={setSort} tabs={[["risk", "By risk"], ["id", "By id"]]} /></span>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        <table className="w-full text-[15px]">
          <thead className="sticky top-0 bg-panel"><tr className="text-sm text-dim text-left">
            <th className="font-normal py-1">Transformer</th><th className="font-normal">Basis</th><th className="font-normal">Likelihood terms (score)</th>
            <th className="font-normal text-right">Likelihood</th><th className="font-normal text-right">Consequence</th><th className="font-normal text-right">Risk</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-edge align-top">
                <td className="py-1.5 font-semibold whitespace-nowrap">{r.id}{chosen.has(r.id) && <span className="ml-1 text-sm font-normal text-dim">(in plan)</span>}</td>
                <td className="text-dim whitespace-nowrap">{r.monitored ? "own twin" : "metadata"}</td>
                <td className="text-sm">{r.likelihood_terms.map((t) => `${t.name} ${fmt(t.score, 2)}${t.weight !== undefined ? ` ×${fmt(t.weight, 2)}` : ""}`).join(" · ")}</td>
                <td className="text-right">{fmt(r.likelihood, 2)}</td>
                <td className="text-right">{fmt(r.consequence.value, 2)}</td>
                <td className={`text-right font-semibold ${TONE_TEXT[BAND_TONE[r.band]]}`}>{fmt(r.risk, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Fold>
  );
}

export default function Planner({ fleet, budget, setBudget }) {
  const B = useDebounced(budget);
  const [res, setRes] = useState(null);
  const [live, setLive] = useState(null);
  const [show, setShow] = useState("optimised");
  const [err, setErr] = useState(null);
  const seq = useRef(0);
  useEffect(() => {
    const n = ++seq.current;
    api(`net/plan?budget=${B}`).then((x) => { if (n === seq.current) { setRes(x); setErr(null); } }).catch((e) => setErr(String(e)));
  }, [B]);
  useEffect(() => { api("net/overview").then((o) => setLive(o.risks)).catch(() => {}); }, []);
  const p = res?.[show];
  const allRisks = useMemo(() => ({ ...(res?.risks || {}), ...Object.fromEntries(Object.entries(live || {}).filter(([, v]) => v.monitored)) }), [res, live]);
  return (
    <main className="px-6 py-6 space-y-6">
      {err && <p className="text-alarm">{err}</p>}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-start">
        <Card pad="p-0" className="overflow-hidden">
          <div className="px-4 pt-4 pb-3 border-b border-edge">
            <div className="flex items-center gap-3 mb-2">
              <span className="text-base font-semibold">Proposed deployment</span>
              <Tabs className="mb-0 border-b-0" value={show} onChange={setShow} tabs={[["optimised", "Optimised plan"], ["greedy", "Greedy baseline"]]} />
              <span className="ml-auto"><Help text={HELP.map} title="the map" /></span>
            </div>
            <MapLegend mode="planner" />
          </div>
          {res && p ? <NetMap fleet={fleet} risks={res.risks} plan={p} mode="planner" /> : <p className="p-6 text-dim">Solving…</p>}
        </Card>
        <div className="space-y-6">
          <BudgetSlider budget={budget} setBudget={setBudget} extra={res ? `solved in ${fmt(res.solve_ms, 0)} ms` : null} />
          {res && <PlanCard p={res.optimised} gr={res.greedy} why={res.why} />}
        </div>
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-start">
        <RiskTable risks={allRisks} plan={res?.optimised} />
        <Sensitivity budget={B} />
      </div>
    </main>
  );
}
