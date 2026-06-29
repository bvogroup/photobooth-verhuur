<#
  optimize_surface.ps1 — koeler & stabieler draaien op een Surface Pro 7

  De Surface Pro 7 is FANLESS (passief gekoeld) — er is geen ventilator om
  warmte af te voeren, dus hij throttle't hard zodra de CPU te heet wordt.
  Daarom is Turbo Boost uitzetten hier de belangrijkste maatregel. Alles is
  OMKEERBAAR met -Restore.

  Wat het doet:
    1. CPU-max op 99%  -> schakelt Turbo Boost uit. DE tweak op een fanless
       toestel: de CPU blijft bijna even snel maar wordt fors koeler en valt
       niet meer in de throttle-klif.
    2. Adaptieve helderheid uit + schermhelderheid op 90% (vast).
    3. Windows Search-indexering + SysMain pauzeren -> minder achtergrond-IO.

  Bewust NIET aangeraakt: Windows Defender en Windows Update (veiligheid).
  Koelbeleid (ventilator) wordt NIET gezet — een Surface Pro 7 heeft geen fan.

  GEBRUIK (als Administrator, PowerShell):
     Optimaliseren:   powershell -ExecutionPolicy Bypass -File optimize_surface.ps1
     Terugzetten:     powershell -ExecutionPolicy Bypass -File optimize_surface.ps1 -Restore
#>

param([switch]$Restore)

# --- Admin-check ---
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "Dit script moet als Administrator draaien." -ForegroundColor Red
  Write-Host "Rechtsklik PowerShell -> 'Als administrator uitvoeren', en draai opnieuw."
  exit 1
}

$SUB_PROC = "SUB_PROCESSOR"
$MAXFREQ  = "PROCTHROTTLEMAX"   # max processor state (%)
$SUB_DISP = "SUB_VIDEO"
$ADAPTBRT = "ADAPTBRIGHT"       # adaptieve helderheid: 0 = uit, 1 = aan

function Set-PowerValue($sub, $guid, $value) {
  powercfg /setacvalueindex SCHEME_CURRENT $sub $guid $value | Out-Null
  powercfg /setdcvalueindex SCHEME_CURRENT $sub $guid $value | Out-Null
}

function Set-Brightness($pct) {
  try {
    (Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, $pct) | Out-Null
    Write-Host "  Schermhelderheid op $pct% gezet"
  } catch { Write-Host "  Kon helderheid niet zetten: $($_.Exception.Message)" -ForegroundColor Yellow }
}

if ($Restore) {
  Write-Host "== Terugzetten naar standaard ==" -ForegroundColor Cyan
  Set-PowerValue $SUB_PROC $MAXFREQ 100   # Turbo weer aan
  Set-PowerValue $SUB_DISP $ADAPTBRT 1    # adaptieve helderheid weer aan
  powercfg /setactive SCHEME_CURRENT | Out-Null
  foreach ($svc in "WSearch","SysMain") {
    try {
      Set-Service $svc -StartupType Automatic -ErrorAction Stop
      Start-Service $svc -ErrorAction SilentlyContinue
      Write-Host "  $svc terug op Automatisch + gestart"
    } catch { Write-Host "  $svc kon niet hersteld worden: $($_.Exception.Message)" -ForegroundColor Yellow }
  }
  Write-Host "Klaar — standaardinstellingen hersteld." -ForegroundColor Green
  exit 0
}

Write-Host "== Surface Pro 7 koeler maken (fanless) ==" -ForegroundColor Cyan

# 1. Turbo Boost uit (max 99%) — grootste winst op een fanless toestel
Set-PowerValue $SUB_PROC $MAXFREQ 99
Write-Host "  CPU-max op 99% gezet (Turbo Boost uit -> koeler, geen throttle-klif)"

# 2. Adaptieve helderheid uit + vaste 90% (zo blijft onze instelling staan)
Set-PowerValue $SUB_DISP $ADAPTBRT 0
powercfg /setactive SCHEME_CURRENT | Out-Null
Set-Brightness 90

# 3. Achtergrond-vreters temmen
foreach ($svc in "WSearch","SysMain") {
  try {
    Stop-Service $svc -Force -ErrorAction SilentlyContinue
    Set-Service $svc -StartupType Manual -ErrorAction Stop
    Write-Host "  $svc gepauzeerd (start niet meer automatisch)"
  } catch { Write-Host "  $svc kon niet gepauzeerd worden: $($_.Exception.Message)" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Klaar! Nog koeler:" -ForegroundColor Green
Write-Host "  - Houd de achterkant vrij; leg 'm niet op een zachte/warme ondergrond."
Write-Host "  - De app zet de helderheid zelf ook op 90% bij elke start."
Write-Host "  - Terugzetten kan altijd:  optimize_surface.ps1 -Restore"
