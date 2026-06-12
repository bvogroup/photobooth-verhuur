; Bootharoo Photobooth Installer (verhuur-versie)
; Built with Inno Setup 6
;
; Build steps:
;   1. cd C:\Photobooth-verhuur
;   2. pyinstaller bootharoo.spec --noconfirm
;   3. "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss

#define MyAppName "Bootharoo"
#define MyAppVersion "1.99.112"
#define MyAppPublisher "Bootharoo"
#define MyAppURL "https://bootharoo.com"
#define MyAppExeName "Bootharoo.exe"
#define MyTaskName "Bootharoo Photobooth"

[Setup]
; Consistent AppId ensures upgrades work correctly (never change this GUID)
AppId={{B00TH4R00-PH0T0-B00T-H000-000000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={commonpf32}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=C:\Photobooth-verhuur\dist
OutputBaseFilename=Bootharoo_Setup_v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=yes
PrivilegesRequired=admin
MinVersion=10.0
; Automatically close the running app before installing/updating
CloseApplications=force
CloseApplicationsFilter=Bootharoo.exe
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Snelkoppeling op bureaublad aanmaken"; GroupDescription: "Extra opties:"

[Files]
Source: "C:\Photobooth-verhuur\dist\Bootharoo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} verwijderen"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; First remove any old/corrupt autostart entries (always runs, hidden)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Unregister-ScheduledTask -TaskName '{#MyTaskName}' -Confirm:$false -ErrorAction SilentlyContinue; Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name '{#MyAppName}' -ErrorAction SilentlyContinue; Remove-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' -Name '{#MyAppName}' -ErrorAction SilentlyContinue; $startup = [Environment]::GetFolderPath('Startup'); Get-ChildItem $startup -Filter '*Bootharoo*' | Remove-Item -Force -ErrorAction SilentlyContinue; $cstartup = [Environment]::GetFolderPath('CommonStartup'); Get-ChildItem $cstartup -Filter '*Bootharoo*' | Remove-Item -Force -ErrorAction SilentlyContinue"""; Flags: runhidden

; Register new Task Scheduler task (always — autostart is automatic)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$action = New-ScheduledTaskAction -Execute '{app}\{#MyAppExeName}'; $trigger = New-ScheduledTaskTrigger -AtLogOn; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Priority 0; Register-ScheduledTask -TaskName '{#MyTaskName}' -Action $action -Trigger $trigger -Settings $settings -Force -RunLevel Highest"""; Flags: runhidden

; Optionally launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} starten"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove Task Scheduler task on uninstall
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Unregister-ScheduledTask -TaskName '{#MyTaskName}' -Confirm:$false -ErrorAction SilentlyContinue"""; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  // Kill running Bootharoo before updating
  Exec('taskkill', '/F /IM Bootharoo.exe', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  StartupFile: String;
  CommonStartupFile: String;
begin
  if CurStep = ssInstall then
  begin
    // Remove ALL old autostart shortcuts from Startup folders before installing
    // This cleans up corrupt/old-path shortcuts from previous installs
    StartupFile := ExpandConstant('{userstartup}\Bootharoo.lnk');
    if FileExists(StartupFile) then
      DeleteFile(StartupFile);

    CommonStartupFile := ExpandConstant('{commonstartup}\Bootharoo.lnk');
    if FileExists(CommonStartupFile) then
      DeleteFile(CommonStartupFile);

    // Also remove registry autostart entries left by old installers
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'Bootharoo');
    RegDeleteValue(HKEY_LOCAL_MACHINE,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'Bootharoo');
  end;
end;
