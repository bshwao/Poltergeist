#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef BuildId
  #error BuildId is required
#endif
#ifndef AppIdentity
  #define AppIdentity "Poltergeist.Private.Companion"
#endif
#ifndef ProductName
  #define ProductName "Poltergeist Companion Private"
#endif
#ifndef ProfileDir
  #define ProfileDir "{localappdata}\Poltergeist\CompanionPrototype"
#endif
#ifndef MaxInstallPath
  #define MaxInstallPath 111
#endif
#ifndef ReleaseVersion
  #define ReleaseVersion "0.1.0"
#endif
#define RuntimeDir "{app}\versions\" + BuildId

[Setup]
AppId={#AppIdentity}
AppName={#ProductName}
AppVersion={#ReleaseVersion}
AppVerName={#ProductName} {#ReleaseVersion} (prototype)
#ifdef SignedBuild
SignTool=poltergeist
SignedUninstaller=yes
#endif
DefaultDirName={code:DefaultInstallFolder}
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Poltergeist-Companion-Private-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallLogging=yes
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
AppMutex=Poltergeist.Private.Companion.Running
SetupMutex={#AppIdentity}.Setup
UninstallDisplayIcon={#RuntimeDir}\runtime\pythonw.exe
UninstallDisplayName={#ProductName}
VersionInfoDescription={#ProductName} Setup

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
Name: startup; Description: "Open the companion when I sign in to Windows"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{#RuntimeDir}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{srcexe}"; DestDir: "{app}\maintenance"; DestName: "Repair.exe"; Flags: external ignoreversion; Check: ShouldCopyRepair

[InstallDelete]
Type: files; Name: "{userstartup}\{#ProductName}.lnk"; Tasks: not startup
Type: files; Name: "{userdesktop}\{#ProductName}.lnk"; Tasks: not desktopicon

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{#RuntimeDir}\runtime\pythonw.exe"; Parameters: "-I -B -m scripts.companion --data-dir ""{#ProfileDir}"""; WorkingDir: "{#RuntimeDir}"
Name: "{group}\Uninstall {#ProductName}"; Filename: "{uninstallexe}"
Name: "{group}\Repair {#ProductName}"; Filename: "{app}\maintenance\Repair.exe"; Parameters: "/DIR=""{app}"""
Name: "{userdesktop}\{#ProductName}"; Filename: "{#RuntimeDir}\runtime\pythonw.exe"; Parameters: "-I -B -m scripts.companion --data-dir ""{#ProfileDir}"""; Tasks: desktopicon
Name: "{userstartup}\{#ProductName}"; Filename: "{#RuntimeDir}\runtime\pythonw.exe"; Parameters: "-I -B -m scripts.companion --data-dir ""{#ProfileDir}"""; Tasks: startup

[Run]
Filename: "{#RuntimeDir}\runtime\pythonw.exe"; Parameters: "-I -B -m scripts.companion --data-dir ""{#ProfileDir}"""; Description: "Open Poltergeist Companion and first-run setup"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\installation.ini"; Check: ShouldDeleteInstallationMarker
Type: files; Name: "{app}\last-setup-check.json"
Type: dirifempty; Name: "{app}\versions"

[Code]
var
  RemoveData: Boolean;
  PreviousRuntime: String;
  RuntimeVerified: Boolean;

function GetFileAttributesW(Name: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function DefaultInstallFolder(Param: String): String;
begin
  Result := ExpandConstant('{localappdata}\Programs\{#ProductName}');
  if Length(Result) > {#MaxInstallPath} then
    Result := AddBackslash(GetEnv('USERPROFILE')) + '{#ProductName}';
end;

function ShouldCopyRepair(): Boolean;
begin
  Result := CompareText(ExpandConstant('{srcexe}'), ExpandConstant('{app}\maintenance\Repair.exe')) <> 0;
end;

function ShouldDeleteInstallationMarker(): Boolean;
begin
  { Recovery copies are intentionally retained. Keep their ownership marker so
    reinstall can reuse this folder without accepting an arbitrary nonempty one. }
  Result := not DirExists(ExpandConstant('{app}\recovery'));
end;

function PlainDirectoryChain(Folder: String): Boolean;
var
  Parent: String;
  Attributes: Cardinal;
begin
  Result := False;
  Folder := RemoveBackslashUnlessRoot(ExpandFileName(Folder));
  repeat
    Attributes := GetFileAttributesW(Folder);
    if (Attributes <> $FFFFFFFF) and
       (((Attributes and $400) <> 0) or ((Attributes and $10) = 0)) then exit;
    Parent := ExtractFileDir(Folder);
    if (Parent = '') or (CompareText(Parent, Folder) = 0) then break;
    Folder := Parent;
  until False;
  Result := True;
end;

function RecoveryFolder(): String;
var
  Base: String;
  Index: Integer;
begin
  Base := ExpandConstant('{app}\recovery');
  if not PlainDirectoryChain(Base) then
    RaiseException('The recovery folder or its parent is linked. Choose a normal local install folder.');
  if not ForceDirectories(Base) then
    RaiseException('Could not create the runtime recovery folder. No existing runtime was changed.');
  for Index := 1 to 9999 do begin
    Result := AddBackslash(Base) + '{#BuildId}-' + IntToStr(Index);
    if not FileExists(Result) and not DirExists(Result) then exit;
  end;
  RaiseException('The runtime recovery folder is full. Move its retained copies elsewhere before repair.');
end;

procedure PreservePreviousRuntime();
var
  Runtime: String;
begin
  if CheckForMutexes('Poltergeist.Private.Companion.Running') then
    RaiseException('Close the companion before installing or repairing its runtime.');
  Runtime := ExpandConstant('{#RuntimeDir}');
  if not PlainDirectoryChain(Runtime) then
    RaiseException('The runtime folder or its parent is linked. Existing files were preserved.');
  if not DirExists(Runtime) then exit;
  if GetIniString('Installation', 'AppId', '', ExpandConstant('{app}\installation.ini')) <> '{#AppIdentity}' then
    RaiseException('The existing runtime is not owned by this installation. Existing files were preserved.');
  PreviousRuntime := RecoveryFolder();
  if not RenameFile(Runtime, PreviousRuntime) then begin
    PreviousRuntime := '';
    RaiseException('Could not preserve the previous runtime. Close programs using it and retry repair.');
  end;
  Log('Previous runtime retained at ' + PreviousRuntime);
end;

function ValidInstallFolder(const Folder: String): String;
var
  Item: TFindRec;
  HasFiles: Boolean;
begin
  Result := '';
  if Length(Folder) > {#MaxInstallPath} then begin
    Result := 'Choose a shorter install folder (at most {#MaxInstallPath} characters). Native Python dependencies may fail in deeply nested paths.';
    exit;
  end;
  if not PlainDirectoryChain(Folder) then begin
    Result := 'Choose a normal local folder, not a link or junction.';
    exit;
  end;
  if DirExists(Folder) then begin
    HasFiles := False;
    if FindFirst(AddBackslash(Folder) + '*', Item) then begin
      try
        repeat
          if (Item.Name <> '.') and (Item.Name <> '..') then HasFiles := True;
        until not FindNext(Item);
      finally
        FindClose(Item);
      end;
    end;
    if HasFiles and (GetIniString('Installation', 'AppId', '',
       AddBackslash(Folder) + 'installation.ini') <> '{#AppIdentity}') then
      Result := 'This folder contains other files. Choose an empty folder or this companion''s existing install folder for repair.';
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    Problem := ValidInstallFolder(WizardDirValue);
    if Problem <> '' then begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  AppFolder, DataFolder: String;
begin
  Result := ValidInstallFolder(ExpandConstant('{app}'));
  AppFolder := AddBackslash(Lowercase(ExpandFileName(ExpandConstant('{app}'))));
  DataFolder := AddBackslash(Lowercase(ExpandFileName(ExpandConstant('{#ProfileDir}'))));
  if (Pos(AppFolder, DataFolder) = 1) or (Pos(DataFolder, AppFolder) = 1) then
    Result := 'Install the application separately from its user data folder. Existing data will not be moved.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  DataFolder: String;
begin
  if CurStep = ssInstall then PreservePreviousRuntime();
  if CurStep = ssPostInstall then begin
    SetIniString('Installation', 'AppId', '{#AppIdentity}', ExpandConstant('{app}\installation.ini'));
    SetIniString('Installation', 'Build', '{#BuildId}', ExpandConstant('{app}\installation.ini'));
    SetIniString('Installation', 'Version', '{#ReleaseVersion}', ExpandConstant('{app}\installation.ini'));
    DataFolder := ExpandConstant('{#ProfileDir}');
    SetIniString('Installation', 'Profile', DataFolder, ExpandConstant('{app}\installation.ini'));
    if not DirExists(DataFolder) then begin
      ForceDirectories(DataFolder);
      SetIniString('Owner', 'AppId', '{#AppIdentity}', AddBackslash(DataFolder) + 'installation-owner.ini');
      SetIniString('Owner', 'Profile', DataFolder, AddBackslash(DataFolder) + 'installation-owner.ini');
    end;
    if not Exec(ExpandConstant('{#RuntimeDir}\runtime\python.exe'),
       '-I -B -m scripts.install_check --output "' + ExpandConstant('{app}\last-setup-check.json') + '"',
       ExpandConstant('{#RuntimeDir}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      RaiseException('Installed runtime check failed. Choose a shorter folder and check the native Microsoft Visual C++ runtime. Review last-setup-check.json and the Setup log; rerun this installer to repair.');
    RuntimeVerified := True;
  end;
end;

procedure DeinitializeSetup();
var
  Runtime, FailedRuntime: String;
begin
  if RuntimeVerified or (PreviousRuntime = '') then exit;
  Runtime := ExpandConstant('{#RuntimeDir}');
  { Preserve a failed extraction too. Never recursively delete an old runtime. }
  if not PlainDirectoryChain(Runtime) or not PlainDirectoryChain(PreviousRuntime) then begin
    Log('Could not restore the retained runtime because a folder became linked: ' + PreviousRuntime);
    exit;
  end;
  if DirExists(Runtime) then begin
    FailedRuntime := RecoveryFolder();
    if not RenameFile(Runtime, FailedRuntime) then begin
      Log('Could not move the failed runtime aside. Previous runtime remains at ' + PreviousRuntime);
      exit;
    end;
  end;
  if RenameFile(PreviousRuntime, Runtime) then
    Log('Restored the previous runtime after an unsuccessful repair.')
  else
    Log('Could not restore the previous runtime. Its files remain at ' + PreviousRuntime);
end;

function InitializeUninstall(): Boolean;
var
  Choice: Integer;
  DataFolder: String;
begin
  Result := True;
  RemoveData := False;
  DataFolder := ExpandConstant('{#ProfileDir}');
  if UninstallSilent then begin
    RemoveData := ExpandConstant('{param:REMOVEOWNEDDATA|0}') = '1';
    exit;
  end;
  Choice := MsgBox('Keep your settings, experiments and recordings at ' + DataFolder + '?'#13#10#13#10 +
    'Yes keeps all data. No permanently removes only a profile created by this installer. Existing imported profiles and linked folders are preserved.',
    mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON1);
  Result := Choice <> IDCANCEL;
  RemoveData := Choice = IDNO;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataFolder: String;
  ExitCode: Integer;
  ReportPath: String;
begin
  if (CurUninstallStep = usUninstall) and RemoveData then begin
    DataFolder := RemoveBackslashUnlessRoot(ExpandConstant('{#ProfileDir}'));
    ReportPath := ExpandConstant('{app}-profile-removal.json');
    if not Exec(ExpandConstant('{#RuntimeDir}\runtime\python.exe'),
      '-I -B -m scripts.remove_owned_profile --profile "' + DataFolder +
      '" --owner "{#AppIdentity}" --output "' + ReportPath + '"',
      ExpandConstant('{#RuntimeDir}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then begin
      Log('Some or all profile data was preserved. Review ' + ReportPath);
      if not UninstallSilent then
        MsgBox('Profile data could not be fully removed and was preserved where necessary. Review ' + ReportPath, mbInformation, MB_OK);
    end;
  end;
end;
