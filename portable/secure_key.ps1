<#
  secure_key.ps1 -- chiffre (ou dechiffre) les cles API de la cle avec une phrase
  de passe, pour qu'une cle perdue ne livre rien. N'EFFACE jamais de donnees.

      powershell -ExecutionPolicy Bypass -File secure_key.ps1            # etat
      powershell ... -File secure_key.ps1 -Action lock                   # fermer le coffre
      powershell ... -File secure_key.ps1 -Action unlock                 # ouvrir pour une session

  NB : on ne met PAS de BitLocker sur toute la cle -- elle est bootable (Ventoy),
  un chiffrement de la partition entiere casserait le boot des ISOs. On chiffre
  seulement app\config\api_keys.json -> api_keys.enc (voir portable\vault.py).
  Au repos, seul le .enc existe : cle perdue = cles API illisibles.

  Apres 'lock', le lanceur demandera la phrase a chaque demarrage (il deverrouille
  pour la session puis re-efface le clair en sortant).
#>
[CmdletBinding()]
param(
    [ValidateSet('status','lock','unlock')]
    [string]$Action = 'status',
    [switch]$IncludeMemory   # avec 'lock' : chiffre aussi la memoire (long_term.json, incidents.json)
)
$ErrorActionPreference = 'Stop'

# app\ contient portable\ et config\.
$app = Split-Path -Parent $PSScriptRoot
$cfg = Join-Path $app 'config'
if (-not (Test-Path $cfg)) { throw "Dossier config introuvable : $cfg" }

# Manifeste : ce que le coffre protege. Memoire incluse a la demande.
if ($Action -eq 'lock' -and $IncludeMemory) {
    $protect = @('config/api_keys.json','memory/long_term.json','memory/incidents.json')
    @{ protect = $protect } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $cfg 'vault.json') -Encoding UTF8
    Write-Host "Manifeste ecrit : cles + memoire seront chiffrees."
}

# Python : celui de la cle en priorite, sinon celui du systeme.
$keyPy = Join-Path (Split-Path -Parent $app) 'runtime\python\python.exe'
$py = if (Test-Path $keyPy) { $keyPy } else { 'python' }

& $py (Join-Path $PSScriptRoot 'vault.py') $Action --root $app
exit $LASTEXITCODE
