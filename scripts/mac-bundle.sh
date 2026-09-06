#!/bin/bash
# Shared Mac bundle helpers (sourced by install-mac.sh / build-app.sh /
# notarize-mac.sh). Safe on Linux — no Darwin requirement.
#
# replace_app_bundle: copy src.app onto dest.app without a half-replaced
# bundle. Stage next to dest, rm -rf dest and verify it is gone (rename-aside
# if a first rm leaves residue), then mv -f into place. Idempotent on re-run.
# strip_finder_junk: delete .DS_Store / AppleDouble sidecars that unseal
# codesign ("unsealed contents present in the bundle root").

remove_tree_verified() {
  local path="$1"
  local aside
  [[ -e "${path}" ]] || return 0
  rm -rf "${path}"
  if [[ -e "${path}" ]]; then
    aside="${path}.old.$$"
    rm -rf "${aside}"
    mv -f "${path}" "${aside}" 2>/dev/null || true
    rm -rf "${path}" "${aside}"
  fi
  if [[ -e "${path}" ]]; then
    echo "failed to remove ${path}" >&2
    return 1
  fi
  return 0
}

replace_app_bundle() {
  local src="$1"
  local dest="$2"
  local dest_dir dest_base stage leftover
  if [[ ! -d "${src}" ]]; then
    echo "replace_app_bundle: missing source ${src}" >&2
    return 1
  fi
  dest_dir="$(dirname "${dest}")"
  dest_base="$(basename "${dest}")"
  mkdir -p "${dest_dir}"
  # Sweep leftover staging dirs from an interrupted prior replace.
  for leftover in "${dest_dir}/.${dest_base}.new."*; do
    if [[ -e "${leftover}" ]]; then
      rm -rf "${leftover}"
    fi
  done
  stage="${dest_dir}/.${dest_base}.new.$$"
  rm -rf "${stage}"
  cp -R "${src}" "${stage}"
  if [[ -e "${dest}" ]]; then
    echo "Replacing ${dest}"
    remove_tree_verified "${dest}" || {
      rm -rf "${stage}"
      return 1
    }
  fi
  mv -f "${stage}" "${dest}"
  if [[ ! -d "${dest}" ]]; then
    echo "replace failed: ${dest} missing after mv" >&2
    return 1
  fi
}

strip_finder_junk() {
  local app="$1"
  [[ -d "${app}" ]] || return 0
  # .DS_Store and AppleDouble / resource-fork sidecars unseal the bundle.
  find "${app}" \( -name '.DS_Store' -o -name '._*' \) -type f -delete 2>/dev/null || true
}
