# Results

All figures come from scripts in the repository and can be reproduced with
the commands shown. Detection is measured against ground truth that the
detector never sees (`evaluate.py` is the only reader of `*_truth.csv`).

## Physics — `python validate_physics.py`

**38 of 38** checks against closed-form expectations, in six sections: steady
state at rated load (rated load at 30 C ambient puts the hot-spot exactly at
110 C, F_AA = 1.0), the ageing law, dynamic response, time to threshold and the
load-shed answer, unbalance, and nameplate scaling from 25 to 630 kVA.

## Detection — held-out (headline) — `python evaluate.py --set heldout`

Six scenarios on seeds, severities, ramp rates, ratings (25, 63, 100, 160 kVA)
and cadences (10, 15, 30 min) never used in development, including an
asset-and-sensor overlap. Scored **once**, with the detector frozen at commit
`370b0b3`. Output: `results/heldout_detector_370b0b3.txt`.

| Metric | Held-out | In-sample (tuned on) |
|---|---|---|
| Events detected | **11 / 12** | 18 / 18 |
| Severity at detection, ramped faults | **median 34 %** (20-79 %) | median 30 % |
| Residual at detection, thermal faults | **median 4.3 sd** | median 4.8 sd |
| Latency (secondary) | median 14.8 h | 9.6 h |
| Diagnosis accuracy, post-detection | **98.4 %** | 99.0 % |
| Wrong-family alarms | **0.17 %** of fault samples | 0.07 % |
| False alarms, genuinely clean samples | **0.28 %**, 0.02 per day (4 episodes in 180 days) | 0.00 % |
| Alarm-clear latency | median 0.4 h, max 24.8 h | median 0.0 h, max 23.8 h |

On the demo record (`showcase.csv`), the cooling fault is first alarmed when it
has added 1.35 K to the oil — 14.2 h before it adds 3 K and 32.2 h before it adds
5 K. The fixed 60 C trip first fires 9.5 h after onset, from afternoon heat,
when the fault has added only 0.5 K: a threshold cannot tell the two apart.
The probe drift is called a sensor fault with 0 % of its samples called an
asset fault.

## Regression and self-test

| Test | Result |
|---|---|
| `check_time_term.py`: cooling fault at 24, 96, 240 h ramps | all called **asset**, 100 % post-detection, 0 % called sensor |
| Severity at detection by ramp | 71 % (24 h), 29 % (96 h), 32 % (240 h): the percentage transfers only for ramps well beyond the 24 h window |
| Self-test, 100 and 250 kVA | minimum detectable cooling degradation **4 %** (about 2.2 K extra rise at rated load), caught 27 h after onset (14 h from 6 % up); 2 % is missed; no alarms before the fault |

## Ingestion — `python check_ingest.py`

**Blind test.** A teammate built a file we had never seen: an Excel workbook
with the nameplate on one sheet and readings on another, three title rows, a
cp1252 CSV export, decoy columns (kVA, IN, WTI, PF, Freq, Status), Excel-native
date and time cells, three concatenated newest-first exports with repeated
headers, a 6 h outage, and "--" / "ERR" missing markers. **The ingestion layer
that passed 22 of 22 in-sample mangles crashed on both files** — no Excel
support, and a decoder that tried UTF-16 before cp1252. Behind those it would
also have taken a padded title row as the header and only avoided mapping three
decoys by luck of column order. All fixed; both files are now permanent
regression cases (`tests/fixtures/blind/`). This is the in-sample caveat
measured: our own mangles missed every one of these failure modes.

**31 of 31** cases now pass: the 22 mangles below, both blind files, and seven
cases for the failure classes the blind test exposed (plain workbook, native
date and time cells with the nameplate on another sheet, a README / training /
test workbook that must warn and honour `--sheet`, every missing marker read
as missing and never zero, decoys placed before the real columns, two columns
competing for one channel, cp1252 with comma-padded title rows).

The original **22 of 22** mangled variants of `showcase.csv` load, map every channel to the
right source column, match the clean values after unit conversion, and still
catch the cooling fault and call the probe drift a sensor fault (or, with
ambient removed, report the thermal diagnosis unavailable and still catch the
overload). Output: `results/ingest_check.txt`.

