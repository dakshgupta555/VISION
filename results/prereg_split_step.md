# Pre-registration: split-window step test

Written and committed before the test is implemented or run.

## Method
Inside each rolling thermal fit window (after any existing reset), scan split
points tau at least 3 h from either end and fit by OLS

    measured_rise = a*predicted_rise + b + c*t + S*[t > tau]

The best tau is the one that maximises |S|/SE(S). A step is declared when
|S| >= 1.5 K **and** |S|/SE(S) >= 6. The fit window then restarts at tau,
exactly as a single-difference step does now. Thresholds stay fixed after this
file is committed.

## Development set C (seeds 4701+, not used before)
- 24 ambient bias cases: size {2.0, 2.5, 3.0, 3.5} K x sign {+,-} x cadence {5, 15, 30} min
- 6 healthy cases across seasons and cadences
- 6 asset cases: cooling 24 h and 96 h ramps, oil loss, overload, unbalance, cooling at 30 min

Baseline is the frozen detector 370b0b3 run on the same set.

## PASS only if all four hold
1. Wrong-family cooling alarms (thermal asset_cooling on rows with no asset
   fault in truth) in the bias cases fall by at least 50% against baseline.
2. Healthy cases: false-alarm samples do not increase.
3. Asset cases: every event still detected, median severity-at-detection for
   thermal faults no more than 5 points worse, wrong-family not increased.
4. In-sample set: 18/18 detected, 0.00% false alarms, wrong-family no more
   than +0.1 pp; check_time_term.py passes.

PASS -> freeze, commit, generate a fresh held-out set (seeds 5001+, at least
two overlap cases), score once.
FAIL -> keep 6-sigma single-difference; docs/results.md states that a missed step
can cause a wrong-family alarm.

## Outcome — FAIL (rule 4)

| Rule | Baseline 370b0b3 | Split test | Result |
|---|---|---|---|
| 1. Wrong-family cooling samples, bias cases | 630 | 216 (-66%) | PASS |
| 2. Healthy false-alarm samples | 4 | 4 | PASS |
| 3. Asset cases: detected / sev@det median / wrong-family | 6/6, 40.3%, 0 | 6/6, 40.3%, 0 | PASS |
| 4. In-sample false alarms | 0.00% | 0.16% (1 episode) | **FAIL** |

The failing episode is on showcase: restarting the window after the day-27
cooling clean-out and after the ambient-bias end breaks a continuous
alarm-clear run, and the re-fire is then counted as a false alarm. Side effect
worth knowing: maximum alarm-clear latency fell from 23.8 h to 4.8 h.

Per this file, the test is disabled (`split_test=False`), leaving the detector
behaviour identical to 370b0b3. Re-enabling it needs a new pre-registration
and a fresh held-out set.
