"""
    python -m dtwin.calibrate <report.yaml|json> --show

Reads a factory test report, calibrates, and prints every parameter with its
provenance and formula, then the checks. Nothing is applied to a running
dashboard; use the Engineering page's Calibration tab for that.
"""

import argparse
import sys

from . import Calibration, ReportError, load


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report")
    ap.add_argument("--show", action="store_true", help="print the parameter table and the checks")
    a = ap.parse_args(argv)
    try:
        cal = Calibration(load(a.report))
    except (ReportError, OSError) as e:
        print(f"cannot calibrate: {e}", file=sys.stderr)
        return 2
    j = cal.as_json()
    tag = "  (ILLUSTRATIVE, not a real certificate)" if j["illustrative"] and "illustrative" not in j["name"].lower() \
        and "synthetic" not in j["name"].lower() else ""
    print(f"Calibrated from test report: {j['name']}{tag}")
    if not a.show:
        return 0
    print(f"\n{'parameter':38} {'value':>12} {'unit':10} {'provenance':12} {'was':>10} {'change':>10}")
    for p in j["params"]:
        v = p["value"] if not isinstance(p["value"], float) else f"{p['value']:.3f}"
        w = p["was"] if not isinstance(p["was"], float) else f"{p['was']:.3f}"
        ch = "" if p["change"] in (None, 0) else f"{p['change']:+.3f}"
        print(f"{p['label'][:38]:38} {str(v):>12} {p['unit'][:10]:10} {p['provenance']:12} {str(w):>10} {ch:>10}")
        if p["formula"] and p["provenance"] == "DERIVED":
            print(f"{'':40}{p['formula']}")
        if p["note"]:
            print(f"{'':40}note: {p['note']}")
    if j["hotspot_note"]:
        print(f"\nnote: {j['hotspot_note']}")
    print("\nChecks (checks, not pass/fail guarantees):")
    for c in j["checks"]:
        print(f"  {c['label']}: {c['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
