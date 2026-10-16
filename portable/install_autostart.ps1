<#
  install_autostart.ps1 -- vrai lancement automatique au branchement, sur CE PC.

  ATTENTION : contrairement au reste du mode portable, CECI LAISSE UNE TRACE SUR
  LE PC. On enregistre une tache planifiee "JARVIS portable" qui, a chaque
  ouverture de session, demarre le veilleur (autostart_watcher.ps1) pointant vers
  la cle. Resultat : branche la cle, JARVIS se lance seul -- mais la tache reste
  sur ce PC jusqu'a desinstallation (uninstall_autostart.ps1).

  A n'utiliser que sur TES machines de confiance.

      powershell -ExecutionPolicy Bypass -File install_autostart.ps1
#>
$ErrorActionPreference = 'Stop'
$taskName = 'JARVIS portable (veilleur de cle)'
$watcher  = Join-Path $PSScriptRoot 'autostart_watcher.ps1'

if (-not (Test-Path $watcher)) { throw "autostart_watcher.ps1 introuvable a cote de ce script." }

Write-Host "Cette tache lancera le veilleur a chaque ouverture de session de $env:USERNAME."
Write-Host "Elle restera sur ce PC jusqu'a 'uninstall_autostart.ps1'. Continuer ? (O/N)"
$ans = Read-Host
if ($ans -notmatch '^[OoYy]') { Write-Host "Annule."; return }

$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
             -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Veille le branchement d'une cle JARVIS et la demarre." -Force | Out-Null

Write-Host "Installe. Le veilleur demarrera a la prochaine ouverture de session."
Write-Host "Pour l'activer maintenant : Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Pour tout retirer         : uninstall_autostart.ps1"
