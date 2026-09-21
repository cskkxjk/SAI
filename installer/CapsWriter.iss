; Compile after building build/installer-stage/CapsWriter-Offline.
#ifndef SourceDir
  #define SourceDir "..\build\installer-stage\CapsWriter-Offline"
#endif
#ifndef OutputPath
  #define OutputPath "..\dist\installer"
#endif
#define AppVersion "2.7.0"

[Setup]
AppId={{23100B11-53C4-4AFA-93E6-8C054B16B942}
AppName=CapsWriter Offline
AppVersion={#AppVersion}
AppPublisher=cskkxjk
AppPublisherURL=https://github.com/cskkxjk/CapsWriter-Offline
DefaultDirName={localappdata}\Programs\CapsWriter Offline
DefaultGroupName=CapsWriter Offline
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
DisableProgramGroupPage=no
WizardStyle=modern
OutputDir={#OutputPath}
OutputBaseFilename=CapsWriter-Offline-{#AppVersion}-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\CapsWriter.exe
LicenseFile=..\LICENSE
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=no
CloseApplications=yes
RestartApplications=no
AppMutex=Local\CapsWriterOfflineDesktop
SetupLogging=yes
Uninstallable=yes
ChangesAssociations=no

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "models\*,logs\*,20*\*,__pycache__\*,config_gui.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installed.flag"; DestDir: "{app}"; Flags: ignoreversion
Source: "config_gui.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\CapsWriter Offline"; Filename: "{app}\CapsWriter.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall CapsWriter Offline"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CapsWriter Offline"; Filename: "{app}\CapsWriter.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\CapsWriter.exe"; Description: "启动 CapsWriter Offline"; Flags: nowait postinstall skipifsilent

; User data lives outside {app}; no UninstallDelete entries for it.
