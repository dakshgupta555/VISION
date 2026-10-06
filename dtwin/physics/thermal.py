"""
Thermal model of an oil-immersed distribution transformer.

Implements the exponential (clause 7) formulation of IEEE C57.91, which is the
same structure as IEC 60076-7:

    steady-state top-oil rise over ambient
        dTO_ult = dTO_rated * ((K^2 * R + 1) / (R + 1)) ^ n

    steady-state hot-spot rise over top-oil
        dHS_ult = dHS_rated * K ^ (2m)

    both approached as first-order lags with their own time constants, oil slow
    (hours) and winding fast (minutes)

        Theta_hotspot = Theta_ambient + dTO + dHS

Two load factors are carried rather than one. Total copper loss heats the oil,
so the oil follows the RMS of the three phase currents; the hot-spot sits in
the hottest winding, so it follows the worst phase. Under balanced load the two
coincide, and under unbalance the hot-spot correctly runs ahead of what a
single average current would predict.

The model is a plain recursive filter, so it runs identically on a historical
CSV and on a live sample arriving every few seconds.
"""

from dataclasses import dataclass

import numpy as np

from .nameplate import Nameplate


# --------------------------------------------------------------------------- #
# Steady-state relations
# --------------------------------------------------------------------------- #
def top_oil_rise_ultimate(K: float, np_: Nameplate) -> float:
    """Top-oil rise over ambient the transformer settles at, for load ratio K."""
    K = max(float(K), 0.0)
    ratio = (K ** 2 * np_.loss_ratio_R + 1.0) / (np_.loss_ratio_R + 1.0)
    return np_.top_oil_rise_rated_k * ratio ** np_.n_exponent


def hotspot_rise_ultimate(K: float, np_: Nameplate) -> float:
    """Hot-spot rise over top-oil the winding settles at, for load ratio K."""
    K = max(float(K), 0.0)
    return np_.hotspot_rise_rated_k * K ** (2.0 * np_.m_exponent)


def steady_state_hotspot(K: float, ambient_c: float, np_: Nameplate) -> float:
    """Hot-spot temperature once everything has settled. Used by the what-if
    panel, where the question is 'where does this end up', not 'how fast'."""
    return ambient_c + top_oil_rise_ultimate(K, np_) + hotspot_rise_ultimate(K, np_)


def load_ratio_for_hotspot(target_hotspot_c: float, ambient_c: float,
                           np_: Nameplate) -> float:
    """Inverse of the above: the largest load ratio that keeps the steady-state
    hot-spot at or below a target. This is what turns an alarm into a usable
    instruction, since it answers 'how much load must I shed'."""
    lo, hi = 0.0, 3.0
    if steady_state_hotspot(hi, ambient_c, np_) <= target_hotspot_c:
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if steady_state_hotspot(mid, ambient_c, np_) <= target_hotspot_c:
            lo = mid
        else:
            hi = mid
    return lo


# --------------------------------------------------------------------------- #
# Dynamic model
# --------------------------------------------------------------------------- #
@dataclass
class ThermalState:
    top_oil_rise_k: float
    hotspot_rise_k: float
    ambient_c: float = 30.0

    @property
    def top_oil_c(self) -> float:
        return self.ambient_c + self.top_oil_rise_k

    @property
    def hotspot_c(self) -> float:
        return self.ambient_c + self.top_oil_rise_k + self.hotspot_rise_k


