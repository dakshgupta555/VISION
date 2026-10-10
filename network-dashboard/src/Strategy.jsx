// Strategy comparison: four ways to spend on monitoring, at one capital budget,
// from the same fleet file and the same planner (dtwin_network/strategy.py).
import { useEffect, useState } from "react";
import { api, Card, fmt, Help } from "./ui.jsx";
import { HELP } from "./help.js";
import { inrIN } from "./parts.jsx";
import { BudgetSlider } from "./Planner.jsx";

const COLS = [
  ["Assets individually monitored", (r) => `${r.monitored} of ${r.monitored + r.unmonitored}`],
  ["Initial cost", (r) => inrIN(r.initial_inr) + (r.within_budget ? "" : " (over budget)")],
  ["Recurring cost, per year", (r) => inrIN(r.recurring_inr)],
  ["High-priority assets directly monitored", (r) => `${r.high_monitored} of ${r.high_total}`],
  ["Assets unmonitored", (r) => fmt(r.unmonitored, 0)],
  ["Gateway utilisation", (r) => `${fmt(r.gateway_util_pct, 0)} % (${r.gateway_used} of ${r.gateway_capacity} slots)`],
  ["Risk points covered (new nodes)", (r) => fmt(r.risk_covered, 0)],
];

export default function Strategy({ budget, setBudget }) {
  const [s, setS] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    const id = setTimeout(() => api(`net/strategies?budget=${budget}`).then((x) => { setS(x); setErr(null); }).catch((e) => setErr(String(e))), 120);
    return () => clearTimeout(id);
  }, [budget]);
  return (
    <main className="px-6 py-6 space-y-6">
      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] items-start">
        <BudgetSlider budget={budget} setBudget={setBudget} />
        <Card title="How to read this" help={HELP.strategy}>
          <p className="text-[15px]">Four ways to spend the same capital budget on monitoring this district. B and C add nodes to individual
            transformers chosen by risk; C also decides which gateway sites to open. D monitors one transformer per manufacturer and
            rating group. Every number comes from the fleet file and the planner, and is illustrative.</p>
        </Card>
      </div>
      {err && <p className="text-alarm">{err}</p>}
      <Card pad="p-0">
        <div className="px-6 pt-5 pb-1 flex items-center">
          <h2 className="text-base font-semibold">Strategy comparison</h2>
          <span className="ml-auto"><Help text={HELP.strategy} title="the comparison" /></span>
        </div>
        {!s ? <p className="p-6 text-dim">Solving…</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[15px]">
              <thead>
                <tr className="text-left border-b border-edge">
                  <th className="font-normal text-dim px-6 py-3 w-[22%]">Budget {inrIN(s.budget)}</th>
                  {s.rows.map((r) => (
                    <th key={r.key} className={`px-4 py-3 align-bottom ${r.key === "C" ? "bg-ink" : ""}`}>
                      <div className="text-sm text-dim font-normal">{r.key}</div>
                      <div className="font-semibold">{r.name}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COLS.map(([label, f]) => (
                  <tr key={label} className="border-t border-edge">
                    <td className="px-6 py-2.5 text-dim">{label}</td>
                    {s.rows.map((r) => (
                      <td key={r.key} className={`px-4 py-2.5 font-semibold ${r.key === "C" ? "bg-ink" : ""}`}>{r.feasible === false ? "—" : f(r)}</td>
                    ))}
                  </tr>
                ))}
                <tr className="border-t border-edge">
                  <td className="px-6 py-2.5 text-dim">Gateways opened</td>
                  {s.rows.map((r) => <td key={r.key} className={`px-4 py-2.5 ${r.key === "C" ? "bg-ink" : ""}`}>{r.gateways_opened?.join(", ") || "none"}</td>)}
                </tr>
                <tr className="border-t border-edge">
                  <td className="px-6 py-2.5 text-dim">Notes</td>
                  {s.rows.map((r) => <td key={r.key} className={`px-4 py-2.5 text-sm text-dim ${r.key === "C" ? "bg-ink" : ""}`}>{r.note || "—"}</td>)}
                </tr>
              </tbody>
            </table>
            <p className="px-6 py-4 border-t border-edge text-[15px]">{s.caption}</p>
          </div>
        )}
      </Card>
    </main>
  );
}
