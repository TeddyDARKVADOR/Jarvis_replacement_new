<#
  eject.ps1 -- retire la cle JARVIS en securite (vide le cache, demonte le volume).

      powershell -ExecutionPolicy Bypass -File eject.ps1 [-Drive "E:"] [-ValidateOnly]

  Sans -Drive, trouve la cle qui porte \JARVIS\Lancer-JARVIS.bat. Ferme JARVIS
  AVANT d'ejecter (sinon des ecritures en cours seraient perdues). N'efface rien.
#>
[CmdletBinding()]
param(
    [string]$Drive,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'

if (-not $Drive) {
    $d = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=2" -ErrorAction SilentlyContinue |
         Where-Object { Test-Path (Join-Path $_.DeviceID 'JARVIS\Lancer-JARVIS.bat') } |
         Select-Object -First 1
    if ($d) { $Drive = $d.DeviceID }
}

if ($ValidateOnly) {
    $shell = New-Object -ComObject Shell.Application
    if ($null -eq $shell) { throw "Shell.Application indisponible" }
    Write-Output "eject : Shell.Application OK (cible: $(if($Drive){$Drive}else{'aucune cle detectee'}))"
    return
}

if (-not $Drive) { Write-Warning "Aucune cle JARVIS detectee a ejecter."; exit 1 }
$letter = $Drive.TrimEnd(':').Substring(0,1) + ':'

# Vider le cache d'ecriture du volume (best effort, sans admin ca peut etre refuse)
try {
    $vol = Get-CimInstance Win32_Volume -Filter "DriveLetter='$letter'" -ErrorAction SilentlyContinue
    if ($vol) { $vol | Invoke-CimMethod -MethodName Flush -ErrorAction SilentlyContinue | Out-Null }
} catch {}

# Ejection via le shell (verbe "Eject"), sans admin pour un media amovible
try {
    $shell = New-Object -ComObject Shell.Application
    $item = $shell.Namespace(17).ParseName($letter)
    if ($item) {
        $item.InvokeVerb("Eject")
        Start-Sleep -Milliseconds 800
        if (Test-Path "$letter\") {
            Write-Warning "Windows a refuse l'ejection (un fichier de la cle est encore ouvert ?). Ferme JARVIS puis reessaie."
            exit 2
        }
        Write-Output "Cle $letter ejectee en securite. Tu peux la retirer, Sir."
    } else {
        Write-Warning "Volume $letter introuvable pour l'ejection."
        exit 1
    }
} catch {
    Write-Warning "Ejection impossible : $($_.Exception.Message)"
    exit 1
}
