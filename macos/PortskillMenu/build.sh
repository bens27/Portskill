#!/bin/bash
# Build Portskill native Swift shell and install into Portskill.app (Dock + menubar).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "${ROOT}/../.." && pwd)"
OUT_DIR="${ROOT}/.build"
APP_MACOS="${PACKAGE_ROOT}/macos/Portskill.app/Contents/MacOS"
APP_RES="${PACKAGE_ROOT}/macos/Portskill.app/Contents/Resources"
MENU_APP="${ROOT}/PortskillMenu.app"
mkdir -p "${OUT_DIR}"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Install Xcode or Command Line Tools." >&2
  exit 1
fi

echo "Compiling Portskill native shell…"
swiftc -O -parse-as-library \
  -framework AppKit -framework Foundation \
  "${ROOT}/main.swift" \
  -o "${OUT_DIR}/PortskillMenu"

# Standalone menubar-only .app (LSUIElement) — optional
rm -rf "${MENU_APP}"
mkdir -p "${MENU_APP}/Contents/MacOS" "${MENU_APP}/Contents/Resources"
cp "${OUT_DIR}/PortskillMenu" "${MENU_APP}/Contents/MacOS/PortskillMenu"
chmod +x "${MENU_APP}/Contents/MacOS/PortskillMenu"
cp "${ROOT}/Info.plist" "${MENU_APP}/Contents/Info.plist"
if [[ -d "${ROOT}/Resources" ]]; then
  cp -R "${ROOT}/Resources/"* "${MENU_APP}/Contents/Resources/" 2>/dev/null || true
fi

# Install into Portskill.app as primary executable (Dock + menubar)
mkdir -p "${APP_MACOS}" "${APP_RES}"
if [[ -f "${APP_MACOS}/Portskill" ]] && file "${APP_MACOS}/Portskill" | grep -q 'script\|text'; then
  cp "${APP_MACOS}/Portskill" "${APP_MACOS}/Portskill.bash"
  echo "Preserved shell launcher → Portskill.bash"
fi
cp "${OUT_DIR}/PortskillMenu" "${APP_MACOS}/Portskill"
chmod +x "${APP_MACOS}/Portskill"
if [[ -d "${ROOT}/Resources" ]]; then
  cp -R "${ROOT}/Resources/"* "${APP_RES}/" 2>/dev/null || true
fi

echo "Built:"
echo "  ${OUT_DIR}/PortskillMenu"
echo "  ${MENU_APP}"
echo "  ${APP_MACOS}/Portskill  (Dock + menubar native)"
