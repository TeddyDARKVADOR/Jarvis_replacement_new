<#
  update_key.ps1 -- met a jour JARVIS sur la cle depuis ce PC de dev.

      powershell -ExecutionPolicy Bypass -File update_key.ps1                 # resync vers E:\JARVIS
      powershell ... -File update_key.ps1 -KeyRoot "F:\JARVIS" -Pull          # git pull d'abord

  -Pull : fait 'git pull --ff-only' dans le depot avant de recopier (si le depot
          a un remote propre). Sans -Pull, copie simplement l'etat local.
  Ne touche jamais data\ ni runtime\ de la cle (tes donnees, ton Python, tes
  modeles restent) : seul app\ et le lanceur sont rafraichis par sync_to_key.ps1.
#>
[CmdletBinding()]
param(
    [string]$KeyRoot = "E:\JARVIS",
    [switch]$Pull
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($Pull) {
    Write-Host "[update] git pull --ff-only dans $repo ..."
    Push-Location $repo
    try {
        & git pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Warning "git pull a echoue (code $LASTEXITCODE) — on recopie quand meme l'etat local." }
    } finally { Pop-Location }
}

& (Join-Path $PSScriptRoot 'sync_to_key.ps1') -KeyRoot $KeyRoot
Write-Host "[update] Cle a jour. Tes donnees (data\) et ton Python (runtime\) sont intacts."
