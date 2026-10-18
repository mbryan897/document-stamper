# Build the test copies (D-087, D-092), from the repo root with the project venv active:
#   .\packaging\build.ps1
#
#   dist\DocumentStamper.exe   one file; runs by itself from any folder or drive
#   dist\DocumentStamper\      folder fallback: DocumentStamper.exe + stamper-cli.exe + _internal\
#
# Unsigned test builds. Run from a normal (not administrator) terminal.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

foreach ($spec in "packaging\stamper-onefile.spec", "packaging\stamper.spec") {
    python -m PyInstaller --noconfirm --clean --distpath dist --workpath build $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $spec ($LASTEXITCODE)." }
}

$one = Join-Path (Get-Location) "dist\DocumentStamper.exe"
$folder = Join-Path (Get-Location) "dist\DocumentStamper"
if (-not (Test-Path $one)) { throw "dist\DocumentStamper.exe is missing." }
foreach ($exe in "DocumentStamper.exe", "stamper-cli.exe") {
    if (-not (Test-Path (Join-Path $folder $exe))) { throw "$exe is missing from $folder." }
}
& (Join-Path $folder "stamper-cli.exe") --version
if ($LASTEXITCODE -ne 0) { throw "stamper-cli.exe --version failed." }
$mb = [math]::Round((Get-Item $one).Length / 1MB, 1)
Write-Host "Built $one ($mb MB) and $folder"
