# Build the test folder (D-087): dist\DocumentStamper\ with DocumentStamper.exe and stamper-cli.exe.
# Run from the repo root with the project venv active:  .\packaging\build.ps1
# Unsigned test build for the Phase 4 portability check; Phase 5 adds signing and the antivirus check.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\stamper.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)." }

$out = Join-Path (Get-Location) "dist\DocumentStamper"
foreach ($exe in "DocumentStamper.exe", "stamper-cli.exe") {
    if (-not (Test-Path (Join-Path $out $exe))) { throw "$exe is missing from $out." }
}
& (Join-Path $out "stamper-cli.exe") --version
if ($LASTEXITCODE -ne 0) { throw "stamper-cli.exe --version failed." }
Write-Host "Built $out"
