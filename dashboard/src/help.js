// Explanations behind every "?" on the dashboard. Each answers three things:
// what this is, how it is computed (with the equation or standard), and what
// normal looks like. Values that belong to this transformer are passed in from
// the backend response, never typed here.
import { fmt, inr } from "./ui.jsx";

export const HELP = {
  whats: [
    "The twin's diagnosis at this moment, in plain words. It is built from the alarms the analytics raise: the thermal fit of measured against predicted oil rise, cross-checks between the eight channels, overload and phase unbalance.",
    "Actions come from the same rules. \"How sure\" is the evidence the alarm fired on. Normal: a green \"All normal, no action needed\".",
  ],
  hotspot: (lim) => [
    "The hottest point inside the windings. It sets how fast the paper insulation ages, so it is the number that decides whether the load is safe.",
    "IEEE C57.91: hot-spot = measured top oil + winding gradient, where the gradient is 25 K at rated current and scales with (load)^1.6 for the most loaded phase.",
    `Normal: below ${lim.limit} °C, where insulation wears at its rated speed. Each 7 K above that roughly doubles the wear; amber from ${lim.warn} °C, red from ${lim.alarm} °C.`,
  ],
  load: (lim, ratedA) => [
    `Current carried as a share of rated current (${ratedA} A per phase): the RMS of the three phase currents divided by the rated current.`,
    `Copper loss grows with the square of load, so heating rises fast above 100 %. Normal: below ${lim.warn} %; amber from ${lim.warn} %, red above ${lim.alarm} %. Short peaks are expected, sustained overload is not.`,
  ],
  topoil: (lim) => [
    "Oil temperature at the top of the tank, measured by the oil probe.",
    "The twin also predicts it from load and ambient with the IEEE C57.91 top-oil model (rise = 55 K x ((K²R + 1)/(R + 1))^0.8 at steady state). Measured and predicted should agree within about 1 K.",
    `${lim.alarm} °C is the IEC 60076-7 top-oil limit for normal cyclic loading; amber from ${lim.warn} °C.`,
  ],
  health: (comps, b) => [
    "One 0-100 summary of five physical scores, each 0 exactly where its own alarm fires and 100 at healthy behaviour.",
    `Weighted: ${(comps || []).map((c) => `${c.label.split(" (")[0].toLowerCase()} ${Math.round(100 * c.weight)} %`).join(", ")}. `
      + "The index may not exceed the worse of the ageing and loading scores by more than 10, so an extreme hot-spot drives it near 0.",
    `Normal: ${b.warn_below} or more. Amber ${b.alarm_below}-${b.warn_below}, red below ${b.alarm_below}. Open Engineering details for each score.`,
  ],
  twin: [
    "A 3D model of this transformer. Each part is coloured by the evidence for it: green normal, amber warning, red alarm (flashing), violet a faulty instrument with the transformer itself unaffected, grey not monitored.",
    "Click a part to see the numbers and the rule behind its colour. Heat map shows the temperature inside the windings from the finite-element model.",
  ],
  timeline: [
    "The whole record. Bands mark when an alarm (red), a warning (amber) or an instrument fault (violet) was active; hover a band for its name.",
    "Drag the slider to replay. Everything on the page is recomputed using only data up to that moment, as it would have been seen live.",
  ],
  trends: (v) => [
    "The last 48 hours up to the cursor.",
    "Temperatures: measured top oil, computed hot-spot and ambient. Load: each phase's current as a share of rated current. Voltage: phase-to-neutral voltage of each phase.",
    `Normal: hot-spot under its limit line, phases close together, voltage within ${v.low}-${v.high} V (nominal ${v.nominal} V ± ${v.tolerance_pct} %).`,
  ],
  details: [
    "The measurements behind the summary: each phase's current and voltage, whether each of the eight channels is trusted, the cooling fit, unbalance, insulation wear and how the existing protection setting would have acted.",
    "Everything here is also on the Engineering tab, with history.",
  ],
  // ---- engineering tab
  projection: [
    "Where the temperatures go if the present load and ambient continue: the IEEE C57.91 first-order oil and winding responses stepped forward from now.",
    "It gives the time until the hot-spot limit and how much load to shed to stay under it. Normal: the hot-spot line settles below its limit.",
  ],
  cooling: [
    "Measured oil rise regressed on predicted oil rise over a rolling 24 h window (Huber fit): measured = a x predicted + b + c x t.",
    "A degraded cooling path scales the rise (slope a above 1); a drifting probe shifts it (intercept b). Normal: slope close to 1.00; the alarm is at 1.06.",
  ],
  healthBreakdown: [
    "Each of the five scores is computed on the same quantity, at the same instant, that its alarm fires on, and is 0 exactly where that alarm fires.",
    "100 comes from healthy behaviour (95th percentile of a healthy record) or from physics. Linear between the two; ageing on a log scale.",
  ],
  life: (L) => [
    "Insulation life used, in equivalent hours at the rated hot-spot, and its value in rupees.",
    "IEEE C57.91 ageing factor F_AA = exp(15000/383 - 15000/(hot-spot + 273)), integrated over time. "
      + `Rupees = ${inr(L.replacement_cost_inr)} assumed replacement cost (illustrative) ÷ ${fmt(L.normal_life_hours, 0)} h `
      + `normal life (IEEE C57.91) = ₹${fmt(L.cost_per_aged_hour_inr, 2)} per hour of life used.`,
    `Above a ${L.emergency_hotspot_c} °C hot-spot (IEC 60076-7 long-time emergency limit) protection would trip, so those hours `
      + "add no life used and no cost. Normal: a rate below 1.",
  ],
  alerts: [
    "Every alarm and warning the twin has raised, with repeats of the same fault grouped. Each says what happened, why the twin believes it and what to do.",
    "Asset faults are the transformer; sensor faults are an instrument, where the transformer itself is fine.",
  ],
  protection: [
    "What the existing device's fixed 60 °C top-oil trip would do at this moment. Shown for comparison only; it does not count towards status.",
    "An absolute oil limit carries the ambient inside it: at 48 °C ambient it trips with no load. The twin judges rise over ambient and the hot-spot instead.",
  ],
  notifications: [
    "The messages the escalation policy would send for the alerts so far: who, by which channel, and why.",
    "In replay they go to an outbox only. Normal: nothing sent while the transformer is healthy.",
  ],
  whatif: [
    "Hand the judge the slider. Change load, ambient or the cooling factor and the twin projects where the hot-spot and top oil settle, using IEEE C57.91 steady-state rises.",
    "Normal: the hot-spot settles below its limit and insulation ages at 1x or less.",
  ],
  gprs: [
    "The alarm message as it would be sent over the device's GPRS link: one line, at most 160 characters.",
    "The field order is assumed until CPRI's message format is known.",
  ],
  selftest: [
    "Injects cooling degradation of increasing severity into a fresh simulated record for this nameplate and measures the smallest one the twin catches, and how quickly.",
    "Normal result: about 4 % degradation (about 2 K of extra rise at rated load) is caught, with no false alarms before the fault.",
  ],
  inject: [
    "Changes the simulated transformer's physics in live mode; the detector is not told. Use it to show a fault being found and named.",
    "Clear the fault to watch the twin recover.",
  ],
  fem: [
    "The temperature field inside each winding from a finite-element model: steady heat conduction in an axisymmetric section of each phase leg, with that phase's own I²R loss.",
    "It shows where the hot-spot sits and how the phases differ. It has no oil flow inside the ducts, so IEEE C57.91 remains the value used for alarms.",
  ],
  curves: [
    "Characteristic curves of the core and windings from the equivalent circuit and the core steel's B-H curve.",
    "They show how the transformer responds to voltage and load; parameters without test data are labelled illustrative.",
  ],
  losses: [
    "Copper loss per phase (I²R) and core loss (V²/Rc) at this moment, efficiency and voltage regulation from the per-phase equivalent circuit.",
    "Normal: efficiency around 98 % or more at moderate load, peaking where copper loss equals core loss.",
  ],
  diagnostics: [
    "For each group of components: the evidence available and missing, and ranked hypotheses for any abnormal finding.",
    "Rankings come from stated rules on computed quantities; they are an order, not probabilities.",
  ],
  agents: [
    "The same analysis as structured records (observation, physics, diagnostic, recommendation) in one schema, for export or audit.",
    "Each record lists observations, calculations, deviations, hypotheses, evidence coverage and the action.",
  ],
};
