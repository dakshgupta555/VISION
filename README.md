# Digital Twin of a Distribution Transformer — physics core

Day 1 foundation: the thermal, ageing and unbalance engines, plus a synthetic
data generator with ground-truth fault labels.

## Run

    python validate_physics.py       # 38 checks against closed-form expectations
    python check_detectability.py    # proves the fault signal is present
    python -c "from dtwin.simulate.generator import quick; quick('showcase')"

## Layout

    dtwin/physics/nameplate.py   transformer spec, any rating
    dtwin/physics/thermal.py     IEEE C57.91 top-oil and hot-spot model
    dtwin/physics/ageing.py      ageing factor, life consumed, life cost
    dtwin/physics/unbalance.py   NEMA and IEC unbalance, equivalent currents
    dtwin/simulate/faults.py     injectable faults with ground truth
    dtwin/simulate/generator.py  SCADA-style data generator
    data/                        seven pre-generated scenarios

## Key results established today

| | |
|---|---|
| Physics checks passing | 38 of 38 |
| Healthy thermal residual | sd 0.45 K |
| Cooling fault separation | 18.8 sigma |
| Sensor vs asset discriminator | slope > 1 = asset, intercept > 0 = sensor |
| A fixed 60 C oil trip at 45 C ambient | permits only 0.19 pu load |
