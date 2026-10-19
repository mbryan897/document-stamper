# Regenerate the hash-locked requirement files (D-117), on Windows with Python 3.14,
# from the repo root, with the dev venv active:
#   .\tools\lock.ps1             re-lock with the current pins
#   .\tools\lock.ps1 -Upgrade    also move unpinned (transitive) packages to their latest versions
#
# First time only, in the dev venv:
#   python -m pip install pip-tools==7.6.2 pip-audit==2.10.1
# Afterwards keep the dev venv in step with the lock:
#   python -m pip install --require-hashes -r requirements\dev.lock
#
# A direct dependency changes in requirements\*.in and pyproject.toml together
# (tests\test_requirements.py checks they agree). When PyMuPDF changes, note the
# MuPDF version it bundles and check it against NVD/OSV by hand: pip-audit only
# sees advisories filed against the PyPI package, not CVEs in MuPDF itself.

param(
    [switch]$Upgrade
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$version = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($version -ne "3.14") { throw "Python $version is active; lock with 3.14 so the files match the build." }
& python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('piptools') is None)"
if ($LASTEXITCODE -ne 0) { throw "pip-tools isn't installed: python -m pip install pip-tools==7.6.2 pip-audit==2.10.1" }

$extra = @()
if ($Upgrade) { $extra += "--upgrade" }
Write-Host "pip-compile downloads every wheel of each locked version to record its hash: about 2 GB"
Write-Host "the first time (PySide6 and PyMuPDF are most of it), cached for later runs. On a slow"
Write-Host "connection the first run can take a while; the lines below show it's working."
foreach ($name in "runtime", "build", "dev") {
    Write-Host ""
    Write-Host "Locking requirements\$name.in ..." -ForegroundColor Cyan
    & python -m piptools compile --verbose --generate-hashes --allow-unsafe --strip-extras `
        --no-emit-index-url @extra --output-file "requirements\$name.lock" "requirements\$name.in"
    if ($LASTEXITCODE -ne 0) { throw "pip-compile failed for requirements\$name.in." }
    $count = (Select-String -Path "requirements\$name.lock" -Pattern '^[A-Za-z0-9_.-]+==').Count
    Write-Host "requirements\$name.lock: $count packages, each with hashes"
}
& python -c "import pymupdf; print('MuPDF bundled in PyMuPDF ' + pymupdf.VersionBind + ': ' + pymupdf.VersionFitz + ' (check it against NVD/OSV)')"
