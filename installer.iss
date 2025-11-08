; Inno Setup installer script for Launchpad Mapper
; Bygg med: iscc installer.iss

#define MyAppName "Launchpad Mapper"
#define MyAppVersion "0.1.5"  ; Synka med pyproject.toml version (uppdatera vid release)
#define MyAppPublisher "Carl Jagemalm"
#define MyAppExeName "LaunchpadMapper.exe"

[Setup]
AppId={{9F2C7EF1-7B3C-4E5C-9B2B-1234567890AB}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
LicenseFile=LICENSE
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=LaunchpadMapperSetup
Compression=lzma
SolidCompression=yes
WizardStyle=classic
; Require admin install so the app has sufficient rights and installs under Program Files
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Om du kör one-dir: peka mot dist/LaunchpadMapper/ filerna
Source: "dist/LaunchpadMapper/*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Starta appen direkt efter installation (valfritt av användaren)
Filename: "{app}\\{#MyAppExeName}"; Description: "Start {#MyAppName} nu"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Ta bort användarkonfiguration och presets under AppData vid avinstallering
Type: filesandordirs; Name: "{userappdata}\\LaunchpadMapper\\presets"
Type: filesandordirs; Name: "{userappdata}\\LaunchpadMapper"
