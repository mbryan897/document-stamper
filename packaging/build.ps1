# Build the portable DocumentStamper.exe from hash-locked dependencies
# (D-087, D-092, D-117; Phase 4.6 hardening; Phase 4.7 help; Phase 5 icon and version resource).
#
# From the repo root, in a normal (not administrator) PowerShell, with the dev venv active
# (it supplies pytest and pip-audit):
#
#   .\packaging\build.ps1                  the portable exe (the usual build)
#   .\packaging\build.ps1 -Release         also: clean git tree, full test suite, audit required
#   .\packaging\build.ps1 -Fallback        also build the folder version, for when antivirus
#                                          blocks the single file (goes to dist\fallback\)
#   .\packaging\build.ps1 -RegenerateIcons rebuild assets\icon from the master picture first
#                                          (needs .venv-icon, see tools\make_icon.py)
#   .\packaging\build.ps1 -SignThumbprint <thumbprint> [-TimestampUrl <url>]
#                                          Authenticode-sign the exe with a certificate from
#                                          your certificate store (H-5)
#   Other switches: -FullTests, -SkipTests, -SkipAudit (offline only; never release),
#                   -RefreshIconCache (ask Explorer to redraw icons afterwards),
#                   -AllowAdministrator (only for a throwaway build machine whose only
#                   account is an administrator, such as a hosted CI runner; see
#                   .github\workflows\release.yml)
#
# Output (nothing else is left in dist\):
#   dist\DocumentStamper.exe   THE portable app. One file; runs by itself from any folder or
#                              drive. Put config.json next to it to carry settings with it.
#   dist\SHA256SUMS.txt        its SHA-256, to publish with it
#   dist\BUILD-INFO.txt        what it was built from, and which checks passed
#   dist\fallback\...          only with -Fallback: the folder build, which does NOT run on its
#                              own (its exe needs the _internal folder beside it)
#
# Stops on any of these:
#   - run as administrator (unless -AllowAdministrator)
#   - Python isn't 3.14 (a note if it isn't 3.14.6, the tested version)
#   - a bundled font doesn't match the SHA-256 in assets\fonts\SOURCE.txt
#   - an icon file is missing (one older than the master picture is only a note)
#   - the guard tests fail (requirements, no bare launches, icon, help), or the full suite
#     with -FullTests / -Release
#   - pip-audit finds a known vulnerability in requirements\build.lock
#   - any package doesn't come from requirements\build.lock by hash (wheels only, fresh
#     .venv-build, never the dev venv); pip check fails; PyInstaller isn't the locked version
#   - the built exe isn't 64-bit with ASLR and DEP, is UPX-packed, carries a different icon
#     than assets\icon\stamper.ico, has no or a wrong version resource, isn't a single-file
#     build, or bundles help, icons, fonts or the default config that differ from the repo,
#     or bundles anything deliberately left out (packaging\check_exe.py)
#   - a copy of the exe, alone in an empty folder whose name has a space and an accented
#     letter, doesn't run (--version)
#   - signing was asked for and the signature doesn't verify

param(
    [switch]$Release,
    [switch]$Fallback,
    [switch]$FullTests,
    [switch]$SkipTests,
    [switch]$SkipAudit,
    [switch]$RegenerateIcons,
    [switch]$RefreshIconCache,
    [switch]$AllowAdministrator,
    [string]$SignThumbprint = "",
    [string]$TimestampUrl = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Repo = (Get-Location).Path

$TestedPython = "3.14.6"
$Lock = "requirements\build.lock"
$Venv = ".venv-build"
$OneSpec = "packaging\stamper-onefile.spec"
$FolderSpec = "packaging\stamper.spec"
$Exe = Join-Path $Repo "dist\DocumentStamper.exe"
$GuardTests = @("tests\test_requirements.py", "tests\test_no_bare_launch.py", "tests\test_icon.py", "tests\test_help.py")

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "Build stopped: $Message" -ForegroundColor Red
    exit 1
}
function Step([string]$Message) { Write-Host ""; Write-Host "== $Message" -ForegroundColor Cyan }
function Note([string]$Message) { Write-Host "Note: $Message" -ForegroundColor Yellow }

# 0. Ground rules ---------------------------------------------------------------------------
Step "Checking the build environment"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if (-not $AllowAdministrator) {
        Fail "this terminal is running as administrator. Open a normal PowerShell and run the build there."
    }
    Note "running as administrator (-AllowAdministrator). Use this only on a throwaway build machine."
}
if ($Release) {
    if ($SkipAudit) { Fail "-Release can't be combined with -SkipAudit." }
    if ($SkipTests) { Fail "-Release can't be combined with -SkipTests." }
    $FullTests = $true
}
if ($SkipTests -and $FullTests) { Fail "choose -SkipTests or -FullTests, not both." }

