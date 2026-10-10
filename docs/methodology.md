# Methodology

Physics first; statistics only for the residual. Every number the twin shows
is either a standard calculation a reviewer can check by hand or a measured
result with its test named.

## 1. Thermal model — IEEE C57.91 clause 7 (same structure as IEC 60076-7)

Steady-state rises for load ratio K:

    top-oil rise over ambient   dTO_u = dTO_R * ((K^2 * R + 1) / (R + 1)) ^ n
    hot-spot rise over top oil  dHS_u = dHS_R * K ^ (2m)

Each approaches its steady state as a first-order lag, oil slow and winding
fast, discretised exactly so the result does not depend on sample interval:

    dTO(t+dt) = dTO(t) + (1 - exp(-dt / tau_oil))     * (dTO_u - dTO(t))
    dHS(t+dt) = dHS(t) + (1 - exp(-dt / tau_winding)) * (dHS_u - dHS(t))
    hot-spot  = ambient + dTO + dHS

| Constant | Value | Source |
|---|---|---|
| dTO_R, top-oil rise at rated | 55 K | typical ONAN distribution unit |
| dHS_R, hot-spot rise at rated | 25 K | " |
| R, load loss / no-load loss | 5.0 | " |
| n, m | 0.8, 0.8 | C57.91 table for ONAN |
| tau_oil | 3 h (2.5 h <= 63 kVA, 3.5 h > 250 kVA) | scaled with oil volume |
| tau_winding | 7 min | |

These are self-consistent: at rated load and 30 C ambient the hot-spot lands
exactly on the 110 C ageing reference.

**Two load ratios.** Total copper loss heats the oil, so the oil follows the
RMS of the three phase currents. The hot-spot sits in the hottest winding, so
it follows the worst phase. Under unbalance the hot-spot correctly runs ahead
of a three-phase average.

**Hot-spot shown on the dashboard** = measured top oil + modelled winding
gradient (dHS) when the oil probe is trusted. Under a cooling fault the
measured oil already carries the extra rise; a purely predicted hot-spot would
miss it.

## 2. Ageing — IEEE C57.91 clause 5

Thermally upgraded paper:

    F_AA = exp(15000 / 383 - 15000 / (theta_HS + 273))

F_AA = 1.0 at 110 C. It halves at 103.3 C and doubles at 116.9 C: asymmetric,
which is why a few hot hours matter more than many cool ones. Integrating
F_AA over time gives equivalent ageing hours; dividing by the 1,80,000 h normal
life gives life consumed; multiplying by replacement cost / normal life gives
its value in rupees:

    aged_h  = sum over samples of F_AA x dt                (dt in hours)
    cost    = aged_h x (replacement cost / normal life)
            = aged_h x (Rs 4,50,000 assumed, illustrative / 1,80,000 h, IEEE C57.91)
            = aged_h x Rs 2.50                             (100 kVA; cost scales at Rs 4,500 per kVA)

The replacement cost is OUR assumption, not a C57.91 figure; only the normal
life is from the standard.

**Physical limit.** Above a hot-spot of 140 C (IEC 60076-7 long-time
emergency limit for distribution transformers, `config/components.yaml`
`winding.hotspot_emergency_c`) protection would trip and the unit would be
de-energised. Samples above it add **no** aged hours and no cost, and the
dashboard says "Beyond IEC emergency limit — protection would trip; model
extrapolating". Life used therefore cannot exceed what the asset could
physically experience; the hours not counted are reported (`beyond_trip_h`).
The C57.91 equations themselves are unchanged and still extrapolate above 140 C
for the projection and what-if, where the result is labelled the same way.

## 3. Unbalance from magnitudes only

The CPRI device records magnitudes, not angles. NEMA unbalance is the largest
deviation from the mean over the mean. The IEC voltage unbalance factor is
recovered exactly from three line-voltage magnitudes by the standard beta
relation. Unbalance enters the thermal model through the worst-phase load
ratio.

## 4. Measurement validation

### Per channel
- **Dead**: missing, negative, out of physical range, or a zero current while
  the other phases carry load.
- **Stuck**: a run of identical readings longer than chance allows. The run
  length comes from how often the channel repeats anyway, so a coarse logger
  is not flagged for its quantisation.
- **Step**: a first difference, detrended against its local median, beyond 6
  robust sd. Oil temperature cannot jump between samples.

### Cross-phase
Each phase against the mean of the other two, normalised by the standing
pattern of the first three days. A lone current deviating by more than 4%
while the other two agree within 2% and its phase voltage moves less than
0.6% is the **CT**. A deviation spread across phases, or one the voltage
corroborates, is **real unbalance**. Calls must persist for 1 h. A failed
phase current is rebuilt from the others before the thermal model uses it.

