#!/usr/bin/env bash
# DEPRECATED as the primary path — the product is Portskill.
#   portskill
#   python3 -m port_registry_app
# This wrapper still runs the optional skill sidecar installer for agents.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "note: product is Portskill (portskill / python3 -m port_registry_app), not this installer." >&2
exec "${SCRIPT_DIR}/scripts/install-skill.sh" "$@"