class ThermalModel:
    """First-order thermal twin.

    cooling_factor scales the effective top-oil rise. 1.0 is a healthy unit;
    values above 1.0 represent a degraded cooling path, which is what a blocked
    radiator, low oil or a failed fan looks like thermally. The anomaly layer
    estimates this factor from measurements rather than being told it, and a
    drift above 1.0 is the cooling-degradation signal.
    """

    def __init__(self, nameplate: Nameplate, cooling_factor: float = 1.0):
        self.np_ = nameplate
        self.cooling_factor = float(cooling_factor)

    # ---- initialisation -----------------------------------------------------
    def initial_state(self, K: float, ambient_c: float) -> ThermalState:
        """Start from thermal equilibrium so a simulation does not open with a
        spurious warm-up transient."""
        return ThermalState(
            top_oil_rise_k=top_oil_rise_ultimate(K, self.np_) * self.cooling_factor,
            hotspot_rise_k=hotspot_rise_ultimate(K, self.np_),
            ambient_c=ambient_c)

    # ---- one step -----------------------------------------------------------
    def step(self, state: ThermalState, K_oil: float, K_winding: float,
             ambient_c: float, dt_minutes: float) -> ThermalState:
        """Advance the state by dt_minutes.

        Exact discretisation of the first-order lag, so the result does not
        depend on the sample interval. That matters because the hackathon data
        may arrive at any cadence.
        """
        tau_oil_min = self.np_.tau_oil_h * 60.0
        a_oil = 1.0 - np.exp(-dt_minutes / max(tau_oil_min, 1e-9))
        a_wdg = 1.0 - np.exp(-dt_minutes / max(self.np_.tau_winding_min, 1e-9))

        dTO_ult = top_oil_rise_ultimate(K_oil, self.np_) * self.cooling_factor
        dHS_ult = hotspot_rise_ultimate(K_winding, self.np_)

        return ThermalState(
            top_oil_rise_k=state.top_oil_rise_k + a_oil * (dTO_ult - state.top_oil_rise_k),
            hotspot_rise_k=state.hotspot_rise_k + a_wdg * (dHS_ult - state.hotspot_rise_k),
            ambient_c=ambient_c)

    # ---- vectorised run over a series --------------------------------------
    def run(self, K_oil, K_winding, ambient_c, dt_minutes,
            state: ThermalState = None):
        """Run the model across arrays and return (top_oil_c, hotspot_c).

        Used both to generate synthetic truth and, at inference time, to produce
        the predicted temperature the measured one is compared against.
        """
        K_oil = np.asarray(K_oil, float)
        K_winding = np.asarray(K_winding, float)
        ambient_c = np.asarray(ambient_c, float)
        dt = np.full(len(K_oil), float(dt_minutes)) if np.isscalar(dt_minutes) \
            else np.asarray(dt_minutes, float)

        if state is None:
            state = self.initial_state(K_oil[0], ambient_c[0])

        top_oil = np.empty(len(K_oil))
        hotspot = np.empty(len(K_oil))
        for i in range(len(K_oil)):
            state = self.step(state, K_oil[i], K_winding[i], ambient_c[i], dt[i])
            top_oil[i] = state.top_oil_c
            hotspot[i] = state.hotspot_c
        return top_oil, hotspot

    # ---- forecasting --------------------------------------------------------
    def project(self, state: ThermalState, K_oil: float, K_winding: float,
                ambient_c: float, horizon_min: float, dt_minutes: float = 1.0):
        """Project the temperature trajectory forward under a held load and
        ambient. Feeds the time-to-threshold countdown."""
        n = max(int(horizon_min / dt_minutes), 1)
        return self.run(np.full(n, K_oil), np.full(n, K_winding),
                        np.full(n, ambient_c), dt_minutes, state)

    def minutes_to_threshold(self, state: ThermalState, K_oil: float, K_winding: float,
                             ambient_c: float, threshold_c: float,
                             use: str = "top_oil", horizon_min: float = 720.0):
        """Minutes until a temperature threshold is crossed, or None if the
        steady state stays below it.

        This is the output that turns the existing fixed-threshold trip into a
        warning: the device reports that 60 C has been reached, this reports
        that it will be reached in 47 minutes.
        """
        top_oil, hotspot = self.project(state, K_oil, K_winding, ambient_c,
                                        horizon_min, dt_minutes=1.0)
        series = top_oil if use == "top_oil" else hotspot
        hit = np.where(series >= threshold_c)[0]
        if len(hit) == 0:
            return None
        return float(hit[0] + 1)
