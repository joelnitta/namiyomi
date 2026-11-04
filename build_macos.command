#!/bin/zsh
# Private staging, fail-closed signing/notarization; original AB1s are untouched.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_PYTHON="${SANGER_PYTHON:-python3}"
RELEASE_DIR="$APP_DIR/release"
APPLICATIONS_DIR="${SANGER_APP_DIR:-$HOME/Applications}"
APP_VERSION="$("$BUILD_PYTHON" -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["APP_VERSION"])' "$APP_DIR/app_info.py")"
APP_NAME="$("$BUILD_PYTHON" -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["APP_NAME"])' "$APP_DIR/app_info.py")"
APP_BUNDLE_ID="$("$BUILD_PYTHON" -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["APP_BUNDLE_ID"])' "$APP_DIR/app_info.py")"
APP_BUNDLE="$APP_NAME.app"
SIGNING_IDENTITY="${NAMIYOMI_CODESIGN_IDENTITY:-}"
NOTARY_PROFILE="${NAMIYOMI_NOTARY_PROFILE:-}"
NOTARIZE="${NAMIYOMI_NOTARIZE:-0}"
INSTALL="${NAMIYOMI_INSTALL:-1}"
if [[ "$NOTARIZE" == 1 && ( -z "$SIGNING_IDENTITY" || -z "$NOTARY_PROFILE" ) ]]; then
  printf '%s\n' 'Notarization requires NAMIYOMI_CODESIGN_IDENTITY and NAMIYOMI_NOTARY_PROFILE.'
  exit 1
fi
if [[ -n "$SIGNING_IDENTITY" ]]; then
  # Only an exact, unique, usable Developer ID Application identity is valid.
  # Resolve to its fingerprint so codesign cannot select an ambiguous name.
  SIGNING_IDENTITY="$(security find-identity -v -p codesigning | "$BUILD_PYTHON" "$APP_DIR/signing_release.py" resolve-identity "$SIGNING_IDENTITY")"
  export NAMIYOMI_CODESIGN_IDENTITY="$SIGNING_IDENTITY"
fi
# Do not replace a running app, even if the caller forgets to close it.
if [[ "$INSTALL" == 1 ]] && ps -ax -o command= | /usr/bin/grep -F -- "$APPLICATIONS_DIR/$APP_BUNDLE/Contents/MacOS/$APP_NAME" | /usr/bin/grep -v grep >/dev/null; then
  printf '%s\n' 'Save and close the installed app first, or use NAMIYOMI_INSTALL=0.'
  exit 1
fi
STAGE_DIR="$(mktemp -d /private/tmp/namiyomi-build.XXXXXX)"
cleanup() {
  # Only this invocation's generated intermediates; no app backup or user data.
  if [[ "$STAGE_DIR" == /private/tmp/namiyomi-build.* && -d "$STAGE_DIR" && ! -L "$STAGE_DIR" ]]; then
    /bin/rm -r -- "$STAGE_DIR"
  fi
}
trap cleanup EXIT
export PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR=1
clang -fno-modules -dynamiclib -framework AppKit "$APP_DIR/native_menus.m" -o "$APP_DIR/assets/libnamiyomi-menus.dylib"
"$BUILD_PYTHON" -m PyInstaller --noconfirm --distpath "$STAGE_DIR/dist" --workpath "$STAGE_DIR/work" "$APP_DIR/namiyomi.spec"
BUILT_APP="$STAGE_DIR/dist/$APP_BUNDLE"
"$BUILD_PYTHON" "$APP_DIR/collect_licenses.py" "$BUILT_APP"
# Embedded Mach-O files were signed by PyInstaller. Re-seal the outer bundle
# after adding text notices, without a blind deep re-sign.
if [[ -n "$SIGNING_IDENTITY" ]]; then
  EXTRA_SIGNING=()
  if [[ -n "${NAMIYOMI_ENTITLEMENTS:-}" ]]; then EXTRA_SIGNING=(--entitlements "$NAMIYOMI_ENTITLEMENTS"); fi
  codesign --force --sign "$SIGNING_IDENTITY" --timestamp --options runtime "${EXTRA_SIGNING[@]}" "$BUILT_APP"
  # A matching display name alone is not proof of an Apple-issued certificate.
  # Check Apple's Developer ID CA and Application certificate OIDs (TN3127).
  codesign --verify --strict --test-requirement '=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists' "$BUILT_APP"
else
  codesign --force --sign - "$BUILT_APP"
