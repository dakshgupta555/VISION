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
F_AA over time gives equivalent ageing hours; dividing by the 180 000 h normal
life gives life consumed; multiplying by replacement cost / normal life gives
its value in rupees.

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
| Thermal ageing | F_AA, instant | <= 1/8 pu (hot-spot ~91 C) | 1.0 pu (hot-spot 110 C: shed advice) | 0.30 |
| Cooling | rise-fit slope | <= 1.025 (healthy p95) | 1.06 (cooling alarm) | 0.25 |
| Loading | K, instant | <= 0.80 pu | 1.00 pu (overload alarm) | 0.20 |
| Measurement integrity | channels trusted, 1 h mean | all present channels | one channel short (sensor alarm) | 0.15 |
| Unbalance | max phase deviation from standing | <= 1.45 % (healthy p95) | 4.0 % (unbalance / CT call) | 0.10 |

Weights are a stated judgement: ageing and cooling consume insulation life;
unbalance is weighted lowest because its heating already reaches the ageing
component through the worst-phase hot-spot.

## 6. Decision outputs

- **Time to trip.** Project the twin forward from its present state, holding
  load and ambient, using its own estimate of the cooling factor (the fit
  slope when the cooling alarm stands). The **twin's limit** is the hot-spot at
  110 C; only it produces a load-shed instruction, with the shed computed as
  the largest load whose steady hot-spot stays under the limit. The existing
  **60 C oil trip** is projected too, shown as a comparison only.
- **Ambient-compensated protection.** At each ambient, the steady load a fixed
  60 C oil trip permits, a rise-over-ambient trip of 30 K permits (the
  protection the 60 C setting gives at the 30 C design ambient), and the
  hot-spot limit permits. The fixed trip permits 0.86 pu at 15 C and 0.00 pu
  at 48 C.
- **Self-test.** Inject cooling degradation of increasing severity into a fresh
  simulated record for the selected nameplate and report the smallest one the
  twin detects.

## 7. How detection is scored

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
