# GameBanana listing

Game: The Legend of Zelda: Ocarina of Time (Ship of Harkinian section)
Category: Skins, Link
Title: Sword Kirby
Version: 1.0.0
File: SwordKirby-1.0.0.zip
Screenshots: screenshot_idle_front.png (thumbnail), screenshot_guard_front.png,
screenshot_sword_behind.png, screenshot_sheathed_behind.png, screenshot_equipment.png

## Post body (paste as is)

Sword Kirby replaces Link, child and adult, in Ship of Harkinian 9.3.0.

Kirby's sword ability is already Link in everything but shape: he wears the green cap and
swings a blade. This mod puts the ball underneath. His body, cap and shoes ride Link's
skeleton with his face and blinking eyes. The sheathed sword and shield sit on the back of
the ball, the drawn sword is held blade up beside him, the shield comes up at his side, and
all 573 of Link's animations are re-posed so his mittens stay on his body instead of
swinging around on Link's arms. The pause screen's Equipment page is posed to match. Both
ages, every sword and shield.

Works on Windows (DirectX 11) and macOS (Metal).

INSTALL
Copy kirby_child.o2r and kirby_adult.o2r into Ship of Harkinian's mods folder (the one
beside oot.o2r that holds custom_mod_files_go_here.txt) and start the game. Nothing to
enable. Delete the two files to get Link back.

kirby_voice.o2r is optional and gives him Kirby's voice from Super Smash Bros. in place of
Link's.

KNOWN LIMITS
Held items other than the sword and shield (bottles, ocarina, slingshot, bow) sit where
Link's hand is, which on a ball this size is beside the cap. The cap's tail pokes through
the sheathed shield from some angles.

SOURCE
The build scripts, and how a custom player model gets into this port at all, are at
github.com/kirbycope/sword-of-harkinian. Two port bugs found on the way are filed
upstream: Metal crashes on any skeleton limb served from a mod (Shipwright issue 7401),
and animation data served in place of the game's own mis-draws the player (issue 7402);
the mod works around both.

Kirby and The Legend of Zelda are the property of Nintendo and HAL Laboratory.

## Notes for the upload (not part of the listing)

- The archives carry the model's vertex data and the voice archive carries audio decoded
  from a Super Smash Bros. ROM. That is the usual content of a GameBanana model swap, but
  it is Nintendo material; the repository itself never commits any of it. Your call
  whether the voice archive goes up with the models or at all.
- GameBanana's Ship of Harkinian section lists mods under the Ocarina of Time game page;
  check the current category names at upload time.
