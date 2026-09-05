#!/usr/bin/env bash
# Only documented smoke / test entry.
# Sets PYTHONPATH and cwd so friends and CI never need
# `PYTHONPATH=. python3 tests/smoke_test.py`.
#
# Runs the friend smoke, then the expanded stdlib unittest suite (test_*.py).
# Linux offline; no Mac .app required.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "${ROOT}"
python3 "${ROOT}/tests/smoke_test.py" "$@"
python3 -m unittest discover -s "${ROOT}/tests" -t "${ROOT}" -p "test_*.py" -v