### Thermal residual and the slope/intercept discriminator
Run the twin on the trusted inputs. Over a trailing 24 h window, evaluated
hourly, fit with a Huber loss (so a few outliers cannot drag it):

    measured_rise = a * predicted_rise + b + c * t        (t in days, 0 at window end)

| Case | Signature | Call |
|---|---|---|
| healthy | a ~ 1, b ~ 0 | ok |
| cooling path degraded (fouled radiator, low oil, failed fan) | a > 1.06 | **asset** |
| temperature instrument offset or drift | a ~ 1, abs(b) > 2 K | **sensor** |
| a < 0.94 | | thermal mismatch (nameplate or probe placement) |

A degraded cooling path **scales** the rise with load; a drifting probe
**shifts** every reading by the same amount. Slope says the transformer
changed; intercept says the instrument did. Each call must hold for three
consecutive fits.

Two refinements, each from a measured failure:
- **The time term `c`.** Without it, a probe drifting 2 K/day inside a rolling
  window aliases into the slope through the diurnal cycle (1.12 at midnight,
  0.90 at noon) and reads as a cooling fault. With it, drift lands in `c` and
  `b`. `check_time_term.py` proves it does not absorb real cooling faults at
  24, 96 and 240 h ramps.
- **Window restart on a step.** A temperature step the oil cannot make means
  the instrument changed state; data before it is discarded.

Healthy residual sd is 0.45 K; a cooling fault at full severity sits 18.8 sd
away.

Tested and rejected (do not retry): residual/predicted-rise ratio CV and
night-time residual as discriminators (do not separate); an independent
"report both" slope/intercept classifier (under an asset fault the intercept
carries up to 12 K of model-form error); a windowed step test and a
split-window step scan (failed pre-registered rules). See `results.md`.

## 5. Health index

The brief asks for a 0-100 index. It is a weighted mean of five physical
components, each displayed with its own value and unit. One definition of
"bad" across the system: **each component is scored on the same quantity, at
the same instant, that its own alarm fires on, and scores 0 exactly where
that alarm fires.** "100" comes from healthy behaviour (p95 of the healthy
record) where the quantity has scatter, and from physics where it does not.
Linear between the anchors; ageing on log2 because it doubles every ~7 K.

| Component | Quantity | 100 at | 0 at (alarm fires) | Weight |
|---|---|---|---|---|
| Thermal ageing | F_AA, instant, log2 | <= 0.5 (hot-spot 103.3 C, half the rated wear rate) | 2.71 (hot-spot 120 C: the hot-spot alarm) | 0.30 |
| Cooling | rise-fit slope | <= 1.025 (healthy p95) | 1.06 (cooling alarm) | 0.25 |
| Loading | K, instant, piecewise linear | <= 0.90 pu | 1.50 pu; 50 at 1.00 pu, where the overload alarm fires | 0.20 |
| Measurement integrity | channels trusted, 1 h mean | all present channels | one channel short (sensor alarm) | 0.15 |
| Unbalance | max phase deviation from standing | <= 1.45 % (healthy p95) | 4.0 % (unbalance / CT call) | 0.10 |

Weights are a stated judgement: ageing and cooling consume insulation life;
unbalance is weighted lowest because its heating already reaches the ageing
component through the worst-phase hot-spot.

    score_i = clip(100 x (bad_i - x_i) / (bad_i - good_i), 0, 100)   (x = log2 F_AA for ageing)
    mean    = sum(w_i score_i) / sum(w_i)       over components with a value
    index   = min(mean, min(score_ageing, score_loading) + 10)

**Cap.** A weighted mean alone cannot fall below 50 while cooling, integrity
and unbalance stay at 100, even with a 200 C hot-spot. So the index may not
exceed the worse of the two safety-critical scores, ageing and loading, by
more than 10. Measured on every scenario: the cap never binds on
`healthy.csv`; it binds on 3.9 % of `summer_overload.csv` samples. Cooling is
deliberately NOT in the cap: the healthy slope's scatter reaches 1.058 against
the 1.06 alarm, and including it turned the index red on 0.4 % of healthy
samples. Each component is still shown on its own, and the dashboard says
when the cap holds the index down.

Ageing and loading anchors were set by team decision on 2026-10-11 (results.md,
"Health index by load"): ageing reaches 0 at the 120 C hot-spot alarm rather
than at the 110 C ageing reference, and loading is 50 at rated load, so a unit
at exactly rated load and 30 C ambient reads 60 (amber), not 10.

## 6. Decision outputs

