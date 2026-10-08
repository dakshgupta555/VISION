# User guide

## Install

Tested with Python 3.13 and Node 22 on macOS.

    python -m venv .venv
    source .venv/bin/activate              # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cd dashboard && npm install && cd ..
    python validate_physics.py             # expect: 38 of 38 checks passed

## Run

Two terminals, both from the repository root.

    uvicorn dtwin.api.app:app --port 8000
    cd dashboard && npx vite

Open **http://localhost:5173**. The first load of each dataset takes about a
second while the twin runs over the whole record; after that every query
takes a few milliseconds.

## Using the dashboard

- **Scenario** (top right) picks any CSV in `data/`.
- **Nameplate** re-runs the twin with a different rating. Use the real one: a
  wrong rating reads as a thermal fault.
- **Replay bar**: drag to any moment, or press Play. Every panel shows what the
  twin would have said at that instant, using only data up to it. The coloured
  strip shows where the alarms (red) and warnings (amber) are.
- **Headline**: names the most urgent problem with its value now and what to
  do. When everything is normal it shows the hot-spot projection instead.
- **Health breakdown** and **Insulation life** are folded; click to open. The
  folded line keeps its colour, so a problem is visible without opening it.
- **Alert feed** groups repeats of the same fault; click a grouped entry for its
  episodes.
- **Comparison** shows what the existing fixed 60 C device would do; it is not
  an alert. Open the protection-setting table from it.
- **What-if**: move load, ambient and cooling factor. Reset returns to now.
- **Self-test**: measures the smallest cooling degradation the twin catches for
  the selected nameplate (about 3 seconds).
- **Download report (PDF)**: the condition report as of the replay cursor.

## Load a new file

1. Copy it to `data/<name>.csv`.
2. Read what the ingestion layer decided:

        python -m dtwin.ingest data/<name>.csv

   Check, top to bottom: every channel mapped to the right column, the unit and
   its reason, day-first vs month-first, cadence, rows dropped and why, and the
   warnings.
3. Restart the backend and pick `<name>` in the dashboard. If the report shows
   warnings, they also appear in an amber banner under the header.

## Correct a wrong guess

Every decision can be overridden. On the command line:

    python -m dtwin.ingest data/<name>.csv \
        --map oil_temp=T1 --map ambient_temp=T2 \
        --unit IR=mA --unit VR=kV_LL \
        --dayfirst yes \
        --kva 63 \
        --save-sidecar

`--save-sidecar` writes `data/<name>.mapping.json`, which the dashboard and the
PDF also read:

    {
      "map": {"oil_temp": "T1", "ambient_temp": "T2"},
      "units": {"IR": "mA", "VR": "kV_LL"},
      "dayfirst": true,
      "rating_kva": 63
    }

Channels: `IR IY IB VR VY VB oil_temp ambient_temp timestamp`.
Units: currents `A mA kA`; voltages `V kV V_LL kV_LL` (line-to-line is divided
by sqrt 3); temperatures `C K F`. Also accepted in the sidecar: `separator`,
`decimal`, `assumed_ambient_c`.

If a channel is missing from the file, the report says **ABSENT** and the
dashboard banner says which diagnoses are off. Without ambient, the cooling and
probe-offset diagnoses are off; hot-spot and ageing still work from measured
oil.

## Reports from the command line

    python -m dtwin.report <name>                         # as of the last sample
    python -m dtwin.report <name> --t "2026-04-23 15:00" -o out.pdf

## Checks

    python validate_physics.py           # physics, 38 checks
    python evaluate.py                   # in-sample detection scoring
    python evaluate.py --set heldout     # held-out scoring
    python check_time_term.py            # drift term vs real cooling faults
    python check_ingest.py               # 22 mangled CSVs must load and diagnose

## Regenerate simulated data

    python -c "from dtwin.simulate.generator import quick; quick('showcase')"
    python make_heldout.py               # the held-out set (do not tune against it)
