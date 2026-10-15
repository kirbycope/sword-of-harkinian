# Install an archive, photograph the player, and always close the game again.
#
#   powershell -File tools/check_build.ps1 -Archive dist/kirby_child_v5.o2r -Out docs/preview/v5
#
# Whatever happens in the middle, the finally closes Ship of Harkinian: a build left
# running is a build the next test fights with, and the window is not mine to leave open.
param(
  [Parameter(Mandatory)][string]$Archive,
  [Parameter(Mandatory)][string]$Out,
  [string]$Age = 'child',
  [int]$File = 1,
  [ValidateSet('sword','blink','attract','look')][string]$Routine = 'sword',
  [int]$Frames = 24,
  [int]$Gap = 400
)
$ErrorActionPreference = 'Stop'
$root = 'C:\GitHub\sword-of-harkinian'
$app  = Join-Path $root '.toolchain\soh\app'
$mods = Join-Path $app 'mods'

. C:\GitHub\.claude\skills\ship-of-harkinian-models\drive_soh.ps1
. C:\GitHub\.claude\skills\ship-of-harkinian-models\sword_check.ps1

function Close-Soh { Get-Process soh -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Milliseconds 600 }

$src = if ($Archive -eq 'installed') { '' } elseif ([System.IO.Path]::IsPathRooted($Archive)) { $Archive } else { Join-Path $root $Archive }
$dir = if ([System.IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $root $Out }
New-Item -ItemType Directory -Force -Path $dir | Out-Null

Close-Soh
if ($Archive -eq 'installed') {
  "checking what is already in mods: {0}" -f ((Get-ChildItem $mods -Filter *.o2r | ForEach-Object Name) -join ', ')
} else {
  Get-ChildItem $mods -Filter "kirby_$Age*.o2r" | Remove-Item -Force
  Copy-Item $src (Join-Path $mods ("kirby_{0}_test.o2r" -f $Age)) -Force
  "installed {0} as kirby_{1}_test.o2r" -f (Split-Path $src -Leaf), $Age
}

try {
  Start-Process -FilePath (Join-Path $app 'soh.exe') -WorkingDirectory $app
  Start-Sleep -Seconds 12
  # The game's own menu is a toggle on Esc and it remembers being open, so a run can start
  # with it covering the screen. Pressing Esc blind opens it as often as it closes it, so
  # the window is photographed first and the menu's blue tab strip looked for.
  Add-Type -AssemblyName System.Drawing
  $probe = Join-Path $dir '_menu_probe.png'
  Soh-Shot $probe | Out-Null
  if (Test-Path $probe) {
    $bmp = [System.Drawing.Bitmap]::FromFile($probe)
    $px = $bmp.GetPixel([Math]::Min(145, $bmp.Width - 1), [Math]::Min(107, $bmp.Height - 1))
    $bmp.Dispose()
    if ($px.B -gt 150 -and $px.B - $px.R -gt 60) {
      "the game's menu was open; closing it"
      Soh-Press Menu -After 900
    }
    Remove-Item $probe -Force
  }
  if ($Routine -eq 'attract') {
    # Left alone, the game runs its own demo, which is the only place the camera gets
    # in front of him for any length of time. Nothing is pressed: the demo is the shot.
    Start-Sleep -Seconds 25
    1..$Frames | ForEach-Object {
      Soh-Shot (Join-Path $dir ("a{0:d3}.png" -f $_)) | Out-Null
      Start-Sleep -Milliseconds $Gap
    }
    "attract frames: {0}" -f (Get-ChildItem $dir -Filter 'a*.png').Count
    return
  }
  Soh-OpenFile -File $File -OutDir $dir
  if ($Routine -eq 'blink') {
    # The equipment subscreen holds him still, facing the camera, and he blinks there
    # like he does anywhere: a burst across a few seconds shows whether the eyes move.
    if (-not (Soh-WaitInGame $dir)) { throw 'never reached the HUD' }
    [SohInput]::ClickCentre()
    Soh-Press Z -After 400
    Soh-Press Start -After 2500
    1..3 | ForEach-Object { Soh-Press R -After 1200 }
    1..$Frames | ForEach-Object {
      Soh-Shot (Join-Path $dir ("f{0:d2}.png" -f $_)) | Out-Null
      Start-Sleep -Milliseconds 120
    }
  } elseif ($Routine -eq 'look') {
    # The poses that caught the bad ones before release: the draw, a slash, and the
    # guard, where a held shield used to come out inverted and the arms disappeared.
    if (-not (Soh-WaitInGame $dir)) { throw 'never reached the HUD' }
    [SohInput]::ClickCentre()
    Soh-Press Z -After 400
    Soh-Press Z -After 900
    Soh-Shot (Join-Path $dir '00_idle.png') | Out-Null
    Soh-Press B -Hold 150 -After 2200
    Soh-Shot (Join-Path $dir '10_drawn.png') | Out-Null
    Soh-Press B -Hold 150 -After 200
    1..3 | ForEach-Object { Soh-Shot (Join-Path $dir ("20_slash{0}.png" -f $_)) | Out-Null; Start-Sleep -Milliseconds 110 }
    Start-Sleep -Milliseconds 1200
    Soh-KeyDown R; Start-Sleep -Milliseconds 900
    Soh-Shot (Join-Path $dir '30_guard.png') | Out-Null
    Soh-Hold Left -Milliseconds 500 -After 200
    Soh-Shot (Join-Path $dir '31_guard_side.png') | Out-Null
    Soh-KeyUp R
    Soh-Press Z -After 900
    Soh-Hold Down -Milliseconds 900 -After 200
    Soh-Shot (Join-Path $dir '40_front.png') | Out-Null
    "shots in {0}: {1}" -f $dir, (Get-ChildItem $dir -Filter *.png).Count
  } else {
    Soh-SwordCheck -OutDir $dir
  }
  "shots in {0}: {1}" -f $dir, (Get-ChildItem $dir -Filter *.png).Count
} finally {
  Close-Soh
  "closed Ship of Harkinian"
}
