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

Map view: open http://localhost:5180 → **Map** tab. Demo: the **Demo** button on
the Map tab, or headless:

    python -m dtwin_network.demo                 # six steps, prints each caption and checks it

Checks and screenshots:

    python check_network.py                      # 42 checks; the UI ones need :8000/:5173 and :5180/:8001 up
    python check_network.py --skip-ui            # without the browser checks
    python check_network.py --net-url=http://localhost:5181      # map UI checks against another copy
    cd network-dashboard && node scripts/shot.mjs --tab map --demo 3 --out ../screens/demo3.png
    cd network-dashboard && node scripts/shot.mjs --tab map --offline --out ../screens/offline.png
    cd network-dashboard && node scripts/shot.mjs --tab map --gateway GW-A --radius 1.5 --out ../screens/edit.png

## Geospatial network twin (Map tab)

**What is synthetic.** Every position is a *synthetic demonstration layout — not
real utility assets*: the original 15 × 10.2 km layout placed with its centre
near Hoskote, Bengaluru Rural, Karnataka (origin 13.0249 N, 77.7290 E, in
`config_network/fleet.yaml`), same relative layout, same IDs. Coverage is an
*ESTIMATED PROXIMITY COVERAGE — not verified LoRa coverage*. Costs are
*illustrative*. All three labels are on screen. Regenerate with
`python -m dtwin_network.make_fleet`.

**Five separate fields per transformer × gateway pair** (`dtwin_network/geo.py`),
never merged into one number, and shown as visibly different rows in the
transformer panel:

| Field | What it is |
|---|---|
| `distance_m` | geometry: haversine on lat/lon (mean Earth radius 6 371 008.8 m) |
| `within_assumed_radius` | distance ≤ the gateway's `assumed_radius_m` (an assumption, 3 km default) |
| `est_link_margin_db` | link-budget **estimate**, only when the link budget is switched on |
| measured RSSI / SNR | **simulated** packets only: link-budget mean + seeded per-node shadowing (σ 6 dB) + per-packet fading (σ 2 dB); gateway receive metadata, never part of the payload |
| `comm_status` | live: online / stale / offline from the backend |

**Feasible** = within the assumed radius AND (link budget on → margin ≥ the
required margin) AND the gateway has capacity. The nearest gateway is never
assumed feasible: the panel lists every gateway nearest first with yes/no and
why.

**Coverage model and its limits.** A transformer is *covered* when at least
one built gateway (existing, or built in the plan shown) is feasible for it.
It is a disc test: no terrain, buildings, antenna height, interference or
spreading-factor choice. It says only that a gateway is *close enough to be
worth surveying*.

**Link budget (optional, off by default).** Log-distance path loss, a rough
planning model, not a propagation prediction. Every value and its source is in
`link_budget` in the fleet file:

    PL(d) = FSPL(d0 = 100 m, 866 MHz) + 10 · n · log10(d / d0) + extra loss
    Prx   = Ptx + Gtx + Grx − cable loss − PL(d)
    margin = Prx − sensitivity(SF)

| Value | Setting | Source |
|---|---|---|
| frequency | 866 MHz | India licence-free 865–867 MHz band (WPC GSR 1048(E), 2018) |
| Ptx | 14 dBm | typical LoRa end-device setting; band limit 1 W |
| Gtx / Grx / cable | 2.15 / 5 / 2 dB | assumed (whip node, collinear gateway, feeder) |
| n | 3.5 | assumed peri-urban clutter (typical 2.7–4) |
| extra loss | 20 dB | assumed: node in a metal enclosure at the pole/plinth, plus clutter |
| sensitivity | SF7 −123 … SF12 −137 dBm (SF10 used, −132) | Semtech SX1276 datasheet, 125 kHz |
| required margin | 10 dB | assumed fade margin |

With these numbers the margin falls below the required 10 dB at about 2.7 km;
switching it on removes 5 in-radius pairs and lowers current coverage from
58 % to 56 %.

**Offline.** The basemap is OpenStreetMap's standard tile server, with
attribution, fetched only as the map is viewed (no bulk download). If no tile
loads (3 errors, or nothing in 6 s) the map switches to a plain light
background with a lat/lon grid and a scale bar; all markers, circles, lines and
panels keep working. A **Basemap on/off** toggle forces it. Tested with the tile
host blocked (`check_network.py` 22).

