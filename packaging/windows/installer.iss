; Usage: ISCC.exe /DMyAppVersion=0.1.0 packaging\windows\installer.iss
; Run from the repository root (SourceDir below is relative to this file).
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C2E-3D4A-4E8B-9A57-2C0E5B1D7F34}
AppName=Voice2Text
AppVersion={#MyAppVersion}
AppPublisher=Martin Bednář
DefaultDirName={localappdata}\Programs\Voice2Text
DefaultGroupName=Voice2Text
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=Voice2Text-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Voice2Text.exe
ShowLanguageDialog=auto
UsePreviousLanguage=no
Uninstallable=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "..\..\dist\Voice2Text\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Voice2Text"; Filename: "{app}\Voice2Text.exe"
Name: "{autodesktop}\Voice2Text"; Filename: "{app}\Voice2Text.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Voice2Text.exe"; Description: "{cm:LaunchProgram,Voice2Text}"; Flags: nowait postinstall skipifsilent

; Models and logs live in %LOCALAPPDATA%\Voice2Text. There is deliberately no
; [UninstallDelete] entry for them: uninstalling keeps the ~3 GB model.
