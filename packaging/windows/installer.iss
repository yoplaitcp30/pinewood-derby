#define AppName "Pinewood Derby Race Manager"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppExeName "Pinewood Derby Race Manager.exe"
#define AppSource "..\..\dist\Pinewood Derby Race Manager"

[Setup]
AppId={{BA70F8C7-369D-47CE-901E-581CB9E39DA2}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Pinewood Derby Race Manager
DefaultDirName={localappdata}\Programs\Pinewood Derby Race Manager
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\..\dist\installer
OutputBaseFilename=PinewoodDerby-Setup
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: postinstall nowait skipifsilent
