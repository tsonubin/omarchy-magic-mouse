#!/bin/bash
# Container-side entrypoint: unit tests, then the end-to-end scenarios.
set -uo pipefail

export PYTHONPATH=/src
cd /src

echo "=== unit tests (inside the emulated Omarchy environment) ==="
python3 -m unittest discover -s tests 2>&1 | tail -4
UNIT_RC=${PIPESTATUS[0]}

echo
echo "=== environment ==="
echo "kernel:  $(uname -r) ($(uname -m))"
hyprctl version 2>/dev/null | head -1 || echo "hyprctl: not running yet (scenarios start it)"
if command -v omarchy >/dev/null; then
  echo "omarchy CLI: $(command -v omarchy) ($(ls /usr/share/omarchy/version 2>/dev/null && cat /usr/share/omarchy/version 2>/dev/null | head -1))"
else
  echo "omarchy CLI: NOT PRESENT"
fi

echo
echo "=== end-to-end scenarios ==="
cd /src/test/e2e
python3 -m harness.scenarios "$@"
SCENARIO_RC=$?

exit $(( UNIT_RC != 0 || SCENARIO_RC != 0 ))
