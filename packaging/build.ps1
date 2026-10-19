# Build the test copies from hash-locked dependencies (D-087, D-092, D-117).
# From the repo root, with the dev venv active (it supplies pip-audit):
#   .\packaging\build.ps1
#
#   dist\DocumentStamper.exe      one file; runs by itself from any folder or drive
#   dist\DocumentStamper-folder\  folder fallback: DocumentStamper.exe + stamper-cli.exe + _internal\
#                                 (copy the whole folder; its exe does not run on its own)
#   dist\SHA256SUMS.txt           the SHA-256 of each exe, to publish with a release
#
# What it checks, and stops on:
#   - Python is 3.14 (a warning if not exactly 3.14.6, the tested version)
#   - every package comes from requirements\build.lock, matched by hash, wheels only,
#     installed into a fresh .venv-build (never the dev venv)
#   - pip check finds no broken requirements
#   - pip-audit finds no known vulnerability in requirements\build.lock (needs the network;
#     -SkipAudit only when offline, and say so wherever the build is used)
#   - PyInstaller in .venv-build is the version the lock names
#
# Unsigned test builds. Run from a normal (not administrator) terminal.

param(
    [switch]$SkipAudit
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$TestedPython = "3.14.6"
$Lock = "requirements\build.lock"
$Venv = ".venv-build"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "Build stopped: $Message" -ForegroundColor Red
    exit 1
}

# 1. Python
$version = (& python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
if ($LASTEXITCODE -ne 0) { Fail "python didn't run. Activate the dev venv first." }
if (-not $version.StartsWith("3.14.")) { Fail "Python $version is active; the build needs 3.14." }
if ($version -ne $TestedPython) {
    Write-Host "Note: Python $version, but $TestedPython is the tested version." -ForegroundColor Yellow
}
if (-not (Test-Path $Lock)) { Fail "$Lock is missing. Run .\tools\lock.ps1 first." }

# 2. Known vulnerabilities, before anything is installed
if ($SkipAudit) {
    Write-Host "WARNING: pip-audit skipped (-SkipAudit). Don't release this build." -ForegroundColor Yellow
} else {
    & python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pip_audit') is None)"
    if ($LASTEXITCODE -ne 0) { Fail "pip-audit isn't installed in the dev venv: python -m pip install --require-hashes -r requirements\dev.lock" }
    & python -m pip_audit -r $Lock --require-hashes --disable-pip --strict --progress-spinner off
    if ($LASTEXITCODE -ne 0) { Fail "pip-audit reported a problem with $Lock (see above). Upgrade the package, or record the triage in Decisions.md before building." }
}

# 3. A fresh build venv, from the lock only
if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
& python -m venv $Venv
if ($LASTEXITCODE -ne 0) { Fail "couldn't create $Venv." }
$py = Join-Path (Get-Location) "$Venv\Scripts\python.exe"
& $py -m pip install --quiet --disable-pip-version-check --require-hashes --only-binary=:all: --no-deps -r $Lock
if ($LASTEXITCODE -ne 0) { Fail "installing $Lock failed (a hash or version didn't match, or a wheel is missing)." }
& $py -m pip install --quiet --disable-pip-version-check --no-deps --no-build-isolation --no-index .
if ($LASTEXITCODE -ne 0) { Fail "installing the project into $Venv failed." }
& $py -m pip check
if ($LASTEXITCODE -ne 0) { Fail "pip check found broken requirements in $Venv." }

# 4. PyInstaller is the locked version
$want = (Select-String -Path $Lock -Pattern '^pyinstaller==([^\s\\]+)').Matches[0].Groups[1].Value
$have = (& $py -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
if ($have -ne $want) { Fail "PyInstaller $have is installed, but $Lock names $want." }

# 5. Build from clean folders
# The folder build used to be dist\DocumentStamper\; remove it so its small exe can't be mistaken for the one-file exe.
foreach ($old in "build", "dist") { if (Test-Path $old) { Remove-Item $old -Recurse -Force } }
foreach ($spec in "packaging\stamper-onefile.spec", "packaging\stamper.spec") {
    & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build $spec
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed for $spec ($LASTEXITCODE)." }
}

$one = Join-Path (Get-Location) "dist\DocumentStamper.exe"
$folder = Join-Path (Get-Location) "dist\DocumentStamper-folder"
if (-not (Test-Path $one)) { Fail "dist\DocumentStamper.exe is missing." }
foreach ($exe in "DocumentStamper.exe", "stamper-cli.exe") {
    if (-not (Test-Path (Join-Path $folder $exe))) { Fail "$exe is missing from $folder." }
}
& (Join-Path $folder "stamper-cli.exe") --version
if ($LASTEXITCODE -ne 0) { Fail "stamper-cli.exe --version failed." }

# 6. Checksums (release engineering, not document hashing: D-007 is unaffected)
$sums = foreach ($exe in @($one, (Join-Path $folder "DocumentStamper.exe"), (Join-Path $folder "stamper-cli.exe"))) {
    $hash = (Get-FileHash -Algorithm SHA256 -Path $exe).Hash.ToLowerInvariant()
    $rel = $exe.Substring((Join-Path (Get-Location) "dist").Length + 1).Replace("\", "/")
    "$hash  $rel"
}
$sums | Set-Content -Path "dist\SHA256SUMS.txt" -Encoding ascii
$sums | ForEach-Object { Write-Host $_ }
$mb = [math]::Round((Get-Item $one).Length / 1MB, 1)
Write-Host "Built $one ($mb MB) and $folder from $Lock (Python $version, PyInstaller $have)."
