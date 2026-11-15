#ifndef AppVersion
  #define AppVersion "1.7.0"
#endif
#define AppName "Print Cost Calculator"
#define AppExe "PrintCostCalculator.exe"
#define PdfKey "Software\Classes\SystemFileAssociations\.pdf\shell\PrintCostCalculator"
#define DocxKey "Software\Classes\SystemFileAssociations\.docx\shell\PrintCostCalculator"

[Setup]
AppId={{8C5E1F0A-3B7D-4E92-A1C6-5D2F9B6E7A43}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=MarkCelesteria
AppPublisherURL=https://github.com/MarkCelesteria/2D_Print_Cost_Calculator
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\assets\app.ico
OutputDir=..\dist
OutputBaseFilename=PrintCostCalculator-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}.0

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "contextmenu"; Description: "Add ""Calculate Print Cost"" to the right-click menu of PDF and Word files"; GroupDescription: "Integration:"
Name: "startup"; Description: "Start with &Windows (opens quietly in the system tray)"; GroupDescription: "Integration:"; Flags: unchecked

[Files]
Source: "..\dist\PrintCostCalculator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PrintCostCalculator"; ValueData: """{app}\{#AppExe}"" --minimized"; Flags: uninsdeletevalue; Tasks: startup
Root: HKCU; Subkey: "{#PdfKey}"; ValueType: string; ValueName: ""; ValueData: "Calculate Print Cost"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#PdfKey}"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKCU; Subkey: "{#PdfKey}\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "{#DocxKey}"; ValueType: string; ValueName: ""; ValueData: "Calculate Print Cost"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#DocxKey}"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppExe}"; Tasks: contextmenu
Root: HKCU; Subkey: "{#DocxKey}\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".docx"; ValueData: ""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithList\{#AppExe}"; ValueType: none; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\.docx\OpenWithList\{#AppExe}"; ValueType: none; Flags: uninsdeletekey; Tasks: contextmenu

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[Code]
procedure QuitRunningApp();
var
  Exe: String;
  Code: Integer;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  QuitRunningApp();
  Result := True;
end;