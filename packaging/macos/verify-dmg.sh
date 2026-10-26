#!/bin/bash
# Mount a built DMG and prove the app inside works: signature, architecture, headless self-test.
#   bash packaging/macos/verify-dmg.sh dist/release/Genibo-Studio-0.1.0-macos-arm64.dmg
set -euo pipefail
dmg="$1"
mnt="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -mountpoint "$mnt" "$dmg" >/dev/null
trap 'hdiutil detach "$mnt" -quiet || hdiutil detach "$mnt" -force -quiet || true' EXIT
app="$mnt/Genibo Studio.app"
test -L "$mnt/Applications"
codesign --verify --deep --strict "$app"
info="$app/Contents/Info.plist"
icns="$(plutil -extract CFBundleIconFile raw "$info")"
test -s "$app/Contents/Resources/${icns%.icns}.icns"              # macOS 14/15 icon
if [ "${ALLOW_LEGACY_MAC_ICON:-0}" != "1" ]; then
  test -s "$app/Contents/Resources/Assets.car"                    # macOS 26+ Liquid Glass icon
  echo "icon: CFBundleIconFile=$icns CFBundleIconName=$(plutil -extract CFBundleIconName raw "$info")"
fi
echo "architectures: $(lipo -archs "$app/Contents/MacOS/genibo-studio")"
plutil -extract CFBundleShortVersionString raw "$app/Contents/Info.plist"
QT_QPA_PLATFORM=offscreen "$app/Contents/MacOS/genibo-studio" --self-test
echo "DMG OK: $dmg"
