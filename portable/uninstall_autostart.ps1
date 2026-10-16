<#
  uninstall_autostart.ps1 -- retire la tache planifiee posee par install_autostart.ps1.
  Ne laisse plus rien sur ce PC.

      powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1
#>
$ErrorActionPreference = 'Stop'
$taskName = 'JARVIS portable (veilleur de cle)'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Aucune tache '$taskName' sur ce PC -- rien a retirer."
    return
}
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Tache '$taskName' retiree. Ce PC ne garde plus aucune trace de JARVIS portable."
