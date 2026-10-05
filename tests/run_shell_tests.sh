#!/usr/bin/env bash
# Runs every shell test of the installer, uninstaller, seal and first-start tools, one after the other.
# Needs root (the installer refuses to run otherwise) and the ports 18080-18084, 18091 and 18092 (override: PORT=… and
# RELPORT=… apply to the single tests; this runner uses each test's own default). Nothing here changes the real system.
#   sudo bash tests/run_shell_tests.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
failed=()
for t in units install fetch migrate seal firstboot; do
  printf '\n=== test_%s.sh\n' "$t"
  bash "$HERE/test_$t.sh" || failed+=("$t")
done
if (( ${#failed[@]} )); then
  printf '\nFAILED: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf '\nall shell tests passed\n'
