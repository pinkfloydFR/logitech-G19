# Reproducible local build script for G15-Retro-HUD (Windows x64)
# Usage: .\build.ps1
$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean G15-Retro-HUD.spec

$exe = "dist\G15-Retro-HUD.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
Write-Host ""
Write-Host "Build OK : $exe"
Write-Host "SHA-256  : $hash"
