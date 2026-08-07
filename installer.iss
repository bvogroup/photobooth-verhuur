; MyBoothBox Photobooth Installer (verhuur-versie)
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
; ── DE NAAMSWIJZIGING BOOTHAROO → MYBOOTHBOX ───────────────────────────────
;
; Het programma heet naar buiten toe MyBoothBox. Wat NIET meeverhuist:
;
;   * De AppId hieronder. Zie de waarschuwing daar.
;   * De gegevensmap ~\Documents\Bootharoo (config.DATA_DIR). Daar staan op
;     elke draaiende booth de events, sjablonen, instellingen en de
;     uploadwachtrij. Die verplaatsen betekent migreren, en dat kan stuk.
;   * De naam van dit .iss-bestand en van bootharoo.spec — bouwbestanden,
;     die ziet geen enkele gebruiker.
;
; Op een booth die al een Bootharoo-installatie heeft, draait tijdens deze
; installatie nog de OUDE Bootharoo.exe en staan er nog autostart-resten met
; de oude naam. Alles wat daarvoor nodig is staat hieronder bij elkaar met
; het voorvoegsel "Oude".
#define MyAppName "MyBoothBox"
; Het versienummer komt van buiten mee (ISCC /DMyAppVersion=1.99.147) zodat het
; niet uit de pas kan lopen met config.VERSION. Zonder die vlag geldt de
; waarde hieronder, en blijft een handmatige build werken zoals hij deed.
#ifndef MyAppVersion
  #define MyAppVersion "1.99.146"
#endif
#define MyAppPublisher "MyBoothBox"
#define MyAppURL "https://myboothbox.nl"
#define MyAppExeName "MyBoothBox.exe"
#define MyTaskName "MyBoothBox Photobooth"

; De namen van vóór de naamswijziging. Deze staan hier alleen om resten op te
; ruimen; er wordt niets meer onder deze namen aangemaakt. Weghalen mag pas
; als geen enkele booth in het veld nog van Bootharoo komt.
#define OudeAppName "Bootharoo"
#define OudeAppExeName "Bootharoo.exe"
#define OudeTaskName "Bootharoo Photobooth"

