; Compile after building build/installer-stage/SAI.
#ifndef SourceDir
  #define SourceDir "..\build\installer-stage\SAI"
#endif
#ifndef OutputPath
  #define OutputPath "..\dist\installer"
#endif
#define AppVersion "1.0.5"

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
OutputBaseFilename=sai-desktop-win-x64
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\SAI.exe
LicenseFile=..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
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

[InstallDelete]
; Older releases shipped these unused packages; reclaim the space on upgrade.
Type: filesandordirs; Name: "{app}\internal\scipy"
Type: filesandordirs; Name: "{app}\internal\scipy.libs"
Type: filesandordirs; Name: "{app}\internal\Cython"
Type: filesandordirs; Name: "{app}\internal\lxml"
Type: filesandordirs; Name: "{app}\internal\soynlp"
Type: filesandordirs; Name: "{app}\internal\pyximport"
Type: filesandordirs; Name: "{app}\internal\pydoc_data"

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

[UninstallDelete]
; The pointer only tells the app where user data lives; the data itself stays.
Type: files; Name: "{app}\data-dir.txt"

[Code]
var
  DataDirPage: TInputDirWizardPage;

function ReadDataDir(): String;
var
  raw: AnsiString;
  dir: String;
begin
  Result := '';
  if LoadStringFromFile(ExpandConstant('{app}\data-dir.txt'), raw) then
  begin
    dir := Trim(Utf8Decode(raw));
    if (Length(dir) > 0) and (dir[1] = #$FEFF) then
      Delete(dir, 1, 1);
    Result := dir;
  end;
end;

function DefaultDataDir(): String;
var
  dir: String;
begin
  dir := ReadDataDir();
  if dir <> '' then
    Result := dir
  else
    Result := ExpandConstant('{localappdata}\SAI');
end;

procedure InitializeWizard;
begin
  DataDirPage := CreateInputDirPage(wpSelectDir, '数据存放目录',
    '选择配置、热词、日志、录音与日记的存放位置',
    '这些用户数据不随程序一起卸载。默认放在当前用户目录；想放到其它磁盘，请在这里选择。',
    False, '');
  DataDirPage.Add('');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  // {app} is only usable after the directory page, so fill the default here.
  if (DataDirPage <> nil) and (CurPageID = DataDirPage.ID)
     and (Trim(DataDirPage.Values[0]) = '') then
    DataDirPage.Values[0] := DefaultDataDir();
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  selected: String;
  lines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then
    exit;
  selected := '';
  if Assigned(DataDirPage) then
    selected := Trim(DataDirPage.Values[0]);
  if selected = '' then
    selected := DefaultDataDir();
  if selected = '' then
    selected := ExpandConstant('{localappdata}\SAI');
  SetArrayLength(lines, 1);
  lines[0] := selected;
  SaveStringsToUTF8File(ExpandConstant('{app}\data-dir.txt'), lines, False);
end;
