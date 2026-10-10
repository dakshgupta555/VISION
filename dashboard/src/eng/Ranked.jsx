// Ranked hypotheses with the evidence for and against each. Shared by the twin
// inspector and the Engineering diagnostics tab.
const TONE = { "more likely": "text-fg", possible: "text-fg/80", "less likely": "text-dim", excluded: "text-dim line-through" };

export default function Ranked({ finding }) {
  return (
    <div className="mb-3">
      <div className="text-xs font-semibold mb-1">{finding.condition}</div>
      {finding.evidence?.length > 0 && <div className="text-xs text-dim mb-1">Evidence: {finding.evidence.join("; ")}</div>}
      <ol className="text-xs space-y-1.5">
        {finding.ranked.map((h) => (
          <li key={h.rank} className="border-l-2 border-edge pl-2">
            <div className={TONE[h.likelihood]}><span className="tabular-nums">{h.rank}.</span> {h.hypothesis}
              <span className="text-dim">, {h.likelihood}</span></div>
            {h.supporting.length > 0 && <div className="text-dim">For: {h.supporting.join("; ")}</div>}
            {h.against.length > 0 && <div className="text-dim">Against: {h.against.join("; ")}</div>}
            {h.uncertainty && <div className="text-dim">Uncertainty: {h.uncertainty}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}
