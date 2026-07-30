#!/bin/bash
# Подпись и нотаризация приложения для macOS.
#
# Без подписи приложение работает, но при скачивании система скажет «разработчик
# не может быть проверен». Три уровня:
#   1. ad-hoc (по умолчанию при сборке) — запускается только там, где собрано;
#   2. Apple Development — запускается на устройствах вашей команды;
#   3. Developer ID Application + нотаризация — запускается у кого угодно.
#
# Использование:
#   packaging/sign_macos.sh "Developer ID Application: Имя (TEAMID)"
#   NOTARY_PROFILE=aijobsearch packaging/sign_macos.sh "Developer ID Application: ..."
set -euo pipefail

APP="dist/AI Job Search.app"
DMG="dist/AI Job Search.dmg"
IDENTITY="${1:-}"

if [ -z "$IDENTITY" ]; then
  echo "Доступные сертификаты:"
  security find-identity -v -p codesigning
  echo
  echo "Запустите: $0 \"<название сертификата>\""
  exit 1
fi

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

echo "→ Собираем образ диска"
rm -f "$DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "AI Job Search" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"

# Нотаризация нужна только для раздачи другим людям: Apple проверяет сборку и
# ставит «штамп», после которого Gatekeeper пускает её молча.
if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "→ Отправляем на нотаризацию (это занимает несколько минут)"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP"
  echo "→ Нотаризовано"
else
  echo "→ Нотаризацию пропустили (не задан NOTARY_PROFILE)."
  echo "  Профиль создаётся один раз:"
  echo "  xcrun notarytool store-credentials aijobsearch --apple-id <email> --team-id <TEAMID> --password <app-specific-password>"
fi

echo "Готово: $DMG"
