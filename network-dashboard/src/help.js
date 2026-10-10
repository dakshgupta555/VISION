// Every "?" on the network dashboard: what it is, how it is computed, what
// normal looks like. Same format as the single-transformer app's help.js.

export const DISCLAIMER = "Simulated network: positions, radio coverage, telemetry and costs are illustrative, "
  + "not CPRI infrastructure or procurement data.";

export const HELP = {
  map: [
    "Each filled circle is a monitored transformer, coloured by its own twin's health: green normal, amber warning, red alarm, violet instrument fault, grey unknown or unverified.",
    "The small mark beside it is communication, a separate thing: solid dot online, half dot stale (packets overdue), crossed ring offline. A unit that is not reporting keeps its last known colour only as grey: never green.",
    "Squares are gateways, with their assumed coverage radius dashed. Lines link each node to its gateway: one radio hop, no mesh. Hollow grey circles are transformers with no monitoring.",
  ],
  whats: [
    "The single most important thing in the network now, in plain words, from a fixed template filled with the twins' own results. No language model writes any of it.",
    "Each transformer is judged only on its own measurements: a fault on one never changes another's health, even in the same manufacturer group.",
  ],
  monitored: ["Transformers with a node reporting to the backend, out of all transformers in the district file."],
  alarms: ["Monitored transformers whose own twin reports a warning or alarm on the asset now, verified by a recent packet. Instrument-only faults are counted separately."],
  stale: [
    "Stale: packets overdue for 2 or more reporting intervals. Offline: 6 or more. Counted on the network clock (the newest time any gateway reported).",
    "Lost communication is not a transformer fault. The unit's last known health is shown, marked unverified.",
  ],
  capacity: ["Nodes assigned to the existing gateways against what they can take (nodes per gateway, an illustrative capacity). The planner never exceeds it."],
  visit: [
    "Ranked by the risk score (Deployment planner tab shows the formula). Monitored units use their own twin: health, active alarm, time to the hot-spot limit, and communication.",
    "Unmonitored units use metadata only: peak-load ratio, age and failures in the last 5 years, weighted by customers served and criticality.",
  ],
  events: [
    "Everything the backend saw, newest first. A packet's path is shown hop by hop: node sends, gateway forwards, backend receives, the twin diagnoses, with wall-clock times.",
    "Times in brackets are simulated sample times.",
  ],
  gateway: ["A LoRaWAN gateway forwards each packet unchanged to the backend. It never averages or merges packets from different nodes."],
  asset: [
    "This transformer's own twin, the same physics and detectors as the single-transformer dashboard (IEEE C57.91 thermal model, rise-fit diagnosis), run on this unit's packets only.",
  ],
  fields: [
    "Health, confidence and communication are three separate things.",
    "Health: the twin's health index for this unit. Confidence: how far the diagnosis can be trusted now (full history, every channel trusted). Communication: whether packets are arriving.",
  ],
  risk: [
    "risk = 100 × likelihood × (0.5 + 0.5 × consequence).",
    "Likelihood, monitored: the worst of health shortfall, active alarm, time to hot-spot limit and communication. Unmonitored: 0.40 × peak load + 0.25 × age + 0.35 × failure history, each scaled 0 to 1.",
    "Consequence: 0.6 × customers served (400 = 1) + 0.4 × criticality. High at 45 or more, medium at 25. The constants are stated judgements, not fitted values.",
  ],
  budget: ["Capital budget for new nodes and new gateways. Moving the slider re-solves the plan; recurring costs are shown but not limited."],
  plan: [
    "A mixed-integer program (scipy, HiGHS) picks which unmonitored transformers get a node and which candidate gateway sites open.",
    "It maximises the risk covered, never exceeds the budget or a gateway's capacity, and only places a node within the coverage radius of an open gateway. The greedy baseline instead adds the best risk per rupee one at a time.",
  ],
  sensitivity: ["Each cost and the coverage radius are moved ±20 % and the plan re-solved. The assumption that moves the most choices is the one to firm up first with real data."],
  strategy: [
    "All four at the same capital budget, from the same fleet file. Recurring cost is per year: node fees plus gateway running costs.",
    "High priority = the high band of the metadata risk score among unmonitored transformers.",
  ],
  confidence: ["High: 48 h of history and every channel trusted. Reduced: history still filling, a channel untrusted, or packets overdue. None: not reporting."],
};
