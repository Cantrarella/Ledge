; Ledge Windows 安装包脚本 (Inno Setup 6)
; 单用户免 UAC 安装至 %LOCALAPPDATA%\Programs\Ledge，支持覆盖升级保留配置，支持卸载可选清理个人数据

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Ledge"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define MyAppName "Ledge"
#define MyAppPublisher "Cantrarella"
#define MyAppURL "https://github.com/Cantrarella/Ledge"
#define MyAppExeName "Ledge.exe"

[Setup]
AppId={{7B6E3A8D-5C3C-4B3D-9A7B-4C7D8D19A2F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\wisland\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
OutputDir={#OutputDir}
OutputBaseFilename=Ledge-Setup-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Launch {#MyAppName} automatically at Windows startup"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "config.json,data\*.log,data\*.json,data\wisland.lock"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Ledge"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  CfgPath, DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    CfgPath := ExpandConstant('{app}\config.json');
    DataDir := ExpandConstant('{app}\data');
    if (FileExists(CfgPath) or DirExists(DataDir)) and (not UninstallSilent) then
    begin
      if MsgBox('Remove personal configuration and local data (config.json and data folder)?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        if FileExists(CfgPath) then
          DeleteFile(CfgPath);
        if DirExists(DataDir) then
          DelTree(DataDir, True, True, True);
        RemoveDir(ExpandConstant('{app}'));
      end;
    end;
  end;
end;
