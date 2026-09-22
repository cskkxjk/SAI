; Compile after building build/installer-stage/SAI.
#ifndef SourceDir
  #define SourceDir "..\build\installer-stage\SAI"
#endif
#ifndef OutputPath
  #define OutputPath "..\dist\installer"
#endif
#define AppVersion "2.7.0"

[Setup]
AppId={{23100B11-53C4-4AFA-93E6-8C054B16B942}
AppName=SAI
AppVersion={#AppVersion}
AppPublisher=cskkxjk
AppPublisherURL=https://github.com/cskkxjk/SAI
DefaultDirName={localappdata}\Programs\SAI
DefaultGroupName=SAI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
DisableProgramGroupPage=no
WizardStyle=modern
OutputDir={#OutputPath}
OutputBaseFilename=SAI-{#AppVersion}-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\SAI.exe
LicenseFile=..\LICENSE
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=no
CloseApplications=yes
RestartApplications=no
AppMutex=Local\SAIDesktop
SetupLogging=yes
Uninstallable=yes
ChangesAssociations=no

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "assets\*,models\*,logs\*,20*\*,__pycache__\*,config_gui.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "{#SourceDir}\assets\icon-recording.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "installed.flag"; DestDir: "{app}"; Flags: ignoreversion
Source: "config_gui.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\SAI"; Filename: "{app}\SAI.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall SAI"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SAI"; Filename: "{app}\SAI.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\SAI.exe"; Description: "启动 SAI"; Flags: nowait postinstall skipifsilent

; User data lives outside {app}; no UninstallDelete entries for it.
