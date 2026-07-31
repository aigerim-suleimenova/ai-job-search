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

# Раздавать приложение можно только сертификатом Developer ID Application.
# Apple Development подписывает для своих устройств: у другого человека
# Gatekeeper всё равно скажет «разработчик не может быть проверен».
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

# Нотаризация нужна только для раздачи другим людям: Apple проверяет сборку и
# ставит «штамп», после которого Gatekeeper пускает её молча.
#
# Порядок важен. Штамп приложения нужно поставить ДО сборки образа, иначе внутри
# образа окажется приложение без штампа: пока у человека есть интернет, Gatekeeper
# спросит Apple и пустит, а без сети — откажет. Поэтому две отправки: сначала
# приложение (архивом — notarytool не принимает каталог), потом готовый образ.
if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "→ Отправляем приложение на нотаризацию (несколько минут)"
  ZIP="$(mktemp -d)/app.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
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

if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "→ Отправляем образ на нотаризацию"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  echo "→ Нотаризовано: штамп и на приложении, и на образе"
else
  echo "→ Нотаризацию пропустили (не задан NOTARY_PROFILE)."
  echo "  Профиль создаётся один раз:"
  echo "  xcrun notarytool store-credentials aijobsearch --apple-id <email> --team-id <TEAMID> --password <app-specific-password>"
fi

echo "Готово: $DMG"
