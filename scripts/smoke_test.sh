#!/usr/bin/env bash
# Non-destructive Portskill smoke: import + CLI status/doctor + optional HTTP from listen.json
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "${ROOT}/tests/smoke_test.py" "$@"
