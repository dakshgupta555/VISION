"""The one record shape every agent emits, and a validator for it."""

DISCLAIMER = ("Advisory output from a monitoring model. It never operates equipment and never authorises a "
              "transformer to remain in service; that decision stays with the responsible engineer.")

FIELDS = {
    "agent": str, "asset_id": str, "component_id": (str, type(None)), "timestamp": str,
    "observations": list, "calculations": list, "deviations": list, "hypotheses": list,
    "evidence_coverage": dict, "recommended_action": (str, type(None)),
    "additional_evidence_required": list, "explanation": str, "evidence_ids": list, "disclaimer": str,
}
ITEM_FIELDS = {
    "observations": {"id", "name", "value", "unit", "source"},
    "calculations": {"id", "name", "equation", "substituted", "result", "module"},
    "deviations": {"id", "quantity", "observed", "reference", "deviation", "rule"},
    "hypotheses": {"rank", "hypothesis", "likelihood", "supporting", "against", "uncertainty"},
}
AGENTS = {"observation", "physics", "diagnostic", "recommendation"}


def record(agent, asset_id, component_id, timestamp, **kw):
    r = {"agent": agent, "asset_id": asset_id, "component_id": component_id, "timestamp": timestamp,
         "observations": [], "calculations": [], "deviations": [], "hypotheses": [],
         "evidence_coverage": {"available": [], "missing": [], "fraction": 0.0},
         "recommended_action": None, "additional_evidence_required": [], "explanation": "",
         "evidence_ids": [], "disclaimer": DISCLAIMER}
    r.update(kw)
    return r


def validate(rec):
    """List of problems with a record; empty when it fits the schema."""
    errs = []
    for k, t in FIELDS.items():
        if k not in rec:
            errs.append(f"missing {k}")
        elif not isinstance(rec[k], t):
            errs.append(f"{k} has type {type(rec[k]).__name__}")
    if rec.get("agent") not in AGENTS:
        errs.append(f"unknown agent {rec.get('agent')!r}")
    for k, need in ITEM_FIELDS.items():
        for n, item in enumerate(rec.get(k, [])):
            miss = need - set(item)
            if miss:
                errs.append(f"{k}[{n}] missing {sorted(miss)}")
    cov = rec.get("evidence_coverage", {})
    if not {"available", "missing", "fraction"} <= set(cov):
        errs.append("evidence_coverage incomplete")
    elif not 0.0 <= cov["fraction"] <= 1.0:
        errs.append("evidence_coverage.fraction outside 0..1")
    return errs
