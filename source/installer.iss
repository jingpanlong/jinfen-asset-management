[Setup]
AppId={{66A38AC1-8789-4FD1-B914-C709E13BC564}
AppName=锦纷资产管理系统
AppVersion=2.5.0
MinVersion=10.0.17763
AppPublisher=锦纷资产管理系统
DefaultDirName={localappdata}\Programs\JinfenAssets
DefaultGroupName=锦纷资产管理系统
UsePreviousGroup=no
UsePreviousTasks=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer
OutputBaseFilename=锦纷资产管理系统-安装版-2.5.0
SetupIconFile=app.ico
UninstallDisplayIcon={app}\锦纷资产管理系统.exe
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "可选设置:"; Flags: unchecked
Name: "autostart"; Description: "随当前用户的 Windows 登录自动启动"; GroupDescription: "可选设置:"; Flags: unchecked

[Files]
Source: "..\portable\2.5.0\锦纷资产管理系统.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\2.1新增功能与操作说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\2.2高级可视化使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\2.3自启动与托盘使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\2.4高级权限使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\第三方软件声明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\锦纷资产管理系统"; Filename: "{app}\锦纷资产管理系统.exe"
Name: "{autodesktop}\锦纷资产管理系统"; Filename: "{app}\锦纷资产管理系统.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\锦纷资产管理系统.exe"; Description: "启动锦纷资产管理系统"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "JinfenAssets"; ValueData: """{app}\锦纷资产管理系统.exe"" --startup"; Tasks: autostart

[InstallDelete]
Type: files; Name: "{app}\QiyunAssets.exe"
Type: files; Name: "{userprograms}\栖云资产管理系统\栖云资产管理系统.lnk"
Type: files; Name: "{autodesktop}\栖云资产管理系统.lnk"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Command: String;
begin
  if CurUninstallStep = usUninstall then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'JinfenAssets', Command) then
      if Pos(Lowercase(ExpandConstant('{app}\锦纷资产管理系统.exe')), Lowercase(Command)) > 0 then
        RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'JinfenAssets');
end;

// Uninstallation deliberately leaves company databases and backups intact.

[Files]
Source: "..\2.5资产图片与详情使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\2.6增删改查使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\2.7局域网共享使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
