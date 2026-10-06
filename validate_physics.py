"""
Validation of the physics engine against closed-form expectations.

Every check compares the model against something independently known, not
against itself. This is what lets the team state that the twin is correct
rather than merely plausible, and it is the first slide a sceptical judge
should be shown.

    python validate_physics.py
"""

import numpy as np

from dtwin.physics.nameplate import Nameplate, preset
from dtwin.physics.thermal import (ThermalModel, steady_state_hotspot,
                                   top_oil_rise_ultimate, hotspot_rise_ultimate,
                                   load_ratio_for_hotspot)
from dtwin.physics import ageing as ag
from dtwin.physics import unbalance as ub

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, condition, detail=""):
    results.append((PASS if condition else FAIL, name, detail))
    print(f"  [{PASS if condition else FAIL}] {name}" + (f"   {detail}" if detail else ""))


def head(t):
    print("\n" + "=" * 76 + f"\n{t}\n" + "=" * 76)


np_ = Nameplate()

# =============================================================================
head("1. STEADY STATE AT RATED LOAD")
# =============================================================================
dTO = top_oil_rise_ultimate(1.0, np_)
dHS = hotspot_rise_ultimate(1.0, np_)
check("top-oil rise at K=1 equals the nameplate rise",
      abs(dTO - np_.top_oil_rise_rated_k) < 1e-9, f"{dTO:.3f} K")
check("hot-spot rise at K=1 equals the nameplate rise",
      abs(dHS - np_.hotspot_rise_rated_k) < 1e-9, f"{dHS:.3f} K")

hs = steady_state_hotspot(1.0, 30.0, np_)
check("hot-spot at rated load and 30 C ambient lands on the ageing reference",
      abs(hs - np_.reference_hotspot_c) < 1e-6, f"{hs:.2f} C vs reference 110 C")

check("no-load top-oil rise follows the loss-ratio relation",
      abs(top_oil_rise_ultimate(0.0, np_)
          - np_.top_oil_rise_rated_k * (1 / (np_.loss_ratio_R + 1)) ** np_.n_exponent) < 1e-9,
      f"{top_oil_rise_ultimate(0.0, np_):.3f} K at zero load")

check("hot-spot rise is zero at zero load", hotspot_rise_ultimate(0.0, np_) == 0.0)
check("temperature rises monotonically with load",
      all(steady_state_hotspot(k, 30, np_) < steady_state_hotspot(k + 0.1, 30, np_)
          for k in np.arange(0, 1.5, 0.1)))

# =============================================================================
head("2. AGEING LAW")
# =============================================================================
check("ageing factor is exactly 1.0 at the reference hot-spot",
      abs(float(ag.ageing_factor(110.0, np_)) - 1.0) < 1e-9,
      f"{float(ag.ageing_factor(110.0, np_)):.6f} pu")

f118 = float(ag.ageing_factor(118.0, np_))
check("roughly doubles for a 6-8 C rise above reference",
      1.7 < f118 < 2.5, f"{f118:.3f} pu at 118 C")

# The Arrhenius law is asymmetric about the reference by construction, so the
# halving interval below is not the mirror of the doubling interval above.
f103 = float(ag.ageing_factor(103.4, np_))
check("halves about 6-7 C below reference (asymmetric, as Arrhenius requires)",
      0.45 < f103 < 0.55, f"{f103:.3f} pu at 103.4 C")
check("ageing is negligible at normal operating temperatures",
      float(ag.ageing_factor(80.0, np_)) < 0.05,
      f"{float(ag.ageing_factor(80.0, np_)):.4f} pu at 80 C")
check("ageing is monotonic in temperature",
      all(ag.ageing_factor(t, np_) < ag.ageing_factor(t + 1, np_) for t in range(60, 140)))

hot = np.full(8760, 110.0)
loss = ag.life_consumed_pct(hot, 1.0, np_)
expected = 100.0 * 8760 / np_.normal_life_hours
check("a year at reference temperature consumes the expected share of life",
      abs(loss - expected) < 1e-6, f"{loss:.4f}% over one year")

