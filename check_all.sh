#!/usr/bin/env bash
# Every regression suite, one line each. Exit non-zero if any fails.
#   ./check_all.sh
cd "$(dirname "$0")"
fail=0
run() { local name="$1"; shift; local out; out="$("$@" 2>&1)"; local rc=$?
        printf "  %-14s %s\n" "$name" "$(echo "$out" | grep -E 'checks pass|cases pass|of [0-9]+ .*passed|PASS -|FAIL -' | tail -1)"
        [ $rc -eq 0 ] || { fail=1; echo "$out" | grep -E 'FAIL' | head -5 | sed 's/^/      /'; }; }
run physics   python validate_physics.py
run circuit   python check_circuit.py
run ingestion python check_ingest.py
run notify    python check_notify.py
run time-term python check_time_term.py
[ -f check_live.py ] && run live python check_live.py
[ -f check_twin.py ] && run twin python check_twin.py
[ -f check_engineering.py ] && run engineering python check_engineering.py
[ -f check_simulate.py ] && run simulate python check_simulate.py
[ -f check_fem.py ] && run fem python check_fem.py
[ -f check_restart.py ] && run restart python check_restart.py
[ -f check_calibration.py ] && run calibration python check_calibration.py
[ -f check_explain.py ] && run explain python check_explain.py
tmp="$(mktemp)"; python evaluate.py --set heldout > "$tmp" 2>&1
if diff -q "$tmp" results/heldout_detector_370b0b3.txt >/dev/null; then
  printf "  %-14s %s\n" "held-out" "byte-identical to results/heldout_detector_370b0b3.txt"
else
  printf "  %-14s %s\n" "held-out" "DIFFERS from results/heldout_detector_370b0b3.txt"; fail=1
fi
rm -f "$tmp"
exit $fail
