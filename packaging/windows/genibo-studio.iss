; Inno Setup script for Genibo Studio. Invoked by packaging/build.py with:
;   /DAppVersion=... /DAppNumericVersion=... /DSourceDir=... /DOutputDir=... /DOutputBaseFilename=... /DIconFile=...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.0.0"
#endif

[Setup]
AppId={{6C1C5E61-4B8B-4C47-9C7B-1F0B7A3E2D51}
AppName=Genibo Studio
AppVersion={#AppVersion}
AppVerName=Genibo Studio {#AppVersion}
AppPublisher=Genibo SDK
VersionInfoVersion={#AppNumericVersion}
DefaultDirName={autopf}\Genibo Studio
DefaultGroupName=Genibo Studio
DisableProgramGroupPage=yes
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\genibo-studio.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Genibo Studio"; Filename: "{app}\genibo-studio.exe"
Name: "{autodesktop}\Genibo Studio"; Filename: "{app}\genibo-studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\genibo-studio.exe"; Description: "{cm:LaunchProgram,Genibo Studio}"; Flags: nowait postinstall skipifsilent
