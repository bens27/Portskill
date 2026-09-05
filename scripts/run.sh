#!/usr/bin/env bash
# Module launch with PYTHONPATH set (UI + MCP HTTP by default).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "${ROOT}"
exec python3 -m port_registry_app "$@"
