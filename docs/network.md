# GridTwin network dashboard

A second, separate app that scales the single-transformer twin to a district:
many distribution transformers, each with its own twin, reporting over a
LoRaWAN network, plus a planner that decides where the next nodes and gateways
should go.

> **Simulated network: positions, radio coverage, telemetry and costs are
> illustrative, not CPRI infrastructure or procurement data.** No figure here is
> a measured saving or a measured radio coverage.

## Run

Both apps can run side by side. The single-transformer app is unchanged.

| | Backend | Frontend |
|---|---|---|
| Single transformer | `uvicorn dtwin.api.app:app --port 8000` | `cd dashboard && npx vite` → http://localhost:5173 |
| Network | `uvicorn dtwin_network.app:app --port 8001` | `cd network-dashboard && npm install && npx vite` → http://localhost:5180 |

Fleet simulator (posts to :8001; one 15-min interval per second):

    python -m dtwin_network.sim                  # --step-s 0.5 for faster, --api http://host:8001

Stop it after any test and confirm:

    pkill -f "dtwin_network.sim"; pgrep -fl "dtwin_network.sim"     # must print nothing

Checks and screenshots:

    python check_network.py                      # 26 checks; needs :8000 and :5173 up for the last one
    python check_network.py --skip-ui            # without the browser checks
    cd network-dashboard && node scripts/shot.mjs --tab planner --budget 550000 --out ../screens/planner.png

## Architecture

    config_network/fleet.yaml        the district: assets, gateways, costs, radio assumptions
         |
    dtwin_network/sim.py  ──────────── one process: every monitored DT's physics + every node + every gateway
      radio.py  Node: its own step physics (dtwin/live/sim.py, imported), sequence numbers, buffer
                FleetSim: gateways forward up to capacity per interval; alarms first
         |  POST /api/net/uplink   (one batch per gateway per interval, packets unchanged)
         v
    dtwin_network/app.py :8001  ─────── FastAPI
      store.py   packet validation, per-DT buffers, comms status, event log, replay snapshots;
                 each DT's twin = dtwin.live.store.analyse(this DT's samples only)
      risk.py    transparent risk score
      planner.py MILP (scipy.optimize.milp / HiGHS) + greedy + sensitivity
      strategy.py A/B/C/D comparison
      views.py   "What's happening in the network", visit-first, key numbers: fixed templates
         |
    network-dashboard/ :5180  ──────── Vite + React + Tailwind + Recharts; tokens, CSS and
                                       small components copied from dashboard/src (never imported)

**Isolation.** Nothing in `dashboard/`, `dtwin/`, `config/` or the existing tests
was changed. The network backend imports the existing engine read-only:
`dtwin.live.sim.LiveSimulator` (physics, subclassed only to add a per-site
ambient offset), `dtwin.live.store.analyse` (validation, thermal fit, alarms,
health index), `dtwin.physics.nameplate.preset`. No physics was copied.

**Topology.** LoRaWAN star-of-stars: node → gateway → backend, one radio hop.
No multi-hop mesh. A gateway forwards each packet unchanged and adds its own
receive stamp; it never merges or averages packets (`check_network.py` 3
compares the payload at the node with what the backend stored).

**Packets.** `device_id`, `seq`, `ts` (sample time), `payload` (the eight
channels), `priority`, hop stamps. The backend rejects a bad sequence number,
an unknown device, a bad timestamp, a missing or non-numeric payload, with the
reason; a duplicate sequence number is ignored. A sequence gap counts as lost;
a late arrival from a node buffer is counted as late, not lost.

**Priority alarms.** The node raises a priority packet once, at onset, when
mean phase current exceeds rating or oil rise over ambient exceeds the rated
55 K; it is sent at once and taken first by the gateway. The diagnosis itself
is always the backend twin's.

**Three fields per transformer, never merged.**

| Field | Values | From |
|---|---|---|
| Health | index 0–100 and level, `verified` true/false | that unit's own twin; last known and unverified when not online |
| Diagnostic confidence | none / reduced / high, with reason | none when offline or no data; reduced while history < 48 h, a channel untrusted, packets overdue or ambient missing |
| Communication | online / stale / offline / no data | missed reporting intervals on the network clock |

The network clock is the newest simulated time any gateway has reported.
Stale after 2 missed intervals, offline after 6 (`radio:` in the fleet file).
If no gateway reports for 12 s of wall time, every unit is stale: the backend
cannot see the network. Lost communication is never shown as a transformer
failure, and a unit that is not reporting is never drawn green.

**Independence.** Each DT's twin reads only that DT's buffer. No cohort,
neighbour or average fills missing data. `check_network.py` 1 injects a fault
into one DT and requires the other 11 twin states to be byte-identical,
including the unit of the same manufacturer and rating.

**Demo controls.** Inject a fault (changes only that DT's physics in the
simulator; the backend only sees its packets), cut a radio link (the node
keeps measuring and buffers, up to 192 packets, and replays oldest first on
restore), switch a gateway off, packet loss, pause, reset (clears everything;
the simulator re-sends 48 h of history).

## Risk score

    risk = 100 × likelihood × (0.5 + 0.5 × consequence)        bands: high ≥ 45, medium ≥ 25