# Nothing from the environment may add code to the build or change where the app looks.
foreach ($name in "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT",
                  "PYTHONBREAKPOINT", "PYTHONEXECUTABLE") {
    if (Test-Path "Env:\$name") { Note "ignoring $name for this build."; Remove-Item "Env:\$name" }
}
Get-ChildItem Env: | Where-Object { $_.Name -like "STAMPER_*" } | ForEach-Object {
    Note "ignoring $($_.Name) for this build."; Remove-Item "Env:\$($_.Name)"
}
$env:PYTHONNOUSERSITE = "1"            # no packages from the user's site-packages
$env:PYTHONHASHSEED = "0"              # PyInstaller's advice for repeatable builds
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
foreach ($name in "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS") {
    if (Test-Path "Env:\$name") { Note "$name is set; packages may come from there (hashes are still checked)." }
}
if (Test-Path "Env:\PIP_TRUSTED_HOST") {
    Fail "PIP_TRUSTED_HOST is set, which turns off certificate checks for those hosts. Clear it for the build."
}

# Python (the dev venv's; it runs pytest and pip-audit and creates the build venv)
$version = (& python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
if ($LASTEXITCODE -ne 0) { Fail "python didn't run. Activate the dev venv first." }
if (-not $version.StartsWith("3.14.")) { Fail "Python $version is active; the build needs 3.14." }
if ($version -ne $TestedPython) { Note "Python $version, but $TestedPython is the tested version." }
if (-not $env:VIRTUAL_ENV) { Note "no venv is active; using the python on PATH." }
if (-not (Test-Path $Lock)) { Fail "$Lock is missing. Run .\tools\lock.ps1 first." }

# Source revision, for BUILD-INFO and the version resource
$commit = ""
$dirty = $true
if (Get-Command git -ErrorAction SilentlyContinue) {
    # Windows PowerShell turns a native command's stderr into an error under "Stop".
    $ErrorActionPreference = "Continue"
    try {
        $commit = (& git rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $commit) {
            $commit = "$commit".Trim()
            $changes = (& git status --porcelain 2>$null)
            $dirty = ($LASTEXITCODE -ne 0) -or [bool]$changes
        } else { $commit = "" }
    } catch { $commit = "" }
    $ErrorActionPreference = "Stop"
}
if (-not $commit) {
    if ($Release) { Fail "couldn't read the git commit; a release must come from a known commit." }
    Note "couldn't read the git commit; the build is marked as from an unknown source."
} elseif ($dirty) {
    if ($Release) { Fail "the working tree has uncommitted changes (git status). Commit them, then build the release." }
    Note "the working tree has uncommitted changes; the exe is marked as a private build."
}

# 1. Inputs: fonts, icons, help ---------------------------------------------------------------
Step "Checking bundled fonts, icons and help"
$sourceTxt = "assets\fonts\SOURCE.txt"
if (-not (Test-Path $sourceTxt)) { Fail "$sourceTxt is missing." }
$fontHashes = Select-String -Path $sourceTxt -Pattern '^\s+(\S+)\s+([0-9a-fA-F]{64})\s*$'
if ($fontHashes.Count -lt 1) { Fail "no SHA-256 lines found in $sourceTxt." }
foreach ($m in $fontHashes) {
    $file = Join-Path "assets\fonts" $m.Matches[0].Groups[1].Value
    $want = $m.Matches[0].Groups[2].Value.ToLowerInvariant()
    if (-not (Test-Path $file)) { Fail "$file is missing. Run .\tools\fetch_font.ps1." }
    $have = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant()
    if ($have -ne $want) { Fail "$file doesn't match the SHA-256 in $sourceTxt. Run .\tools\fetch_font.ps1." }
}
Write-Host "Fonts: $($fontHashes.Count) files match $sourceTxt."

$master = "assets\icon\source\stamper-icon-master.png"
$ico = "assets\icon\stamper.ico"
$pngSizes = 16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512
if ($RegenerateIcons) {
    $iconPy = Join-Path $Repo ".venv-icon\Scripts\python.exe"
    if (-not (Test-Path $iconPy)) {
        Fail "-RegenerateIcons needs .venv-icon: python -m venv .venv-icon; .venv-icon\Scripts\pip install pillow"
    }
    & $iconPy tools\make_icon.py
    if ($LASTEXITCODE -ne 0) { Fail "tools\make_icon.py failed." }
}
if (-not (Test-Path $master)) { Fail "$master is missing." }
# File times can't prove an icon is current (git and copies set them in any order), so an
# icon clearly older than the master is only a note. What's built in is proven later:
# check_exe.py compares the exe's icon with stamper.ico byte for byte.
$masterTime = (Get-Item $master).LastWriteTimeUtc
$iconFiles = @($ico) + ($pngSizes | ForEach-Object { "assets\icon\png\stamper-$_.png" })
$stale = @()
foreach ($f in $iconFiles) {
    if (-not (Test-Path $f)) { Fail "$f is missing. Run with -RegenerateIcons (or tools\make_icon.py)." }
    if ((Get-Item $f).LastWriteTimeUtc -lt $masterTime.AddMinutes(-1)) { $stale += $f }
}
if ($stale) {
    Note "$($stale.Count) icon file(s) are more than a minute older than $master; if you changed the master, run with -RegenerateIcons."
}
$icoTime = (Get-Item $ico).LastWriteTime.ToString("yyyy-MM-dd HH:mm")
Write-Host "Icons: $ico ($icoTime) and $($pngSizes.Count) PNGs present."

if (-not (Test-Path "assets\help\index.json")) { Fail "assets\help\index.json is missing; the help wouldn't be bundled." }
$helpCount = (Get-ChildItem "assets\help" -Recurse -File).Count
Write-Host "Help: $helpCount files in assets\help."

# 2. Tests (dev venv) -------------------------------------------------------------------------
if ($SkipTests) {
    Note "tests skipped (-SkipTests)."
    $testsRun = "skipped"
} else {
    # A fresh temp folder of the build's own, removed afterwards. pytest's shared
    # %TEMP%\pytest-of-<user> can be left locked by an earlier run as administrator,
    # which fails every test with "Access is denied" before it starts.
    $testTemp = Join-Path $env:TEMP ("stamper-build-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    $pytestArgs = @("-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", $testTemp)
    try {
        if ($FullTests) {
            Step "Running the full test suite"
            & python @pytestArgs
            $testsRun = "full suite passed"
        } else {
            Step "Running the guard tests (requirements, launches, icon, help)"
            & python @pytestArgs @GuardTests
            $testsRun = "guard tests passed (" + (($GuardTests | ForEach-Object { Split-Path $_ -Leaf }) -join ", ") + ")"
        }
        $testExit = $LASTEXITCODE
    } finally {
        Remove-Item $testTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($testExit -ne 0) { Fail "tests failed (see above)." }
}

# 3. Known vulnerabilities, before anything is installed ------------------------------------
Step "Checking $Lock for known vulnerabilities"
if ($SkipAudit) {
    Write-Host "WARNING: pip-audit skipped (-SkipAudit). Don't release this build." -ForegroundColor Yellow
    $auditResult = "SKIPPED (-SkipAudit); not for release"
} else {
    & python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pip_audit') is None)"
    if ($LASTEXITCODE -ne 0) { Fail "pip-audit isn't installed in the dev venv: python -m pip install --require-hashes -r requirements\dev.lock" }
    & python -m pip_audit -r $Lock --require-hashes --disable-pip --strict --progress-spinner off
    if ($LASTEXITCODE -ne 0) { Fail "pip-audit reported a problem with $Lock (see above). Upgrade the package, or record the triage in Decisions.md before building." }
    $auditResult = "passed, no known vulnerabilities (" + (Get-Date -Format "yyyy-MM-dd") + ")"
}

# 4. A fresh build venv, from the lock only ----------------------------------------------------
Step "Creating a fresh $Venv from $Lock"
if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
& python -m venv $Venv
if ($LASTEXITCODE -ne 0) { Fail "couldn't create $Venv." }
$py = Join-Path $Repo "$Venv\Scripts\python.exe"
& $py -m pip install --quiet --require-hashes --only-binary=:all: --no-deps -r $Lock
if ($LASTEXITCODE -ne 0) { Fail "installing $Lock failed (a hash or version didn't match, or a wheel is missing)." }
& $py -m pip install --quiet --no-deps --no-build-isolation --no-index .
if ($LASTEXITCODE -ne 0) { Fail "installing the project into $Venv failed." }
& $py -m pip check
if ($LASTEXITCODE -ne 0) { Fail "pip check found broken requirements in $Venv." }

$want = (Select-String -Path $Lock -Pattern '^pyinstaller==([^\s\\]+)').Matches[0].Groups[1].Value
$have = (& $py -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
if ($have -ne $want) { Fail "PyInstaller $have is installed, but $Lock names $want." }
$appVersion = (& $py -c "from importlib.metadata import version; print(version('document-stamper'))").Trim()
$mupdf = (& $py -c "import pymupdf; print('PyMuPDF ' + pymupdf.VersionBind + ', MuPDF ' + pymupdf.VersionFitz)").Trim()
Write-Host "Document Stamper $appVersion; PyInstaller $have; $mupdf"

# 5. Build from clean folders ----------------------------------------------------------------
Step "Building the single-file exe"
foreach ($old in "build", "dist") { if (Test-Path $old) { Remove-Item $old -Recurse -Force } }
$versionFile = Join-Path $Repo "build\_meta\version_info.txt"
$viArgs = @("packaging\version_info.py", $versionFile)
if ($commit) { $viArgs += @("--commit", $commit) }
if ($dirty) { $viArgs += "--dirty" }
& $py @viArgs | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "couldn't write the version resource." }
$env:STAMPER_VERSION_FILE = $versionFile

& $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build $OneSpec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed for $OneSpec ($LASTEXITCODE)." }
if (-not (Test-Path $Exe)) { Fail "dist\DocumentStamper.exe is missing." }
$stray = Get-ChildItem dist -Force | Where-Object { $_.Name -ne "DocumentStamper.exe" }
if ($stray) { Fail "dist\ holds more than the exe ($($stray.Name -join ', ')); the single-file build should be alone." }

if ($Fallback) {
    Step "Building the folder fallback (dist\fallback\)"
    & $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist\fallback --workpath build $FolderSpec
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed for $FolderSpec ($LASTEXITCODE)." }
    $folder = Join-Path $Repo "dist\fallback\DocumentStamper-folder"
    foreach ($e in "DocumentStamper.exe", "stamper-cli.exe") {
        if (-not (Test-Path (Join-Path $folder $e))) { Fail "$e is missing from $folder." }
    }
    & (Join-Path $folder "stamper-cli.exe") --version
    if ($LASTEXITCODE -ne 0) { Fail "the fallback stamper-cli.exe --version failed." }
    @(
        "This folder build does NOT run on its own.",
        "DocumentStamper.exe here is only a launcher for the _internal folder beside it:",
        "copy the whole DocumentStamper-folder, never the exe by itself.",
        "",
        "For a single file that runs anywhere, use dist\DocumentStamper.exe."
    ) | Set-Content -Path (Join-Path $Repo "dist\fallback\READ-ME-NOT-PORTABLE.txt") -Encoding ascii
}
Remove-Item Env:\STAMPER_VERSION_FILE

# 6. Sign (optional) -------------------------------------------------------------------------
$signed = "no (unsigned test build)"
if ($SignThumbprint) {
    Step "Signing the exe"
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($signtool) { $signtool = $signtool.Source } else {
        $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $signtool) { Fail "signtool.exe not found (install the Windows SDK signing tools)." }
    $signArgs = @("sign", "/sha1", $SignThumbprint, "/fd", "SHA256")
    if ($TimestampUrl) { $signArgs += @("/tr", $TimestampUrl, "/td", "SHA256") }
    else { Note "no -TimestampUrl: the signature stops being valid when the certificate expires." }
    & $signtool @signArgs $Exe
    if ($LASTEXITCODE -ne 0) { Fail "signtool failed ($LASTEXITCODE)." }
    $sig = Get-AuthenticodeSignature -FilePath $Exe
    if ($sig.Status -ne "Valid") { Fail "the signature doesn't verify: $($sig.Status) - $($sig.StatusMessage)" }
    $signed = "yes: $($sig.SignerCertificate.Subject) (thumbprint $($sig.SignerCertificate.Thumbprint))"
    if ($TimestampUrl) { $signed += ", timestamped" }
    Write-Host "Signed: $signed"
}

# 7. Check what was built --------------------------------------------------------------------
Step "Checking the exe (64-bit, ASLR/DEP, icon, version, bundle contents)"
& $py packaging\check_exe.py $Exe --version $appVersion
if ($LASTEXITCODE -ne 0) { Fail "the built exe didn't pass its checks (see above)." }

Step "Running a copy of the exe alone in an empty folder"
$smoke = Join-Path $env:TEMP ("Stamper smoke " + [char]0x00E9 + " " + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $smoke | Out-Null
try {
    Copy-Item $Exe $smoke
    $copy = Join-Path $smoke "DocumentStamper.exe"
    $p = Start-Process -FilePath $copy -ArgumentList "--version" -WorkingDirectory $smoke -PassThru -WindowStyle Hidden
    $null = $p.Handle  # keeps the exit code readable after the process ends
    if (-not $p.WaitForExit(180000)) {
        try { $p.Kill() } catch { }
        Fail "the copy didn't finish --version within 3 minutes."
    }
    if ($p.ExitCode -ne 0) { Fail "the copy exited with $($p.ExitCode) for --version (antivirus, or a missing file)." }
    $left = Get-ChildItem $smoke -Force | Where-Object { $_.Name -ne "DocumentStamper.exe" }
    if ($left) { Note "the exe left files beside itself: $($left.Name -join ', ')" }
    Write-Host "OK: runs on its own from '$smoke' (no _internal folder needed)."
} finally {
    Remove-Item $smoke -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $Exe)) { Fail "dist\DocumentStamper.exe has disappeared; antivirus may have quarantined it." }

# 8. Checksums and build record (release engineering, not document hashing: D-007) ----------
Step "Writing SHA256SUMS.txt and BUILD-INFO.txt"
$hashTargets = @($Exe)
if ($Fallback) {
    $hashTargets += (Join-Path $Repo "dist\fallback\DocumentStamper-folder\DocumentStamper.exe")
    $hashTargets += (Join-Path $Repo "dist\fallback\DocumentStamper-folder\stamper-cli.exe")
}
$distRoot = Join-Path $Repo "dist"
$sums = foreach ($f in $hashTargets) {
    $h = (Get-FileHash -Algorithm SHA256 -Path $f).Hash.ToLowerInvariant()
    "$h  " + $f.Substring($distRoot.Length + 1).Replace("\", "/")
}
$sums | Set-Content -Path "dist\SHA256SUMS.txt" -Encoding ascii

$exeHash = (Get-FileHash -Algorithm SHA256 -Path $Exe).Hash.ToLowerInvariant()
$lockHash = (Get-FileHash -Algorithm SHA256 -Path $Lock).Hash.ToLowerInvariant()
$mb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
if ($commit) { $source = $commit } else { $source = "unknown" }
if ($dirty) { $source += " (with uncommitted changes)" }
@(
    "Document Stamper $appVersion - portable single-file build",
    "",
    "File          DocumentStamper.exe ($mb MB)",
    "SHA-256       $exeHash",
    "Built         " + (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") + " UTC on $env:COMPUTERNAME",
    "Source        $source",
    "Python        $version",
    "PyInstaller   $have",
    "PDF engine    $mupdf (check MuPDF against NVD/OSV on upgrade, D-117)",
    "Lock          $Lock, SHA-256 $lockHash",
    "pip-audit     $auditResult",
    "Tests         $testsRun",
    "Exe checks    64-bit, ASLR and DEP on, no UPX, icon = assets\icon\stamper.ico, version resource,",
    "              single-file bundle with help, icons, fonts and default config matching the repo",
    "Portable      ran --version alone in an empty folder",
    "Signed        $signed",
    "",
    "To check a copy:",
    "  Get-FileHash .\DocumentStamper.exe -Algorithm SHA256      (must equal the SHA-256 above)",
    "  Get-AuthenticodeSignature .\DocumentStamper.exe            (Valid, once signed)"
) | Set-Content -Path "dist\BUILD-INFO.txt" -Encoding ascii
$sums | ForEach-Object { Write-Host $_ }

if ($RefreshIconCache) {
    $ie4 = Join-Path ([Environment]::GetFolderPath("System")) "ie4uinit.exe"
    if (Test-Path $ie4) { & $ie4 -show } else { Note "ie4uinit.exe not found; icons refresh after sign-out." }
}

Write-Host ""
Write-Host "Built dist\DocumentStamper.exe ($mb MB): Document Stamper $appVersion, portable single file." -ForegroundColor Green
Write-Host "Copy that one file (plus config.json if you use one). Nothing else is needed."
Write-Host "If Explorer still shows the old icon, that's its icon cache: run with -RefreshIconCache or sign out and in."
if ($SkipAudit -or $SkipTests -or $dirty -or -not $SignThumbprint) {
    Write-Host "Not a release build:" -ForegroundColor Yellow
    if ($SkipAudit) { Write-Host "  - pip-audit was skipped" -ForegroundColor Yellow }
    if ($SkipTests) { Write-Host "  - tests were skipped" -ForegroundColor Yellow }
    if ($dirty) { Write-Host "  - built from uncommitted changes" -ForegroundColor Yellow }
    if (-not $SignThumbprint) { Write-Host "  - unsigned" -ForegroundColor Yellow }
}
