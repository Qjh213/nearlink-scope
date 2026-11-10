#define MyAppName "NearLink LinkScope"
#define MyAppVersion "0.3.6"
#define MyAppPublisher "Qjh213"
#define MyAppURL "https://github.com/Qjh213/nearlink-scope"
#define MyAppExeName "LinkScope.exe"

[Setup]
AppId={{A311E290-219E-4DB7-B863-0D0138099BC2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\NearLink LinkScope
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist\release
OutputBaseFilename=NearLink-LinkScope-0.3.6-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "..\..\dist\LinkScope\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\NOTICE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\NearLink LinkScope"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 NearLink LinkScope"; Filename: "{uninstallexe}"
Name: "{autodesktop}\NearLink LinkScope"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 NearLink LinkScope"; Flags: nowait postinstall skipifsilent
