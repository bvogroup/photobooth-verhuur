<#
  optimize_surface.ps1 — koeler & stabieler draaien op een Surface Pro

  De Surface wordt heet doordat de CPU "turbo" geeft tot hij oververhit en
  dan hard terugklokt (throttle-klif). Dit script haalt die piek eraf en
  temt een paar achtergrond-vreters. Alles is OMKEERBAAR met -Restore.

  Wat het doet:
    1. CPU-max op 99%  -> schakelt Turbo Boost uit. Dit is DE tweak: de CPU
       blijft bijna even snel maar wordt fors koeler en throttle't niet meer.
    2. Koelbeleid "Actief" -> ventilator eerder aan i.p.v. eerst terugklokken.
    3. Windows Search-indexering + SysMain pauzeren -> minder achtergrond-IO.

  Bewust NIET aangeraakt: Windows Defender en Windows Update (veiligheid).

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
$COOLPOL  = "SYSCOOLPOL"        # 0 = Passief (terugklokken), 1 = Actief (ventilator)

function Set-PowerValue($guid, $value) {
  powercfg /setacvalueindex SCHEME_CURRENT $SUB_PROC $guid $value | Out-Null
  powercfg /setdcvalueindex SCHEME_CURRENT $SUB_PROC $guid $value | Out-Null
}

if ($Restore) {
  Write-Host "== Terugzetten naar standaard ==" -ForegroundColor Cyan
  Set-PowerValue $MAXFREQ 100          # Turbo weer aan
  Set-PowerValue $COOLPOL 1            # Actief koelen (standaard op netstroom)
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

Write-Host "== Surface koeler maken ==" -ForegroundColor Cyan

# 1. Turbo Boost uit (max 99%) — grootste winst tegen oververhitting
Set-PowerValue $MAXFREQ 99
Write-Host "  CPU-max op 99% gezet (Turbo Boost uit -> koeler, geen throttle-klif)"

# 2. Actief koelen
Set-PowerValue $COOLPOL 1
Write-Host "  Koelbeleid op 'Actief' (ventilator eerder aan)"

powercfg /setactive SCHEME_CURRENT | Out-Null

# 3. Achtergrond-vreters temmen
foreach ($svc in "WSearch","SysMain") {
  try {
    Stop-Service $svc -Force -ErrorAction SilentlyContinue
    Set-Service $svc -StartupType Manual -ErrorAction Stop
    Write-Host "  $svc gepauzeerd (start niet meer automatisch)"
  } catch { Write-Host "  $svc kon niet gepauzeerd worden: $($_.Exception.Message)" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Klaar! Tips voor nog koeler draaien:" -ForegroundColor Green
Write-Host "  - Zet de schermhelderheid op ~70% (groot warmtebron op een tablet)."
Write-Host "  - Houd de achterkant vrij / zorg voor luchtstroom in de behuizing."
Write-Host "  - Terugzetten kan altijd:  optimize_surface.ps1 -Restore"
