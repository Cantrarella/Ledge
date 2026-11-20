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
DisableWelcomePage=yes
DisableReadyPage=yes
WizardResizable=no
WizardImageFile=wizard_side.bmp
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
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "开机自动启动 {#MyAppName}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

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
procedure InitializeWizard;
var
  SidePoster: TBitmapImage;
  RightLeft, RightW: Integer;
begin
  WizardForm.ClientWidth := ScaleX(580);
  WizardForm.ClientHeight := ScaleY(370);
  WizardForm.Color := clWhite;
  WizardForm.Font.Name := 'Microsoft YaHei UI';

  { 隐藏默认顶部横幅与底部分割线，保留标准系统标题栏以便正常拖拽 }
  WizardForm.MainPanel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.Bevel.Visible := False;

  { 左侧贯通全高的 Ledge 品牌海报 }
  SidePoster := TBitmapImage.Create(WizardForm);
  SidePoster.Parent := WizardForm;
  SidePoster.SetBounds(0, 0, ScaleX(196), WizardForm.ClientHeight);
  SidePoster.Bitmap := WizardForm.WizardBitmapImage.Bitmap;
  SidePoster.Stretch := True;
  SidePoster.BringToFront;

  RightLeft := ScaleX(222);
  RightW := WizardForm.ClientWidth - ScaleX(246);

  { 右侧主内容区 }
  WizardForm.OuterNotebook.SetBounds(ScaleX(196), 0, WizardForm.ClientWidth - ScaleX(196), WizardForm.ClientHeight - ScaleY(56));
  WizardForm.InnerPage.Color := clWhite;
  WizardForm.InnerNotebook.SetBounds(ScaleX(26), ScaleY(24), RightW, WizardForm.OuterNotebook.Height - ScaleY(28));

  { 同步各内置页面控件宽度，确保中文不截断 }
  WizardForm.SelectDirLabel.Width := RightW;
  WizardForm.SelectDirLabel.WordWrap := True;
  WizardForm.SelectDirBrowseLabel.Width := RightW;
  WizardForm.SelectDirBrowseLabel.WordWrap := True;
  WizardForm.DirEdit.Width := RightW - ScaleX(82);
  WizardForm.DirBrowseButton.Left := RightW - ScaleX(74);
  WizardForm.DiskSpaceLabel.Width := RightW;

  WizardForm.SelectTasksLabel.Width := RightW;
  WizardForm.SelectTasksLabel.WordWrap := True;
  WizardForm.TasksList.Width := RightW;

  WizardForm.StatusLabel.Width := RightW;
  WizardForm.FilenameLabel.Width := RightW;
  WizardForm.ProgressGauge.Width := RightW;

  WizardForm.FinishedHeadingLabel.Width := RightW;
  WizardForm.FinishedLabel.Width := RightW;
  WizardForm.FinishedLabel.WordWrap := True;
  WizardForm.RunList.Width := RightW;

  WizardForm.PreparingLabel.Width := RightW;
  WizardForm.PreparingLabel.WordWrap := True;
  WizardForm.PreparingMemo.Width := RightW;

  { 紧凑尺寸的底部操作按钮 }
  WizardForm.BackButton.SetBounds(RightLeft, WizardForm.ClientHeight - ScaleY(38), ScaleX(68), ScaleY(25));
  WizardForm.NextButton.SetBounds(WizardForm.ClientWidth - ScaleX(174), WizardForm.ClientHeight - ScaleY(38), ScaleX(80), ScaleY(25));
  WizardForm.CancelButton.SetBounds(WizardForm.ClientWidth - ScaleX(86), WizardForm.ClientHeight - ScaleY(38), ScaleX(66), ScaleY(25));
  WizardForm.BackButton.BringToFront;
  WizardForm.NextButton.BringToFront;
  WizardForm.CancelButton.BringToFront;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
    WizardForm.NextButton.Caption := '立即安装'
  else if CurPageID = wpFinished then
  begin
    WizardForm.NextButton.Caption := '完成';
    WizardForm.CancelButton.Visible := False;
  end;
end;

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
      if MsgBox('是否同时删除个人配置与本地数据（config.json 和 data 文件夹）？', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
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
