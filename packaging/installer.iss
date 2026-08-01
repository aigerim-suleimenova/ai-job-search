; The Windows installer. The build used to be handed out as a zip archive: a person
; had to unpack the folder and find the right .exe in it next to the _internal
; directory — and half of them simply deleted the archive. Now it is one file: run
; it, press "Install", and the program appears in the Start menu and in the list of
; installed programs.
;
; We install into the user profile rather than Program Files: that way no
; administrator password is needed, and it is the only thing the program wants from
; the system.

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
; Into the user profile only. {autopf} with a choice dialog gave people the option
; of installing "for everyone" — and then the program went into Program Files, the
; installer asked for an administrator, and immediately after installing could not
; start the file it had just written there: "CreateProcess failed; code 5".
; The program does not need administrator rights at all, so there is nothing to ask for.
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
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
; runasoriginaluser: if the installer did end up running elevated, the program has
; to be opened as the person rather than as the administrator — otherwise its data
; would land in somebody else's profile.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

; A person's data — the CV, the settings, the jobs found — lives in %APPDATA% and
; is left alone when the program is removed: reinstalling must not wipe the history.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
