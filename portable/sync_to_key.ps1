<#
  sync_to_key.ps1 -- copie JARVIS sur la cle USB pour le mode portable.

  Depuis le depot, sur le PC de dev :
      powershell -ExecutionPolicy Bypass -File portable\sync_to_key.ps1
      powershell ... -File portable\sync_to_key.ps1 -KeyRoot "E:\JARVIS"

  Ce que ca fait :
    - cree <KeyRoot>\app, \data, \runtime
    - copie le depot dans \app (sans .git, .venv, caches, donnees de session)
    - y inclut config\ (ta cle Gemini locale) pour que JARVIS marche hors de ce PC
    - pose Lancer-JARVIS.bat a la racine de <KeyRoot>
  Ca n'EFFACE jamais tes ISOs ni les autres dossiers de la cle.
#>
[CmdletBinding()]
param(
    [string]$KeyRoot = "E:\JARVIS"
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Host "[sync] Depot : $repo"
Write-Host "[sync] Cle   : $KeyRoot"

$drive = Split-Path -Qualifier $KeyRoot
if (-not (Test-Path $drive)) {
    throw "Le lecteur $drive n'existe pas. Branche la cle ou passe -KeyRoot <lettre>:\JARVIS."
}

$app = Join-Path $KeyRoot 'app'
foreach ($d in @($KeyRoot, $app, (Join-Path $KeyRoot 'data'), (Join-Path $KeyRoot 'runtime'))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# robocopy : miroir du depot vers \app, en ecartant ce qui ne doit pas voyager.
$exclDirs = @('.git', '.venv', '__pycache__', '.pytest_cache', 'webview_data',
              'node_modules', '.sandbox', 'nightshift-data')
$exclFiles = @('*.pyc', '*.pyo', '*.log')
$rcArgs = @($repo, $app, '/MIR', '/NFL', '/NDL', '/NJH', '/NP', '/R:1', '/W:1',
            '/XD') + ($exclDirs | ForEach-Object { Join-Path $repo $_ }) +
          @('/XF') + $exclFiles
Write-Host "[sync] Copie du depot vers $app ..."
$null = & robocopy @rcArgs
# robocopy : codes 0-7 = succes (8+ = erreur reelle)
if ($LASTEXITCODE -ge 8) { throw "robocopy a echoue (code $LASTEXITCODE)." }

# Le lanceur a la racine de la cle (c'est le fichier qu'on double-clique).
Copy-Item -Force (Join-Path $PSScriptRoot 'Lancer-JARVIS.bat') (Join-Path $KeyRoot 'Lancer-JARVIS.bat')

# Garde-fous de donnees deja presentes : on ne les ecrase pas, on s'assure qu'ils existent.
foreach ($d in @('data', 'data\home', 'data\tmp', 'data\hf', 'data\logs',
                 'data\ollama-models', 'data\ms-playwright')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $KeyRoot $d) | Out-Null
}

# Manifeste d'integrite de la cle (hash des .py) pour l'auto-verification au boot
$kpy = Join-Path $KeyRoot 'runtime\python\python.exe'
$genPy = if (Test-Path $kpy) { $kpy } else { (Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
if ($genPy) {
    & $genPy (Join-Path $app 'portable\selfcheck.py') manifest | Out-Null
    Write-Host "[sync] Manifeste d'integrite ecrit."
}

$free = (Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($drive)'").FreeSpace
Write-Host ""
Write-Host "[sync] Termine. Espace libre sur $drive : $([math]::Round($free/1GB,1)) Go"
Write-Host "[sync] Sur un PC : double-clic sur $KeyRoot\Lancer-JARVIS.bat"
Write-Host "[sync] 1er lancement : internet requis (Python portable + dependances)."
