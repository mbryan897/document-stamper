# Download the bundled stamp font and its license, pinned to one upstream
# commit, and verify both against known SHA-256 values.
#
# Usage (PowerShell, from the repo root):
#     .\tools\fetch_font.ps1
#
# Source: https://github.com/googlefonts/atkinson-hyperlegible-next-mono
# Static Regular cut, not the variable font (MuPDF would embed the variable
# font's default instance, which isn't guaranteed to be Regular).

$ErrorActionPreference = 'Stop'

$Commit = '154d50362016cc3e873eb21d242cd0772384c8f9'
$Base = "https://raw.githubusercontent.com/googlefonts/atkinson-hyperlegible-next-mono/$Commit"
$Dest = Join-Path $PSScriptRoot '..\assets\fonts'

$Files = @(
    @{ Url = "$Base/fonts/ttf/AtkinsonHyperlegibleMono-Regular.ttf"
       Name = 'AtkinsonHyperlegibleMono-Regular.ttf'
       Sha256 = '2C9A7B9FABEBD5C746A808D8A1EF2F52E618850CAAD6EC4FD9F5C48F56E1C1AF' },
    @{ Url = "$Base/OFL.txt"
       Name = 'OFL.txt'
       Sha256 = '1EBB31CF7393164F20D10C1D48406CDDB5314FEFF8465531CF1E4BA37E9DD740' }
)

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
foreach ($f in $Files) {
    $target = Join-Path $Dest $f.Name
    $tmp = "$target.download"
    Invoke-WebRequest -Uri $f.Url -OutFile $tmp -UseBasicParsing
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $tmp).Hash
    if ($hash -ne $f.Sha256) {
        Remove-Item -LiteralPath $tmp -Force
        throw "SHA-256 mismatch for $($f.Name): got $hash, expected $($f.Sha256). Nothing was replaced."
    }
    Move-Item -LiteralPath $tmp -Destination $target -Force
    Write-Output "OK  $($f.Name)  $hash"
}
