# Digital Twin of a Distribution Transformer

Condition monitoring and decision support for a distribution transformer from
the signals CPRI's existing monitoring device already records: three phase
currents, three phase voltages, top-oil and ambient temperature. Nothing else.

> A threshold tells you a limit has already been crossed. The twin tells you it
> is *going* to be crossed, why, and what to do, and it tells a real fault from
> a hot afternoon or a faulty probe.

## Read first

| Document | What it holds |
|---|---|
| `docs/status.md` | **What is implemented, what is simulated, what is illustrative, and what needs real test data or hardware validation** |
| `docs/methodology.md` | Every equation: C57.91 thermal and ageing, unbalance, measurement validation and the slope/intercept discriminator, health index and its cap, decision outputs, equivalent circuit, magnetics, FEM |
| `docs/assumptions.md` | Every assumption, why, and what happens if it is wrong (risks marked) |
| `docs/results.md` | Measured performance, held-out set first, and the live fault check |
| `docs/user-guide.md` | Running it, swapping in a new data file in 20 minutes |
| `docs/architecture.md` | Module map and data flow |
| `docs/live-contract.md` | The JSON a live source (simulator or ESP32) sends |

## Run

    pip install -r requirements.txt
    uvicorn dtwin.api.app:app --port 8000          # backend, from the repo root
    cd dashboard && npm install && npx vite        # http://localhost:5173

    python -m dtwin.live                           # optional live stream, 120x real time
    ./check_all.sh                                 # every regression suite, one line each

## Network dashboard (separate app, `docs/network.md`)

A district of transformers on a simulated LoRaWAN network, plus a deployment
planner. Illustrative data only; it does not change the app above.

    uvicorn dtwin_network.app:app --port 8001                # network backend
    cd network-dashboard && npm install && npx vite          # http://localhost:5180
    python -m dtwin_network.sim                              # fleet simulator, posts to :8001
    python check_network.py                                  # 26 checks (needs :8000 and :5173 for the last)

## Key equations (full set in `docs/methodology.md`)

    top-oil rise     dTO_u = 55 K x ((K^2 x 5 + 1) / 6)^0.8           first-order lag, tau_oil 3 h
    hot-spot rise    dHS_u = 25 K x K_worst^1.6                         first-order lag, tau_w 7 min
    hot-spot         = measured top oil + dHS   (when the oil probe is trusted)
    ageing factor    F_AA = exp(15000/383 - 15000/(hot-spot + 273))     = 1 at 110 C
    life cost        aged hours x Rs 4,50,000 assumed (illustrative) / 1,80,000 h normal life (IEEE C57.91)
                     = Rs 2.50 per hour of life used; nothing accrues above the 140 C emergency limit
    asset vs sensor  measured rise = a x predicted rise + b + c x t   (Huber, 24 h window)
                     a > 1.06: cooling degraded (asset);  a ~ 1, |b| > 2 K: probe offset (sensor)
    health index     min(weighted mean of five scores, min(ageing, loading score) + 10)

## Key results

| | |
|---|---|
| Held-out detection (detector frozen at `370b0b3`) | 11 of 12 events, 98.4 % diagnosis, 0.28 % false alarms |
| Smallest cooling fault caught (self-test) | 4 % (about 2.2 K extra rise at rated load) |
| Healthy thermal residual / cooling fault separation | sd 0.45 K / 18.8 sigma |
| A fixed 60 C oil trip at 48 C ambient | trips with no load at all (0.86 pu at 15 C) |
| Regression suites | physics 38, circuit 23, ingestion 31, notify 15, live 10, twin 12, engineering 32, simulate 12, FEM 20, restart 8; held-out byte-identical |

All detection figures are measured on simulated data (see `docs/status.md`).

## Layout

    dtwin/physics/     nameplate, C57.91 thermal, ageing, unbalance, equivalent circuit, magnetics, FEM
    dtwin/analytics/   measurement validation and the rolling rise fit
    dtwin/api/         the engine (twin queried at any instant) and the FastAPI app
    dtwin/live/        live stream: step simulator, causal buffer, SSE, fault injection
    dtwin/twin/        3D component states, diagnosis, corrective actions, simulate workspace
    dtwin/notify/      escalation policy, dispatch, notifiers
    dtwin/ingest.py    any CSV or workbook -> 8 channels + a mapping report
    dtwin/report.py    PDF condition report
    config/            components, catalogue (sourced), escalation, FEM inputs
    dashboard/         React + Tailwind + Recharts + three.js
    data/              simulated scenarios and their truth files (truth read only by evaluate.py)
