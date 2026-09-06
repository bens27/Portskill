#!/bin/bash
# Shared Mac bundle helpers (sourced by build-app.sh). Safe on Linux —
# no Darwin requirement.
#
# replace_app_bundle: copy src.app onto dest.app without a half-replaced
# bundle. Stage as a *sibling* of dest (dirname/dest/.Portskill.app.new.$$),
# never inside the .app. flock-serialize overlapping keepalive / rebuild
# replaces. rm -rf dest and verify it is gone (rename-aside if a first rm
# leaves residue), then mv -f into place only if dest is absent (POSIX mv
# into an existing dir would nest the stage and unseal codesign).
# strip_finder_junk / purge_bundle_root_residue: delete .DS_Store,
# AppleDouble sidecars, leftover .*.new.* stage dirs, and any non-Contents
# junk at the bundle root ("unsealed contents present in the bundle root").

_strip_trailing_slashes() {
  local p="$1"
  while [[ "${p}" == */ && "${p}" != "/" ]]; do
    p="${p%/}"
  done
  printf '%s' "${p}"
}

# Sibling stage path for dest (unique via $$). Never dest/.something.
bundle_stage_path() {
  local dest dest_dir dest_base
  dest="$(_strip_trailing_slashes "$1")"
  dest_dir="$(dirname "${dest}")"
  dest_base="$(basename "${dest}")"
  printf '%s' "${dest_dir}/.${dest_base}.new.$$"
}

portskill_install_lock_path() {
  if [[ -n "${PORTSKILL_INSTALL_LOCK:-}" ]]; then
    printf '%s' "${PORTSKILL_INSTALL_LOCK}"
    return 0
  fi
  if [[ "$(uname -s)" == "Darwin" ]]; then
    mkdir -p "${HOME}/Library/Caches/Portskill"
    printf '%s' "${HOME}/Library/Caches/Portskill/install.lock"
    return 0
  fi
  printf '%s' "${TMPDIR:-/tmp}/portskill-install.lock"
}

# Single-flight around replace / keepalive rebuild critical section.
# Re-entrant in the same shell (nested replace under an outer lock).
# flock(1) on Linux; python3 fcntl fallback on stock macOS.
with_install_lock() {
  local rc=0
  local lock
  if [[ "${PORTSKILL_INSTALL_LOCK_HELD:-0}" == "1" ]]; then
    "$@"
    return $?
  fi
  lock="$(portskill_install_lock_path)"
  mkdir -p "$(dirname "${lock}")"
  : >>"${lock}"
  if command -v flock >/dev/null 2>&1; then
    exec 8>>"${lock}"
    flock 8 || return 1
    PORTSKILL_INSTALL_LOCK_HELD=1
    "$@" || rc=$?
    PORTSKILL_INSTALL_LOCK_HELD=0
    flock -u 8 2>/dev/null || true
    exec 8>&-
    return "${rc}"
  fi
  _with_install_lock_python "${lock}" "$@"
}

_with_install_lock_python() {
  local lock="$1"
  shift
  local rc=0
  local ready holder i
  ready="${lock}.$$.$RANDOM.ready"
  python3 - "${lock}" "${ready}" <<'PY' &
import fcntl, os, sys, time
lock, ready = sys.argv[1], sys.argv[2]
fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
with open(ready, "w", encoding="utf-8") as fh:
    fh.write("ok\n")
while os.path.exists(ready):
    time.sleep(0.05)
PY
  holder=$!
  for ((i = 0; i < 200; i++)); do
    if [[ -f "${ready}" ]]; then
      break
    fi
    if ! kill -0 "${holder}" 2>/dev/null; then
      echo "replace_app_bundle: lock holder exited" >&2
      return 1
    fi
    sleep 0.05
  done
  if [[ ! -f "${ready}" ]]; then
    echo "replace_app_bundle: timed out acquiring install lock" >&2
    return 1
  fi
  PORTSKILL_INSTALL_LOCK_HELD=1
  "$@" || rc=$?
  PORTSKILL_INSTALL_LOCK_HELD=0
  rm -f "${ready}"
  wait "${holder}" 2>/dev/null || true
  return "${rc}"
}

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

