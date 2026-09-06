#!/bin/bash
# Friend-grade macOS install: build (or reuse) Portskill.app, copy to Applications,
# strip Gatekeeper quarantine, optionally start keepalive, print the UI URL.
#
# Primary friend path — not a developer ritual. Does not implement remotes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=mac-bundle.sh
. "${SCRIPT_DIR}/mac-bundle.sh"
DIST_APP="${PACKAGE_ROOT}/dist/Portskill.app"
LISTEN_JSON="${HOME}/.config/port-registry/listen.json"

DEST="/Applications"
USER_INSTALL=0
KEEPALIVE=0
REBUILD=0
OPEN_APP=0

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Install Portskill.app so a friend can double-click it like a real Mac app.

  --user          Install to ~/Applications (no admin)
  --dest DIR      Install into DIR (default: /Applications)
  --rebuild       Always run build-app.sh even if dist/ already exists
  --keepalive     After install, run scripts/install-keepalive.sh install
  --open          open the installed app when finished
  -h, --help      Show this help

If dist/Portskill.app is already built, it is reused. Otherwise this script
runs ./scripts/build-app.sh first.

After a trusted local build, quarantine is stripped with:
  xattr -dr com.apple.quarantine <installed.app>

Until a build is Apple-notarized (see scripts/notarize-mac.sh), friends who
skip this script can still first-launch via right-click → Open.
USAGE
}

need_darwin() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "install-mac.sh is for macOS (found $(uname -s))." >&2
    exit 1
  fi
}

app_looks_complete() {
  local app="$1"
  [[ -x "${app}/Contents/MacOS/Portskill" ]] && \
    [[ -f "${app}/Contents/Resources/python/port_registry_app/__main__.py" ]]
}

print_ui_url() {
  if [[ ! -f "${LISTEN_JSON}" ]]; then
    echo "UI URL: not written yet (double-click Portskill, then re-read ${LISTEN_JSON})"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "${LISTEN_JSON}" <<'PY'
import json, sys
path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception as exc:
    print("UI URL: listen.json unreadable (%s)" % exc)
    raise SystemExit(0)
url = data.get("ui_url") or ""
port = data.get("port")
listening = data.get("listening")
if url:
    print("UI URL: %s" % url)
else:
    print("UI URL: missing ui_url in %s" % path)
if port is not None:
    print("listen port: %s  listening=%s" % (port, listening))
PY
  else
    echo "listen.json: ${LISTEN_JSON}"
    cat "${LISTEN_JSON}"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) USER_INSTALL=1; shift ;;
    --dest)
      if [[ $# -lt 2 ]]; then
        echo "--dest requires a directory" >&2
        exit 2
      fi
      DEST="$2"
      shift 2
      ;;
    --rebuild) REBUILD=1; shift ;;
    --keepalive) KEEPALIVE=1; shift ;;
    --open) OPEN_APP=1; shift ;;
    -h|--help|help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

need_darwin

if [[ "${USER_INSTALL}" -eq 1 ]]; then
  DEST="${HOME}/Applications"
fi

if [[ "${REBUILD}" -eq 1 ]] || ! app_looks_complete "${DIST_APP}"; then
  echo "== Building Portskill.app =="
  if [[ ! -x "${PACKAGE_ROOT}/scripts/build-app.sh" ]]; then
    echo "missing ${PACKAGE_ROOT}/scripts/build-app.sh" >&2
    exit 1
  fi
  "${PACKAGE_ROOT}/scripts/build-app.sh"
else
  echo "Using existing ${DIST_APP}"
fi

if ! app_looks_complete "${DIST_APP}"; then
  echo "dist/Portskill.app is incomplete after build: ${DIST_APP}" >&2
  exit 1
fi

mkdir -p "${DEST}"
INSTALLED="${DEST}/Portskill.app"

# Single-flight with keepalive / overlapping install-mac (same lock as replace).
_install_mac_place() {
  replace_app_bundle "${DIST_APP}" "${INSTALLED}"
  chmod +x "${INSTALLED}/Contents/MacOS/Portskill" 2>/dev/null || true
  chmod +x "${INSTALLED}/Contents/MacOS/Portskill.bash" 2>/dev/null || true
  if command -v xattr >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "${INSTALLED}" || true
    echo "Stripped com.apple.quarantine on ${INSTALLED}"
  else
    echo "WARN: xattr not found — if Gatekeeper blocks, right-click → Open once." >&2
  fi
}
with_install_lock _install_mac_place

echo
echo "Installed: ${INSTALLED}"
echo "Open:      open \"${INSTALLED}\""
echo "Or double-click Portskill in $(basename "${DEST}")."

if [[ "${KEEPALIVE}" -eq 1 ]]; then
  echo
  echo "== Keepalive =="
  "${PACKAGE_ROOT}/scripts/install-keepalive.sh" install
fi

if [[ "${OPEN_APP}" -eq 1 ]]; then
  open "${INSTALLED}"
  # Give the server a moment to rewrite listen.json when we launched it.
  sleep 2
fi

echo
print_ui_url
echo "listen.json: ${LISTEN_JSON}"
echo
echo "Gatekeeper: this install stripped quarantine after a trusted local build."
echo "If a friend received an unsigned zip another way, right-click → Open once,"
echo "or re-run this script. Notarization: ./scripts/notarize-mac.sh (maintainer)."
