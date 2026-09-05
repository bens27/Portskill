#!/bin/bash
# Codesign + notarytool + staple for dist/Portskill.app.
#
# Does NOT claim success unless notarytool + stapler actually finish.
# Fails closed when signing identity or notarization credentials are missing.
# Never prints secrets. Do not commit API keys / app-specific passwords.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_APP="${PACKAGE_ROOT}/dist/Portskill.app"
DIST_DIR="${PACKAGE_ROOT}/dist"
MAKE_DMG=0
SKIP_STAPLE=0

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Sign dist/Portskill.app with Developer ID Application, submit to Apple
notarytool, wait, and staple the ticket.

  --dmg           Also wrap the app in a UDZO dmg before submit (default: zip)
  --skip-staple   Submit only; do not stapler staple
  -h, --help      Show this help

Signing identity (first match wins):
  PORTSKILL_SIGN_IDENTITY   exact codesign identity string
  or auto-detect the first "Developer ID Application" identity

Notarization credentials — one of these two sets is required:

  App Store Connect API key:
    APP_STORE_CONNECT_API_KEY_PATH
    APP_STORE_CONNECT_ISSUER_ID
    APP_STORE_CONNECT_KEY_ID

  Apple ID + app-specific password:
    APPLE_ID
    APPLE_APP_SPECIFIC_PASSWORD   (or APPLE_PASSWORD)
    APPLE_TEAM_ID

Optional: PORTSKILL_BUNDLE_ID (codesign identifier; default from Info.plist).

This script does not store or commit credentials. It will not print a success
line unless notarytool reports Accepted and staple (if enabled) succeeds.
USAGE
}

need_darwin() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "notarize-mac.sh is for macOS (found $(uname -s))." >&2
    exit 1
  fi
}

creds_hint() {
  cat <<'HINT' >&2
Missing notarization credentials. Set one complete set, then retry.

  App Store Connect API key:
    APP_STORE_CONNECT_API_KEY_PATH   path to AuthKey_XXXXXX.p8
    APP_STORE_CONNECT_ISSUER_ID      issuer UUID
    APP_STORE_CONNECT_KEY_ID         key id

  Apple ID + app-specific password:
    APPLE_ID
    APPLE_APP_SPECIFIC_PASSWORD      (or APPLE_PASSWORD)
    APPLE_TEAM_ID

Signing identity (if not auto-detected):
    PORTSKILL_SIGN_IDENTITY          Developer ID Application: …

Do not commit these values. This script does not claim notarization until
notarytool + stapler actually succeed.
HINT
}

have_api_key() {
  [[ -n "${APP_STORE_CONNECT_API_KEY_PATH:-}" ]] && \
    [[ -n "${APP_STORE_CONNECT_ISSUER_ID:-}" ]] && \
    [[ -n "${APP_STORE_CONNECT_KEY_ID:-}" ]]
}

have_apple_id() {
  [[ -n "${APPLE_ID:-}" ]] && \
    [[ -n "${APPLE_APP_SPECIFIC_PASSWORD:-${APPLE_PASSWORD:-}}" ]] && \
    [[ -n "${APPLE_TEAM_ID:-}" ]]
}

detect_identity() {
  if [[ -n "${PORTSKILL_SIGN_IDENTITY:-}" ]]; then
    printf '%s' "${PORTSKILL_SIGN_IDENTITY}"
    return 0
  fi
  if ! command -v security >/dev/null 2>&1; then
    echo "security(1) not found; set PORTSKILL_SIGN_IDENTITY." >&2
    return 1
  fi
  local found
  found="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -F'"' '/Developer ID Application/ { print $2; exit }')"
  if [[ -z "${found}" ]]; then
    echo "No Developer ID Application identity found. Set PORTSKILL_SIGN_IDENTITY." >&2
    return 1
  fi
  printf '%s' "${found}"
}

bundle_id() {
  if [[ -n "${PORTSKILL_BUNDLE_ID:-}" ]]; then
    printf '%s' "${PORTSKILL_BUNDLE_ID}"
    return 0
  fi
  local plist="${DIST_APP}/Contents/Info.plist"
  if [[ -f "${plist}" ]] && command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${plist}" 2>/dev/null || true
  fi
}

write_entitlements() {
  # Hardened runtime + system Python / bash launcher needs these for a working staple.
  local path="$1"
  cat > "${path}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <key>com.apple.security.cs.disable-library-validation</key>
  <true/>
</dict>
</plist>
PLIST
}

