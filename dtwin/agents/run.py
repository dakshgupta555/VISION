"""
The four agents and the orchestrator. Each wraps existing modules; nothing here
re-implements physics or detection.
"""

import math

import numpy as np

from ..physics import circuit as C
from ..physics import magnetic as M
from ..physics.ageing import ageing_factor
from ..physics.thermal import hotspot_rise_ultimate, top_oil_rise_ultimate
from ..twin import components as TC
from ..twin.diagnose import GROUPS, diagnose
from ..twin.engineering import losses
from .schema import record

STATE_TXT = {"green": "no monitored abnormality", "yellow": "a concerning deviation", "red": "a critical condition",
             "grey": "not assessed", "sensor": "an instrument fault, transformer unaffected"}


def _o(i, name, value, unit, source):
    return {"id": f"obs:{i}", "name": name, "value": value, "unit": unit, "source": source}


def _c(i, name, equation, substituted, result, module):
    return {"id": f"calc:{i}", "name": name, "equation": equation, "substituted": substituted,
            "result": result, "module": module}


def _num(x, nd=3):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), nd)


# --------------------------------------------------------------------------- #
# Observation
# --------------------------------------------------------------------------- #
def observation_agent(tw, i, fresh=None):
    r, d, rep = tw.rows.iloc[i], tw.df.iloc[i], tw.report
    ts = tw.rows.index[i].isoformat()
    obs = []
    for ch, unit in (("IR", "A"), ("IY", "A"), ("IB", "A"), ("VR", "V"), ("VY", "V"), ("VB", "V"),
                     ("oil_temp", "°C"), ("ambient_temp", "°C")):
        obs.append(_o(ch, ch, _num(d[ch], 2), unit, f"{rep.mapping.get(ch, {}).get('source', 'absent')} "
                                                     f"(channel {r[f'ch_{ch}']})"))
    devs = []
    for ch in ("IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp"):
        st = r[f"ch_{ch}"]
        if st in ("dead", "stuck", "suspect", "absent"):
            devs.append({"id": f"dev:quality:{ch}", "quantity": f"{ch} channel", "observed": st,
                         "reference": "ok", "deviation": st, "rule": "validator tier 1/2 channel checks"})
    stale = bool(fresh and fresh.get("stale"))
    if stale:
        devs.append({"id": "dev:quality:stale", "quantity": "data freshness", "observed": fresh.get("reason"),
                     "reference": "a sample within 3 periods", "deviation": "stale", "rule": "live freshness"})
    present = 8 - len(rep.absent)
    calc = [_c("cadence", "Sample cadence", "median spacing of timestamps", "",
               f"{rep.cadence.get('median_min')} min", "dtwin.ingest")]
    expl = (f"{present} of 8 channels present; {sum(1 for x in devs if x['id'].startswith('dev:quality:') and 'stale' not in x['id'])} "
            f"flagged by the validator" + ("; data are stale" if stale else "") + f". Rating {tw.np_.rating_kva:g} kVA "
            f"({rep.nameplate.get('how')}).")
    return [record("observation", tw.np_.tag, None, ts, observations=obs, calculations=calc, deviations=devs,
                   evidence_coverage={"available": [c for c in ("IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp")
                                                    if c not in rep.absent],
                                      "missing": list(rep.absent), "fraction": round(present / 8, 3)},
                   additional_evidence_required=[f"a trusted {x['quantity']}" for x in devs],
                   explanation=expl, evidence_ids=[o["id"] for o in obs] + [x["id"] for x in devs])]


