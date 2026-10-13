# Credits

Kirby and The Legend of Zelda: Ocarina of Time are the property of Nintendo and HAL
Laboratory. This repository contains no Nintendo material: no ROM, no extracted assets,
and no ripped models. Everything in `blender/` is built from primitives by script.

Running the mod needs your own copy of Ocarina of Time, exactly as Ship of Harkinian
does. Nothing extracted from it is committed here, and `.gitignore` is set up to keep it
that way.

## Tools

| Tool | By | Licence | Used for |
| --- | --- | --- | --- |
| [Ship of Harkinian](https://github.com/HarbourMasters/Shipwright) | Harbour Masters | MIT | The PC port the mod runs on. Staged to `.toolchain/soh/`, not committed. |
| [Fast64](https://github.com/Fast-64/fast64) | Fast-64 contributors, forked from kurethedead | MIT | Blender plugin that imports and exports Ocarina of Time skeletons and display lists. Staged to `.toolchain/fast64/`, not committed. |
| [retro](https://github.com/HarbourMasters/retro) | Harbour Masters | MIT | Builds the o2r archive. Staged to `.toolchain/retro/`, not committed. |
| [Ocarina of Time decompilation](https://github.com/zeldaret/oot) | zeldaret | Non-commercial, see the repository | Fast64's import and export target, and the source of the limb table. Cloned to `decomp/`, not committed. |
| [Blender](https://www.blender.org/) | Blender Foundation | GPL | Modelling and rendering. |

## References

The limb table in `blender/link_skeleton.py` is taken from the decompilation rather than
from memory. The limb list and ordering come from its `object_link_boy.xml`, and the
limb names from the `PlayerLimb` enum in `include/player.h`.

The rules the generator follows for a custom player model, chiefly that geometry must be
weighted to every deformable bone and that the root, upper control and lower control
bones must be the only non-deform ones, come from Fast64's Ocarina of Time guide, in the
Custom Link Process section of `fast64_internal/z64/README.md`.

Community guides on the Ship of Harkinian modelling pipeline, by PxExYxTxOxN and Thebes,
are published on GameBanana and were used to work out which Fast64 and decompilation
combination the current toolchain expects.
