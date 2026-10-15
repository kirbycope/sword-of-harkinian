# Credits

Kirby and The Legend of Zelda: Ocarina of Time are the property of Nintendo and HAL
Laboratory. This repository contains no Nintendo material: no ROM, no extracted assets,
no ripped models and no decoded audio. What is committed is the scripts that rebuild the
mod from your own copies.

Running it needs your own Ocarina of Time ROM, exactly as Ship of Harkinian does, and the
voice mod additionally needs your own Super Smash Bros. ROM. Nothing extracted from any of
them is committed, and `.gitignore` is set up to keep it that way.

## Tools

| Tool | By | Licence | Used for |
| --- | --- | --- | --- |
| [Ship of Harkinian](https://github.com/HarbourMasters/Shipwright) | Harbour Masters | MIT | The PC port the mod runs on. Staged to `.toolchain/soh/`, not committed. |
| [Fast64](https://github.com/Fast-64/fast64) | Fast-64 contributors, forked from kurethedead | MIT | Blender plugin that imports and exports Ocarina of Time skeletons and display lists. Staged to `.toolchain/fast64/`, not committed. Ship of Harkinian maintains [its own fork](https://github.com/HarbourMasters/Fast64) for this port, and the model is built with that fork: its `hm64` feature set imports from the game's `oot.o2r` and exports the port's own resources. Staged to `.toolchain/fast64-hm/`, not committed. |
| [retro](https://github.com/HarbourMasters/retro) | Harbour Masters | MIT | Builds the o2r archive. Staged to `.toolchain/retro/`, not committed. |
| [Ocarina of Time decompilation](https://github.com/zeldaret/oot) | zeldaret | Non-commercial, see the repository | Fast64's import and export target, and the source of the limb table. Cloned to `decomp/`, not committed. |
| [Blender](https://www.blender.org/) | Blender Foundation | GPL | Modelling and rendering. |
| [Super Smash Bros. decompilation](https://github.com/VetriTheRetri/ssb-decomp-re) | VetriTheRetri and contributors | Non-commercial, see the repository | Names every sound effect, which is how Kirby's voice clips were identified rather than guessed at. Cloned to `decomp-ssb64/`, not committed. |
| [Kirby 64 decompilation](https://github.com/Kirby64Ret/kirby64) | Kirby64Ret and contributors | Non-commercial, see the repository | Confirmed the sound bank offsets the reader finds on its own. Cloned to `decomp-k64/`, not committed. |

## References

The limb table in `blender/link_skeleton.py` is taken from the decompilation rather than
from memory. The limb list and ordering come from its `object_link_boy.xml`, and the
limb names from the `PlayerLimb` enum in `include/player.h`.

The rules the generator follows for a custom player model, chiefly that geometry must be
weighted to every deformable bone and that the root, upper control and lower control
bones must be the only non-deform ones, come from Fast64's Ocarina of Time guide, in the
Custom Link Process section of `fast64_internal/z64/README.md`.

Kirby's voice is paired to Link's by name on both sides rather than by ear. Ocarina of
Time's half comes from the port's own archive, where samples are named in plain English.
Smash 64's half comes from its decompilation: the `gmFGMVoiceID` enum in `src/gm/gmsound.h`
names each voice, `fgm.ucd` holds its script, and the articulation that script selects in
`fgm.tbl` carries the sample index in its `trigger` opcode. The resolved table is
`tools/kirby_voices.json` and the pairing is `tools/kirby_voice_map.py`.

The Kirby 64 sound bank offsets its decompilation names, 0x2A8CB0 with 0x2B1510 and
0x3E1400 with 0x3E6BC0, match what `tools/n64_soundbank.py` finds by scanning, which is
what first showed the reader was correct.

Community guides on the Ship of Harkinian modelling pipeline, by PxExYxTxOxN and Thebes,
are published on GameBanana and were used to work out which Fast64 and decompilation
combination the current toolchain expects.

The port's own tooling is Harbour Masters' [Fast64
fork](https://github.com/HarbourMasters/Fast64), which builds the model here, and proxy's [o2r
animation editor](https://o2r.garrettcox.dev/), whose published mods settled how animations are
shipped. Version 1.0.0 wrote the archive by hand instead; the rebuild follows the tooling.