fi
codesign --verify --deep --strict "$BUILT_APP"
mkdir -p "$RELEASE_DIR"
LABEL=development
if [[ -n "$SIGNING_IDENTITY" ]]; then LABEL=signed-unnotarized; fi
if [[ "$NOTARIZE" == 1 ]]; then
  ditto -c -k --keepParent --noextattr --norsrc "$BUILT_APP" "$STAGE_DIR/notarize.zip"
  SUBMIT_STATUS=0
  xcrun notarytool submit "$STAGE_DIR/notarize.zip" --keychain-profile "$NOTARY_PROFILE" --wait --output-format json > "$RELEASE_DIR/notarization-result.json" || SUBMIT_STATUS=$?
  # Fetch the service log even when --wait returned Invalid/nonzero. A successful
  # upload or an Accepted result alone is not sufficient release evidence.
  SUBMISSION_ID="$("$BUILD_PYTHON" "$APP_DIR/signing_release.py" submission-id "$RELEASE_DIR/notarization-result.json")"
  NOTARY_LOG="$RELEASE_DIR/notarization-$SUBMISSION_ID-log.json"
  xcrun notarytool log "$SUBMISSION_ID" --keychain-profile "$NOTARY_PROFILE" "$STAGE_DIR/notarization-log.json"
  mv "$STAGE_DIR/notarization-log.json" "$NOTARY_LOG"
  "$BUILD_PYTHON" "$APP_DIR/signing_release.py" check-notarization "$RELEASE_DIR/notarization-result.json" "$NOTARY_LOG"
  if [[ "$SUBMIT_STATUS" != 0 ]]; then
    printf '%s\n' 'notarytool submit returned an error; result and service log are saved. Do not distribute.'
    exit 1
  fi
  xcrun stapler staple "$BUILT_APP"
  xcrun stapler validate "$BUILT_APP"
  codesign --verify --deep --strict "$BUILT_APP"
  spctl --assess --type execute --verbose=2 "$BUILT_APP"
  LABEL=notarized
fi
ARCHIVE="$RELEASE_DIR/$APP_NAME-$APP_VERSION-macOS-arm64-$LABEL.zip"
PACKAGE_DIR="$STAGE_DIR/package/$APP_NAME"
mkdir -p "$PACKAGE_DIR/demo"
ditto --noextattr --norsrc "$BUILT_APP" "$PACKAGE_DIR/$APP_BUNDLE"
for notice in LICENSE.txt CITATION.cff CITATION.txt; do cp "$APP_DIR/$notice" "$PACKAGE_DIR/$notice"; done
cp "$APP_DIR/docs/START_HERE-macOS.txt" "$PACKAGE_DIR/START_HERE.txt"
for sample in Demo01_F.ab1 Demo01_R.ab1 Demo02_F.ab1 Demo02_R.ab1 Demo.json README.md; do
  cp "$APP_DIR/demo/$sample" "$PACKAGE_DIR/demo/$sample"
done
ditto -c -k --keepParent --noextattr --norsrc "$PACKAGE_DIR" "$STAGE_DIR/final.zip"
if [[ "$NOTARIZE" == 1 ]]; then
  # Assess what recipients will actually extract, not only the pre-ZIP bundle.
  ditto -x -k "$STAGE_DIR/final.zip" "$STAGE_DIR/archive-check"
  codesign --verify --deep --strict "$STAGE_DIR/archive-check/$APP_NAME/$APP_BUNDLE"
  xcrun stapler validate "$STAGE_DIR/archive-check/$APP_NAME/$APP_BUNDLE"
  spctl --assess --type execute --verbose=2 "$STAGE_DIR/archive-check/$APP_NAME/$APP_BUNDLE"
fi
if [[ -e "$ARCHIVE" ]]; then /usr/bin/trash "$ARCHIVE"; fi
mv "$STAGE_DIR/final.zip" "$ARCHIVE"
if [[ "$INSTALL" == 1 ]]; then
  mkdir -p "$APPLICATIONS_DIR"
  if [[ -e "$APPLICATIONS_DIR/$APP_BUNDLE" ]]; then
    if [[ "$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$APPLICATIONS_DIR/$APP_BUNDLE/Contents/Info.plist")" != "$APP_BUNDLE_ID" ]]; then
      printf '%s\n' 'Refusing to replace an unrelated application.';exit 1
    fi
    /usr/bin/trash "$APPLICATIONS_DIR/$APP_BUNDLE"
  fi
  ditto --noextattr --norsrc "$BUILT_APP" "$APPLICATIONS_DIR/$APP_BUNDLE"
  # Cloud-synced Desktop may attach Finder display metadata to the newly made
  # root directory. Remove only that non-security attribute, never quarantine,
  # provenance or other security attributes. See Apple QA1940.
  if xattr "$APPLICATIONS_DIR/$APP_BUNDLE" | /usr/bin/grep -Fx com.apple.FinderInfo >/dev/null; then
    xattr -d com.apple.FinderInfo "$APPLICATIONS_DIR/$APP_BUNDLE"
  fi
  codesign --verify --deep --strict "$APPLICATIONS_DIR/$APP_BUNDLE"
  printf '%s\n' "Application: $APPLICATIONS_DIR/$APP_BUNDLE"
fi
printf '%s\n' "Archive: $ARCHIVE" "Distribution status: $LABEL" 'Temporary build intermediates are removed automatically.'
