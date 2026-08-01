#!/bin/bash
# Signing and notarising the macOS app.
#
# Unsigned the app works, but on download the system will say "the developer
# cannot be verified". Three levels:
#   1. ad-hoc (the default when building) — runs only where it was built;
#   2. Apple Development — runs on your team's devices;
#   3. Developer ID Application plus notarisation — runs for anybody.
#
# Usage:
#   packaging/sign_macos.sh "Developer ID Application: Name (TEAMID)"
#   NOTARY_PROFILE=aijobsearch packaging/sign_macos.sh "Developer ID Application: ..."
set -euo pipefail

APP="dist/AI Job Search.app"
DMG="dist/AI Job Search.dmg"
IDENTITY="${1:-}"

# An app may only be handed out under a Developer ID Application certificate.
# Apple Development signs for your own devices: on somebody else's machine
# Gatekeeper will still say "the developer cannot be verified".
if [ -z "$IDENTITY" ]; then
  IDENTITY="$(security find-identity -v -p codesigning |
              sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
fi

if [ -z "$IDENTITY" ]; then
  echo "Не найден сертификат Developer ID Application."
  echo
  echo "Что есть в системе:"
  security find-identity -v -p codesigning | sed 's/^/  /'
  echo
  echo "Создать нужный: developer.apple.com → Certificates → + →"
  echo "Developer ID Application (или Xcode → Settings → Accounts →"
  echo "Manage Certificates → + → Developer ID Application), скачать и"
  echo "открыть файл — он встанет в связку ключей."
  echo
  echo "Затем: $0                       (сертификат подхватится сам)"
  echo "   или: $0 \"<название сертификата>\""
  exit 1
fi

echo "→ Подписываем как: $IDENTITY"

[ -d "$APP" ] || { echo "Нет $APP — сначала соберите: pyinstaller --clean --noconfirm packaging/aijobsearch.spec"; exit 1; }

echo "→ Подписываем вложенные бинарники"
find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" \) -print0 |
  xargs -0 -I{} codesign --force --timestamp --options runtime --sign "$IDENTITY" {} 2>/dev/null || true

echo "→ Подписываем приложение"
codesign --force --deep --timestamp --options runtime \
  --entitlements packaging/entitlements.plist \
  --sign "$IDENTITY" "$APP"

echo "→ Проверяем подпись"
codesign --verify --deep --strict --verbose=2 "$APP"

# Notarisation is needed only for handing the app to other people: Apple checks
# the build and staples a "stamp" to it, after which Gatekeeper lets it through
# without a word.
#
# The order matters. The app has to be stapled BEFORE the disk image is built, or
# the image will contain an unstapled app: while a person has internet Gatekeeper
# will ask Apple and let it through, but offline it will refuse. Hence two
# submissions: first the app (as an archive — notarytool will not take a
# directory), then the finished image.
#
# Access to notarisation is given in two ways. On your own machine a keychain
# profile (NOTARY_PROFILE) is more convenient. A build machine has no keychain and
# there is no point making one for a single run — there the details arrive as
# environment variables.
NOTARY_ARGS=()
if [ -n "${NOTARY_PROFILE:-}" ]; then
  NOTARY_ARGS=(--keychain-profile "$NOTARY_PROFILE")
elif [ -n "${NOTARY_APPLE_ID:-}" ] && [ -n "${NOTARY_TEAM_ID:-}" ] && [ -n "${NOTARY_PASSWORD:-}" ]; then
  NOTARY_ARGS=(--apple-id "$NOTARY_APPLE_ID" --team-id "$NOTARY_TEAM_ID" --password "$NOTARY_PASSWORD")
fi

if [ ${#NOTARY_ARGS[@]} -gt 0 ]; then
  echo "→ Отправляем приложение на нотаризацию (несколько минут)"
  ZIP="$(mktemp -d)/app.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" "${NOTARY_ARGS[@]}" --wait
  xcrun stapler staple "$APP"
  rm -rf "$(dirname "$ZIP")"
fi

echo "→ Собираем образ диска"
rm -f "$DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "AI Job Search" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"

if [ ${#NOTARY_ARGS[@]} -gt 0 ]; then
  echo "→ Отправляем образ на нотаризацию"
  xcrun notarytool submit "$DMG" "${NOTARY_ARGS[@]}" --wait
  xcrun stapler staple "$DMG"
  echo "→ Нотаризовано: штамп и на приложении, и на образе"
else
  echo "→ Нотаризацию пропустили: нет ни NOTARY_PROFILE, ни NOTARY_APPLE_ID/TEAM_ID/PASSWORD."
  echo "  Профиль на своей машине создаётся один раз:"
  echo "  xcrun notarytool store-credentials aijobsearch"
fi

echo "Готово: $DMG"