Renamed columns · units by magnitude (mA, kV, K) · units in names · shuffled
rows · 10 % holes · 1 h cadence · ambient removed · duplicate timestamps ·
DD/MM/YYYY · MM/DD/YYYY with AM/PM · ten junk columns · generic headers ·
semicolon · semicolon with decimal comma · metadata preamble · epoch seconds ·
split date and time · L1/L2/L3 and U1/U2/U3 naming · line-to-line voltages ·
jittered timestamps · blank rows and a trailer · all of these at once.

The 22 were written by the same people who wrote the ingestion layer, against
the same schema; the blind test above is what that is worth. More blind files
from someone else remain the best check before an unseen dataset.

## Notifications — `python check_notify.py`

**14 of 14** (11 below plus three for the demo button: disabled without the
live flag; one click sends the whole P1 event to the demo addresses only,
tagged and with the PDF; the per-session cap holds). A fault re-firing 10 times inside the hold-off sends one OPEN, one
ESCALATE and one CLEAR per recipient. On showcase, 14 alert episodes become 10
incidents and 33 alert notices, plus a daily digest. With SMTP credentials set,
`DTWIN_NOTIFY_LIVE=1` set and every network call rigged to raise, a full replay
and the API endpoint make zero network attempts; real notifiers refuse replay
messages even when called directly; live mode refuses to start without the
flag and sends nothing from a historical file; with "now" at the event time the
real path sends an email carrying the PDF and a 160-character SMS; with SMTP
unset it falls back to the outbox and says so; every P3 says "do not send a
crew" and no P1/P2 does. A mutation test (guard deliberately broken) produced
10 network attempts, so the replay check does catch a leak.

## Finding — a fixed oil trip is ambient-blind

Steady load a fixed 60 C oil trip permits, against the twin's hot-spot limit:

| Ambient | Fixed 60 C oil trip | Rise-over-ambient trip (30 K) | Hot-spot 110 C |
|---|---|---|---|
| 15 C | 0.86 pu | 0.60 pu | 1.12 pu |
| 30 C | 0.60 pu | 0.60 pu | 0.99 pu |
| 42 C | 0.31 pu | 0.60 pu | 0.88 pu |
| 48 C | **0.00 pu** | 0.60 pu | 0.83 pu |

At 48 C ambient the existing device trips with no load at all.

## Known limits — all measured

- **A probe drift concurrent with a cooling fault can be missed**, since the
  classifier checks slope before intercept. Held-out
  `ho_overlap_cooling_drift`: the 3.5 K oil drift went undetected (the one
  held-out miss) while the fit showed slope ~1.4 and intercept +3 to +5.6 K. It
  cannot be fixed from the thermal fit: under pure asset faults the intercept
  carries up to 12 K of model-form error (oil loss) and the drift term reaches
  4.8 K/day at p99, against 2.5-6 K and 1.8 K/day for a real probe fault. A
  second, independent temperature reference would be needed.
- **A missed ambient step can cause a false cooling alarm**, not just an
  unnamed channel. Held-out `ho_160kva_30min`: a +2.5 K ambient bias clearing
  went undetected at 30-minute cadence, the fit window straddled it, and
  cooling alarms fired on clean data — 2.12 % false alarms on that file. That
  would send a crew to site for an instrument fault.
- **Oil-high and ambient-low offsets are thermally identical**; the twin reports
  "oil or ambient probe" rather than guessing.
- **A wrong nameplate reads as a thermal fault** (see assumptions).
- **Alarm-clear latency** up to about 25 h after a thermal fault clears, from
  the 24 h trailing window.
- **The cooling alarm margin is thin**: healthy slope reaches 1.058 against the
  1.06 threshold; persistence holds the alarm off.
- **The cross-phase reference assumes the record opens healthy.**

## Tested and rejected

| Approach | Why rejected |
|---|---|
| Residual / predicted-rise ratio CV | 0.315 cooling vs 0.351 probe drift: does not separate |
| Night-time residual | +6.38 K cooling vs +6.08 K probe drift: does not separate |
| Lowering the step threshold from 6 to 5 sd | false oil steps on healthy and overload files |
| Windowed (k-sample) step test | fires at every overload onset in oil; found 7/18 true ambient steps against 9/18 for the existing test |
| Independent "report both" slope/intercept classifier | 25.5 % false sensor calls on oil loss in-sample; failed `check_time_term.py` |
| Split-window step scan (pre-registered, `results/prereg_split_step.md`) | cut wrong-family cooling alarms 66 % on a development set but raised in-sample false alarms from 0.00 % to 0.16 %, failing its pre-set rule; shipped switched off |
| DGA, Duval triangle, harmonic derating | no dissolved-gas or harmonic data exists |
