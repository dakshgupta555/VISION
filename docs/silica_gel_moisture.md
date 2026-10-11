# Silica-gel breather and moisture: what is modelled, what is not

## What the application does

- The breather is drawn in the 3D model on the conservator breathing path.
- Its condition (Effective / Partially exhausted / Saturated / Unknown) is a
  **user-set scenario or inspection entry**. Default is Unknown. It resets on
  reload. There is no breather, humidity or moisture sensor in this system.
- Colours are an **illustrative indicator convention** in `config/breather.yaml`
  and are editable. Real gels differ by formulation.
- The condition feeds only a qualitative chain: breather effectiveness
  (Full / Reduced / Ineffective) and moisture-ingress risk (Low / Elevated / High).
  Both are categories, not percentages.
- Oil and paper moisture are shown as
  "not calculated: requires moisture-in-oil or paper-moisture data".
- Hot-spot, top-oil temperature, ageing factor and the health index (weights,
  scores, cap) are **not** functions of the gel condition. `check_breather.py`
  asserts that every engine output is identical for all four states.

## Why there is no quantitative moisture coupling (M4)

The brief allows a quantitative coupling only if a published relationship
between paper moisture and ageing rate is found and its values are read in the
session. Outcome of the attempt (2026-10-11):

- A search found abstract-level reports that the Arrhenius pre-exponential
  (ageing) constant depends on cellulose moisture, with a reported factor of
  about 2.5 on insulation life between roughly 1 % and 4 % moisture, and a
  secondhand claim of a factor 20 at 2 %. The full text with the equation and
  coefficients is restricted (IEEE TDEI, DOI 10.1109/TDEI.2019.008184), so the
  values could not be read and nothing was coded from them.
- IEC 60076-7 and IEEE C57.91 are paywalled and were not read here.
- Even with a published moisture-to-ageing law, it takes **paper moisture
  content** as input. Nothing connects a silica-gel state to paper moisture
  without moisture-in-oil measurements, oil temperature history and a
  moisture-equilibrium model. That step has no defensible mapping here.

## What would be needed to turn it on

1. A moisture-in-oil sensor or periodic oil sample (Karl Fischer), or a paper
   moisture test.
2. A moisture-equilibrium relation between oil and paper (temperature
   dependent), taken from a source that can be read and cited.
3. A published moisture-dependent ageing relation, with its validity range.
4. A flag, off by default, with the equation, citation and substituted values
   shown in the explanation panel, and the byte-identical regression kept green
   with the flag off.
