# Setup

## Python side

    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    python validate_physics.py       # expect 38 of 38

## Regenerating data

    python -c "from dtwin.simulate.generator import quick; quick('showcase')"

Or all scenarios at once — see the loop in README.md.
