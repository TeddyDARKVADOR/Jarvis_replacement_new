<#
  add_ollama.ps1 -- pose Ollama + un petit modele SUR LA CLE, pour un JARVIS qui
  marche 100 % hors-ligne (sans Gemini, sans internet) une fois fait.

      powershell -ExecutionPolicy Bypass -File add_ollama.ps1
      powershell ... -File add_ollama.ps1 -KeyRoot "E:\JARVIS" -Model "qwen3:4b-instruct"

  Gros telechargement la 1re fois (Ollama ~ ... Mo + le modele, souvent 2-3 Go) :
  internet requis MAINTENANT, plus jamais ensuite. Tout reste sur la cle
  (runtime\ollama\ollama.exe et data\ollama-models\). Le lanceur Lancer-JARVIS.bat
  demarre Ollama tout seul s'il trouve ce binaire.

  Le modele doit savoir appeler des outils (JARVIS s'en sert) : qwen3:4b-instruct
  par defaut (leger). Sur un PC a gros GPU tu peux viser plus gros plus tard.
#>
[CmdletBinding()]
param(
    [string]$KeyRoot = "E:\JARVIS",
    [string]$Model           # vide => recommandation du profil materiel (GPU/RAM)
)
$ErrorActionPreference = 'Stop'
$rt     = Join-Path $KeyRoot 'runtime'
$pyProf = Join-Path $rt 'python\python.exe'

if (-not $Model) {
    if (Test-Path $pyProf) {
        $env:JARVIS_DATA_DIR = Join-Path $KeyRoot 'data'
        $Model = (& $pyProf (Join-Path $PSScriptRoot 'host_profile.py') --model).Trim()
    }
    if (-not $Model) { $Model = 'qwen3:4b-instruct' }
    Write-Host "[ollama] Modele choisi d'apres le materiel de ce PC : $Model"
}
$odir   = Join-Path $rt 'ollama'
$oexe   = Join-Path $odir 'ollama.exe'
$models = Join-Path $KeyRoot 'data\ollama-models'
$tmp    = Join-Path $KeyRoot 'data\tmp'
New-Item -ItemType Directory -Force -Path $odir, $models, $tmp | Out-Null

if (-not (Test-Path $oexe)) {
    $zip = Join-Path $tmp 'ollama-windows-amd64.zip'
    $url = 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip'
    Write-Host "[ollama] Telechargement d'Ollama portable..."
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Write-Host "[ollama] Extraction dans $odir ..."
    Expand-Archive -Path $zip -DestinationPath $odir -Force
    Remove-Item -Force $zip
    if (-not (Test-Path $oexe)) { throw "ollama.exe introuvable apres extraction dans $odir." }
} else {
    Write-Host "[ollama] Ollama deja present : $oexe"
}

# Demarrer le serveur avec les modeles sur la cle, le temps de tirer le modele.
$env:OLLAMA_MODELS = $models
Write-Host "[ollama] Demarrage du serveur (modeles -> $models)..."
$srv = Start-Process -FilePath $oexe -ArgumentList 'serve' -PassThru -WindowStyle Hidden
try {
    # attendre que l'API reponde
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null; $ready = $true; break } catch {}
    }
    if (-not $ready) { throw "le serveur Ollama n'a pas repondu sur 127.0.0.1:11434." }
    Write-Host "[ollama] Telechargement du modele '$Model' (peut etre long)..."
    & $oexe pull $Model
    if ($LASTEXITCODE -ne 0) { throw "echec du pull de '$Model' (code $LASTEXITCODE)." }
    Write-Host "[ollama] Modeles sur la cle :"
    & $oexe list
}
finally {
    Write-Host "[ollama] Arret du serveur temporaire..."
    if ($srv -and -not $srv.HasExited) { Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue }
}
$sz = (Get-ChildItem $models -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
Write-Host ""
Write-Host "[ollama] Termine. Modeles sur la cle : $([math]::Round($sz/1GB,2)) Go."
Write-Host "[ollama] JARVIS demarrera ce modele hors-ligne au prochain Lancer-JARVIS.bat."
