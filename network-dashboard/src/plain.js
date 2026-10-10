// Plain-language wording for the twin's findings. The words live here; every
// number is read from the backend's /state response.
import { duration, fmt } from "./ui.jsx";

// Same names as dtwin/labels.py: no code names reach the screen.
export const CH = { IR: "R-phase current", IY: "Y-phase current", IB: "B-phase current", VR: "R-phase voltage",
  VY: "Y-phase voltage", VB: "B-phase voltage", oil_temp: "top-oil temperature", ambient_temp: "ambient temperature" };
const channelOf = (a) => CH[a.channel] || "a";

// kind -> { headline(s, a), meaning(s, a) }
const PLAIN = {
  cooling: {
    headline: () => "Cooling is weakening: the radiators are shedding less heat than designed",
    meaning: (s) => `The oil's temperature rise is ${fmt(100 * (s.readings.slope - 1), 0)} % higher than this load and weather explain. `
      + "Left alone, the insulation wears faster and the unit will overheat at a lower load than its rating.",
    evidence: (s) => {
      const lim = s.health.components.find((c) => c.key === "cooling")?.bad;
      return `cooling fit slope ${fmt(s.readings.slope, 2)} against an alarm level of ${fmt(lim, 2)} (1.00 = as designed). `
        + "The extra rise grows with load, which a faulty probe cannot do.";
    },
  },
  overload: {
    headline: (s) => `The transformer is overloaded: it is carrying ${fmt(s.readings.load_pct, 0)} % of its rating`,
    meaning: () => "The windings are heating faster than designed and the insulation is wearing faster than normal.",
  },
  unbalance: {
    headline: () => "The three phases are unevenly loaded",
    meaning: (s) => `Current unbalance is ${fmt(s.readings.current_unbalance_pct, 1)} %. The busiest phase runs hotter `
      + "than the average suggests, and the neutral carries extra current.",
  },
  temp_offset: {
    headline: () => "A temperature sensor is reading wrong; the transformer itself is fine",
    meaning: (s) => `The oil-rise reading is shifted by ${fmt(s.readings.intercept_k, 1)} K at every load, which is what `
      + "a drifting probe does. A real heating fault would grow with load instead.",
    evidence: (s) => `cooling fit offset ${fmt(s.readings.intercept_k, 1)} K with slope ${fmt(s.readings.slope, 2)}: `
      + "the reading moved, the transformer's response to load did not.",
  },
  channel_dead: {
    headline: (s, a) => (a.what.includes("plausible range") ? a.what
      : `The ${channelOf(a)} measurement has stopped; the transformer itself is fine`),
    meaning: (s, a) => (a.what.includes("plausible range")
      ? `${a.why} Load is ${fmt(s.readings.load_pct, 0)} % of rating now.`
      : "No value is arriving. The twin rebuilds it from the other channels, so the diagnosis continues."),
  },
  channel_stuck: {
    headline: (s, a) => `The ${channelOf(a)} measurement is frozen; the transformer itself is fine`,
    meaning: () => "The value has not changed for too long to be real. The twin replaces it from the other channels.",
  },
  channel_suspect: {
    headline: (s, a) => `The ${channelOf(a)} measurement disagrees with the other phases`,
    meaning: () => "Most likely a wiring or CT/VT ratio fault on that phase, not the transformer.",
  },
  mismatch: {
    headline: () => "The transformer runs cooler than its nameplate predicts",
    meaning: () => "Usually the rating set in the twin is wrong, or the oil probe sits in a cool spot. Check the nameplate setting.",
  },
};

export function headlineFor(s) {
  const a = s.alerts_live[0];
  const h = s.countdown.hotspot;
  if (a && PLAIN[a.kind]) {
    const p = PLAIN[a.kind];
    return { text: p.headline(s, a), meaning: p.meaning(s, a), alert: a, evidence: p.evidence ? p.evidence(s, a) : a.why };
  }
  if (a) return { text: a.what, meaning: a.why, alert: a };
  if (h.already_above) {
    return { text: `The hot-spot is above its ${h.limit_c} °C limit`, meaning: "The insulation is wearing faster than its rated speed.", alert: null };
  }
  if (h.minutes !== null) {
    return { text: `The hot-spot is heading for its ${h.limit_c} °C limit`,
      meaning: `At the present load and weather it gets there in ${duration(h.minutes)}.`, alert: null };
  }
  return { text: "All normal, no action needed", meaning: "Temperatures, load and every measurement are where the model expects them.", alert: null };
}

// The status of the whole asset, for the top-bar pill: a sensor-only problem
// is violet and says so.
export function overallStatus(s, stale) {
  if (stale) return { level: "info", text: "No recent data" };
  const live = s.alerts_live;
  const lvl = s.status.level;
  if (lvl === "ok") {
    const h = s.countdown.hotspot;
    if (h.already_above) return { level: "warn", text: "Hot-spot over limit" };
    return { level: "ok", text: "Normal" };
  }
  if (live.length && live.every((a) => a.family === "sensor")) return { level: "sensor", text: "Sensor fault" };
  return lvl === "alarm" ? { level: "alarm", text: "Alarm" } : { level: "warn", text: "Warning" };
}

// Which displayed quantities rest on an untrusted channel.
export function suspectSet(s) {
  const sus = new Set((s.status.suspect_channels || "").split(",").filter(Boolean));
  Object.entries(s.readings.channels).forEach(([c, v]) => { if (!["ok", "step"].includes(v)) sus.add(c); });
  return sus;
}

// Why the health index is lower than its weighted mean, when it is.
export function capNote(h) {
  if (!h?.capped_by) return null;
  const c = h.components.find((x) => x.key === h.capped_by);
  return `Held at ${fmt(h.index, 0)}: the weighted mean of the five scores is ${fmt(h.weighted_mean, 0)}, but the index `
    + `may not exceed its worst safety-critical score (${c.label.split(" (")[0].toLowerCase()}, ${fmt(c.score, 0)}) by more than 10.`;
}
