; Installer of Default Island (Inno Setup 6).
; Built by build.py, which passes the version: ISCC /DAppVersion=x.y.z setup.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Default Island"
#define AppExe "DefaultIsland.exe"
#define AppUrl "https://github.com/lourchik69/default-island"

[Setup]
AppId={{8F1C2B7E-4D6A-4C1E-9B53-2E7A1D0F6C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=lourchik69
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
; Installed for the current user only: no administrator rights needed.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
; The folder page shows on updates too, so the place can be changed.
DisableDirPage=no
WizardImageFile=build\art\wizard-large.png
WizardSmallImageFile=build\art\wizard-small.png
WizardImageFileDynamicDark=build\art\wizard-large.png
WizardSmallImageFileDynamicDark=build\art\wizard-small.png
UsePreviousAppDir=yes
UsePreviousTasks=yes
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=DefaultIsland-Setup
SetupIconFile=Dynamicisland.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; Light or dark, as Windows is set.
WizardStyle=modern dynamic
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The installer stops the island itself (PrepareToInstall).
CloseApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.GroupOptions=Настройки:
uk.GroupOptions=Налаштування:
en.GroupOptions=Settings:
ru.TaskAutostart=Запускать вместе с Windows
uk.TaskAutostart=Запускати разом з Windows
en.TaskAutostart=Start with Windows
ru.TaskAutoUpdate=Обновлять программу автоматически (новые версии ставятся сами)
uk.TaskAutoUpdate=Оновлювати програму автоматично (нові версії встановлюються самі)
en.TaskAutoUpdate=Update automatically (new versions install by themselves)
ru.ExtensionShortcut=Расширение для браузера (папка)
uk.ExtensionShortcut=Розширення для браузера (тека)
en.ExtensionShortcut=Browser extension (folder)
ru.ExtensionNote=Чтобы островок видел музыку на сайтах (SoundCloud, YouTube Music, Яндекс Музыка и др.), установите расширение для браузера: откройте brave://extensions или chrome://extensions, включите «Режим разработчика», нажмите «Загрузить распакованное» и выберите папку:
uk.ExtensionNote=Щоб острів бачив музику на сайтах (SoundCloud, YouTube Music, Яндекс Музика та ін.), встановіть розширення для браузера: відкрийте brave://extensions або chrome://extensions, увімкніть «Режим розробника», натисніть «Завантажити розпаковане» й виберіть теку:
en.ExtensionNote=For the island to see music on websites (SoundCloud, YouTube Music and others), install the browser extension: open brave://extensions or chrome://extensions, turn on Developer mode, click "Load unpacked" and choose the folder:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "{cm:TaskAutostart}"; GroupDescription: "{cm:GroupOptions}"
Name: "autoupdate"; Description: "{cm:TaskAutoUpdate}"; GroupDescription: "{cm:GroupOptions}"

[InstallDelete]
; Versions before 2.1 put the program next to its settings, without an uninstaller.
Type: files; Name: "{localappdata}\DefaultIsland\{#AppExe}"
Type: files; Name: "{localappdata}\DefaultIsland\DefaultIsland.ico"
Type: files; Name: "{autoprograms}\{#AppName}\{#AppName}.lnk"
Type: dirifempty; Name: "{autoprograms}\{#AppName}"
Type: files; Name: "{autodesktop}\{#AppName}.lnk"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "Dynamicisland.ico"; DestDir: "{app}"; DestName: "DefaultIsland.ico"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "soundcloud_bridge\*"; DestDir: "{app}\browser-extension"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\DefaultIsland.ico"
Name: "{autoprograms}\{cm:ExtensionShortcut}"; Filename: "{app}\browser-extension"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\DefaultIsland.ico"; Tasks: desktopicon

[Registry]
; The same Run entry the island's own "Start with Windows" switch uses.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DefaultIsland"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DefaultIsland"; Flags: deletevalue; Tasks: not autostart
; Choices the island takes over into its settings on its next start.
Root: HKCU; Subkey: "Software\Default Island"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Default Island"; ValueType: dword; ValueName: "AutoUpdate"; ValueData: "1"; Tasks: autoupdate
Root: HKCU; Subkey: "Software\Default Island"; ValueType: dword; ValueName: "AutoUpdate"; ValueData: "0"; Tasks: not autoupdate
Root: HKCU; Subkey: "Software\Default Island"; ValueType: dword; ValueName: "AutoStart"; ValueData: "1"; Tasks: autostart
Root: HKCU; Subkey: "Software\Default Island"; ValueType: dword; ValueName: "AutoStart"; ValueData: "0"; Tasks: not autostart
Root: HKCU; Subkey: "Software\Default Island"; ValueType: dword; ValueName: "Pending"; ValueData: "1"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; An automatic update runs silently and starts the island again afterwards.
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: IsAutoUpdate

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopIsland"

[Code]
function IsAutoUpdate: Boolean;
begin
  Result := Pos('/AUTOUPDATE', UpperCase(GetCmdTail)) > 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { The running island holds its exe open; close it so it can be replaced. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExe} /F', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Sleep(500);
  Result := '';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      CustomMessage('ExtensionNote') + #13#10 + ExpandConstant('{app}\browser-extension');
end;
