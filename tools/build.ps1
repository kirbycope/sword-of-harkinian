# Build Sword Kirby for both ages and install him into Ship of Harkinian.
#
#   pwsh -File tools/build.ps1
#   pwsh -File tools/build.ps1 -SkipInstall
#
# The skeleton imports are cached, because they need Fast64 and the extracted assets
# and never change. Delete build/link_*skeleton.blend to force them again.

param(
  [switch]$SkipInstall,
  [switch]$Reimport,
  [string]$Blender = 'C:\Program Files\Blender Foundation\Blender 4.5\blender.exe',
  [string]$Mods    = 'C:\GitHub\sword-of-harkinian\.toolchain\soh\app\mods'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Run([string]$What, [string[]]$Args) {
  Write-Host "`n=== $What ===" -ForegroundColor Cyan
  & $Blender @Args
  if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

$ages = @(
  @{ Name = 'adult'; Mode = 'Adult Link'; Skel = 'build/link_skeleton.blend';       Rig = 'build/sword_kirby_rigged.blend' },
  @{ Name = 'child'; Mode = 'Child Link'; Skel = 'build/link_child_skeleton.blend'; Rig = 'build/sword_kirby_child.blend' }
)

foreach ($age in $ages) {
  if ($Reimport -or -not (Test-Path $age.Skel)) {
    Run "import $($age.Mode)" @('--background', '--python', 'blender/import_link_skeleton.py',
      '--', '--mode', $age.Mode, '--out', $age.Skel)
  } else {
    Write-Host "`n=== cached skeleton $($age.Skel) ===" -ForegroundColor DarkGray
  }

  Run "rig $($age.Name)" @('--background', '--python', 'blender/rig_to_link.py',
    '--', '--skeleton', $age.Skel, '--out', $age.Rig)

  Run "pack $($age.Name)" @('--background', '--python', 'tools/make_mod.py',
    '--', '--blend', $age.Rig, '--age', $age.Name, '--out', "dist/kirby_$($age.Name).o2r")
}

if (-not $SkipInstall) {
  if (-not (Test-Path $Mods)) { throw "no mods folder at $Mods" }
  foreach ($age in $ages) {
    Copy-Item "dist/kirby_$($age.Name).o2r" $Mods -Force
  }
  Write-Host "`ninstalled into $Mods" -ForegroundColor Green
}

Get-ChildItem dist/*.o2r | ForEach-Object {
  "{0,-22} {1,8:N0} bytes" -f $_.Name, $_.Length
}