cost = ag.life_cost_inr(np.full(24, 110.0), 1.0, np_)
check("life cost for one day at reference is positive and sane",
      0 < cost < np_.replacement_cost_inr, f"INR {cost:.2f} per day at rated duty")

# =============================================================================
head("3. DYNAMIC RESPONSE")
# =============================================================================
m = ThermalModel(np_)
st = m.initial_state(0.0, 30.0)
dt = 1.0
tau_min = np_.tau_oil_h * 60.0
start_rise = st.top_oil_rise_k
target = top_oil_rise_ultimate(1.0, np_)

s = st
for _ in range(int(tau_min)):
    s = m.step(s, 1.0, 1.0, 30.0, dt)
frac = (s.top_oil_rise_k - start_rise) / (target - start_rise)
check("one oil time constant reaches 63.2% of the step",
      abs(frac - 0.632) < 0.01, f"{100 * frac:.1f}% after {np_.tau_oil_h} h")

s = st
for _ in range(int(5 * tau_min)):
    s = m.step(s, 1.0, 1.0, 30.0, dt)
span = target - start_rise
check("five time constants leave exactly the expected exp(-5) residual",
      abs((30.0 + target - s.top_oil_c) - span * np.exp(-5)) < 0.01,
      f"{s.top_oil_c:.2f} C, residual {30.0 + target - s.top_oil_c:.3f} K")
s = st
for _ in range(int(8 * tau_min)):
    s = m.step(s, 1.0, 1.0, 30.0, dt)
check("eight time constants settle on the steady state",
      abs(s.top_oil_c - (30.0 + target)) < 0.05, f"{s.top_oil_c:.2f} C")

# the result must not depend on how finely the model is stepped
ends = []
for step_min in [0.5, 1.0, 5.0, 15.0, 30.0]:
    s = m.initial_state(0.3, 30.0)
    for _ in range(int(600 / step_min)):
        s = m.step(s, 1.0, 1.0, 30.0, step_min)
    ends.append(s.top_oil_c)
check("result is independent of sample interval (0.5 to 30 min)",
      max(ends) - min(ends) < 0.05, f"spread {max(ends) - min(ends):.4f} C")

# =============================================================================
head("4. TIME TO THRESHOLD, AND THE LOAD-SHED ANSWER")
# =============================================================================
s = m.initial_state(0.35, 24.0)
mins = m.minutes_to_threshold(s, 1.20, 1.20, 30.0, np_.trip_oil_temp_c)
check("a 1.2 pu step from light load reaches the 60 C trip in a sensible time",
      mins is not None and 10 < mins < 600, f"{mins:.0f} minutes" if mins else "never")
check("the countdown lengthens when the starting load is lower",
      m.minutes_to_threshold(m.initial_state(0.20, 24.0), 1.20, 1.20, 30.0,
                             np_.trip_oil_temp_c) > mins)

s2 = m.initial_state(0.4, 28.0)
never = m.minutes_to_threshold(s2, 0.45, 0.45, 28.0, np_.trip_oil_temp_c)
check("light load at mild ambient never reaches the trip", never is None)

k_safe = load_ratio_for_hotspot(np_.reference_hotspot_c, 45.0, np_)
check("maximum safe load at 45 C ambient is below rated",
      0.5 < k_safe < 1.0, f"K = {k_safe:.3f} to hold 110 C hot-spot at 45 C ambient")
check("maximum safe load is higher on a cold day",
      load_ratio_for_hotspot(np_.reference_hotspot_c, 15.0, np_) > k_safe,
      f"K = {load_ratio_for_hotspot(np_.reference_hotspot_c, 15.0, np_):.3f} at 15 C")

