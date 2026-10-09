# Assumptions

Everything the twin assumes, why, and what happens if it is wrong. The ones
most likely to be wrong on CPRI's data are marked **risk**.

## Nameplate and physics

| Assumption | Value | Why | If wrong |
|---|---|---|---|
| **risk** Rating | read from the file preamble or sidecar; else **100 kVA** with a warning | the CSV probably will not say | A wrong rating reads as a thermal fault. The 100 kVA showcase run as 250 kVA raises "Cooling path degraded"; as 25 or 63 kVA it raises "Thermal model mismatch". Set it at setup. A slope off 1 from day one points at the nameplate; real degradation appears as a change. |
| Rated rises | top oil 55 K, hot-spot 25 K over oil | typical Indian ONAN distribution unit | Absolute temperatures shift; the slope/intercept discriminator still works because it compares against the same model over time, but the slope baseline moves off 1. |
| Loss ratio R, exponents n, m | 5.0; 0.8, 0.8 | C57.91 ONAN values | Shape of rise vs load slightly off; small effect at normal loads. |
| Oil time constant | 3 h (scaled by rating) | typical | Projection timing (minutes to limit) proportionally off. |
| Insulation | thermally upgraded paper, 110 C reference, 180 000 h normal life | C57.91 | Non-upgraded paper uses 98 C and the Montsinger form; ageing figures and rupee values change. The nameplate has a flag for it. |
| Replacement cost | Rs 4 500 per kVA (Rs 450 000 for 100 kVA) | order-of-magnitude for Indian distribution units | Rupee figures scale linearly; hours of life do not change. |
| LV phase voltage nominal | 250 V line-to-neutral (433 V line-to-line) | Indian LV | Used for range checks and when voltages are absent. |

## Data and ingestion

| Assumption | Why | If wrong |
|---|---|---|
| **risk** Unphased columns are in R, Y, B order | the usual logger order | Phases swapped; diagnoses are symmetric, so only the named phase in an alert would be wrong. Check the mapping report. |
| A column with "b" as its only phase token is blue (third) when R or Y appears elsewhere, otherwise the second phase of A/B/C | resolves the RYB/ABC clash | As above. |
| Currents whose 95th percentile exceeds 30x rated are in mA | real currents never reach that | A unit with an undersized nameplate and genuine heavy overload could be misread; the report states the reason, override with `--unit IR=A`. |
| Voltages with median below 2 are kV; 340-480 V is line-to-line | the only plausible readings | A 400 V system measured line-to-neutral (230 V) is handled; an unusual system voltage would need `--unit`. |
| Temperatures with median above 200 are kelvin; Fahrenheit only when the name says so | Celsius 200 is impossible for oil; Fahrenheit oil (100-200) overlaps Celsius overload | A Fahrenheit column without "F" in its name would be read as Celsius: the oil-median sanity warning fires. |
| **risk** Day-first vs month-first chosen by which gives a regular cadence | an ambiguous file has only one reading that steps evenly | For a file whose dates never exceed the 12th the choice is reported as ambiguous; check it, override with `--dayfirst`. |
| Timestamps are local wall time; time zones are dropped | the device logs local time | A UTC file would shift the diurnal pattern by 5.5 h; ambient and load shapes would look odd, diagnoses largely unaffected. |
| kW is converted to kVA at power factor 0.9 | only used when phase currents are absent | Load ratio off by PF error; derived currents are balanced so unbalance is unavailable. |
| **risk** The data sheet is the one with the most numeric rows x recognised column names | the readings sheet dominates a workbook | A workbook with several data-like sheets warns and names them all; choose with `--sheet`. |
| Decoy columns (power, PF, frequency, neutral, winding temperature, HV side, status, logger supply) are never channels | they share words with real channels (`kVA` contains "V", `IN` reads as phase A) | A real channel named with one of these words would be excluded; the report lists every exclusion. |
| Encoding: UTF-16 only with a BOM or NUL bytes, else UTF-8, else cp1252 | any byte string decodes as UTF-16, so trying it blind corrupts cp1252 files | A BOM-less UTF-16 file without NULs is not possible in practice. |
| Gaps of 1-2 samples are interpolated; longer gaps are left missing | a short hole is a logging glitch; a long one may be a dead channel | A real 30-minute sensor dropout would be filled rather than flagged. |
| Data finer than 1 minute is averaged to 1 minute; irregular data is put on the nearest standard cadence | keeps the model fast and the fit window well populated | Sub-minute transients are smoothed. |
| **risk** Missing ambient: assumed constant 30 C and the thermal diagnosis switched off | the fit compares rise over ambient; a constant would manufacture faults | Cooling and probe faults go undetected; hot-spot and ageing still use measured oil. The dashboard banner says so. |

