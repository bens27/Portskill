#!/usr/bin/env bash
# Stdlib CLI with PYTHONPATH set so a cold clone does not need PYTHONPATH=.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "${ROOT}"
exec python3 -m port_registry_app.cli "$@"
