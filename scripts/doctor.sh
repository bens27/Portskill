#!/usr/bin/env bash
# Friend / agent doctor entry — sets PYTHONPATH and cwd.
# Do not document `PYTHONPATH=. python3 -m port_registry_app.cli doctor`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/scripts/cli.sh" doctor "$@"