## Detection

| Assumption | Why | If wrong |
|---|---|---|
| **risk** The first 3 days (or 25% of the record) are healthy | the cross-phase check needs a standing pattern | A file that opens with unbalance treats it as normal and misses it. Planned fix: estimate the reference robustly across the whole record and flag an atypical opening. |
| Cooling alarm at slope > 1.06 for 3 consecutive hourly fits | 6% excess rise is ~3 K at 50 K rise; healthy slope p95 is 1.025 | Thin margin: the healthy maximum is 1.058. A noisier real sensor may need a wider threshold — re-measure on a known-healthy stretch. |
| Probe offset at abs(intercept) > 2 K | ~4 sd of the healthy residual | Smaller offsets are not called. |
| Step at 6 robust sd of the detrended first difference | lowering to 5 created false steps on healthy data | Small or slow steps are missed; a missed step can cause a false cooling alarm (see results). |
| 24 h trailing fit window | spans a full load cycle so the slope is identifiable | Detection takes at least ~17 h; an alarm can outlast its fault by up to ~25 h. |
| Thermal noise sd 0.45 K | measured on the simulator | Real probes are probably noisier; residual-in-sd figures would shrink. |

## Decision outputs

| Assumption | Why | If wrong |
|---|---|---|
| Load-shed advice targets hot-spot 110 C (F_AA = 1) | the point where insulation ages at its rated rate | Utilities with a stricter policy can set `reference_hotspot_c`. |
| Rise-over-ambient trip of 30 K | equals what the 60 C setting gives at the 30 C design ambient | A different design ambient gives a different equivalent. |
| Projections hold load and ambient constant | no load forecast is used | Real afternoons warm up; the countdown is a "at present conditions" figure and is labelled so. |
| **risk** GPRS payload format | CPRI's message format is not in hand | The preview is an assumed comma-separated frame; field order will be matched once their format is known. |

## Notifications

| Assumption | Why | If wrong |
|---|---|---|
| **risk** Recipients in `config/escalation.json` are placeholders | real contacts are CPRI's to give | Nothing reaches anyone until they are replaced and live mode is enabled. |
| Clear hold-off 8 h | showcase re-fires within 0.5-6.5 h (same fault); overloads 20 h apart are separate evenings | A shorter hold-off sends more open/clear pairs for a flickering fault; a longer one merges separate events. |
| P1 escalates after 4 h, P2 after 48 h, P3 after 24 h | an alarm unanswered for half a shift should reach the next level | Set per tier in the config. |
| P2 waits for the next working morning (Mon-Sat, 09:00) | attention items are not night call-outs | Set `working_days` and `working_hour`. |
| P3 needs 30 min before notifying | dead-channel blips of one or two samples are logger noise | A real 20-minute sensor fault is not notified (it still shows on the dashboard). |
| Live mode only sends events less than 30 minutes old | a historical file must never reach a real recipient | A delayed live feed would outbox rather than send; raise `live_window_min`. |

## Simulator (development only)

Jaipur-like summer ambient (34 C mean, 9 K swing), a mixed feeder load shape
with a double peak, 1.6% standing unbalance, measurement noise 0.9% on
currents, 0.35% on voltages, 0.25 K on temperatures. All detector numbers come
from this simulator. CPRI's data will differ, which is why the ingestion layer
reports everything and the thresholds are documented with their reasons.