[Setup]
; Consistent AppId ensures upgrades work correctly (never change this GUID)
;
; LET OP: deze GUID blijft staan, óók nu het programma anders heet. Voor
; Windows is de AppId hét kenmerk van een programma. Zou hij veranderen, dan
; ziet Windows MyBoothBox als een tweede, los programma: bestaande booths
; krijgen dan twee installaties naast elkaar in de programmalijst, twee
; verwijderaars en twee mappen, in plaats van een nette opwaardering.
AppId={{B00TH4R00-PH0T0-B00T-H000-000000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Zonder deze regel toont Windows' programmalijst nog de naam waarmee de
; vorige installatie is geregistreerd. Met dezelfde AppId én een expliciete
; weergavenaam staat er na de opwaardering gegarandeerd MyBoothBox.
UninstallDisplayName={#MyAppName} {#MyAppVersion}
;
; DefaultDirName geldt alleen voor een booth die nog niets geïnstalleerd
; heeft; die krijgt Program Files (x86)\MyBoothBox. Een booth die van
; Bootharoo komt houdt door UsePreviousAppDir zijn bestaande map
; Program Files (x86)\Bootharoo. Dat is met opzet: de installatiemap ziet
; niemand, en 'm alsnog verplaatsen zou betekenen dat de oude map met de
; draaiende exe erin moet worden leeggehaald terwijl Windows die vasthoudt —
; precies het soort actie dat een booth op een feestavond stuk maakt.
; Naam, snelkoppelingen, taakbalk en programmalijst zeggen wél MyBoothBox.
DefaultDirName={commonpf32}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; De installatiemap mag blijven staan waar hij staat, de startmenu-map niet.
; Inno hergebruikt standaard de groepsnaam van de vorige installatie
; (UsePreviousGroup), en dat zou op een booth die van Bootharoo komt een map
; "Bootharoo" in het startmenu opleveren met daarin een snelkoppeling
; "MyBoothBox" — precies wat de opdracht niet wil. Met 'no' geldt
; DefaultGroupName hierboven en heet de map MyBoothBox. De oude map wordt bij
; [InstallDelete] opgeruimd; anders bleef die als lege huls achter.
UsePreviousGroup=no
OutputDir=dist
OutputBaseFilename={#MyAppName}_Setup_v{#MyAppVersion}
; Het merkicoon op het installatiebestand zelf. updater.py herkent de
; installer aan de extensie .exe en niet aan de bestandsnaam, dus de
; hernoemde installer bereikt booths in het veld gewoon.
SetupIconFile=icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=yes
PrivilegesRequired=admin
MinVersion=10.0
; Automatically close the running app before installing/updating
;
; Dit stond op alleen "Bootharoo.exe". Dat kan niet blijven: het filter zegt
; welke te installeren BESTANDEN Setup op een slot controleert, en
; Bootharoo.exe wordt niet meer geïnstalleerd — het zou dus nooit meer
; kijken. Andersom is een booth die van Bootharoo komt precies het geval
; waarin de draaiende exe nog Bootharoo.exe heet.
; Met *.exe,*.dll wordt elk te vervangen bestand gecontroleerd; de oude
; Bootharoo.exe houdt namelijk óók de meegeleverde DLL's vast, dus zo wordt
; hij hoe dan ook gevonden en gesloten. Zonder dit blijft er een bestand
; vergrendeld en mislukt de installatie halverwege.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Snelkoppeling op bureaublad aanmaken"; GroupDescription: "Extra opties:"

[InstallDelete]
; Resten van Bootharoo weghalen VOORDAT de nieuwe bestanden erin gaan.
;
; Inno verwijdert bij een opwaardering alleen wat het zelf opnieuw neerzet.
; De oude Bootharoo.exe staat niet meer in [Files] en zou dus voorgoed in de
; installatiemap blijven staan: twee exe's, twee iconen, en een gerede kans
; dat een oude snelkoppeling of een oude Taakplanner-taak de verkeerde
; aanwijst. Daarom hier expliciet weg.
Type: files; Name: "{app}\{#OudeAppExeName}"
; De hele oude startmenu-map, inclusief de snelkoppelingen erin. Hier staat
; met opzet niet {group}: die wijst na UsePreviousGroup=no al naar de nieuwe
; map, dus de oude moet bij naam genoemd worden. Beide plekken, omdat oudere
; installaties de groep per gebruiker of voor iedereen konden aanmaken.
Type: filesandordirs; Name: "{commonprograms}\{#OudeAppName}"
Type: filesandordirs; Name: "{userprograms}\{#OudeAppName}"
; En de bureaubladsnelkoppeling die naar de verdwenen Bootharoo.exe wijst.
Type: files; Name: "{commondesktop}\{#OudeAppName}.lnk"
Type: files; Name: "{userdesktop}\{#OudeAppName}.lnk"

[Files]
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
; Zowel de oude als de nieuwe taaknaam gaan hier weg. De oude omdat een booth
; die van Bootharoo komt nog een taak 'Bootharoo Photobooth' heeft die naar
; de verdwenen Bootharoo.exe wijst; de nieuwe omdat het opnieuw registreren
; hieronder anders op een bestaande taak zou stuiten. Na deze regel is er
; gegarandeerd GEEN autostart-taak meer, zodat de volgende regel er precies
; één aanmaakt en er nooit twee tegelijk draaien.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Unregister-ScheduledTask -TaskName '{#OudeTaskName}' -Confirm:$false -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName '{#MyTaskName}' -Confirm:$false -ErrorAction SilentlyContinue; foreach ($n in '{#OudeAppName}','{#MyAppName}') {{ Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $n -ErrorAction SilentlyContinue; Remove-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $n -ErrorAction SilentlyContinue }; $startup = [Environment]::GetFolderPath('Startup'); $cstartup = [Environment]::GetFolderPath('CommonStartup'); foreach ($map in $startup, $cstartup) {{ foreach ($p in '*{#OudeAppName}*','*{#MyAppName}*') {{ Get-ChildItem $map -Filter $p -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue } }"""; Flags: runhidden

; Register new Task Scheduler task (always — autostart is automatic)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$action = New-ScheduledTaskAction -Execute '{app}\{#MyAppExeName}'; $trigger = New-ScheduledTaskTrigger -AtLogOn; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Priority 0; Register-ScheduledTask -TaskName '{#MyTaskName}' -Action $action -Trigger $trigger -Settings $settings -Force -RunLevel Highest"""; Flags: runhidden

; Launch na install bij een handmatige (zichtbare) installatie — checkbox
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} starten"; Flags: nowait postinstall skipifsilent
; Launch na install bij een STILLE installatie (auto-update vanuit de app):
; de in-app updater draait de installer met /SILENT, dus de booth moet
; daarna vanzelf weer opstarten.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; Remove Task Scheduler task on uninstall — beide namen, want op een booth
; die ooit van Bootharoo kwam kan de oude taak nog bestaan.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Unregister-ScheduledTask -TaskName '{#MyTaskName}' -Confirm:$false -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName '{#OudeTaskName}' -Confirm:$false -ErrorAction SilentlyContinue"""; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  // Het draaiende programma afsluiten voor de update.
  //
  // BEIDE namen, en in deze volgorde. Op een booth die van Bootharoo komt
  // draait op dit moment nog Bootharoo.exe: die houdt de DLL's in de
  // installatiemap vast en moet weg voordat er iets overheen kan. Op een
  // booth die al MyBoothBox draait geldt hetzelfde voor MyBoothBox.exe.
  // taskkill op een naam die niet draait doet niets en geeft alleen een
  // andere afloopcode terug; die negeren we bewust.
  Exec('taskkill', '/F /IM {#OudeAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Exec('taskkill', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Namen: array[0..1] of String;
  I: Integer;
  Pad: String;
begin
  if CurStep = ssInstall then
  begin
    Namen[0] := '{#OudeAppName}';
    Namen[1] := '{#MyAppName}';

    for I := 0 to 1 do
    begin
      // Alle oude autostart-snelkoppelingen uit de opstartmappen halen.
      // Dit ruimt scheve of kapotte snelkoppelingen van eerdere installaties
      // op; de autostart loopt via de Taakplanner-taak hierboven.
      Pad := ExpandConstant('{userstartup}\') + Namen[I] + '.lnk';
      if FileExists(Pad) then
        DeleteFile(Pad);

      Pad := ExpandConstant('{commonstartup}\') + Namen[I] + '.lnk';
      if FileExists(Pad) then
        DeleteFile(Pad);

      // En de autostart-sleutels die oude installers in het register zetten.
      RegDeleteValue(HKEY_CURRENT_USER,
        'Software\Microsoft\Windows\CurrentVersion\Run', Namen[I]);
      RegDeleteValue(HKEY_LOCAL_MACHINE,
        'Software\Microsoft\Windows\CurrentVersion\Run', Namen[I]);
    end;
  end;
end;
