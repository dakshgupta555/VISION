"""
Fault injection with known ground truth.

Each fault is a dataclass describing what goes wrong, when, and how severely.
The generator applies them and records a label per sample, so detection rate,
false-alarm rate and detection latency can all be measured rather than asserted.

Two families, and keeping them separate is the point:

  ASSET faults   the transformer itself is in trouble - cooling degradation,
                 overload, growing unbalance, oil loss. The measurements are
                 telling the truth.

  SENSOR faults  the transformer is fine but an instrument is lying - drift,
                 stuck channel, dead channel, bias. Acting on these is a false
                 maintenance call, which costs real money.

A monitoring system that cannot tell the two apart will send a crew to site for
a faulty thermocouple. Distinguishing them is the headline claim.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

FaultFamily = Literal["asset", "sensor"]


@dataclass
class Fault:
    kind: str
    family: FaultFamily
    start_h: float
    end_h: Optional[float] = None       # None means it persists to the end
    severity: float = 1.0               # meaning depends on kind, see below
    channel: Optional[str] = None       # which signal, for sensor faults
    ramp_h: float = 0.0                 # 0 = step, >0 = linear onset over this many hours
    label: str = ""

    def active(self, t_h: float) -> bool:
        if t_h < self.start_h:
            return False
        return self.end_h is None or t_h <= self.end_h

    def progress(self, t_h: float) -> float:
        """0 to 1 severity envelope, so a gradual fault really is gradual."""
        if not self.active(t_h):
            return 0.0
        if self.ramp_h <= 0:
            return 1.0
        return min((t_h - self.start_h) / self.ramp_h, 1.0)

    def scale(self, t_h: float) -> float:
        return self.severity * self.progress(t_h)


# --------------------------------------------------------------------------- #
# Constructors for the scenarios worth demonstrating
# --------------------------------------------------------------------------- #
def cooling_degradation(start_h, severity=0.35, ramp_h=72, end_h=None):
    """Radiator fouling, low oil, or a failed fan.

    severity is the fractional increase in effective top-oil rise, so 0.35 means
    the oil runs 35% hotter for the same load. This is the flagship case: at
    moderate load the absolute temperature stays under the 60 C trip, so a
    threshold device sees nothing, while the twin's residual climbs steadily.
    """
    return Fault("cooling_degradation", "asset", start_h, end_h, severity,
                 ramp_h=ramp_h, label="Cooling path degrading")


def overload(start_h, end_h, severity=1.35):
    """severity is the load ratio applied during the window."""
    return Fault("overload", "asset", start_h, end_h, severity,
                 label="Sustained overload")


def unbalance_growth(start_h, severity=0.18, ramp_h=96, end_h=None, channel="IY"):
    """One phase progressively loaded differently - a shifting single-phase mix
    or a developing connection problem. severity is the fractional offset."""
    return Fault("unbalance_growth", "asset", start_h, end_h, severity,
                 channel=channel, ramp_h=ramp_h, label="Phase unbalance growing")


def oil_loss(start_h, severity=0.30, ramp_h=120, end_h=None):
    """Falling oil level: less thermal mass, so the unit heats faster and higher.
    Shortens the oil time constant as well as raising the rise."""
    return Fault("oil_loss", "asset", start_h, end_h, severity,
                 ramp_h=ramp_h, label="Oil level falling")


def sensor_drift(start_h, channel="oil_temp", severity=6.0, ramp_h=120, end_h=None):
    """Calibration drift. severity is the terminal offset in the channel's own
    units. Slow drift is the hardest sensor fault to catch, which is why the
    detection-limit figure matters."""
    return Fault("sensor_drift", "sensor", start_h, end_h, severity,
                 channel=channel, ramp_h=ramp_h, label=f"{channel} drifting")


def sensor_stuck(start_h, end_h, channel="oil_temp"):
    """Channel freezes at its last value. Looks perfectly plausible in isolation
    and is only visible against the other channels."""
    return Fault("sensor_stuck", "sensor", start_h, end_h, 1.0,
                 channel=channel, label=f"{channel} frozen")


def sensor_dead(start_h, end_h, channel="IB"):
    """Channel reads zero or goes missing."""
    return Fault("sensor_dead", "sensor", start_h, end_h, 1.0,
                 channel=channel, label=f"{channel} dead")


def sensor_bias(start_h, channel="ambient_temp", severity=-4.0, end_h=None):
    """Step offset, for instance a solar-exposed ambient probe reading high or
    a badly sited one reading low."""
    return Fault("sensor_bias", "sensor", start_h, end_h, severity,
                 channel=channel, label=f"{channel} offset")


# --------------------------------------------------------------------------- #
# Ready-made scenarios
# --------------------------------------------------------------------------- #
SCENARIOS = {
    "healthy": [],

    "cooling_fault": [cooling_degradation(start_h=14 * 24, severity=0.40, ramp_h=96)],

    "summer_overload": [overload(start_h=10 * 24 + 13, end_h=10 * 24 + 19, severity=1.30),
                        overload(start_h=11 * 24 + 13, end_h=11 * 24 + 20, severity=1.38),
                        overload(start_h=12 * 24 + 14, end_h=12 * 24 + 20, severity=1.45)],

    "sensor_trouble": [sensor_drift(start_h=8 * 24, channel="oil_temp",
                                    severity=7.0, ramp_h=120),
                       sensor_stuck(start_h=20 * 24, end_h=20 * 24 + 9,
                                    channel="IY"),
                       sensor_dead(start_h=25 * 24 + 2, end_h=25 * 24 + 6,
                                   channel="IB")],

    "unbalance": [unbalance_growth(start_h=6 * 24, severity=0.22, ramp_h=144)],

    "oil_loss": [oil_loss(start_h=12 * 24, severity=0.35, ramp_h=168)],

    # The demo scenario, written as a narrative over 30 days.
    #
    # Faults are deliberately separated in time so that roughly half the record
    # is clean. That matters for two reasons: a false-alarm rate can only be
    # measured against clean baseline, and overlapping faults would compound
    # into temperatures no real transformer would survive, which would make the
    # demo look synthetic.
    #
    # Each fault also ends, which gives the dashboard a recovery to show. The
    # cooling fault in particular is detected, then cleared by maintenance, and
    # the residual returns to normal - the clearest possible illustration of
    # what a twin does that a threshold cannot.
    "showcase": [
        # d3-d5   ambient probe reads low, then is re-sited
        sensor_bias(start_h=3 * 24, channel="ambient_temp", severity=-3.5,
                    end_h=5 * 24),
        # d7-d11  one phase drifts out of balance, then is rebalanced
        unbalance_growth(start_h=7 * 24, end_h=11 * 24, severity=0.08, ramp_h=72),
        # d12-d13 two evening overload peaks
        overload(start_h=12 * 24 + 15, end_h=12 * 24 + 19, severity=1.18),
        overload(start_h=13 * 24 + 15, end_h=13 * 24 + 20, severity=1.22),
        # d15-d18 oil temperature probe drifts, then is recalibrated
        sensor_drift(start_h=15 * 24, end_h=18 * 24, channel="oil_temp",
                     severity=5.5, ramp_h=60),
        # d20-d26 cooling path fouls progressively, then is cleaned
        cooling_degradation(start_h=20 * 24, end_h=26 * 24, severity=0.30,
                            ramp_h=96),
        # d22     a current channel freezes for part of a shift
        sensor_stuck(start_h=22 * 24 + 9, end_h=22 * 24 + 14, channel="IY"),
        # d27     a current channel drops out entirely
        sensor_dead(start_h=27 * 24 + 2, end_h=27 * 24 + 5, channel="IB"),
    ],
}
