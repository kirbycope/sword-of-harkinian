# Sword of Harkinian

Sword Kirby as the player character in The Legend of Zelda: Ocarina of Time, running on
[Ship of Harkinian](https://github.com/HarbourMasters/Shipwright).

Kirby's sword ability is already Link in everything but shape: he wears the green cap
and swings a blade. Ocarina of Time's player skeleton turns out to meet him most of the
way, because the cap and the sheathed sword are each their own bone. The costume is
mostly a matter of replacing the ball underneath.

## Status

The model is built and checked. The export path is wired up to the point where it needs
assets extracted from a retail ROM, which is running now.

| Piece | State |
| --- | --- |
| Link's limb table, from the decompilation | Done, and self-checking |
| Sword Kirby mesh, rigged across all 21 limbs | Done, 642 vertices, 1192 triangles |
| Preview renders, four angles plus a limb-ownership view | Done, in `docs/preview` |
| Asset extraction from the ROM | Running |
| Fast64 skeleton import and export | Blocked on the extraction |
| Packing the mod as an o2r | Not started |
| Running it in Ship of Harkinian 9.3.0 | Not started |

## What you need

Ship of Harkinian does not ship any copyrighted material and neither does this
repository. You supply your own Ocarina of Time ROM, the same way Ship of Harkinian
itself requires one. This project is built against the US v1.2 ROM, which the
decompilation calls `ntsc-1.2`.

| Tool | Version | Note |
| --- | --- | --- |
| Blender | 4.5 | Fast64 supports 3.2 up to 5.1.2 and recommends 4.0 or newer. Blender 5.2 is too new. |
| Fast64 | upstream `main` | The HarbourMasters fork is Banjo-Kazooie work and has no Ship of Harkinian code. |
| retro | 0.2.1 | Builds the o2r archive. |
| Ship of Harkinian | 9.3.0 | Dewey Alpha, released 6 October 2026. |
| Ocarina of Time decompilation | zeldaret/oot | Fast64's import and export target. Never committed here. |

`tools/setup.ps1` stages all of these into `.toolchain/` and clones the decompilation
into `decomp/`. Both directories are ignored by git.

## Build

Build the model and check that every limb carries geometry:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\blender.exe' --background `
  --python blender/sword_kirby.py -- --out build/sword_kirby.blend
```

Render the preview sheets:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\blender.exe' --background `
  --python blender/render_preview.py -- --out docs/preview
```

The limb-ownership renders recolour every triangle by which of Link's limbs owns it.
That is the picture to check before any export, because a limb left with no geometry is
obvious there and invisible in a normal render.

## How the costume works

Three things about Ocarina of Time's player skeleton shape this model, and all three
come from the decompilation or from Fast64's own guide rather than from guesswork.

**Twenty-one limbs, three of which must not deform.** The limb list and its order come
from `object_link_boy.xml`, and the names from the `PlayerLimb` enum. Fast64 requires
that the root, upper control and lower control bones are the only non-deform bones,
which leaves eighteen that deform. `blender/link_skeleton.py` holds that table and
asserts both facts at import time.

**Every deforming limb needs geometry.** Fast64's guide is explicit that Link's actor
breaks if a deformable bone has nothing weighted to it. Kirby has no thighs, shins or
shoulders, so those three pairs get small wedges hidden inside the body. They satisfy
the exporter and the actor without ever being seen. The generator fails loudly if any
limb ends up empty.

**Rigid weights are correct here, not a shortcut.** Link is a flex skeleton of LOD
limbs, so the game draws one display list per limb. A vertex weighted across two limbs
has nowhere to go. The body ball is therefore split into whole horizontal rings handed
out to the waist, torso, collar and head.

The head deliberately takes the largest share of the ball. Kirby's face sits around its
middle, and it has to belong to the head limb or he will not turn to look at anything,
because the head bone is what the game aims during Z-targeting and cutscenes.

Two limbs do the costume almost by themselves. The cap goes on `PLAYER_LIMB_HAT`, which
already trails behind the head, and the sword rides `PLAYER_LIMB_SHEATH` on his back. A
held sword lives in the hand mesh, so a mitten inherits the grip.

## Layout

| Path | Holds |
| --- | --- |
| `blender/link_skeleton.py` | Link's limb table, and a check against a real import |
| `blender/sword_kirby.py` | The model generator |
| `blender/render_preview.py` | Preview and limb-ownership renders |
| `docs/preview/` | Rendered previews |
| `mod/` | The o2r contents |
| `decomp/` | The decompilation. Ignored by git, never committed. |
| `.toolchain/` | Staged third-party tools. Ignored by git. |

Third-party tools and references are credited in [CREDITS.md](CREDITS.md).
