#!/usr/bin/env bash
# OPTIONAL agent skill sidecar installer — NOT the primary product.
# Primary product: run the Portskill:
#   python3 -m port_registry_app
#   portskill
#   open macos/Portskill.app
#
# This script copies SKILL.md + thin wrappers + package so Claude/Codex/Cursor
# agents that want a skills/ folder can load the sidecar. Idempotent; never
# overwrites an existing registry.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CANONICAL_DIR="${PORT_REGISTRY_SKILL_DIR:-${HOME}/.agents/skills/port-registry}"
REGISTRY_DIR="${HOME}/.config/port-registry"
REGISTRY_FILE="${REGISTRY_DIR}/registry.json"
CLAUDE_LINK="${HOME}/.claude/skills/port-registry"
CODEX_DIR="${HOME}/.codex/skills/port-registry"

echo "port-registry skill sidecar install (optional)"
echo "  package:   ${PACKAGE_DIR}"
echo "  canonical: ${CANONICAL_DIR}"
echo "  tip:       run the app with:  python3 -m port_registry_app"
echo "             (from ${PACKAGE_DIR} with PYTHONPATH, or after pip install -e .)"

mkdir -p "${CANONICAL_DIR}"

# Skill entrypoint and supporting references
mkdir -p "${CANONICAL_DIR}/skill"
if [[ -f "${PACKAGE_DIR}/skill/SKILL.md" ]]; then
  cp -R "${PACKAGE_DIR}/skill/." "${CANONICAL_DIR}/skill/"
  cp "${PACKAGE_DIR}/SKILL.md" "${CANONICAL_DIR}/SKILL.md"
else
  cp "${PACKAGE_DIR}/SKILL.md" "${CANONICAL_DIR}/SKILL.md"
fi

# Thin wrappers + importable package + ui + examples + pyproject
cp "${PACKAGE_DIR}/port_registry.py" "${CANONICAL_DIR}/port_registry.py"
cp "${PACKAGE_DIR}/serve_ui.py" "${CANONICAL_DIR}/serve_ui.py"
cp "${PACKAGE_DIR}/app.py" "${CANONICAL_DIR}/app.py"
cp "${PACKAGE_DIR}/pyproject.toml" "${CANONICAL_DIR}/pyproject.toml"
chmod +x "${CANONICAL_DIR}/port_registry.py" "${CANONICAL_DIR}/serve_ui.py" "${CANONICAL_DIR}/app.py"

rm -rf "${CANONICAL_DIR}/port_registry_app"
cp -R "${PACKAGE_DIR}/port_registry_app" "${CANONICAL_DIR}/port_registry_app"
# Drop bytecode from copy
find "${CANONICAL_DIR}/port_registry_app" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

rm -rf "${CANONICAL_DIR}/ui"
cp -R "${PACKAGE_DIR}/ui" "${CANONICAL_DIR}/ui"

rm -rf "${CANONICAL_DIR}/examples"
cp -R "${PACKAGE_DIR}/examples" "${CANONICAL_DIR}/examples"

rm -rf "${CANONICAL_DIR}/macos"
cp -R "${PACKAGE_DIR}/macos" "${CANONICAL_DIR}/macos"

echo "  updated:   ${CANONICAL_DIR}/{SKILL.md,skill/,port_registry_app/,ui/,app.py,examples/,macos/}"

# Claude Code: symlink ~/.claude/skills/port-registry → canonical
mkdir -p "$(dirname "${CLAUDE_LINK}")"
if [[ -L "${CLAUDE_LINK}" ]]; then
  current="$(readlink "${CLAUDE_LINK}")"
  if [[ "${current}" == "${CANONICAL_DIR}" ]]; then
    echo "  claude:    symlink ok → ${CANONICAL_DIR}"
  else
    ln -sfn "${CANONICAL_DIR}" "${CLAUDE_LINK}"
    echo "  claude:    symlink retargeted → ${CANONICAL_DIR} (was ${current})"
  fi
elif [[ -d "${CLAUDE_LINK}" ]]; then
  echo "  claude:    SKIP — ${CLAUDE_LINK} is a real directory (not a symlink); leave it untouched"
elif [[ -e "${CLAUDE_LINK}" ]]; then
  echo "  claude:    SKIP — ${CLAUDE_LINK} exists and is not a symlink; leave it untouched"
else
  ln -s "${CANONICAL_DIR}" "${CLAUDE_LINK}"
  echo "  claude:    symlink created → ${CANONICAL_DIR}"
fi

# Codex: real directory copy synced fully from canonical each run
mkdir -p "${CODEX_DIR}"
rsync -a --delete \
  --exclude '__pycache__' \
  "${CANONICAL_DIR}/" "${CODEX_DIR}/" 2>/dev/null || {
  # fallback without rsync
  cp "${CANONICAL_DIR}/SKILL.md" "${CODEX_DIR}/SKILL.md"
  cp "${CANONICAL_DIR}/port_registry.py" "${CODEX_DIR}/port_registry.py"
  cp "${CANONICAL_DIR}/serve_ui.py" "${CODEX_DIR}/serve_ui.py"
  cp "${CANONICAL_DIR}/app.py" "${CODEX_DIR}/app.py"
  cp "${CANONICAL_DIR}/pyproject.toml" "${CODEX_DIR}/pyproject.toml"
  chmod +x "${CODEX_DIR}/port_registry.py" "${CODEX_DIR}/serve_ui.py" "${CODEX_DIR}/app.py"
  rm -rf "${CODEX_DIR}/port_registry_app" "${CODEX_DIR}/ui" "${CODEX_DIR}/skill" "${CODEX_DIR}/examples" "${CODEX_DIR}/macos"
  cp -R "${CANONICAL_DIR}/port_registry_app" "${CODEX_DIR}/port_registry_app"
  cp -R "${CANONICAL_DIR}/ui" "${CODEX_DIR}/ui"
  cp -R "${CANONICAL_DIR}/skill" "${CODEX_DIR}/skill" 2>/dev/null || true
  cp -R "${CANONICAL_DIR}/examples" "${CODEX_DIR}/examples"
  cp -R "${CANONICAL_DIR}/macos" "${CODEX_DIR}/macos"
}
echo "  codex:     real copy at ${CODEX_DIR} (synced from canonical)"

if [[ -n "${PORT_REGISTRY_SKILL_DIR:-}" ]]; then
  echo "  cursor:    PORT_REGISTRY_SKILL_DIR override in use (${PORT_REGISTRY_SKILL_DIR})"
else
  echo "  cursor:    use canonical ${CANONICAL_DIR} (or set PORT_REGISTRY_SKILL_DIR before install)"
fi

mkdir -p "${REGISTRY_DIR}"
if [[ -f "${REGISTRY_FILE}" ]]; then
  echo "  registry:  left existing file untouched (${REGISTRY_FILE})"
else
  printf '%s\n' '{"pool":{"end":29999,"start":20000},"projects":{},"version":1}' > "${REGISTRY_FILE}"
  echo "  registry:  initialized ${REGISTRY_FILE}"
fi

echo
echo "Skill sidecar install complete (optional)."
echo "Run the app:"
echo "  cd ${PACKAGE_DIR} && PYTHONPATH=. python3 -m port_registry_app"
echo "  # or from canonical:"
echo "  PYTHONPATH=${CANONICAL_DIR} python3 -m port_registry_app"
echo "CLI:"
echo "  PYTHONPATH=${CANONICAL_DIR} python3 -m port_registry_app.cli status"
echo "  PYTHONPATH=${CANONICAL_DIR} python3 -m port_registry_app.cli doctor"
