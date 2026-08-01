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
; Программу нельзя обновить и нельзя удалить, пока она открыта: Windows не даёт
; переписать работающий файл. А закрыть её люди забывают — у неё есть фоновый
; режим, и окно может быть закрыто, когда сама она ещё работает. Отсюда и
; загадочное «часть элементов не удалось удалить» после удаления.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no

; Язык установщика выбирается из тех, на которых говорит сама программа. Их
; четырнадцать, а здесь было четыре: человек ставил программу по-английски и
; только потом обнаруживал, что она знает его язык.
;
; Трёх недостаёт — арабского, хинди и китайского: официальный набор переводов
; Inno Setup их не содержит, а брать чужие неофициальные файлы в сборку значит
; тащить в установщик текст, за который мы не отвечаем. Сама программа на этих
; языках говорит по-прежнему.
[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "pt"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"

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
;
; Без skipifsilent, но с проверкой: тихая установка бывает двух разных родов.
; Когда программа обновляет себя сама, она закрывается и должна открыться снова —
; иначе человек нажал «Обновить» и остался без программы. А тихая установка,
; запущенная кем-то из скрипта, открывать окно не должна. Различаем по флагу
; /RELAUNCH, который передаёт только наше собственное обновление.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall runasoriginaluser; Check: ShouldLaunchAfter

; A person's data — the CV, the settings, the jobs found — lives in %APPDATA% and
; is left alone unless the person asks otherwise on the way out (see [Code]):
; reinstalling must not wipe the history by itself.
;
; Здесь же — то, что заводит не установщик, а сама программа во время работы, и
; о чём удаление поэтому не знает: кэш встроенного браузера. Он и оставался, а
; человек читал «часть элементов не удалось удалить» и не понимал, каких.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\{#AppExe}.WebView2"
Type: filesandordirs; Name: "{app}\EBWebView"
Type: dirifempty; Name: "{app}"

[CustomMessages]
; Без префикса языка — значение по умолчанию для всех, у кого своего перевода нет.
DataPrompt=Your data — profiles, CVs, the jobs found and the settings — is kept separately and is not removed with the program. Reinstalling picks it up again.
DataCheckbox=Delete my data as well. This cannot be undone.
Leftovers=Some things were left in the program folder and can be removed by hand:
ru.DataPrompt=Ваши данные — профили, CV, найденные вакансии и настройки — хранятся отдельно и вместе с программой не удаляются. При следующей установке они найдутся снова.
ru.DataCheckbox=Удалить и мои данные. Отменить будет нельзя.
ru.Leftovers=В папке программы кое-что осталось, это можно удалить вручную:
de.DataPrompt=Ihre Daten — Profile, Lebensläufe, gefundene Stellen und Einstellungen — liegen getrennt und werden mit dem Programm nicht entfernt. Eine erneute Installation findet sie wieder.
de.DataCheckbox=Meine Daten ebenfalls löschen. Das lässt sich nicht rückgängig machen.
de.Leftovers=Im Programmordner ist etwas zurückgeblieben, das von Hand entfernt werden kann:
it.DataPrompt=I suoi dati — profili, CV, offerte trovate e impostazioni — sono conservati a parte e non vengono rimossi con il programma. Una nuova installazione li ritrova.
it.DataCheckbox=Eliminare anche i miei dati. Non sarà possibile annullare.
it.Leftovers=Nella cartella del programma è rimasto qualcosa, che può essere rimosso a mano:

[Code]
var
  RemoveData: Boolean;

{ Открывать ли программу после установки. Обычная установка — да, как и раньше.
  Тихая — только если это мы сами себя обновляем и передали /RELAUNCH. }
function ShouldLaunchAfter(): Boolean;
begin
  { Скобок у встроенных функций без параметров быть не должно: Pascal Script
    считает это вызовом с неверным числом аргументов и не собирается. }
  Result := (Pos('/RELAUNCH', Uppercase(GetCmdTail)) > 0) or (not WizardSilent);
end;

{ Спрашиваем один раз, до удаления: галочка и есть ответ. }
function AskAboutData(): Boolean;
var
  Form: TSetupForm;
  Text: TNewStaticText;
  Box: TNewCheckBox;
  Ok, Cancel: TNewButton;
begin
  Result := False;
  RemoveData := False;
  Form := CreateCustomForm();
  try
    Form.Caption := '{#AppName}';
    Form.ClientWidth := ScaleX(440);
    Form.ClientHeight := ScaleY(190);
    Form.Position := poScreenCenter;

    Text := TNewStaticText.Create(Form);
    Text.Parent := Form;
    Text.Left := ScaleX(16);
    Text.Top := ScaleY(16);
    Text.Width := Form.ClientWidth - ScaleX(32);
    Text.WordWrap := True;
    Text.AutoSize := True;
    Text.Caption := CustomMessage('DataPrompt');

    Box := TNewCheckBox.Create(Form);
    Box.Parent := Form;
    Box.Left := ScaleX(16);
    Box.Top := Text.Top + Text.Height + ScaleY(16);
    Box.Width := Form.ClientWidth - ScaleX(32);
    Box.Height := ScaleY(36);
    Box.WordWrap := True;
    Box.Checked := False;
    Box.Caption := CustomMessage('DataCheckbox');

    Ok := TNewButton.Create(Form);
    Ok.Parent := Form;
    Ok.Width := ScaleX(96);
    Ok.Height := ScaleY(28);
    Ok.Top := Form.ClientHeight - ScaleY(42);
    Ok.Left := Form.ClientWidth - ScaleX(218);
    Ok.Caption := SetupMessage(msgButtonOK);
    Ok.ModalResult := mrOk;
    Ok.Default := True;

    Cancel := TNewButton.Create(Form);
    Cancel.Parent := Form;
    Cancel.Width := ScaleX(96);
    Cancel.Height := ScaleY(28);
    Cancel.Top := Ok.Top;
    Cancel.Left := Form.ClientWidth - ScaleX(112);
    Cancel.Caption := SetupMessage(msgButtonCancel);
    Cancel.ModalResult := mrCancel;
    Cancel.Cancel := True;

    if Form.ShowModal() = mrOk then
    begin
      RemoveData := Box.Checked;
      Result := True;
    end;
  finally
    Form.Free();
  end;
end;

function InitializeUninstall(): Boolean;
begin
  { В тихом режиме спрашивать некого: данные тогда остаются, как и раньше. }
  if UninstallSilent then
    Result := True
  else
    Result := AskAboutData();
end;

{ Что осталось в папке программы — по именам, а не «часть элементов». }
procedure ReportLeftovers();
var
  Found: TFindRec;
  List: String;
  Count: Integer;
begin
  List := '';
  Count := 0;
  if FindFirst(ExpandConstant('{app}\*'), Found) then
  try
    repeat
      if (Found.Name <> '.') and (Found.Name <> '..') then
      begin
        if Count < 12 then
          List := List + '   ' + Found.Name + #13#10;
        Count := Count + 1;
      end;
    until not FindNext(Found);
  finally
    FindClose(Found);
  end;
  if (Count > 0) and not UninstallSilent then
    MsgBox(CustomMessage('Leftovers') + #13#10 + #13#10
           + ExpandConstant('{app}') + #13#10 + #13#10 + List,
           mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurStep: TUninstallStep);
begin
  if CurStep = usPostUninstall then
  begin
    { Автозапуск заводит сама программа, установщик о нём не знает — и после
      удаления Windows при каждом входе пыталась бы запустить то, чего нет. }
    RegDeleteValue(HKEY_CURRENT_USER,
                   'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
    if RemoveData then
      DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
    ReportLeftovers();
  end;
end;
