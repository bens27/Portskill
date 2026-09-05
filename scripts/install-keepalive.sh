#!/bin/bash
# Install / uninstall / stop Portskill keep-alive (LaunchAgent + optional menubar).
# Idempotent. NEVER wipes ~/.config/port-registry/registry.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LABEL="local.portskill.keepalive"
# Template filename stays local.portskill.plist; Label inside is substituted label
PLIST_SRC="${PACKAGE_ROOT}/macos/launchd/local.portskill.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LEGACY_PLIST="${HOME}/Library/LaunchAgents/local.portskill.plist"
DAEMON="${PACKAGE_ROOT}/scripts/portskill-daemon.sh"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") <install|uninstall|stop|status|start>

  install     Build icons + menubar (best-effort), write LaunchAgent, bootstrap
  uninstall   bootout + remove plist (does not delete registry or package)
  stop        bootout agent and stop server/menu (keep plist for next login)
  start       bootstrap/kickstart agent (after install)
  status      show launchctl + listening port

Package root: ${PACKAGE_ROOT}
LaunchAgent:  ${PLIST_DST}
Label:        ${LABEL}
Registry:     ~/.config/port-registry/registry.json  (never modified here)
Listen:       ~/.config/port-registry/listen.json    (written by server after bind)
USAGE
}

ensure_logs() {
  mkdir -p "${HOME}/Library/Logs/Portskill"
  mkdir -p "${HOME}/Library/LaunchAgents"
}

write_plist() {
  ensure_logs
  if [[ ! -f "${PLIST_SRC}" ]]; then
    echo "missing template: ${PLIST_SRC}" >&2
    exit 1
  fi
  chmod +x "${DAEMON}" 2>/dev/null || true
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|__PORTSKILL_DAEMON__|${DAEMON}|g" \
    -e "s|__PORTSKILL_PACKAGE_ROOT__|${PACKAGE_ROOT}|g" \
    -e "s|__HOME__|${HOME}|g" \
    -e "s|<string>local.portskill</string>|<string>${LABEL}</string>|g" \
    -e "s|<string>local.portskill.keepalive</string>|<string>${LABEL}</string>|g" \
    "${PLIST_SRC}" > "${tmp}"
  # Ensure Label matches (sed above handles template)
  if [[ -f "${PLIST_DST}" ]] && cmp -s "${tmp}" "${PLIST_DST}"; then
    rm -f "${tmp}"
    echo "LaunchAgent plist unchanged: ${PLIST_DST}"
  else
    mv "${tmp}" "${PLIST_DST}"
    echo "Wrote ${PLIST_DST}"
  fi
}

bootout_label() {
  local lab="$1"
  launchctl bootout "${DOMAIN}/${lab}" 2>/dev/null || \
  launchctl unload "${HOME}/Library/LaunchAgents/${lab}.plist" 2>/dev/null || true
}

bootout() {
  bootout_label "${LABEL}"
  bootout_label "local.portskill"  # legacy
}

bootstrap() {
  if launchctl bootstrap "${DOMAIN}" "${PLIST_DST}" 2>/tmp/portskill-bootstrap.err; then
    echo "bootstrapped ${DOMAIN}/${LABEL}"
  else
    cat /tmp/portskill-bootstrap.err >&2 || true
    launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "${DOMAIN}" "${PLIST_DST}" 2>/dev/null || \
      launchctl load "${PLIST_DST}" 2>/dev/null || true
  fi
  launchctl kickstart -k "${DOMAIN}/${LABEL}" 2>/dev/null || true
}

kill_menu() {
  pkill -f '/PortskillMenu$' 2>/dev/null || true
  pkill -f 'PortskillMenu.app/Contents/MacOS/PortskillMenu' 2>/dev/null || true
}

kill_server() {
  pkill -f 'Portskill.app/Contents/MacOS/Portskill$' 2>/dev/null || true
  pkill -f -- '-m port_registry_app' 2>/dev/null || true
}