| | Likelihood (0–1) |
|---|---|
| Monitored | worst of: (100 − health index)/100; active asset alarm 1.0, instrument-only 0.3; hot-spot above limit 0.9, within 2 h 0.8, within 12 h 0.5; no data 0.5, offline 0.4, stale 0.2 |
| Unmonitored | 0.40 × clip((peak-load ratio − 0.7)/0.5) + 0.25 × clip(age/35 y) + 0.35 × clip(failures in 5 y / 3) |

Consequence = 0.6 × clip(customers/400) + 0.4 × (criticality − 1)/2.

Every constant is a stated judgement for a synthetic fleet, not a fitted value.
The dashboard shows every term for every transformer (Deployment planner →
"Risk score for every transformer"). The planner uses the unmonitored scores
only (metadata), so the plan does not move with the live state.

## Deployment MILP

Sets: U unmonitored transformers, G gateways (existing, always open, and
candidate sites), P = {(i, g) : distance(i, g) ≤ coverage radius of g}.

    variables   x_i ∈ {0,1}  node on i ∈ U
                y_g ∈ {0,1}  site g opens (existing: fixed 1)
                z_ig ∈ {0,1} i reports through g, (i, g) ∈ P

    maximise    Σ_i r_i x_i  −  ε · (Σ_i c_i x_i + Σ_g C_g y_g)        ε = 1e-9

    subject to  Σ_g z_ig = x_i                         ∀ i      one gateway in range
                z_ig ≤ y_g                             ∀ (i,g)  only through an open gateway
                Σ_i z_ig ≤ cap_g − used_g              ∀ g      capacity (nodes)
                Σ_i c_i x_i + Σ_g C_g y_g ≤ B                   capital budget

r_i is the risk score, c_i the node cost, C_g the gateway cost (0 for an
existing one), used_g the nodes already on g. ε only breaks ties toward the
cheaper plan (ε × the largest possible cost < 0.01 risk point). Solved by
HiGHS through `scipy.optimize.milp`, ~5 ms. The budget row makes an
over-budget plan impossible; `check_network.py` 8 still checks 392 plans.

**Greedy baseline.** Repeatedly add the transformer with the highest
r_i / (c_i + cheapest gateway in range if no open gateway there has room) that
fits the remaining budget.

**Why left out.** For each high-risk unit not chosen: *range* if no gateway or
site is within its radius; *capacity* if every open gateway in range is full
and no closed site is in range; otherwise *budget*, with the cost it would
need.

**Sensitivity.** Node cost, gateway cost and coverage radius each moved ±20 %;
the plan is re-solved and the assumption that moves the most choices (then
risk points) is named.

**Group representative (strategy D).** Same MILP with x restricted to
cohorts (manufacturer + rating) that have no monitored unit, at most one node
per cohort, objective: most cohorts represented, then risk, then least cost;
budget not applied (its cost is reported as is).

### Results on the synthetic district (illustrative)

| Budget | Optimised | Greedy |
|---|---|---|
| ₹2,50,000 | 10 nodes, no new gateway, ₹2,20,000, 374 risk points, 3 of 8 high-risk | the same plan |
| ₹3,50,000 | 10 nodes + CG-1, ₹3,40,000, 465 points, 5 of 8 | 10 nodes, no gateway, 374 points, 3 of 8 |
| ₹5,50,000 | 14 nodes + CG-1, CG-2, ₹5,48,000, 640 points, 7 of 8 | 15 nodes + CG-1, ₹4,50,000, 574 points, 5 of 8 |

The optimiser never covers less risk than greedy (checked at 25 budgets). It
wins where a gateway pays off only through several units together; elsewhere
greedy finds the same plan. At ₹4,00,000 node cost is the assumption the
plan is most sensitive to.

## Assumptions

- Positions: a synthetic 15 × 10 km grid, flagged `coordinates_synthetic`.
- Coverage: a flat 3 km radius per gateway. Real LoRaWAN range depends on
  terrain, clutter, antenna height and spreading factor.
- Capacity: nodes per gateway (8, 8, 6 existing; 12 per candidate site), an
  illustrative stand-in for duty-cycle and airtime limits at one uplink per
  15 min.
- Costs: node ₹22,000 + ₹1,500/yr; gateway ₹1,20,000 + ₹18,000/yr. Illustrative.
- Unmonitored metadata (age, estimated peak load, customers, criticality,
  failures) generated with a fixed seed by `python -m dtwin_network.make_fleet`.
- Each monitored DT runs the existing physics with its own nameplate, load seed,
  peak load and ambient offset; DT-001 uses the single-unit twin's defaults.

## Limitations

- No radio propagation model: in range or not, nothing in between; no
  collisions, no spreading-factor trade-off, no downlink acknowledgements
  modelled (a cut link is treated as known to the node, so it buffers).
- Unconfirmed uplinks: an air loss is gone for good, seen as a sequence gap.
- Capacity is a node count, not airtime.
- The risk weights are judgement; the plan is only as good as the metadata.
- The planner chooses for capital only; recurring cost is reported, not
  optimised.
- Replay covers what the backend held (snapshots of the last 600 network
  intervals), not a re-run of the physics.

## What needs real data

1. Actual transformer coordinates and the feeder topology.
2. Procurement costs: node, gateway, installation, SIM/backhaul, maintenance.
3. A radio survey (or a calibrated propagation model) for each candidate
   gateway site, replacing the flat radius.
4. Asset records: age, nameplate, loading history, failure history, customers
   and critical loads.
5. Gateway capacity from the chosen LoRaWAN plan (region, data rate, duty cycle).
