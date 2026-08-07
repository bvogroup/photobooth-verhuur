; Bootharoo Photobooth Installer (verhuur-versie)
; Built with Inno Setup 6
;
; Build steps:
;   1. cd C:\Photobooth-verhuur
;   2. pyinstaller bootharoo.spec --noconfirm
;   3. "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Alle paden hieronder zijn RELATIEF aan de map waar dit .iss-bestand staat.
; Op de ontwikkelmachine is dat C:\Photobooth-verhuur, dus daar verandert er
; niets; op een bouwserver (GitHub Actions) werkt het daardoor ook.
;
; ── WAAROM HIER BOOTHAROO STAAT EN NIET MYBOOTHBOX ─────────────────────────
;
; Het product heet naar buiten toe MyBoothBox. Dat zit in het opstartscherm,
; het startscherm, het icoon en de teksten in de app — daar ziet de gast en de
; verhuurder het.
;
; Alles wat WINDOWS aangaat blijft Bootharoo: de exe, de Taakplanner-taak, de
; installatiemap, de snelkoppelingen, de opruiming van autostart-resten. Er
; staan 25 booths bij klanten die zichzelf bij de eerstvolgende overdracht
; verplicht bijwerken. De winst van een hernoemde exe is cosmetisch — niemand
; op een feest kijkt in Taakbeheer — en de kosten zijn 25 apparaten die na een
; verplichte update misschien niet meer vanzelf opstarten. Die ruil is niet de
; moeite waard.
;
; Dit bestand is daarom gelijk aan dat van v1.99.148, de versie die nu op die
; booths draait, op één ding na: SetupIconFile hieronder. Wie hier iets aan
; verandert, verandert het opstartpad van elke booth in het veld.
#define MyAppName "Bootharoo"
; Het versienummer komt van buiten mee (ISCC /DMyAppVersion=1.99.147) zodat het
; niet uit de pas kan lopen met config.VERSION. Zonder die vlag geldt de
; waarde hieronder, en blijft een handmatige build werken zoals hij deed.
#ifndef MyAppVersion
  #define MyAppVersion "1.99.146"
#endif
#define MyAppPublisher "Bootharoo"
#define MyAppURL "https://bootharoo.com"
#define MyAppExeName "Bootharoo.exe"
#define MyTaskName "Bootharoo Photobooth"

[Setup]
; Consistent AppId ensures upgrades work correctly (never change this GUID)
;
; Deze GUID is voor Windows hét kenmerk van een programma. Zou hij veranderen,
; dan ziet Windows dit als een tweede, los programma: bestaande booths krijgen
; twee installaties naast elkaar in de programmalijst, twee verwijderaars en
; twee mappen, in plaats van een nette opwaardering.
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
OutputDir=dist
OutputBaseFilename={#MyAppName}_Setup_v{#MyAppVersion}
; Het merkicoon op het installatiebestand zelf. Dit is wat de verhuurder ziet
; als hij de installer voor zich heeft, en het is het enige verschil met de
; installer van v1.99.148. icon.ico raakt niets aan de installatie: het is
; hetzelfde bestand dat bootharoo.spec al in de exe bakt, alleen nu ook op de
; setup. updater.py herkent de installer aan de extensie .exe en niet aan de
; bestandsnaam of het icoon.
SetupIconFile=icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=yes
PrivilegesRequired=admin
MinVersion=10.0
; Automatically close the running app before installing/updating
;
; Alleen Bootharoo.exe, want dat is het enige dat er draait en het enige dat
; hier geïnstalleerd wordt. Het filter zegt welke te installeren BESTANDEN
; Setup op een slot controleert; een breder filter (*.exe,*.dll) zou hier
; niets toevoegen en alleen meer bestanden bij de gebruiker in beeld brengen.
CloseApplications=force
CloseApplicationsFilter=Bootharoo.exe
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Snelkoppeling op bureaublad aanmaken"; GroupDescription: "Extra opties:"

[Files]
; De mapnaam onder dist\ komt uit bootharoo.spec (COLLECT name=). Die twee
; moeten gelijk lopen, anders vindt Setup hier niets.
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Systeem-tweak-script (eenmalig per booth handmatig als admin draaien)
Source: "optimize_surface.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} verwijderen"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; First remove any old/corrupt autostart entries (always runs, hidden)
;
; LET OP bij het bewerken van deze regel: Inno Setup leest een losse { als het
; begin van een constante en breekt de compilatie af met "Unknown constant".
; Een letterlijke accolade — bijvoorbeeld van een PowerShell-blok — moet als
; {{ geschreven worden. Daar is de bouw op 7 augustus 2026 al een keer op
; gestruikeld. Deze regel heeft er nu geen enkele nodig, en dat is de
; goedkoopste manier om die fout uit te sluiten: hij is één rechte reeks
; opdrachten zonder blokken.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Unregister-ScheduledTask -TaskName '{#MyTaskName}' -Confirm:$false -ErrorAction SilentlyContinue; Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name '{#MyAppName}' -ErrorAction SilentlyContinue; Remove-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' -Name '{#MyAppName}' -ErrorAction SilentlyContinue; $startup = [Environment]::GetFolderPath('Startup'); Get-ChildItem $startup -Filter '*Bootharoo*' | Remove-Item -Force -ErrorAction SilentlyContinue; $cstartup = [Environment]::GetFolderPath('CommonStartup'); Get-ChildItem $cstartup -Filter '*Bootharoo*' | Remove-Item -Force -ErrorAction SilentlyContinue"""; Flags: runhidden

; Register new Task Scheduler task (always — autostart is automatic)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$action = New-ScheduledTaskAction -Execute '{app}\{#MyAppExeName}'; $trigger = New-ScheduledTaskTrigger -AtLogOn; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Priority 0; Register-ScheduledTask -TaskName '{#MyTaskName}' -Action $action -Trigger $trigger -Settings $settings -Force -RunLevel Highest"""; Flags: runhidden

; Launch na install bij een handmatige (zichtbare) installatie — checkbox
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} starten"; Flags: nowait postinstall skipifsilent
; Launch na install bij een STILLE installatie (auto-update vanuit de app):
; de in-app updater draait de installer met /SILENT, dus de booth moet
; daarna vanzelf weer opstarten.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent

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
