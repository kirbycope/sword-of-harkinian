# What a Ship of Harkinian model mod actually contains

Written after taking apart a published one, then rebuilding this mod a second time through
Harbour Masters' own tooling. Both halves are recorded here: the format, which this got right,
and the route, which it got wrong the first time.

The reference mods are "Playas 3DS Link", a model replacement from GameBanana, mod 475743, and
their 3DS player animation pack. Their own readmes say the whole install is "drop the archive in
the Mods folder", with no ROM involved, which is the point: **a model mod does not need a rebuilt
ROM.**

## The archive

Mods ship as `.otr` or `.o2r`. They are different containers:

| Extension | Container |
| --- | --- |
| `.otr` | an MPQ archive, magic `MPQ\x1a` |
| `.o2r` | an ordinary ZIP |

`.o2r` is the newer one and is far easier to write, being a plain ZIP.

Model resources sit under `alt/`, Ship of Harkinian's alternate assets tree, then mirror the
game's own resource names:

```
alt/objects/object_link_child/gLinkChildSkel
alt/objects/object_link_child/gLinkChildSkelLimb_011
alt/objects/object_link_child/bone011_gLinkChildHatLimb_mesh_layer_Opaque
alt/objects/object_link_child/gLinkChildEyesClosedfTex
```

Player animations do not. They ship as a pair at the root of the archive: the data under its own
name, and the header that points at it, overriding the game's header under `alt/`:

```
objects/gameplay_keep/gPlayerAnim_link_normal_run_Data
alt/objects/gameplay_keep/gPlayerAnim_link_normal_run
```

Overriding an animation data blob in place instead mis-draws the player for a frame or two at
every animation change, and no mod of theirs does it.

## The resources

Two formats, and only one of them is binary.

**Skeletons, limbs, display lists, materials and vertices are XML.** A limb names the list it
draws and where it sits:

```xml
<SkeletonLimb Version="0" Type="Standard" LegTransX="-365" LegTransY="-670" LegTransZ="0"
              ChildIndex="255" SiblingIndex="255"
              DisplayList1="objects/object_link_child/bone011_gLinkChildHatLimb_mesh_layer_Opaque"/>
```

A display list is a list of F3D commands:

```xml
<DisplayList Version="0">
    <CallDisplayList Path="objects/object_link_child/mat_gLinkChildSkel_kirby_f3d_layerOpaque"/>
    <LoadVertices Path="objects/object_link_child/bone011_..._vtx" VertexBufferIndex="0" VertexOffset="0" Count="32"/>
    <Triangles2 V00="0" V01="1" V02="2" Flag0="0" V10="0" V11="3" V12="1" Flag1="0"/>
</DisplayList>
```

A vertex list is just as plain, with position, texture coordinates and colour as integers:

```xml
<Vertex Version="0">
    <Vtx X="115" Y="333" Z="-129" S="598" T="813" R="48" G="30" B="142" A="255"/>
</Vertex>
```

A material is a display list too, carrying the texture and combiner setup: `SetTextureImage`,
`SetTile`, `LoadBlock`, `SetCombineLERP` and so on.

**Textures are binary**: a 92-byte header whose bytes 4 to 8 read `XETO`, which is the four
character code `OTEX` stored little-endian, followed by the image in an N64 texture format.

## How this mod is built now

Harbour Masters keep a fork of Fast64 whose `hm64` feature set imports Link straight out of the
game's `oot.o2r` and exports every resource above. That is the whole model half of the build: no
ROM extraction, no decompilation checkout, no hand-written XML.

```
blender/hm_import_link.py    import Adult or Child Link from oot.o2r
blender/hm_face.py           give the face Link's own eye material, with our frames
blender/hm_export_model.py   collapse the filler, export twice so the palettes are right
tools/pack_mod.py            pack it under alt/, with the animations beside their headers
tools/check_build.ps1        install it, drive the game, photograph it, close it again
```

What the exporter does not own is the game's own equipment: the sheathed sword and shield, and
the hand lists the player code swaps in for whatever he is holding. Those stay overrides of the
game's own vertex lists and textures, which is what `tools/make_mod.py --assets-only` writes.

## The wrong turns, recorded so they are not repeated

**Retro is not the packer for a model.** It is named in Ship of Harkinian's readme, so it was the
first thing tried. Its Ship of Harkinian tools only cover music sequences and fonts, and its
Custom option packs a directory of files exactly as they already are. From that it looked as
though nothing could convert a model, and the conclusion drawn was that the game had to extract
the model from a rebuilt ROM. That conclusion was wrong and the evidence against it was one
download away: retro packs files as they are because that is all a mod needs.

**Writing the resources by hand is unsupported, and it cost two invalid bug reports.** Version
1.0.0 generated every display list, vertex list and limb itself and overrode the game's own
resources with them. It works and it shipped. But two bugs filed from it were closed as not
planned, and both were the workflow rather than the port:

- a Metal crash on a `SkeletonLimb` from an alt archive, which came from shipping hand-written
  binary limb resources where a mod's limbs are XML;
- a player glitch from overriding animation data blobs in place, where their own animation mods
  ship the data beside the header instead.

The second rebuild, through their tooling, found four settings that mod makers have to get right
and that fail in ways that look like anything but a setting:

| Symptom | Cause |
| --- | --- |
| The port dies in `gfx_load_tlut_handler_rdp`, two rooms into the game | every paletted texture exports with "Color Count" at its default of 255 while the palette holds four or eight colours, and the display list loads 256 entries out of an eight-byte resource |
| The model vanishes from the pause screen but plays fine | a limb with no geometry is exported pointing at `gEmptyDL`, a decompilation name that is in no archive |
| The whole scene goes black, HUD still drawing | pointing those limbs at an empty display list of your own instead; a list with no commands is what one renderer tolerates and another runs off the end of |
| The face is missing entirely | a converted material is a shaded solid whose combiner names no texture, so the exporter writes a display list with no `SetTextureImage` at all |

The fixes are in `blender/hm_export_model.py` and `blender/hm_face.py`, each with the reason
written where the change is made.