# --------------------------------------------------------------------------- #
# Physics
# --------------------------------------------------------------------------- #
def physics_agent(tw, i):
    np_, d, r = tw.np_, tw.df.iloc[i], tw.rows.iloc[i]
    ts = tw.rows.index[i].isoformat()
    out = []
    L = losses(tw, i)
    p = C.from_nameplate(np_)
    calcs = [_c(f"pcu_{ph}", f"Copper loss, {ph} phase", "P_cu = I^2 (R2 + R1 / a^2)",
                f"{L['per_phase'][ph]['current_a'] or 0:.1f}^2 x {L['req_lv_ohm']}", f"{L['per_phase'][ph]['p_cu_w']} W",
                "dtwin.physics.circuit") for ph in "RYB"]
    calcs.append(_c("pcore", "Core loss", "P_core = 3 (V_LV a)^2 / Rc", "", f"{L['core_loss_w']} W", "dtwin.physics.circuit"))
    calcs.append(_c("eff", "Efficiency", "P_out / (P_out + P_cu + P_core)", f"power factor {L['pf_assumed']} (assumed)",
                    f"{L['efficiency_pct']} %", "dtwin.physics.circuit"))
    rc = C.thermal_rc(np_, p)
    calcs.append(_c("rth", "Oil thermal resistance and time constant", "Rth = rise_R / P_total,R ; tau = Rth Cth",
                    f"{np_.top_oil_rise_rated_k} K / {rc['rated_total_loss_w']:.0f} W", f"Rth {1000 * rc['Rth_k_per_w']:.1f} K/kW, "
                    f"tau {rc['tau_oil_h']} h", "dtwin.physics.thermal"))
    out.append(record("physics", np_.tag, None, ts, calculations=calcs,
                      evidence_coverage={"available": ["phase currents", "phase voltages"],
                                         "missing": ["winding resistance test", "power factor"], "fraction": 0.5},
                      additional_evidence_required=["factory test report (losses, impedance)", "power factor"],
                      explanation=f"Total loss {L['copper_loss_w'] + L['core_loss_w']:.0f} W at {L['load_pct']:.0f} % load; "
                                  f"efficiency {L['efficiency_pct']} % at an assumed power factor of {L['pf_assumed']}.",
                      evidence_ids=[c["id"] for c in calcs]))
    oil = float(d.oil_temp) if np.isfinite(d.oil_temp) else float(r.pred_oil_c)
    for ph in "RYB":
        cur = float(d[f"I{ph}"]) if np.isfinite(d[f"I{ph}"]) else None
        if cur is None:
            continue
        k = cur / np_.rated_current_a
        hs = oil + hotspot_rise_ultimate(k, np_)
        faa = float(ageing_factor(hs, np_))
        c = _c(f"hs_{ph}", f"Hot-spot, {ph} phase", "theta_hs = theta_oil + dTheta_hs,R K^(2m)",
               f"{oil:.1f} + {np_.hotspot_rise_rated_k:g} x {k:.3f}^{2 * np_.m_exponent:g}", f"{hs:.1f} °C, F_AA {faa:.3f}",
               "dtwin.physics.thermal")
        out.append(record("physics", np_.tag, f"lv_winding_{ph.lower()}", ts, calculations=[c],
                          observations=[_o(f"I{ph}", f"I{ph}", round(cur, 2), "A", "measured"),
                                        _o("oil_temp", "top oil", round(oil, 2), "°C", "measured")],
                          evidence_coverage={"available": [f"I{ph}", "oil_temp"], "missing": ["winding temperature"],
                                             "fraction": round(2 / 3, 3)},
                          additional_evidence_required=["direct winding temperature"],
                          explanation=f"{ph}-phase hot-spot {hs:.1f} °C from top oil {oil:.1f} °C and load {100 * k:.0f} %.",
                          evidence_ids=[c["id"]]))
    k_mean = float(r.K_oil)
    amb = float(d.ambient_temp) if np.isfinite(d.ambient_temp) else None
    if amb is not None:
        rise = top_oil_rise_ultimate(k_mean, np_)
        c = _c("oil_ss", "Steady top oil, healthy cooling", "theta_oil = theta_amb + rise_R ((K^2 R + 1)/(R + 1))^n",
               f"{amb:.1f} + {np_.top_oil_rise_rated_k:g} x (({k_mean:.3f}^2 x {np_.loss_ratio_R:g} + 1)/{np_.loss_ratio_R + 1:g})^{np_.n_exponent:g}",
               f"{amb + rise:.1f} °C", "dtwin.physics.thermal")
        out.append(record("physics", np_.tag, "radiators", ts, calculations=[c],
                          evidence_coverage={"available": ["oil_temp", "ambient_temp", "load"], "missing": ["fin temperatures"],
                                             "fraction": 0.75},
                          explanation=f"With healthy cooling the oil would settle at {amb + rise:.1f} °C at this load and ambient.",
                          evidence_ids=[c["id"]]))
    volts = [float(d[f"V{ph}"]) for ph in "RYB" if np.isfinite(d[f"V{ph}"])]
    if volts:
        des = M.core_design(np_)
        v_pu = float(np.mean(volts)) / np_.voltage_nominal_ln_v
        b = des["b_rated_t"] * v_pu
        c = _c("bmax", "Peak core flux density", "B_max = V / (4.44 f N1 A)",
               f"{des['v_winding_rated_v'] * v_pu:.0f} / (4.44 x 50 x {des['n1_turns']:.0f} x {des['a_core_m2']:.4f})",
               f"{b:.3f} T (knee {M.KNEE_T} T)", "dtwin.physics.magnetic")
        out.append(record("physics", np_.tag, "core", ts, calculations=[c],
                          evidence_coverage={"available": ["phase voltages"], "missing": ["open-circuit test", "core design data"],
                                             "fraction": round(1 / 3, 3)},
                          additional_evidence_required=["open-circuit test", "core design data (turns, area)"],
                          explanation=f"At {100 * v_pu:.1f} % voltage the core runs at about {b:.2f} T, "
                                      f"{'below' if b < M.KNEE_T else 'above'} the {M.KNEE_T} T knee (illustrative design).",
                          evidence_ids=[c["id"]]))
    return out


