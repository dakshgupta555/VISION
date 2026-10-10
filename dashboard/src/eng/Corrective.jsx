// Corrective-action panel for a yellow, red or instrument-fault component.
// Everything comes from the recommendation agent; materials only from
// config/catalogue.yaml.
export default function Corrective({ rec }) {
  if (!rec) return null;
  const List = ({ title, items, dim }) => (items && items.length ? (
    <div className="mt-2">
      <div className="text-xs font-semibold">{title}</div>
      <ul className={`text-xs list-disc ml-4 ${dim ? "text-dim" : ""}`}>{items.map((x) => <li key={x}>{x}</li>)}</ul>
    </div>
  ) : null);
  return (
    <div>
      {rec.corrective.conditions.map((c) => (
        <div key={c.key} className="mb-4 border-l-2 border-accent pl-3">
          <div className="text-sm font-semibold">{c.title}</div>
          <div className="mt-1 text-xs"><b>Detected condition.</b> {c.detected_condition}</div>
          <div className="mt-2">
            <div className="text-xs font-semibold">Supporting evidence</div>
            <ul className="text-xs list-disc ml-4 tabular-nums">{c.evidence.map((e) => <li key={e.id}>{e.text}</li>)}</ul>
          </div>
          {c.indication_vs_source && <div className="mt-2 text-xs bg-ink border border-edge rounded p-2">{c.indication_vs_source}</div>}
          <List title="Recommended inspection or verification" items={c.inspection} />
          <List title="Potential corrective action" items={c.corrective} />
          <List title="Required qualification" items={c.qualification} />
          <List title="Safety considerations" items={c.safety} />
          {c.materials.length > 0 && (
            <div className="mt-2">
              <div className="text-xs font-semibold">Parts and materials (from the catalogue only)</div>
              <table className="w-full text-xs"><tbody>
                {c.materials.map((m) => (
                  <tr key={m.item} className="border-t border-edge align-top">
                    <td className="py-0.5 pr-2">{m.item}</td><td className="py-0.5">{m.specification}
                      {m.note && <div className="text-dim">{m.note}</div>}</td>
                  </tr>
                ))}
              </tbody></table>
            </div>
          )}
          {c.notes && <div className="mt-2 text-xs text-dim">{c.notes}</div>}
        </div>
      ))}
      <p className="text-xs text-dim">{rec.corrective.advisory}</p>
    </div>
  );
}
