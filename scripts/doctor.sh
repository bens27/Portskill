#!/usr/bin/env bash
# Friend / agent doctor entry — sets PYTHONPATH and cwd.
# Do not document `PYTHONPATH=. python3 -m port_registry_app.cli doctor`.
# Exit codes match CLI doctor: 0 healthy / informational warnings; 2 fail-closed.
# See README "Doctor exit contract".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/scripts/cli.sh" doctor "$@"
