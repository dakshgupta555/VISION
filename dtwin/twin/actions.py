"""
Corrective actions for yellow, red and instrument-fault components.

Maps a diagnostic record to catalogue conditions by rule, and returns, for
each: the detected condition with its supporting evidence (ids that resolve
in the record), the inspection or verification, the potential corrective
action, the qualification and safety considerations, and materials ONLY from
config/catalogue.yaml. Advisory: never an instruction to operate equipment or
to keep a unit in service.
"""

from functools import lru_cache
from pathlib import Path

import yaml

CATALOGUE = Path(__file__).resolve().parents[2] / "config" / "catalogue.yaml"

BY_CONDITION = {"Cooling path degraded": "cooling_degraded", "Windings overloaded": "winding_overload",
                "Phase load unbalance": "phase_unbalance", "Temperature instrument offset": "probe_offset",
                "Thermal model mismatch": "model_mismatch"}


@lru_cache(maxsize=1)
def catalogue():
    return yaml.safe_load(CATALOGUE.read_text())


def condition_keys(rec):
    """Catalogue conditions for a diagnostic record, by rule."""
    cid, state = rec["component_id"], rec.get("state")
    keys = [BY_CONDITION[c] for c in rec.get("conditions", []) if c in BY_CONDITION]
    keys += ["channel_fault" for c in rec.get("conditions", []) if " channel " in c]
    if not keys:
        if cid == "radiators":
            keys = ["cooling_degraded"]
        elif cid == "conservator":
            keys = ["oil_level_check"]
        elif cid.startswith(("lv_winding", "hv_winding")):
            keys = ["winding_overload"]
        elif cid.startswith("lv_bushing"):
            keys = ["connection_resistance"]
        elif cid == "oil":
            keys = ["oil_temperature_high"]
        elif cid == "core":
            keys = ["core_test"]
        elif cid == "oil_pocket":
            keys = ["probe_offset"]
        elif cid == "monitor_box":
            keys = ["channel_fault"]
    if cid == "radiators" and "cooling_degraded" in keys and "oil_level_check" not in keys:
        keys.append("oil_level_check")              # low oil is one of the ranked causes
    return list(dict.fromkeys(keys))


def corrective_actions(rec, cat=None):
    cat = cat or catalogue()
    evidence = [{"id": d["id"], "text": f"{d['quantity']}: {d['observed']} (rule: {d['rule']})"} for d in rec["deviations"]]
    evidence += [{"id": o["id"], "text": f"{o['name']} {o['value']} {o['unit']}".strip()} for o in rec["observations"][:4]]
    out = []
    for k in condition_keys(rec):
        e = cat["conditions"][k]
        out.append({"key": k, "title": e["title"], "detected_condition": rec["explanation"],
                    "evidence": evidence, "evidence_ids": [x["id"] for x in evidence],
                    "indication_vs_source": e.get("indication_vs_source"),
                    "inspection": e["inspection"], "corrective": e["corrective"],
                    "qualification": e["qualification"], "safety": e.get("safety", []),
                    "materials": e.get("materials", []), "notes": e.get("notes")})
    first = out[0] if out else None
    summary = (f"{first['title']}: {first['inspection'][0]}" if first else None)
    expl = (f"{rec['component_id']} is flagged ({rec.get('state')}). Recommended first step: {first['inspection'][0]} "
            f"Supported by {len(evidence)} piece(s) of evidence: " + "; ".join(x["text"] for x in evidence[:2]) + "."
            if first else "No catalogue condition applies.")
    return {"conditions": out, "summary": summary, "explanation": expl,
            "advisory": "The system never operates equipment and never authorises a transformer to remain in service."}