### The placement optimiser in plain words

For a capital budget it decides three things at once: which candidate gateway
sites to build, which unmonitored transformers get a node, and which gateway
every node (old and new) reports through. A node can only report through a
gateway that is built and feasible for it; no gateway takes more nodes than its
`max_nodes`; new nodes plus new gateways never cost more than the budget.
Among all plans that satisfy that, it picks the one covering the most risk;
between equal-risk plans, the cheaper; installed nodes stay connected
whenever range and capacity allow and are moved to another gateway only if
that frees room for more risk (reported as "Move …"). If an assumption change
leaves an installed node with nowhere to go, it is reported as "would lose
coverage", never silently dropped.

    variables  y_g ∈ {0,1} build candidate g (existing: built)
               z_t ∈ {0,1} instrument transformer t
               x_tg ∈ {0,1} t reports through g, only for feasible pairs
    maximise   Σ risk_t · z_t (new)  + 1000 · Σ z_t (installed)  − 1e-9 · cost − 0.001 · moves
    subject to Σ_g x_tg = z_t;  x_tg ≤ y_g;  Σ_t x_tg ≤ max_nodes_g;
               Σ node_cost · z (new) + Σ gateway_cost · y (candidates) ≤ budget

Solved by HiGHS (`scipy.optimize.milp`) with the relative gap set to 1e-9,
so the optimum is exact; 10–25 ms per solve, and an edit in the map re-solves
and redraws in about 160 ms including the 120 ms debounce. Every
recommendation carries a template reason, e.g. *"Build CG-1 (₹1,20,000):
unlocks DT-118, DT-119, DT-120, DT-121 (DT-118, DT-119 high risk), out of range
of all existing gateways."* High-risk transformers left out are listed with the
binding reason: out of range of every site / gateway full / budget. The greedy
baseline and the current deployment are shown side by side.

| Budget | Plan | Capital | Nodes (new) | Gateways (new) | Instrumented | Within coverage | High-risk instrumented |
|---|---|---|---|---|---|---|---|
| any | Current | ₹0 | 12 | 3 | 23 % | 58 % | 0 of 8 |
| ₹2,50,000 | Greedy | ₹2,20,000 | 22 (+10) | 3 | 42 % | 58 % | 3 of 8 |
| ₹2,50,000 | Optimised | ₹2,20,000 | 22 (+10) | 3 | 42 % | 58 % | 3 of 8 |
| ₹3,50,000 | Greedy | ₹2,20,000 | 22 (+10) | 3 | 42 % | 58 % | 3 of 8 |
| ₹3,50,000 | Optimised | ₹3,40,000 | 22 (+10) | 4 (+CG-1) | 42 % | 67 % | 5 of 8 |
| ₹5,50,000 | Greedy | ₹4,50,000 | 27 (+15) | 4 (+CG-1) | 52 % | 67 % | 5 of 8 |
| ₹5,50,000 | Optimised | ₹5,48,000 | 26 (+14) | 5 (+CG-1, CG-2) | 50 % | 77 % | 7 of 8 |

(At ₹5,50,000 the optimiser also moves DT-004 from GW-A to CG-2 to free a GW-A
slot.) The optimiser never covers less risk than greedy (checked at 25 budgets,
link budget off and on).

### Demo (six steps, seeded)

Runs in-process on its own simulator and store, never the live stream, with a
fixed start time and seeds, so every run is identical; Back/Next move between
steps, Reset returns to step 1.

1. Initial network: 12 monitored on 3 gateways, estimated coverage 30 of 52 (58 %).
2. Overload injected into DT-007's own physics: only DT-007 changes (alarm).
3. DT-004's radio link cut: STALE, health last known and unverified, no fault alarm.
4. DT-118 highlighted: high risk (60), outside every existing gateway; nearest site CG-1 is a candidate.
5. ₹3,50,000: the optimiser builds CG-1 and covers DT-118 (5 of 8 high-risk; greedy 3).
6. ₹5,50,000: a different plan, CG-1 + CG-2, 7 of 8 high-risk.

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
| ₹5,50,000 | 14 nodes + CG-1, CG-2 (moves DT-004 to CG-2), ₹5,48,000, 654 points, 7 of 8 | 15 nodes + CG-1, ₹4,50,000, 574 points, 5 of 8 |

The optimiser never covers less risk than greedy (checked at 25 budgets, link budget off and on). It
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
