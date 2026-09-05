#!/bin/bash
# Build AppIcon.icns from AppIcon.iconset (macOS iconutil).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICONSET="${ROOT}/macos/Portskill.app/Contents/Resources/AppIcon.iconset"
OUT="${ROOT}/macos/Portskill.app/Contents/Resources/AppIcon.icns"
if [[ ! -d "${ICONSET}" ]]; then
  echo "missing iconset: ${ICONSET}" >&2
  exit 1
fi
if ! command -v iconutil >/dev/null 2>&1; then
  echo "iconutil not found (macOS only); skipping icns" >&2
  exit 0
fi
iconutil -c icns "${ICONSET}" -o "${OUT}"
echo "Wrote ${OUT}"
