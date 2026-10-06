"""
Three-phase unbalance, computed from magnitudes alone.

The CPRI monitoring device records IR/IY/IB and VR/VY/VB as magnitudes, with no
phase angles, so classical symmetrical components are not directly available.
Two magnitude-only measures are used instead:

  NEMA    max deviation from the mean, as a percentage of the mean. Simple,
          widely quoted, and what most utility staff recognise.

  IEC     the true voltage unbalance factor V2/V1, recovered from the three
          line voltage magnitudes alone by the standard beta relation:

              beta = (Vab^4 + Vbc^4 + Vca^4) / (Vab^2 + Vbc^2 + Vca^2)^2
              VUF  = sqrt( (1 - sqrt(3 - 6*beta)) / (1 + sqrt(3 - 6*beta)) )

          This is exact for the negative-sequence ratio given only magnitudes,
          which is why it is worth carrying rather than relying on NEMA alone.

Unbalance matters thermally. Negative-sequence current produces loss without
producing useful output, and the hottest winding runs ahead of the three-phase
average. The equivalent heating currents exported here are what the thermal
model consumes: the RMS of the three phases drives the oil, the worst phase
drives the hot-spot.
"""

import numpy as np


def _arr(*xs):
    return [np.asarray(x, dtype=float) for x in xs]


def nema_unbalance_pct(a, b, c):
    """Maximum deviation from the mean, as a percentage of the mean."""
    a, b, c = _arr(a, b, c)
    stack = np.vstack([a, b, c])
    mean = stack.mean(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 * np.abs(stack - mean).max(axis=0) / mean
    return np.where(mean > 1e-9, out, 0.0)


def vuf_from_magnitudes(v_ab, v_bc, v_ca):
    """IEC voltage unbalance factor (%) from three line voltage magnitudes."""
    vab, vbc, vca = _arr(v_ab, v_bc, v_ca)
    num = vab ** 4 + vbc ** 4 + vca ** 4
    den = (vab ** 2 + vbc ** 2 + vca ** 2) ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        beta = np.where(den > 1e-12, num / den, 1.0 / 3.0)
    root = np.sqrt(np.clip(3.0 - 6.0 * beta, 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        vuf = np.sqrt(np.clip((1.0 - root) / (1.0 + root), 0.0, None))
    return 100.0 * np.nan_to_num(vuf)


def line_voltages_from_phase(vr, vy, vb):
    """Approximate line voltages from phase magnitudes, assuming the phase
    displacement stays near 120 degrees. Flagged as an approximation because
    without angles it cannot be exact; it is adequate for a VUF in the low
    single-digit percent range, which is the band that matters operationally.
    """
    vr, vy, vb = _arr(vr, vy, vb)
    r3 = np.sqrt(3.0)
    return (r3 * np.sqrt((vr ** 2 + vy ** 2 + vr * vy) / 3.0),
            r3 * np.sqrt((vy ** 2 + vb ** 2 + vy * vb) / 3.0),
            r3 * np.sqrt((vb ** 2 + vr ** 2 + vb * vr) / 3.0))


def equivalent_currents(ir, iy, ib):
    """Return (rms_current, max_current, unbalance_pct).

    rms drives total copper loss and therefore the oil; max drives the hottest
    winding and therefore the hot-spot.
    """
    ir, iy, ib = _arr(ir, iy, ib)
    stack = np.vstack([ir, iy, ib])
    rms = np.sqrt((stack ** 2).mean(axis=0))
    mx = stack.max(axis=0)
    return rms, mx, nema_unbalance_pct(ir, iy, ib)


def neutral_current_estimate(ir, iy, ib):
    """Magnitude of the vector sum of three currents assumed 120 degrees apart.

    Exact only for balanced phase angles, so it is reported as an estimate. It
    is still useful: a measured neutral current far from this estimate points at
    phase-angle distortion or a measurement fault rather than plain unbalance.
    """
    ir, iy, ib = _arr(ir, iy, ib)
    re = ir - 0.5 * iy - 0.5 * ib
    im = (np.sqrt(3.0) / 2.0) * (iy - ib)
    return np.sqrt(re ** 2 + im ** 2)


def unbalance_derating_factor(unbalance_pct):
    """Multiplier on equivalent heating current to account for the extra loss
    negative-sequence current produces.

    NEMA MG-1 gives a derating curve for motors under voltage unbalance that is
    conventionally approximated by 1 + 2*(u/100)^2 in loss terms. Transformers
    are less sensitive than motors, so this is the conservative end. Treated as
    a small correction and labelled as such, never as a headline result.
    """
    u = np.asarray(unbalance_pct, float)
    return np.sqrt(1.0 + 2.0 * (u / 100.0) ** 2)


def analyse(ir, iy, ib, vr, vy, vb, rated_current_a, unbalance_limit_pct=2.0):
    """Full per-sample unbalance picture, including the load ratios the thermal
    model needs."""
    rms, mx, i_unb = equivalent_currents(ir, iy, ib)
    v_unb_nema = nema_unbalance_pct(vr, vy, vb)
    vab, vbc, vca = line_voltages_from_phase(vr, vy, vb)
    vuf = vuf_from_magnitudes(vab, vbc, vca)
    derate = unbalance_derating_factor(i_unb)

    return {
        "i_rms_a": rms,
        "i_max_a": mx,
        "current_unbalance_pct": i_unb,
        "voltage_unbalance_nema_pct": v_unb_nema,
        "voltage_unbalance_iec_vuf_pct": vuf,
        "neutral_current_estimate_a": neutral_current_estimate(ir, iy, ib),
        "K_oil": rms * derate / rated_current_a,
        "K_winding": mx * derate / rated_current_a,
        "unbalance_exceeded": vuf > unbalance_limit_pct,
    }
