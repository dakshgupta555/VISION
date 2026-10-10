# Digital twin audit

State of the system before the core-twin phase (tag `pre-judges-feedback`,
commit `7519646`), and what each field on screen actually rests on.

## What exists

| Area | Module | What it does |
|---|---|---|
| Nameplate | `dtwin/physics/nameplate.py` | ratings, thermal constants, protection settings, `phases` (3 by default, 1 supported for rated current) |
| Thermal | `dtwin/physics/thermal.py` | IEEE C57.91 clause 7 top-oil and hot-spot first-order model, steady state, time to threshold |
| Ageing | `dtwin/physics/ageing.py` | F_AA, equivalent ageing hours, life consumed, rupees |
| Unbalance | `dtwin/physics/unbalance.py` | NEMA unbalance, IEC VUF from magnitudes, RMS and worst-phase load ratios |
| Simulator | `dtwin/simulate/` | labelled synthetic records with injected asset and sensor faults |
| Ingestion | `dtwin/ingest.py` | any CSV or Excel workbook to eight channels plus a mapping report |
| Validation | `dtwin/analytics/validation.py` | per-channel, cross-phase and thermal-fit checks; asset vs sensor |
| Engine and API | `dtwin/api/` | state as of a replay cursor, what-if, PDF, notifications, self-test |
| Notifications | `dtwin/notify/` | tiers, debounce, outbox/SMTP/GPRS, replay guard, demo button |
| Dashboard | `dashboard/` | React replay dashboard (six zones and the Day 4 features) |

Not present: an equivalent circuit, a live stream, a 3D model, a simulation
workspace separate from observed data.

## Fields: measured, simulated, unavailable

**Measured** (from the file or, later, the live stream): IR, IY, IB (A);
VR, VY, VB (V, line-to-neutral); top-oil temperature (C); ambient
temperature (C).

**Computed from measurements by a standard** (exact given the nameplate):
RMS and worst-phase load ratio, current and voltage unbalance, ageing factor
and life consumed, the fixed-trip comparison.

**Modelled** (depend on nameplate constants that are typical, not measured for
this unit): predicted top oil, hot-spot (measured oil + modelled winding
gradient), time to threshold, load-shed advice, the rise-fit slope and
intercept as diagnostics.

**Simulated** (synthetic, never observed): every file in `data/` and
`data/heldout/`; the live simulator stream to be added in this phase.

**Unavailable** (no channel exists; must never be shown as healthy):
dissolved gas, moisture, oil level, oil pressure, vibration, partial
discharge, bushing condition, breather silica-gel condition, tap position,
HV-side voltage and current, winding temperature by direct measurement, tank
surface temperature, earthing resistance.

## Run commands

    uvicorn dtwin.api.app:app --port 8000                 # backend
    cd dashboard && npx vite                              # dashboard, :5173
    python validate_physics.py                            # 38 of 38
    python check_ingest.py                                # 31 of 31
    python check_notify.py                                # 14 of 14
    python evaluate.py --set heldout                      # must equal results/heldout_detector_370b0b3.txt
    cd dashboard && node scripts/shot.mjs --t 2200        # screenshot + console errors
