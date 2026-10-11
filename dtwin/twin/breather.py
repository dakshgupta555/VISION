"""
Silica-gel breather: condition record for the 3D model and the info panel.

There is no breather, humidity or moisture sensor in this system, so the
condition is a USER-SET scenario or an inspection entry. It defaults to
"unknown". This module only describes the condition; it is read-only and is
not called by the engine, so with the condition at its default every existing
output is untouched (check_breather.py, check_calibration.py).

Causal chain, in the order the physics runs:

    silica-gel condition      user-set state (not measured)
      -> breather effectiveness   qualitative: drying of the air that breathes in
      -> moisture-ingress risk    qualitative: how much undried air can reach the oil
      -> insulation moisture status   NOT CALCULATED: needs moisture-in-oil or
                                      paper-moisture data this system does not have
      -> insulation ageing indicator  unchanged, unless the quantitative coupling
                                      is deliberately enabled (see couplings)

The thermal chain is separate and unchanged:

    load, ambient -> losses -> oil and winding rises -> hot-spot -> F_AA

Silica gel is a moisture-control component, not a cooling medium: it has no
term in the hot-spot or top-oil equations, by design.
"""

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config" / "breather.yaml"
STATES = ("effective", "partial", "saturated", "unknown")
NOT_CALCULATED = "not calculated: requires moisture-in-oil or paper-moisture data"


@lru_cache(maxsize=1)
def config():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def describe(condition=None):
    """The breather record for one condition. Raises ValueError on a name that
    is not one of STATES (the API turns that into a 422)."""
    cfg = config()
    cond = (condition or cfg["default"]).strip().lower()
    if cond not in STATES:
        raise ValueError(f"unknown silica-gel condition {condition!r}; one of {', '.join(STATES)}")
    st = cfg["states"][cond]
    known = cond != "unknown"
    return {
        "condition": cond,
        "label": st["label"],
        "colour": st["colour"],
        "convention": cfg["convention"],
        "source": {
            "kind": "ASSUMED",
            "text": "Set by the user as a scenario or inspection entry. No breather, humidity or moisture "
                    "sensor exists in this system, so this is not a measurement." if known else
                    "No inspection entry or scenario set. No breather, humidity or moisture sensor exists in "
                    "this system."},
        "chain": [
            {"key": "silica_gel_condition", "label": "Silica-gel condition", "value": st["label"],
             "kind": "ASSUMED", "note": "user-set, not measured"},
            {"key": "breather_effectiveness", "label": "Breather moisture-protection effectiveness",
             "value": st["effectiveness"], "kind": "DERIVED" if known else "ASSUMED",
             "note": "qualitative category from the condition, not a percentage"},
            {"key": "moisture_ingress_risk", "label": "Potential moisture-ingress risk",
             "value": st["ingress_risk"], "kind": "DERIVED" if known else "ASSUMED",
             "note": "qualitative; no humidity or breathing-volume data"},
            {"key": "insulation_moisture_status", "label": "Oil and paper moisture", "value": NOT_CALCULATED,
             "kind": "ASSUMED", "note": "needs a moisture-in-oil sensor or a paper-moisture test"},
            {"key": "thermal_hotspot_temperature", "label": "Hot-spot temperature",
             "value": "unaffected", "kind": "DERIVED",
             "note": "calculated from load and ambient only; the gel has no term in the thermal equations"},
            {"key": "insulation_ageing_indicator", "label": "Insulation ageing indicator (F_AA)",
             "value": "unaffected", "kind": "DERIVED",
             "note": "the ageing equation uses hot-spot only; the moisture coupling is off"}],
        "explain": {
            "purpose": "The conservator tank breathes: the oil expands and contracts with temperature, drawing "
                       "outside air in and pushing it out. The breather dries that air so moisture does not "
                       "reach the oil and paper insulation.",
            "mechanism": "Silica gel is a porous desiccant. Water vapour from the incoming air adsorbs onto the "
                         "gel surface, and the air that continues to the conservator is drier. An oil seal "
                         "cup at the bottom stops the gel taking up moisture when the unit is not breathing.",
            "decline": "Each breath loads the gel with moisture. As the gel nears its capacity it dries the "
                       "air less well, then not at all, so moisture-protection effectiveness falls over "
                       "months, faster in humid climates and with large load swings.",
            "inputs": "None. The condition is a user-set scenario or an inspection entry. It is not inferred "
                      "from current, load or temperature, because none of those can show how much water the "
                      "gel has taken up.",
            "can_tell": "Whether the moisture-protection path is, by the entry you made, intact, reduced or "
                        "lost, and therefore how much undried air could be reaching the conservator.",
            "cannot_tell": "How much moisture is in the oil or the paper, the dielectric strength, or any "
                           "temperature. Those need a moisture-in-oil sensor, an oil sample (Karl Fischer) or "
                           "a paper test, none of which this system has.",
            "downstream": "Moisture degrades insulation and dielectric strength over the long term and speeds "
                          "paper ageing, but no quantitative relationship is applied here. The hot-spot, "
                          "top-oil temperature and health index are unchanged by this setting."},
        "health": {"changed": False,
                   "text": "Health-index components, weights and the cap are unchanged by this setting."},
        "legend": [{"condition": k, "label": cfg["states"][k]["label"], "colour": cfg["states"][k]["colour"]}
                   for k in STATES],
    }