# --------------------------------------------------------------------------- #
# Diagnostic
# --------------------------------------------------------------------------- #
def diagnostic_agent(tw, i, dg):
    ts = dg["timestamp"]
    group_of = {cid: g for g in dg["groups"] for cid in g["component_ids"]}
    out = []
    for c in dg["components"]["components"]:
        g = group_of.get(c["id"])
        devs = []
        if c["rule"] and c["state"] in ("yellow", "red", "sensor"):
            devs.append({"id": f"dev:{c['id']}", "quantity": c["name"], "observed": c["rule"]["substituted"],
                         "reference": "; ".join(f"{e['label']} {e['value']}" for e in c["expected"]),
                         "deviation": c["deviation"], "rule": c["rule"]["equation"]})
        hyps = []
        for f in c.get("findings", []):
            hyps += f["ranked"]
        obs = [_o(f"{c['id']}:{k}", o["label"], o["value"], o["unit"], "analytics") for k, o in enumerate(c["observed"])]
        calc = [_c(f"{c['id']}:rule", c["name"], c["rule"]["equation"], c["rule"]["substituted"] or "",
                   c["rule"]["result"] or "", "dtwin.twin.components")] if c["rule"] else []
        cov = {"available": g["evidence_available"] if g else [], "missing": g["evidence_missing"] if g else c["missing"],
               "fraction": g["evidence_coverage"] if g else 0.0}
        expl = f"{c['name']}: {STATE_TXT[c['state']]}. {c['reason']}"
        if calc:
            expl += f" Rule: {c['rule']['equation']}; {c['rule']['substituted']}."
        out.append(record("diagnostic", tw.np_.tag, c["id"], ts, observations=obs, calculations=calc, deviations=devs,
                          hypotheses=hyps, evidence_coverage=cov, additional_evidence_required=c["missing"],
                          explanation=expl, evidence_ids=[x["id"] for x in obs + calc + devs], state=c["state"],
                          conditions=[f["condition"] for f in c.get("findings", [])]))
    return out


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def recommendation_agent(tw, i, diag_records, catalogue=None):
    from ..twin.actions import corrective_actions
    out = []
    for rec in diag_records:
        if rec.get("state") not in ("yellow", "red", "sensor"):
            continue
        acts = corrective_actions(rec, catalogue)
        out.append(record("recommendation", rec["asset_id"], rec["component_id"], rec["timestamp"],
                          deviations=rec["deviations"], hypotheses=rec["hypotheses"][:3],
                          evidence_coverage=rec["evidence_coverage"],
                          recommended_action=acts["summary"], additional_evidence_required=rec["additional_evidence_required"],
                          explanation=acts["explanation"], evidence_ids=list(rec["evidence_ids"]),
                          corrective=acts, state=rec["state"]))
    return out


def run_agents(tw, i, fresh=None):
    dg = diagnose(tw, i, fresh)
    diag = diagnostic_agent(tw, i, dg)
    return {"asset_id": tw.np_.tag, "timestamp": dg["timestamp"], "stale": dg["stale"],
            "agents": {"observation": observation_agent(tw, i, fresh), "physics": physics_agent(tw, i),
                       "diagnostic": diag, "recommendation": recommendation_agent(tw, i, diag)},
            "method": "deterministic rules over dtwin modules; no language model; templates filled with computed numbers"}
