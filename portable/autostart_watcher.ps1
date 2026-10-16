<#
  autostart_watcher.ps1 -- veilleur discret dans la barre des taches. Quand une
  cle JARVIS est branchee, il DEMANDE l'autorisation (Oui / Non) de lancer JARVIS
  sur ce PC. Rien ne demarre sans un « Oui ».

  A lancer UNE FOIS sur un PC (aucune installation, aucune trace laissee) :
      powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File <cle>\JARVIS\app\portable\autostart_watcher.ps1

  Une icone apparait pres de l'horloge. Clic droit : « Lancer JARVIS maintenant »,
  « Quitter ». Au branchement d'une cle portant <lettre>\JARVIS\Lancer-JARVIS.bat,
  une fenetre « Lancer JARVIS sur ce PC ? [Oui] [Non] » s'affiche ; Oui -> demarrage,
  Non -> on ne redemande pas pour cette cle tant qu'elle reste branchee.

  Windows a desactive l'autorun des cles USB depuis 2011 : ce veilleur (sans trace,
  a relancer par session) ou la tache planifiee install_autostart.ps1 sont les deux
  seuls moyens d'un demarrage au branchement.
#>
[CmdletBinding()]
param(
    [int]$PollSeconds = 3,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing | Out-Null

# Icone : celle de JARVIS si on la trouve a cote, sinon une icone systeme.
$icoPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config\jarvis.ico'
try { $icon = if (Test-Path $icoPath) { New-Object System.Drawing.Icon $icoPath } else { [System.Drawing.SystemIcons]::Application } }
catch { $icon = [System.Drawing.SystemIcons]::Application }

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $icon
$notify.Text = "JARVIS portable - veille le branchement de la cle"
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$script:seen = @{}

function Find-Keys {
    @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=2" -ErrorAction SilentlyContinue |
      Where-Object { Test-Path (Join-Path $_.DeviceID 'JARVIS\Lancer-JARVIS.bat') })
}
function Launch-Key($drv) {
    $bat = Join-Path $drv 'JARVIS\Lancer-JARVIS.bat'
    Start-Process -FilePath $bat -WorkingDirectory (Split-Path $bat)
    $script:seen[$drv] = 'launched'
    $notify.ShowBalloonTip(3000, "JARVIS portable", "Demarrage depuis $drv", [System.Windows.Forms.ToolTipIcon]::Info)
}
$askScript = Join-Path $PSScriptRoot 'ask_launch.ps1'
function Ask-On-Insert {
    foreach ($d in Find-Keys) {
        $drv = $d.DeviceID
        if (-not $script:seen.ContainsKey($drv)) {
            $yes = $false
            if (Test-Path $askScript) {
                # dialogue elegant ; code de sortie 0 = Oui
                $p = Start-Process powershell -PassThru -Wait -WindowStyle Hidden -ArgumentList @(
                    '-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',"`"$askScript`"",
                    '-Drive',$drv,'-Volume',"`"$($d.VolumeName)`"")
                $yes = ($p.ExitCode -eq 0)
            } else {
                $ans = [System.Windows.Forms.MessageBox]::Show(
                    "Une cle JARVIS a ete branchee ($drv $($d.VolumeName)).`n`nLancer JARVIS sur ce PC ?",
                    "JARVIS portable", [System.Windows.Forms.MessageBoxButtons]::YesNo,
                    [System.Windows.Forms.MessageBoxIcon]::Question)
                $yes = ($ans -eq [System.Windows.Forms.DialogResult]::Yes)
            }
            if ($yes) { Launch-Key $drv } else { $script:seen[$drv] = 'declined' }
        }
    }
    foreach ($k in @($script:seen.Keys)) {
        if (-not (Test-Path (Join-Path $k 'JARVIS\Lancer-JARVIS.bat'))) { $script:seen.Remove($k) }
    }
}

$miLaunch = $menu.Items.Add("Lancer JARVIS maintenant")
$miLaunch.add_Click({ $k = Find-Keys | Select-Object -First 1; if ($k) { Launch-Key $k.DeviceID } else {
    $notify.ShowBalloonTip(3000,"JARVIS portable","Aucune cle JARVIS branchee.",[System.Windows.Forms.ToolTipIcon]::Warning) } })
$ejectScript = Join-Path $PSScriptRoot 'eject.ps1'
$miEject = $menu.Items.Add("Ejecter la cle en securite")
$miEject.add_Click({
    $k = Find-Keys | Select-Object -First 1
    if ($k -and (Test-Path $ejectScript)) {
        Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$ejectScript`"",'-Drive',$k.DeviceID)
    } else {
        $notify.ShowBalloonTip(3000,"JARVIS portable","Aucune cle JARVIS a ejecter.",[System.Windows.Forms.ToolTipIcon]::Warning)
    }
})
$miQuit = $menu.Items.Add("Quitter")
$miQuit.add_Click({ $notify.Visible = $false; [System.Windows.Forms.Application]::Exit() })
$notify.ContextMenuStrip = $menu

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000, $PollSeconds * 1000)
$timer.add_Tick({ Ask-On-Insert })

if ($ValidateOnly) {
    if ($null -eq $notify -or $null -eq $menu -or $menu.Items.Count -lt 2) { throw "NotifyIcon/menu non construits" }
    $timer.Dispose(); $notify.Visible = $false; $notify.Dispose()
    Write-Output "autostart_watcher : NotifyIcon + menu + timer construits OK"
    return
}

$timer.Start()
Ask-On-Insert
$notify.ShowBalloonTip(3000, "JARVIS portable", "Je veille le branchement de ta cle. Clic droit pour le menu.", [System.Windows.Forms.ToolTipIcon]::Info)
[System.Windows.Forms.Application]::Run()
$notify.Dispose()
