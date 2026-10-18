# Download the bundled fonts and their licenses, each pinned to one upstream
# commit, and verify every file against a known SHA-256 value.
#
# Usage (PowerShell, from the repo root):
#     .\tools\fetch_font.ps1
#
# Stamp font (D-057): Atkinson Hyperlegible Mono Regular, static cut, from
#   https://github.com/googlefonts/atkinson-hyperlegible-next-mono
#   (MuPDF would embed a variable font's default instance, which isn't
#   guaranteed to be Regular.) SemiBold is used only by the interface.
# Interface font (D-071): Atkinson Hyperlegible Next Regular, Medium, Bold,
#   static cuts, from https://github.com/googlefonts/atkinson-hyperlegible-next
#
# A file is replaced only after its hash checks out.

$ErrorActionPreference = 'Stop'

$MonoCommit = '154d50362016cc3e873eb21d242cd0772384c8f9'
$NextCommit = '7925f50f649b3813257faf2f4c0b381011f434f1'
$Mono = "https://raw.githubusercontent.com/googlefonts/atkinson-hyperlegible-next-mono/$MonoCommit"
$Next = "https://raw.githubusercontent.com/googlefonts/atkinson-hyperlegible-next/$NextCommit"
$Dest = Join-Path $PSScriptRoot '..\assets\fonts'

$Files = @(
    @{ Url = "$Mono/fonts/ttf/AtkinsonHyperlegibleMono-Regular.ttf"
       Name = 'AtkinsonHyperlegibleMono-Regular.ttf'
       Sha256 = '2C9A7B9FABEBD5C746A808D8A1EF2F52E618850CAAD6EC4FD9F5C48F56E1C1AF' },
    @{ Url = "$Mono/fonts/ttf/AtkinsonHyperlegibleMono-SemiBold.ttf"
       Name = 'AtkinsonHyperlegibleMono-SemiBold.ttf'
       Sha256 = '6DAFAE0D4CD636B5ED1B91BDE38E46308FF3A2299665F7C32A67F35925C2177D' },
    @{ Url = "$Mono/OFL.txt"
       Name = 'OFL.txt'
       Sha256 = '1EBB31CF7393164F20D10C1D48406CDDB5314FEFF8465531CF1E4BA37E9DD740' },
    @{ Url = "$Next/fonts/ttf/AtkinsonHyperlegibleNext-Regular.ttf"
       Name = 'AtkinsonHyperlegibleNext-Regular.ttf'
       Sha256 = '88ED5C31A71584C7772963B02D04BEF1EB7E3D2E9C8B9CB204339B1F82CF432C' },
    @{ Url = "$Next/fonts/ttf/AtkinsonHyperlegibleNext-Medium.ttf"
       Name = 'AtkinsonHyperlegibleNext-Medium.ttf'
       Sha256 = 'DD50B08B3C560846097D23BAAAF6A97FFA20DD077115D23C59DF68083B9EA05E' },
    @{ Url = "$Next/fonts/ttf/AtkinsonHyperlegibleNext-Bold.ttf"
       Name = 'AtkinsonHyperlegibleNext-Bold.ttf'
       Sha256 = '994414047DF66BB4998D01C1CB1EEB4A2DDC4622D1AA56BBB8ADBECA7645B041' },
    @{ Url = "$Next/OFL.txt"
       Name = 'OFL-AtkinsonHyperlegibleNext.txt'
       Sha256 = 'ACA6A428580965D2297D1B718042DD427C2A9443ECE3B0D02D758E161E0C4030' }
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
