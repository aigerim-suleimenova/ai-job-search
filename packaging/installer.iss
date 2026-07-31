; Установщик для Windows. Раньше сборка раздавалась zip-архивом: человек должен
; был распаковать папку и найти в ней нужный .exe рядом с каталогом _internal —
; а половина просто удаляла архив. Теперь один файл: запустил, нажал «Установить»,
; программа появилась в меню «Пуск» и в списке установленных.
;
; Ставим в профиль пользователя, а не в Program Files: так не нужен пароль
; администратора, и это единственное, чего программа от системы хочет.

#define AppName "AI Job Search"
#define AppPublisher "Viktor Lavrov"
#define AppURL "https://mrwd.github.io/products/ai-job-search/"
#define AppExe "AI Job Search.exe"
#ifndef AppVersion
  #define AppVersion "0.8.0"
#endif

[Setup]
AppId={{6F2A1E7C-3D4B-4A9E-9C21-0B7E5D8F1A34}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=AI Job Search Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; Данные человека — резюме, настройки, найденные вакансии — лежат в %APPDATA% и
; при удалении программы не трогаются: переустановка не должна стирать историю.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