- **Time to trip.** Project the twin forward from its present state, holding
  load and ambient, using its own estimate of the cooling factor (the fit
  slope when the cooling alarm stands). The **twin's limit** is the hot-spot at
  110 C; only it produces a load-shed instruction, with the shed computed as
  the largest load whose steady hot-spot stays under the limit:

      safe K = max K such that  ambient + cf x dTO_u(K) + dHS_u(K x K_w/K_o) <= 110 C   (bisection)
      shed % = 100 x (1 - safe K / K now)

  An overload alarm and this projection produce ONE action ("Shed X% of load
  to bring the hot-spot back under 110 C"), with the settling temperature on
  its own line ("If nothing changes it settles at Y C"). The existing
  **60 C oil trip** is projected too, shown as a comparison only.
- **Ambient-compensated protection.** At each ambient, the steady load a fixed
  60 C oil trip permits, a rise-over-ambient trip of 30 K permits (the
  protection the 60 C setting gives at the 30 C design ambient), and the
  hot-spot limit permits. The fixed trip permits 0.86 pu at 15 C and 0.00 pu
  at 48 C.
- **Self-test.** Inject cooling degradation of increasing severity into a fresh
  simulated record for the selected nameplate and report the smallest one the
  twin detects.

## 7. Equivalent circuit (dtwin/physics/circuit.py)

Per-phase T circuit referred to HV, three-phase unit on its star equivalent
(V_ph = V_LL / sqrt3, a = V1_ph / V2_ph, impedances referred by a^2):

    V1 --R1--jX1--+--R2'--jX2'-- a V2,   magnetising branch Rc || jXm at E1

From nameplate percentages (illustrative unless test data are given):

    Z_base = V1_ph^2 / (S / 3);  Zeq = z% x Z_base;  Req = (P_cu / 3) / I1^2;  Xeq = sqrt(Zeq^2 - Req^2)
    Rc = V1_ph^2 / (P_core / 3);  Ic = V1_ph / Rc;  Im = sqrt(I0^2 - Ic^2);  Xm = V1_ph / Im

Open-circuit test (LV or HV side): Rc = V^2 / P0, Ic = P0 / V, Im = sqrt(I0^2 - Ic^2), Xm = V / Im.
Short-circuit test: Req = Psc / I^2, Zeq = Vsc / I, Xeq = sqrt(Zeq^2 - Req^2).
Solved with phasors; losses P_cu = 3 |I1|^2 R1 + 3 |I2|^2 R2, P_core = 3 |E1|^2 / Rc;
efficiency = P_out / (P_out + P_cu + P_core); regulation = (V2_no-load - V2) / V2;
maximum efficiency at K = sqrt(P_core / P_cu,rated).

Link to the thermal model: rated core loss = rated copper loss / R, so
(P_cu + P_core) / rated total = (K^2 R + 1) / (R + 1), exactly the C57.91 term
(`check_circuit.py` tests it). Thermal RC of the oil: Rth = rated top-oil rise
/ rated total loss, Cth = tau_oil / Rth, rise_ult = cf x rise_R x
(P_total / P_total,R)^n. The Simulate page runs the what-if through this
loss-driven form.

## 8. Magnetic characteristics (dtwin/physics/magnetic.py)

    B_max = V / (4.44 f N1 A_core)
    i_m(t) = [H(B(t)) l_core + B(t) g / mu0] / N1,   I_m = RMS(i_m),   I_c = V / Rc,   I0 = sqrt(I_m^2 + I_c^2)

B-H curve of CRGO steel, N1, A_core, l_core and the joint gap g are
ILLUSTRATIVE. Hysteresis is not modelled.

## 9. Winding temperature field (dtwin/physics/fem.py)

Steady conduction, 2D axisymmetric (r, z), one phase leg, P1 triangles (scikit-fem):

    -div(k grad T) = q,     q = per-phase I^2 R / block volume
    -k dT/dn = h (T - T_oil(z)) on every face touching oil,  T_oil linear from bottom to top oil
    homogenised blocks:  k_z = sum(f_i k_i),  k_r = 1 / sum(f_i / k_i)

h is calibrated once so the rated FEM winding-to-oil gradient equals the rated
gradient; nothing else is fitted. No oil flow (no CFD); geometry and materials
are illustrative (`config/fem.yaml`, each value sourced). The FEM never drives
an alarm; C57.91 does (`check_fem.py`).

## 10. How detection is scored

`evaluate.py` is the only reader of the truth files. Per fault event:
detected (the correct diagnosis appeared), **severity at detection** as a
share of the severity the fault eventually reached (primary, because it
transfers to other ramp rates; hours do not), residual at detection in units
of the 0.45 K healthy sd, latency in hours (secondary), diagnosis accuracy over
post-detection samples, and wrong-family alarms. False alarms are counted only
on genuinely clean samples; alarms continuous with a fault that has just
cleared are reported separately as alarm-clear latency, a property of the
trailing window.

Detector thresholds were frozen at commit `370b0b3` and scored once on a
held-out set (`data/heldout/`, seeds never used in development). Changes
after that were pre-registered with a pass rule before being run
(`results/prereg_split_step.md`).
