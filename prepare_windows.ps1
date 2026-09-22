$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
$python = (Resolve-Path ".venv\Scripts\python.exe").Path
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements-client.txt -r requirements-server.txt -r requirements-build.txt
& $python download_models.py all
& $python -m PyInstaller --noconfirm build-desktop.spec

Write-Host ""
Write-Host "完成: dist\SAI\SAI.exe"