# =============================================================================
head("4b. FINDING - A FIXED OIL-TEMPERATURE TRIP IS AMBIENT-BLIND")
# =============================================================================
print("  Load a 60 C absolute top-oil trip permits, by ambient temperature:\n")
print(f"    {'ambient':>9}  {'max load pu':>12}  {'max kVA':>9}")
allowed = {}
for amb in [15, 25, 30, 35, 40, 42, 45, 48]:
    lo, hi = 0.0, 2.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if amb + top_oil_rise_ultimate(mid, np_) <= np_.trip_oil_temp_c:
            lo = mid
        else:
            hi = mid
    allowed[amb] = lo
    print(f"    {amb:7d} C  {lo:12.3f}  {lo * np_.rating_kva:9.1f}")
print()
check("the permitted load collapses as ambient rises",
      allowed[15] > 4 * allowed[45],
      f"{allowed[15]:.2f} pu at 15 C against {allowed[45]:.2f} pu at 45 C")
check("at 48 C ambient the trip fires with no load at all",
      allowed[48] < 0.01,
      "an absolute threshold cannot distinguish a hot day from a hot transformer")
print("  This is a design finding, not a model error. An absolute oil-temperature")
print("  threshold carries the ambient inside it, so in a Rajasthan summer the same")
print("  setting that protects the unit in January nuisance-trips in May. The twin's")
print("  recommendation is to trip on top-oil RISE over measured ambient, or on")
print("  hot-spot, both of which this engine computes directly.")

# =============================================================================
head("5. UNBALANCE")
# =============================================================================
check("balanced currents give zero unbalance",
      float(ub.nema_unbalance_pct([100], [100], [100])[0]) < 1e-9)
val = float(ub.nema_unbalance_pct([105], [100], [95])[0])
check("NEMA unbalance matches the hand calculation", abs(val - 5.0) < 1e-6, f"{val:.3f}%")

vuf_bal = float(ub.vuf_from_magnitudes([415], [415], [415])[0])
check("VUF is zero for balanced line voltages", vuf_bal < 1e-6, f"{vuf_bal:.6f}%")
vuf = float(ub.vuf_from_magnitudes([415], [405], [400])[0])
check("VUF is a small positive percentage for mild unbalance",
      0.5 < vuf < 5.0, f"{vuf:.3f}%")

rms, mx, unb = ub.equivalent_currents([120], [100], [80])
check("RMS current sits between the mean and the maximum",
      100.0 < float(rms[0]) < 120.0, f"RMS {float(rms[0]):.2f} A, max {float(mx[0]):.2f} A")
check("neutral current is near zero when balanced",
      float(ub.neutral_current_estimate([100], [100], [100])[0]) < 1e-6)
check("neutral current is substantial when badly unbalanced",
      float(ub.neutral_current_estimate([120], [100], [80])[0]) > 20,
      f"{float(ub.neutral_current_estimate([120], [100], [80])[0]):.1f} A")

a = ub.analyse([120], [100], [80], [250], [250], [250], 133.3)
check("unbalanced load makes the hot-spot load ratio exceed the oil load ratio",
      a["K_winding"][0] > a["K_oil"][0],
      f"K_oil {a['K_oil'][0]:.3f} vs K_winding {a['K_winding'][0]:.3f}")

# =============================================================================
head("6. NAMEPLATE SCALING")
# =============================================================================
for kva in [25, 63, 100, 250, 630]:
    p = preset(kva)
    i = p.rated_current_a
    ok = abs(i - kva * 1000 / (3 ** 0.5 * p.lv_v)) < 1e-6
    check(f"{kva} kVA rated current computed correctly", ok, f"{i:.1f} A")
check("nameplate self-check reports no issues on defaults",
      len(Nameplate().self_check()) == 0, str(Nameplate().self_check()))

# =============================================================================
head("SUMMARY")
# =============================================================================
npass = sum(1 for r in results if r[0] == PASS)
print(f"  {npass} of {len(results)} checks passed")
for status, name, detail in results:
    if status == FAIL:
        print(f"  FAILED: {name}  {detail}")
