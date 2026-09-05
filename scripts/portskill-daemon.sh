#!/bin/bash
# LaunchAgent entrypoint.
# Outer loop restarts the UI+MCP server if it exits/crashes (does not rely solely
# on launchd KeepAlive after SIGKILL, which modern macOS may pend as "inefficient").
# Sidecar: native Portskill.app in a new session (Dock + menubar).
# Never touches registry.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PORTSKILL_PACKAGE_ROOT="${PACKAGE_ROOT}"
export PYTHONPATH="${PACKAGE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PORTSKILL_KEEPALIVE=1
export PORTSKILL_NO_OPEN=1

mkdir -p "${HOME}/Library/Logs/Portskill"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] portskill-daemon start package=${PACKAGE_ROOT}"

APP_BIN="${PACKAGE_ROOT}/macos/Portskill.app/Contents/MacOS/Portskill"
cd "${PACKAGE_ROOT}"

if command -v python3 >/dev/null 2>&1; then PY=python3
elif [[ -x /opt/homebrew/bin/python3 ]]; then PY=/opt/homebrew/bin/python3
elif [[ -x /usr/bin/python3 ]]; then PY=/usr/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then PY=/usr/local/bin/python3
else echo "python3 not found" >&2; exit 1
fi

ensure_native() {
  [[ -x "${APP_BIN}" ]] || return 0
  file "${APP_BIN}" | grep -q 'Mach-O' || return 0
  if pgrep -f 'Portskill.app/Contents/MacOS/Portskill$' >/dev/null 2>&1; then
    return 0
  fi
  "${PY}" - <<PY
import os, subprocess
env = os.environ.copy()
env["PORTSKILL_PACKAGE_ROOT"] = "${PACKAGE_ROOT}"
env["PORTSKILL_KEEPALIVE"] = "1"
env["PORTSKILL_NO_OPEN"] = "1"
env["PORTSKILL_SERVER_EXTERNAL"] = "1"
subprocess.Popen(
    ["${APP_BIN}"],
    env=env,
    start_new_session=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print("started native Dock/menubar (detached session)", flush=True)
PY
}

# Stay in foreground as the LaunchAgent job. Restart server on exit.
# Exit the loop only on SIGTERM from launchctl bootout (stop/uninstall/Quit).
STOP=0
on_term() { STOP=1; }
trap on_term TERM INT

while [[ "$STOP" -eq 0 ]]; do
  ensure_native
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting python UI+MCP"
  set +e
  "${PY}" -m port_registry_app --no-open
  rc=$?
  set -e
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] python exited rc=${rc}"
  if [[ "$STOP" -eq 1 ]]; then
    break
  fi
  sleep 2
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] portskill-daemon stopping"
exit 0
