#!/bin/bash
# Double-clickable Portskill.app — UI+MCP server (+ optional menubar helper).
# Shows in Dock (LSUIElement false). LaunchAgent may also invoke this binary.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CONTENTS="$(cd "${HERE}/.." && pwd)"
EMBEDDED="${CONTENTS}/Resources/python"
if [[ -d "${EMBEDDED}/port_registry_app" ]]; then
  # Self-contained bundle (dist/Portskill.app or packaged macos/Portskill.app)
  PACKAGE_ROOT="${EMBEDDED}"
else
  # Dev: …/macos/Portskill.app/Contents/MacOS → package root is ../../../../
  PACKAGE_ROOT="$(cd "${HERE}/../../../.." && pwd)"
fi
export PYTHONPATH="${PACKAGE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PORTSKILL_PACKAGE_ROOT="${PACKAGE_ROOT}"

# Prefer python3 on PATH; fall back to common locations
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then
  PY=/usr/local/bin/python3
elif [[ -x /opt/homebrew/bin/python3 ]]; then
  PY=/opt/homebrew/bin/python3
else
  osascript -e 'display alert "Portskill" message "python3 not found on PATH. Install Python 3 or open a Terminal and run: python3 -m port_registry_app" as critical' 2>/dev/null || true
  echo "python3 not found" >&2
  exit 1
fi

# Start menubar helper if built and not already running (best-effort).
# Prefer nothing outside the bundle when embedded python is present.
MENU_BIN=""
if [[ ! -d "${EMBEDDED}/port_registry_app" ]]; then
  MENU_BIN="${PACKAGE_ROOT}/macos/PortskillMenu/.build/PortskillMenu"
  MENU_APP="${PACKAGE_ROOT}/macos/PortskillMenu/PortskillMenu.app/Contents/MacOS/PortskillMenu"
fi
start_menu() {
  local bin="$1"
  if [[ -z "$bin" || ! -x "$bin" ]]; then
    return 0
  fi
  if pgrep -f '/PortskillMenu$' >/dev/null 2>&1 || pgrep -xf "$bin" >/dev/null 2>&1; then
    return 0
  fi
  PORTSKILL_PACKAGE_ROOT="${PACKAGE_ROOT}" "$bin" >/dev/null 2>&1 &
  disown || true
}
if [[ -n "${MENU_APP:-}" ]]; then start_menu "$MENU_APP"; fi
if [[ -n "${MENU_BIN:-}" ]]; then start_menu "$MENU_BIN"; fi

cd "${PACKAGE_ROOT}"
# When launched by LaunchAgent, skip auto-browser unless PORTSKILL_OPEN_BROWSER=1
if [[ "${PORTSKILL_NO_OPEN:-}" == "1" ]] || [[ "${PORTSKILL_KEEPALIVE:-}" == "1" ]]; then
  exec "${PY}" -m port_registry_app --no-open "$@"
fi
exec "${PY}" -m port_registry_app "$@"
