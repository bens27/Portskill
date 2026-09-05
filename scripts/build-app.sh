#!/bin/bash
# Build a self-contained Portskill.app (UI+MCP + Dock + menubar).
# Embeds port_registry_app under Contents/Resources/python/ so the bundle
# does not reach outside for the Python package.
#
# Output: <package>/dist/Portskill.app
# Also refreshes: <package>/macos/Portskill.app (launcher + icons; python embedded in dist)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_APP="${PACKAGE_ROOT}/dist/Portskill.app"
MACOS_APP="${PACKAGE_ROOT}/macos/Portskill.app"
MENU_DIR="${PACKAGE_ROOT}/macos/PortskillMenu"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/portskill-app.XXXXXX")"
trap 'rm -rf "${STAGE}"' EXIT

copy_python_tree() {
  local src="$1"
  local dst="$2"
  mkdir -p "${dst}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete       --exclude '__pycache__/'       --exclude '*.pyc'       --exclude '.DS_Store'       "${src}/" "${dst}/"
  else
    rm -rf "${dst}"
    mkdir -p "$(dirname "${dst}")"
    cp -R "${src}" "${dst}"
    find "${dst}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find "${dst}" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete 2>/dev/null || true
  fi
}

echo "== Portskill.app build =="
echo "Package: ${PACKAGE_ROOT}"

if [[ ! -f "${PACKAGE_ROOT}/port_registry_app/__main__.py" ]]; then
  echo "missing port_registry_app/__main__.py under ${PACKAGE_ROOT}" >&2
  exit 1
fi

CONTENTS="${STAGE}/Portskill.app/Contents"
mkdir -p "${CONTENTS}/MacOS" "${CONTENTS}/Resources/python"

# Info.plist
cp "${MACOS_APP}/Contents/Info.plist" "${CONTENTS}/Info.plist"

# Icons + menu icons
if [[ -f "${MACOS_APP}/Contents/Resources/AppIcon.icns" ]]; then
  cp "${MACOS_APP}/Contents/Resources/AppIcon.icns" "${CONTENTS}/Resources/"
fi
if [[ -f "${MACOS_APP}/Contents/Resources/AppIcon.png" ]]; then
  cp "${MACOS_APP}/Contents/Resources/AppIcon.png" "${CONTENTS}/Resources/"
fi
if [[ -d "${MACOS_APP}/Contents/Resources/AppIcon.iconset" ]]; then
  cp -R "${MACOS_APP}/Contents/Resources/AppIcon.iconset" "${CONTENTS}/Resources/"
fi
# Menu icons (from menu Resources or already in app Resources)
for f in MenuIcon.png diana.k@example.org; do
  if [[ -f "${MENU_DIR}/Resources/${f}" ]]; then
    cp "${MENU_DIR}/Resources/${f}" "${CONTENTS}/Resources/"
  elif [[ -f "${MACOS_APP}/Contents/Resources/${f}" ]]; then
    cp "${MACOS_APP}/Contents/Resources/${f}" "${CONTENTS}/Resources/"
  fi
done

# Embed Python package (stdlib module tree only; skip caches)
copy_python_tree \
  "${PACKAGE_ROOT}/port_registry_app" \
  "${CONTENTS}/Resources/python/port_registry_app"

# Bash fallback always written
BASH_SRC="${MACOS_APP}/Contents/MacOS/Portskill.bash"
if [[ ! -f "${BASH_SRC}" ]]; then
  echo "missing ${BASH_SRC}" >&2
  exit 1
fi
cp "${BASH_SRC}" "${CONTENTS}/MacOS/Portskill.bash"
chmod +x "${CONTENTS}/MacOS/Portskill.bash"

SWIFT_OK=0
if command -v swiftc >/dev/null 2>&1 && [[ -f "${MENU_DIR}/main.swift" ]]; then
  echo "Compiling native Swift shell…"
  if "${MENU_DIR}/build.sh"; then
    # build.sh installs into macos/Portskill.app; copy binary into staging
    if [[ -x "${MACOS_APP}/Contents/MacOS/Portskill" ]] && \
       file "${MACOS_APP}/Contents/MacOS/Portskill" | grep -q 'Mach-O'; then
      cp "${MACOS_APP}/Contents/MacOS/Portskill" "${CONTENTS}/MacOS/Portskill"
      chmod +x "${CONTENTS}/MacOS/Portskill"
      SWIFT_OK=1
      echo "Native Mach-O → Contents/MacOS/Portskill"
    else
      echo "WARN: build.sh ran but MacOS/Portskill is not Mach-O; using bash" >&2
    fi
  else
    echo "WARN: Swift build failed; using bash launcher" >&2
  fi
else
  echo "swiftc or main.swift unavailable — bash launcher only"
fi

if [[ "${SWIFT_OK}" -ne 1 ]]; then
  cp "${CONTENTS}/MacOS/Portskill.bash" "${CONTENTS}/MacOS/Portskill"
  chmod +x "${CONTENTS}/MacOS/Portskill"
  echo "Bash → Contents/MacOS/Portskill"
fi

# Ensure bash fallback still present after Swift install (build.sh may have copied it)
if [[ ! -f "${CONTENTS}/MacOS/Portskill.bash" ]]; then
  cp "${BASH_SRC}" "${CONTENTS}/MacOS/Portskill.bash"
  chmod +x "${CONTENTS}/MacOS/Portskill.bash"
fi

# Install dist/
mkdir -p "${PACKAGE_ROOT}/dist"
rm -rf "${DIST_APP}"
cp -R "${STAGE}/Portskill.app" "${DIST_APP}"

# Also refresh macos/Portskill.app launchers + embed python there for consistency
# (dev tree still has package-root fallback; embedded python makes either path work)
mkdir -p "${MACOS_APP}/Contents/Resources/python"
copy_python_tree \
  "${PACKAGE_ROOT}/port_registry_app" \
  "${MACOS_APP}/Contents/Resources/python/port_registry_app"
cp "${CONTENTS}/MacOS/Portskill.bash" "${MACOS_APP}/Contents/MacOS/Portskill.bash"
chmod +x "${MACOS_APP}/Contents/MacOS/Portskill.bash"
cp "${CONTENTS}/MacOS/Portskill" "${MACOS_APP}/Contents/MacOS/Portskill"
chmod +x "${MACOS_APP}/Contents/MacOS/Portskill"
for f in MenuIcon.png diana.k@example.org AppIcon.icns AppIcon.png; do
  if [[ -f "${CONTENTS}/Resources/${f}" ]]; then
    cp "${CONTENTS}/Resources/${f}" "${MACOS_APP}/Contents/Resources/" 2>/dev/null || true
  fi
done

SIZE="$(du -sh "${DIST_APP}" | awk '{print $1}')"
echo
echo "Built: ${DIST_APP}"
echo "Size:  ~${SIZE}"
if [[ "${SWIFT_OK}" -eq 1 ]]; then
  echo "Binary: native Swift (Mach-O) + Portskill.bash fallback"
else
  echo "Binary: bash launcher (Portskill.bash)"
fi
echo
echo "Open once:"
echo "  open \"${DIST_APP}\""
echo "Or verify without thrashing keepalive:"
echo "  test -f \"${DIST_APP}/Contents/Resources/python/port_registry_app/__main__.py\""
echo "  PYTHONPATH=\"${DIST_APP}/Contents/Resources/python\" python3 -c 'import port_registry_app'"