# Leftover sibling / nested stage dirs from an interrupted or raced replace.
sweep_sibling_stage_dirs() {
  local dest_dir="$1"
  local leftover
  [[ -d "${dest_dir}" ]] || return 0
  for leftover in "${dest_dir}"/.*.new.*; do
    if [[ -e "${leftover}" || -L "${leftover}" ]]; then
      rm -rf "${leftover}"
    fi
  done
}

# Bundle root must be only Contents/. Nested .*.new.* (raced mv) and any
# other non-Contents entry unseals ad-hoc codesign.
purge_bundle_root_residue() {
  local app="$1"
  local p base
  app="$(_strip_trailing_slashes "${app}")"
  [[ -d "${app}" ]] || return 0
  for p in "${app}"/.*.new.*; do
    if [[ -e "${p}" || -L "${p}" ]]; then
      rm -rf "${p}"
    fi
  done
  for p in "${app}"/* "${app}"/.[!.]* "${app}"/..?*; do
    if [[ ! -e "${p}" && ! -L "${p}" ]]; then
      continue
    fi
    base="$(basename "${p}")"
    if [[ "${base}" != "Contents" ]]; then
      rm -rf "${p}"
    fi
  done
}

_replace_test_hook() {
  if [[ -n "${PORTSKILL_TEST_REPLACE_SLEEP:-}" ]]; then
    sleep "${PORTSKILL_TEST_REPLACE_SLEEP}"
  fi
}

_replace_app_bundle_unlocked() {
  local src="$1"
  local dest="$2"
  local dest_dir dest_base stage leftover
  if [[ -n "${PORTSKILL_TEST_LOCK_LOG:-}" ]]; then
    echo "${PORTSKILL_TEST_LOCK_TAG:-x}-in" >> "${PORTSKILL_TEST_LOCK_LOG}"
  fi
  if [[ ! -d "${src}" ]]; then
    echo "replace_app_bundle: missing source ${src}" >&2
    return 1
  fi
  dest="$(_strip_trailing_slashes "${dest}")"
  dest_dir="$(dirname "${dest}")"
  dest_base="$(basename "${dest}")"
  mkdir -p "${dest_dir}"
  if [[ -d "${dest}" ]]; then
    # Strip raced nested stages before we treat dest as a real bundle.
    purge_bundle_root_residue "${dest}"
  fi
  sweep_sibling_stage_dirs "${dest_dir}"
  stage="${dest_dir}/.${dest_base}.new.$$"
  case "${stage}" in
    "${dest}"/*)
      echo "replace_app_bundle: refuse stage inside dest (${stage})" >&2
      return 1
      ;;
  esac
  rm -rf "${stage}"
  cp -R "${src}" "${stage}"
  # Tests observe that stage is a sibling (dest still present here).
  _replace_test_hook
  if [[ -e "${dest}" ]]; then
    echo "Replacing ${dest}"
    remove_tree_verified "${dest}" || {
      rm -rf "${stage}"
      return 1
    }
  fi
  # POSIX mv into an existing directory nests the stage (unseals codesign)
  # and can print "Directory not empty" when residue already sits inside.
  if [[ -e "${dest}" ]]; then
    echo "replace refused: ${dest} still exists (mv would nest stage)" >&2
    rm -rf "${stage}"
    return 1
  fi
  mv -f "${stage}" "${dest}"
  if [[ ! -d "${dest}" ]]; then
    echo "replace failed: ${dest} missing after mv" >&2
    return 1
  fi
  purge_bundle_root_residue "${dest}"
  strip_finder_junk "${dest}"
  for leftover in "${dest}"/.*.new.*; do
    if [[ -e "${leftover}" || -L "${leftover}" ]]; then
      echo "replace failed: leftover stage still inside ${dest}" >&2
      return 1
    fi
  done
  if [[ -n "${PORTSKILL_TEST_LOCK_LOG:-}" ]]; then
    echo "${PORTSKILL_TEST_LOCK_TAG:-x}-out" >> "${PORTSKILL_TEST_LOCK_LOG}"
  fi
}

replace_app_bundle() {
  with_install_lock _replace_app_bundle_unlocked "$@"
}

strip_finder_junk() {
  local app="$1"
  app="$(_strip_trailing_slashes "${app}")"
  [[ -d "${app}" ]] || return 0
  purge_bundle_root_residue "${app}"
  # .DS_Store and AppleDouble / resource-fork sidecars unseal the bundle.
  find "${app}" \( -name '.DS_Store' -o -name '._*' \) -type f -delete 2>/dev/null || true
}
