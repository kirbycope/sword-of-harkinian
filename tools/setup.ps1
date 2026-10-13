# Stage the Sword of Harkinian toolchain.
#
# Nothing this script fetches is committed: .toolchain/ and decomp/ are both ignored.
# Run it once after cloning, then follow the build steps in README.md.
#
#     powershell -ExecutionPolicy Bypass -File tools/setup.ps1

[CmdletBinding()]
param(
    [string]$SohVersion = '9.3.0',
    [string]$SohAsset = 'SoH-Dewey-Alpha-Win64.zip',
    [string]$RetroVersion = '0.2.1'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$toolchain = Join-Path $root '.toolchain'

function Get-File {
    param([string]$Url, [string]$Destination, [string]$Label)

    if (Test-Path $Destination) {
        Write-Host "  $Label already staged"
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Write-Host "  fetching $Label"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    $size = [math]::Round((Get-Item $Destination).Length / 1MB, 1)
    Write-Host "  got $Label ($size MB)"
}

Write-Host 'Staging toolchain'

Get-File -Label "Ship of Harkinian $SohVersion" `
    -Url "https://github.com/HarbourMasters/Shipwright/releases/download/$SohVersion/$SohAsset" `
    -Destination (Join-Path $toolchain 'soh\SoH-Win64.zip')

# Fast64 upstream, not the HarbourMasters fork: that fork is Banjo-Kazooie work and
# carries no Ship of Harkinian or Ocarina of Time code the fork's name might suggest.
Get-File -Label 'Fast64 (upstream main)' `
    -Url 'https://github.com/Fast-64/fast64/archive/refs/heads/main.zip' `
    -Destination (Join-Path $toolchain 'fast64\fast64-main.zip')

Get-File -Label "retro $RetroVersion" `
    -Url "https://github.com/HarbourMasters/retro/releases/download/$RetroVersion/windows.zip" `
    -Destination (Join-Path $toolchain 'retro\windows.zip')

$decomp = Join-Path $root 'decomp'
if (Test-Path (Join-Path $decomp '.git')) {
    Write-Host '  decompilation already cloned'
} else {
    Write-Host '  cloning the Ocarina of Time decompilation'
    git clone --depth 1 https://github.com/zeldaret/oot.git $decomp
}

Write-Host ''
Write-Host 'Staged. Next steps:'
Write-Host '  1. Install Blender 4.5. Fast64 supports 3.2 to 5.1.2; Blender 5.2 is too new.'
Write-Host '  2. Install the Fast64 zip as a Blender add-on.'
Write-Host '  3. Put your own Ocarina of Time ROM at decomp/baseroms/ntsc-1.2/baserom.z64'
Write-Host '     and extract its assets. See the extraction notes in README.md.'
Write-Host '  4. Build the model: blender --background --python blender/sword_kirby.py'
