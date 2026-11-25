<#
  Lance le HUD du Logitech G19 sous Windows.
  Prérequis (une seule fois) : pilote WinUSB sur l'interface 0 du G19 (046D C229 00) avec Zadig, voir README.md.
  Usage :  .\windows\run.ps1 [-Console]   (par défaut : sans fenêtre, icône dans la barre système)
#>
param([switch]$Console)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo ".venv-win"

if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Création de l'environnement Python ($venv)..."
    $py = (Get-Command py -ErrorAction SilentlyContinue)
    if ($py) { & py -3 -m venv $venv } else { & python -m venv $venv }
    & "$venv\Scripts\python.exe" -m pip install -q -r (Join-Path $PSScriptRoot "requirements.txt")
}

$env:G19_BACKEND = "g19"
if (-not $env:G19_UI) { $env:G19_UI = "native" }
$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo
if ($Console) {
    & "$venv\Scripts\python.exe" -u g19_dashboard.py
} else {
    Start-Process -FilePath "$venv\Scripts\pythonw.exe" -ArgumentList "g19_dashboard.py" -WorkingDirectory $repo -WindowStyle Hidden
}