submit_notary() {
  local payload="$1"
  if have_api_key; then
    if [[ ! -f "${APP_STORE_CONNECT_API_KEY_PATH}" ]]; then
      echo "APP_STORE_CONNECT_API_KEY_PATH is not a file: ${APP_STORE_CONNECT_API_KEY_PATH}" >&2
      creds_hint
      exit 1
    fi
    xcrun notarytool submit "${payload}" --wait \
      --key "${APP_STORE_CONNECT_API_KEY_PATH}" \
      --key-id "${APP_STORE_CONNECT_KEY_ID}" \
      --issuer "${APP_STORE_CONNECT_ISSUER_ID}"
    return 0
  fi
  if have_apple_id; then
    xcrun notarytool submit "${payload}" --wait \
      --apple-id "${APPLE_ID}" \
      --password "${APPLE_APP_SPECIFIC_PASSWORD:-${APPLE_PASSWORD:-}}" \
      --team-id "${APPLE_TEAM_ID}"
    return 0
  fi
  creds_hint
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dmg) MAKE_DMG=1; shift ;;
    --skip-staple) SKIP_STAPLE=1; shift ;;
    -h|--help|help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

# Fail closed on missing creds before OS / toolchain checks so the env list
# is always the first error when credentials are absent.
if ! have_api_key && ! have_apple_id; then
  creds_hint
  exit 1
fi

need_darwin

if [[ ! -d "${DIST_APP}" ]]; then
  echo "missing ${DIST_APP} — run ./scripts/build-app.sh first." >&2
  exit 1
fi

if ! command -v codesign >/dev/null 2>&1 || ! command -v xcrun >/dev/null 2>&1; then
  echo "codesign and xcrun are required (Xcode command-line tools)." >&2
  exit 1
fi

IDENTITY="$(detect_identity)" || exit 1
echo "== Codesign =="
echo "App:      ${DIST_APP}"
echo "Identity: ${IDENTITY}"

ENTITLEMENTS="$(mktemp "${TMPDIR:-/tmp}/portskill-entitlements.XXXXXX.plist")"
trap 'rm -f "${ENTITLEMENTS}"' EXIT
write_entitlements "${ENTITLEMENTS}"

BID="$(bundle_id || true)"
# Deep + hardened runtime + timestamp is what notarytool expects.
if [[ -n "${BID}" ]]; then
  echo "Bundle ID: ${BID}"
  codesign --force --deep --timestamp --options runtime \
    --entitlements "${ENTITLEMENTS}" \
    --identifier "${BID}" \
    --sign "${IDENTITY}" \
    "${DIST_APP}"
else
  codesign --force --deep --timestamp --options runtime \
    --entitlements "${ENTITLEMENTS}" \
    --sign "${IDENTITY}" \
    "${DIST_APP}"
fi

codesign --verify --deep --strict --verbose=2 "${DIST_APP}"
echo "codesign verify ok"

mkdir -p "${DIST_DIR}"
SUBMIT_PATH=""
if [[ "${MAKE_DMG}" -eq 1 ]]; then
  if ! command -v hdiutil >/dev/null 2>&1; then
    echo "hdiutil not found; cannot build dmg." >&2
    exit 1
  fi
  SUBMIT_PATH="${DIST_DIR}/Portskill.dmg"
  rm -f "${SUBMIT_PATH}"
  STAGE="$(mktemp -d "${TMPDIR:-/tmp}/portskill-dmg.XXXXXX")"
  cp -R "${DIST_APP}" "${STAGE}/Portskill.app"
  hdiutil create -volname Portskill -srcfolder "${STAGE}" -ov -format UDZO "${SUBMIT_PATH}" >/dev/null
  rm -rf "${STAGE}"
  echo "Created ${SUBMIT_PATH}"
else
  SUBMIT_PATH="${DIST_DIR}/Portskill-notarize.zip"
  rm -f "${SUBMIT_PATH}"
  # ditto zip is the Apple-recommended notary payload (preserves resource forks).
  ditto -c -k --keepParent "${DIST_APP}" "${SUBMIT_PATH}"
  echo "Created ${SUBMIT_PATH}"
fi

echo
echo "== notarytool submit --wait =="
submit_notary "${SUBMIT_PATH}"

if [[ "${SKIP_STAPLE}" -eq 1 ]]; then
  echo "Skipped staple (--skip-staple). Ticket is with Apple; app is not stapled locally."
  exit 0
fi

echo
echo "== stapler staple =="
xcrun stapler staple "${DIST_APP}"
xcrun stapler validate "${DIST_APP}"
echo
echo "Notarization succeeded: ticket stapled on ${DIST_APP}"
echo "Submit artifact: ${SUBMIT_PATH}"