cmd_install() {
  echo "== Portskill keepalive install =="
  echo "Package: ${PACKAGE_ROOT}"
  if [[ -f "${HOME}/.config/port-registry/registry.json" ]]; then
    echo "Registry present (left untouched): ${HOME}/.config/port-registry/registry.json"
  fi

  if [[ -x "${PACKAGE_ROOT}/scripts/build-icons.sh" ]]; then
    "${PACKAGE_ROOT}/scripts/build-icons.sh" || true
  fi

  if [[ -x "${PACKAGE_ROOT}/scripts/build-app.sh" ]]; then
    "${PACKAGE_ROOT}/scripts/build-app.sh" || echo "WARN: build-app.sh failed (will try menu build)" >&2
  elif [[ -x "${PACKAGE_ROOT}/macos/PortskillMenu/build.sh" ]]; then
    if command -v swiftc >/dev/null 2>&1; then
      "${PACKAGE_ROOT}/macos/PortskillMenu/build.sh" || echo "WARN: menubar build failed (Dock/keepalive still OK)" >&2
    else
      echo "swiftc not found — shipping sources only; Dock + LaunchAgent still install."
    fi
  fi

  chmod +x "${PACKAGE_ROOT}/macos/Portskill.app/Contents/MacOS/Portskill" 2>/dev/null || true
  chmod +x "${PACKAGE_ROOT}/macos/Portskill.app/Contents/MacOS/Portskill.bash" 2>/dev/null || true
  chmod +x "${PACKAGE_ROOT}/dist/Portskill.app/Contents/MacOS/Portskill" 2>/dev/null || true
  chmod +x "${DAEMON}" 2>/dev/null || true

  # Remove legacy agent if present
  if [[ -f "${LEGACY_PLIST}" ]]; then
    bootout_label "local.portskill"
    rm -f "${LEGACY_PLIST}"
    echo "Removed legacy ${LEGACY_PLIST}"
  fi

  write_plist
  bootout
  bootstrap

  echo
  echo "Installed. UI URL: see ~/.config/port-registry/listen.json (sticky; was historically :8765)"
  echo "Disable:  ${SCRIPT_DIR}/install-keepalive.sh uninstall"
  echo "Stop now: ${SCRIPT_DIR}/install-keepalive.sh stop"
  echo "Logs:     ~/Library/Logs/Portskill/"
}

cmd_uninstall() {
  echo "== Portskill keepalive uninstall =="
  bootout
  kill_menu
  kill_server
  rm -f "${PLIST_DST}" "${LEGACY_PLIST}"
  echo "Removed agent plists (if present). Registry NOT modified."
}

cmd_stop() {
  echo "== Portskill keepalive stop =="
  bootout
  kill_menu
  kill_server
  # Give the previous bind a moment to release so sticky listen.json can reuse.
  sleep 1
  echo "Stopped (plist kept at ${PLIST_DST} if installed)."
}

cmd_start() {
  if [[ ! -f "${PLIST_DST}" ]]; then
    echo "Not installed. Run: $0 install" >&2
    exit 1
  fi
  bootstrap
  echo "Started ${LABEL}"
}

cmd_status() {
  echo "== Portskill keepalive status =="
  echo "plist: ${PLIST_DST} $([ -f "${PLIST_DST}" ] && echo PRESENT || echo MISSING)"
  launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | head -40 || echo "(agent not loaded)"
  echo
  LISTEN_JSON="${HOME}/.config/port-registry/listen.json"
  PORT=""
  if [[ -f "${LISTEN_JSON}" ]]; then
    echo "listen.json: ${LISTEN_JSON}"
    if command -v python3 >/dev/null 2>&1; then
      python3 - "${LISTEN_JSON}" <<'PY'
import json, sys
path = sys.argv[1]
try:
    d = json.load(open(path))
except Exception as e:
    print("unreadable:", e)
    raise SystemExit(0)
port = d.get("port")
print("port=%s listening=%s" % (port, d.get("listening")))
print("ui_url=%s" % (d.get("ui_url"),))
print("mcp_url=%s" % (d.get("mcp_url"),))
open("/tmp/portskill-status-port", "w").write(str(port or ""))
PY
      PORT="$(cat /tmp/portskill-status-port 2>/dev/null || true)"
    else
      cat "${LISTEN_JSON}"
    fi
  else
    echo "listen.json: MISSING (server not broadcast yet; historical default :8765)"
    PORT=8765
  fi
  if [[ -n "${PORT}" ]] && command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || echo "Nothing listening on :${PORT}"
  fi
  pgrep -lf "Portskill.app/Contents/MacOS/Portskill" 2>/dev/null || echo "Native Portskill.app: not running"
  pgrep -lf PortskillMenu 2>/dev/null || true
  pgrep -lf "port_registry_app" 2>/dev/null || echo "Server module: not in pgrep"
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    install) cmd_install ;;
    uninstall|remove) cmd_uninstall ;;
    stop) cmd_stop ;;
    start) cmd_start ;;
    status) cmd_status ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 1 ;;
    *) echo "Unknown command: $cmd" >&2; usage; exit 2 ;;
  esac
}

main "$@"
